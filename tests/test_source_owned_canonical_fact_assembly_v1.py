"""Offline tests for source-owned canonical fact assembly."""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    SourceOwnedAnchorSelectionResult,
    SourceOwnedCanonicalFact,
    SourceOwnedCanonicalProposal,
    Step2AExtractionPath,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2AResponseHandlingMode,
    Step2Stage,
    make_evidence_anchor_span_id,
    make_step2_request_identity,
    source_owned_assemble_canonical_fact,
    source_owned_derive_atomic_numeric_claims,
    source_owned_derive_from_evidence,
    source_owned_select_anchor_spans,
    step2a_response_handling_mode,
    step2a_result_payload_model,
)
from examcard.knowledge.extraction_v1 import (
    select_document_extraction_path,
    source_owned_extract_document_proposals,
)
from examcard.materials.schema import CoverageRequirement, PolicyDecision, PolicyDisposition, SemanticRole


def _make_anchor(text: str, *, suffix: str = "a") -> object:
    from examcard.knowledge.contracts import EvidenceAnchorSpan

    parent_evidence_id = "ev:" + suffix * 24
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


def _canonical_proposal(anchor_span_id: str, *, assertion: str) -> SourceOwnedCanonicalProposal:
    response = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(
        {
            "proposals": [
                {
                    "fact_type": "definition",
                    "label": "Source-owned fact",
                    "assertion": assertion,
                    "primary_anchor_span_ids": [anchor_span_id],
                }
            ]
        }
    )
    return source_owned_extract_document_proposals(response=response)[0]


def _assemble(proposal: SourceOwnedCanonicalProposal, anchor: object):
    selection = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans={proposal.primary_anchor_span_ids[0]: anchor},
    )
    derivation = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={proposal.primary_anchor_span_ids[0]: anchor},
    )
    return source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=selection,
        derivation_result=derivation,
    )


def test_selected_anchor_and_local_numeric_derivation_assemble() -> None:
    anchor = _make_anchor("45MPa")
    proposal = _canonical_proposal(anchor.anchor_span_id, assertion="The value is 45MPa.")

    fact = _assemble(proposal, anchor)

    assert isinstance(fact, SourceOwnedCanonicalFact)
    assert fact.derivation_status == "assembled"
    assert fact.proposal_key == proposal.proposal_key
    assert fact.anchor_span_ids == (anchor.anchor_span_id,)
    assert len(fact.grounded_numeric_claims) == 1


def test_selected_anchor_with_no_quantitative_tokens_still_assembles() -> None:
    anchor = _make_anchor("plain source text")
    proposal = _canonical_proposal(anchor.anchor_span_id, assertion="The selected source defines the fact.")
    selection = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans={anchor.anchor_span_id: anchor},
    )
    derivation = source_owned_derive_from_evidence(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    fact = source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=selection,
        derivation_result=derivation,
    )

    assert fact.derivation_status == "assembled"
    assert fact.grounded_numeric_claims == ()


def test_failed_anchor_selection_returns_failed_closed_fact() -> None:
    anchor = _make_anchor("45MPa")
    proposal = _canonical_proposal(anchor.anchor_span_id, assertion="The value is 45MPa.")
    derivation = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    fact = source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=SourceOwnedAnchorSelectionResult(anchor_selection_status="failed_closed"),
        derivation_result=derivation,
    )

    assert fact.derivation_status == "failed_closed"
    assert fact.grounded_numeric_claims == ()


def test_failed_local_derivation_returns_failed_closed_fact() -> None:
    anchor = _make_anchor("45MPa")
    proposal = _canonical_proposal(anchor.anchor_span_id, assertion="The value is 45MPa.")
    selection = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans={anchor.anchor_span_id: anchor},
    )
    failed_derivation = source_owned_derive_from_evidence(proposal=proposal, evidence_spans={})

    fact = source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=selection,
        derivation_result=failed_derivation,
    )

    assert fact.derivation_status == "failed_closed"
    assert fact.grounded_numeric_claims == ()


