"""Materialize source-owned card inputs into the existing KnowledgeCard type."""
from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from ..schema import ContentSegment, EvidenceRef, GroundingStatus, KnowledgeCard
from .hashing import canonical_step2_sha256
from .source_owned_card_adapter import SourceOwnedKnowledgeCardCandidate
from .source_owned_card_pipeline_bridge import SourceOwnedKnowledgeCardBridgeResult
from .source_owned_contract import SourceOwnedCanonicalFact


SourceOwnedKnowledgeCardMaterializationStatus: TypeAlias = Literal[
    "materialized",
    "failed_closed",
]


class SourceOwnedKnowledgeCardMaterializationInput(StrictModel):
    """Explicit inputs required to build a downstream KnowledgeCard.

    The input deliberately carries the source-owned objects separately. No
    missing content, provenance, or page metadata is looked up from the bridge
    result, and no provider response is accepted at this boundary.
    """

    bridge_result: SourceOwnedKnowledgeCardBridgeResult
    candidate: SourceOwnedKnowledgeCardCandidate
    canonical_fact: SourceOwnedCanonicalFact
    evidence_refs: tuple[EvidenceRef, ...] = ()


class SourceOwnedKnowledgeCardMaterializationResult(StrictModel):
    """Typed result of the source-owned-to-KnowledgeCard materialization."""

    status: SourceOwnedKnowledgeCardMaterializationStatus
    knowledge_card_id: str = Field(min_length=0)
    materialized_card: KnowledgeCard | None = None

    @model_validator(mode="after")
    def validate_materialization_result(self) -> "SourceOwnedKnowledgeCardMaterializationResult":
        if self.status == "materialized":
            if not self.knowledge_card_id or self.materialized_card is None:
                raise ValueError("materialized source-owned results require a KnowledgeCard")
            if self.materialized_card.id != self.knowledge_card_id:
                raise ValueError("materialized KnowledgeCard identity must match the bridge identity")
        elif self.knowledge_card_id or self.materialized_card is not None:
            raise ValueError("failed source-owned materialization results cannot carry a card")
        return self

    @classmethod
    def materialized(cls, card: KnowledgeCard) -> "SourceOwnedKnowledgeCardMaterializationResult":
        return cls(
            status="materialized",
            knowledge_card_id=card.id,
            materialized_card=card,
        )

    @classmethod
    def failed_closed(cls) -> "SourceOwnedKnowledgeCardMaterializationResult":
        return cls(
            status="failed_closed",
            knowledge_card_id="",
            materialized_card=None,
        )


def _source_owned_raw_card_id(*, source_fact_id: str, knowledge_card_id: str) -> str:
    digest = canonical_step2_sha256(
        {
            "source_fact_id": source_fact_id,
            "knowledge_card_id": knowledge_card_id,
        }
    )
    return f"kp:{digest[:24]}"


def _has_explicit_evidence_refs(evidence_refs: object) -> bool:
    if not isinstance(evidence_refs, tuple) or not evidence_refs:
        return False
    if any(not isinstance(ref, EvidenceRef) for ref in evidence_refs):
        return False
    evidence_ids = tuple(ref.evidence_id for ref in evidence_refs)
    return len(evidence_ids) == len(set(evidence_ids)) and all(evidence_ids)


def _inputs_are_consistent(
    materialization_input: SourceOwnedKnowledgeCardMaterializationInput,
) -> bool:
    bridge = materialization_input.bridge_result
    candidate = materialization_input.candidate
    fact = materialization_input.canonical_fact

    if bridge.status != "bridged":
        return False
    if candidate.generation_status != "ready" or fact.derivation_status != "assembled":
        return False
    if not bridge.knowledge_card_id or not bridge.source_fact_id:
        return False
    if bridge.knowledge_card_id != candidate.card_id:
        return False
    if bridge.source_fact_id != candidate.fact_id or candidate.fact_id != fact.fact_id:
        return False
    if bridge.provenance_anchor_span_ids != candidate.provenance_anchor_span_ids:
        return False
    if fact.anchor_span_ids != candidate.provenance_anchor_span_ids:
        return False
    if fact.label != candidate.title or fact.assertion != candidate.body_assertion:
        return False
    if fact.grounded_numeric_claims != candidate.grounded_numeric_claims:
        return False
    if not candidate.title or not candidate.body_assertion:
        return False
    if not _has_explicit_evidence_refs(materialization_input.evidence_refs):
        return False

    evidence_ids = {ref.evidence_id for ref in materialization_input.evidence_refs}
    return all(claim.evidence_id in evidence_ids for claim in candidate.grounded_numeric_claims)


def _build_knowledge_card(
    materialization_input: SourceOwnedKnowledgeCardMaterializationInput,
) -> KnowledgeCard:
    candidate = materialization_input.candidate
    fact = materialization_input.canonical_fact
    evidence_refs = materialization_input.evidence_refs
    evidence_ids = tuple(ref.evidence_id for ref in evidence_refs)
    source_pages = tuple(sorted({ref.page_num for ref in evidence_refs}))
    return KnowledgeCard(
        id=candidate.card_id,
        raw_id=_source_owned_raw_card_id(
            source_fact_id=fact.fact_id,
            knowledge_card_id=candidate.card_id,
        ),
        title=candidate.title,
        content=candidate.body_assertion,
        content_segments=(
            ContentSegment(
                text=candidate.body_assertion,
                fact_ids=(fact.fact_id,),
                evidence_ids=evidence_ids,
                renderer="text",
            ),
        ),
        evidence_refs=evidence_refs,
        grounding_status=GroundingStatus.VERIFIED,
        source_pages=source_pages,
    )


def source_owned_materialize_knowledge_card(
    materialization_input: object,
) -> SourceOwnedKnowledgeCardMaterializationResult:
    """Build one existing ``KnowledgeCard`` from explicit source-owned inputs.

    This function performs only structural mapping. It does not rewrite the
    assertion, re-ground numeric claims, tokenize, query Evidence, or invoke a
    provider. Numeric claim and anchor provenance consistency is checked before
    the card is built; the existing KnowledgeCard schema stores the assertion,
    fact identity, and Evidence references without introducing new fields.
    """

    if not isinstance(materialization_input, SourceOwnedKnowledgeCardMaterializationInput):
        return SourceOwnedKnowledgeCardMaterializationResult.failed_closed()
    if not _inputs_are_consistent(materialization_input):
        return SourceOwnedKnowledgeCardMaterializationResult.failed_closed()
    try:
        card = _build_knowledge_card(materialization_input)
    except (TypeError, ValueError):
        return SourceOwnedKnowledgeCardMaterializationResult.failed_closed()
    return SourceOwnedKnowledgeCardMaterializationResult.materialized(card)
