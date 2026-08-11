"""中继能力诊断：失败分类税 + 就绪判定 + 探针顺序纪律（r2232 场景）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examcard.providers import (
    CapabilityProbeRecord,
    RelayFailureClass,
    assess_relay_capability,
    sanitize_error_text,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"

_CASES = json.loads((FIXTURES / "capability_cases.json").read_text(encoding="utf-8"))


def _probes(case: dict) -> list[CapabilityProbeRecord]:
    return [CapabilityProbeRecord.model_validate(item) for item in case["probes"]]


@pytest.mark.parametrize("case", _CASES, ids=[case["case_id"] for case in _CASES])
def test_recorded_classification_cases(case: dict) -> None:
    assessment = assess_relay_capability(_probes(case))
    assert assessment.primary_failure_classification is RelayFailureClass(case["expected_classification"])
    assert assessment.relay_ready_for_fresh_business is case["expected_ready"]
    assert assessment.business_call_sent is False
    if not case["expected_ready"]:
        assert assessment.failure_codes, "unready assessment must carry a failure code"


class TestProbeDiscipline:
    def test_duplicate_probe_rejected(self) -> None:
        case = next(item for item in _CASES if item["case_id"] == "all_green")
        probes = _probes(case)
        with pytest.raises(ValueError, match="duplicate"):
            assess_relay_capability(probes + [probes[0]])

    def test_out_of_order_probes_rejected(self) -> None:
        case = next(item for item in _CASES if item["case_id"] == "all_green")
        probes = list(reversed(_probes(case)))
        with pytest.raises(ValueError, match="order"):
            assess_relay_capability(probes)

    def test_strict_probe_requires_chat_success(self) -> None:
        case = next(item for item in _CASES if item["case_id"] == "all_green")
        probes = [probe for probe in _probes(case) if probe.kind.value != "chat_json"]
        with pytest.raises(ValueError, match="chat_json success"):
            assess_relay_capability(probes)

    def test_probe_detail_must_be_sanitized(self) -> None:
        with pytest.raises(ValueError, match="sanitized"):
            CapabilityProbeRecord.model_validate(
                {
                    "kind": "chat_json",
                    "outcome": "failed",
                    "http_status": 401,
                    "sanitized_detail": "denied for Bearer sk-abcdefgh12345678",
                }
            )


class TestSanitizer:
    def test_masks_credentials_and_truncates(self) -> None:
        masked = sanitize_error_text("Bearer sk-abcdefgh12345678 rejected; api_key=topsecret " + "x" * 500)
        assert "sk-" not in masked
        assert "topsecret" not in masked
        assert len(masked) <= 300
