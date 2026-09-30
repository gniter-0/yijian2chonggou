"""Offline tests for request-identity extraction dispatch."""
from __future__ import annotations

import json
from typing import cast

import pytest
from pydantic import ValidationError

from examcard.knowledge.contracts import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    Step2AExtractionPath,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2AResponseHandlingMode,
    Step2AResultPayload,
    Step2Stage,
    make_step2_request_identity,
    step2a_response_handling_mode,
    step2a_result_payload_model,
)
from examcard.knowledge.extraction_v1 import (
    select_document_extraction_path,
    source_owned_extract_document_proposals,
)
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from examcard.knowledge.llm_transport import TransportCompletion


_SPAN_ID = "span:" + "a" * 24


def _identity(request_version: str):
    return make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=request_version,
        model="offline-selector-model",
        payload_sha256="b" * 64,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
    )


def _source_owned_payload() -> dict[str, object]:
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


def test_selector_routes_only_by_request_identity() -> None:
    existing_versions = (
        STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    )

    for request_version in existing_versions:
        identity = _identity(request_version)
        assert select_document_extraction_path(identity) == Step2AExtractionPath.EXISTING_VERSIONED
        assert step2a_response_handling_mode(request_version) == Step2AResponseHandlingMode.EXISTING_VERSIONED
        assert Step2OpenAIAdapter.response_handling_mode(request_version) == Step2AResponseHandlingMode.EXISTING_VERSIONED

    production_identity = _identity(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION)
    assert select_document_extraction_path(production_identity) == Step2AExtractionPath.SOURCE_OWNED_ARBITRARY_PDF
    assert (
        step2a_response_handling_mode(production_identity.request_version)
        == Step2AResponseHandlingMode.PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF
    )


def test_source_owned_extraction_seam_accepts_validated_payload_and_returns_canonical_result() -> None:
    response = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(_source_owned_payload())

    canonical = source_owned_extract_document_proposals(response=response)

    assert len(canonical) == 1
    assert set(canonical[0].model_dump(mode="json")) == {
        "proposal_key",
        "fact_type",
        "label",
        "assertion",
        "primary_anchor_span_ids",
    }


def test_legacy_payload_cannot_enter_source_owned_extraction_seam() -> None:
    legacy = Step2AResultPayload.model_validate(
        {
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
    )

    with pytest.raises(ValueError, match="source-owned"):
        source_owned_extract_document_proposals(
            response=cast(Step2AProductionSourceOwnedArbitraryPDFResultPayload, legacy)
        )


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "numeric_claims",
        "table_occurrences",
        "evidence_ids",
        "provenance",
    ),
)
def test_forbidden_authority_fails_before_source_owned_extraction(forbidden_field: str) -> None:
    payload = _source_owned_payload()
    payload["proposals"][0][forbidden_field] = []
    completion = TransportCompletion(content=json.dumps(payload), http_status=200)

    with pytest.raises(ValidationError):
        Step2OpenAIAdapter.decode_source_owned_completion(
            request_version=PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
            completion=completion,
        )


def test_identity_and_response_schema_namespaces_remain_separate() -> None:
    v10 = _identity(STEP2A_REQUEST_VERSION)
    production = _identity(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION)

    assert production.request_version != v10.request_version
    assert production.request_id != v10.request_id
    assert production.cache_key != v10.cache_key
    assert production.prompt_sha256 != v10.prompt_sha256
    assert production.response_schema_sha256 != v10.response_schema_sha256
    assert step2a_result_payload_model(STEP2A_REQUEST_VERSION) is Step2AResultPayload
    assert step2a_result_payload_model(
        PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
    ) is Step2AProductionSourceOwnedArbitraryPDFResultPayload
