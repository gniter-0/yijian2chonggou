"""cm35 切分产物：草稿 / 发布 / 人审 step3 契约家族（层 3）。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from ...materials.schema import StrictModel
from .admission_legacy_hash import canonical_hash_r10x
from .admission_fixtures_constants import (CONTRACT_ID, DECISION_VERSION, DRAFT_CARD_VERSION, DRAFT_VERSION, HASH, ID, R103_FIXTURE_VERSION, R10_FIXTURE_VERSION, RELEASE_VERSION, REVIEWED_CARD_VERSION)
from .admission_fixtures_step3card import Step3CardContract


class FakeArtifact(StrictModel):
    schema_version: Literal["phase2r9_step3_deterministic_draft_v1"] = DRAFT_VERSION
    contract_id: str = Field(pattern=CONTRACT_ID)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    rendered_element_ids: tuple[str, ...] = Field(min_length=1)
    rendered_source_texts: tuple[str, ...] = Field(min_length=1)
    relation_texts: tuple[str, ...] = ()
    extra_texts: tuple[str, ...] = ()
    materialized: Literal[False] = False
    real_image: Literal[False] = False
    artifact_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_artifact(self) -> "FakeArtifact":
        if self.relation_texts or self.extra_texts:
            raise ValueError("deterministic draft contains non-source text")
        if len(self.rendered_element_ids) != len(set(self.rendered_element_ids)):
            raise ValueError("deterministic draft element IDs must be unique")
        if self.artifact_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "artifact_sha256"})):
            raise ValueError("deterministic draft hash mismatch")
        return self


class DraftBundle(StrictModel):
    schema_version: Literal["phase2r9_step3_deterministic_draft_v1"] = DRAFT_VERSION
    source_fixture_sha256: str = Field(pattern=HASH)
    artifacts: tuple[FakeArtifact, ...] = Field(min_length=1)
    artifact_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_bundle(self) -> "DraftBundle":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("draft artifact count mismatch")
        return self


class DeterministicDraftCard(StrictModel):
    schema_version: Literal["phase2r98_deterministic_draft_card_v1"] = DRAFT_CARD_VERSION
    contract: Step3CardContract
    artifact: FakeArtifact
    draft_state: Literal["draft_manual_review"] = "draft_manual_review"
    reviewed_fixture_created: Literal[False] = False

    @model_validator(mode="after")
    def validate_draft_card(self) -> "DeterministicDraftCard":
        if self.artifact.contract_id != self.contract.contract_id:
            raise ValueError("deterministic artifact was grouped with another Card")
        if self.artifact.presentation != self.contract.presentation:
            raise ValueError("deterministic artifact presentation mismatch")
        if self.artifact.rendered_element_ids != tuple(item.element_id for item in self.contract.display_elements):
            raise ValueError("deterministic artifact element IDs are not exactly once")
        if self.artifact.rendered_source_texts != tuple(item.text for item in self.contract.display_elements):
            raise ValueError("deterministic artifact source text changed or truncated")
        return self


class DeterministicReleaseBundle(StrictModel):
    schema_version: Literal["phase2r98_deterministic_release_bundle_v1"] = RELEASE_VERSION
    source_fixture_sha256: str = Field(pattern=HASH)
    source_fixture_path: str = Field(min_length=1)
    r9_draft_sha256: str = Field(pattern=HASH)
    cards: tuple[DeterministicDraftCard, ...] = Field(min_length=1)
    card_count: int = Field(ge=1)
    draft_authority: Literal["local_deterministic_compiler"] = "local_deterministic_compiler"
    reviewed_fixture_created: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    release_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_bundle(self) -> "DeterministicReleaseBundle":
        if self.card_count != len(self.cards):
            raise ValueError("deterministic release Card count mismatch")
        ids = tuple(item.contract.contract_id for item in self.cards)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Cards are fail-closed")
        candidates = tuple(item.contract.source_candidate_id for item in self.cards)
        if len(candidates) != len(set(candidates)):
            raise ValueError("cross-sample grouping is fail-closed")
        if any(item.contract.source_fixture_sha256 != self.source_fixture_sha256 for item in self.cards):
            raise ValueError("source fixture provenance mismatch")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "release_sha256"}))
        if self.release_sha256 != expected:
            raise ValueError("deterministic release hash mismatch")
        return self


class Step3HumanDecision(StrictModel):
    schema_version: Literal["phase2r10_step3_production_human_decision_v1"] = DECISION_VERSION
    decision_id: str = Field(pattern=r"^step3-human-decision:[0-9a-f]{24}$")
    queue_item_id: str = Field(pattern=r"^step3-review-queue:[0-9a-f]{24}$")
    queue_sha256: str = Field(pattern=HASH)
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=ID)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    source_texts: tuple[str, ...] = Field(min_length=1)
    typed_contract_sha256: str = Field(pattern=HASH)
    provenance_sha256: str = Field(pattern=HASH)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    geometry_sha256: str = Field(pattern=HASH)
    disposition: Literal["reviewed_candidate", "manual_review", "blocked"]
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    source_text_confirmed: bool = False
    fact_card_confirmed: bool = False
    evidence_anchor_confirmed: bool = False
    typed_contract_confirmed: bool = False
    provenance_confirmed: bool = False
    presentation_confirmed: bool = False
    geometry_confirmed: bool = False
    explicit_human_authorization: bool = False
    browser_demo: Literal[False] = False
    decision_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_decision(self) -> "Step3HumanDecision":
        if self.disposition == "reviewed_candidate" and not all((
            self.source_text_confirmed, self.fact_card_confirmed, self.evidence_anchor_confirmed,
            self.typed_contract_confirmed, self.provenance_confirmed, self.presentation_confirmed,
            self.geometry_confirmed, self.explicit_human_authorization,
        )):
            raise ValueError("reviewed Step 3 decision requires all confirmations and explicit human authorization")
        if self.disposition != "reviewed_candidate" and self.explicit_human_authorization:
            raise ValueError("non-reviewed decision cannot authorize a fixture")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "decision_sha256"}))
        if self.decision_sha256 != expected:
            raise ValueError("Step 3 decision hash mismatch")
        return self


class ReviewedStep3Card(StrictModel):
    schema_version: Literal["phase2r10_reviewed_step3_card_v1"] = REVIEWED_CARD_VERSION
    queue_item_id: str = Field(pattern=r"^step3-review-queue:[0-9a-f]{24}$")
    contract: Step3CardContract
    artifact: DeterministicDraftCard
    decision: Step3HumanDecision
    reviewed_state: Literal["reviewed_step3"] = "reviewed_step3"
    reviewed_card_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_reviewed_card(self) -> "ReviewedStep3Card":
        if self.decision.disposition != "reviewed_candidate":
            raise ValueError("only explicitly reviewed decisions may enter a reviewed Step 3 fixture")
        if self.decision.contract_id != self.contract.contract_id or self.artifact.contract.contract_id != self.contract.contract_id:
            raise ValueError("reviewed fixture Card grouping mismatch")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "reviewed_card_sha256"}))
        if self.reviewed_card_sha256 != expected:
            raise ValueError("reviewed Step 3 Card hash mismatch")
        return self


class ReviewedStep3Fixture(StrictModel):
    schema_version: Literal["phase2r10_reviewed_step3_fixture_v1"] = R10_FIXTURE_VERSION
    source_fixture_sha256: str = Field(pattern=HASH)
    deterministic_release_sha256: str = Field(pattern=HASH)
    decision_manifest_sha256: str = Field(pattern=HASH)
    cards: tuple[ReviewedStep3Card, ...] = ()
    reviewed_card_count: int = Field(ge=0)
    fixture_authorized: bool
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    fixture_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedStep3Fixture":
        if self.reviewed_card_count != len(self.cards):
            raise ValueError("reviewed Step 3 fixture count mismatch")
        if self.fixture_authorized != bool(self.cards):
            raise ValueError("empty Step 3 fixture cannot be authorized")
        if len({item.contract.contract_id for item in self.cards}) != self.reviewed_card_count:
            raise ValueError("reviewed Step 3 fixture duplicate Card")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "fixture_sha256"}))
        if self.fixture_sha256 != expected:
            raise ValueError("reviewed Step 3 fixture hash mismatch")
        return self


class ReviewedStep3FixtureR103(StrictModel):
    schema_version: Literal["phase2r103_reviewed_step3_fixture_v1"] = R103_FIXTURE_VERSION
    source_raw_draft_sha256: str = Field(pattern=HASH)
    source_queue_sha256: str = Field(pattern=HASH)
    source_typed_evidence_sha256: str = Field(pattern=HASH)
    reviewed_queue_item_ids: tuple[str, ...] = ()
    reviewed_card_count: Literal[0] = 0
    fixture_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    fixture_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedStep3FixtureR103":
        if self.reviewed_queue_item_ids:
            raise ValueError("R10.3 cannot create a reviewed fixture without an explicit human production manifest")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "fixture_sha256"}))
        if self.fixture_sha256 != expected:
            raise ValueError("R10.3 reviewed fixture hash mismatch")
        return self
