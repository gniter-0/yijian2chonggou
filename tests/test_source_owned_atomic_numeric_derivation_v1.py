"""Offline tests for source-owned atomic numeric derivation."""
from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    EvidenceAnchorSpan,
    Step2AExtractionPath,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2AResponseHandlingMode,
    Step2Stage,
    make_evidence_anchor_span_id,
    make_step2_request_identity,
    source_owned_derive_atomic_numeric_claims,
    step2a_response_handling_mode,
    step2a_result_payload_model,
)
from examcard.knowledge.extraction_v1 import (
    select_document_extraction_path,
    source_owned_extract_document_proposals,
)
from examcard.materials.schema import CoverageRequirement, PolicyDecision, PolicyDisposition, SemanticRole


def _make_anchor(text: str, *, evidence_suffix: str = "a") -> EvidenceAnchorSpan:
    parent_evidence_id = "ev:" + evidence_suffix * 24
    content_policy_fingerprint = "b" * 64
    source_manifest_sha256 = "c" * 64
    exact_text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    anchor_span_id = make_evidence_anchor_span_id(
        parent_evidence_id=parent_evidence_id,
        start=0,
        end=len(text),
        exact_text_sha256=exact_text_sha256,
        content_policy_fingerprint_sha256=content_policy_fingerprint,
        source_manifest_sha256=source_manifest_sha256,
    )
    policy_decision = PolicyDecision(
        evidence_id=parent_evidence_id,
        semantic_role=SemanticRole.CORE,
        disposition=PolicyDisposition.INCLUDED,
        coverage_requirement=CoverageRequirement.REQUIRED,
        policy_id="core-policy-v1",
        policy_revision=1,
        reason="selected source anchor",
    )
    return EvidenceAnchorSpan(
        anchor_span_id=anchor_span_id,
        parent_evidence_id=parent_evidence_id,
        parent_evidence_kind=EvidenceKind.PARAGRAPH,
        start=0,
        end=len(text),
        canonical_text=text,
        exact_text_sha256=exact_text_sha256,
        content_policy_id="core-policy-v1",
        content_policy_revision=1,
        content_policy_fingerprint_sha256=content_policy_fingerprint,
        policy_decision=policy_decision,
        source_manifest_sha256=source_manifest_sha256,
    )


def _canonical_proposal(
    *,
    assertion: str,
    anchor_span_ids: tuple[str, ...],
    fact_type: str = "definition",
):
    payload = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(
        {
            "proposals": [
                {
                    "fact_type": fact_type,
                    "label": "Source-owned fact",
                    "assertion": assertion,
                    "primary_anchor_span_ids": list(anchor_span_ids),
                }
            ]
        }
    )
    return source_owned_extract_document_proposals(response=payload)[0]


def _identity(request_version: str):
    return make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=request_version,
        model="offline-atomic-numeric-model",
        payload_sha256="b" * 64,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
    )


def test_bare_number_derives_one_grounded_claim_with_parent_provenance() -> None:
    anchor = _make_anchor("The source value is 42 units")
    proposal = _canonical_proposal(assertion="The asserted value is 42 units", anchor_span_ids=(anchor.anchor_span_id,))

    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.numeric_derivation_status == "derived"
    assert len(result.grounded_numeric_claims) == 1
    claim = result.grounded_numeric_claims[0]
    assert claim.value == "42"
    assert claim.anchor_span_id == anchor.anchor_span_id
    assert claim.evidence_id == anchor.parent_evidence_id


def test_unit_bearing_value_preserves_the_complete_source_token() -> None:
    anchor = _make_anchor("The rated value is 65MPa.")
    proposal = _canonical_proposal(assertion="The rated value is 65MPa.", anchor_span_ids=(anchor.anchor_span_id,))

    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.numeric_derivation_status == "derived"
    assert tuple(claim.value for claim in result.grounded_numeric_claims) == ("65MPa",)


