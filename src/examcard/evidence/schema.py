"""Pydantic contracts for the immutable V2 evidence layer."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_serializer, model_validator

from examcard.materials.schema import PolicyDecision, SemanticRole, StrictModel, V2_SCHEMA_VERSION


class EvidenceKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_GROUP = "list_group"
    LIST_ITEM = "list_item"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    FORMULA = "formula"
    FIGURE = "figure"
    DIAGRAM_NODE = "diagram_node"
    DIAGRAM_EDGE = "diagram_edge"
    CAPTION = "caption"
    EXAMPLE = "example"
    EXERCISE = "exercise"
    EXAM_TAG = "exam_tag"
    HEADER_FOOTER = "header_footer"
    WATERMARK = "watermark"
    PAGE_NUMBER = "page_number"
    DECORATIVE = "decorative"


class SourceChannel(StrEnum):
    NATIVE_TEXT = "native_text"
    VISUAL_OCR = "visual_ocr"
    LAYOUT = "layout"
    VISION_STRUCTURE = "vision_structure"
    MANUAL = "manual"


class SourceQuality(StrEnum):
    VERIFIED_AGREEMENT = "verified_agreement"
    NATIVE_ONLY = "native_only"
    VISUAL_ONLY = "visual_only"
    CONFLICT = "conflict"
    UNRESOLVED = "unresolved"


class BBox(StrictModel):
    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(gt=0)
    y1: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_extent(self) -> "BBox":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("bbox must have positive width and height")
        return self


class SourceLocator(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_num: int = Field(ge=1)
    bbox: BBox | None = None
    native_block_index: int | None = Field(default=None, ge=0)
    native_line_index: int | None = Field(default=None, ge=0)
    native_span_index: int | None = Field(default=None, ge=0)
    region_id: str | None = None


class EvidenceProvenance(StrictModel):
    crop_path: str | None = None
    source_channels: frozenset[SourceChannel]
    engine_versions: dict[str, str] = Field(default_factory=dict)
    config_hash: str
    generated_at_utc: str

    @field_serializer("source_channels", when_used="json")
    def _source_channels_json(self, value: frozenset[SourceChannel]) -> list[str]:
        # JSON order must not follow PYTHONHASHSEED: manifests and Step 2A ids hash this JSON.
        return sorted(channel.value for channel in value)


class SourceConflict(StrictModel):
    conflict_id: str
    locator: SourceLocator
    native_text: str | None = None
    visual_text: str | None = None
    status: SourceQuality = SourceQuality.CONFLICT
    resolution: str | None = None
    reviewer: str | None = None


class EvidenceUnit(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    evidence_id: str = Field(pattern=r"^ev:[0-9a-f]{24}$")
    fingerprint: str = Field(pattern=r"^evfp:[0-9a-f]{24}$")
    revision_id: str = Field(pattern=r"^evrev:[0-9a-f]{24}$")
    document_id: str = Field(pattern=r"^doc:[0-9a-f]{24}$")
    kind: EvidenceKind
    locator: SourceLocator
    raw_text: str = ""
    canonical_text: str = ""
    semantic_role: SemanticRole
    source_quality: SourceQuality
    confidence: float = Field(ge=0, le=1)
    parent_id: str | None = None
    order: int = Field(ge=0)
    risk_tags: frozenset[str] = frozenset()
    must_cover_by_source: bool = False
    policy_decision: PolicyDecision | None = None
    provenance: EvidenceProvenance
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("risk_tags", when_used="json")
    def _risk_tags_json(self, value: frozenset[str]) -> list[str]:
        # JSON order must not follow PYTHONHASHSEED: manifests and Step 2A ids hash this JSON.
        return sorted(value)

    @model_validator(mode="after")
    def validate_grounding_shape(self) -> "EvidenceUnit":
        visual_channels = {
            SourceChannel.VISUAL_OCR,
            SourceChannel.VISION_STRUCTURE,
        }
        if self.source_quality == SourceQuality.VISUAL_ONLY:
            if self.locator.bbox is None:
                raise ValueError("visual-only evidence requires bbox")
            if not (self.provenance.source_channels & visual_channels):
                raise ValueError("visual-only evidence requires a visual source channel")
        if self.source_quality == SourceQuality.VERIFIED_AGREEMENT:
            channels = self.provenance.source_channels
            if SourceChannel.NATIVE_TEXT not in channels or not (channels & visual_channels):
                raise ValueError("verified agreement requires native and visual channels")
        if self.must_cover_by_source and self.source_quality == SourceQuality.UNRESOLVED:
            if self.policy_decision is not None:
                raise ValueError("unresolved must-cover evidence cannot receive a final policy decision")
        if self.policy_decision is not None and self.policy_decision.evidence_id != self.evidence_id:
            raise ValueError("policy decision must reference this evidence unit")
        return self


class PageQuality(StrictModel):
    native_text_coverage: float = Field(ge=0, le=1)
    ocr_alignment_rate: float = Field(ge=0, le=1)
    unresolved_regions: int = Field(ge=0)
    conflict_count: int = Field(ge=0)


class PageEvidence(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    page_id: str
    page_num: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: int = 0
    evidence_units: tuple[EvidenceUnit, ...]
    source_conflicts: tuple[SourceConflict, ...] = ()
    quality: PageQuality

    @model_validator(mode="after")
    def validate_page_membership(self) -> "PageEvidence":
        if any(item.locator.page_num != self.page_num for item in self.evidence_units):
            raise ValueError("all evidence units must belong to this page")
        ids = [item.evidence_id for item in self.evidence_units]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence_id must be unique within a page")
        return self


class SourceDocument(StrictModel):
    relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0)
    page_count: int = Field(gt=0)


class EvidenceLineage(StrictModel):
    run_id: str
    stage: str = "step1_evidence_v2"
    schema_version: str = V2_SCHEMA_VERSION
    generated_at_utc: str
    profile_id: str
    profile_revision: int = Field(ge=1)
    config_hash: str


class DocumentEvidenceManifest(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    manifest_id: str
    document_id: str = Field(pattern=r"^doc:[0-9a-f]{24}$")
    material_profile_id: str
    material_profile_revision: int = Field(ge=1)
    source: SourceDocument
    pages: tuple[PageEvidence, ...]
    lineage: EvidenceLineage

    @model_validator(mode="after")
    def validate_document(self) -> "DocumentEvidenceManifest":
        if self.source.page_count != len(self.pages):
            raise ValueError("manifest must include exactly one PageEvidence per source page")
        if [page.page_num for page in self.pages] != list(range(1, self.source.page_count + 1)):
            raise ValueError("manifest pages must be contiguous and ordered from 1")
        for page in self.pages:
            if any(item.document_id != self.document_id for item in page.evidence_units):
                raise ValueError("evidence document_id must match manifest document_id")
            if any(item.locator.document_sha256 != self.source.sha256 for item in page.evidence_units):
                raise ValueError("evidence locator hash must match source hash")
        return self
