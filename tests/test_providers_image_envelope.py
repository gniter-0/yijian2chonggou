"""图像信封严格适配器与 502 不透明失败（r112/r114/r118 场景，录制 fixture 驱动）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examcard.common.gates import run_fail_closed
from examcard.common.hashing import canonical_json, sha256_bytes
from examcard.providers import (
    StrictImageAdapter,
    build_image_profile,
    build_opaque_failure,
    inspect_image_envelope,
)
from examcard.providers.base import summarize_opaque_http_error

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"


def _case(name: str) -> tuple[int, bytes, str]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload["status"], canonical_json(payload["body"]), payload["prompt"]


PROFILE = build_image_profile()


class TestImageSuccessPath:
    def test_valid_envelope_admitted(self) -> None:
        status, body, prompt = _case("image_valid.json")
        gate = inspect_image_envelope(status, body, prompt, PROFILE)
        assert gate.valid
        assert gate.image_width == PROFILE.image_width
        assert gate.image_height == PROFILE.image_height
        assert gate.image_sha256 is not None
        assert gate.response_sha256 == sha256_bytes(body)

    def test_adapter_returns_untransformed_document(self) -> None:
        status, body, prompt = _case("image_valid.json")
        document = StrictImageAdapter(PROFILE).validate_success_body(status, body, prompt)
        assert document == json.loads(body.decode("utf-8"))


class TestImageFailClosed:
    @pytest.mark.parametrize(
        ("fixture", "failed_check"),
        [
            ("image_url_response.json", "data_item_exact"),
            ("image_two_items.json", "data_item_exact"),
            ("image_bad_b64.json", "b64_strict"),
            ("image_non_png_payload.json", "png_valid"),
            ("image_wrong_size_png.json", "png_dimensions_exact"),
            ("image_usage_mismatch.json", "usage_sums_consistent"),
            ("image_revised_prompt_drift.json", "revised_prompt_exact"),
            ("image_extra_top_level.json", "wrapper_exact"),
        ],
    )
    def test_mutations_rejected(self, fixture: str, failed_check: str) -> None:
        status, body, prompt = _case(fixture)
        gate = inspect_image_envelope(status, body, prompt, PROFILE)
        assert not gate.valid
        assert failed_check in gate.failure_codes
        assert getattr(gate, failed_check) is False
        assert gate.image_sha256 is None

    def test_regression_matrix_fail_closed(self) -> None:
        adapter = StrictImageAdapter(PROFILE)
        for fixture in (
            "image_url_response.json",
            "image_two_items.json",
            "image_bad_b64.json",
            "image_non_png_payload.json",
            "image_wrong_size_png.json",
            "image_usage_mismatch.json",
            "image_revised_prompt_drift.json",
            "image_extra_top_level.json",
        ):
            status, body, prompt = _case(fixture)
            result = run_fail_closed(fixture, lambda: adapter.validate_success_body(status, body, prompt))
            assert result.passed


class TestOpaqueHttpFailure:
    def test_non_200_is_terminal_failure(self) -> None:
        status, body, prompt = _case("image_http_502.json")
        gate = inspect_image_envelope(status, body, prompt, PROFILE)
        assert not gate.valid
        assert gate.http_status_success is False

    def test_success_shaped_body_with_502_status_still_fails(self) -> None:
        _, body, prompt = _case("image_valid.json")
        gate = inspect_image_envelope(502, body, prompt, PROFILE)
        assert not gate.valid
        assert "http_status_success" in gate.failure_codes

    def test_opaque_failure_record_never_persists_body(self) -> None:
        status, body, _ = _case("image_http_502.json")
        record = build_opaque_failure(status, body, detail="upstream said Bearer sk-abcdefgh12345678 failed")
        assert record.http_status == 502
        assert record.body_persisted is False
        assert record.response_sha256 == sha256_bytes(body)
        assert "sk-" not in record.sanitized_detail
        assert "[REDACTED]" in record.sanitized_detail

    def test_opaque_failure_rejects_2xx(self) -> None:
        run_fail_closed("opaque-2xx", lambda: build_opaque_failure(200, b"{}"))

    def test_summarizer_emits_safe_openai_structure_only(self) -> None:
        body = json.dumps(
            {
                "error": {
                    "message": (
                        "Invalid size. Authorization: Bearer sk-supersecret123456 "
                        "Cookie: session=abcdef api_key=another-secret"
                    ),
                    "type": "invalid_request_error",
                    "code": "invalid_size",
                    "param": "size",
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")

        summary = summarize_opaque_http_error(body)

        assert summary == (
            "body=json; envelope=error; class=unsupported_size; "
            "type=invalid_request_error; code=invalid_size; param=size"
        )
        for secret in (
            "Invalid size",
            "Authorization",
            "Bearer",
            "sk-supersecret123456",
            "Cookie",
            "session=abcdef",
            "api_key",
            "another-secret",
        ):
            assert secret not in summary

    def test_summarizer_handles_fastapi_detail_list_without_prose(self) -> None:
        body = json.dumps(
            {"detail": [{"loc": ["body", "size"], "msg": "secret-bearing arbitrary prose"}]},
            separators=(",", ":"),
        ).encode("utf-8")

        summary = summarize_opaque_http_error(body)

        assert summary == "body=json; envelope=detail; class=validation_error"
        assert "secret-bearing" not in summary
        assert "size" not in summary

    def test_summarizer_rejects_non_json_without_copying_input(self) -> None:
        body = b"Bearer sk-supersecret Cookie: session=abcdef"

        summary = summarize_opaque_http_error(body)

        assert summary == "body=non_json; envelope=unknown; class=unknown"
        assert "Bearer" not in summary
        assert "session=abcdef" not in summary

    def test_summarizer_bounds_oversized_body_without_parsing(self) -> None:
        body = b"{" + (b"x" * (64 * 1024)) + b"Bearer sk-supersecret"

        summary = summarize_opaque_http_error(body)

        assert summary == "body=oversized; envelope=unknown; class=unknown"
        assert "sk-supersecret" not in summary

    def test_summarizer_omits_unsafe_structural_identifiers(self) -> None:
        body = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error secret=abcdef",
                    "code": "invalid size with spaces",
                    "param": "size",
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")

        summary = summarize_opaque_http_error(body)

        assert summary == "body=json; envelope=error; class=unknown; param=size"
        assert "invalid_request_error" not in summary
        assert "invalid size" not in summary

    def test_summarizer_omits_regex_valid_credential_shaped_identifiers(self) -> None:
        body = json.dumps(
            {
                "error": {
                    "message": "Invalid size",
                    "type": "access_token",
                    "code": "sk-supersecret123456",
                    "param": "api_key",
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")

        summary = summarize_opaque_http_error(body)

        assert summary == "body=json; envelope=error; class=unsupported_size"
        for secret in ("access_token", "sk-supersecret123456", "api_key"):
            assert secret not in summary

        jwt_body = json.dumps(
            {"error": {"message": "Invalid request", "code": "eyJhbGciOiJIUzI1NiJ9.abc.def"}},
            separators=(",", ":"),
        ).encode("utf-8")
        jwt_summary = summarize_opaque_http_error(jwt_body)
        assert jwt_summary == "body=json; envelope=error; class=invalid_request"
        assert "eyJhbGciOiJIUzI1NiJ9.abc.def" not in jwt_summary