@pytest.mark.parametrize("structural_text", ("1、", "3.2", "Q235", "P12"))
def test_structural_numeric_classes_do_not_generate_numeric_claims(structural_text: str) -> None:
    anchor = _make_anchor(structural_text)
    proposal = _canonical_proposal(assertion=structural_text, anchor_span_ids=(anchor.anchor_span_id,))

    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.numeric_derivation_status == "no_quantitative_tokens"
    assert result.grounded_numeric_claims == ()


def test_assertion_quantitative_token_without_source_candidate_fails_closed() -> None:
    anchor = _make_anchor("The source has no numeric expression.")
    proposal = _canonical_proposal(assertion="The asserted value is 42 units", anchor_span_ids=(anchor.anchor_span_id,))

    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.numeric_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_same_quantitative_token_in_two_selected_anchors_fails_closed() -> None:
    first = _make_anchor("First source value is 42 units", evidence_suffix="a")
    second = _make_anchor("Second source value is 42 units", evidence_suffix="d")
    proposal = _canonical_proposal(
        assertion="The asserted value is 42 units",
        anchor_span_ids=(first.anchor_span_id, second.anchor_span_id),
    )

    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={first.anchor_span_id: first, second.anchor_span_id: second},
    )

    assert result.numeric_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_missing_anchor_fails_closed_before_numeric_derivation() -> None:
    anchor = _make_anchor("The source value is 42 units")
    proposal = _canonical_proposal(assertion="The asserted value is 42 units", anchor_span_ids=(anchor.anchor_span_id,))

    result = source_owned_derive_atomic_numeric_claims(proposal=proposal, evidence_spans={})

    assert result.derived_facts.local_extraction_status == "failed_closed"
    assert result.numeric_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_composition_numeric_derivation_is_explicitly_unsupported() -> None:
    anchor = _make_anchor("The table value is 42.")
    proposal = _canonical_proposal(
        assertion="The table value is 42.",
        anchor_span_ids=(anchor.anchor_span_id,),
        fact_type="composition",
    )

    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.numeric_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_no_quantitative_assertion_is_not_a_failure() -> None:
    anchor = _make_anchor("The source states a qualitative rule.")
    proposal = _canonical_proposal(
        assertion="The assertion states a qualitative rule.",
        anchor_span_ids=(anchor.anchor_span_id,),
    )

    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.numeric_derivation_status == "no_quantitative_tokens"
    assert result.grounded_numeric_claims == ()


def test_model_numeric_authority_is_rejected_before_derivation() -> None:
    anchor = _make_anchor("The source value is 42.")
    payload = {
        "proposals": [
            {
                "fact_type": "definition",
                "label": "Source-owned fact",
                "assertion": "The asserted value is 42.",
                "primary_anchor_span_ids": [anchor.anchor_span_id],
                "numeric_claims": [],
            }
        ]
    }

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(payload)


@pytest.mark.parametrize(
    "request_version",
    (
        STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    ),
)
def test_v10_v21_v23_remain_outside_source_owned_atomic_lane(request_version: str) -> None:
    identity = _identity(request_version)

    assert select_document_extraction_path(identity) == Step2AExtractionPath.EXISTING_VERSIONED
    assert step2a_response_handling_mode(request_version) == Step2AResponseHandlingMode.EXISTING_VERSIONED
    assert request_version != PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION


def test_successful_claim_has_only_claim_provenance_not_full_evidence_text() -> None:
    source_text = "PRIVATE_FULL_EVIDENCE_TEXT 42 units"
    anchor = _make_anchor(source_text)
    proposal = _canonical_proposal(assertion="The asserted value is 42 units", anchor_span_ids=(anchor.anchor_span_id,))

    result = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )
    claim_dump = result.grounded_numeric_claims[0].model_dump(mode="json")

    assert set(claim_dump) == {"value", "anchor_span_id", "evidence_id"}
    assert source_text not in str(result.model_dump(mode="json"))
