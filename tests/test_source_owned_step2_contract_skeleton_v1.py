"""Offline boundary tests for the arbitrary-PDF source-owned Step 2A skeleton."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from examcard.knowledge.contracts import (
    ContractValidationReport,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    Step2AContractProfile,
    Step2AResponseHandlingMode,
    Step2AResultPayload,
    Step2AProductionSourceOwnedArbitraryPDFModelFactProposal,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2Stage,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    Step2AV21ResultPayload,
    Step2AV23ResultPayload,
    default_step2_prompt_template,
    make_step2_request_identity,
    step2a_request_version_for_profile,
    step2a_response_handling_mode,
    step2a_result_payload_model,
)
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter


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
        model="offline-contract-test-model",
        payload_sha256="b" * 64,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
    )


def test_new_contract_has_an_explicit_profile_and_response_dispatch_mode() -> None:
    assert (
        step2a_request_version_for_profile(Step2AContractProfile.PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF)
        == PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
    )
    assert (
        step2a_response_handling_mode(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION)
        == Step2AResponseHandlingMode.PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF
    )
    assert step2a_response_handling_mode(STEP2A_REQUEST_VERSION) == Step2AResponseHandlingMode.EXISTING_VERSIONED
    assert Step2OpenAIAdapter.response_handling_mode(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION) == Step2AResponseHandlingMode.PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF
    assert Step2OpenAIAdapter.response_handling_mode(STEP2A_REQUEST_VERSION) == Step2AResponseHandlingMode.EXISTING_VERSIONED


def test_new_identity_namespace_differs_from_v10() -> None:
    v10 = _identity(STEP2A_REQUEST_VERSION)
    production = _identity(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION)

    assert production.request_version != v10.request_version
    assert production.request_id != v10.request_id
    assert production.cache_key != v10.cache_key
    assert production.prompt_sha256 != v10.prompt_sha256
    assert production.response_schema_sha256 != v10.response_schema_sha256


def test_minimal_source_owned_response_accepts_only_semantic_fields() -> None:
    payload = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(_minimal_payload())
    proposal = payload.proposals[0]

    assert set(proposal.model_dump(mode="json")) == {
        "fact_type",
        "label",
        "assertion",
        "primary_anchor_span_ids",
    }
    assert proposal.derived_proposal_key.startswith("proposal:")


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "numeric_claims",
        "table_occurrences",
        "structural_indexes",
        "proposal_key",
        "source_section_ids",
        "primary_evidence_ids",
        "coverage_metadata",
    ),
)
def test_source_owned_model_response_rejects_derived_fields(forbidden_field: str) -> None:
    payload = _minimal_payload()
    payload["proposals"][0][forbidden_field] = "forbidden"

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(payload)


def test_unknown_model_response_fields_fail_closed() -> None:
    payload = _minimal_payload()
    payload["proposals"][0]["unknown_response_field"] = "not admitted"

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(payload)


def test_new_response_schema_is_minimal_and_old_version_shapes_remain_distinct() -> None:
    production_schema = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_json_schema()
    production_proposal_schema = next(
        value
        for value in production_schema["$defs"].values()
        if value.get("properties", {}).get("primary_anchor_span_ids") is not None
    )

    assert set(production_schema["properties"]) == {"proposals"}
    assert set(production_proposal_schema["properties"]) == {
        "fact_type",
        "label",
        "assertion",
        "primary_anchor_span_ids",
    }
    assert "numeric_claims" not in production_proposal_schema["properties"]
    assert "table_occurrences" not in production_proposal_schema["properties"]
    assert "structural_indexes" not in production_proposal_schema["properties"]
    assert step2a_result_payload_model(STEP2A_REQUEST_VERSION) is Step2AResultPayload
    assert step2a_result_payload_model(SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION) is Step2AV23ResultPayload
    assert step2a_result_payload_model(PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION) is Step2AProductionSourceOwnedArbitraryPDFResultPayload
    assert Step2AV21ResultPayload.model_json_schema()["properties"] == step2a_result_payload_model(
        "step2a_contract_v21_composition_numeric_occurrence"
    ).model_json_schema()["properties"]


def test_published_validation_schema_excludes_internal_diagnostics() -> None:
    schema_text = json.dumps(ContractValidationReport.model_json_schema(), sort_keys=True)
    assert "numeric_claim_failure_diagnostics" not in ContractValidationReport.model_json_schema().get("properties", {})
    assert "numeric_claim_failure_diagnostics" not in schema_text


def test_new_prompt_is_fingerprinted_separately_without_changing_v10_prompt() -> None:
    v10_prompt = default_step2_prompt_template(Step2Stage.FACT_PLANNING, request_version=STEP2A_REQUEST_VERSION)
    production_prompt = default_step2_prompt_template(
        Step2Stage.FACT_PLANNING,
        request_version=PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    )

    assert "V10 Fact planner" in v10_prompt
    assert "production Step 2A semantic Fact planner" in production_prompt
    assert v10_prompt != production_prompt


def test_source_owned_canonical_boundary_does_not_add_implementation_or_fields() -> None:
    proposal = Step2AProductionSourceOwnedArbitraryPDFModelFactProposal.model_validate(_minimal_payload()["proposals"][0])

    assert not hasattr(proposal, "numeric_claims")
    assert not hasattr(proposal, "table_occurrences")
    assert not hasattr(proposal, "structural_indexes")
