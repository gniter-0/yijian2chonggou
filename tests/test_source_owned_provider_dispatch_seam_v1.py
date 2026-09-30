"""Focused offline tests for the independent source-owned provider seam."""
from __future__ import annotations

import json
from typing import cast

import pytest
from pydantic import ValidationError

import examcard.knowledge.extraction_v1 as legacy_extraction
from examcard.knowledge.contracts import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_MODEL_INPUT_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_PROMPT_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    SourceOwnedProviderCompletion,
    SourceOwnedProviderRequest,
    SourceOwnedProviderRequestIdentity,
    SOURCE_OWNED_PROVIDER_MAX_RETRIES,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    build_source_owned_provider_request,
    decode_source_owned_provider_completion,
    dispatch_source_owned_provider_request,
)
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from examcard.knowledge.extraction_v1 import source_owned_extract_document_proposals


def _payload(*, anchor_id: str = "span:" + "a" * 24) -> dict[str, object]:
    return {
        "proposals": [
            {
                "fact_type": "definition",
                "label": "Source-owned provider fact",
                "assertion": "The selected source defines this fact.",
                "primary_anchor_span_ids": [anchor_id],
            }
        ]
    }


def _request() -> SourceOwnedProviderRequest:
    return build_source_owned_provider_request(
        model="offline-source-owned-model",
        model_input={
            "anchor_span_ids": ["span:" + "a" * 24],
            "semantic_projection": "typed-local-input",
        },
    )


class _FakeSourceOwnedTransport:
    max_retries = SOURCE_OWNED_PROVIDER_MAX_RETRIES

    def __init__(self, content: str, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.calls = 0
        self.observed_prompt = ""

    def complete_source_owned(
        self,
        *,
        request: SourceOwnedProviderRequest,
        response_schema: dict[str, object],
        prompt: str,
    ) -> SourceOwnedProviderCompletion:
        self.calls += 1
        self.observed_prompt = prompt
        assert response_schema == request.response_schema
        if self.fail:
            raise RuntimeError("fake upstream failure")
        return SourceOwnedProviderCompletion(content=self.content, finish_reason="stop")


def test_source_owned_request_has_independent_version_and_identity_namespaces() -> None:
    request = _request()

    assert request.identity.request_version == PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
    assert request.identity.model_input_version == PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_MODEL_INPUT_VERSION
    assert request.identity.prompt_version == PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_PROMPT_VERSION
    assert request.identity.request_version not in {
        STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    }
    assert request.identity.request_id.startswith("source-owned-step2a-request:")
    assert request.identity.cache_key.startswith("source-owned-step2-cache:")
    assert request.identity.idempotency_key.startswith("source-owned-step2-idempotency:")
    assert request.identity.prompt_sha256
    assert request.identity.response_schema_sha256


def test_same_model_input_is_stable_and_prompt_or_schema_identity_changes() -> None:
    first = _request()
    second = _request()
    assert first.identity == second.identity

    changed_prompt = build_source_owned_provider_request(
        model="offline-source-owned-model",
        model_input={
            "anchor_span_ids": ["span:" + "a" * 24],
            "semantic_projection": "typed-local-input",
        },
        prompt_template="source-owned prompt variant",
    )
    assert changed_prompt.identity.prompt_sha256 != first.identity.prompt_sha256
    assert changed_prompt.identity.request_id != first.identity.request_id
    assert changed_prompt.identity.cache_key != first.identity.cache_key
    assert changed_prompt.identity.response_schema_sha256 == first.identity.response_schema_sha256


def test_fake_transport_dispatch_decodes_then_existing_source_owned_extraction_builds_proposal() -> None:
    request = _request()
    transport = _FakeSourceOwnedTransport(json.dumps(_payload()))

    dispatched = dispatch_source_owned_provider_request(request=request, transport=transport)

    assert dispatched.status == "succeeded"
    assert dispatched.metadata.status == "succeeded"
    assert dispatched.metadata.provider_call_count == 1
    assert transport.calls == 1
    assert dispatched.response is not None
    proposals = source_owned_extract_document_proposals(response=dispatched.response)
    assert len(proposals) == 1
    assert proposals[0].fact_type.value == "definition"
    assert proposals[0].primary_anchor_span_ids == ("span:" + "a" * 24,)


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "numeric_claims",
        "table_occurrences",
        "structural_indexes",
        "evidence_ids",
        "provenance",
        "grounding",
        "unknown_field",
    ),
)
def test_invalid_source_owned_fields_are_rejected_by_strict_decoder(
    forbidden_field: str,
) -> None:
    payload = _payload()
    proposal = payload["proposals"][0]
    assert isinstance(proposal, dict)
    proposal[forbidden_field] = []
    request = _request()
    completion = SourceOwnedProviderCompletion(
        content=json.dumps(payload),
        finish_reason="stop",
    )

    with pytest.raises(ValidationError):
        decode_source_owned_provider_completion(request=request, completion=completion)

    transport = _FakeSourceOwnedTransport(json.dumps(payload))
    result = dispatch_source_owned_provider_request(request=request, transport=transport)
    assert result.status == "failed_closed"
    assert result.response is None
    assert result.metadata.provider_call_count == 1
    assert transport.calls == 1


