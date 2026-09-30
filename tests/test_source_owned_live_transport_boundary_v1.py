"""Focused tests for the isolated source-owned live transport boundary."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError
from pydantic import ValidationError

import examcard.knowledge.extraction_v1 as legacy_extraction
import examcard.knowledge.source_owned_live_transport as live_transport
from examcard.knowledge.contracts import (
    SOURCE_OWNED_PROVIDER_MAX_RETRIES,
    SourceOwnedProviderCompletion,
    SourceOwnedProviderRequest,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    build_source_owned_provider_request,
)
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter


def _anchor_id() -> str:
    return "span:" + "a" * 24


def _payload(*, extra: dict[str, object] | None = None) -> dict[str, object]:
    proposal: dict[str, object] = {
        "fact_type": "definition",
        "label": "Live source-owned fact",
        "assertion": "The selected source defines this fact.",
        "primary_anchor_span_ids": [_anchor_id()],
    }
    if extra:
        proposal.update(extra)
    return {"proposals": [proposal]}


def _request() -> SourceOwnedProviderRequest:
    return build_source_owned_provider_request(
        model="source-owned-live-test-model",
        model_input={
            "anchor_span_ids": [_anchor_id()],
            "semantic_projection": "typed-source-owned-input",
        },
    )


class _FakeLiveTransport:
    max_retries = SOURCE_OWNED_PROVIDER_MAX_RETRIES

    def __init__(
        self,
        content: str,
        *,
        fail: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self.content = content
        self.fail = fail
        self.error = error
        self.calls = 0

    def complete_source_owned(
        self,
        *,
        request: SourceOwnedProviderRequest,
        response_schema: dict[str, object],
        prompt: str,
    ) -> SourceOwnedProviderCompletion:
        self.calls += 1
        assert response_schema == request.response_schema
        assert prompt == request.prompt_template
        if self.error is not None:
            raise self.error
        if self.fail:
            raise RuntimeError("provider body must never escape")
        return SourceOwnedProviderCompletion(content=self.content, finish_reason="stop")


def test_live_boundary_calls_fake_transport_once_and_decodes_strictly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    transport = _FakeLiveTransport(json.dumps(_payload()))

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=transport,
    )

    assert result.status == "succeeded"
    assert result.stage == "completed"
    assert result.provider_call_count == 1
    assert transport.calls == 1
    assert result.response is not None
    assert isinstance(result.response, Step2AProductionSourceOwnedArbitraryPDFResultPayload)
    assert result.response_sha256 is not None
    assert result.response_schema_sha256 == _request().identity.response_schema_sha256


def test_network_disabled_fails_before_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "0")
    transport = _FakeLiveTransport(json.dumps(_payload()))

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=transport,
    )

    assert result.status == "failed_closed"
    assert result.stage == "gate"
    assert result.safe_error_category == "network_disabled"
    assert result.provider_call_count == 0
    assert transport.calls == 0


def test_nonzero_retry_policy_is_rejected_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    transport = _FakeLiveTransport(json.dumps(_payload()))
    transport.max_retries = 1

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=transport,
    )

    assert result.status == "failed_closed"
    assert result.safe_error_category == "retry_policy_violation"
    assert result.provider_call_count == 0
    assert transport.calls == 0
    assert not hasattr(result, "retry")
    assert not hasattr(result, "fallback")


def test_transport_failure_is_one_shot_and_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    transport = _FakeLiveTransport("unused", fail=True)

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=transport,
    )

    assert result.status == "failed_closed"
    assert result.stage == "transport"
    assert result.safe_error_category == "transport_failure"
    assert result.provider_call_count == 1
    assert transport.calls == 1
    assert not hasattr(result, "retry")
    assert not hasattr(result, "fallback")


def _request_for_provider_error() -> httpx.Request:
    return httpx.Request("POST", "https://provider.example/v1/chat/completions")


@pytest.mark.parametrize(
    ("error_factory", "expected_category", "expected_status"),
    [
        (
            lambda: APITimeoutError(request=_request_for_provider_error()),
            "timeout",
            None,
        ),
        (
            lambda: APIConnectionError(
                message="secret connection detail",
                request=_request_for_provider_error(),
            ),
            "connection_failure",
            None,
        ),
        (
            lambda: APIStatusError(
                "secret 429 body",
                response=httpx.Response(429, request=_request_for_provider_error()),
                body={"secret": "provider-body"},
            ),
            "rate_limited",
            429,
        ),
        (
            lambda: APIStatusError(
                "secret 403 body",
                response=httpx.Response(403, request=_request_for_provider_error()),
                body={"secret": "provider-body"},
            ),
            "provider_request_rejected",
            403,
        ),
        (
            lambda: APIStatusError(
                "secret 503 body",
                response=httpx.Response(503, request=_request_for_provider_error()),
                body={"secret": "provider-body"},
            ),
            "upstream_unavailable",
            503,
        ),
        (
            lambda: APIError(
                "secret provider API detail",
                request=_request_for_provider_error(),
                body={"secret": "provider-body"},
            ),
            "provider_api_error",
            None,
        ),
        (
            lambda: RuntimeError("secret unexpected transport detail"),
            "unexpected_transport_failure",
            None,
        ),
    ],
)
def test_transport_failures_are_typed_without_exception_content(
    monkeypatch: pytest.MonkeyPatch,
    error_factory: Callable[[], BaseException],
    expected_category: str,
    expected_status: int | None,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    error = error_factory()
    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=_FakeLiveTransport("unused", error=error),
    )

    assert result.status == "failed_closed"
    assert result.stage == "transport"
    assert result.safe_error_category == "transport_failure"
    assert result.provider_call_count == 1
    assert result.transport_diagnostic is not None
    assert result.transport_diagnostic.category == expected_category
    assert result.transport_diagnostic.http_status_code == expected_status
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert "secret" not in serialized.lower()
    assert "provider-body" not in serialized
    assert result.decoder_diagnostic is None


def test_invalid_response_schema_is_rejected_at_live_decoder_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    content = json.dumps(_payload(extra={"numeric_claims": []}))
    transport = _FakeLiveTransport(content)

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=transport,
    )

    assert result.status == "failed_closed"
    assert result.stage == "decoder"
    assert result.safe_error_category == "response_schema_rejected"
    assert result.response is None
    assert result.provider_call_count == 1
    assert result.response_sha256 is not None
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True).lower()
    assert "numeric_claims" not in serialized
    assert "live source-owned fact" not in serialized
    assert "provider body" not in serialized


@pytest.mark.parametrize(
    ("content", "expected_category"),
    [
        ("{malformed", "json_decode_failure"),
        ("[\"top-level-array\"]", "top_level_not_object"),
    ],
)
def test_decoder_diagnostics_distinguish_json_shape_failures(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    expected_category: str,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=_FakeLiveTransport(content),
    )

    assert result.status == "failed_closed"
    assert result.safe_error_category == "response_schema_rejected"
    assert result.decoder_diagnostic is not None
    assert result.decoder_diagnostic.category == expected_category
    assert result.decoder_diagnostic.validation_error_count == 0
    assert result.provider_call_count == 1


def test_schema_decoder_diagnostics_are_structural_and_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    secret_key = "x-secret-provider-field"
    secret_value = "provider-secret-value"
    invalid = _payload(extra={secret_key: secret_value})

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=_FakeLiveTransport(json.dumps(invalid)),
    )

    assert result.status == "failed_closed"
    assert result.decoder_diagnostic is not None
    assert result.decoder_diagnostic.category == "schema_validation_failure"
    assert result.decoder_diagnostic.validation_error_count == 1
    assert result.decoder_diagnostic.distinct_error_types == ("extra_forbidden",)
    assert result.decoder_diagnostic.extra_forbidden_field_count == 1
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert secret_key not in serialized
    assert secret_value not in serialized
    assert "provider body" not in serialized


def test_missing_fields_have_deterministic_schema_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=_FakeLiveTransport(json.dumps({"proposals": [{}]})),
    )

    assert result.status == "failed_closed"
    assert result.decoder_diagnostic is not None
    assert result.decoder_diagnostic.category == "schema_validation_failure"
    assert result.decoder_diagnostic.missing_field_count == 4
    assert result.decoder_diagnostic.distinct_error_types == ("missing",)


def test_unexpected_decoder_failure_uses_generic_safe_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")

    def unexpected_failure(*_: object, **__: object) -> object:
        raise RuntimeError("secret decoder exception must not escape")

    monkeypatch.setattr(
        live_transport,
        "decode_source_owned_provider_completion",
        unexpected_failure,
    )
    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=_FakeLiveTransport(json.dumps(_payload())),
    )

    assert result.status == "failed_closed"
    assert result.decoder_diagnostic is not None
    assert result.decoder_diagnostic.category == "unexpected_decoder_failure"
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert "secret decoder exception" not in serialized


def test_missing_transport_fails_closed_without_a_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=None,
    )

    assert result.status == "failed_closed"
    assert result.safe_error_category == "missing_transport"
    assert result.provider_call_count == 0


def test_non_source_owned_request_cannot_enter_live_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    transport = _FakeLiveTransport(json.dumps(_payload()))

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=cast(SourceOwnedProviderRequest, object()),
        transport=transport,
    )

    assert result.status == "failed_closed"
    assert result.safe_error_category == "invalid_request"
    assert result.provider_call_count == 0
    assert transport.calls == 0


def test_live_dispatch_does_not_enter_legacy_adapter_or_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")

    def forbidden_legacy(*_: object, **__: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    def forbidden_adapter(*_: object, **__: object) -> None:
        raise AssertionError("legacy Step2 adapter path was entered")

    monkeypatch.setattr(legacy_extraction, "extract_document_proposals", forbidden_legacy)
    monkeypatch.setattr(Step2OpenAIAdapter, "complete_step2a", forbidden_adapter)
    transport = _FakeLiveTransport(json.dumps(_payload()))

    result = live_transport.dispatch_source_owned_live_provider_request(
        request=_request(),
        transport=transport,
    )

    assert result.status == "succeeded"
    assert transport.calls == 1


def test_result_serializes_only_safe_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    content = json.dumps(_payload())
    request = _request()
    result = live_transport.dispatch_source_owned_live_provider_request(
        request=request,
        transport=_FakeLiveTransport(content),
    )

    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True).lower()
    assert result.request_id == request.identity.request_id
    assert result.response_schema_sha256 == request.identity.response_schema_sha256
    assert "typed-source-owned-input" not in serialized
    assert "live source-owned fact" not in serialized
    assert "prompt_template" not in serialized
    assert "response" not in result.model_dump(mode="json")
    assert "secret" not in serialized
    assert "credential" not in serialized


def test_openai_source_owned_transport_constructs_sdk_with_zero_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            observed.update(kwargs)

    monkeypatch.setattr(live_transport, "OpenAI", _FakeOpenAI)
    transport = live_transport.OpenAICompatibleSourceOwnedLiveTransport(
        base_url="https://provider.invalid/v1",
        api_key="test-only-key",
        timeout=5.0,
    )

    assert transport.max_retries == 0
    assert observed["max_retries"] == 0
    assert observed["base_url"] == "https://provider.invalid/v1"


def test_result_model_rejects_success_without_response() -> None:
    with pytest.raises(ValidationError):
        live_transport.SourceOwnedLiveProviderDispatchResult(
            status="succeeded",
            request_id="request-id",
            response_schema_sha256="0" * 64,
            response_sha256="0" * 64,
            provider_call_count=1,
            stage="completed",
        )
