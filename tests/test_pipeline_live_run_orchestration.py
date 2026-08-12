"""pipeline.live_run T4b 编排层单测（设计 §11 离线组；全部伪件注入、零外呼）。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
)
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
