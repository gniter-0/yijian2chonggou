"""pipeline.live_run T4b 编排层单测（设计 §11 离线组；全部伪件注入、零外呼）。"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from migration.run_exam_pipeline import _validate_evidence_root

from examcard.gates.review.image_admission_batch import (
    BatchImageAuthorizationManifest,
    build_batch_image_authorization,
)
from examcard.pipeline.live_run import (
    ExamPaths,
    LiveBudgetExceeded,
    LiveRunBudget,
    LiveRunConfig,
    MissingUpstreamArtifact,
    ResendPlan,
    build_admission_checkpoint,
    build_live_run_report,
    build_stage_report,
    budget_rehearsal_estimate,
    derive_stage_call_caps,
    evaluate_preflight_gate,
    execute_stage_samples,
    ingest_admission_decisions,
    ingest_image_review_sheet,
    emit_image_review_sheet,
    orchestrate,
)
from examcard.pipeline.live_config import RunState, RunStateBindingError, build_run_resume_contract
from examcard.common.hashing import canonical_hash
from examcard.evidence.golden import GoldenSampleRegistry, load_golden_registry
from examcard.evidence.golden_runner import _assertion_page
from tests.fixtures.compilers_synthetic import _hex, _page

# ---------------------------------------------------------------------------
# 预算护栏（§11-2）
# ---------------------------------------------------------------------------


def test_budget_charges_and_persists(repo_tmp_path: Path) -> None:
    state = repo_tmp_path / "budget.json"
    budget = LiveRunBudget(state, usd_cap=1.0, stage_call_caps={"step3": 3})
    budget.charge("step3", usd_estimate=0.2)
    budget.charge("step3", usd_estimate=0.2)
    resumed = LiveRunBudget(state, usd_cap=1.0, stage_call_caps={"step3": 3})
    assert resumed.total_calls == 2
    assert resumed.spent_usd == pytest.approx(0.4)


def test_budget_call_cap_and_usd_cap_take_strictest(repo_tmp_path: Path) -> None:
    budget = LiveRunBudget(repo_tmp_path / "b.json", usd_cap=0.5, stage_call_caps={"step3": 2})
    budget.charge("step3", usd_estimate=0.1)
    budget.charge("step3", usd_estimate=0.1)
    with pytest.raises(LiveBudgetExceeded) as calls_exceeded:
        budget.charge("step3", usd_estimate=0.0)
    assert calls_exceeded.value.stop_reason == "stage_call_budget_reached"
    usd_budget = LiveRunBudget(repo_tmp_path / "b2.json", usd_cap=0.15, stage_call_caps={"step3": 99})
    usd_budget.charge("step3", usd_estimate=0.1)
    with pytest.raises(LiveBudgetExceeded) as usd_exceeded:
        usd_budget.charge("step3", usd_estimate=0.1)
    assert usd_exceeded.value.stop_reason == "usd_budget_reached"


def test_budget_resume_cap_mismatch_refused(repo_tmp_path: Path) -> None:
    state = repo_tmp_path / "budget.json"
    LiveRunBudget(state, usd_cap=1.0, stage_call_caps={"step3": 3})
    with pytest.raises(ValueError, match="usd cap mismatch"):
        LiveRunBudget(state, usd_cap=2.0, stage_call_caps={"step3": 3})
    with pytest.raises(ValueError, match="call caps mismatch"):
        LiveRunBudget(state, usd_cap=1.0, stage_call_caps={"step3": 4})


# ---------------------------------------------------------------------------
# 预检放行矩阵（§11-7）
# ---------------------------------------------------------------------------


def _preflight_report(**verdicts: str) -> dict:
    return {
        "requirement_conclusions": {key: {"verdict_code": value} for key, value in verdicts.items()},
        "network_calls": 3,
    }


def test_preflight_gate_matrix() -> None:
    ok = _preflight_report(step2a="likely_pass", step3_v2="pass", step4="pass")
    assert evaluate_preflight_gate(ok, ("step1", "step2a", "step2b", "step3", "step4"))["allowed"]
    step3_bad = evaluate_preflight_gate(_preflight_report(step2a="pass", step3_v2="fail"), ("step2a", "step3"))
    assert step3_bad == {
        "allowed": False,
        "stop_reason": "preflight_step3_unmet",
        "stage": "step3",
        "requirement": "step3_v2",
        "verdict_code": "fail",
    }
    unknown = evaluate_preflight_gate(_preflight_report(step2a="pass"), ("step4",))
    assert unknown["stop_reason"] == "preflight_step4_unmet"
    assert evaluate_preflight_gate({}, ("step1",))["allowed"]  # step1 纯离线无预检项


# ---------------------------------------------------------------------------
# 样本执行：cache 跳过 / 新身份重发 / 轮耗尽 / 探针 fail-closed（§11-3/4）
# ---------------------------------------------------------------------------


def _fake_context(sample_id: str, reason: str) -> SimpleNamespace:
    identity = SimpleNamespace(
        request_id=f"req-{sample_id}-{reason}",
        cache_key=f"cache-{sample_id}-{reason}",
        payload_sha256="0" * 64,
        response_schema_sha256="0" * 64,
    )
    request = SimpleNamespace(identity=identity)
    return SimpleNamespace(sample_id=sample_id, step2a_request=request, step2b_request=request)


def _context_builder(*, sample_ids: tuple[str, ...], reason: str):
    return tuple(_fake_context(sample_id, reason) for sample_id in sample_ids)


def _executor_with_script(storage: Path, script: dict[str, list[str]]):
    """伪 _execute_stage：按样本脚本依次产出 state，并落 cache 文件（四件套的最小面）。"""

    calls: list[str] = []

    def executor(*, storage_root: Path, transport, request, stage: str) -> dict:
        sample = request.identity.request_id.split("-")[1]
        state = script[sample].pop(0)
        calls.append(request.identity.request_id)
        cache_path = storage_root / "cache" / f"{request.identity.cache_key.replace(':', '_')}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"request_id": request.identity.request_id, "state": state}), encoding="utf-8")
        return {"state": state, "request_id": request.identity.request_id, "http_status": 200, "latency_ms": 1.0}

    executor.calls = calls  # type: ignore[attr-defined]
    return executor


def test_execute_stage_samples_resend_uses_fresh_identity(repo_tmp_path: Path) -> None:
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 10})
    resend = ResendPlan(base_reason="exam-step2a-t", max_attempts=3)
    executor = _executor_with_script(repo_tmp_path, {"s1": ["failed", "success"], "s2": ["success"]})
    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("s1", "s2"),
        context_builder=_context_builder,
        transport=None,
        storage=repo_tmp_path,
        budget=budget,
        resend=resend,
        stage_executor=executor,
    )
    assert outcome["status"] == "completed"
    s1 = outcome["samples"][0]
    assert s1["outcome"] == "success"
    assert [attempt["reason"] for attempt in s1["attempts"]] == ["exam-step2a-t", "exam-step2a-t-r2"]
    assert len(set(executor.calls)) == len(executor.calls)  # 身份决不重复


def test_execute_stage_samples_cache_success_is_reused(repo_tmp_path: Path) -> None:
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 10})
    cache_path = repo_tmp_path / "cache" / "cache-s1-exam-step2a-t.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"state": "success"}), encoding="utf-8")
    executor = _executor_with_script(repo_tmp_path, {"s1": []})
    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("s1",),
        context_builder=_context_builder,
        transport=None,
        storage=repo_tmp_path,
        budget=budget,
        resend=ResendPlan(base_reason="exam-step2a-t", max_attempts=2),
        stage_executor=executor,
    )
    assert outcome["samples"][0]["outcome"] == "success"
    assert outcome["samples"][0]["attempts"][0]["reused_cache"] is True
    assert executor.calls == []  # 零外呼：断点续跑跳过
    assert budget.total_calls == 0


def test_execute_stage_samples_rounds_exhausted_marks_failures(repo_tmp_path: Path) -> None:
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 10})
    executor = _executor_with_script(repo_tmp_path, {"s1": ["failed", "unknown_outcome"]})
    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("s1",),
        context_builder=_context_builder,
        transport=None,
        storage=repo_tmp_path,
        budget=budget,
        resend=ResendPlan(base_reason="exam-step2a-t", max_attempts=2),
        stage_executor=executor,
    )
    assert outcome["status"] == "completed_with_failures"
    assert outcome["failed_samples"] == 1


def test_execute_stage_samples_probe_failure_fails_closed(repo_tmp_path: Path) -> None:
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 10})
    attempts: list[str] = []

    def probe(stage: str) -> dict:
        attempts.append(stage)
        return {"outcome": "invalid_response"}

    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("s1",),
        context_builder=_context_builder,
        transport=None,
        storage=repo_tmp_path,
        budget=budget,
        resend=ResendPlan(base_reason="exam-step2a-t", max_attempts=2),
        stage_executor=_executor_with_script(repo_tmp_path, {"s1": ["success"]}),
        probe=probe,
    )
    assert outcome["status"] == "failed"
    assert outcome["stop_reason"] == "step2a_probe_failed"
    assert attempts == ["step2a", "step2a"]  # 探针重发一次的现行口径
    assert outcome["samples"] == []  # 不进样本重发道


# ---------------------------------------------------------------------------
# 报告模型不变式（§11-10）
# ---------------------------------------------------------------------------


def test_live_run_report_skipped_after_failure_invariant() -> None:
    stages = [
        build_stage_report("step2a", "failed", "boom"),
        build_stage_report("step2b", "skipped", "skipped after upstream failure"),
    ]
    report = build_live_run_report(
        run_id="20260813-t-abc",
        exam_id="demo",
        mode="dry_run",
        stages=stages,
        stop_reason="completed_with_failures",
        network_calls=0,
        usd_estimate=0.0,
    )
    assert report.completed is False
    bad = [
        build_stage_report("step2a", "failed", "boom"),
        build_stage_report("step2b", "completed", "should not run"),
    ]
    with pytest.raises(ValueError, match="skipped"):
        build_live_run_report(
            run_id="20260813-t-abc",
            exam_id="demo",
            mode="dry_run",
            stages=bad,
            stop_reason="completed",
            network_calls=0,
            usd_estimate=0.0,
        )


# ---------------------------------------------------------------------------
# 目录契约（§11-1：缺件报错指名道姓）
# ---------------------------------------------------------------------------


def test_exam_paths_missing_workspace_and_registry(repo_tmp_path: Path) -> None:
    with pytest.raises(MissingUpstreamArtifact, match="exams/nope/"):
        ExamPaths.resolve(repo_tmp_path, "nope")
    (repo_tmp_path / "exams" / "demo").mkdir(parents=True)
    with pytest.raises(MissingUpstreamArtifact, match="golden_samples/demo_phase0.yaml"):
        ExamPaths.resolve(repo_tmp_path, "demo")


def test_live_run_config_validation() -> None:
    with pytest.raises(ValueError, match="budget-usd"):
        LiveRunConfig(exam_id="demo", stages=("step1",), mode="live", run_label="ok-label")
    with pytest.raises(ValueError, match="run label"):
        LiveRunConfig(exam_id="demo", stages=("step1",), mode="live", run_label="BAD LABEL", budget_usd=1.0)
    with pytest.raises(ValueError, match="release-fixture"):
        LiveRunConfig(
            exam_id="demo",
            stages=("step3",),
            mode="live",
            run_label="ok",
            budget_usd=1.0,
            step3_mode="release-bypass",
        )
    config = LiveRunConfig(exam_id="demo", stages=("step4", "step1"), mode="dry_run", run_label="")
    assert config.stages == ("step1", "step4")  # 固定 DAG 排序
    assert config.run_label == "dry-run"


def test_stage_caps_and_rehearsal_estimate() -> None:
    caps = derive_stage_call_caps(stages=("step2a", "step3", "step4"), sample_count=5, step3_card_count=6, step4_card_count=16, max_attempts=3)
    assert caps["step2a"] == 5 * 3 + 2
    assert caps["step3"] == 6 * 2 + 1
    assert caps["step4"] == 16 * 2 + 1
    assert caps["preflight"] >= 1
    estimate = budget_rehearsal_estimate(
        stages=("step2a", "step4"),
        sample_count=5,
        step3_card_count=0,
        step4_card_count=16,
        pricing={"step2a_call": 0.08, "step4_image_call": 0.045},
        max_attempts=3,
    )
    assert estimate["per_stage"]["step2a"]["usd_max"] == pytest.approx((5 * 3 + 2) * 0.08)
    assert estimate["usd_max_total"] > 0


# ---------------------------------------------------------------------------
# 检查点①往返（§11-5）
# ---------------------------------------------------------------------------


def _queue_item_payload(index: int) -> dict:
    presentation = "table"
    return {
        "candidate_id": f"candidate:test:{index}",
        "queue_item_id": f"queue:test:{index}",
        "subject_id": "subject:test",
        "record_id": f"record:test:{index}",
        "fact_id": f"fact:test:{index}",
        "evidence_id": f"evidence:test:{index}",
        "presentation": presentation,
        "source_manifest_sha256": _hex(f"manifest:{presentation}"),
        "anchor_ids": (f"anchor:test:{index}",),
        "pages": (_page(presentation),),
        "source_decision_id": None,
        "decision_audit_id": f"audit:test:{index}",
        "reason": "pending human admission review",
    }


def test_admission_checkpoint_roundtrip(repo_tmp_path: Path) -> None:
    ticket = build_admission_checkpoint(
        run_root=repo_tmp_path,
        queue_items=[_queue_item_payload(1), _queue_item_payload(2)],
        workbench_renderer=lambda entries: f"<html>{len(entries)}</html>",
    )
    checkpoint = repo_tmp_path / "checkpoints" / "admission"
    assert (checkpoint / "queue.json").is_file()
    assert (checkpoint / "progress.json").is_file()
    assert (checkpoint / "workbench.html").read_text(encoding="utf-8") == "<html>2</html>"
    assert ticket["queue_count"] == 2

    # 决定文件缺席 → 继续等待。
    waiting = ingest_admission_decisions(
        run_root=repo_tmp_path,
        candidates_by_id={},
        expected_queue_sha256=ticket["queue_sha256"],
        decision_builder=lambda raw, candidate: raw,
    )
    assert waiting["status"] == "waiting_human"

    raw_manifest = {
        "manifest_id": "raw:test:manifest",
        "queue_sha256": ticket["queue_sha256"],
        "decisions": [
            {
                "draft_id": "draft:test:1",
                "candidate_id": "candidate:test:1",
                "queue_item_id": "queue:test:1",
                "queue_sha256": ticket["queue_sha256"],
                "disposition": "reviewed_candidate",
                "reviewer": "tester",
                "reason": "looks correct",
                "source_text_confirmed": True,
                "anchor_confirmed": True,
                "source_page_confirmed": True,
                "image_hash_confirmed": True,
                "typed_contract_confirmed": True,
                "recorded_at": "2026-08-13T00:00:00Z",
            }
        ],
        "decision_count": 1,
    }
    inbox = checkpoint / "inbox"
    (inbox / "review-browser-decisions.json").write_text(json.dumps(raw_manifest), encoding="utf-8")

    captured: list[str] = []

    def decision_builder(raw_decision, candidate):
        captured.append(raw_decision.candidate_id)
        # r82 定式：浏览器 reviewed_candidate 一律降格 manual_review；builder 产出权威决策。
        return SimpleNamespace(disposition="manual_review")

    ingested = ingest_admission_decisions(
        run_root=repo_tmp_path,
        candidates_by_id={"candidate:test:1": object()},
        expected_queue_sha256=ticket["queue_sha256"],
        decision_builder=decision_builder,
    )
    assert ingested["status"] == "ingested"
    assert ingested["released_count"] == 0  # 浏览器 reviewed 主张不产生放行
    assert captured == ["candidate:test:1"]
    assert (checkpoint / "ingest-audit.json").is_file()

    # 队列哈希越界 → gate_refused_admission。
    refused = ingest_admission_decisions(
        run_root=repo_tmp_path,
        candidates_by_id={"candidate:test:1": object()},
        expected_queue_sha256="0" * 64,
        decision_builder=decision_builder,
    )
    assert refused["status"] == "refused"
    assert refused["stop_reason"] == "gate_refused_admission"


# ---------------------------------------------------------------------------
# 检查点②往返 + v2 批量授权清单（§11-6）
# ---------------------------------------------------------------------------


def test_image_review_roundtrip(repo_tmp_path: Path) -> None:
    step4_outcome = {
        "cards": [
            {
                "card_index": 1,
                "source_card_id": "card:test:one",
                "render_presentation": "table",
                "outcome": "success",
                "image_path": "images/one.png",
                "image_sha256": _hex("image-one"),
            },
            {"card_index": 2, "source_card_id": "card:test:two", "render_presentation": "diagram", "outcome": "failed"},
        ]
    }
    sheet_path = emit_image_review_sheet(run_root=repo_tmp_path, step4_outcome=step4_outcome, storage=repo_tmp_path / "step4")
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    assert len(sheet["cards"]) == 1  # 仅成功出图卡进入底稿

    waiting = ingest_image_review_sheet(run_root=repo_tmp_path)
    assert waiting["status"] == "waiting_human"

    sheet["cards"][0].update({"disposition": "approved", "reviewer": "tester", "basis": "pixel check ok"})
    sheet_path.write_text(json.dumps(sheet), encoding="utf-8")
    ingested = ingest_image_review_sheet(run_root=repo_tmp_path)
    assert ingested == {"status": "ingested", "approved_count": 1, "decision_count": 1}
    manifest_path = repo_tmp_path / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest = BatchImageAuthorizationManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    assert manifest.decision_count == 1
    assert manifest.approved_count == 1


def test_batch_image_manifest_rejects_duplicates_and_bad_counts() -> None:
    row = {
        "card_index": 1,
        "source_card_id": "card:test:one",
        "render_presentation": "table",
        "image_sha256": _hex("image-one"),
        "disposition": "approved",
        "reviewer": "tester",
        "basis": "ok basis",
    }
    manifest = build_batch_image_authorization([row])
    assert manifest.decisions[0].disposition == "approved"
    with pytest.raises(Exception):
        build_batch_image_authorization([row, row])  # card_index 重复整单拒绝
    with pytest.raises(Exception):
        build_batch_image_authorization([{**row, "reviewer": ""}])  # 缺 reviewer 拒绝


# ---------------------------------------------------------------------------
# 检查点② resume 对账回归（彩排收口发现：step4 已 completed 时 resume 曾空跳收场，
# 摄取块位于 step4 分支内不可达；检查点①因摄取位于后继 stage 入口而幸免）
# ---------------------------------------------------------------------------

_REAL_REPO = Path(__file__).resolve().parents[1]
_REHEARSAL_EXAM = "gonglu_golden5_rehearsal"


def _seed_exam_workspace(root: Path) -> None:
    """用真实彩排工作区的已入库固定资产在 tmp 根下拼出 ExamPaths 可解析的最小仓。"""
    import shutil

    registry_rel = Path("exams") / _REHEARSAL_EXAM / "fixtures" / "golden_samples" / f"{_REHEARSAL_EXAM}_phase0.yaml"
    profile_rel = Path("configs/v2/material_profiles/yijian_gonglu_2026.yaml")
    policy_rel = Path("configs/v2/content_policies/core_only_v1.yaml")
    for rel in (registry_rel, profile_rel, policy_rel):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REAL_REPO / rel, target)
    registry = load_golden_registry(root / registry_rel)
    for sample in registry.samples:
        source = _REAL_REPO / sample.pdf_relative_path
        target = root / sample.pdf_relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _seed_step1_workspace(root: Path) -> None:
    _seed_exam_workspace(root)
    exam_root = root / "exams" / _REHEARSAL_EXAM
    (exam_root / "input_pdf").mkdir(parents=True, exist_ok=True)
    (exam_root / "input_pdf" / "placeholder.pdf").write_bytes(b"synthetic")
    annotation_path = exam_root / "fixtures" / "golden_annotations" / "gonglu_golden5_rehearsal_phase0_v1.yaml"
    annotation_path.parent.mkdir(parents=True, exist_ok=True)
    annotation_path.write_text("synthetic: true\n", encoding="utf-8")


def _seed_paused_run(root: Path, run_id: str, *, checkpoint_status: str = "waiting_human") -> Path:
    run_root = root / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / run_id
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    sheet = {
        "cards": [
            {
                "card_index": 1,
                "source_card_id": "card:resume:one",
                "render_presentation": "table",
                "image_sha256": _hex("resume-image-one"),
                "disposition": "approved",
                "reviewer": "resume-tester",
                "basis": "clean render",
            },
            {
                "card_index": 2,
                "source_card_id": "card:resume:two",
                "render_presentation": "diagram",
                "image_sha256": _hex("resume-image-two"),
                "disposition": "rejected",
                "reviewer": "resume-tester",
                "basis": "missing edge",
            },
        ]
    }
    (checkpoint_dir / "image_review_sheet.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    config = _resume_config(root, run_id)
    caps = derive_stage_call_caps(
        stages=config.stages,
        sample_count=1,
        step3_card_count=1,
        step4_card_count=1,
        max_attempts=config.max_attempts,
    )
    run_config = build_run_resume_contract(
        config,
        resolved_sample_ids=("s1",),
        step3_card_count=0,
        step4_card_count=0,
        stage_call_caps=caps,
        image_model="",
    )
    state = RunState.load_or_create(
        run_root / "run_state.json",
        run_id=run_id,
        exam_id=config.exam_id,
        stages=config.stages,
        run_config=run_config,
        run_config_sha256=canonical_hash(run_config),
    )
    state.set_stage("step3", "completed")
    state.set_stage("step4", "completed")
    state.set_checkpoint("image_review", checkpoint_status, sheet_path=str(checkpoint_dir / "image_review_sheet.json"))
    return run_root


def _resume_config(root: Path, run_id: str) -> LiveRunConfig:
    return LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step3", "step4"),
        mode="live",
        run_label="resume-regression",
        budget_usd=1.0,
        step3_mode="release-bypass",
        step3_release_fixture=_REAL_REPO / "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml",
        skip_preflight=True,
        resume_run_id=run_id,
        repo_root=root,
    )


def test_resume_after_step4_completed_ingests_image_review(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-fixture1"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), {"pricing": {}, "sample_ids": ("s1",)})
    assert exit_code == 0
    assert report.stop_reason == "completed"
    by_stage = {entry.stage: entry for entry in report.stages}
    assert by_stage["image_review_checkpoint"].status == "completed"
    assert "approved=1" in by_stage["image_review_checkpoint"].detail
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest = BatchImageAuthorizationManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    assert (manifest.decision_count, manifest.approved_count, manifest.rejected_count) == (2, 1, 1)
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "ingested"


def test_resume_after_ingest_is_idempotent(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-fixture2"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), {"pricing": {}, "sample_ids": ("s1",)})
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), {"pricing": {}, "sample_ids": ("s1",)})
    assert (second_exit, second_report.stop_reason) == (0, "completed")
    # 已 ingested：不再产出 image_review_checkpoint 段、清单不被重写。
    assert all(entry.stage != "image_review_checkpoint" for entry in second_report.stages)
    assert manifest_path.read_bytes() == manifest_bytes
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "ingested"


def test_resume_with_undecided_sheet_stays_waiting(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-fixture3"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"][1]["disposition"] = None
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), {"pricing": {}, "sample_ids": ("s1",)})
    assert exit_code == 2
    assert report.stop_reason == "waiting_human_image_review"
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "waiting_human"


def _binding_dry_run(root: Path) -> tuple[LiveRunConfig, str, Path]:
    _seed_step1_workspace(root)
    config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step1",),
        mode="dry_run",
        run_label="resume-binding",
        repo_root=root,
    )
    report, exit_code = orchestrate(
        config,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": lambda: {"ok": True}},
    )
    assert (exit_code, report.stop_reason) == (0, "completed")
    run_root = root / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / report.run_id
    return config, report.run_id, run_root


def _must_not_run() -> dict[str, object]:
    raise AssertionError("provider execution must not occur before resume binding validation")


def _assert_resume_refused_without_mutation(
    config: LiveRunConfig, run_id: str, run_root: Path, providers: dict[str, object]
) -> None:
    state_path = run_root / "run_state.json"
    report_path = run_root / "live_run_report.json"
    budget_path = run_root / "budget.json"
    before = {
        "state": state_path.read_bytes(),
        "report": report_path.read_bytes() if report_path.is_file() else None,
        "budget": budget_path.read_bytes() if budget_path.is_file() else None,
    }
    with pytest.raises(RunStateBindingError):
        orchestrate(replace(config, resume_run_id=run_id), providers)
    assert state_path.read_bytes() == before["state"]
    assert (report_path.read_bytes() if report_path.is_file() else None) == before["report"]
    assert (budget_path.read_bytes() if budget_path.is_file() else None) == before["budget"]


def test_dry_run_state_cannot_resume_as_live(repo_tmp_path: Path) -> None:
    config, run_id, run_root = _binding_dry_run(repo_tmp_path)
    live_config = replace(config, mode="live", budget_usd=1.0)
    _assert_resume_refused_without_mutation(
        live_config,
        run_id,
        run_root,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": _must_not_run},
    )
    report = json.loads((run_root / "live_run_report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "dry_run"


def test_live_run_state_cannot_resume_as_dry_run(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    live_config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step1",),
        mode="live",
        run_label="resume-binding-live",
        budget_usd=1.0,
        skip_preflight=True,
        repo_root=repo_tmp_path,
    )
    report, exit_code = orchestrate(
        live_config,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": lambda: {"ok": True}},
    )
    assert (exit_code, report.stop_reason) == (0, "completed")
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / report.run_id
    _assert_resume_refused_without_mutation(
        replace(live_config, mode="dry_run", budget_usd=None),
        report.run_id,
        run_root,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": _must_not_run},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stages", ("step1", "step2a")),
        ("only_samples", ("does-not-match",)),
        ("max_attempts", 4),
        ("step3_contract", "v1"),
        ("step3_mode", "release-bypass"),
        ("prompt_variant", "v1"),
    ],
)
def test_semantic_run_config_mismatch_refuses_resume(repo_tmp_path: Path, field: str, value: object) -> None:
    config, run_id, run_root = _binding_dry_run(repo_tmp_path)
    _assert_resume_refused_without_mutation(
        replace(config, **{field: value}),
        run_id,
        run_root,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": _must_not_run},
    )


def test_fixture_byte_drift_refuses_resume(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    fixture = repo_tmp_path / "fixtures" / "typed.yaml"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_bytes(b"original fixture bytes")
    config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step1",),
        mode="dry_run",
        run_label="fixture-binding",
        step3_typed_fixture=fixture,
        repo_root=repo_tmp_path,
    )
    report, exit_code = orchestrate(
        config,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": lambda: {"ok": True}},
    )
    assert (exit_code, report.stop_reason) == (0, "completed")
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / report.run_id
    fixture.write_bytes(b"drifted fixture bytes")
    _assert_resume_refused_without_mutation(
        config,
        report.run_id,
        run_root,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": _must_not_run},
    )


def test_legacy_unbound_state_refuses_resume(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    run_id = "20260812-legacy-unbound"
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    legacy = {
        "run_id": run_id,
        "exam_id": _REHEARSAL_EXAM,
        "stages_requested": ["step1"],
        "stage_states": {"step1": {"status": "completed"}},
        "checkpoints": {},
    }
    state_path = run_root / "run_state.json"
    state_path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")
    before = state_path.read_bytes()
    config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step1",),
        mode="dry_run",
        run_label="legacy-unbound",
        resume_run_id=run_id,
        repo_root=repo_tmp_path,
    )
    with pytest.raises(RunStateBindingError, match="legacy or unbound"):
        orchestrate(config, {"pricing": {}, "step1_runner": _must_not_run})
    assert state_path.read_bytes() == before
    assert not (run_root / "budget.json").exists()
    assert not (run_root / "live_run_report.json").exists()


@pytest.mark.parametrize("tamper", ["projection", "sha"])
def test_tampered_run_state_binding_refuses_resume(repo_tmp_path: Path, tamper: str) -> None:
    config, run_id, run_root = _binding_dry_run(repo_tmp_path)
    state_path = run_root / "run_state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if tamper == "projection":
        payload["run_config"]["prompt_variant"] = "v1"
    else:
        payload["run_config_sha256"] = "0" * 64
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _assert_resume_refused_without_mutation(
        config,
        run_id,
        run_root,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": _must_not_run},
    )


def test_identical_run_config_resume_remains_supported(repo_tmp_path: Path) -> None:
    config, run_id, run_root = _binding_dry_run(repo_tmp_path)
    second_report, exit_code = orchestrate(
        replace(config, resume_run_id=run_id),
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": _must_not_run},
    )
    assert (exit_code, second_report.stop_reason) == (0, "completed")
    assert second_report.stages[0].detail == "already completed in this run (resume skip)"
    assert (run_root / "run_state.json").is_file()


def _step1_config(root: Path) -> LiveRunConfig:
    return LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step1", "step2a"),
        mode="dry_run",
        run_label="step1-contract",
        repo_root=root,
    )


def test_step1_success_allows_downstream_stage(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    calls: list[str] = []

    def facts_runner() -> dict[str, bool]:
        calls.append("facts")
        return {"quality_gates_passed": True, "machine_assertions_passed": True}

    report, exit_code = orchestrate(
        _step1_config(repo_tmp_path),
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": lambda: {"ok": True}, "facts_runner": facts_runner},
    )
    by_stage = {entry.stage: entry for entry in report.stages}
    assert exit_code == 0
    assert by_stage["step1"].status == "completed"
    assert by_stage["step2a"].status == "completed"
    assert calls == ["facts"]


@pytest.mark.parametrize("payload", [{"ok": False}, {}, {"ok": "true"}, {"ok": 1}, {"ok": None}])
def test_step1_contract_is_fail_closed_and_skips_downstream(repo_tmp_path: Path, payload: dict) -> None:
    _seed_step1_workspace(repo_tmp_path)
    calls: list[str] = []

    def facts_runner() -> dict[str, bool]:
        calls.append("facts")
        return {"quality_gates_passed": True, "machine_assertions_passed": True}

    report, exit_code = orchestrate(
        _step1_config(repo_tmp_path),
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": lambda: payload, "facts_runner": facts_runner},
    )
    by_stage = {entry.stage: entry for entry in report.stages}
    assert exit_code == 1
    assert by_stage["step1"].status == "failed"
    assert by_stage["step2a"].status == "skipped"
    assert calls == []


def test_nonempty_evidence_without_success_report_cannot_resume(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    paths = ExamPaths.resolve(repo_tmp_path, _REHEARSAL_EXAM)
    paths.evidence_root.mkdir(parents=True, exist_ok=True)
    (paths.evidence_root / "partial-page.json").write_text("{}", encoding="utf-8")
    registry = load_golden_registry(paths.registry_path)

    result = _validate_evidence_root(evidence_root=paths.evidence_root, registry=registry, sample_ids=None)

    assert result["ok"] is False
    assert "golden_run_report.json" in str(result["detail"])


def _valid_golden_report(registry: GoldenSampleRegistry) -> dict[str, object]:
    samples = []
    for sample in registry.samples:
        assertions = [
            {
                "assertion_id": assertion.assertion_id,
                "pages": list(_assertion_page(assertion, sample)),
                "passed": True,
                "missing_evidence_kinds": [],
                "missing_texts": [],
                "missing_conflicts": [],
                "missing_must_cover": [],
                "missing_expected_matches": [],
            }
            for assertion in sample.assertions
        ]
        samples.append(
            {
                "sample_id": sample.sample_id,
                "pdf_relative_path": sample.pdf_relative_path,
                "pdf_sha256": sample.pdf_sha256,
                "selected_pages": list(sample.selected_pages),
                "annotation_status": sample.annotation_status.value,
                "quality": {},
                "assertions": assertions,
                "assertions_passed": True,
                "error": None,
            }
        )
    return {
        "registry_id": registry.registry_id,
        "material_profile_id": registry.material_profile_id,
        "golden_registry_ready_for_phase1": True,
        "sample_count": len(samples),
        "selected_page_count": sum(len(sample["selected_pages"]) for sample in samples),
        "seeded_assertion_count": sum(len(sample["assertions"]) for sample in samples),
        "seeded_assertions_passed": True,
        "samples": samples,
        "status": "evidence_ready_for_review",
        "note": "synthetic accepted report for resume validation",
    }


def _write_valid_golden_report(evidence_root: Path, registry: GoldenSampleRegistry) -> None:
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / "golden_run_report.json").write_text(
        json.dumps(_valid_golden_report(registry), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_resume_revalidates_current_golden_source_bytes(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    paths = ExamPaths.resolve(repo_tmp_path, _REHEARSAL_EXAM)
    registry = load_golden_registry(paths.registry_path)
    _write_valid_golden_report(paths.evidence_root, registry)

    accepted = _validate_evidence_root(
        evidence_root=paths.evidence_root,
        registry=registry,
        sample_ids=None,
        workspace_root=repo_tmp_path,
    )
    assert accepted["ok"] is True

    source_path = repo_tmp_path / registry.samples[0].pdf_relative_path
    source_path.write_bytes(source_path.read_bytes() + b"\nresume-source-drift")
    rejected = _validate_evidence_root(
        evidence_root=paths.evidence_root,
        registry=registry,
        sample_ids=None,
        workspace_root=repo_tmp_path,
    )
    assert rejected["ok"] is False
    assert "source hash mismatch" in str(rejected["detail"])


def test_seeded_evidence_rejects_missing_current_golden_source(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    paths = ExamPaths.resolve(repo_tmp_path, _REHEARSAL_EXAM)
    registry = load_golden_registry(paths.registry_path)
    seed_root = repo_tmp_path / "seed-evidence"
    _write_valid_golden_report(seed_root, registry)
    source_path = repo_tmp_path / registry.samples[-1].pdf_relative_path
    source_path.unlink()

    result = _validate_evidence_root(
        evidence_root=seed_root,
        registry=registry,
        sample_ids=None,
        workspace_root=repo_tmp_path,
    )
    assert result["ok"] is False
    assert "missing source" in str(result["detail"])