def test_legacy_payload_cannot_cross_the_source_owned_decoder() -> None:
    legacy_payload = {
        "proposals": [
            {
                "proposal_key": "proposal:legacy-001",
                "source_section_ids": ["section:" + "b" * 24],
                "fact_type": "definition",
                "label": "Legacy fact",
                "assertion": "The legacy contract remains available.",
                "primary_anchor_span_ids": ["span:" + "a" * 24],
                "numeric_claims": [],
            }
        ]
    }

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(legacy_payload)


def test_provider_failure_is_one_shot_without_retry_or_fallback() -> None:
    transport = _FakeSourceOwnedTransport("unused", fail=True)

    result = dispatch_source_owned_provider_request(request=_request(), transport=transport)

    assert result.status == "failed_closed"
    assert result.metadata.provider_call_count == 1
    assert transport.calls == 1
    assert not hasattr(result, "retry")
    assert not hasattr(result, "fallback")


def test_nonzero_transport_retry_policy_is_rejected_before_call() -> None:
    transport = _FakeSourceOwnedTransport(json.dumps(_payload()))
    transport.max_retries = 1

    result = dispatch_source_owned_provider_request(request=_request(), transport=transport)

    assert result.status == "failed_closed"
    assert result.metadata.provider_call_count == 0
    assert transport.calls == 0


def test_missing_provider_config_fails_closed_without_a_call() -> None:
    result = dispatch_source_owned_provider_request(request=_request(), transport=None)

    assert result.status == "failed_closed"
    assert result.metadata.provider_call_count == 0
    assert result.response is None


def test_non_offline_environment_is_blocked_before_transport_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    transport = _FakeSourceOwnedTransport(json.dumps(_payload()))

    result = dispatch_source_owned_provider_request(request=_request(), transport=transport)

    assert result.status == "failed_closed"
    assert result.metadata.provider_call_count == 0
    assert transport.calls == 0


def test_source_owned_dispatch_does_not_enter_legacy_adapter_or_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_legacy(*_: object, **__: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    def forbidden_adapter(*_: object, **__: object) -> None:
        raise AssertionError("legacy Step2 adapter path was entered")

    monkeypatch.setattr(legacy_extraction, "extract_document_proposals", forbidden_legacy)
    monkeypatch.setattr(Step2OpenAIAdapter, "complete_step2a", forbidden_adapter)
    transport = _FakeSourceOwnedTransport(json.dumps(_payload()))

    result = dispatch_source_owned_provider_request(request=_request(), transport=transport)

    assert result.status == "succeeded"
    assert transport.calls == 1


def test_non_source_owned_request_cannot_enter_dispatch() -> None:
    transport = _FakeSourceOwnedTransport(json.dumps(_payload()))
    result = dispatch_source_owned_provider_request(
        request=cast(SourceOwnedProviderRequest, object()),
        transport=transport,
    )

    assert result.status == "failed_closed"
    assert result.metadata.provider_call_count == 0
    assert transport.calls == 0


def test_request_and_dispatch_serialization_exclude_prompt_payload_and_raw_response() -> None:
    request = _request()
    transport = _FakeSourceOwnedTransport(json.dumps(_payload()))
    result = dispatch_source_owned_provider_request(request=request, transport=transport)

    request_dump = request.model_dump(mode="json")
    result_dump = result.model_dump(mode="json")
    serialized = json.dumps((request_dump, result_dump), sort_keys=True).lower()

    assert "typed-local-input" not in serialized
    assert "source-owned provider fact" not in serialized
    assert "prompt_template" not in request_dump
    assert "model_input" not in request_dump
    assert "response_schema" not in request_dump
    assert "response" not in result_dump
    assert "raw" not in serialized
    assert "secret" not in serialized
    assert "credential" not in serialized


def test_request_rejects_tampered_identity() -> None:
    request = _request()
    identity = request.identity.model_dump(mode="json")
    identity["request_id"] = "source-owned-step2a-request:" + "0" * 64
    with pytest.raises(ValidationError):
        SourceOwnedProviderRequestIdentity.model_validate(identity)
