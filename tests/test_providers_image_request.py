"""HTTP 422 请求兼容：公网五字段投影 + 内部字段 sidecar 隔离（r116 场景）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examcard.common.gates import run_fail_closed
from examcard.common.hashing import canonical_json, sha256_bytes, sha256_text
from examcard.providers import (
    IMAGE_HTTP_PUBLIC_FIELDS,
    PublicImageRequest,
    build_image_profile,
    project_image_request,
)
from examcard.providers.envelope_profiles import KNOWN_INTERNAL_FIELDS

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"
PROFILE = build_image_profile()


def _payload() -> dict:
    return json.loads((FIXTURES / "request_422_regression.json").read_text(encoding="utf-8"))


class TestRequestProjection:
    def test_422_regression_payload_projected(self) -> None:
        payload = _payload()
        projection, sidecar = project_image_request(payload, PROFILE)

        # 公网体=恰好五公有字段，且哈希与 canonical 字节一致。
        body = canonical_json(projection.http_request)
        assert tuple(sorted(json.loads(body))) == tuple(sorted(IMAGE_HTTP_PUBLIC_FIELDS))
        assert projection.http_payload_sha256 == sha256_bytes(body)
        assert projection.http_payload_utf8_bytes == len(body)

        # 历史泄漏字段（schema_version 等）全部进 sidecar、决不进公网体。
        assert "schema_version" in sidecar
        for field in KNOWN_INTERNAL_FIELDS:
            if field in payload:
                assert field in sidecar
                assert field not in json.loads(body)
        assert projection.sidecar_field_names == tuple(sorted(sidecar))
        assert projection.internal_fields_leaked is False

    def test_prompt_never_altered(self) -> None:
        payload = _payload()
        projection, _ = project_image_request(payload, PROFILE)
        assert projection.http_request.prompt == payload["prompt"]
        assert projection.source_prompt_sha256 == sha256_text(payload["prompt"])

    def test_missing_public_field_rejected(self) -> None:
        payload = _payload()
        payload.pop("prompt")
        run_fail_closed("missing-prompt", lambda: project_image_request(payload, PROFILE))

    def test_model_pin_mismatch_rejected(self) -> None:
        payload = _payload()
        payload["model"] = "gpt-image-9"
        run_fail_closed("model-pin", lambda: project_image_request(payload, PROFILE))

    def test_size_pin_mismatch_rejected(self) -> None:
        payload = _payload()
        payload["size"] = "1024x1024"
        run_fail_closed("size-pin", lambda: project_image_request(payload, PROFILE))

    def test_multi_image_request_rejected(self) -> None:
        payload = _payload()
        payload["n"] = 2
        run_fail_closed("n-pin", lambda: project_image_request(payload, PROFILE))


class TestPublicBodyIsSealed:
    def test_internal_field_cannot_enter_public_request(self) -> None:
        # extra=forbid 使内部字段根本装不进公网模型——构造失败本身就是断言。
        run_fail_closed(
            "internal-field-in-public-body",
            lambda: PublicImageRequest.model_validate(
                {
                    "model": "gpt-image-2",
                    "n": 1,
                    "prompt": "p",
                    "response_format": "b64_json",
                    "size": "1536x1024",
                    "schema_version": "step4_image_request_v1",
                }
            ),
        )

    def test_url_response_format_rejected(self) -> None:
        run_fail_closed(
            "url-response-format",
            lambda: PublicImageRequest.model_validate(
                {"model": "gpt-image-2", "n": 1, "prompt": "p", "response_format": "url", "size": "1536x1024"}
            ),
        )

    def test_tampered_projection_hash_rejected(self) -> None:
        projection, _ = project_image_request(_payload(), PROFILE)
        payload = projection.model_dump(mode="json")
        payload["sidecar_field_names"] = ()
        with pytest.raises(ValueError):
            type(projection).model_validate(payload)