def test_inconsistent_local_proposal_identity_fails_closed() -> None:
    anchor = _make_anchor("45MPa")
    proposal = _canonical_proposal(anchor.anchor_span_id, assertion="The value is 45MPa.")
    selection = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans={anchor.anchor_span_id: anchor},
    )
    derivation = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )
    inconsistent = derivation.model_copy(update={"proposal_key": "proposal:other"})

    fact = source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=selection,
        derivation_result=inconsistent,
    )

    assert fact.derivation_status == "failed_closed"
    assert fact.grounded_numeric_claims == ()


def test_model_numeric_claim_injection_is_rejected_before_assembly() -> None:
    anchor = _make_anchor("45MPa")
    payload = {
        "proposals": [
            {
                "fact_type": "definition",
                "label": "Source-owned fact",
                "assertion": "The value is 45MPa.",
                "primary_anchor_span_ids": [anchor.anchor_span_id],
                "numeric_claims": [],
            }
        ]
    }

    with pytest.raises(ValidationError):
        Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(payload)


def test_fact_id_is_deterministic_and_uses_only_contract_identity() -> None:
    anchor = _make_anchor("plain source text")
    first = SourceOwnedCanonicalProposal(
        proposal_key="proposal:stable",
        fact_type="definition",
        label="First label",
        assertion="First assertion.",
        primary_anchor_span_ids=(anchor.anchor_span_id,),
    )
    second = first.model_copy(update={"label": "Second label", "assertion": "Second assertion."})

    first_fact = _assemble(first, anchor)
    second_selection = source_owned_select_anchor_spans(
        proposal=second,
        candidate_anchor_spans={anchor.anchor_span_id: anchor},
    )
    second_derivation = source_owned_derive_from_evidence(
        proposal=second,
        evidence_spans={anchor.anchor_span_id: anchor},
    )
    second_fact = source_owned_assemble_canonical_fact(
        proposal=second,
        anchor_selection=second_selection,
        derivation_result=second_derivation,
    )

    assert first_fact.fact_id == second_fact.fact_id
    assert first_fact.fact_id.startswith("fact:")
    assert first_fact.fact_id == _assemble(first, anchor).fact_id


def test_canonical_fact_preserves_local_numeric_provenance_without_source_text() -> None:
    secret_text = "SECRET_RAW_EVIDENCE_TEXT 45MPa"
    anchor = _make_anchor(secret_text)
    proposal = _canonical_proposal(anchor.anchor_span_id, assertion="The value is 45MPa.")

    fact = _assemble(proposal, anchor)
    dumped = fact.model_dump(mode="json")
    serialized = json.dumps(dumped, sort_keys=True)

    claim = fact.grounded_numeric_claims[0]
    assert fact.anchor_span_ids == (anchor.anchor_span_id,)
    assert claim.anchor_span_id == anchor.anchor_span_id
    assert claim.evidence_id == anchor.parent_evidence_id
    assert secret_text not in serialized
    assert "canonical_text" not in serialized
    assert "prompt" not in serialized
    assert "response" not in serialized
    assert "provider" not in serialized


def test_canonical_fact_schema_has_no_internal_diagnostics() -> None:
    schema_text = json.dumps(SourceOwnedCanonicalFact.model_json_schema(), sort_keys=True)

    assert "diagnostic" not in schema_text.lower()
    assert "numeric_claim_failure_diagnostics" not in schema_text


def _identity(request_version: str):
    return make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=request_version,
        model="offline-canonical-fact-test-model",
        payload_sha256="b" * 64,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
    )


@pytest.mark.parametrize(
    "request_version",
    (
        STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    ),
)
def test_legacy_contracts_do_not_enter_source_owned_assembly_path(request_version: str) -> None:
    identity = _identity(request_version)

    assert select_document_extraction_path(identity) == Step2AExtractionPath.EXISTING_VERSIONED
    assert step2a_response_handling_mode(request_version) == Step2AResponseHandlingMode.EXISTING_VERSIONED
    assert request_version != PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION
