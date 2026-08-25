"""Pydantic contracts for facts, cards, coverage, and hard publication gates."""
from __future__ import annotations

from enum import StrEnum

from pydantic import Field, computed_field, model_validator

from ..evidence.schema import BBox
from ..materials.schema import StrictModel, V2_SCHEMA_VERSION


class FactType(StrEnum):
    DEFINITION = "definition"
    CLASSIFICATION = "classification"
    ENUMERATION = "enumeration"
    PROCEDURE = "procedure"
    CONDITION = "condition"
    THRESHOLD = "threshold"
    FORMULA = "formula"
    COMPARISON = "comparison"
    EXCEPTION = "exception"
    COMPOSITION = "composition"
    DIAGRAM_RELATION = "diagram_relation"
    VISUAL_REFERENCE = "visual_reference"
    EXAM_NOTE = "exam_note"
    EXAMPLE = "example"
    EXERCISE = "exercise"


class ShortCardException(StrEnum):
    """Explicitly reviewed reasons why a compact learning card is acceptable."""

    ATOMIC_FORMULA = "atomic_formula"
    DIAGRAM_SUMMARY = "diagram_summary"
    COMPACT_ENUMERATION = "compact_enumeration"
    IDENTIFIER_ONLY = "identifier_only"


class CompletenessMode(StrEnum):
    ATOMIC = "atomic"
    ALL_ITEMS = "all_items"
    ORDERED_STEPS = "ordered_steps"
    COMPLETE_FORMULA = "complete_formula"
    FULL_TABLE_ROW = "full_table_row"
    FULL_DIAGRAM_SUBGRAPH = "full_diagram_subgraph"


class NumberClaim(StrictModel):
    raw: str
    normalized: str
    unit: str | None = None
    comparator: str | None = None
    evidence_id: str


class Renderability(StrEnum):
    PUBLISHABLE = "publishable"
    MANUAL_REVIEW = "manual_review"


class ContentQualityFlag(StrEnum):
    DIAGRAM_MISSING_STRUCTURE = "diagram_missing_structure"
    DIAGRAM_UNREADABLE_OCR = "diagram_unreadable_ocr"
    KNOWN_NOISE_LEAK = "known_noise_leak"
    EMBEDDED_EXAMPLE_LEAK = "embedded_example_leak"
    VISUAL_REFERENCE_ONLY = "visual_reference_only"


class DiagramDispositionTrace(StrictModel):
    disposition_id: str = Field(pattern=r"^visual:[0-9a-f]{24}$")
    disposition: str = Field(min_length=1)
    source_diagram_review_sidecar_id: str = Field(min_length=1)
    source_diagram_review_sidecar_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_crop_path: str = Field(min_length=1)
    original_crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_crop_manifest_path: str = Field(min_length=1)
    review_crop_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_crop_path: str = Field(min_length=1)
    review_crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_crop_render_dpi: int = Field(ge=300)
    review_crop_bbox: BBox


class PresentationTransform(StrictModel):
    """A source-span-preserving transformation used only in Fact/Card rendering."""

    evidence_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_span(self) -> "PresentationTransform":
        if self.end <= self.start:
            raise ValueError("presentation transform span must not be empty")
        return self


