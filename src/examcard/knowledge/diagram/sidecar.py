"""Non-destructive, auditable manual structure reviews for V2 diagram facts."""
from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from ...evidence.schema import BBox
from ...materials.schema import StrictModel, V2_SCHEMA_VERSION
from .disposition import DiagramReviewCropManifest, HighResReviewAsset


class DiagramReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class DiagramSemanticContractVersion(StrEnum):
    """Schema level for reviewed edge semantics.

    Existing manually reviewed sidecars predate per-edge evidence metadata and
    remain readable as ``legacy_v1``. New semantic reviews must opt in to the
    stricter ``semantic_v2`` contract instead of silently claiming provenance.
    """

    LEGACY_V1 = "legacy_v1"
    SEMANTIC_V2 = "semantic_v2"


class DiagramType(StrEnum):
    HIERARCHY = "hierarchy"
    CROSS_SECTION = "cross_section"
    SCALE = "scale"
    LOAD_DISTRIBUTION = "load_distribution"
    STRUCTURAL = "structural"
    PROCESS = "process"
    FLOW = "flow"
    COMPARISON = "comparison"
    TABLE = "table"
    LAYOUT = "layout"


class DiagramCandidate(StrictModel):
    """Untrusted OCR candidate retained to make human review faster."""

    evidence_id: str
    text: str
    bbox: BBox | None = None


class DiagramNode(StrictModel):
    node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "DiagramNode":
        if not self.evidence_ids or len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("reviewed diagram nodes require unique evidence IDs")
        return self


class DiagramEdge(StrictModel):
    source_node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    target_node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    relation: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    basis: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_nodes_differ(self) -> "DiagramEdge":
        if self.source_node_id == self.target_node_id:
            raise ValueError("diagram edges cannot self-reference")
        metadata_fields = (bool(self.evidence_ids), self.basis is not None, self.confidence is not None)
        if any(metadata_fields) and not all(metadata_fields):
            raise ValueError("diagram edge evidence_ids, basis and confidence must be supplied together")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("diagram edge evidence IDs must be unique")
        return self

    @property
    def has_semantic_evidence(self) -> bool:
        return bool(self.evidence_ids) and self.basis is not None and self.confidence is not None


class DiagramReviewAudit(StrictModel):
    reviewer: str = Field(min_length=1)
    reviewed_at_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    crop_path: str = Field(min_length=1)
    crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis: str = Field(min_length=1)
    review_crop_manifest: DiagramReviewCropManifest | None = None
    review_asset: HighResReviewAsset | None = None

    @model_validator(mode="after")
    def validate_highres_review_binding(self) -> "DiagramReviewAudit":
        if (self.review_crop_manifest is None) != (self.review_asset is None):
            raise ValueError("diagram review high-resolution audit requires both manifest and review asset")
        if self.review_asset is not None and self.review_asset.render_dpi < 600:
            raise ValueError("diagram review high-resolution audit requires at least 600 DPI")
        return self


class DiagramReviewEntry(StrictModel):
    review_id: str = Field(pattern=r"^diagram:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_num: int = Field(ge=1)
    figure_evidence_id: str
    figure_crop_path: str = Field(min_length=1)
    figure_crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: DiagramReviewState = DiagramReviewState.DRAFT
    semantic_contract_version: DiagramSemanticContractVersion = DiagramSemanticContractVersion.LEGACY_V1
    title: str | None = None
    diagram_type: DiagramType | None = None
    nodes: tuple[DiagramNode, ...] = ()
    edges: tuple[DiagramEdge, ...] = ()
    candidate_nodes: tuple[DiagramCandidate, ...] = ()
    candidate_edge_texts: tuple[str, ...] = ()
    audit: DiagramReviewAudit | None = None

    @model_validator(mode="after")
    def validate_review_shape(self) -> "DiagramReviewEntry":
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("reviewed diagram node IDs must be unique")
        if self.state == DiagramReviewState.REVIEWED:
            if not self.title or not self.diagram_type or len(self.nodes) < 2 or not self.edges or self.audit is None:
                raise ValueError("reviewed diagrams require title, type, nodes, edges and audit")
            if self.audit.crop_path != self.figure_crop_path or self.audit.crop_sha256 != self.figure_crop_sha256:
                raise ValueError("diagram audit must bind the reviewed figure crop")
            if any(edge.source_node_id not in node_ids or edge.target_node_id not in node_ids for edge in self.edges):
                raise ValueError("diagram edges must reference reviewed nodes")
            if any(self.figure_evidence_id not in node.evidence_ids for node in self.nodes):
                raise ValueError("reviewed nodes must remain anchored to the source figure evidence")
            if self.semantic_contract_version == DiagramSemanticContractVersion.SEMANTIC_V2:
                if not all(edge.has_semantic_evidence for edge in self.edges):
                    raise ValueError("semantic_v2 reviewed diagrams require edge evidence_ids, basis and confidence")
        elif self.audit is not None:
            raise ValueError("draft diagrams cannot contain final audit evidence")
        return self


class DiagramReviewSidecar(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    sidecar_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    registry_id: str
    material_profile_id: str
    annotation_revision: int = Field(ge=1)
    entries: tuple[DiagramReviewEntry, ...]

    @model_validator(mode="after")
    def validate_entries(self) -> "DiagramReviewSidecar":
        review_ids = [entry.review_id for entry in self.entries]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("diagram review IDs must be unique")
        bindings = [(entry.sample_id, entry.figure_evidence_id) for entry in self.entries]
        if len(bindings) != len(set(bindings)):
            raise ValueError("each source figure may have only one diagram review entry")
        return self


def make_diagram_review_id(*, sample_id: str, figure_evidence_id: str) -> str:
    digest = hashlib.sha256(f"{sample_id}:{figure_evidence_id}".encode("utf-8")).hexdigest()[:24]
    return f"diagram:{digest}"


def load_diagram_review_sidecar(path: Path) -> DiagramReviewSidecar:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return DiagramReviewSidecar.model_validate(raw)


def reviewed_entry_map(sidecar: DiagramReviewSidecar) -> dict[tuple[str, str], DiagramReviewEntry]:
    return {
        (entry.sample_id, entry.figure_evidence_id): entry
        for entry in sidecar.entries
        if entry.state == DiagramReviewState.REVIEWED
    }
