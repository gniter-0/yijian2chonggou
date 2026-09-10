"""Minimal v2 reviewed-candidate fixture builder."""
from __future__ import annotations

from types import SimpleNamespace

from ...knowledge.contracts.admission_legacy_hash import hash_model_r8x
from ...knowledge.contracts.admission_v2 import ReviewedCandidateV2
from .decisions_v2 import HumanDecisionV2, validate_human_decision_v2
from .queue_v2 import AdmissionQueueItemV2


def build_reviewed_candidate_v2(
    queue_item: AdmissionQueueItemV2,
    candidate_id: str,
    decision: HumanDecisionV2,
) -> ReviewedCandidateV2:
    """Build a candidate only from a fully validated reviewed authority chain."""

    if decision.disposition != "reviewed_candidate":
        raise ValueError("only reviewed decisions can build a reviewed candidate")
    if decision.candidate_id != candidate_id:
        raise ValueError("decision candidate identity does not match requested candidate")
    candidate_ref = SimpleNamespace(candidate_id=candidate_id)
    validate_human_decision_v2(decision, queue_item, candidate_ref)
    if decision.typed_contract is None or decision.typed_contract_sha256 is None:
        raise ValueError("reviewed decision must carry a complete typed contract")
    payload = {
        "candidate_id": candidate_id,
        "queue_item_id": queue_item.queue_item_id,
        "subject_id": decision.subject_id,
        "record_id": decision.record_id,
        "fact_id": decision.fact_id,
        "evidence_id": decision.evidence_id,
        "presentation": decision.presentation,
        "source_manifest_sha256": decision.source_manifest_sha256,
        "anchor_ids": decision.anchor_ids,
        "pages": decision.pages,
        "typed_contract": decision.typed_contract,
        "typed_contract_sha256": decision.typed_contract_sha256,
        "source_decision_id": decision.decision_id,
        "reviewer": decision.reviewer,
        "reviewed_candidate": True,
        "fixture_authorized": True,
    }
    return hash_model_r8x(ReviewedCandidateV2, payload, "candidate_sha256")


__all__ = ("build_reviewed_candidate_v2",)
