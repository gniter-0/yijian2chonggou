"""common/gates：门禁定式（valid==all(checks) and not failure_codes）与 fail-closed 回归定式。"""
from __future__ import annotations

from typing import Literal

import pytest
from pydantic import StrictBool, ValidationError

from examcard.common.gates import (
    FailClosedRegression,
    GateBase,
    GateCheck,
    failed_check_ids,
    run_fail_closed,
)
from examcard.common.hashing import canonical_hash, hash_model


class BucketGate(GateBase):
    schema_version: Literal["bucket_gate_v1"] = "bucket_gate_v1"
    freeze_valid: StrictBool


def _check(check_id: str, passed: bool) -> GateCheck:
    return GateCheck(check_id=check_id, passed=passed, detail="detail")


def test_gate_check_validates_lengths() -> None:
    with pytest.raises(ValidationError):
        GateCheck(check_id="ab", passed=True, detail="d")
    with pytest.raises(ValidationError):
        GateCheck(check_id="abc", passed=True, detail="   ")


def test_failed_check_ids_selects_failing_checks() -> None:
    checks = (_check("first-ok", True), _check("second-bad", False))
    assert failed_check_ids(checks) == ("second-bad",)


def test_gate_base_accepts_consistent_gate() -> None:
    checks = (_check("only-check", True),)
    gate = hash_model(GateBase, {"valid": True, "checks": checks, "failure_codes": ()}, "gate_sha256")
    assert gate.valid is True


def test_gate_base_rejects_valid_true_with_failing_check() -> None:
    checks = (_check("only-check", False),)
    with pytest.raises(ValidationError, match="gate validity mismatch"):
        hash_model(GateBase, {"valid": True, "checks": checks, "failure_codes": ()}, "gate_sha256")


def test_gate_base_rejects_valid_true_with_failure_codes() -> None:
    checks = (_check("only-check", True),)
    with pytest.raises(ValidationError, match="gate validity mismatch"):
        hash_model(GateBase, {"valid": True, "checks": checks, "failure_codes": ("code",)}, "gate_sha256")


def test_gate_base_rejects_hash_tamper() -> None:
    checks = (_check("only-check", True),)
    gate = hash_model(GateBase, {"valid": True, "checks": checks, "failure_codes": ()}, "gate_sha256")
    payload = gate.model_dump(mode="json")
    payload["gate_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="gate hash mismatch"):
        GateBase.model_validate(payload)


def test_gate_subclass_seal_excludes_schema_version_only() -> None:
    checks = (_check("only-check", True),)
    gate = hash_model(BucketGate, {"valid": True, "checks": checks, "failure_codes": (), "freeze_valid": True}, "gate_sha256")
    expected = canonical_hash(gate.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
    assert gate.gate_sha256 == expected


def test_gate_requires_at_least_one_check() -> None:
    with pytest.raises(ValidationError):
        hash_model(GateBase, {"valid": True, "checks": (), "failure_codes": ()}, "gate_sha256")


def test_fail_closed_regression_cannot_record_acceptance() -> None:
    record = FailClosedRegression(case_id="unknown-field")
    assert (record.expected_accepted, record.actual_accepted, record.passed) == (False, False, True)
    with pytest.raises(ValidationError):
        FailClosedRegression.model_validate({"case_id": "x", "actual_accepted": True})


@pytest.mark.parametrize("error", [ValueError("v"), TypeError("t"), KeyError("k")])
def test_run_fail_closed_records_rejection(error: Exception) -> None:
    def rejecting() -> None:
        raise error

    assert run_fail_closed("case-id", rejecting).case_id == "case-id"


def test_run_fail_closed_records_pydantic_validation_error() -> None:
    def rejecting() -> None:
        GateCheck(check_id="ab", passed=True, detail="d")

    assert run_fail_closed("validation-case", rejecting).passed is True


def test_run_fail_closed_raises_when_mutation_is_accepted() -> None:
    with pytest.raises(ValueError, match="accepted-case"):
        run_fail_closed("accepted-case", lambda: None)


def test_run_fail_closed_propagates_unexpected_errors() -> None:
    def crashing() -> None:
        raise RuntimeError("infrastructure failure")

    with pytest.raises(RuntimeError):
        run_fail_closed("crash-case", crashing)
