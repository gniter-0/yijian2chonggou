"""chat 信封白名单/画像门禁（r94/r96 场景，录制 fixture 驱动，纯离线）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examcard.common.gates import run_fail_closed
from examcard.providers import (
    ChatEnvelopeInspection,
    StubInnerGate,
    build_chat_allowlist,
    build_chat_profile,
    inspect_chat_envelope,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"


def _body(name: str) -> bytes:
    return (FIXTURES / name).read_text(encoding="utf-8").encode("utf-8")


def _inner_ok(normalized: bytes) -> StubInnerGate:
    document = json.loads(normalized.decode("utf-8"))
    assert set(document) == {"choices"}, "inner inspector must receive the minimal choices envelope"
    choice = document["choices"][0]
    assert set(choice) == {"index", "message"}, "transport-only fields must be stripped before forwarding"
    return StubInnerGate(valid=True)


def _inner_fail(normalized: bytes) -> StubInnerGate:
    return StubInnerGate(valid=False, failure_codes=("result-schema",))


def _inspect_allowlist(name: str, inner=_inner_ok) -> ChatEnvelopeInspection:
    return inspect_chat_envelope(_body(name), allowlist=build_chat_allowlist(), inner_inspector=inner)


def _inspect_profile(name: str, inner=_inner_ok, expected_model: str | None = None) -> ChatEnvelopeInspection:
    return inspect_chat_envelope(
        _body(name), profile=build_chat_profile(expected_model=expected_model), inner_inspector=inner
    )


class TestChatAllowlist:
    def test_minimal_envelope_admitted(self) -> None:
        inspection = _inspect_allowlist("chat_valid_minimal.json")
        assert inspection.gate.valid
        assert inspection.gate.mode == "allowlist"
        assert inspection.normalized_envelope is not None

    def test_full_metadata_envelope_admitted(self) -> None:
        inspection = _inspect_allowlist("chat_valid_full_metadata.json")
        assert inspection.gate.valid
        assert inspection.gate.usage_consistent

    @pytest.mark.parametrize(
        ("fixture", "expected_code"),
        [
            ("chat_unknown_top_level.json", "wrapper-unknown-top-level"),
            ("chat_core_metadata_incomplete.json", "core-metadata-incomplete"),
            ("chat_usage_mismatch.json", "usage-invalid"),
            ("chat_two_choices.json", "choices-value-transformed"),
            ("chat_padded_content.json", "choices-value-transformed"),
        ],
    )
    def test_envelope_variants_fail_closed(self, fixture: str, expected_code: str) -> None:
        inspection = _inspect_allowlist(fixture)
        assert not inspection.gate.valid
        assert expected_code in inspection.gate.failure_codes
        assert inspection.normalized_envelope is None

    def test_inner_gate_failure_propagates(self) -> None:
        inspection = _inspect_allowlist("chat_valid_minimal.json", inner=_inner_fail)
        assert not inspection.gate.valid
        assert "inner-result-schema" in inspection.gate.failure_codes

    def test_not_json_fails(self) -> None:
        inspection = inspect_chat_envelope(
            b"\xff\xfe not json", allowlist=build_chat_allowlist(), inner_inspector=_inner_ok
        )
        assert not inspection.gate.valid
        assert "wrapper-json-invalid" in inspection.gate.failure_codes


class TestChatProfile:
    def test_profile_envelope_admitted(self) -> None:
        inspection = _inspect_profile("chat_profile_valid.json")
        assert inspection.gate.valid
        assert inspection.gate.mode == "profile"

    def test_expected_model_pin(self) -> None:
        ok = _inspect_profile("chat_profile_valid.json", expected_model="gpt-5.5")
        assert ok.gate.valid
        drifted = _inspect_profile("chat_profile_valid.json", expected_model="gpt-4o")
        assert not drifted.gate.valid
        assert "response-model-mismatch" in drifted.gate.failure_codes

    @pytest.mark.parametrize(
        ("fixture", "expected_code"),
        [
            ("chat_profile_missing_usage_details.json", "usage-keys-not-exact"),
            ("chat_profile_finish_reason_length.json", "choices-value-transformed"),
            ("chat_profile_extra_choice_key.json", "choices-value-transformed"),
            ("chat_profile_unknown_top_level.json", "wrapper-keys-not-exact"),
            ("chat_profile_bad_id.json", "envelope-id-pattern-mismatch"),
        ],
    )
    def test_profile_variants_fail_closed(self, fixture: str, expected_code: str) -> None:
        inspection = _inspect_profile(fixture)
        assert not inspection.gate.valid
        assert expected_code in inspection.gate.failure_codes

    def test_finish_reason_stripped_from_forwarded_envelope(self) -> None:
        inspection = _inspect_profile("chat_profile_valid.json")
        assert inspection.normalized_envelope is not None
        document = json.loads(inspection.normalized_envelope.decode("utf-8"))
        assert "finish_reason" not in document["choices"][0]


class TestContractSelfVerification:
    def test_exactly_one_of_profile_or_allowlist(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            inspect_chat_envelope(b"{}", inner_inspector=_inner_ok)
        with pytest.raises(ValueError, match="exactly one"):
            inspect_chat_envelope(
                b"{}",
                profile=build_chat_profile(),
                allowlist=build_chat_allowlist(),
                inner_inspector=_inner_ok,
            )

    def test_contracts_are_deterministic(self) -> None:
        assert build_chat_allowlist().contract_sha256 == build_chat_allowlist().contract_sha256
        assert build_chat_profile().profile_sha256 == build_chat_profile().profile_sha256

    def test_tampered_gate_hash_rejected(self) -> None:
        inspection = _inspect_allowlist("chat_valid_minimal.json")
        payload = inspection.gate.model_dump(mode="json")
        payload["valid"] = False
        run_fail_closed("chat-gate-tamper", lambda: type(inspection.gate).model_validate(payload))

    def test_tampered_profile_hash_rejected(self) -> None:
        profile = build_chat_profile()
        payload = profile.model_dump(mode="json")
        payload["expected_model"] = "gpt-tampered"
        run_fail_closed("chat-profile-tamper", lambda: type(profile).model_validate(payload))
