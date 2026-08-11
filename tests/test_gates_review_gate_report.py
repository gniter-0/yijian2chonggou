"""聚合门禁 / 回归矩阵 / 报告物化骨架（消费 common 底座）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from examcard.common.freeze import verify_freeze
from examcard.common.io import read_json
from examcard.gates.review.gate import ReviewAdmissionReadiness, build_admission_readiness, build_review_gate
from examcard.gates.review.regression import all_regressions_caught, build_regression_matrix, run_admission_regression
from examcard.gates.review.report import build_acceptance_report, build_machine_report, persist_review_artifacts

JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="suite" tests="4" failures="0" errors="0" skipped="1"/>
"""


def test_review_gate_valid_formula_and_counts() -> None:
    gate = build_review_gate(
        {"freeze-valid": True, "manifest-valid": True},
        dispositions={"a": "reviewed_candidate", "b": "manual_review"},
        queue_entry_count=2,
        decision_count=2,
    )
    assert gate.valid is True
    assert gate.reviewed_candidate_count == 1 and gate.manual_review_count == 1
    failing = build_review_gate({"freeze-valid": True, "manifest-valid": False})
    assert failing.valid is False
    assert failing.failure_codes == ("manifest-valid",)


def test_admission_readiness_requires_reviewed_candidates() -> None:
    ready_gate = build_review_gate({"all-ok": True}, dispositions={"a": "reviewed_candidate"})
    readiness = build_admission_readiness(ready_gate)
    assert readiness.valid is True and readiness.fixture_ready is True
    empty_gate = build_review_gate({"all-ok": True})
    not_ready = build_admission_readiness(empty_gate)
    assert not_ready.valid is True and not_ready.fixture_ready is False
    payload = readiness.model_dump(mode="json")
    payload["cards_created"] = True
    with pytest.raises(ValidationError):
        ReviewAdmissionReadiness.model_validate(payload)


def test_regression_matrix_records_catch_and_miss() -> None:
    def rejecting() -> None:
        raise ValueError("mutation rejected by validator")

    matrix = build_regression_matrix(
        {
            "tampered-hash": ("hash mismatch", rejecting),
            "accepted-mutation": ("must fail", lambda: None),
        }
    )
    by_case = {item.case_id: item for item in matrix}
    assert by_case["tampered-hash"].caught is True
    assert by_case["accepted-mutation"].caught is False
    assert by_case["accepted-mutation"].detail == "mutation was unexpectedly accepted"
    assert all_regressions_caught(matrix) is False
    assert all_regressions_caught([run_admission_regression("only", "code", rejecting)]) is True


def test_machine_and_acceptance_reports(tmp_path: Path) -> None:
    gate = build_review_gate({"all-ok": True}, dispositions={"a": "reviewed_candidate"})
    readiness = build_admission_readiness(gate)
    report = build_machine_report(
        run_id="review-run-1",
        gate_sha256=gate.gate_sha256,
        readiness_sha256=readiness.readiness_sha256,
        disposition_counts={"reviewed_candidate": 1},
        stop_reason="completed",
    )
    assert report.reviewed_candidate_count == 1
    targeted = tmp_path / "targeted.xml"
    full = tmp_path / "full.xml"
    targeted.write_text(JUNIT_XML, encoding="utf-8")
    full.write_text(JUNIT_XML, encoding="utf-8")
    acceptance = build_acceptance_report(
        run_id="review-run-1", gate_valid=gate.valid, targeted_junit=targeted, full_junit=full, stop_reason="completed"
    )
    assert acceptance.accepted is True
    assert tuple(item.label for item in acceptance.receipts) == ("targeted", "full")


def test_persist_review_artifacts_freezes_outputs(tmp_path: Path) -> None:
    output = tmp_path / "run"
    snapshot = persist_review_artifacts(output, {"gate.json": {"valid": True}, "report.json": {"run": 1}})
    assert verify_freeze(output, snapshot) is True
    assert read_json(output / "gate.json") == {"valid": True}
    (output / "gate.json").write_text("{}", encoding="utf-8")
    assert verify_freeze(output, snapshot) is False
    with pytest.raises(ValueError, match="relative file name"):
        persist_review_artifacts(output, {"../escape.json": {}})
    with pytest.raises(ValueError, match="nothing to persist"):
        persist_review_artifacts(output, {})
