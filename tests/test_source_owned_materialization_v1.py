"""Offline tests for source-owned KnowledgeCard materialization."""
from __future__ import annotations

import json

import pytest

from examcard.knowledge.contracts import (
    GroundedNumericClaim,
    SourceOwnedCanonicalFact,
    SourceOwnedKnowledgeCardBridgeResult,
    SourceOwnedKnowledgeCardCandidate,
    SourceOwnedKnowledgeCardMaterializationInput,
    SourceOwnedKnowledgeCardMaterializationResult,
    source_owned_materialize_knowledge_card,
)
from examcard.knowledge.contracts.hashing import canonical_step2_sha256
from examcard.knowledge.schema import EvidenceRef, FactType, KnowledgeCard


ANCHOR_ID = "span:" + "b" * 24
EVIDENCE_ID = "ev:source-owned"
PROPOSAL_KEY = "proposal:materialization"


def _fact_id(*, proposal_key: str = PROPOSAL_KEY, anchor_id: str = ANCHOR_ID) -> str:
    return "fact:" + canonical_step2_sha256(
        {
            "proposal_key": proposal_key,
            "fact_type": FactType.DEFINITION.value,
            "anchor_span_ids": [anchor_id],
        }
    )[:24]


def _card_id(fact_id: str) -> str:
    return "card:" + canonical_step2_sha256({"fact_id": fact_id})[:24]


def _candidate(
    *,
    fact_id: str | None = None,
    claims: tuple[GroundedNumericClaim, ...] = (),
) -> SourceOwnedKnowledgeCardCandidate:
    resolved_fact_id = fact_id or _fact_id()
    return SourceOwnedKnowledgeCardCandidate(
        card_id=_card_id(resolved_fact_id),
        fact_id=resolved_fact_id,
        title="Source-owned fact",
        body_assertion="The source states the fact.",
        grounded_numeric_claims=claims,
        provenance_anchor_span_ids=(ANCHOR_ID,),
        generation_status="ready",
    )


def _fact(
    *,
    fact_id: str | None = None,
    proposal_key: str = PROPOSAL_KEY,
    claims: tuple[GroundedNumericClaim, ...] = (),
) -> SourceOwnedCanonicalFact:
    resolved_fact_id = fact_id or _fact_id(proposal_key=proposal_key)
    return SourceOwnedCanonicalFact(
        fact_id=resolved_fact_id,
        proposal_key=proposal_key,
        fact_type=FactType.DEFINITION,
        label="Source-owned fact",
        assertion="The source states the fact.",
        anchor_span_ids=(ANCHOR_ID,),
        grounded_numeric_claims=claims,
        derivation_status="assembled",
    )


def _input(
    *,
    candidate: SourceOwnedKnowledgeCardCandidate | None = None,
    fact: SourceOwnedCanonicalFact | None = None,
    bridge: SourceOwnedKnowledgeCardBridgeResult | None = None,
    evidence_refs: tuple[EvidenceRef, ...] = (EvidenceRef(evidence_id=EVIDENCE_ID, page_num=2),),
) -> SourceOwnedKnowledgeCardMaterializationInput:
    resolved_candidate = candidate or _candidate()
    return SourceOwnedKnowledgeCardMaterializationInput(
        bridge_result=bridge
        or SourceOwnedKnowledgeCardBridgeResult(
            status="bridged",
            knowledge_card_id=resolved_candidate.card_id,
            source_fact_id=resolved_candidate.fact_id,
            provenance_anchor_span_ids=resolved_candidate.provenance_anchor_span_ids,
        ),
        candidate=resolved_candidate,
        canonical_fact=fact or _fact(fact_id=resolved_candidate.fact_id),
        evidence_refs=evidence_refs,
    )


def test_consistent_bridge_candidate_fact_and_refs_materialize() -> None:
    materialization_input = _input()

    result = source_owned_materialize_knowledge_card(materialization_input)

    assert result.status == "materialized"
    assert result.knowledge_card_id == materialization_input.candidate.card_id
    assert isinstance(result.materialized_card, KnowledgeCard)
    assert result.materialized_card.id == materialization_input.candidate.card_id
    assert result.materialized_card.title == materialization_input.candidate.title
    assert result.materialized_card.content == materialization_input.candidate.body_assertion
    assert result.materialized_card.evidence_refs == materialization_input.evidence_refs
    assert result.materialized_card.source_pages == (2,)


