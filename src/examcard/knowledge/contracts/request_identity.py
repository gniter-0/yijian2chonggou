"""Step 2 request identity: payload/prompt/schema hashes -> request_id/cache_key."""
from __future__ import annotations
import re
from collections.abc import Mapping
from typing import Any

from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from .enums import Step2DecoderConfig, Step2Stage, default_step2_decoder
from .hashing import (
    _OFFLINE_RELAY_BASE_URL,
    _canonical_sha256,
    fingerprint_step2_prompt,
    fingerprint_step2_relay,
)
from .prompts import default_step2_prompt_template
from .versions import LEGACY_STEP2A_REQUEST_VERSION


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class Step2RequestIdentity(StrictModel):
    stage: Step2Stage
    request_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    relay_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    prompt_sha256: str = Field(pattern=_SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    decoder: Step2DecoderConfig
    projection_id: str | None = Field(default=None, pattern=r"^step2a-projection:[0-9a-f]{24}$")
    projection_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    source_manifest_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    content_policy_fingerprint_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    manual_reissue_reason: str | None = None
    request_id: str = Field(pattern=r"^step2[ab]-request:[0-9a-f]{64}$")
    cache_key: str = Field(pattern=r"^step2-cache:[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^step2-[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> "Step2RequestIdentity":
        if self.request_version == LEGACY_STEP2A_REQUEST_VERSION and self.manual_reissue_reason is not None:
            raise ValueError("legacy Step 2A identities cannot carry manual reissue context")
        if self.manual_reissue_reason is not None:
            if self.stage not in {Step2Stage.FACT_PLANNING, Step2Stage.CARD_PLANNING}:
                raise ValueError("manual reissue context is only supported for Step 2A or Step 2B")
            if (
                self.manual_reissue_reason != self.manual_reissue_reason.strip()
                or len(self.manual_reissue_reason) > 160
                or re.fullmatch(r"[a-z0-9][a-z0-9._-]*", self.manual_reissue_reason) is None
            ):
                raise ValueError("manual reissue reason must be a trimmed ASCII audit identifier")
        digest_payload = {
            "stage": self.stage.value,
            "request_version": self.request_version,
            "model": self.model,
            "payload_sha256": self.payload_sha256,
            "relay_fingerprint_sha256": self.relay_fingerprint_sha256,
            "prompt_sha256": self.prompt_sha256,
            "response_schema_sha256": self.response_schema_sha256,
            "decoder": self.decoder.model_dump(mode="json"),
            "projection_id": self.projection_id,
            "projection_sha256": self.projection_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "content_policy_fingerprint_sha256": self.content_policy_fingerprint_sha256,
        }
        if self.manual_reissue_reason is not None and (
            self.stage != Step2Stage.FACT_PLANNING or self.request_version != LEGACY_STEP2A_REQUEST_VERSION
        ):
            digest_payload["manual_reissue_reason"] = self.manual_reissue_reason
        digest = _canonical_sha256(digest_payload)
        if (
            self.request_id != f"{self.stage.value}-request:{digest}"
            or self.cache_key != f"step2-cache:{digest}"
            or self.idempotency_key != f"step2-{digest}"
        ):
            raise ValueError("Step 2 request identity does not match its stable cache contract")
        return self


def make_step2_request_identity(
    *,
    stage: Step2Stage,
    request_version: str,
    model: str,
    payload_sha256: str,
    relay_base_url: str = _OFFLINE_RELAY_BASE_URL,
    prompt_template: str | None = None,
    response_schema: Mapping[str, Any] | None = None,
    decoder: Step2DecoderConfig | None = None,
    projection_id: str | None = None,
    projection_sha256: str | None = None,
    source_manifest_sha256: str | None = None,
    content_policy_fingerprint_sha256: str | None = None,
    manual_reissue_reason: str | None = None,
) -> Step2RequestIdentity:
    template = prompt_template or default_step2_prompt_template(stage, request_version=request_version)
    schema = dict(response_schema or {})
    decoder_config = decoder or default_step2_decoder(stage)
    payload = {
        "stage": stage.value,
        "request_version": request_version,
        "model": model,
        "payload_sha256": payload_sha256,
        "relay_fingerprint_sha256": fingerprint_step2_relay(relay_base_url),
        "prompt_sha256": fingerprint_step2_prompt(template),
        "response_schema_sha256": _canonical_sha256(schema),
        "decoder": decoder_config.model_dump(mode="json"),
        "projection_id": projection_id,
        "projection_sha256": projection_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "content_policy_fingerprint_sha256": content_policy_fingerprint_sha256,
    }
    if manual_reissue_reason is not None and (
        stage != Step2Stage.FACT_PLANNING or request_version != LEGACY_STEP2A_REQUEST_VERSION
    ):
        payload["manual_reissue_reason"] = manual_reissue_reason
    digest = _canonical_sha256(payload)
    return Step2RequestIdentity.model_validate(
        {
            "stage": stage,
            "request_version": request_version,
            "model": model,
            "payload_sha256": payload_sha256,
            "relay_fingerprint_sha256": payload["relay_fingerprint_sha256"],
            "prompt_sha256": payload["prompt_sha256"],
            "response_schema_sha256": payload["response_schema_sha256"],
            "decoder": decoder_config,
            "projection_id": projection_id,
            "projection_sha256": projection_sha256,
            "source_manifest_sha256": source_manifest_sha256,
            "content_policy_fingerprint_sha256": content_policy_fingerprint_sha256,
            "manual_reissue_reason": manual_reissue_reason,
            "request_id": f"{stage.value}-request:{digest}",
            "cache_key": f"step2-cache:{digest}",
            "idempotency_key": f"step2-{digest}",
        }
    )
