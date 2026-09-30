"""Offline fake-transport tests for the source-owned Step 2A boundary."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from examcard.knowledge.contracts import (
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2ARequest,
    Step2AResultPayload,
    Step2AResponseHandlingMode,
    Step2Stage,
    SourceOwnedCanonicalProposal,
    SourceOwnedModelProposal,
    canonicalize_source_owned_model_proposals,
    make_step2_request_identity,
    step2a_response_handling_mode,
    step2a_result_payload_model,
)
from examcard.knowledge.contracts.versions import (
    _MULTIPAGE_STEP2A_REQUEST_VERSIONS,
    _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS,
)
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from examcard.knowledge.llm_transport import CallableStep2Transport, TransportCompletion


_SPAN_ID = "span:" + "a" * 24


def _minimal_source_owned_payload() -> dict[str, object]:
    return {
        "proposals": [
            {
                "fact_type": "definition",
                "label": "Source-owned fact",
                "assertion": "The selected source defines the fact.",
                "primary_anchor_span_ids": [_SPAN_ID],
            }
        ]
    }


def _identity(request_version: str):
    return make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=request_version,
        model="offline-contract-test-model",
        payload_sha256="b" * 64,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
    )


def test_fake_transport_flows_through_response_model_to_canonical_boundary() -> None:
    identity = _identity(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION)
    request = SimpleNamespace(identity=identity)
    seen: dict[str, object] = {}

    def fake_provider(**kwargs: object) -> TransportCompletion:
        seen["request_version"] = request.identity.request_version
        seen["response_schema"] = kwargs["response_schema"]
        return TransportCompletion(
            content=json.dumps(_minimal_source_owned_payload()),
            http_status=200,
            finish_reason="stop",
        )

    transport = CallableStep2Transport(fake_provider)
    completion = transport.complete(
        request=cast(Step2ARequest, request),
        response_schema=Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_json_schema(),
        prompt="offline fake prompt",
        use_idempotency_key=True,
    )
    model_payload = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(
        json.loads(completion.content)
    )
    canonical = Step2OpenAIAdapter.decode_source_owned_completion(
        request_version=identity.request_version,
        completion=completion,
    )

    assert completion.http_status == 200
    assert seen["request_version"] == PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
    assert seen["response_schema"] == Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_json_schema()
    assert isinstance(model_payload.proposals[0], SourceOwnedModelProposal)
    assert canonical == canonicalize_source_owned_model_proposals(model_payload.proposals)
    assert isinstance(canonical[0], SourceOwnedCanonicalProposal)
    assert set(canonical[0].model_dump(mode="json")) == {
        "proposal_key",
        "fact_type",
        "label",
        "assertion",
        "primary_anchor_span_ids",
    }


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "numeric_claims",
        "table_occurrences",
        "structural_indexes",
        "evidence_ids",
        "provenance",
        "unknown_field",
    ),
)
def test_forbidden_model_authority_is_rejected_at_response_schema_layer(forbidden_field: str) -> None:
    payload = _minimal_source_owned_payload()
    payload["proposals"][0][forbidden_field] = []

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(payload)


def test_source_owned_dispatch_does_not_enter_legacy_v10_parser_path() -> None:
    completion = TransportCompletion(content=json.dumps(_minimal_source_owned_payload()), http_status=200)

    assert (
        step2a_response_handling_mode(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION)
        == Step2AResponseHandlingMode.PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF
    )
    assert step2a_response_handling_mode(STEP2A_REQUEST_VERSION) == Step2AResponseHandlingMode.EXISTING_VERSIONED
    with pytest.raises(ValueError, match="source-owned"):
        Step2OpenAIAdapter.decode_source_owned_completion(
            request_version=STEP2A_REQUEST_VERSION,
            completion=completion,
        )


def test_source_owned_contract_does_not_require_continuation_or_review_sidecar() -> None:
    assert PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION not in _MULTIPAGE_STEP2A_REQUEST_VERSIONS
    assert PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION not in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS
    assert PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION != DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION
    assert PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION != SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION


def test_v10_result_shape_remains_the_existing_model_shape() -> None:
    v10_payload = {
        "proposals": [
            {
                "proposal_key": "proposal:legacy-001",
                "source_section_ids": ["section:" + "b" * 24],
                "fact_type": "definition",
                "label": "Legacy fact",
                "assertion": "The legacy contract remains available.",
                "primary_anchor_span_ids": [_SPAN_ID],
                "numeric_claims": [],
            }
        ]
    }

    parsed = Step2AResultPayload.model_validate(v10_payload)

    assert step2a_result_payload_model(STEP2A_REQUEST_VERSION) is Step2AResultPayload
    assert parsed.proposals[0].proposal_key == "proposal:legacy-001"
    assert "numeric_claims" in next(
        value
        for value in Step2AResultPayload.model_json_schema()["$defs"].values()
        if value.get("properties", {}).get("proposal_key") is not None
    )["properties"]
