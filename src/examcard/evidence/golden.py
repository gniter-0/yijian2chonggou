"""Golden-sample registry contract used before any Phase 1 implementation."""
from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

import fitz
import yaml
from pydantic import Field, model_validator

from examcard.materials.schema import SemanticRole, StrictModel, V2_SCHEMA_VERSION
from examcard.providers.base import sanitize_error_text

from .schema import EvidenceKind, SourceQuality


class AnnotationStatus(StrEnum):
    SELECTED = "selected"
    SEEDED = "seeded"
    REVIEWED = "reviewed"


class GoldenAssertion(StrictModel):
    assertion_id: str
    description: str
    expected_evidence_kinds: tuple[EvidenceKind, ...] = ()
    expected_texts: tuple[str, ...] = ()
    expected_conflicts: tuple[tuple[str, str], ...] = ()
    must_cover: bool = False
    expected_matches: tuple["ExpectedEvidenceMatch", ...] = ()


class ExpectedEvidenceMatch(StrictModel):
    """A text anchor together with the properties that make it meaningful."""

    text: str = Field(min_length=1)
    kind: EvidenceKind | None = None
    semantic_role: SemanticRole | None = None
    source_quality: SourceQuality | None = None
    must_cover: bool | None = None


class GoldenSample(StrictModel):
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    pdf_relative_path: str
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_pages: tuple[int, ...]
    focus: tuple[str, ...]
    annotation_status: AnnotationStatus
    assertions: tuple[GoldenAssertion, ...] = ()
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    @model_validator(mode="after")
    def validate_sample(self) -> "GoldenSample":
        if not self.selected_pages or min(self.selected_pages) < 1:
            raise ValueError("golden samples require positive selected pages")
        if len(self.selected_pages) != len(set(self.selected_pages)):
            raise ValueError("selected_pages must be unique")
        if self.annotation_status == AnnotationStatus.REVIEWED and (not self.reviewer or not self.reviewed_at_utc):
            raise ValueError("reviewed samples require reviewer and reviewed_at_utc")
        if self.annotation_status != AnnotationStatus.REVIEWED and (
            self.reviewer is not None or self.reviewed_at_utc is not None
        ):
            raise ValueError("only reviewed samples may contain reviewer metadata")
        if self.annotation_status != AnnotationStatus.SELECTED and not self.assertions:
            raise ValueError("seeded/reviewed samples require assertions")
        assertion_ids = [item.assertion_id for item in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("golden assertion IDs must be unique within a sample")
        return self


class GoldenSampleRegistry(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    registry_id: str
    material_profile_id: str
    samples: tuple[GoldenSample, ...]

    @model_validator(mode="after")
    def validate_registry(self) -> "GoldenSampleRegistry":
        ids = [item.sample_id for item in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("golden sample IDs must be unique")
        if len(self.samples) < 5:
            raise ValueError("Phase 0 requires at least five representative documents")
        page_count = sum(len(sample.selected_pages) for sample in self.samples)
        if not 20 <= page_count <= 30:
            raise ValueError("Phase 0 requires 20-30 selected golden pages")
        return self

    @property
    def ready_for_phase1(self) -> bool:
        return all(sample.annotation_status == AnnotationStatus.REVIEWED for sample in self.samples)


def load_golden_registry(path: Path) -> GoldenSampleRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return GoldenSampleRegistry.model_validate(raw)


def verify_golden_sources(registry: GoldenSampleRegistry, root: Path) -> list[str]:
    errors: list[str] = []
    resolved_root = root.resolve()
    for sample in registry.samples:
        path = (resolved_root / sample.pdf_relative_path).resolve()
        if path != resolved_root and resolved_root not in path.parents:
            errors.append(f"{sample.sample_id}: path escapes workspace")
            continue
        if not path.is_file():
            errors.append(f"{sample.sample_id}: missing source {sample.pdf_relative_path}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != sample.pdf_sha256:
            errors.append(f"{sample.sample_id}: source hash mismatch")
            continue
        try:
            with fitz.open(path) as document:
                invalid_pages = [page for page in sample.selected_pages if page > document.page_count]
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{sample.sample_id}: cannot open source PDF: {sanitize_error_text(str(exc))}")
            continue
        if invalid_pages:
            errors.append(
                f"{sample.sample_id}: selected pages exceed source page count {document.page_count}: "
                f"{invalid_pages}"
            )
    return errors
