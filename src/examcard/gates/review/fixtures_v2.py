"""V2 reviewed-candidate and gate-local fixture builders."""
from __future__ import annotations

from typing import Final, Literal, Sequence
from types import SimpleNamespace

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures_constants import HASH_PATTERN
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...knowledge.contracts.admission_v2 import ReviewedCandidateV2
from ...materials.schema import StrictModel
from .decisions_v2 import HumanDecisionV2, validate_human_decision_v2
from .queue_v2 import AdmissionQueueItemV2

REVIEWED_FIXTURE_V2_VERSION: Final = "phase2r84_reviewed_typed_evidence_fixture_v2"


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


class ReviewedFixtureV2(StrictModel):
    """Gate-local reviewed fixture with the V2 candidate family."""

    schema_version: Literal["phase2r84_reviewed_typed_evidence_fixture_v2"] = REVIEWED_FIXTURE_V2_VERSION
    phase: Literal["2R.8.4"] = "2R.8.4"
    source_phase: Literal["2R.8.3"] = "2R.8.3"
    queue_sha256: str = Field(pattern=HASH_PATTERN)
    candidates: tuple[ReviewedCandidateV2, ...] = ()
    reviewed_candidate_count: int = Field(ge=0)
    fixture_authorized: bool = False
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    fixture_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedFixtureV2":
        if self.reviewed_candidate_count != len(self.candidates):
            raise ValueError("reviewed candidate count mismatch")
        if self.fixture_authorized != bool(self.candidates):
            raise ValueError("fixture authorization mismatch")
        if len({item.candidate_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("reviewed fixture V2 candidate identities must be unique")
        if self.fixture_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"fixture_sha256"})):
            raise ValueError("reviewed fixture V2 hash mismatch")
        return self


def build_reviewed_fixture_v2(queue_sha256: str, candidates: Sequence[ReviewedCandidateV2]) -> ReviewedFixtureV2:
    payload = {
        "source_phase": "2R.8.3",
        "queue_sha256": queue_sha256,
        "candidates": tuple(candidates),
        "reviewed_candidate_count": len(candidates),
        "fixture_authorized": bool(candidates),
        "cards_created": False,
        "step3_authorized": False,
        "step4_authorized": False,
    }
    return hash_model_r8x(ReviewedFixtureV2, payload, "fixture_sha256")


__all__ = ("REVIEWED_FIXTURE_V2_VERSION", "ReviewedFixtureV2", "build_reviewed_candidate_v2", "build_reviewed_fixture_v2")
