"""Offline tests for the explicit source-owned materialization input boundary."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from examcard.knowledge.contracts import (
    SourceOwnedCanonicalFact,
    SourceOwnedKnowledgeCardBridgeResult,
    SourceOwnedKnowledgeCardCandidate,
    SourceOwnedKnowledgeCardMaterializationInput,
    source_owned_bridge_to_knowledge_card,
)
from examcard.knowledge.contracts.hashing import canonical_step2_sha256
from examcard.knowledge.schema import EvidenceRef, FactType


PROPOSAL_KEY = "proposal:materialization"
ANCHOR_ID = "span:" + "b" * 24
EVIDENCE_ID = "ev:source-owned"
FACT_ID = "fact:" + canonical_step2_sha256(
    {
        "proposal_key": PROPOSAL_KEY,
        "fact_type": FactType.DEFINITION.value,
        "anchor_span_ids": [ANCHOR_ID],
    }
)[:24]
CARD_ID = "card:" + canonical_step2_sha256({"fact_id": FACT_ID})[:24]


def _candidate() -> SourceOwnedKnowledgeCardCandidate:
    return SourceOwnedKnowledgeCardCandidate(
        card_id=CARD_ID,
        fact_id=FACT_ID,
        title="Source-owned fact",
        body_assertion="The source states the fact.",
        provenance_anchor_span_ids=(ANCHOR_ID,),
        generation_status="ready",
    )


def _fact() -> SourceOwnedCanonicalFact:
    return SourceOwnedCanonicalFact(
        fact_id=FACT_ID,
        proposal_key=PROPOSAL_KEY,
        fact_type=FactType.DEFINITION,
        label="Source-owned fact",
        assertion="The source states the fact.",
        anchor_span_ids=(ANCHOR_ID,),
        derivation_status="assembled",
    )


def _input() -> SourceOwnedKnowledgeCardMaterializationInput:
    candidate = _candidate()
    bridge = SourceOwnedKnowledgeCardBridgeResult(
        status="bridged",
        knowledge_card_id=candidate.card_id,
        source_fact_id=candidate.fact_id,
        provenance_anchor_span_ids=candidate.provenance_anchor_span_ids,
    )
    return SourceOwnedKnowledgeCardMaterializationInput(
        bridge_result=bridge,
        candidate=candidate,
        canonical_fact=_fact(),
        evidence_refs=(EvidenceRef(evidence_id=EVIDENCE_ID, page_num=2),),
    )


def test_typed_input_keeps_bridge_candidate_fact_and_evidence_explicit() -> None:
    materialization_input = _input()

    assert isinstance(materialization_input, SourceOwnedKnowledgeCardMaterializationInput)
    assert materialization_input.bridge_result.status == "bridged"
    assert materialization_input.candidate.fact_id == FACT_ID
    assert materialization_input.canonical_fact.derivation_status == "assembled"
    assert materialization_input.evidence_refs[0].evidence_id == EVIDENCE_ID


def test_input_boundary_does_not_search_or_reconstruct_evidence() -> None:
    materialization_input = _input()

    assert materialization_input.evidence_refs == (EvidenceRef(evidence_id=EVIDENCE_ID, page_num=2),)
    assert not hasattr(materialization_input, "evidence_store")
    assert not hasattr(materialization_input, "raw_evidence")


@pytest.mark.parametrize(
    "forbidden_field",
    ("prompt", "provider", "raw_response", "generated_image"),
)
def test_input_boundary_rejects_authority_and_delivery_fields(
    forbidden_field: str,
) -> None:
    payload = _input().model_dump(mode="python")
    payload[forbidden_field] = "SENSITIVE"

    with pytest.raises(ValidationError):
        SourceOwnedKnowledgeCardMaterializationInput.model_validate(payload)


def test_input_boundary_requires_typed_source_owned_objects() -> None:
    payload = _input().model_dump(mode="python")
    payload["candidate"] = {"fact_id": FACT_ID}

    with pytest.raises(ValidationError):
        SourceOwnedKnowledgeCardMaterializationInput.model_validate(payload)


def test_input_boundary_allows_empty_refs_for_materializer_fail_closed_check() -> None:
    materialization_input = _input().model_copy(update={"evidence_refs": ()})

    assert materialization_input.evidence_refs == ()


def test_input_boundary_does_not_call_provider_or_use_bridge_lookup() -> None:
    candidate = _candidate()
    bridge = source_owned_bridge_to_knowledge_card(candidate)
    assert bridge.status == "bridged"
    materialization_input = SourceOwnedKnowledgeCardMaterializationInput(
        bridge_result=bridge,
        candidate=candidate,
        canonical_fact=_fact(),
        evidence_refs=(EvidenceRef(evidence_id=EVIDENCE_ID, page_num=2),),
    )

    assert materialization_input.bridge_result is bridge
    assert materialization_input.candidate is candidate