class AtomicFact(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    fact_id: str = Field(pattern=r"^fact:[0-9a-f]{24}$")
    fact_type: FactType
    label: str = Field(min_length=2)
    assertion: str = Field(min_length=2)
    completeness_mode: CompletenessMode = CompletenessMode.ATOMIC
    primary_evidence_ids: tuple[str, ...]
    secondary_evidence_ids: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    numbers: tuple[NumberClaim, ...] = ()
    list_group_id: str | None = None
    ordered_member_ids: tuple[str, ...] = ()
    must_cover: bool = False
    confidence: float = Field(ge=0, le=1)
    renderability: Renderability = Renderability.PUBLISHABLE
    quality_flags: tuple[ContentQualityFlag, ...] = ()
    presentation_transforms: tuple[PresentationTransform, ...] = ()
    diagram_review_id: str | None = Field(default=None, pattern=r"^diagram:[0-9a-f]{24}$")
    diagram_disposition_id: str | None = Field(default=None, pattern=r"^visual:[0-9a-f]{24}$")
    diagram_disposition_trace: DiagramDispositionTrace | None = None

    @model_validator(mode="after")
    def validate_grounding(self) -> "AtomicFact":
        if not self.primary_evidence_ids:
            raise ValueError("every fact requires primary evidence")
        if len(self.primary_evidence_ids) != len(set(self.primary_evidence_ids)):
            raise ValueError("primary evidence IDs must be unique")
        if set(self.primary_evidence_ids) & set(self.secondary_evidence_ids):
            raise ValueError("evidence cannot be both primary and secondary")
        all_evidence = set(self.primary_evidence_ids) | set(self.secondary_evidence_ids)
        if any(number.evidence_id not in all_evidence for number in self.numbers):
            raise ValueError("number claims must point to fact evidence")
        if self.completeness_mode != CompletenessMode.ATOMIC and not self.ordered_member_ids:
            raise ValueError("non-atomic facts require complete ordered member IDs")
        if any(transform.evidence_id not in all_evidence for transform in self.presentation_transforms):
            raise ValueError("presentation transforms must point to fact evidence")
        return self


class CardPlan(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    raw_id: str = Field(pattern=r"^kp:[0-9a-f]{24}$")
    title: str = Field(min_length=2)
    kp_type: str = "content"
    primary_fact_ids: tuple[str, ...] = ()
    secondary_fact_ids: tuple[str, ...] = ()
    child_kp_ids: tuple[str, ...] = ()
    presentation: str
    split_reason: str
    is_overview: bool = False

    @model_validator(mode="after")
    def validate_plan(self) -> "CardPlan":
        if not self.is_overview and not self.primary_fact_ids:
            raise ValueError("content cards require primary facts")
        if self.is_overview and not (self.primary_fact_ids or self.child_kp_ids):
            raise ValueError("overview cards require facts or child cards")
        if set(self.primary_fact_ids) & set(self.secondary_fact_ids):
            raise ValueError("facts cannot be both primary and secondary")
        return self


class EvidenceRef(StrictModel):
    evidence_id: str
    page_num: int = Field(ge=1)
    bbox: BBox | None = None
    visual_only: bool = False

    @model_validator(mode="after")
    def validate_visual_ref(self) -> "EvidenceRef":
        if self.visual_only and self.bbox is None:
            raise ValueError("visual-only evidence refs require bbox")
        return self


class ContentSegment(StrictModel):
    text: str = Field(min_length=1)
    fact_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    renderer: str
    presentation_transforms: tuple[PresentationTransform, ...] = ()

    @model_validator(mode="after")
    def validate_segment(self) -> "ContentSegment":
        if not self.fact_ids or not self.evidence_ids:
            raise ValueError("content segments require fact and evidence provenance")
        if any(transform.evidence_id not in self.evidence_ids for transform in self.presentation_transforms):
            raise ValueError("segment transforms must point to segment evidence")
        return self


class GroundingStatus(StrEnum):
    VERIFIED = "verified"
    DEGRADED = "degraded"
    UNRESOLVED = "unresolved"
    MANUAL_VERIFIED = "manual_verified"


class KnowledgeCard(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    id: str
    raw_id: str = Field(pattern=r"^kp:[0-9a-f]{24}$")
    title: str
    content: str
    content_segments: tuple[ContentSegment, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    grounding_status: GroundingStatus
    renderability: Renderability = Renderability.PUBLISHABLE
    quality_flags: tuple[ContentQualityFlag, ...] = ()
    diagram_review_id: str | None = Field(default=None, pattern=r"^diagram:[0-9a-f]{24}$")
    diagram_disposition_id: str | None = Field(default=None, pattern=r"^visual:[0-9a-f]{24}$")
    diagram_disposition_trace: DiagramDispositionTrace | None = None
    key_words: tuple[str, ...] = ()
    diagram_type: str = ""
    diagram_elements: tuple[str, ...] = ()
    annotations: tuple[str, ...] = ()
    source_pages: tuple[int, ...]
    is_overview: bool = False
    kp_type: str = "content"

    @model_validator(mode="after")
    def validate_card(self) -> "KnowledgeCard":
        if not self.content_segments:
            raise ValueError("cards require deterministic content segments")
        assembled = "\n".join(segment.text for segment in self.content_segments)
        if self.content != assembled:
            raise ValueError("content must be exactly assembled from content_segments")
        ref_ids = {item.evidence_id for item in self.evidence_refs}
        segment_ids = {evidence_id for segment in self.content_segments for evidence_id in segment.evidence_ids}
        if not segment_ids <= ref_ids:
            raise ValueError("every segment evidence ID must exist in evidence_refs")
        if set(self.source_pages) != {item.page_num for item in self.evidence_refs}:
            raise ValueError("source_pages must be derived from evidence_refs")
        return self


class CoverageDisposition(StrEnum):
    PRIMARY_ASSIGNED = "primary_assigned"
    SECONDARY_REFERENCED = "secondary_referenced"
    EXCLUDED_BY_POLICY = "excluded_by_policy"
    EXCLUDED_BY_AUDIT = "excluded_by_audit"
    IGNORED_NOISE = "ignored_noise"
    UNRESOLVED = "unresolved"
    MANUAL_REVIEW = "manual_review"


class CoverageRecord(StrictModel):
    evidence_id: str
    disposition: CoverageDisposition
    coverage_owner: str | None = None
    references: tuple[str, ...] = ()
    policy_decision_id: str | None = None
    reason: str

    @model_validator(mode="after")
    def validate_ownership(self) -> "CoverageRecord":
        if self.disposition == CoverageDisposition.PRIMARY_ASSIGNED:
            if not self.coverage_owner:
                raise ValueError("primary coverage requires exactly one owner")
        elif self.coverage_owner is not None:
            raise ValueError("only primary_assigned evidence may have a coverage owner")
        if self.coverage_owner and self.coverage_owner in self.references:
            raise ValueError("coverage owner must not be duplicated in references")
        if len(self.references) != len(set(self.references)):
            raise ValueError("coverage references must be unique")
        return self


class CoverageMap(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    material_profile_id: str
    content_policy_id: str
    content_policy_revision: int = Field(ge=1)
    records: tuple[CoverageRecord, ...]

    @model_validator(mode="after")
    def validate_unique_evidence(self) -> "CoverageMap":
        ids = [record.evidence_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("each evidence unit must have exactly one final coverage disposition")
        return self


class ChapterQualityGate(StrictModel):
    must_cover_total: int = Field(ge=0)
    must_cover_primary_assigned: int = Field(ge=0)
    unresolved_evidence: int = Field(ge=0)
    fallback_content: int = Field(ge=0)
    numeric_failures: int = Field(ge=0)
    enumeration_failures: int = Field(ge=0)
    formula_failures: int = Field(ge=0)
    unresolved_visual_only: int = Field(ge=0)
    unresolved_dedup_candidates: int = Field(ge=0)
    known_noise_leaks: int = Field(default=0, ge=0)
    embedded_example_leaks: int = Field(default=0, ge=0)
    unrenderable_diagrams: int = Field(default=0, ge=0)
    schema_valid: bool
    lineage_valid: bool
    adapter_valid: bool

    # mypy 不支持 @property 之上再叠装饰器（长期未实现，见 python/mypy#1362），而
    # `@computed_field` + `@property` 正是 pydantic 官方推荐写法，pydantic 自己的文档
    # 示例里也带着同一条 ignore。逐点关码而不是整模块豁免：盲区只有这一行。
    @computed_field  # type: ignore[prop-decorator]
    @property
    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.must_cover_primary_assigned != self.must_cover_total:
            blockers.append("must_cover_incomplete")
        fields = (
            "unresolved_evidence",
            "fallback_content",
            "numeric_failures",
            "enumeration_failures",
            "formula_failures",
            "unresolved_visual_only",
            "unresolved_dedup_candidates",
            "known_noise_leaks",
            "embedded_example_leaks",
            "unrenderable_diagrams",
        )
        blockers.extend(field for field in fields if getattr(self, field) > 0)
        blockers.extend(
            field
            for field in ("schema_valid", "lineage_valid", "adapter_valid")
            if not getattr(self, field)
        )
        return tuple(blockers)

    @computed_field  # type: ignore[prop-decorator]  # 同上：mypy 的 @property 叠装饰器限制
    @property
    def publishable(self) -> bool:
        return not self.blockers
