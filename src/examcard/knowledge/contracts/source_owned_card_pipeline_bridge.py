"""Bridge admission-approved source-owned candidates to the card pipeline."""
from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from .source_owned_card_adapter import SourceOwnedKnowledgeCardCandidate
from .source_owned_card_admission import source_owned_admit_card_candidate


SourceOwnedKnowledgeCardBridgeStatus: TypeAlias = Literal["bridged", "failed_closed"]


class SourceOwnedKnowledgeCardBridgeResult(StrictModel):
    """Safe identity/provenance result for the pre-card bridge boundary.

    This is deliberately not a ``KnowledgeCard``.  The existing card schema
    remains owned by the downstream pipeline; this sidecar only confirms that
    an admission-approved source-owned candidate can cross the bridge without
    changing its identity or provenance.
    """

    status: SourceOwnedKnowledgeCardBridgeStatus
    knowledge_card_id: str = Field(min_length=0)
    source_fact_id: str = Field(min_length=0)
    provenance_anchor_span_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_bridge_result(self) -> "SourceOwnedKnowledgeCardBridgeResult":
        if self.status == "bridged":
            if not self.knowledge_card_id or not self.source_fact_id:
                raise ValueError("bridged source-owned results require identity")
            if not self.provenance_anchor_span_ids:
                raise ValueError("bridged source-owned results require provenance anchors")
            if len(self.provenance_anchor_span_ids) != len(set(self.provenance_anchor_span_ids)):
                raise ValueError("bridged source-owned provenance anchors must be unique")
        elif self.knowledge_card_id or self.source_fact_id or self.provenance_anchor_span_ids:
            raise ValueError("failed source-owned bridge results cannot carry hand-off data")
        return self

    @classmethod
    def bridged(
        cls,
        *,
        knowledge_card_id: str,
        source_fact_id: str,
        provenance_anchor_span_ids: tuple[str, ...],
    ) -> "SourceOwnedKnowledgeCardBridgeResult":
        return cls(
            status="bridged",
            knowledge_card_id=knowledge_card_id,
            source_fact_id=source_fact_id,
            provenance_anchor_span_ids=provenance_anchor_span_ids,
        )

    @classmethod
    def failed_closed(cls) -> "SourceOwnedKnowledgeCardBridgeResult":
        return cls(
            status="failed_closed",
            knowledge_card_id="",
            source_fact_id="",
            provenance_anchor_span_ids=(),
        )


def source_owned_bridge_to_knowledge_card(
    admitted_candidate: object,
) -> SourceOwnedKnowledgeCardBridgeResult:
    """Cross the source-owned candidate boundary without constructing a card.

    The admission gate is the sole authority for whether the candidate may
    cross.  The bridge intentionally returns only stable identity and anchor
    provenance; title, assertion, and numeric claims remain on the untouched
    candidate for the existing downstream consumer.
    """

    if not isinstance(admitted_candidate, SourceOwnedKnowledgeCardCandidate):
        return SourceOwnedKnowledgeCardBridgeResult.failed_closed()

    admission = source_owned_admit_card_candidate(admitted_candidate)
    if admission.status != "admitted":
        return SourceOwnedKnowledgeCardBridgeResult.failed_closed()

    return SourceOwnedKnowledgeCardBridgeResult.bridged(
        knowledge_card_id=admitted_candidate.card_id,
        source_fact_id=admitted_candidate.fact_id,
        provenance_anchor_span_ids=admitted_candidate.provenance_anchor_span_ids,
    )
