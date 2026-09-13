"""pipeline.live_run T4b 编排层单测（设计 §11 离线组；全部伪件注入、零外呼）。"""
from __future__ import annotations

import base64
import json
from io import BytesIO
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

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
    run_step4_stage,
)
from examcard.pipeline.live_checkpoint import image_review_emission_identity
from examcard.pipeline.live_config import RunState, RunStateBindingError, build_run_resume_contract
from examcard.pipeline.live_stage_step4 import Step4ImageIntegrityError, prepare_step4_request_bindings
from examcard.common.hashing import canonical_hash, sha256_bytes
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


def test_resume_schema_two_binds_v3_step3_contract(repo_tmp_path: Path) -> None:
    config_v3 = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step3",),
        mode="dry_run",
        run_label="resume-v3-contract",
        step3_contract="v3",
        repo_root=repo_tmp_path,
    )
    caps = derive_stage_call_caps(
        stages=config_v3.stages,
        sample_count=1,
        step3_card_count=1,
        step4_card_count=1,
        max_attempts=config_v3.max_attempts,
    )
    v3 = build_run_resume_contract(
        config_v3,
        resolved_sample_ids=("s1",),
        step3_card_count=1,
        step4_card_count=1,
        stage_call_caps=caps,
        image_base_url="",
        image_model="",
    )
    v2 = build_run_resume_contract(
        replace(config_v3, step3_contract="v2"),
        resolved_sample_ids=("s1",),
        step3_card_count=1,
        step4_card_count=1,
        stage_call_caps=caps,
        image_base_url="",
        image_model="",
    )
    assert v3["schema_version"] == 2
    assert v3["step3_contract"] == "v3"
    assert canonical_hash(v3) != canonical_hash(v2)
    assert v3 == build_run_resume_contract(
        config_v3,
        resolved_sample_ids=("s1",),
        step3_card_count=1,
        step4_card_count=1,
        stage_call_caps=caps,
        image_base_url="",
        image_model="",
    )


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


def test_execute_stage_samples_cached_success_is_semantically_revalidated(repo_tmp_path: Path) -> None:
    cache_key = "cache-s1-exam-step2a-t"
    request_id = "req-s1-exam-step2a-t"
    cache_path = repo_tmp_path / "cache" / f"{cache_key}.json"
    record_path = repo_tmp_path / "records" / f"{request_id}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"state": "success", "response": {"proposals": []}}
    record_bytes = json.dumps(record, separators=(",", ":")).encode("utf-8")
    record_path.write_bytes(record_bytes)
    cache_path.write_text(
        json.dumps({"state": "success", "request_id": request_id, "record_sha256": sha256_bytes(record_bytes)}),
        encoding="utf-8",
    )
    executor = _executor_with_script(repo_tmp_path, {"s1": []})
    evaluated: list[dict] = []
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 10})

    def gate(stage, context, persisted_record):
        evaluated.append(persisted_record)
        return {"valid": True}

    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("s1",),
        context_builder=_context_builder,
        transport=None,
        storage=repo_tmp_path,
        budget=budget,
        resend=ResendPlan(base_reason="exam-step2a-t", max_attempts=3),
        stage_executor=executor,
        gate_evaluator=gate,
    )
    assert outcome["samples"][0]["outcome"] == "success"
    assert evaluated == [record]
    assert executor.calls == []
    assert outcome["samples"][0]["attempts"] == [
        {
            "reason": "exam-step2a-t",
            "request_id": request_id,
            "state": "success",
            "reused_cache": True,
        }
    ]
    assert outcome["samples"][0]["semantic_gate_valid"] is True
    assert budget.total_calls == 0


def test_execute_stage_samples_cached_success_missing_record_fails_closed(repo_tmp_path: Path) -> None:
    cache_path = repo_tmp_path / "cache" / "cache-s1-exam-step2a-t.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"state": "success", "request_id": "req-s1-exam-step2a-t", "record_sha256": "0" * 64}),
        encoding="utf-8",
    )
    executor = _executor_with_script(repo_tmp_path, {"s1": []})
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 10})
    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("s1",),
        context_builder=_context_builder,
        transport=None,
        storage=repo_tmp_path,
        budget=budget,
        resend=ResendPlan(base_reason="exam-step2a-t", max_attempts=3),
        stage_executor=executor,
        gate_evaluator=lambda stage, context, record: {"valid": True},
    )
    sample = outcome["samples"][0]
    assert outcome["status"] == "completed_with_failures"
    assert sample["outcome"] == "failed"
    assert sample["semantic_gate_valid"] is False
    assert sample["gates"]["gate_error"] == "ValueError: cached success record is missing"
    assert len(sample["attempts"]) == 1
    assert executor.calls == []
    assert budget.total_calls == 0


def test_execute_stage_samples_semantic_invalid_fails_closed(repo_tmp_path: Path) -> None:
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 10})
    executor = _executor_with_script(repo_tmp_path, {"s1": ["success"]})
    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("s1",),
        context_builder=_context_builder,
        transport=None,
        storage=repo_tmp_path,
        budget=budget,
        resend=ResendPlan(base_reason="exam-step2a-t", max_attempts=3),
        stage_executor=executor,
        gate_evaluator=lambda stage, context, record: {"valid": False, "reason": "semantic mismatch"},
    )
    sample = outcome["samples"][0]
    assert outcome["status"] == "completed_with_failures"
    assert sample["outcome"] == "failed"
    assert len(sample["attempts"]) == 1
    assert executor.calls == ["req-s1-exam-step2a-t"]


def test_execute_stage_samples_semantic_gate_exception_fails_closed(repo_tmp_path: Path) -> None:
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 10})
    executor = _executor_with_script(repo_tmp_path, {"s1": ["success"]})

    def raise_gate(stage, context, record):
        raise ValueError("api_key=TOP-SECRET")

    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("s1",),
        context_builder=_context_builder,
        transport=None,
        storage=repo_tmp_path,
        budget=budget,
        resend=ResendPlan(base_reason="exam-step2a-t", max_attempts=3),
        stage_executor=executor,
        gate_evaluator=raise_gate,
    )
    sample = outcome["samples"][0]
    assert outcome["status"] == "completed_with_failures"
    assert sample["outcome"] == "failed"
    assert len(sample["attempts"]) == 1
    assert sample["gates"]["gate_error"] == "ValueError: [REDACTED]"
    assert "TOP-SECRET" not in json.dumps(sample)
    assert executor.calls == ["req-s1-exam-step2a-t"]


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
    assert caps["step3"] == 6 * 3 + 1
    assert caps["step4"] == 16 * 3 + 1
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
    assert estimate["per_stage"]["step4"]["calls_max"] == 16 * 3 + 1
    assert estimate["per_stage"]["step4"]["usd_max"] == pytest.approx((16 * 3 + 1) * 0.045)
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
    release = repo_tmp_path / "release.yaml"
    handoff = _resume_step4_handoff(repo_tmp_path)
    release.write_text("synthetic: true\n", encoding="utf-8")
    card = _resume_cards()[0]
    png = _resume_png((60, 110, 160))

    def bundle_loader(_release: Path, _handoff: Path) -> tuple[object, object]:
        return SimpleNamespace(cards=(card,)), SimpleNamespace(fact_count=1)

    def prompt_builder(current: SimpleNamespace) -> str:
        return f"resume prompt for {current.sample_id}"

    def image_caller(payload: Mapping[str, object]) -> tuple[int, bytes]:
        return 200, _resume_image_envelope(png, str(payload["prompt"]))

    step4_outcome = run_step4_stage(
        release_fixture_path=release,
        handoff_path=handoff,
        prompt_builder=prompt_builder,
        image_caller=image_caller,
        models_probe=None,
        image_model="gpt-image-2",
        storage=repo_tmp_path / "step4",
        budget=LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=1.0, stage_call_caps={"step4": 3}),
        bundle_loader=bundle_loader,
    )
    sheet_path = emit_image_review_sheet(run_root=repo_tmp_path, step4_outcome=step4_outcome, storage=repo_tmp_path / "step4")
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    assert sheet["schema"] == "image-review-sheet/2.0"
    assert len(sheet["cards"]) == 1

    waiting = ingest_image_review_sheet(run_root=repo_tmp_path)
    assert waiting["status"] == "waiting_human"

    sheet["cards"][0].update({"disposition": "approved", "reviewer": "tester", "basis": "pixel check ok"})
    sheet_path.write_text(json.dumps(sheet), encoding="utf-8")
    ingested = ingest_image_review_sheet(run_root=repo_tmp_path)
    assert ingested["status"] == "ingested"
    assert (ingested["approved_count"], ingested["decision_count"]) == (1, 1)
    manifest_path = repo_tmp_path / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest = BatchImageAuthorizationManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    assert manifest.decision_count == 1
    assert manifest.approved_count == 1


@pytest.mark.parametrize("existing_manifest", [False, True])
def test_image_review_ingest_rejects_reversed_rows_without_manifest_rewrite(
    repo_tmp_path: Path, existing_manifest: bool
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_root = _seed_paused_run(repo_tmp_path, "img-order-reversal")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    if existing_manifest:
        assert ingest_image_review_sheet(run_root=run_root)["status"] == "ingested"
    manifest_before = manifest_path.read_bytes() if manifest_path.is_file() else None
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"].reverse()
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")

    result = ingest_image_review_sheet(run_root=run_root)

    assert result["status"] == "refused"
    if manifest_before is None:
        assert not manifest_path.exists()
    else:
        assert manifest_path.read_bytes() == manifest_before


def test_image_review_ingest_preserves_existing_consistent_manifest_bytes(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_root = _seed_paused_run(repo_tmp_path, "img-existing-consistent-manifest")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"

    first = ingest_image_review_sheet(run_root=run_root)
    manifest_before = manifest_path.read_bytes()
    second = ingest_image_review_sheet(run_root=run_root)

    assert first["status"] == second["status"] == "ingested"
    assert manifest_path.read_bytes() == manifest_before


def test_image_review_ingest_rejects_existing_different_manifest_without_overwrite(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_root = _seed_paused_run(repo_tmp_path, "img-existing-different-manifest")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    assert ingest_image_review_sheet(run_root=run_root)["status"] == "ingested"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    decision = manifest["decisions"][0]
    decision["basis"] = "different valid basis"
    decision["decision_sha256"] = canonical_hash(
        {key: value for key, value in decision.items() if key not in {"schema_version", "decision_sha256"}}
    )
    manifest["manifest_sha256"] = canonical_hash(
        {key: value for key, value in manifest.items() if key not in {"schema_version", "manifest_sha256"}}
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_before = manifest_path.read_bytes()

    result = ingest_image_review_sheet(run_root=run_root)

    assert result["status"] == "refused"
    assert manifest_path.read_bytes() == manifest_before


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


def _resume_cards() -> tuple[SimpleNamespace, ...]:
    return (
        SimpleNamespace(
            card_index=1,
            source_card_id="source:card:one",
            sample_id="one",
            render_presentation="table",
        ),
        SimpleNamespace(
            card_index=2,
            source_card_id="source:card:two",
            sample_id="two",
            render_presentation="diagram",
        ),
    )


def _resume_png(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (1024, 1536), color=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image.close()
    return buffer.getvalue()


def _resume_image_envelope(png: bytes, prompt: str) -> bytes:
    return json.dumps(
        {
            "created": 1_700_000_000,
            "data": [{"b64_json": base64.b64encode(png).decode("ascii"), "revised_prompt": prompt}],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 3,
                "input_tokens_details": {"text_tokens": 1, "image_tokens": 0, "cached_tokens": 0},
                "output_tokens_details": {"text_tokens": 0, "image_tokens": 2, "reasoning_tokens": 0},
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _resume_step4_handoff(root: Path) -> Path:
    path = root / "resume-step4-handoff.json"
    if not path.is_file():
        path.write_text("{}", encoding="utf-8")
    return path


def _resume_providers(root: Path) -> dict[str, object]:
    cards = _resume_cards()
    handoff = _resume_step4_handoff(root)

    def bundle_loader(_release: Path, _handoff: Path) -> tuple[object, object]:
        return SimpleNamespace(cards=cards), SimpleNamespace(fact_count=2)

    def prompt_builder(card: SimpleNamespace) -> str:
        return f"resume prompt for {card.sample_id}"

    def image_caller(payload: dict[str, object]) -> tuple[int, bytes]:
        color = (50, 90, 140) if payload["prompt"] == "resume prompt for one" else (140, 90, 50)
        return 200, _resume_image_envelope(_resume_png(color), str(payload["prompt"]))

    return {
        "pricing": {},
        "sample_ids": ("s1",),
        "prompt_builder": prompt_builder,
        "image_caller": image_caller,
        "image_base_url": "http://127.0.0.1:3100/v1",
        "image_model": "gpt-image-2",
        "step4_bundle_loader": bundle_loader,
        "step4_card_count": 2,
        "step4_handoff": handoff,
    }


def _seed_paused_run(root: Path, run_id: str, *, checkpoint_status: str = "waiting_human") -> Path:
    run_root = root / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / run_id
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    config = _resume_config(root, run_id)
    providers = _resume_providers(root)
    caps = derive_stage_call_caps(
        stages=config.stages,
        sample_count=1,
        step3_card_count=1,
        step4_card_count=2,
        max_attempts=config.max_attempts,
    )
    seeded = run_step4_stage(
        release_fixture_path=config.step3_release_fixture or Path(),
        handoff_path=config.step4_handoff or Path(),
        prompt_builder=providers["prompt_builder"],  # type: ignore[arg-type]
        image_caller=providers["image_caller"],  # type: ignore[arg-type]
        models_probe=None,
        image_model="gpt-image-2",
        storage=run_root / "step4",
        budget=LiveRunBudget(run_root / "budget.json", usd_cap=1.0, stage_call_caps=caps),
        bundle_loader=providers["step4_bundle_loader"],  # type: ignore[arg-type]
    )
    assert seeded["status"] == "completed"
    sheet_path = emit_image_review_sheet(run_root=run_root, step4_outcome=seeded, storage=run_root / "step4")
    emission_identity = image_review_emission_identity(
        run_root=run_root,
        sheet_path=sheet_path,
    )
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"][0].update({"disposition": "approved", "reviewer": "resume-tester", "basis": "clean render"})
    sheet["cards"][1].update({"disposition": "rejected", "reviewer": "resume-tester", "basis": "missing edge"})
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    run_config = build_run_resume_contract(
        config,
        resolved_sample_ids=("s1",),
        step3_card_count=0,
        step4_card_count=2,
        stage_call_caps=caps,
        image_base_url=str(providers["image_base_url"]),
        image_model="gpt-image-2",
        step4_handoff=config.step4_handoff,
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
    state.set_checkpoint("image_review", checkpoint_status, **emission_identity)
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
        step4_handoff=_resume_step4_handoff(root),
        skip_preflight=True,
        resume_run_id=run_id,
        repo_root=root,
    )


def test_live_orchestrate_propagates_step4_max_attempts(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    release = repo_tmp_path / "step4-release-fixture.yaml"
    release.write_text("synthetic: true\n", encoding="utf-8")
    handoff = _resume_step4_handoff(repo_tmp_path)
    card = _resume_cards()[0]
    calls: list[Mapping[str, object]] = []
    valid_png = _resume_png((80, 20, 160))

    def bundle_loader(_release: Path, _handoff: Path) -> tuple[object, object]:
        return SimpleNamespace(cards=(card,)), SimpleNamespace(fact_count=2)

    def prompt_builder(current: SimpleNamespace) -> str:
        return f"orchestration prompt for {current.sample_id}"

    def image_caller(payload: Mapping[str, object]) -> tuple[int, bytes]:
        calls.append(payload)
        if len(calls) < 3:
            return 200, b"{}"
        return 200, _resume_image_envelope(valid_png, str(payload["prompt"]))

    config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step4",),
        mode="live",
        run_label="step4-max-attempts",
        budget_usd=1.0,
        max_attempts=3,
        step4_handoff=handoff,
        skip_preflight=True,
        repo_root=repo_tmp_path,
    )
    providers = {
        "pricing": {"step4_image_call": 0.1},
        "sample_ids": ("s1",),
        "prompt_builder": prompt_builder,
        "image_caller": image_caller,
        "image_model": "gpt-image-2",
        "step4_bundle_loader": bundle_loader,
        "step4_card_count": 1,
        "step4_release_fixture": release,
    }

    report, exit_code = orchestrate(config, providers)

    assert (exit_code, report.stop_reason) == (2, "waiting_human_image_review")
    assert report.network_calls == 3
    assert {entry.stage: entry.status for entry in report.stages}["step4"] == "completed"
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / report.run_id
    materialized = json.loads((run_root / "live_run_report.json").read_text(encoding="utf-8"))
    assert materialized["extras"]["step4"]["status"] == "completed"
    assert materialized["extras"]["step4_cards"][0]["outcome"] == "success"
    assert len(materialized["extras"]["step4_cards"][0]["attempts"]) == 3
    assert materialized["extras"]["budget"]["per_stage"]["step4"]["calls"] == 3
    assert len(calls) == 3
    assert len(list((run_root / "step4" / "images").glob("*.png"))) == 1
    assert len(list((run_root / "step4" / "image-provenance").glob("*.json"))) == 1


def test_live_orchestrate_persists_secret_free_non_2xx_image_failure(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    release = repo_tmp_path / "step4-release-fixture.yaml"
    release.write_text("synthetic: true\n", encoding="utf-8")
    handoff = _resume_step4_handoff(repo_tmp_path)
    card = _resume_cards()[0]
    body = json.dumps(
        {
            "error": {
                "message": "Invalid size. Authorization: Bearer sk-supersecret123456 Cookie: session=abcdef",
                "type": "invalid_request_error",
                "code": "invalid_size",
                "param": "size",
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")

    def bundle_loader(_release: Path, _handoff: Path) -> tuple[object, object]:
        return SimpleNamespace(cards=(card,)), SimpleNamespace(fact_count=2)

    def prompt_builder(current: SimpleNamespace) -> str:
        return f"orchestration prompt for {current.sample_id}"

    def image_caller(_payload: Mapping[str, object]) -> tuple[int, bytes]:
        return 400, body

    config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step4",),
        mode="live",
        run_label="step4-http-failure-diagnostics",
        budget_usd=1.0,
        max_attempts=1,
        step4_handoff=handoff,
        skip_preflight=True,
        repo_root=repo_tmp_path,
    )
    providers = {
        "pricing": {"step4_image_call": 0.1},
        "sample_ids": ("s1",),
        "prompt_builder": prompt_builder,
        "image_caller": image_caller,
        "image_base_url": "https://synthetic.example/v1",
        "image_model": "gpt-image-2",
        "step4_bundle_loader": bundle_loader,
        "step4_card_count": 1,
        "step4_release_fixture": release,
        "models_probe": None,
    }

    report, exit_code = orchestrate(config, providers)

    assert (exit_code, report.stop_reason, report.network_calls) == (1, "completed_with_failures", 1)
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / report.run_id
    materialized = json.loads((run_root / "live_run_report.json").read_text(encoding="utf-8"))
    attempt = materialized["extras"]["step4_cards"][0]["attempts"][0]
    provider_failure = attempt["provider_failure"]
    assert provider_failure["response_sha256"] == sha256_bytes(body)
    assert provider_failure["response_bytes"] == len(body)
    assert provider_failure["body_persisted"] is False
    assert provider_failure["body_reconstruction_allowed"] is False
    assert "class=unsupported_size" in provider_failure["sanitized_detail"]
    serialized = json.dumps(materialized, ensure_ascii=False)
    assert body.decode("utf-8") not in serialized
    for secret in ("Authorization", "Bearer", "sk-supersecret123456", "Cookie", "session=abcdef"):
        assert secret not in serialized
    assert not (run_root / "checkpoints" / "image_review" / "image_review_sheet.json").exists()
    assert not list((run_root / "step4" / "images").glob("*.png"))
    assert not list((run_root / "step4" / "image-provenance").glob("*.json"))


def test_live_orchestrate_step4_multi_card_budget_tracks_max_attempts(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    release = repo_tmp_path / "step4-release-fixture.yaml"
    release.write_text("synthetic: true\n", encoding="utf-8")
    handoff = _resume_step4_handoff(repo_tmp_path)
    cards = _resume_cards()
    image_calls: list[Mapping[str, object]] = []

    def bundle_loader(_release: Path, _handoff: Path) -> tuple[object, object]:
        return SimpleNamespace(cards=cards), SimpleNamespace(fact_count=2)

    def prompt_builder(current: SimpleNamespace) -> str:
        return f"multi-card prompt for {current.sample_id}"

    def image_caller(payload: Mapping[str, object]) -> tuple[int, bytes]:
        image_calls.append(payload)
        if len(image_calls) in {3, 6}:
            color = (80, 20, 160) if len(image_calls) == 3 else (20, 160, 80)
            return 200, _resume_image_envelope(_resume_png(color), str(payload["prompt"]))
        return 200, b"{}"

    config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step4",),
        mode="live",
        run_label="step4-multi-card-max-attempts",
        budget_usd=1.0,
        max_attempts=3,
        step4_handoff=handoff,
        skip_preflight=True,
        repo_root=repo_tmp_path,
    )
    providers = {
        "pricing": {"step4_image_call": 0.1},
        "sample_ids": ("s1",),
        "prompt_builder": prompt_builder,
        "image_caller": image_caller,
        "image_model": "gpt-image-2",
        "step4_bundle_loader": bundle_loader,
        "step4_card_count": 2,
        "step4_release_fixture": release,
        "models_probe": None,
    }

    report, exit_code = orchestrate(config, providers)

    assert (exit_code, report.stop_reason) == (2, "waiting_human_image_review")
    assert report.network_calls == 6
    assert report.usd_estimate == pytest.approx(0.6)
    by_stage = {entry.stage: entry for entry in report.stages}
    assert by_stage["step4"].status == "completed"
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / report.run_id
    materialized = json.loads((run_root / "live_run_report.json").read_text(encoding="utf-8"))
    budget = materialized["extras"]["budget"]["per_stage"]["step4"]
    assert budget["call_cap"] == 7
    assert budget["calls"] == 6
    assert materialized["extras"]["step4"]["status"] == "completed"
    step4_cards = materialized["extras"]["step4_cards"]
    assert len(step4_cards) == 2
    assert [card["outcome"] for card in step4_cards] == ["success", "success"]
    assert [len(card["attempts"]) for card in step4_cards] == [3, 3]
    assert len(image_calls) == 6
    assert len(list((run_root / "step4" / "images").glob("*.png"))) == 2
    assert len(list((run_root / "step4" / "image-provenance").glob("*.json"))) == 2


def test_resume_after_step4_completed_ingests_image_review(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-fixture1"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), _resume_providers(repo_tmp_path))
    assert exit_code == 0
    assert report.stop_reason == "completed"
    by_stage = {entry.stage: entry for entry in report.stages}
    assert by_stage["image_review_checkpoint"].status == "completed"
    assert "approved=1" in by_stage["image_review_checkpoint"].detail
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest = BatchImageAuthorizationManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    assert (manifest.decision_count, manifest.approved_count, manifest.rejected_count) == (2, 1, 1)
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    ticket = state["checkpoints"]["image_review"]
    assert ticket["status"] == "ingested"
    for field in (
        "source_binding_sha256",
        "image_review_source_binding_sha256",
        "sheet_file_sha256",
        "review_sheet_file_sha256",
        "manifest_sha256",
        "manifest_file_sha256",
        "request_manifest_file_sha256",
        "provenance_file_sha256_by_card",
    ):
        assert field in ticket
    assert ticket["decision_count"] == 2
    assert ticket["approved_count"] == 1
    assert ticket["rejected_count"] == 1
    assert ticket["manual_review_count"] == 0
    assert ticket["reviewed_card_indexes"] == [1, 2]
    assert ticket["reviewed_card_count"] == 2


def test_resume_after_ingest_is_idempotent(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-fixture2"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), _resume_providers(repo_tmp_path))
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), _resume_providers(repo_tmp_path))
    assert (second_exit, second_report.stop_reason) == (0, "completed")
    # 已 ingested：不再产出 image_review_checkpoint 段、清单不被重写。
    assert all(entry.stage != "image_review_checkpoint" for entry in second_report.stages)
    assert manifest_path.read_bytes() == manifest_bytes
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "ingested"

    checkpoint_dir = run_root / "checkpoints" / "image_review"
    assert (checkpoint_dir / "reviewed-image-fixture-v2.json").is_file()
    assert (checkpoint_dir / "post-review-queue-v2.json").is_file()
    assert (checkpoint_dir / "image-admission-audit-v2.json").is_file()
    assert (checkpoint_dir / "image-review-materialization-v2.json").is_file()
    assert state["checkpoints"]["image_review"]["materialization_status"] == "frozen"
    image_ticket = state["checkpoints"]["image_review"]
    assert image_ticket["final_release_status"] == "frozen"
    assert image_ticket["final_release_released_count"] == 1
    assert image_ticket["final_release_released_card_indexes"] == [1]
    final_release_dir = run_root / "checkpoints" / "final_release"
    assert (final_release_dir / "final-release-bundle-v2.json").is_file()
    assert (final_release_dir / "final-release-exclusion-ledger-v2.json").is_file()
    assert (final_release_dir / "final-release-audit-v2.json").is_file()
    assert (final_release_dir / "final-release-closure-v2.json").is_file()


def test_frozen_final_release_resume_does_not_write_checkpoint(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-frozen-final-release-read-only"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), _resume_providers(repo_tmp_path))
    assert (first_exit, first_report.stop_reason) == (0, "completed")

    state_path = run_root / "run_state.json"
    budget_path = run_root / "budget.json"
    final_release_dir = run_root / "checkpoints" / "final_release"
    state_before = state_path.read_bytes()
    budget_before = budget_path.read_bytes()
    artifacts_before = {path.name: path.read_bytes() for path in final_release_dir.iterdir()}

    providers = _resume_providers(repo_tmp_path)

    def must_not_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("frozen P4 resume must not execute providers")

    providers["image_caller"] = must_not_call
    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (0, "completed")
    assert state_path.read_bytes() == state_before
    assert budget_path.read_bytes() == budget_before
    assert {path.name: path.read_bytes() for path in final_release_dir.iterdir()} == artifacts_before


def test_p2_only_ingested_resume_upgrades_and_freezes_materialization(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-p2-only-materialization-upgrade"
    run_root = _seed_paused_run(repo_tmp_path, run_id)

    authority = ingest_image_review_sheet(run_root=run_root)
    assert authority["status"] == "ingested"
    state_path = run_root / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["checkpoints"]["image_review"] = {
        key: value for key, value in authority.items() if key != "status"
    } | {"status": "ingested"}
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    assert not any(
        (checkpoint_dir / filename).exists()
        for filename in (
            "reviewed-image-fixture-v2.json",
            "post-review-queue-v2.json",
            "image-admission-audit-v2.json",
            "image-review-materialization-v2.json",
        )
    )

    providers = _resume_providers(repo_tmp_path)
    providers["image_caller"] = _must_not_run
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    first_ticket = first_state["checkpoints"]["image_review"]
    assert first_ticket["status"] == "ingested"
    assert first_ticket["materialization_status"] == "frozen"
    for field in (
        "materialization_receipt_sha256",
        "materialization_receipt_file_sha256",
        "post_review_queue_sha256",
        "post_review_queue_file_sha256",
        "admission_audit_sha256",
        "admission_audit_file_sha256",
        "materialized_decision_count",
        "materialized_approved_count",
        "materialized_rejected_count",
        "materialized_manual_review_count",
        "materialized_reviewed_card_indexes",
        "materialized_post_review_card_indexes",
    ):
        assert field in first_ticket
    first_artifacts = {
        filename: (checkpoint_dir / filename).read_bytes()
        for filename in (
            "reviewed-image-fixture-v2.json",
            "post-review-queue-v2.json",
            "image-admission-audit-v2.json",
            "image-review-materialization-v2.json",
        )
    }

    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (0, "completed")
    assert {
        filename: (checkpoint_dir / filename).read_bytes()
        for filename in first_artifacts
    } == first_artifacts
    second_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert second_state["checkpoints"]["image_review"] == first_ticket


def test_frozen_materialization_status_deletion_refuses_without_rebaseline(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-p3-status-deletion"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")

    state_path = run_root / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    ticket = state["checkpoints"]["image_review"]
    del ticket["materialization_status"]
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    before = {
        name: (checkpoint_dir / name).read_bytes()
        for name in (
            "reviewed-image-fixture-v2.json",
            "post-review-queue-v2.json",
            "image-admission-audit-v2.json",
            "image-review-materialization-v2.json",
        )
    }
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")
    assert {name: (checkpoint_dir / name).read_bytes() for name in before} == before
    refused_state = json.loads(state_path.read_text(encoding="utf-8"))
    refused_ticket = refused_state["checkpoints"]["image_review"]
    assert refused_ticket["status"] == "refused"
    assert "materialization_status" not in refused_ticket


def test_partial_materialization_identity_on_p2_only_ticket_refuses(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-p3-partial-ticket"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    authority = ingest_image_review_sheet(run_root=run_root)
    state_path = run_root / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["checkpoints"]["image_review"] = {
        key: value for key, value in authority.items() if key != "status"
    } | {"status": "ingested", "materialization_receipt_sha256": "0" * 64}
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    providers = _resume_providers(repo_tmp_path)
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")
    refused_state = json.loads(state_path.read_text(encoding="utf-8"))
    refused_ticket = refused_state["checkpoints"]["image_review"]
    assert refused_ticket["status"] == "refused"
    assert refused_ticket["materialization_receipt_sha256"] == "0" * 64
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    assert not (checkpoint_dir / "image-review-materialization-v2.json").exists()


def test_invalid_materialization_status_refuses_without_rebaseline(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-p3-invalid-status"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")

    state_path = run_root / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["checkpoints"]["image_review"]["materialization_status"] = "pending"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")
    refused_state = json.loads(state_path.read_text(encoding="utf-8"))
    refused_ticket = refused_state["checkpoints"]["image_review"]
    assert refused_ticket["status"] == "refused"
    assert refused_ticket["materialization_status"] == "pending"


def test_image_review_materialization_zero_approved_is_valid_without_fixture(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-zero-approved"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    for row in sheet["cards"]:
        row.update({"disposition": "rejected", "reviewer": "resume-tester", "basis": "not suitable"})
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")

    providers = _resume_providers(repo_tmp_path)
    providers["image_caller"] = _must_not_run
    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (0, "completed")
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    assert not (checkpoint_dir / "reviewed-image-fixture-v2.json").exists()
    queue = json.loads((checkpoint_dir / "post-review-queue-v2.json").read_text(encoding="utf-8"))
    audit = json.loads((checkpoint_dir / "image-admission-audit-v2.json").read_text(encoding="utf-8"))
    receipt = json.loads((checkpoint_dir / "image-review-materialization-v2.json").read_text(encoding="utf-8"))
    assert queue["queue_count"] == 2
    assert audit["decision_count"] == 2
    assert audit["approved_count"] == 0
    assert receipt["reviewed_fixture_present"] is False
    assert receipt["reviewed_fixture_sha256"] is None
    assert receipt["reviewed_fixture_file_sha256"] is None


def test_image_review_materialization_rejects_frozen_artifact_tamper_without_calls(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-p3-byte-tamper"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    queue_path = run_root / "checkpoints" / "image_review" / "post-review-queue-v2.json"
    original = queue_path.read_bytes()
    queue_path.write_bytes(original + b"\n")
    calls: list[Mapping[str, object]] = []
    providers["image_caller"] = lambda payload: (calls.append(payload) or _must_not_run())

    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (second_exit, second_report.stop_reason) == (1, "gate_refused_admission")
    assert not calls
    assert queue_path.read_bytes() == original + b"\n"


def test_image_review_materialization_rejects_semantic_tamper_with_valid_child_hash(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-p3-semantic-tamper"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    queue_path = run_root / "checkpoints" / "image_review" / "post-review-queue-v2.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    item = queue["items"][0]
    item["basis"] = "rewritten after freeze"
    item["item_sha256"] = canonical_hash(
        {key: value for key, value in item.items() if key not in {"schema_version", "item_sha256"}}
    )
    queue["queue_sha256"] = canonical_hash(
        {key: value for key, value in queue.items() if key not in {"schema_version", "queue_sha256"}}
    )
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")


def test_image_review_materialization_receipt_missing_child_refuses_without_regeneration(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-p3-receipt-missing-child"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    queue_path = checkpoint_dir / "post-review-queue-v2.json"
    queue_path.unlink()
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")
    assert not queue_path.exists()


def test_image_review_materialization_zero_approved_rejects_unexpected_fixture(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-zero-approved-unexpected-fixture"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    for row in sheet["cards"]:
        row.update({"disposition": "rejected", "reviewer": "resume-tester", "basis": "not suitable"})
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    fixture_path = run_root / "checkpoints" / "image_review" / "reviewed-image-fixture-v2.json"
    fixture_path.write_text("unexpected", encoding="utf-8")
    providers = _resume_providers(repo_tmp_path)
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")
    assert fixture_path.read_bytes() == b"unexpected"


def test_image_review_materialization_recovers_matching_partial_files_without_rewrite(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-p3-partial-recovery"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    authority = ingest_image_review_sheet(run_root=run_root)
    assert authority["status"] == "ingested"
    from examcard.pipeline.live_checkpoint import materialize_image_review_v2

    materialize_image_review_v2(run_root=run_root, expected_authority=authority)
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    queue_path = checkpoint_dir / "post-review-queue-v2.json"
    audit_path = checkpoint_dir / "image-admission-audit-v2.json"
    queue_before = queue_path.read_bytes()
    audit_before = audit_path.read_bytes()
    (checkpoint_dir / "reviewed-image-fixture-v2.json").unlink()
    (checkpoint_dir / "image-review-materialization-v2.json").unlink()

    materialize_image_review_v2(run_root=run_root, expected_authority=authority)

    assert queue_path.read_bytes() == queue_before
    assert audit_path.read_bytes() == audit_before
    assert (checkpoint_dir / "reviewed-image-fixture-v2.json").is_file()


def test_image_review_materialization_receipt_recovers_runstate_without_rewrite(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-image-review-p3-receipt-recovery"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    authority = ingest_image_review_sheet(run_root=run_root)
    assert authority["status"] == "ingested"
    from examcard.pipeline.live_checkpoint import materialize_image_review_v2

    materialize_image_review_v2(run_root=run_root, expected_authority=authority)
    checkpoint_dir = run_root / "checkpoints" / "image_review"
    before = {
        name: (checkpoint_dir / name).read_bytes()
        for name in (
            "reviewed-image-fixture-v2.json",
            "post-review-queue-v2.json",
            "image-admission-audit-v2.json",
            "image-review-materialization-v2.json",
        )
    }
    providers = _resume_providers(repo_tmp_path)
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (0, "completed")
    assert {
        name: (checkpoint_dir / name).read_bytes()
        for name in before
    } == before
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["materialization_status"] == "frozen"


def test_opt_in_publication_waits_for_authorization_then_freezes_without_image_calls(
    repo_tmp_path: Path,
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-publication-v2-orchestration"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    providers["publication_enabled"] = True

    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_publication")
    publication_dir = run_root / "checkpoints" / "publication"
    manifest_path = publication_dir / "publication-manifest-v2.json"
    assert manifest_path.is_file()
    assert not (publication_dir / "publication-receipt-v2.json").exists()

    from examcard.gates.release.publication_v2 import (
        PUBLICATION_SCOPE_V2,
        build_publication_authorization_v2,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    p4_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="orchestration-human-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=p4_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="controlled publication approval",
        authorized_at="2026-09-11T00:00:00Z",
    )
    (publication_dir / "publication-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    providers["image_caller"] = _must_not_run

    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (0, "completed")
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    publication_ticket = state["checkpoints"]["publication"]
    assert publication_ticket["status"] == "frozen"
    assert publication_ticket["publication_status"] == "frozen"
    assert publication_ticket["publication_artifact_count"] == 2
    assert (publication_dir / "publication-receipt-v2.json").is_file()
    assert len([path for path in (publication_dir / "artifacts").rglob("*") if path.is_file()]) == 2

    state_before_frozen_resume = (run_root / "run_state.json").read_bytes()
    checkpoint_bytes_before = {
        path.relative_to(publication_dir): path.read_bytes()
        for path in publication_dir.rglob("*")
        if path.is_file()
    }
    frozen_bytes = {
        path.name: path.read_bytes()
        for path in publication_dir.glob("publication-*.json")
    }
    third_report, third_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (third_exit, third_report.stop_reason) == (0, "completed")
    assert (run_root / "run_state.json").read_bytes() == state_before_frozen_resume
    assert {
        path.relative_to(publication_dir): path.read_bytes()
        for path in publication_dir.rglob("*")
        if path.is_file()
    } == checkpoint_bytes_before
    assert {
        path.name: path.read_bytes()
        for path in publication_dir.glob("publication-*.json")
    } == frozen_bytes

    state_before_tamper = (run_root / "run_state.json").read_bytes()
    authorization_path = publication_dir / "publication-authorization-v2.json"
    authorization_path.write_bytes(authorization_path.read_bytes() + b"\n")
    tampered_report, tampered_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (tampered_exit, tampered_report.stop_reason) == (1, "gate_refused_publication")
    assert (run_root / "run_state.json").read_bytes() == state_before_tamper


def test_p6_delivery_uses_frozen_publication_identity_and_is_idempotent(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-delivery-v2-orchestration"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    from examcard.gates.release.delivery_v2 import build_delivery_target_v2
    from examcard.gates.release.publication_v2 import (
        PUBLICATION_SCOPE_V2,
        build_publication_authorization_v2,
    )

    target = build_delivery_target_v2(
        target_id="target:orchestration",
        target_type="archive",
        destination_reference="offline://orchestration",
        destination_config_sha256="6" * 64,
        allowed_scope=PUBLICATION_SCOPE_V2,
    )

    class Adapter:
        def __init__(self) -> None:
            self.calls = 0

        def deliver(self, *, package_sha256: str, target_sha256: str, idempotency_key: str) -> dict[str, str]:
            assert package_sha256
            assert target_sha256 == target.target_sha256
            assert idempotency_key
            self.calls += 1
            return {"status": "success", "remote_reference": "remote:delivery-1"}

    adapter = Adapter()
    providers = _resume_providers(repo_tmp_path)
    providers.update({"delivery_enabled": True, "delivery_target": target, "delivery_adapter": adapter})

    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_publication")
    publication_dir = run_root / "checkpoints" / "publication"
    manifest = json.loads((publication_dir / "publication-manifest-v2.json").read_text(encoding="utf-8"))
    image_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="delivery-publication-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=image_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="controlled P6 delivery test",
        authorized_at="2026-09-12T00:00:00Z",
    )
    (publication_dir / "publication-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (0, "completed")
    assert adapter.calls == 1
    delivery_dir = run_root / "checkpoints" / "delivery"
    assert (delivery_dir / "delivery-request-v2.json").is_file()
    assert (delivery_dir / "delivery-execution-v2.json").is_file()
    assert (delivery_dir / "delivery-receipt-v2.json").is_file()
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["delivery"]["status"] == "frozen"
    assert state["checkpoints"]["delivery"]["delivery_status"] == "completed"
    assert state["checkpoints"]["delivery"]["delivery_execution_file_sha256"] == sha256_bytes(
        (delivery_dir / "delivery-execution-v2.json").read_bytes()
    )

    request_before = (delivery_dir / "delivery-request-v2.json").read_bytes()
    execution_before = (delivery_dir / "delivery-execution-v2.json").read_bytes()
    receipt_before = (delivery_dir / "delivery-receipt-v2.json").read_bytes()
    state_before = (run_root / "run_state.json").read_bytes()
    third_report, third_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (third_exit, third_report.stop_reason) == (0, "completed")
    assert adapter.calls == 1
    assert (delivery_dir / "delivery-request-v2.json").read_bytes() == request_before
    assert (delivery_dir / "delivery-execution-v2.json").read_bytes() == execution_before
    assert (delivery_dir / "delivery-receipt-v2.json").read_bytes() == receipt_before
    assert (run_root / "run_state.json").read_bytes() == state_before


def test_frozen_delivery_resume_does_not_write_checkpoint(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-delivery-v2-frozen-read-only"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    from examcard.gates.release.delivery_v2 import build_delivery_target_v2
    from examcard.gates.release.publication_v2 import (
        PUBLICATION_SCOPE_V2,
        build_publication_authorization_v2,
    )

    target = build_delivery_target_v2(
        target_id="target:frozen-read-only",
        target_type="archive",
        destination_reference="offline://frozen-read-only",
        destination_config_sha256="7" * 64,
        allowed_scope=PUBLICATION_SCOPE_V2,
    )

    class Adapter:
        def __init__(self) -> None:
            self.calls = 0

        def deliver(self, *, package_sha256: str, target_sha256: str, idempotency_key: str) -> dict[str, str]:
            self.calls += 1
            return {"status": "success", "remote_reference": "remote:frozen-read-only"}

    adapter = Adapter()
    providers = _resume_providers(repo_tmp_path)
    providers.update({"delivery_enabled": True, "delivery_target": target, "delivery_adapter": adapter})
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_publication")
    publication_dir = run_root / "checkpoints" / "publication"
    manifest = json.loads((publication_dir / "publication-manifest-v2.json").read_text(encoding="utf-8"))
    image_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="delivery-frozen-read-only-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=image_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="frozen P6 delivery resume test",
        authorized_at="2026-09-12T00:00:00Z",
    )
    (publication_dir / "publication-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (0, "completed")
    assert adapter.calls == 1

    delivery_dir = run_root / "checkpoints" / "delivery"
    before = {
        path: path.read_bytes()
        for path in (run_root / "run_state.json", *delivery_dir.glob("delivery-*.json"))
    }
    blocked = Adapter()
    providers["delivery_adapter"] = blocked
    third_report, third_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (third_exit, third_report.stop_reason) == (0, "completed")
    assert blocked.calls == 0
    assert {
        path: path.read_bytes()
        for path in (run_root / "run_state.json", *delivery_dir.glob("delivery-*.json"))
    } == before


def test_p7_filesystem_delivery_preserves_frozen_p5_bytes_and_p6_resume(
    repo_tmp_path: Path,
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260912-p7-filesystem-delivery"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    destination_root = repo_tmp_path / "external-delivery"
    destination_root.mkdir()

    from examcard.delivery.filesystem_v1 import (
        FilesystemDirectoryDeliveryAdapter,
        build_filesystem_delivery_target,
    )
    from examcard.gates.release.publication_v2 import (
        PUBLICATION_SCOPE_V2,
        build_publication_authorization_v2,
    )

    target = build_filesystem_delivery_target(destination_root)
    adapter = FilesystemDirectoryDeliveryAdapter(
        publication_dir=run_root / "checkpoints" / "publication",
        destination_root=destination_root,
        target=target,
    )
    providers = _resume_providers(repo_tmp_path)
    providers.update({"delivery_enabled": True, "delivery_target": target, "delivery_adapter": adapter})

    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_publication")
    publication_dir = run_root / "checkpoints" / "publication"
    manifest = json.loads((publication_dir / "publication-manifest-v2.json").read_text(encoding="utf-8"))
    image_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="p7-filesystem-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=image_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="filesystem delivery integration test",
        authorized_at="2026-09-12T00:00:00Z",
    )
    (publication_dir / "publication-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (0, "completed")
    delivery_dir = run_root / "checkpoints" / "delivery"
    receipt = json.loads((delivery_dir / "delivery-receipt-v2.json").read_text(encoding="utf-8"))
    key = receipt["idempotency_key"]
    remote_dir = destination_root / key
    assert receipt["remote_reference"] == remote_dir.resolve().as_uri()
    assert (remote_dir / "publication-manifest-v2.json").read_bytes() == (
        publication_dir / "publication-manifest-v2.json"
    ).read_bytes()
    for artifact in manifest["artifacts"]:
        assert (remote_dir / artifact["relative_path"]).read_bytes() == (
            publication_dir / artifact["relative_path"]
        ).read_bytes()
    assert (remote_dir / "delivery-complete-v1.json").is_file()
    assert adapter.lookup(idempotency_key=key).status == "success"

    before = {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    }
    request_before = (delivery_dir / "delivery-request-v2.json").read_bytes()
    execution_before = (delivery_dir / "delivery-execution-v2.json").read_bytes()
    receipt_before = (delivery_dir / "delivery-receipt-v2.json").read_bytes()
    state_before = (run_root / "run_state.json").read_bytes()
    third_report, third_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (third_exit, third_report.stop_reason) == (0, "completed")
    assert (delivery_dir / "delivery-request-v2.json").read_bytes() == request_before
    assert (delivery_dir / "delivery-execution-v2.json").read_bytes() == execution_before
    assert (delivery_dir / "delivery-receipt-v2.json").read_bytes() == receipt_before
    assert (run_root / "run_state.json").read_bytes() == state_before
    assert {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    } == before


def test_p7_filesystem_remote_success_reconciles_after_receipt_write_failure(
    repo_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260912-p7-filesystem-receipt-recovery"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    destination_root = repo_tmp_path / "external-delivery"
    destination_root.mkdir()

    from examcard.delivery.filesystem_v1 import (
        FilesystemDirectoryDeliveryAdapter,
        build_filesystem_delivery_target,
    )
    from examcard.gates.release.publication_v2 import (
        PUBLICATION_SCOPE_V2,
        build_publication_authorization_v2,
    )

    target = build_filesystem_delivery_target(destination_root)
    adapter = FilesystemDirectoryDeliveryAdapter(
        publication_dir=run_root / "checkpoints" / "publication",
        destination_root=destination_root,
        target=target,
    )

    class AmbiguousAdapter:
        def __init__(self) -> None:
            self.deliver_calls = 0
            self.lookup_calls = 0

        def deliver(self, **kwargs: object) -> dict[str, str]:
            self.deliver_calls += 1
            result = adapter.deliver(**kwargs)  # type: ignore[arg-type]
            assert result.status == "success"
            return {"status": "unknown"}

        def lookup(self, *, idempotency_key: str) -> object:
            self.lookup_calls += 1
            return adapter.lookup(idempotency_key=idempotency_key)

    ambiguous_adapter = AmbiguousAdapter()
    providers = _resume_providers(repo_tmp_path)
    providers.update(
        {"delivery_enabled": True, "delivery_target": target, "delivery_adapter": ambiguous_adapter}
    )
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_publication")
    publication_dir = run_root / "checkpoints" / "publication"
    manifest = json.loads((publication_dir / "publication-manifest-v2.json").read_text(encoding="utf-8"))
    image_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="p7-filesystem-recovery-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=image_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="filesystem receipt recovery test",
        authorized_at="2026-09-12T00:00:00Z",
    )
    (publication_dir / "publication-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    import examcard.pipeline.live_orchestrate as live_orchestrate_module

    real_materialize = live_orchestrate_module.materialize_delivery_v2

    def fail_local_receipt(*args: object, **kwargs: object) -> dict[str, object]:
        execution = kwargs.get("execution")
        if kwargs.get("receipt") is not None or getattr(execution, "execution_status", None) == "unknown":
            raise OSError("forced local receipt persistence failure")
        return real_materialize(*args, **kwargs)

    monkeypatch.setattr(live_orchestrate_module, "materialize_delivery_v2", fail_local_receipt)
    failed_report, failed_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (failed_exit, failed_report.stop_reason) == (1, "delivery_outcome_unknown")
    delivery_dir = run_root / "checkpoints" / "delivery"
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    key = state["checkpoints"]["delivery"]["idempotency_key"]
    remote_dir = destination_root / key
    assert (remote_dir / "delivery-complete-v1.json").is_file()
    remote_before = {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    }
    assert not (delivery_dir / "delivery-receipt-v2.json").exists()
    assert adapter.lookup(idempotency_key=key).status == "success"

    monkeypatch.setattr(live_orchestrate_module, "materialize_delivery_v2", real_materialize)
    recovered_report, recovered_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (recovered_exit, recovered_report.stop_reason) == (0, "completed")
    recovered = json.loads((delivery_dir / "delivery-receipt-v2.json").read_text(encoding="utf-8"))
    assert recovered["idempotency_key"] == key
    assert recovered["remote_reference"] == remote_dir.resolve().as_uri()
    assert ambiguous_adapter.deliver_calls == 1
    assert ambiguous_adapter.lookup_calls == 1
    assert {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    } == remote_before


def test_p7_real_filesystem_success_recovers_local_receipt_write_failure(
    repo_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260912-p7-filesystem-real-success-recovery"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    destination_root = repo_tmp_path / "external-delivery"
    destination_root.mkdir()

    from examcard.delivery.filesystem_v1 import (
        FilesystemDirectoryDeliveryAdapter,
        build_filesystem_delivery_target,
    )
    from examcard.gates.release.publication_v2 import (
        PUBLICATION_SCOPE_V2,
        build_publication_authorization_v2,
    )

    target = build_filesystem_delivery_target(destination_root)
    adapter = FilesystemDirectoryDeliveryAdapter(
        publication_dir=run_root / "checkpoints" / "publication",
        destination_root=destination_root,
        target=target,
    )

    real_deliver = adapter.deliver
    real_lookup = adapter.lookup
    deliver_calls = 0
    lookup_calls = 0

    def counted_deliver(*, package_sha256: str, target_sha256: str, idempotency_key: str) -> object:
        nonlocal deliver_calls
        deliver_calls += 1
        return real_deliver(
            package_sha256=package_sha256,
            target_sha256=target_sha256,
            idempotency_key=idempotency_key,
        )

    def counted_lookup(*, idempotency_key: str) -> object:
        nonlocal lookup_calls
        lookup_calls += 1
        return real_lookup(idempotency_key=idempotency_key)

    monkeypatch.setattr(adapter, "deliver", counted_deliver)
    monkeypatch.setattr(adapter, "lookup", counted_lookup)
    providers = _resume_providers(repo_tmp_path)
    providers.update(
        {"delivery_enabled": True, "delivery_target": target, "delivery_adapter": adapter}
    )
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_publication")
    publication_dir = run_root / "checkpoints" / "publication"
    manifest = json.loads((publication_dir / "publication-manifest-v2.json").read_text(encoding="utf-8"))
    image_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="p7-filesystem-real-success-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=image_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="filesystem receipt persistence recovery test",
        authorized_at="2026-09-12T00:00:00Z",
    )
    (publication_dir / "publication-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    import examcard.pipeline.live_orchestrate as live_orchestrate_module

    real_materialize = live_orchestrate_module.materialize_delivery_v2
    receipt_write_failed = False

    def fail_local_receipt_persistence(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal receipt_write_failed
        if kwargs.get("receipt") is not None:
            receipt_write_failed = True
            raise OSError("forced local receipt persistence failure")
        return real_materialize(*args, **kwargs)

    monkeypatch.setattr(live_orchestrate_module, "materialize_delivery_v2", fail_local_receipt_persistence)
    failed_report, failed_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (failed_exit, failed_report.stop_reason) == (1, "delivery_outcome_unknown")
    assert receipt_write_failed is True
    assert deliver_calls == 1
    delivery_dir = run_root / "checkpoints" / "delivery"
    state_after_failure = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    key = state_after_failure["checkpoints"]["delivery"]["idempotency_key"]
    remote_dir = destination_root / key
    assert (remote_dir / "delivery-complete-v1.json").is_file()
    request_before = (delivery_dir / "delivery-request-v2.json").read_bytes()
    execution_before = (delivery_dir / "delivery-execution-v2.json").read_bytes()
    execution_before_payload = json.loads(execution_before.decode("utf-8"))
    assert execution_before_payload["execution_status"] == "completed"
    identity_before = state_after_failure["checkpoints"]["delivery"]["delivery_identity"]
    remote_before = {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    }
    assert not (delivery_dir / "delivery-receipt-v2.json").exists()

    monkeypatch.setattr(live_orchestrate_module, "materialize_delivery_v2", real_materialize)
    recovered_report, recovered_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (recovered_exit, recovered_report.stop_reason) == (0, "completed"), [
        (stage.stage, stage.status, stage.detail) for stage in recovered_report.stages
    ]
    recovered = json.loads((delivery_dir / "delivery-receipt-v2.json").read_text(encoding="utf-8"))
    assert recovered["idempotency_key"] == key
    assert recovered["delivery_identity"] == identity_before
    assert recovered["remote_reference"] == remote_dir.resolve().as_uri()
    assert deliver_calls == 1
    assert lookup_calls == 1
    assert (delivery_dir / "delivery-request-v2.json").read_bytes() == request_before
    assert (delivery_dir / "delivery-execution-v2.json").read_bytes() == execution_before
    assert {
        path.relative_to(destination_root): path.read_bytes()
        for path in destination_root.rglob("*")
        if path.is_file()
    } == remote_before


def test_p6_completed_resume_without_lookup_recovers_local_receipt(
    repo_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260912-p6-no-lookup-recovery"
    run_root = _seed_paused_run(repo_tmp_path, run_id)

    from examcard.gates.release.delivery_v2 import build_delivery_target_v2
    from examcard.gates.release.publication_v2 import (
        PUBLICATION_SCOPE_V2,
        build_publication_authorization_v2,
    )

    target = build_delivery_target_v2(
        target_id="target:p6-no-lookup",
        target_type="archive",
        destination_reference="offline://p6-no-lookup",
        destination_config_sha256="8" * 64,
        allowed_scope=PUBLICATION_SCOPE_V2,
    )

    class NoLookupAdapter:
        def __init__(self) -> None:
            self.deliver_calls = 0

        def deliver(
            self, *, package_sha256: str, target_sha256: str, idempotency_key: str
        ) -> dict[str, str]:
            assert package_sha256
            assert target_sha256 == target.target_sha256
            assert idempotency_key
            self.deliver_calls += 1
            return {"status": "success", "remote_reference": "remote:p6-no-lookup"}

    adapter = NoLookupAdapter()
    providers = _resume_providers(repo_tmp_path)
    providers.update(
        {"delivery_enabled": True, "delivery_target": target, "delivery_adapter": adapter}
    )
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_publication")
    publication_dir = run_root / "checkpoints" / "publication"
    manifest = json.loads((publication_dir / "publication-manifest-v2.json").read_text(encoding="utf-8"))
    image_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="p6-no-lookup-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=image_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="completed execution no-lookup recovery test",
        authorized_at="2026-09-12T00:00:00Z",
    )
    (publication_dir / "publication-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    import examcard.pipeline.live_orchestrate as live_orchestrate_module

    real_materialize = live_orchestrate_module.materialize_delivery_v2

    def fail_local_receipt(*args: object, **kwargs: object) -> dict[str, object]:
        if kwargs.get("receipt") is not None:
            raise OSError("forced local receipt persistence failure")
        return real_materialize(*args, **kwargs)

    monkeypatch.setattr(live_orchestrate_module, "materialize_delivery_v2", fail_local_receipt)
    failed_report, failed_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (failed_exit, failed_report.stop_reason) == (1, "delivery_outcome_unknown")
    assert adapter.deliver_calls == 1

    delivery_dir = run_root / "checkpoints" / "delivery"
    request_before = (delivery_dir / "delivery-request-v2.json").read_bytes()
    execution_before = (delivery_dir / "delivery-execution-v2.json").read_bytes()
    state_before = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    identity_before = state_before["checkpoints"]["delivery"]["delivery_identity"]
    key = state_before["checkpoints"]["delivery"]["idempotency_key"]
    assert json.loads(execution_before.decode("utf-8"))["execution_status"] == "completed"
    assert not (delivery_dir / "delivery-receipt-v2.json").exists()

    monkeypatch.setattr(live_orchestrate_module, "materialize_delivery_v2", real_materialize)
    recovered_report, recovered_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (recovered_exit, recovered_report.stop_reason) == (0, "completed")
    assert adapter.deliver_calls == 1
    recovered = json.loads((delivery_dir / "delivery-receipt-v2.json").read_text(encoding="utf-8"))
    assert recovered["delivery_status"] == "completed"
    assert recovered["remote_reference"] is None
    assert recovered["delivery_identity"] == identity_before
    assert recovered["idempotency_key"] == key
    assert (delivery_dir / "delivery-request-v2.json").read_bytes() == request_before
    assert (delivery_dir / "delivery-execution-v2.json").read_bytes() == execution_before


def test_p6_completed_resume_with_unresolved_lookup_stays_reconciliation_required(
    repo_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260912-p6-unresolved-lookup"
    run_root = _seed_paused_run(repo_tmp_path, run_id)

    from examcard.gates.release.delivery_v2 import build_delivery_target_v2
    from examcard.gates.release.publication_v2 import (
        PUBLICATION_SCOPE_V2,
        build_publication_authorization_v2,
    )

    target = build_delivery_target_v2(
        target_id="target:p6-unresolved-lookup",
        target_type="archive",
        destination_reference="offline://p6-unresolved-lookup",
        destination_config_sha256="9" * 64,
        allowed_scope=PUBLICATION_SCOPE_V2,
    )

    class UnresolvedLookupAdapter:
        def __init__(self) -> None:
            self.deliver_calls = 0
            self.lookup_keys: list[str] = []

        def deliver(
            self, *, package_sha256: str, target_sha256: str, idempotency_key: str
        ) -> dict[str, str]:
            assert package_sha256
            assert target_sha256 == target.target_sha256
            assert idempotency_key
            self.deliver_calls += 1
            return {"status": "success", "remote_reference": "remote:p6-unresolved-lookup"}

        def lookup(self, *, idempotency_key: str) -> dict[str, str]:
            self.lookup_keys.append(idempotency_key)
            return {"status": "unknown"}

    adapter = UnresolvedLookupAdapter()
    providers = _resume_providers(repo_tmp_path)
    providers.update(
        {"delivery_enabled": True, "delivery_target": target, "delivery_adapter": adapter}
    )
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_publication")
    publication_dir = run_root / "checkpoints" / "publication"
    manifest = json.loads((publication_dir / "publication-manifest-v2.json").read_text(encoding="utf-8"))
    image_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="p6-unresolved-lookup-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=image_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="completed execution unresolved lookup test",
        authorized_at="2026-09-12T00:00:00Z",
    )
    (publication_dir / "publication-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    import examcard.pipeline.live_orchestrate as live_orchestrate_module

    real_materialize = live_orchestrate_module.materialize_delivery_v2

    def fail_local_receipt(*args: object, **kwargs: object) -> dict[str, object]:
        if kwargs.get("receipt") is not None:
            raise OSError("forced local receipt persistence failure")
        return real_materialize(*args, **kwargs)

    monkeypatch.setattr(live_orchestrate_module, "materialize_delivery_v2", fail_local_receipt)
    failed_report, failed_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (failed_exit, failed_report.stop_reason) == (1, "delivery_outcome_unknown")
    assert adapter.deliver_calls == 1

    delivery_dir = run_root / "checkpoints" / "delivery"
    request_before = (delivery_dir / "delivery-request-v2.json").read_bytes()
    execution_before = (delivery_dir / "delivery-execution-v2.json").read_bytes()
    state_before = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    key = state_before["checkpoints"]["delivery"]["idempotency_key"]
    assert json.loads(execution_before.decode("utf-8"))["execution_status"] == "completed"
    assert not (delivery_dir / "delivery-receipt-v2.json").exists()

    monkeypatch.setattr(live_orchestrate_module, "materialize_delivery_v2", real_materialize)
    recovered_report, recovered_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (recovered_exit, recovered_report.stop_reason) == (1, "delivery_outcome_unknown")
    assert adapter.deliver_calls == 1
    assert adapter.lookup_keys == [key]
    assert not (delivery_dir / "delivery-receipt-v2.json").exists()
    assert (delivery_dir / "delivery-request-v2.json").read_bytes() == request_before
    assert (delivery_dir / "delivery-execution-v2.json").read_bytes() == execution_before

    later_report, later_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (later_exit, later_report.stop_reason) == (1, "delivery_outcome_unknown")
    assert adapter.deliver_calls == 1
    assert adapter.lookup_keys == [key, key]
    assert not (delivery_dir / "delivery-receipt-v2.json").exists()
    assert (delivery_dir / "delivery-execution-v2.json").read_bytes() == execution_before


def test_resume_with_undecided_sheet_stays_waiting(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-fixture3"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"][1]["disposition"] = None
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), _resume_providers(repo_tmp_path))
    assert exit_code == 2
    assert report.stop_reason == "waiting_human_image_review"
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "waiting_human"
    assert "sheet_file_sha256" not in state["checkpoints"]["image_review"]
    assert not (run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json").exists()


@pytest.mark.parametrize(
    "artifact",
    ["sheet", "manifest", "request_manifest", "provenance"],
)
def test_already_ingested_resume_refuses_image_review_byte_tamper(
    repo_tmp_path: Path, artifact: str
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = f"img-byte-{artifact}"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")

    paths = {
        "sheet": run_root / "checkpoints" / "image_review" / "image_review_sheet.json",
        "manifest": run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json",
        "request_manifest": run_root / "step4" / "requests-manifest.json",
        "provenance": next((run_root / "step4" / "image-provenance").glob("*.json")),
    }
    target = paths[artifact]
    original = target.read_bytes()
    target.write_bytes(original + b"\n")
    calls: list[Mapping[str, object]] = []
    providers["image_caller"] = lambda payload: (calls.append(payload) or _must_not_run())

    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (1, "gate_refused_admission")
    assert not calls
    assert target.read_bytes() == original + b"\n"


def test_already_ingested_resume_refuses_synchronized_decision_tamper(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "img-sync-tamper"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"][0]["reviewer"] = "attacker"
    sheet["cards"][0]["basis"] = "rewritten basis"
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = build_batch_image_authorization(sheet["cards"])
    manifest_path.write_text(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")
    assert manifest_path.is_file()


@pytest.mark.parametrize(
    "field",
    [
        "sheet_file_sha256",
        "manifest_sha256",
        "manifest_file_sha256",
        "request_manifest_file_sha256",
        "decision_count",
        "approved_count",
        "materialization_receipt_sha256",
        "materialized_decision_count",
        "materialized_reviewed_card_indexes",
    ],
)
def test_already_ingested_resume_refuses_frozen_image_review_ticket_tamper(
    repo_tmp_path: Path, field: str
) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = f"img-ticket-{field}"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    state_path = run_root / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    ticket = state["checkpoints"]["image_review"]
    ticket[field] = (ticket[field] + 1) if isinstance(ticket[field], int) else "0" * 64
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    providers["image_caller"] = _must_not_run

    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")


def test_image_base_url_drift_refuses_resume_before_provider(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-image-base-drift"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    providers["image_base_url"] = "http://127.0.0.1:3200/v1"

    _assert_resume_refused_without_mutation(
        _resume_config(repo_tmp_path, run_id),
        run_id,
        run_root,
        providers,
    )


def test_image_model_drift_refuses_resume_before_provider(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-image-model-drift"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    providers["image_model"] = "different-image-model"

    _assert_resume_refused_without_mutation(
        _resume_config(repo_tmp_path, run_id),
        run_id,
        run_root,
        providers,
    )


def test_image_route_resume_contract_is_v2_and_secret_free(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-image-contract"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    state_path = run_root / "run_state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    run_config = payload["run_config"]

    assert run_config["schema_version"] == 2
    assert run_config["image_base_url"] == "http://127.0.0.1:3100/v1"
    assert run_config["image_model"] == "gpt-image-2"
    serialized = state_path.read_text(encoding="utf-8")
    for forbidden in ("image_api_key", "IMAGE_API_KEY", "API_KEY", "Authorization", "Bearer"):
        assert forbidden not in serialized


def test_image_api_key_rotation_does_not_change_resume_identity(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-image-key-rotation"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    first_providers = _resume_providers(repo_tmp_path)
    first_providers["image_api_key"] = "synthetic-secret-A"
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), first_providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")

    second_providers = _resume_providers(repo_tmp_path)
    second_providers["image_api_key"] = "synthetic-secret-B"
    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), second_providers)
    assert (second_exit, second_report.stop_reason) == (0, "completed")

    serialized = (run_root / "run_state.json").read_text(encoding="utf-8")
    assert "synthetic-secret-A" not in serialized
    assert "synthetic-secret-B" not in serialized


def test_schema_v1_route_contract_refuses_resume_without_migration(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-image-schema-v1"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    state_path = run_root / "run_state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["run_config"]["schema_version"] = 1
    payload["run_config"].pop("image_base_url")
    payload["run_config_sha256"] = canonical_hash(payload["run_config"])
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    before = state_path.read_bytes()

    with pytest.raises(RunStateBindingError):
        orchestrate(_resume_config(repo_tmp_path, run_id), _resume_providers(repo_tmp_path))

    assert state_path.read_bytes() == before


def test_image_review_emit_refuses_bytes_drift(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-emit-drift"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    row = json.loads(sheet_path.read_text(encoding="utf-8"))["cards"][0]
    image_path = run_root / "step4" / row["image_path"]
    image_path.write_bytes(b"replacement bytes")
    with pytest.raises(Step4ImageIntegrityError):
        emit_image_review_sheet(
            run_root=run_root,
            step4_outcome={"cards": [{**row, "outcome": "success"}]},
            storage=run_root / "step4",
        )


@pytest.mark.parametrize("tampered_path", ["../outside.png", "C:/outside.png", ""])
def test_image_review_ingest_refuses_invalid_path(repo_tmp_path: Path, tampered_path: str) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-path-escape"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"][0]["image_path"] = tampered_path
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), _resume_providers(repo_tmp_path))
    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")
    assert not (run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json").exists()


def test_already_ingested_resume_refuses_non_object_sheet(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-invalid-sheet"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest_before = manifest_path.read_bytes()
    (run_root / "checkpoints" / "image_review" / "image_review_sheet.json").write_text("[]", encoding="utf-8")
    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (1, "gate_refused_admission")
    assert manifest_path.read_bytes() == manifest_before
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "refused"


def test_image_review_ingest_refuses_bytes_drift_without_manifest(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-ingest-drift"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    sheet = json.loads((run_root / "checkpoints" / "image_review" / "image_review_sheet.json").read_text(encoding="utf-8"))
    (run_root / "step4" / sheet["cards"][0]["image_path"]).write_bytes(b"replacement bytes")
    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), _resume_providers(repo_tmp_path))
    assert (exit_code, report.stop_reason) == (1, "gate_refused_admission")
    assert not (run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json").exists()


def test_already_ingested_resume_refuses_image_drift_without_rewriting_manifest(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-authorized-drift"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest_before = manifest_path.read_bytes()
    sheet = json.loads((run_root / "checkpoints" / "image_review" / "image_review_sheet.json").read_text(encoding="utf-8"))
    (run_root / "step4" / sheet["cards"][0]["image_path"]).write_bytes(b"replacement bytes")
    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (1, "gate_refused_admission")
    assert manifest_path.read_bytes() == manifest_before
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "refused"


def test_already_ingested_resume_refuses_sample_id_drift_with_same_public_request(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-sample-drift"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest_before = manifest_path.read_bytes()

    drifted_card = SimpleNamespace(
        card_index=1,
        source_card_id="source:card:one",
        sample_id="one-drifted",
        render_presentation="table",
    )
    unchanged_card = _resume_cards()[1]

    def drifted_loader(_release: Path, _handoff: Path) -> tuple[object, object]:
        return SimpleNamespace(cards=(drifted_card, unchanged_card)), SimpleNamespace(fact_count=2)

    drifted_providers = {**providers, "step4_bundle_loader": drifted_loader}

    def stable_prompt_builder(card: SimpleNamespace) -> str:
        return "resume prompt for one" if card.source_card_id == "source:card:one" else "resume prompt for two"

    drifted_providers["prompt_builder"] = stable_prompt_builder
    drifted_config = _resume_config(repo_tmp_path, run_id)
    old_bindings = {
        int(entry["card_index"]): entry
        for entry in json.loads((run_root / "step4" / "requests-manifest.json").read_text(encoding="utf-8"))
    }
    drifted_bindings = prepare_step4_request_bindings(
        release_fixture_path=drifted_config.step3_release_fixture or Path(),
        handoff_path=drifted_config.step4_handoff or Path(),
        prompt_builder=stable_prompt_builder,
        image_model="gpt-image-2",
        bundle_loader=drifted_loader,
    )
    assert old_bindings[1]["http_payload_sha256"] == drifted_bindings[1]["http_payload_sha256"]
    assert old_bindings[1]["sample_id"] != drifted_bindings[1]["sample_id"]

    second_report, second_exit = orchestrate(drifted_config, drifted_providers)
    assert (second_exit, second_report.stop_reason) == (1, "gate_refused_admission")
    assert manifest_path.read_bytes() == manifest_before
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "refused"


def test_already_ingested_resume_refuses_duplicate_review_rows_without_rewriting_manifest(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "20260812-resume-regression-duplicate-review-row"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    providers = _resume_providers(repo_tmp_path)
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    manifest_path = run_root / "checkpoints" / "image_review" / "image-authorization-manifest.json"
    manifest_before = manifest_path.read_bytes()
    sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"].append(dict(sheet["cards"][0]))
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")

    second_report, second_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (second_exit, second_report.stop_reason) == (1, "gate_refused_admission")
    assert manifest_path.read_bytes() == manifest_before
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["image_review"]["status"] == "refused"


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


def test_missing_resume_target_refuses_before_provider(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    run_id = "20260812-missing-resume-target"
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / run_id
    config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step1",),
        mode="dry_run",
        run_label="missing-resume-target",
        resume_run_id=run_id,
        repo_root=repo_tmp_path,
    )

    with pytest.raises(RunStateBindingError, match="missing run_state.json"):
        orchestrate(config, {"pricing": {}, "sample_ids": ("s1",), "step1_runner": _must_not_run})

    assert not run_root.exists()


def test_orphan_resume_directory_refuses_before_provider(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    run_id = "20260812-orphan-resume-directory"
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    marker = run_root / "orphan.marker"
    marker.write_text("preserve orphan contents", encoding="utf-8")
    config = LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=("step1",),
        mode="dry_run",
        run_label="orphan-resume-directory",
        resume_run_id=run_id,
        repo_root=repo_tmp_path,
    )

    with pytest.raises(RunStateBindingError, match="missing run_state.json"):
        orchestrate(config, {"pricing": {}, "sample_ids": ("s1",), "step1_runner": _must_not_run})

    assert marker.read_text(encoding="utf-8") == "preserve orphan contents"
    assert not (run_root / "run_state.json").exists()
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


def _live_step2_test_config(root: Path, stages: tuple[str, ...]) -> LiveRunConfig:
    return LiveRunConfig(
        exam_id=_REHEARSAL_EXAM,
        stages=stages,
        mode="live",
        run_label="semantic-gate-regression",
        budget_usd=10.0,
        skip_preflight=True,
        repo_root=root,
    )


def test_live_step2a_semantic_failure_skips_step2b(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    executor = _executor_with_script(repo_tmp_path, {"s1": ["success"]})
    report, exit_code = orchestrate(
        _live_step2_test_config(repo_tmp_path, ("step1", "step2a", "step2b", "step3")),
        {
            "pricing": {},
            "sample_ids": ("s1",),
            "step1_runner": lambda: {"ok": True},
            "facts_runner": lambda: {"quality_gates_passed": True, "machine_assertions_passed": True},
            "context_builder": _context_builder,
            "stage_executor": executor,
            "gate_evaluator": lambda stage, context, record: {"valid": False},
        },
    )
    statuses = {item.stage: item.status for item in report.stages}
    assert (exit_code, report.stop_reason) == (1, "completed_with_failures")
    assert statuses == {"step1": "completed", "step2a": "completed_with_failures", "step2b": "skipped", "step3": "skipped"}
    assert executor.calls == ["req-s1-gonglu-golden5-rehearsal-step2a-semantic-gate-regression"]


def test_live_step2b_semantic_failure_skips_downstream(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    executor = _executor_with_script(repo_tmp_path, {"s1": ["success", "success"]})
    report, exit_code = orchestrate(
        _live_step2_test_config(repo_tmp_path, ("step1", "step2a", "step2b", "step3")),
        {
            "pricing": {},
            "sample_ids": ("s1",),
            "step1_runner": lambda: {"ok": True},
            "facts_runner": lambda: {"quality_gates_passed": True, "machine_assertions_passed": True},
            "context_builder": _context_builder,
            "stage_executor": executor,
            "gate_evaluator": lambda stage, context, record: {"valid": stage == "step2a"},
        },
    )
    statuses = {item.stage: item.status for item in report.stages}
    assert (exit_code, report.stop_reason) == (1, "completed_with_failures")
    assert statuses == {"step1": "completed", "step2a": "completed", "step2b": "completed_with_failures", "step3": "skipped"}
    assert executor.calls == [
        "req-s1-gonglu-golden5-rehearsal-step2a-semantic-gate-regression",
        "req-s1-gonglu-golden5-rehearsal-step2b-semantic-gate-regression",
    ]
    run_root = repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs" / report.run_id
    assert not (run_root / "checkpoints" / "admission").exists()


def test_live_completed_step2_without_semantic_validation_refuses_resume(repo_tmp_path: Path) -> None:
    _seed_step1_workspace(repo_tmp_path)
    paths = ExamPaths.resolve(repo_tmp_path, _REHEARSAL_EXAM)
    paths.evidence_root.mkdir(parents=True, exist_ok=True)
    run_id = "20260909-missing-semantic-proof"
    config = _live_step2_test_config(repo_tmp_path, ("step2a",))
    config = replace(config, resume_run_id=run_id)
    run_root = paths.live_runs_root / run_id
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
        image_base_url="",
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
    state.set_stage("step2a", "completed")
    calls: list[str] = []

    def must_not_run() -> None:
        calls.append("provider")

    report, exit_code = orchestrate(
        config,
        {"pricing": {}, "sample_ids": ("s1",), "step1_runner": must_not_run},
    )

    assert (exit_code, report.stop_reason) == (1, "completed_with_failures")
    assert report.stages[0].status == "failed"
    assert "semantic validation proof is missing" in report.stages[0].detail
    assert calls == []


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
