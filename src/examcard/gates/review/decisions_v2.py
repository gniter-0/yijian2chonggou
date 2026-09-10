"""Authoritative human decisions and manifests for v2 admission."""
from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures_constants import HASH_PATTERN, ID_PATTERN
from ...knowledge.contracts.admission_fixtures_typed import SourcePageBinding
from ...knowledge.contracts.admission_legacy_hash import canonical_hash
from ...knowledge.contracts.admission_v2 import DispositionV2, PresentationV2, TypedContractV2
from ...materials.schema import StrictModel
from .contracts_v2 import validate_typed_contract_binding_v2
from .queue_v2 import QueueItemLikeV2

HUMAN_DECISION_V2_VERSION: Final = "phase2r84_human_decision_v2"
HUMAN_MANIFEST_V2_VERSION: Final = "phase2r84_human_decision_manifest_v2"
DECISION_IDENTITY_PREFIX: Final = "decision:r84:human:"
MANIFEST_IDENTITY_PREFIX: Final = "manifest:r84:human:"


class HumanDecisionV2(StrictModel):
    """Parallel r84 human decision schema that can authorize threshold evidence."""

    schema_version: Literal["phase2r84_human_decision_v2"] = HUMAN_DECISION_V2_VERSION
    decision_id: str = Field(pattern=ID_PATTERN)
    candidate_id: str = Field(pattern=ID_PATTERN)
    queue_item_id: str = Field(pattern=ID_PATTERN)
    queue_sha256: str = Field(pattern=HASH_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: PresentationV2
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    pages: tuple[SourcePageBinding, ...] = Field(min_length=1)
    disposition: DispositionV2
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    source_text_confirmed: bool = False
    anchor_confirmed: bool = False
    source_page_confirmed: bool = False
    image_hash_confirmed: bool = False
    typed_contract_confirmed: bool = False
    typed_contract: TypedContractV2 | None = None
    typed_contract_sha256: str | None = None
    recorded_at: str = Field(min_length=1)
    browser_demo: Literal[False] = False
    production_fixture_authorized: bool = False
    decision_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_decision(self) -> "HumanDecisionV2":
        if not self.decision_id.startswith(DECISION_IDENTITY_PREFIX):
            raise ValueError(f"human decision must use a fresh identity with prefix {DECISION_IDENTITY_PREFIX!r}")
        if any(page.source_manifest_sha256 != self.source_manifest_sha256 for page in self.pages):
            raise ValueError("human decision crossed source manifest")
        if self.typed_contract is None:
            if self.typed_contract_sha256 is not None:
                raise ValueError("typed contract hash exists without typed contract")
        elif self.typed_contract_sha256 != canonical_hash(self.typed_contract.model_dump(mode="json")):
            raise ValueError("typed contract hash mismatch")
        elif self.typed_contract.presentation != self.presentation:
            raise ValueError("human decision presentation does not match typed contract")
        if self.disposition == "reviewed_candidate":
            if not all(
                (
                    self.source_text_confirmed,
                    self.anchor_confirmed,
                    self.source_page_confirmed,
                    self.image_hash_confirmed,
                    self.typed_contract_confirmed,
                    self.production_fixture_authorized,
                )
            ):
                raise ValueError("reviewed candidate requires five confirmations and fixture authorization")
            if self.typed_contract is None:
                raise ValueError("reviewed candidate requires a complete typed contract")
        elif self.typed_contract is not None or self.typed_contract_confirmed or self.production_fixture_authorized:
            raise ValueError("non-reviewed decision cannot carry an authorized typed contract")
        if self.decision_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"decision_sha256"})):
            raise ValueError("human decision hash mismatch")
        return self


class HumanDecisionManifestV2(StrictModel):
    """Versioned decision manifest preserving the r84 queue hash chain."""

    schema_version: Literal["phase2r84_human_decision_manifest_v2"] = HUMAN_MANIFEST_V2_VERSION
    manifest_id: str = Field(pattern=ID_PATTERN)
    queue_sha256: str = Field(pattern=HASH_PATTERN)
    decisions: tuple[HumanDecisionV2, ...] = ()
    decision_count: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_manifest(self) -> "HumanDecisionManifestV2":
        if not self.manifest_id.startswith(MANIFEST_IDENTITY_PREFIX):
            raise ValueError(f"human decision manifest must use a fresh identity with prefix {MANIFEST_IDENTITY_PREFIX!r}")
        if self.decision_count != len(self.decisions):
            raise ValueError("human decision count mismatch")
        ids = [item.decision_id for item in self.decisions]
        candidates = [item.candidate_id for item in self.decisions]
        if len(ids) != len(set(ids)) or len(candidates) != len(set(candidates)):
            raise ValueError("human decision identities must be unique")
        if any(item.queue_sha256 != self.queue_sha256 for item in self.decisions):
            raise ValueError("human decision crossed queue hash")
        if self.manifest_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"manifest_sha256"})):
            raise ValueError("human decision manifest hash mismatch")
        return self


def validate_human_decision_v2(decision: HumanDecisionV2, queue_item: QueueItemLikeV2, candidate: object) -> None:
    """Validate the decision against the exact queue item and candidate reference."""

    if decision.queue_item_id != queue_item.queue_item_id:
        raise ValueError("human decision crossed queue item identity")
    if decision.candidate_id != queue_item.candidate_id or decision.candidate_id != getattr(candidate, "candidate_id", None):
        raise ValueError("human decision crossed candidate identity")
    if (
        decision.subject_id != queue_item.subject_id
        or decision.record_id != queue_item.record_id
        or decision.fact_id != queue_item.fact_id
        or decision.evidence_id != queue_item.evidence_id
    ):
        raise ValueError("human decision crossed source identity")
    if decision.presentation != queue_item.presentation:
        raise ValueError("human decision crossed queue presentation")
    if decision.source_manifest_sha256 != queue_item.source_manifest_sha256:
        raise ValueError("human decision crossed source manifest")
    if tuple(decision.anchor_ids) != tuple(queue_item.anchor_ids):
        raise ValueError("human decision crossed queue anchor binding")
    expected_pages = {page.page_ref_id: page for page in queue_item.pages}
    actual_pages = {page.page_ref_id: page for page in decision.pages}
    if set(actual_pages) != set(expected_pages):
        raise ValueError("human decision page set is not exact")
    for page_ref, source_page in expected_pages.items():
        page = actual_pages[page_ref]
        if page.model_dump(mode="json", exclude={"schema_version", "page_binding_sha256"}) != source_page.model_dump(
            mode="json", exclude={"schema_version", "page_binding_sha256"}
        ):
            raise ValueError("human decision page binding mismatch")
    if decision.disposition == "reviewed_candidate":
        validate_typed_contract_binding_v2(decision, queue_item)


__all__ = (
    "DECISION_IDENTITY_PREFIX",
    "HUMAN_DECISION_V2_VERSION",
    "HUMAN_MANIFEST_V2_VERSION",
    "MANIFEST_IDENTITY_PREFIX",
    "HumanDecisionManifestV2",
    "HumanDecisionV2",
    "validate_human_decision_v2",
)