def test_bridge_identity_mismatch_fails_closed() -> None:
    candidate = _candidate()
    bridge = SourceOwnedKnowledgeCardBridgeResult(
        status="bridged",
        knowledge_card_id="card:" + "c" * 24,
        source_fact_id=candidate.fact_id,
        provenance_anchor_span_ids=candidate.provenance_anchor_span_ids,
    )

    result = source_owned_materialize_knowledge_card(_input(candidate=candidate, bridge=bridge))

    assert result == SourceOwnedKnowledgeCardMaterializationResult.failed_closed()


def test_candidate_and_canonical_fact_identity_mismatch_fails_closed() -> None:
    candidate = _candidate()
    other_fact = _fact(proposal_key="proposal:other")

    result = source_owned_materialize_knowledge_card(
        _input(candidate=candidate, fact=other_fact),
    )

    assert result.status == "failed_closed"
    assert result.materialized_card is None


def test_missing_evidence_refs_fails_closed() -> None:
    result = source_owned_materialize_knowledge_card(_input(evidence_refs=()))

    assert result == SourceOwnedKnowledgeCardMaterializationResult.failed_closed()


def test_numeric_claim_and_provenance_are_preserved_without_rederivation() -> None:
    claim = GroundedNumericClaim(
        value="45MPa",
        anchor_span_id=ANCHOR_ID,
        evidence_id=EVIDENCE_ID,
    )
    candidate = _candidate(claims=(claim,))
    fact = _fact(fact_id=candidate.fact_id, claims=(claim,))
    materialization_input = _input(candidate=candidate, fact=fact)
    before_candidate = candidate.model_dump(mode="json")
    before_fact = fact.model_dump(mode="json")

    result = source_owned_materialize_knowledge_card(materialization_input)

    assert result.status == "materialized"
    assert candidate.model_dump(mode="json") == before_candidate
    assert fact.model_dump(mode="json") == before_fact
    assert candidate.grounded_numeric_claims == (claim,)
    assert fact.grounded_numeric_claims == (claim,)
    assert result.materialized_card is not None
    assert result.materialized_card.evidence_refs[0].evidence_id == claim.evidence_id
    assert result.materialized_card.content_segments[0].evidence_ids == (EVIDENCE_ID,)


def test_materialization_is_deterministic() -> None:
    materialization_input = _input()

    first = source_owned_materialize_knowledge_card(materialization_input)
    second = source_owned_materialize_knowledge_card(materialization_input)

    assert first.status == second.status == "materialized"
    assert first.knowledge_card_id == second.knowledge_card_id
    assert first.materialized_card is not None
    assert second.materialized_card is not None
    assert first.materialized_card.model_dump(mode="json") == second.materialized_card.model_dump(mode="json")


@pytest.mark.parametrize("forbidden_field", ("provider", "prompt", "raw_response", "generated_image"))
def test_materialized_result_has_no_provider_prompt_or_image_fields(
    forbidden_field: str,
) -> None:
    result = source_owned_materialize_knowledge_card(_input())
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    assert result.status == "materialized"
    assert forbidden_field not in serialized
    assert all(
        field not in result.materialized_card.model_dump(mode="json")
        for field in ("provider", "prompt", "raw_response", "generated_image")
    )


def test_failed_bridge_fails_closed() -> None:
    result = source_owned_materialize_knowledge_card(
        _input(bridge=SourceOwnedKnowledgeCardBridgeResult.failed_closed()),
    )

    assert result.status == "failed_closed"


def test_legacy_knowledgecard_cannot_enter_source_owned_materializer() -> None:
    legacy_card = KnowledgeCard.model_construct()

    result = source_owned_materialize_knowledge_card(legacy_card)

    assert result == SourceOwnedKnowledgeCardMaterializationResult.failed_closed()


def test_invalid_input_object_fails_closed() -> None:
    result = source_owned_materialize_knowledge_card({"prompt": "not allowed"})

    assert result == SourceOwnedKnowledgeCardMaterializationResult.failed_closed()
