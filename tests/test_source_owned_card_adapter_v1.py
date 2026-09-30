"""Offline tests for the source-owned canonical-fact card adapter."""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    SourceOwnedAnchorSelectionResult,
    SourceOwnedCanonicalFact,
    SourceOwnedKnowledgeCardCandidate,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    make_evidence_anchor_span_id,
    source_owned_assemble_canonical_fact,
    source_owned_derive_atomic_numeric_claims,
    source_owned_derive_from_evidence,
    source_owned_fact_to_card_candidate,
    source_owned_select_anchor_spans,
)
from examcard.knowledge.extraction_v1 import source_owned_extract_document_proposals
from examcard.knowledge.schema import (
    ContentSegment,
    EvidenceRef,
    GroundingStatus,
    KnowledgeCard,
)
from examcard.materials.schema import CoverageRequirement, PolicyDecision, PolicyDisposition, SemanticRole


def _make_anchor(text: str, *, suffix: str = "a") -> EvidenceAnchorSpan:
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


def _proposal(anchor_span_id: str, *, assertion: str) -> object:
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


def _assembled_fact(*, anchor: EvidenceAnchorSpan, numeric: bool = True) -> SourceOwnedCanonicalFact:
    proposal = _proposal(
        anchor.anchor_span_id,
        assertion="The value is 45MPa." if numeric else "The selected source defines the fact.",
    )
    selection = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans={anchor.anchor_span_id: anchor},
    )
    derivation = (
        source_owned_derive_atomic_numeric_claims(
            proposal=proposal,
            evidence_spans={anchor.anchor_span_id: anchor},
        )
        if numeric
        else source_owned_derive_from_evidence(
            proposal=proposal,
            evidence_spans={anchor.anchor_span_id: anchor},
        )
    )
    return source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=selection,
        derivation_result=derivation,
    )


def test_assembled_fact_maps_to_ready_candidate() -> None:
    fact = _assembled_fact(anchor=_make_anchor("45MPa"))

    candidate = source_owned_fact_to_card_candidate(fact)

    assert isinstance(candidate, SourceOwnedKnowledgeCardCandidate)
    assert candidate.generation_status == "ready"
    assert candidate.fact_id == fact.fact_id
    assert candidate.title == fact.label
    assert candidate.body_assertion == fact.assertion
    assert candidate.provenance_anchor_span_ids == fact.anchor_span_ids
    assert candidate.grounded_numeric_claims == fact.grounded_numeric_claims


def test_failed_fact_maps_to_failed_closed_candidate() -> None:
    anchor = _make_anchor("45MPa")
    proposal = _proposal(anchor.anchor_span_id, assertion="The value is 45MPa.")
    derivation = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )
    failed_fact = source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=SourceOwnedAnchorSelectionResult(anchor_selection_status="failed_closed"),
        derivation_result=derivation,
    )

    candidate = source_owned_fact_to_card_candidate(failed_fact)

    assert candidate.generation_status == "failed_closed"
    assert candidate.grounded_numeric_claims == ()
    assert candidate.provenance_anchor_span_ids == ()


def test_card_id_is_deterministic_and_depends_only_on_fact_id() -> None:
    fact = _assembled_fact(anchor=_make_anchor("45MPa"))

    first = source_owned_fact_to_card_candidate(fact)
    second = source_owned_fact_to_card_candidate(fact)
    changed_text = fact.model_copy(update={"label": "Different label", "assertion": "Different assertion."})
    changed = source_owned_fact_to_card_candidate(changed_text)

    assert first.card_id == second.card_id == changed.card_id
    assert first.card_id.startswith("card:")


@pytest.mark.parametrize(
    "update",
    (
        {"fact_id": ""},
        {"proposal_key": ""},
        {"anchor_span_ids": ()},
    ),
)
def test_missing_identity_or_invalid_provenance_fails_closed(update: dict[str, object]) -> None:
    fact = _assembled_fact(anchor=_make_anchor("45MPa"))
    malformed = fact.model_copy(update=update)

    candidate = source_owned_fact_to_card_candidate(malformed)

    assert candidate.generation_status == "failed_closed"
    assert candidate.grounded_numeric_claims == ()
    assert candidate.provenance_anchor_span_ids == ()


def test_invalid_numeric_provenance_fails_closed() -> None:
    fact = _assembled_fact(anchor=_make_anchor("45MPa"))
    malformed = fact.model_copy(
        update={
            "grounded_numeric_claims": (
                {
                    "value": "45MPa",
                    "anchor_span_id": "span:" + "f" * 24,
                    "evidence_id": "ev:invalid",
                },
            )
        }
    )

    candidate = source_owned_fact_to_card_candidate(malformed)

    assert candidate.generation_status == "failed_closed"
    assert candidate.grounded_numeric_claims == ()


def test_numeric_claim_and_provenance_are_transmitted_without_matching_or_tokenizing() -> None:
    anchor = _make_anchor("SECRET_RAW_EVIDENCE_TEXT 45MPa")
    fact = _assembled_fact(anchor=anchor)
    before = fact.model_dump(mode="json")

    candidate = source_owned_fact_to_card_candidate(fact)

    assert fact.model_dump(mode="json") == before
    assert candidate.grounded_numeric_claims == fact.grounded_numeric_claims
    assert candidate.provenance_anchor_span_ids == fact.anchor_span_ids
    serialized = json.dumps(candidate.model_dump(mode="json"), sort_keys=True)
    assert "SECRET_RAW_EVIDENCE_TEXT" not in serialized
    assert "prompt" not in serialized
    assert "response" not in serialized
    assert "provider" not in serialized
    assert "evidence_text" not in serialized


def test_candidate_contract_is_minimal_and_does_not_modify_knowledgecard_schema() -> None:
    candidate = source_owned_fact_to_card_candidate(_assembled_fact(anchor=_make_anchor("plain source"), numeric=False))
    candidate_keys = set(candidate.model_dump(mode="json"))

    assert candidate_keys == {
        "card_id",
        "fact_id",
        "title",
        "body_assertion",
        "grounded_numeric_claims",
        "provenance_anchor_span_ids",
        "generation_status",
    }
    assert not isinstance(candidate, KnowledgeCard)
    assert "body_assertion" not in KnowledgeCard.model_json_schema().get("properties", {})


def test_legacy_knowledgecard_object_cannot_enter_source_owned_adapter() -> None:
    evidence_id = "ev:legacy"
    fact_id = "fact:" + "1" * 24
    legacy_card = KnowledgeCard(
        id="card:legacy",
        raw_id="kp:" + "2" * 24,
        title="Legacy card",
        content="Legacy content",
        content_segments=(
            ContentSegment(
                text="Legacy content",
                fact_ids=(fact_id,),
                evidence_ids=(evidence_id,),
                renderer="text",
            ),
        ),
        evidence_refs=(EvidenceRef(evidence_id=evidence_id, page_num=1),),
        grounding_status=GroundingStatus.VERIFIED,
        source_pages=(1,),
    )

    with pytest.raises(ValueError, match="canonical source-owned fact"):
        source_owned_fact_to_card_candidate(legacy_card)  # type: ignore[arg-type]


def test_model_response_cannot_supply_numeric_claims_to_adapter() -> None:
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
