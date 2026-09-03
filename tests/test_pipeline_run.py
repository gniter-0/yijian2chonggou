"""pipeline/run：四步离线编排器（fail-closed、provider 注入、物化+冻结）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from examcard.common.freeze import verify_freeze
from examcard.common.hashing import canonical_hash
from examcard.pipeline.run import (
    PIPELINE_STEPS,
    PipelineContext,
    PipelineRunReport,
    PipelineSteps,
    load_pipeline_freeze,
    run_offline,
)


def _steps(**overrides: Any) -> PipelineSteps:
    def make(step: str):
        def runner(context: PipelineContext, payload: Any) -> Any:
            return {"step": step, "upstream": payload, "provider": sorted(context.providers)}

        return runner

    named = {step: make(step) for step in PIPELINE_STEPS}
    named.update(overrides)
    return PipelineSteps(**named)


def test_run_offline_happy_path(tmp_path: Path) -> None:
    context = PipelineContext(run_id="run-001", output_dir=tmp_path, providers={"chat": object()})
    report, outputs = run_offline(context, _steps(), initial_input={"chapter": "01"})
    assert report.completed is True
    assert tuple(item.step for item in report.steps) == PIPELINE_STEPS
    assert all(item.status == "completed" for item in report.steps)
    assert report.network_calls == 0
    assert set(outputs) == set(PIPELINE_STEPS)
    assert outputs["cards"]["upstream"]["step"] == "facts"
    assert outputs["extract"]["provider"] == ["chat"]
    assert report.steps[0].output_sha256 == canonical_hash(outputs["extract"])


def test_run_offline_fail_closed_skips_downstream(tmp_path: Path) -> None:
    def broken(context: PipelineContext, payload: Any) -> Any:
        raise ValueError("evidence extraction rejected a malformed page")

    context = PipelineContext(run_id="run-002", output_dir=tmp_path)
    report, outputs = run_offline(context, _steps(evidence=broken))
    statuses = {item.step: item.status for item in report.steps}
    assert statuses == {"extract": "completed", "evidence": "failed", "facts": "skipped", "cards": "skipped"}
    assert report.completed is False
    assert report.stop_reason.startswith("evidence failed:")
    assert set(outputs) == {"extract"}


def test_run_offline_sanitizes_persisted_exception_details(tmp_path: Path) -> None:
    secret = "Authorization: Bearer super-secret-token sk-12345678ABCDEFGHIJKLMNOP"

    def broken(context: PipelineContext, payload: Any) -> Any:
        raise RuntimeError(secret)

    context = PipelineContext(run_id="run-008", output_dir=tmp_path)
    report, _ = run_offline(context, _steps(extract=broken))
    journal = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    persisted_report = (tmp_path / "pipeline-report.json").read_text(encoding="utf-8")
    failed_detail = report.steps[0].detail

    assert secret not in failed_detail
    assert secret not in report.stop_reason
    assert secret not in journal
    assert secret not in persisted_report
    assert "[REDACTED]" in failed_detail
    assert verify_freeze(tmp_path, load_pipeline_freeze(tmp_path)) is True


def test_run_offline_persists_report_journal_and_freeze(tmp_path: Path) -> None:
    context = PipelineContext(run_id="run-003", output_dir=tmp_path)
    report, _ = run_offline(context, _steps())
    persisted = PipelineRunReport.model_validate_json((tmp_path / "pipeline-report.json").read_text(encoding="utf-8"))
    assert persisted == report
    journal_lines = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["step"] for line in journal_lines] == list(PIPELINE_STEPS)
    snapshot = load_pipeline_freeze(tmp_path)
    assert verify_freeze(tmp_path, snapshot) is True


def test_run_offline_rejects_non_offline_context(tmp_path: Path) -> None:
    context = PipelineContext(run_id="run-004", output_dir=tmp_path, offline=False)
    with pytest.raises(ValueError, match="offline"):
        run_offline(context, _steps())


def test_report_rejects_wrong_step_order_and_inconsistent_flags(tmp_path: Path) -> None:
    context = PipelineContext(run_id="run-005", output_dir=tmp_path)
    report, _ = run_offline(context, _steps())
    payload = report.model_dump(mode="json")
    payload["steps"] = payload["steps"][::-1]
    with pytest.raises(ValidationError, match="in order"):
        PipelineRunReport.model_validate(payload)
    tampered = report.model_dump(mode="json")
    tampered["completed"] = False
    with pytest.raises(ValidationError):
        PipelineRunReport.model_validate(tampered)


def test_report_requires_skipped_after_failure(tmp_path: Path) -> None:
    def broken(context: PipelineContext, payload: Any) -> Any:
        raise KeyError("missing chapter")

    context = PipelineContext(run_id="run-006", output_dir=tmp_path)
    report, _ = run_offline(context, _steps(extract=broken))
    payload = report.model_dump(mode="json")
    completed_step = dict(payload["steps"][1])
    completed_step.update({"status": "completed", "output_sha256": "0" * 64})
    payload["steps"] = [payload["steps"][0], completed_step, *payload["steps"][2:]]
    with pytest.raises(ValidationError):
        PipelineRunReport.model_validate(payload)


def test_step_output_must_be_canonical_serializable(tmp_path: Path) -> None:
    def opaque(context: PipelineContext, payload: Any) -> Any:
        return object()

    context = PipelineContext(run_id="run-007", output_dir=tmp_path)
    report, outputs = run_offline(context, _steps(facts=opaque))
    statuses = {item.step: item.status for item in report.steps}
    assert statuses["facts"] == "failed"
    assert statuses["cards"] == "skipped"
    assert "facts" not in outputs
