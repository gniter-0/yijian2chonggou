"""Admission gate for source-owned card-pipeline candidates."""
from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import Field

from ...materials.schema import StrictModel
from .claims import GroundedNumericClaim
from .source_owned_card_adapter import SourceOwnedKnowledgeCardCandidate


SourceOwnedCardAdmissionStatus: TypeAlias = Literal["admitted", "rejected"]
SourceOwnedCardAdmissionRejectionReason: TypeAlias = Literal[
    "failed_closed_candidate",
    "missing_provenance",
    "missing_identity",
    "unsupported_content",
]


class SourceOwnedCardAdmissionResult(StrictModel):
    """Typed, non-mutating result of the source-owned candidate gate."""

    status: SourceOwnedCardAdmissionStatus
    candidate_id: str = Field(min_length=0)
    rejection_reason: SourceOwnedCardAdmissionRejectionReason | None = None

    @classmethod
    def admitted(cls, candidate_id: str) -> "SourceOwnedCardAdmissionResult":
        return cls(status="admitted", candidate_id=candidate_id)

    @classmethod
    def rejected(
        cls,
        *,
        candidate_id: str = "",
        reason: SourceOwnedCardAdmissionRejectionReason,
    ) -> "SourceOwnedCardAdmissionResult":
        return cls(status="rejected", candidate_id=candidate_id, rejection_reason=reason)


def _candidate_id(candidate: object) -> str:
    if not isinstance(candidate, SourceOwnedKnowledgeCardCandidate):
        return ""
    return candidate.card_id if isinstance(candidate.card_id, str) else ""


def _has_unsupported_fields(candidate: SourceOwnedKnowledgeCardCandidate) -> bool:
    field_names = set(type(candidate).model_fields)
    instance_fields = set(candidate.__dict__)
    model_extra = candidate.model_extra
    return bool(instance_fields - field_names) or bool(model_extra)


def _has_valid_provenance(candidate: SourceOwnedKnowledgeCardCandidate) -> bool:
    anchor_span_ids = candidate.provenance_anchor_span_ids
    if not isinstance(anchor_span_ids, tuple) or not anchor_span_ids:
        return False
    if len(anchor_span_ids) != len(set(anchor_span_ids)):
        return False
    if any(not isinstance(anchor_span_id, str) or not anchor_span_id for anchor_span_id in anchor_span_ids):
        return False

    claims = candidate.grounded_numeric_claims
    if not isinstance(claims, tuple):
        return False
    for claim in claims:
        if not isinstance(claim, GroundedNumericClaim):
            return False
        if not claim.anchor_span_id or not claim.evidence_id:
            return False
        if claim.anchor_span_id not in anchor_span_ids:
            return False
    return True


def source_owned_admit_card_candidate(
    candidate: object,
) -> SourceOwnedCardAdmissionResult:
    """Admit a ready source-owned candidate without changing its identity/content."""

    if not isinstance(candidate, SourceOwnedKnowledgeCardCandidate):
        return SourceOwnedCardAdmissionResult.rejected(reason="unsupported_content")

    candidate_id = _candidate_id(candidate)
    if candidate.generation_status != "ready":
        return SourceOwnedCardAdmissionResult.rejected(
            candidate_id=candidate_id,
            reason="failed_closed_candidate",
        )
    if not candidate_id or not isinstance(candidate.fact_id, str) or not candidate.fact_id:
        return SourceOwnedCardAdmissionResult.rejected(
            candidate_id=candidate_id,
            reason="missing_identity",
        )
    if _has_unsupported_fields(candidate):
        return SourceOwnedCardAdmissionResult.rejected(
            candidate_id=candidate_id,
            reason="unsupported_content",
        )
    if not _has_valid_provenance(candidate):
        return SourceOwnedCardAdmissionResult.rejected(
            candidate_id=candidate_id,
            reason="missing_provenance",
        )
    return SourceOwnedCardAdmissionResult.admitted(candidate_id)
