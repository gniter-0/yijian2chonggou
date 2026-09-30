"""One-shot live transport boundary for source-owned Step 2A requests.

This module is deliberately separate from the legacy Step 2 adapter and
transport.  It owns only the live request-to-completion boundary: the
source-owned request already carries its contract identities, the completion
is decoded by the strict source-owned decoder, and the returned result exposes
safe metadata only.  Prompt, model input, provider payload, and raw response
remain transient in memory.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Final, Literal, Protocol

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONObject, ResponseFormatJSONSchema
from pydantic import Field, ValidationError, model_validator

from ..materials.schema import StrictModel
from .contracts.enums import Step2ResponseFormat
from .contracts.source_owned_provider_dispatch import (
    SOURCE_OWNED_PROVIDER_MAX_RETRIES,
    SourceOwnedProviderCompletion,
    SourceOwnedProviderRequest,
    decode_source_owned_provider_completion,
)
from .contracts.responses import Step2AProductionSourceOwnedArbitraryPDFResultPayload


type SourceOwnedLiveDispatchStatus = Literal["succeeded", "failed_closed"]
type SourceOwnedLiveDispatchStage = Literal["gate", "transport", "decoder", "completed"]
type SourceOwnedLiveErrorCategory = Literal[
    "network_disabled",
    "invalid_request",
    "missing_transport",
    "retry_policy_violation",
    "transport_failure",
    "invalid_completion",
    "response_schema_rejected",
]
type SourceOwnedLiveDecoderDiagnosticCategory = Literal[
    "json_decode_failure",
    "top_level_not_object",
    "schema_validation_failure",
    "unexpected_decoder_failure",
]
type SourceOwnedLiveTransportDiagnosticCategory = Literal[
    "timeout",
    "connection_failure",
    "rate_limited",
    "provider_request_rejected",
    "upstream_unavailable",
    "provider_api_error",
    "unexpected_transport_failure",
]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
SOURCE_OWNED_LIVE_PROVIDER_MAX_RETRIES: Final[int] = SOURCE_OWNED_PROVIDER_MAX_RETRIES


class SourceOwnedLiveProviderTransport(Protocol):
    """One-shot source-owned transport protocol.

    Implementations must expose ``max_retries == 0`` and perform one provider
    operation per method call.  The protocol intentionally does not expose a
    legacy ``complete`` method, so a legacy adapter cannot be passed by
    accident.
    """

    max_retries: int

    def complete_source_owned(
        self,
        *,
        request: SourceOwnedProviderRequest,
        response_schema: Mapping[str, object],
        prompt: str,
    ) -> SourceOwnedProviderCompletion: ...


class SourceOwnedLiveDecoderDiagnostic(StrictModel):
    """Content-free, deterministic diagnostics for a rejected completion."""

    category: SourceOwnedLiveDecoderDiagnosticCategory
    validation_error_count: int = Field(default=0, ge=0)
    distinct_error_types: tuple[str, ...] = ()
    missing_field_count: int = Field(default=0, ge=0)
    extra_forbidden_field_count: int = Field(default=0, ge=0)


class SourceOwnedLiveTransportDiagnostic(StrictModel):
    """Content-free classification for a failed provider operation."""

    category: SourceOwnedLiveTransportDiagnosticCategory
    http_status_code: int | None = Field(default=None, ge=100, le=599)


def _transport_diagnostic(error: BaseException) -> SourceOwnedLiveTransportDiagnostic:
    """Classify provider failures without retaining exception content."""

    if isinstance(error, (APITimeoutError, TimeoutError)):
        return SourceOwnedLiveTransportDiagnostic(category="timeout")
    if isinstance(error, (APIConnectionError, ConnectionError)):
        return SourceOwnedLiveTransportDiagnostic(category="connection_failure")
    if isinstance(error, APIStatusError):
        status_code = error.status_code
        if status_code == 429:
            category: SourceOwnedLiveTransportDiagnosticCategory = "rate_limited"
        elif 400 <= status_code < 500:
            category = "provider_request_rejected"
        elif 500 <= status_code < 600:
            category = "upstream_unavailable"
        else:
            category = "provider_api_error"
        return SourceOwnedLiveTransportDiagnostic(
            category=category,
            http_status_code=status_code,
        )
    if isinstance(error, APIError):
        return SourceOwnedLiveTransportDiagnostic(category="provider_api_error")
    return SourceOwnedLiveTransportDiagnostic(category="unexpected_transport_failure")


def _decoder_diagnostic(
    *,
    content: str,
    error: Exception,
) -> SourceOwnedLiveDecoderDiagnostic:
    """Classify decoder failures without retaining exception or response content."""

    if isinstance(error, json.JSONDecodeError):
        return SourceOwnedLiveDecoderDiagnostic(category="json_decode_failure")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return SourceOwnedLiveDecoderDiagnostic(category="json_decode_failure")
    if not isinstance(parsed, dict):
        return SourceOwnedLiveDecoderDiagnostic(category="top_level_not_object")

    if isinstance(error, ValidationError):
        errors = error.errors(include_context=False, include_url=False)
        error_types = sorted(
            {
                str(item.get("type", "unknown"))
                for item in errors
                if str(item.get("type", "unknown"))
            }
        )
        return SourceOwnedLiveDecoderDiagnostic(
            category="schema_validation_failure",
            validation_error_count=len(errors),
            distinct_error_types=tuple(error_types),
            missing_field_count=sum(item.get("type") == "missing" for item in errors),
            extra_forbidden_field_count=sum(
                item.get("type") == "extra_forbidden" for item in errors
            ),
        )

    return SourceOwnedLiveDecoderDiagnostic(category="unexpected_decoder_failure")



class SourceOwnedLiveProviderDispatchResult(StrictModel):
    """Safe live-boundary result; the typed response is never serialized."""

    status: SourceOwnedLiveDispatchStatus
    response: Step2AProductionSourceOwnedArbitraryPDFResultPayload | None = Field(
        default=None,
        exclude=True,
    )
    request_id: str | None = None
    response_schema_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    response_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    response_length: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    provider_call_count: int = Field(default=0, ge=0)
    stage: SourceOwnedLiveDispatchStage
    safe_error_category: SourceOwnedLiveErrorCategory | None = None
    decoder_diagnostic: SourceOwnedLiveDecoderDiagnostic | None = None
    transport_diagnostic: SourceOwnedLiveTransportDiagnostic | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "SourceOwnedLiveProviderDispatchResult":
        if self.status == "succeeded":
            if self.response is None:
                raise ValueError("successful source-owned live dispatch requires a typed response")
            if self.response_sha256 is None:
                raise ValueError("successful source-owned live dispatch requires a response hash")
            if self.provider_call_count != 1:
                raise ValueError("successful source-owned live dispatch requires one provider call")
            if (
                self.stage != "completed"
                or self.safe_error_category is not None
                or self.decoder_diagnostic is not None
                or self.transport_diagnostic is not None
            ):
                raise ValueError("successful source-owned live dispatch has invalid safe metadata")
        else:
            if self.response is not None:
                raise ValueError("failed source-owned live dispatch cannot expose a response")
            if self.stage == "completed" or self.safe_error_category is None:
                raise ValueError("failed source-owned live dispatch requires safe failure metadata")
            if self.stage == "decoder" and self.decoder_diagnostic is None:
                raise ValueError("decoder failure requires safe decoder diagnostics")
            if self.stage != "decoder" and self.decoder_diagnostic is not None:
                raise ValueError("non-decoder failure cannot expose decoder diagnostics")
            if self.stage == "transport" and self.safe_error_category == "transport_failure":
                if self.transport_diagnostic is None:
                    raise ValueError("transport failure requires safe transport diagnostics")
            elif self.transport_diagnostic is not None:
                raise ValueError("only transport failures can expose transport diagnostics")
        return self


# Short alias for callers that use the dispatch terminology from the existing
# source-owned offline seam.
SourceOwnedLiveDispatchResult = SourceOwnedLiveProviderDispatchResult



def _response_format(
    *,
    mode: Step2ResponseFormat,
    response_schema: Mapping[str, object],
) -> ResponseFormatJSONObject | ResponseFormatJSONSchema:
    if mode == Step2ResponseFormat.JSON_SCHEMA_STRICT:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "source_owned_step2a_result",
                "strict": True,
                "schema": dict(response_schema),
            },
        }
    if mode == Step2ResponseFormat.JSON_OBJECT:
        return {"type": "json_object"}
    raise ValueError("unsupported source-owned response format")


class OpenAICompatibleSourceOwnedLiveTransport:
    """Dedicated OpenAI-compatible source-owned transport with retries disabled."""

    max_retries: Final[int] = SOURCE_OWNED_LIVE_PROVIDER_MAX_RETRIES

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.network_call_count = 0
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=timeout,
            default_headers=default_headers or None,
            max_retries=SOURCE_OWNED_LIVE_PROVIDER_MAX_RETRIES,
        )

    @staticmethod
    def _messages(
        *,
        prompt: str,
        model_input: Mapping[str, object],
    ) -> list[ChatCompletionMessageParam]:
        return [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "Return only the required JSON object for this immutable request data:\n"
                + json.dumps(dict(model_input), ensure_ascii=False, sort_keys=True),
            },
        ]

    def complete_source_owned(
        self,
        *,
        request: SourceOwnedProviderRequest,
        response_schema: Mapping[str, object],
        prompt: str,
    ) -> SourceOwnedProviderCompletion:
        """Make exactly one provider call and return a transient completion."""

        if self.max_retries != SOURCE_OWNED_LIVE_PROVIDER_MAX_RETRIES:
            raise ValueError("source-owned live transport retry policy is not zero")
        self.network_call_count += 1
        decoder = request.identity.decoder
        raw = self._client.chat.completions.with_raw_response.create(
            model=request.identity.model,
            messages=self._messages(prompt=prompt, model_input=request.model_input),
            max_tokens=decoder.max_tokens,
            temperature=decoder.temperature,
            response_format=_response_format(
                mode=decoder.response_format,
                response_schema=response_schema,
            ),
            extra_headers={"Idempotency-Key": request.identity.idempotency_key},
        )
        response = raw.parse()
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        if not content:
            raise ValueError("source-owned provider returned an empty completion")
        finish_reason = getattr(choice, "finish_reason", None)
        return SourceOwnedProviderCompletion(
            content=content,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )


def _failure(
    *,
    request: SourceOwnedProviderRequest | None,
    stage: SourceOwnedLiveDispatchStage,
    category: SourceOwnedLiveErrorCategory,
    provider_call_count: int,
    response_sha256: str | None = None,
    response_length: int = 0,
    finish_reason: str | None = None,
    decoder_diagnostic: SourceOwnedLiveDecoderDiagnostic | None = None,
    transport_diagnostic: SourceOwnedLiveTransportDiagnostic | None = None,
) -> SourceOwnedLiveProviderDispatchResult:
    return SourceOwnedLiveProviderDispatchResult(
        status="failed_closed",
        response=None,
        request_id=request.identity.request_id if request is not None else None,
        response_schema_sha256=request.identity.response_schema_sha256 if request is not None else None,
        response_sha256=response_sha256,
        response_length=response_length,
        finish_reason=finish_reason,
        provider_call_count=provider_call_count,
        stage=stage,
        safe_error_category=category,
        decoder_diagnostic=decoder_diagnostic,
        transport_diagnostic=transport_diagnostic,
    )


def dispatch_source_owned_live_provider_request(
    *,
    request: SourceOwnedProviderRequest,
    transport: SourceOwnedLiveProviderTransport | None,
) -> SourceOwnedLiveProviderDispatchResult:
    """Run the source-owned live boundary once, then decode strictly.

    The explicit ``REAL_NETWORK=1`` gate is checked before transport access.
    There is no retry loop, fallback transport, response repair, or
    persistence path in this function.
    """

    if os.environ.get("REAL_NETWORK", "0") != "1":
        return _failure(
            request=request if isinstance(request, SourceOwnedProviderRequest) else None,
            stage="gate",
            category="network_disabled",
            provider_call_count=0,
        )
    if not isinstance(request, SourceOwnedProviderRequest):
        return _failure(
            request=None,
            stage="gate",
            category="invalid_request",
            provider_call_count=0,
        )
    if transport is None:
        return _failure(
            request=request,
            stage="gate",
            category="missing_transport",
            provider_call_count=0,
        )
    if getattr(transport, "max_retries", None) != SOURCE_OWNED_LIVE_PROVIDER_MAX_RETRIES:
        return _failure(
            request=request,
            stage="gate",
            category="retry_policy_violation",
            provider_call_count=0,
        )

    try:
        completion = transport.complete_source_owned(
            request=request,
            response_schema=request.response_schema,
            prompt=request.prompt_template,
        )
    except Exception as error:
        return _failure(
            request=request,
            stage="transport",
            category="transport_failure",
            provider_call_count=1,
            transport_diagnostic=_transport_diagnostic(error),
        )
    if not isinstance(completion, SourceOwnedProviderCompletion):
        return _failure(
            request=request,
            stage="transport",
            category="invalid_completion",
            provider_call_count=1,
        )

    response_sha256 = hashlib.sha256(completion.content.encode("utf-8")).hexdigest()
    response_length = len(completion.content)
    try:
        response = decode_source_owned_provider_completion(
            request=request,
            completion=completion,
        )
    except Exception as error:
        return _failure(
            request=request,
            stage="decoder",
            category="response_schema_rejected",
            provider_call_count=1,
            response_sha256=response_sha256,
            response_length=response_length,
            finish_reason=completion.finish_reason,
            decoder_diagnostic=_decoder_diagnostic(content=completion.content, error=error),
        )
    return SourceOwnedLiveProviderDispatchResult(
        status="succeeded",
        response=response,
        request_id=request.identity.request_id,
        response_schema_sha256=request.identity.response_schema_sha256,
        response_sha256=response_sha256,
        response_length=response_length,
        finish_reason=completion.finish_reason,
        provider_call_count=1,
        stage="completed",
        safe_error_category=None,
    )


__all__ = [
    "OpenAICompatibleSourceOwnedLiveTransport",
    "SOURCE_OWNED_LIVE_PROVIDER_MAX_RETRIES",
    "SourceOwnedLiveDispatchResult",
    "SourceOwnedLiveDispatchStage",
    "SourceOwnedLiveDispatchStatus",
    "SourceOwnedLiveDecoderDiagnostic",
    "SourceOwnedLiveTransportDiagnostic",
    "SourceOwnedLiveTransportDiagnosticCategory",
    "SourceOwnedLiveDecoderDiagnosticCategory",
    "SourceOwnedLiveErrorCategory",
    "SourceOwnedLiveProviderDispatchResult",
    "SourceOwnedLiveProviderTransport",
    "dispatch_source_owned_live_provider_request",
]
