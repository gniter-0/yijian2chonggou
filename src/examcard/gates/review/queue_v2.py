"""Threshold-capable r84 admission queue models."""
from __future__ import annotations

from typing import Final, Literal, Protocol

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures_constants import HASH_PATTERN, ID_PATTERN
from ...knowledge.contracts.admission_fixtures_typed import SourcePageBinding
from ...knowledge.contracts.admission_legacy_hash import canonical_hash
from ...knowledge.contracts.admission_v2 import DispositionV2, PresentationV2
from ...materials.schema import StrictModel

ADMISSION_QUEUE_ITEM_V2_VERSION: Final = "phase2r84_admission_queue_item_v2"


class QueueItemLikeV2(Protocol):
    """Read-only shape consumed by the v2 decision and contract gates."""

    @property
    def candidate_id(self) -> str: ...

    @property
    def queue_item_id(self) -> str: ...

    @property
    def subject_id(self) -> str: ...

    @property
    def record_id(self) -> str: ...

    @property
    def fact_id(self) -> str: ...

    @property
    def evidence_id(self) -> str: ...

    @property
    def presentation(self) -> PresentationV2: ...

    @property
    def source_manifest_sha256(self) -> str: ...

    @property
    def anchor_ids(self) -> tuple[str, ...]: ...

    @property
    def pages(self) -> tuple[SourcePageBinding, ...]: ...


class AdmissionQueueItemV2(StrictModel):
    """Versioned admission queue item with threshold capability."""

    schema_version: Literal["phase2r84_admission_queue_item_v2"] = ADMISSION_QUEUE_ITEM_V2_VERSION
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
    disposition: DispositionV2
    source_decision_id: str | None = None
    decision_audit_id: str = Field(pattern=ID_PATTERN)
    typed_contract_authorized: Literal[False] = False
    reason: str = Field(min_length=3)
    entry_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_queue_item(self) -> "AdmissionQueueItemV2":
        if self.disposition == "reviewed_candidate":
            raise ValueError("reviewed candidates do not remain in admission queue")
        if any(page.source_manifest_sha256 != self.source_manifest_sha256 for page in self.pages):
            raise ValueError("admission queue item crossed source manifest")
        if self.entry_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"entry_sha256"})):
            raise ValueError("admission queue item hash mismatch")
        return self


__all__ = ("ADMISSION_QUEUE_ITEM_V2_VERSION", "AdmissionQueueItemV2", "QueueItemLikeV2")
