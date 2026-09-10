"""First-class threshold admission schemas.

This module owns only the data contracts that cross the knowledge boundary.
Queue admission, human authority, and binding validation live in
``examcard.gates.review`` so the schema layer cannot silently authorize a
candidate.
"""
from __future__ import annotations

from typing import Annotated, Final, Literal, TypeAlias

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from .admission_fixtures_constants import HASH_PATTERN, ID_PATTERN
from .admission_fixtures_typed import (
    ConstraintContract,
    DiagramContract,
    FormulaContract,
    OrderedListContract,
    SourcePageBinding,
    TableContract,
    TypedTextBase,
)
from .admission_legacy_hash import canonical_hash

PRESENTATIONS_V2: Final[tuple[str, ...]] = (
    "diagram",
    "table",
    "ordered_list",
    "constraint",
    "threshold",
    "formula",
)
PresentationV2: TypeAlias = Literal["diagram", "table", "ordered_list", "constraint", "threshold", "formula"]
DispositionV2: TypeAlias = Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]

THRESHOLD_TOKEN_VERSION: Final = "phase2r84_threshold_token_v1"
THRESHOLD_CONTRACT_VERSION: Final = "phase2r84_threshold_contract_v1"
REVIEWED_CANDIDATE_V2_VERSION: Final = "phase2r84_reviewed_typed_evidence_candidate_v2"
REVIEWED_TYPED_EVIDENCE_FIXTURE_V2_VERSION: Final = "phase2r810_reviewed_typed_evidence_fixture_v2"


class ThresholdToken(TypedTextBase):
    """One exact, human-confirmed source span in a threshold contract."""

    schema_version: Literal["phase2r84_threshold_token_v1"] = THRESHOLD_TOKEN_VERSION  # type: ignore[assignment]
    source_role: Literal["threshold_token"] = "threshold_token"
    token_order: int = Field(ge=1)


class ThresholdContract(StrictModel):
    """An ordered threshold contract whose identity includes its presentation."""

    schema_version: Literal["phase2r84_threshold_contract_v1"] = THRESHOLD_CONTRACT_VERSION
    presentation: Literal["threshold"] = "threshold"
    tokens: tuple[ThresholdToken, ...] = Field(min_length=1)
    contract_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_threshold(self) -> "ThresholdContract":
        orders = [item.token_order for item in self.tokens]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("threshold token order must be contiguous")
        element_ids = [item.element_id for item in self.tokens]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("threshold token identities must be unique")
        if self.contract_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"contract_sha256"})):
            raise ValueError("threshold contract hash mismatch")
        return self


TypedContractV2: TypeAlias = Annotated[
    DiagramContract | TableContract | OrderedListContract | ConstraintContract | ThresholdContract | FormulaContract,
    Field(discriminator="presentation"),
]


class ReviewedCandidateV2(StrictModel):
    """Reviewed typed-evidence candidate with a threshold-capable contract union."""

    schema_version: Literal["phase2r84_reviewed_typed_evidence_candidate_v2"] = REVIEWED_CANDIDATE_V2_VERSION
    candidate_id: str = Field(pattern=ID_PATTERN)
    queue_item_id: str = Field(pattern=ID_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: PresentationV2
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    pages: tuple[SourcePageBinding, ...] = Field(min_length=1)
    typed_contract: TypedContractV2
    typed_contract_sha256: str = Field(pattern=HASH_PATTERN)
    source_decision_id: str = Field(pattern=ID_PATTERN)
    reviewer: str = Field(min_length=1)
    reviewed_candidate: Literal[True] = True
    fixture_authorized: Literal[True] = True
    candidate_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_candidate(self) -> "ReviewedCandidateV2":
        if self.typed_contract_sha256 != canonical_hash(self.typed_contract.model_dump(mode="json")):
            raise ValueError("reviewed candidate typed contract hash mismatch")
        if self.typed_contract.presentation != self.presentation:
            raise ValueError("reviewed candidate presentation mismatch")
        if any(page.source_manifest_sha256 != self.source_manifest_sha256 for page in self.pages):
            raise ValueError("reviewed candidate crossed source manifest")
        if self.candidate_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"candidate_sha256"})):
            raise ValueError("reviewed candidate hash mismatch")
        return self


class ReviewedTypedEvidenceFixtureV2(StrictModel):
    """Final V2 fixture that carries threshold authority across the boundary."""

    schema_version: Literal["phase2r810_reviewed_typed_evidence_fixture_v2"] = (
        REVIEWED_TYPED_EVIDENCE_FIXTURE_V2_VERSION
    )
    r89_admission_bundle_sha256: str = Field(pattern=HASH_PATTERN)
    authorization_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    exported_r84_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    r84_fixture_sha256: str = Field(pattern=HASH_PATTERN)
    candidates: tuple[ReviewedCandidateV2, ...] = Field(min_length=1)
    reviewed_candidate_count: int = Field(ge=0)
    fixture_authorized: Literal[True] = True
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    fixture_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedTypedEvidenceFixtureV2":
        if self.reviewed_candidate_count != len(self.candidates) or not self.candidates:
            raise ValueError("R8.10 V2 reviewed fixture count mismatch")
        if len({item.candidate_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("R8.10 V2 reviewed fixture candidate identities must be unique")
        if self.fixture_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"fixture_sha256"})):
            raise ValueError("R8.10 V2 reviewed fixture hash mismatch")
        return self


__all__ = (
    "DispositionV2",
    "PRESENTATIONS_V2",
    "PresentationV2",
    "REVIEWED_CANDIDATE_V2_VERSION",
    "REVIEWED_TYPED_EVIDENCE_FIXTURE_V2_VERSION",
    "THRESHOLD_CONTRACT_VERSION",
    "THRESHOLD_TOKEN_VERSION",
    "ReviewedCandidateV2",
    "ReviewedTypedEvidenceFixtureV2",
    "ThresholdContract",
    "ThresholdToken",
    "TypedContractV2",
)
