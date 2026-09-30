"""Provider-neutral dispatch boundary for source-owned Step 2A responses.

The boundary deliberately does not depend on the legacy ``Step2ARequest`` or
``Step2OpenAIAdapter``.  It gives the source-owned lane its own request
identity, strict response decoder, and one-shot transport protocol.  A
transport completion is held in memory only; the dispatch result exposes typed
response data for the next local adapter while serializing only safe metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any, Final, Literal, Protocol

from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from .enums import Step2DecoderConfig, Step2Stage, default_step2_decoder
from .hashing import _canonical_sha256, fingerprint_step2_prompt, fingerprint_step2_relay
from .prompts import default_step2_prompt_template
from .responses import Step2AProductionSourceOwnedArbitraryPDFResultPayload
from .versions import (
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_MODEL_INPUT_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_PROMPT_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
)


type SourceOwnedProviderDispatchStatus = Literal["succeeded", "failed_closed"]
type SourceOwnedProviderResponseStatus = Literal["succeeded", "failed_closed"]

_REQUEST_NAMESPACE = "source_owned_provider_dispatch_v1"
SOURCE_OWNED_PROVIDER_MAX_RETRIES: Final[int] = 0
_REQUEST_ID_PATTERN = r"^source-owned-step2a-request:[0-9a-f]{64}$"
_CACHE_KEY_PATTERN = r"^source-owned-step2-cache:[0-9a-f]{64}$"
_IDEMPOTENCY_KEY_PATTERN = r"^source-owned-step2-idempotency:[0-9a-f]{64}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _source_owned_request_digest(
    *,
    model: str,
    payload_sha256: str,
    relay_fingerprint_sha256: str,
    prompt_sha256: str,
    response_schema_sha256: str,
    decoder: Step2DecoderConfig,
) -> str:
    return _canonical_sha256(
        {
            "namespace": _REQUEST_NAMESPACE,
            "stage": Step2Stage.FACT_PLANNING.value,
            "request_version": PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
            "model_input_version": PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_MODEL_INPUT_VERSION,
            "prompt_version": PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_PROMPT_VERSION,
            "model": model,
            "payload_sha256": payload_sha256,
            "relay_fingerprint_sha256": relay_fingerprint_sha256,
            "prompt_sha256": prompt_sha256,
            "response_schema_sha256": response_schema_sha256,
            "decoder": decoder.model_dump(mode="json"),
        }
    )


class SourceOwnedProviderRequestIdentity(StrictModel):
    """Independent identity for the source-owned provider request lane."""

    request_version: str = (
        PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
    )
    model_input_version: str = (
        PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_MODEL_INPUT_VERSION
    )
    prompt_version: str = (
        PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_PROMPT_VERSION
    )
    model: str = Field(min_length=1)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    relay_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    prompt_sha256: str = Field(pattern=_SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    decoder: Step2DecoderConfig
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    cache_key: str = Field(pattern=_CACHE_KEY_PATTERN)
    idempotency_key: str = Field(pattern=_IDEMPOTENCY_KEY_PATTERN)

    @model_validator(mode="after")
    def validate_identity(self) -> "SourceOwnedProviderRequestIdentity":
        if (
            self.request_version != PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
            or self.model_input_version != PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_MODEL_INPUT_VERSION
            or self.prompt_version != PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_PROMPT_VERSION
        ):
            raise ValueError("source-owned provider request identity uses an incompatible contract version")
        digest = _source_owned_request_digest(
            model=self.model,
            payload_sha256=self.payload_sha256,
            relay_fingerprint_sha256=self.relay_fingerprint_sha256,
            prompt_sha256=self.prompt_sha256,
            response_schema_sha256=self.response_schema_sha256,
            decoder=self.decoder,
        )
        if (
            self.request_id != f"source-owned-step2a-request:{digest}"
            or self.cache_key != f"source-owned-step2-cache:{digest}"
            or self.idempotency_key != f"source-owned-step2-idempotency:{digest}"
        ):
            raise ValueError("source-owned provider request identity does not match its stable contract")
        return self


class SourceOwnedProviderRequest(StrictModel):
    """In-memory request face; source input and prompt are excluded from dumps."""

    identity: SourceOwnedProviderRequestIdentity
    model_input: dict[str, object] = Field(exclude=True)
    prompt_template: str = Field(min_length=1, exclude=True)
    response_schema: dict[str, object] = Field(exclude=True)

    @model_validator(mode="after")
    def validate_request_face(self) -> "SourceOwnedProviderRequest":
        if _canonical_sha256(self.model_input) != self.identity.payload_sha256:
            raise ValueError("source-owned provider request payload identity mismatch")
        if fingerprint_step2_prompt(self.prompt_template) != self.identity.prompt_sha256:
            raise ValueError("source-owned provider request prompt identity mismatch")
        if _canonical_sha256(self.response_schema) != self.identity.response_schema_sha256:
            raise ValueError("source-owned provider request response schema identity mismatch")
        return self


class SourceOwnedProviderCompletion(StrictModel):
    """One raw completion held transiently between transport and decoder."""

    content: str = Field(min_length=1, exclude=True)
    finish_reason: str | None = None


class SourceOwnedProviderResponseMetadata(StrictModel):
    """Safe dispatch metadata; never contains prompt, payload, or raw response."""

    status: SourceOwnedProviderResponseStatus
    response_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    response_length: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    provider_call_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_response_metadata(self) -> "SourceOwnedProviderResponseMetadata":
        if self.status == "succeeded" and self.response_sha256 is None:
            raise ValueError("successful source-owned response metadata requires a response hash")
        return self


class SourceOwnedProviderDispatchResult(StrictModel):
    """Typed dispatch hand-off with response payload excluded from serialization."""

    status: SourceOwnedProviderDispatchStatus
    response: Step2AProductionSourceOwnedArbitraryPDFResultPayload | None = Field(
        default=None,
        exclude=True,
    )
    metadata: SourceOwnedProviderResponseMetadata

    @model_validator(mode="after")
    def validate_dispatch_result(self) -> "SourceOwnedProviderDispatchResult":
        if self.status == "succeeded" and self.response is None:
            raise ValueError("successful source-owned dispatch requires a typed response")
        if self.status == "failed_closed" and self.response is not None:
            raise ValueError("failed source-owned dispatch cannot expose a response")
        if self.status != self.metadata.status:
            raise ValueError("source-owned dispatch and response statuses must agree")
        return self


class SourceOwnedProviderTransport(Protocol):
    """Explicit one-shot transport seam; implementations must not retry."""

    max_retries: int

    def complete_source_owned(
        self,
        *,
        request: SourceOwnedProviderRequest,
        response_schema: Mapping[str, object],
        prompt: str,
    ) -> SourceOwnedProviderCompletion: ...


def build_source_owned_provider_request(
    *,
    model: str,
    model_input: Mapping[str, object],
    relay_base_url: str = "offline://deterministic-fake",
    prompt_template: str | None = None,
    decoder: Step2DecoderConfig | None = None,
) -> SourceOwnedProviderRequest:
    """Build an identity-bound source-owned request without legacy request types."""

    if not model.strip():
        raise ValueError("source-owned provider requests require a model")
    if any(not isinstance(key, str) for key in model_input):
        raise ValueError("source-owned provider model input keys must be strings")
    frozen_model_input = dict(model_input)
    frozen_prompt = prompt_template or default_step2_prompt_template(
        Step2Stage.FACT_PLANNING,
        request_version=PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    )
    frozen_decoder = decoder or default_step2_decoder(Step2Stage.FACT_PLANNING)
    response_schema = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_json_schema()
    payload_sha256 = _canonical_sha256(frozen_model_input)
    relay_fingerprint_sha256 = fingerprint_step2_relay(relay_base_url)
    prompt_sha256 = fingerprint_step2_prompt(frozen_prompt)
    response_schema_sha256 = _canonical_sha256(response_schema)
    digest = _source_owned_request_digest(
        model=model,
        payload_sha256=payload_sha256,
        relay_fingerprint_sha256=relay_fingerprint_sha256,
        prompt_sha256=prompt_sha256,
        response_schema_sha256=response_schema_sha256,
        decoder=frozen_decoder,
    )
    identity = SourceOwnedProviderRequestIdentity(
        model=model,
        payload_sha256=payload_sha256,
        relay_fingerprint_sha256=relay_fingerprint_sha256,
        prompt_sha256=prompt_sha256,
        response_schema_sha256=response_schema_sha256,
        decoder=frozen_decoder,
        request_id=f"source-owned-step2a-request:{digest}",
        cache_key=f"source-owned-step2-cache:{digest}",
        idempotency_key=f"source-owned-step2-idempotency:{digest}",
    )
    return SourceOwnedProviderRequest(
        identity=identity,
        model_input=frozen_model_input,
        prompt_template=frozen_prompt,
        response_schema=response_schema,
    )


def decode_source_owned_provider_completion(
    *,
    request: SourceOwnedProviderRequest,
    completion: SourceOwnedProviderCompletion,
) -> Step2AProductionSourceOwnedArbitraryPDFResultPayload:
    """Strictly decode one source-owned completion without legacy parsing."""

    if not isinstance(request, SourceOwnedProviderRequest):
        raise ValueError("source-owned response decoding requires a source-owned request")
    if not isinstance(completion, SourceOwnedProviderCompletion):
        raise ValueError("source-owned response decoding requires a source-owned completion")
    parsed: Any = json.loads(completion.content)
    if not isinstance(parsed, dict):
        raise ValueError("source-owned provider response must be a JSON object")
    return Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(parsed)


def _dispatch_failure(
    *,
    provider_call_count: int,
    response_sha256: str | None = None,
    response_length: int = 0,
    finish_reason: str | None = None,
) -> SourceOwnedProviderDispatchResult:
    return SourceOwnedProviderDispatchResult(
        status="failed_closed",
        response=None,
        metadata=SourceOwnedProviderResponseMetadata(
            status="failed_closed",
            response_sha256=response_sha256,
            response_length=response_length,
            finish_reason=finish_reason,
            provider_call_count=provider_call_count,
        ),
    )


def dispatch_source_owned_provider_request(
    *,
    request: SourceOwnedProviderRequest,
    transport: SourceOwnedProviderTransport | None,
) -> SourceOwnedProviderDispatchResult:
    """Dispatch exactly once through an explicit transport and decode strictly.

    The seam is offline-gated for this migration.  A missing transport or a
    non-offline environment fails closed before any call.  Once a transport is
    supplied in offline mode, it is invoked exactly once; no retry, fallback,
    response repair, or persistence is performed here.
    """

    if os.environ.get("REAL_NETWORK", "0") != "0":
        return _dispatch_failure(provider_call_count=0)
    if not isinstance(request, SourceOwnedProviderRequest) or transport is None:
        return _dispatch_failure(provider_call_count=0)
    if getattr(transport, "max_retries", SOURCE_OWNED_PROVIDER_MAX_RETRIES) != SOURCE_OWNED_PROVIDER_MAX_RETRIES:
        return _dispatch_failure(provider_call_count=0)
    try:
        completion = transport.complete_source_owned(
            request=request,
            response_schema=request.response_schema,
            prompt=request.prompt_template,
        )
    except Exception:
        return _dispatch_failure(provider_call_count=1)
    if not isinstance(completion, SourceOwnedProviderCompletion):
        return _dispatch_failure(provider_call_count=1)
    response_sha256 = hashlib.sha256(completion.content.encode("utf-8")).hexdigest()
    response_length = len(completion.content)
    try:
        response = decode_source_owned_provider_completion(
            request=request,
            completion=completion,
        )
    except Exception:
        return _dispatch_failure(
            provider_call_count=1,
            response_sha256=response_sha256,
            response_length=response_length,
            finish_reason=completion.finish_reason,
        )
    return SourceOwnedProviderDispatchResult(
        status="succeeded",
        response=response,
        metadata=SourceOwnedProviderResponseMetadata(
            status="succeeded",
            response_sha256=response_sha256,
            response_length=response_length,
            finish_reason=completion.finish_reason,
            provider_call_count=1,
        ),
    )


__all__ = [
    "SourceOwnedProviderCompletion",
    "SourceOwnedProviderDispatchResult",
    "SourceOwnedProviderDispatchStatus",
    "SourceOwnedProviderRequest",
    "SourceOwnedProviderRequestIdentity",
    "SourceOwnedProviderResponseMetadata",
    "SourceOwnedProviderResponseStatus",
    "SourceOwnedProviderTransport",
    "SOURCE_OWNED_PROVIDER_MAX_RETRIES",
    "build_source_owned_provider_request",
    "decode_source_owned_provider_completion",
    "dispatch_source_owned_provider_request",
]
