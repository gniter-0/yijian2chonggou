"""Focused offline tests for the source-owned response pipeline boundary."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from examcard.knowledge.contracts import (
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    source_owned_derive_atomic_numeric_claims,
    source_owned_extract_document_proposals_from_evidence,
)
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from migration.run_source_owned_response_pipeline_v1 import (
    SourceOwnedResponsePipelineSummary,
    default_source_owned_evidence,
    run_source_owned_response_pipeline,
    validate_source_owned_response_payload,
)


def _valid_payload() -> dict[str, object]:
    anchor_id = default_source_owned_evidence().anchor_spans[0].anchor_span_id
    return {
        "proposals": [
            {
                "fact_type": "definition",
                "label": "Source-owned response fact",
                "assertion": "The asserted value is 65MPa.",
                "primary_anchor_span_ids": [anchor_id],
            }
        ]
    }


def test_valid_typed_response_reaches_canonical_proposal_and_derivation() -> None:
    summary = run_source_owned_response_pipeline()

    assert isinstance(summary, SourceOwnedResponsePipelineSummary)
    assert summary.response_validation_status == "validated"
    assert summary.extraction_status == "extracted"
    assert summary.anchor_selection_status == "selected"
    assert summary.derivation_status == "derived"
    assert summary.proposal_count == 1
    assert summary.grounded_numeric_claim_count == 1

    response = validate_source_owned_response_payload(_valid_payload())
    proposals = source_owned_extract_document_proposals_from_evidence(
        evidence=default_source_owned_evidence(),
        response=response,
    )
    assert len(proposals) == 1
    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposals[0],
        evidence_spans={
            anchor.anchor_span_id: anchor
            for anchor in default_source_owned_evidence().anchor_spans
        },
    )
    assert result.numeric_derivation_status == "derived"
    assert len(result.grounded_numeric_claims) == 1


def test_missing_anchor_fails_closed_before_derivation() -> None:
    summary = run_source_owned_response_pipeline(scenario="missing_anchor")

    assert summary.response_validation_status == "validated"
    assert summary.extraction_status == "failed_closed"
    assert summary.anchor_selection_status == "failed_closed"
    assert summary.derivation_status == "failed_closed"
    assert summary.proposal_count == 0
    assert summary.grounded_numeric_claim_count == 0


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "numeric_claims",
        "table_occurrences",
        "evidence_ids",
        "provenance",
        "grounding",
        "structural_indexes",
        "unknown_field",
    ),
)
def test_forbidden_or_unknown_authority_field_is_rejected_at_response_schema(
    forbidden_field: str,
) -> None:
    payload = _valid_payload()
    proposal = payload["proposals"][0]
    assert isinstance(proposal, dict)
    proposal[forbidden_field] = []

    with pytest.raises(ValidationError):
        validate_source_owned_response_payload(payload)

    summary = run_source_owned_response_pipeline(response_payload=payload)
    assert summary.response_validation_status == "rejected"
    assert summary.extraction_status == "failed_closed"
    assert summary.proposal_count == 0
    assert summary.grounded_numeric_claim_count == 0


def test_numeric_derivation_failure_is_downstream_of_successful_extraction() -> None:
    summary = run_source_owned_response_pipeline(scenario="numeric_derivation_failure")

    assert summary.response_validation_status == "validated"
    assert summary.extraction_status == "extracted"
    assert summary.anchor_selection_status == "selected"
    assert summary.derivation_status == "failed_closed"
    assert summary.proposal_count == 1
    assert summary.grounded_numeric_claim_count == 0


def test_legacy_extraction_and_provider_are_not_entered(monkeypatch: pytest.MonkeyPatch) -> None:
    import examcard.knowledge.extraction_v1 as legacy_extraction

    def forbidden_legacy(*_: object, **__: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    def forbidden_provider(*_: object, **__: object) -> None:
        raise AssertionError("provider path was entered")

    monkeypatch.setattr(legacy_extraction, "extract_document_proposals", forbidden_legacy)
    monkeypatch.setattr(Step2OpenAIAdapter, "complete_step2a", forbidden_provider)

    summary = run_source_owned_response_pipeline()

    assert summary.extraction_status == "extracted"
    assert summary.derivation_status == "derived"


def test_summary_is_content_free_and_allow_listed() -> None:
    summary = run_source_owned_response_pipeline()
    payload = summary.model_dump(mode="json")
    serialized = json.dumps(tuple(payload.values()), sort_keys=True).lower()

    assert set(payload) == {
        "response_validation_status",
        "extraction_status",
        "anchor_selection_status",
        "derivation_status",
        "proposal_count",
        "grounded_numeric_claim_count",
    }
    for forbidden in (
        "the source value",
        "65mpa",
        "assertion",
        "prompt",
        "response",
        "provider",
        "token",
        "secret",
        "credential",
        "canonical_text",
    ):
        assert forbidden not in serialized


def test_runner_requires_offline_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")

    with pytest.raises(ValueError, match="REAL_NETWORK=0"):
        run_source_owned_response_pipeline()


def test_legacy_payload_cannot_be_validated_as_source_owned_response() -> None:
    legacy_payload = {
        "proposals": [
            {
                "proposal_key": "proposal:legacy-001",
                "source_section_ids": ["section:" + "b" * 24],
                "fact_type": "definition",
                "label": "Legacy fact",
                "assertion": "The legacy contract remains available.",
                "primary_anchor_span_ids": [
                    default_source_owned_evidence().anchor_spans[0].anchor_span_id
                ],
                "numeric_claims": [],
            }
        ]
    }

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(legacy_payload)
