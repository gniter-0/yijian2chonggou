"""Offline tests for the source-owned canonical-to-local derivation slice."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from examcard.knowledge.contracts import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    SourceOwnedLocalDerivationResult,
    Step2AExtractionPath,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2AResponseHandlingMode,
    Step2AResultPayload,
    Step2Stage,
    make_step2_request_identity,
    source_owned_local_derivation,
    step2a_response_handling_mode,
    step2a_result_payload_model,
)
from examcard.knowledge.extraction_v1 import (
    select_document_extraction_path,
    source_owned_extract_document_proposals,
)


_SPAN_ID = "span:" + "a" * 24


def _minimal_payload() -> dict[str, object]:
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
        model="offline-local-derivation-model",
        payload_sha256="b" * 64,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
    )


def test_validated_model_response_flows_to_local_derivation_result() -> None:
    response = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(_minimal_payload())
    canonical = source_owned_extract_document_proposals(response=response)

    result = source_owned_local_derivation(proposal=canonical[0])

    assert isinstance(result, SourceOwnedLocalDerivationResult)
    assert result.proposal_key.startswith("proposal:")
    assert result.fact_type.value == "definition"
    assert result.primary_anchor_span_ids == (_SPAN_ID,)
    assert result.derived_facts.anchor_count == 1
    assert result.derived_facts.derived_from_local_anchor is True


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "numeric_claims",
        "table_occurrences",
        "evidence_ids",
        "provenance",
        "unknown_field",
    ),
)
def test_model_authority_fields_fail_at_source_owned_schema_boundary(forbidden_field: str) -> None:
    payload = _minimal_payload()
    payload["proposals"][0][forbidden_field] = []

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(payload)


def test_local_result_contains_only_semantic_fields_and_local_anchor_facts() -> None:
    response = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(_minimal_payload())
    canonical = source_owned_extract_document_proposals(response=response)
    result = source_owned_local_derivation(proposal=canonical[0])
    dumped = result.model_dump(mode="json")
    forbidden_names = {
        "prompt",
        "response",
        "credential",
        "provider_metadata",
        "numeric_claims",
        "table_occurrences",
        "provenance",
        "evidence_ids",
    }

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for child in value.values() for key in keys(child)}
        if isinstance(value, list):
            return {key for child in value for key in keys(child)}
        return set()

    assert set(dumped) == {
        "proposal_key",
        "fact_type",
        "label",
        "assertion",
        "primary_anchor_span_ids",
        "derived_facts",
        "grounded_numeric_claims",
        "numeric_derivation_status",
    }
    assert keys(dumped).isdisjoint(forbidden_names)
    assert "prompt" not in json.dumps(dumped, sort_keys=True)


@pytest.mark.parametrize(
    "request_version",
    (
        STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    ),
)
def test_legacy_v10_v21_v23_stay_on_existing_extraction_path(request_version: str) -> None:
    identity = _identity(request_version)

    assert select_document_extraction_path(identity) == Step2AExtractionPath.EXISTING_VERSIONED
    assert step2a_response_handling_mode(request_version) == Step2AResponseHandlingMode.EXISTING_VERSIONED
    assert request_version != PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION


def test_source_owned_identity_and_published_schema_remain_separate() -> None:
    legacy_identity = _identity(STEP2A_REQUEST_VERSION)
    source_owned_identity = _identity(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION)

    assert legacy_identity.request_id != source_owned_identity.request_id
    assert legacy_identity.cache_key != source_owned_identity.cache_key
    assert legacy_identity.response_schema_sha256 != source_owned_identity.response_schema_sha256
    assert step2a_result_payload_model(STEP2A_REQUEST_VERSION) is Step2AResultPayload
    assert "numeric_claim_failure_diagnostics" not in Step2AResultPayload.model_json_schema()
    assert "numeric_claim_failure_diagnostics" not in Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_json_schema()
