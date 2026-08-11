"""reviewed fixture 族（桶内专有层）：r84 期 fixture + 构建器。

跨桶消费的具体模型（ReviewedCandidate、r810 ReviewedTypedEvidenceFixture、
ReviewedStep3Fixture、r2242 合并三型等）已按总指挥 A 案下沉
knowledge/contracts/admission_fixtures，本模块向下 import 并原样转发；
桶内保留 r84 期级 ReviewedFixture（cards_created/step3/step4 恒 False 的
禁授权不变量）与从决策构建 reviewed 产物的定式。
"""
from __future__ import annotations

from typing import Literal, Sequence

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures import (
    HASH_PATTERN,
    ReviewedCandidate,
    ReviewedTypedEvidenceFixture,
)
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...materials.schema import StrictModel
from .decisions import HumanDecision

__all__ = (
    "REVIEWED_FIXTURE_VERSION",
    "ReviewedCandidate",
    "ReviewedTypedEvidenceFixture",
    "ReviewedFixture",
    "build_reviewed_candidate",
    "build_reviewed_fixture",
)

REVIEWED_FIXTURE_VERSION = "phase2r84_reviewed_typed_evidence_fixture_v1"


class ReviewedFixture(StrictModel):
    schema_version: Literal["phase2r84_reviewed_typed_evidence_fixture_v1"] = REVIEWED_FIXTURE_VERSION
    phase: Literal["2R.8.4"] = "2R.8.4"
    source_phase: Literal["2R.8.3"] = "2R.8.3"
    queue_sha256: str = Field(pattern=HASH_PATTERN)
    candidates: tuple[ReviewedCandidate, ...] = ()
    reviewed_candidate_count: int = Field(ge=0)
    fixture_authorized: bool = False
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    fixture_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedFixture":
        if self.reviewed_candidate_count != len(self.candidates):
            raise ValueError("reviewed candidate count mismatch")
        if self.fixture_authorized != bool(self.candidates):
            raise ValueError("fixture authorization mismatch")
        if len({item.candidate_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("reviewed candidate identities must be unique")
        if self.fixture_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"fixture_sha256"})):
            raise ValueError("reviewed fixture hash mismatch")
        return self


def build_reviewed_candidate(queue_item: object, candidate_id: str, decision: HumanDecision) -> ReviewedCandidate:
    """r84 `_reviewed_candidate` 定式：只有已通过严格摄取校验的 reviewed 决策可以调用。"""
    if decision.disposition != "reviewed_candidate":
        raise ValueError("only reviewed decisions can build a reviewed candidate")
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
    return hash_model_r8x(ReviewedCandidate, payload, "candidate_sha256")  # type: ignore[return-value]


def build_reviewed_fixture(queue_sha256: str, candidates: Sequence[ReviewedCandidate]) -> ReviewedFixture:
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
    return hash_model_r8x(ReviewedFixture, payload, "fixture_sha256")  # type: ignore[return-value]
