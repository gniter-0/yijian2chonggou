"""Auditable disposition reviews for diagram sidecar entries that remain unstructured."""
from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, field_validator, model_validator

from ...evidence.schema import BBox
from ...materials.schema import StrictModel, V2_SCHEMA_VERSION


class DiagramDisposition(StrEnum):
    STRUCTURAL_DIAGRAM = "structural_diagram"
    PHOTO_EXAMPLE = "photo_example"
    CONTEXTUAL_ILLUSTRATION = "contextual_illustration"
    NON_KP_VISUAL = "non_kp_visual"
    UNRESOLVED = "unresolved"


_RELEASED_DISPOSITIONS = frozenset(
    {
        DiagramDisposition.PHOTO_EXAMPLE,
        DiagramDisposition.CONTEXTUAL_ILLUSTRATION,
    }
)


def make_diagram_disposition_id(*, sample_id: str, figure_evidence_id: str) -> str:
    digest = hashlib.sha256(f"{sample_id}:{figure_evidence_id}".encode("utf-8")).hexdigest()[:24]
    return f"visual:{digest}"


class DiagramDispositionAudit(StrictModel):
    reviewer: str = Field(min_length=1)
    reviewed_at_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    crop_path: str = Field(min_length=1)
    crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis: str = Field(min_length=1)


class DiagramReviewCropManifest(StrictModel):
    manifest_path: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HighResReviewAsset(StrictModel):
    review_crop_path: str = Field(min_length=1)
    review_crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_dpi: int = Field(ge=300)
    margin_pt: float = Field(ge=0)
    figure_bbox: BBox


class DiagramDispositionEntry(StrictModel):
    disposition_id: str = Field(pattern=r"^visual:[0-9a-f]{24}$")
    review_id: str = Field(pattern=r"^diagram:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_num: int = Field(ge=1)
    figure_evidence_id: str
    figure_crop_path: str = Field(min_length=1)
    figure_crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: DiagramDisposition
    title: str | None = None
    audit: DiagramDispositionAudit
    review_asset: HighResReviewAsset | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("diagram disposition title must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_disposition_shape(self) -> "DiagramDispositionEntry":
        if self.disposition_id != make_diagram_disposition_id(
            sample_id=self.sample_id,
            figure_evidence_id=self.figure_evidence_id,
        ):
            raise ValueError("diagram disposition ID must match the source figure binding")
        if self.audit.crop_path != self.figure_crop_path or self.audit.crop_sha256 != self.figure_crop_sha256:
            raise ValueError("diagram disposition audit must bind the source figure crop")
        if self.disposition in _RELEASED_DISPOSITIONS and not self.title:
            raise ValueError("released visual dispositions require a human-reviewed display title")
        return self


class DiagramDispositionSidecar(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    sidecar_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    registry_id: str
    material_profile_id: str
    annotation_revision: int = Field(ge=1)
    source_diagram_review_sidecar_id: str
    source_diagram_review_sidecar_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_crop_manifest: DiagramReviewCropManifest | None = None
    entries: tuple[DiagramDispositionEntry, ...]

    @model_validator(mode="after")
    def validate_entries(self) -> "DiagramDispositionSidecar":
        disposition_ids = [entry.disposition_id for entry in self.entries]
        review_ids = [entry.review_id for entry in self.entries]
        bindings = [(entry.sample_id, entry.figure_evidence_id) for entry in self.entries]
        if len(disposition_ids) != len(set(disposition_ids)):
            raise ValueError("diagram disposition IDs must be unique")
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("diagram disposition review IDs must be unique")
        if len(bindings) != len(set(bindings)):
            raise ValueError("each source figure may have only one diagram disposition")
        has_review_assets = [entry.review_asset is not None for entry in self.entries]
        if self.review_crop_manifest is None and any(has_review_assets):
            raise ValueError("high-resolution review assets require a review crop manifest")
        if self.review_crop_manifest is not None and not all(has_review_assets):
            raise ValueError("review crop manifest requires every disposition to bind a high-resolution review asset")
        return self


def load_diagram_disposition_sidecar(path: Path) -> DiagramDispositionSidecar:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return DiagramDispositionSidecar.model_validate(raw)


def is_released_disposition(disposition: DiagramDisposition) -> bool:
    return disposition in _RELEASED_DISPOSITIONS
