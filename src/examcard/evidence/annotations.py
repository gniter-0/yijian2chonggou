"""Versioned, non-destructive review annotations for V2 golden evidence."""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel, V2_SCHEMA_VERSION

from .schema import SourceChannel, SourceLocator


class ConflictClassification(StrEnum):
    REAL_ERROR = "real_error"
    ACCEPTABLE_LAYOUT_EQUIVALENCE = "acceptable_layout_equivalence"
    NOISE_OR_MISMATCH = "noise_or_mismatch"
    MANUAL_REVIEW = "manual_review"


class AnnotationState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class PageReviewAudit(StrictModel):
    """Immutable proof that a reviewer inspected one rendered golden page."""

    reviewer: str = Field(min_length=1)
    reviewed_at_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    page_render_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_sheet_path: str = Field(min_length=1)
    page_sheet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis: str = Field(min_length=1)


class ConflictReviewAudit(StrictModel):
    """Immutable proof for one conflict-level human adjudication."""

    reviewer: str = Field(min_length=1)
    reviewed_at_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    page_render_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    crop_path: str = Field(min_length=1)
    crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis: str = Field(min_length=1)


class ConflictReviewDraft(StrictModel):
    conflict_id: str = Field(pattern=r"^conflict:[0-9a-f]{24}$")
    locator: SourceLocator
    native_text: str
    visual_text: str
    classification: ConflictClassification
    authoritative_channel: SourceChannel | None = None
    proposed_canonical_text: str | None = None
    proposed_action: str = Field(min_length=1)
    requires_human_confirmation: bool = False
    rationale: str = Field(min_length=1)
    audit: ConflictReviewAudit | None = None

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> "ConflictReviewDraft":
        if self.classification == ConflictClassification.MANUAL_REVIEW:
            if not self.requires_human_confirmation:
                raise ValueError("manual review conflicts require human confirmation")
            if self.authoritative_channel is None:
                raise ValueError("manual review conflicts require a proposed authoritative channel")
        elif self.requires_human_confirmation:
            raise ValueError("only manual review conflicts may require human confirmation")
        elif self.authoritative_channel is None:
            raise ValueError("non-manual conflicts require an authoritative channel")
        return self


class GoldenPageReviewDraft(StrictModel):
    page_num: int = Field(ge=1)
    page_render_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: AnnotationState = AnnotationState.DRAFT
    review_checklist: tuple[str, ...] = ()
    conflicts: tuple[ConflictReviewDraft, ...] = ()
    audit: PageReviewAudit | None = None

    @model_validator(mode="after")
    def validate_page_conflicts(self) -> "GoldenPageReviewDraft":
        ids = [item.conflict_id for item in self.conflicts]
        if len(ids) != len(set(ids)):
            raise ValueError("page conflict IDs must be unique")
        if any(item.locator.page_num != self.page_num for item in self.conflicts):
            raise ValueError("conflict locator page must match its review page")
        if self.state == AnnotationState.REVIEWED:
            if self.audit is None:
                raise ValueError("reviewed pages require page audit evidence")
            if self.audit.page_render_sha256 != self.page_render_sha256:
                raise ValueError("page audit render hash must match reviewed page")
            if any(item.audit is None for item in self.conflicts):
                raise ValueError("reviewed pages require every conflict audit")
            if any(item.requires_human_confirmation for item in self.conflicts):
                raise ValueError("reviewed pages cannot retain manual-review conflicts")
        elif self.audit is not None or any(item.audit is not None for item in self.conflicts):
            raise ValueError("draft pages cannot contain final audit evidence")
        return self


class GoldenSampleReviewDraft(StrictModel):
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pages: tuple[GoldenPageReviewDraft, ...]

    @model_validator(mode="after")
    def validate_pages(self) -> "GoldenSampleReviewDraft":
        page_numbers = [item.page_num for item in self.pages]
        if not page_numbers or len(page_numbers) != len(set(page_numbers)):
            raise ValueError("sample review pages must be non-empty and unique")
        return self


class GoldenReviewSidecar(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    sidecar_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    registry_id: str
    material_profile_id: str
    annotation_revision: int = Field(ge=1)
    state: AnnotationState = AnnotationState.DRAFT
    samples: tuple[GoldenSampleReviewDraft, ...]
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    @model_validator(mode="after")
    def validate_sidecar(self) -> "GoldenReviewSidecar":
        ids = [item.sample_id for item in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("review sidecar sample IDs must be unique")
        conflict_ids = [
            conflict.conflict_id
            for sample in self.samples
            for page in sample.pages
            for conflict in page.conflicts
        ]
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("review sidecar conflict IDs must be globally unique")
        pages = [page for sample in self.samples for page in sample.pages]
        if self.state == AnnotationState.REVIEWED:
            if self.annotation_revision < 2:
                raise ValueError("reviewed sidecars require annotation revision >= 2")
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed sidecars require reviewer and reviewed_at_utc")
            if any(page.state != AnnotationState.REVIEWED for page in pages):
                raise ValueError("reviewed sidecars require every page to be reviewed")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft sidecars cannot contain final reviewer metadata")
        return self


def load_golden_review_sidecar(path: Path) -> GoldenReviewSidecar:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return GoldenReviewSidecar.model_validate(raw)
