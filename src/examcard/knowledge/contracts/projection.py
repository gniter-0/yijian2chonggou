"""Step 2A semantic projection: immutable policy-filtered page source manifests."""
from __future__ import annotations
from enum import StrEnum

from pydantic import Field, model_serializer, model_validator

from ...evidence.schema import EvidenceKind
from ...materials.schema import PolicyDecision, SemanticRole, StrictModel

from .anchors import _DIRECT_TEXT_KINDS, EvidenceAnchorSpan
from .continuation import ContinuationRecord, TableGridIntegrityRecord
from .hashing import _canonical_sha256
from .request_identity import _SHA256_PATTERN
from .versions import (
    LEGACY_STEP2A_SEMANTIC_PROJECTION_VERSION,
    PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION,
    STEP2A_SEMANTIC_PROJECTION_VERSION,
)


class ProjectionSelection(StrEnum):
    DIRECT_INPUT = "direct_input"
    CONTROLLED_DIAGRAM_SUMMARY = "controlled_diagram_summary"
    EXCLUDED_BY_POLICY = "excluded_by_policy"
    EXCLUDED_BY_TYPE = "excluded_by_type"
    EXCLUDED_AS_TABLE_DUPLICATE = "excluded_as_table_duplicate"
    EXCLUDED_BY_AUDIT = "excluded_by_audit"
    MANUAL_REVIEW = "manual_review"


class ProjectionDecisionRecord(StrictModel):
    """Derived policy application for one immutable Evidence unit."""

    evidence_id: str
    source_kind: EvidenceKind
    source_semantic_role: SemanticRole
    policy_decision: PolicyDecision
    selection: ProjectionSelection
    additional_selections: tuple[ProjectionSelection, ...] = ()
    reason: str = Field(min_length=3)

    @model_validator(mode="after")
    def validate_binding(self) -> "ProjectionDecisionRecord":
        if self.policy_decision.evidence_id != self.evidence_id:
            raise ValueError("projection policy decision must bind the same Evidence ID")
        if self.selection in self.additional_selections or len(self.additional_selections) != len(set(self.additional_selections)):
            raise ValueError("projection additional selections must be unique and differ from the primary selection")
        return self

    @property
    def selections(self) -> frozenset[ProjectionSelection]:
        return frozenset((self.selection, *self.additional_selections))


class ReviewedDiagramNodeInput(StrictModel):
    node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "ReviewedDiagramNodeInput":
        if not self.evidence_ids or len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("reviewed diagram nodes require unique source Evidence IDs")
        return self


class ReviewedDiagramEdgeInput(StrictModel):
    source_node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    target_node_id: str = Field(pattern=r"^node-[a-z0-9][a-z0-9._-]*$")
    relation: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]
    basis: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_edge(self) -> "ReviewedDiagramEdgeInput":
        if self.source_node_id == self.target_node_id:
            raise ValueError("reviewed diagram edges cannot self-reference")
        if not self.evidence_ids or len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("reviewed diagram edges require unique source Evidence IDs")
        return self


class ReviewedStructuralDiagramInput(StrictModel):
    review_id: str = Field(pattern=r"^diagram:[0-9a-f]{24}$")
    figure_evidence_id: str
    title: str = Field(min_length=1)
    diagram_type: str = Field(min_length=1)
    nodes: tuple[ReviewedDiagramNodeInput, ...]
    edges: tuple[ReviewedDiagramEdgeInput, ...]

    @model_validator(mode="after")
    def validate_diagram(self) -> "ReviewedStructuralDiagramInput":
        node_ids = {node.node_id for node in self.nodes}
        if len(self.nodes) < 2 or len(node_ids) != len(self.nodes) or not self.edges:
            raise ValueError("reviewed structural diagrams require unique nodes and edges")
        if any(edge.source_node_id not in node_ids or edge.target_node_id not in node_ids for edge in self.edges):
            raise ValueError("reviewed diagram edges must reference supplied nodes")
        return self

    @property
    def source_evidence_ids(self) -> tuple[str, ...]:
        identifiers = [self.figure_evidence_id]
        identifiers.extend(evidence_id for node in self.nodes for evidence_id in node.evidence_ids)
        identifiers.extend(evidence_id for edge in self.edges for evidence_id in edge.evidence_ids)
        return tuple(dict.fromkeys(identifiers))


class Step2ASemanticProjection(StrictModel):
    """Immutable, policy-filtered source manifest for one Step 2A page."""

    projection_id: str = Field(pattern=r"^step2a-projection:[0-9a-f]{24}$")
    projection_version: str = STEP2A_SEMANTIC_PROJECTION_VERSION
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    page_num: int = Field(ge=1)
    source_manifest_path: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_policy_id: str = Field(min_length=1)
    content_policy_revision: int = Field(ge=1)
    content_policy_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    decisions: tuple[ProjectionDecisionRecord, ...]
    direct_evidence_ids: tuple[str, ...]
    context_only_evidence_ids: tuple[str, ...] = ()
    anchor_spans: tuple[EvidenceAnchorSpan, ...]
    table_grid_integrity: tuple[TableGridIntegrityRecord, ...] = ()
    continuation_records: tuple["ContinuationRecord", ...] = ()
    reviewed_table_repair_ids: tuple[str, ...] = ()
    table_repair_sidecar_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    controlled_diagrams: tuple[ReviewedStructuralDiagramInput, ...] = ()
    diagram_review_sidecar_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    controlled_diagram_anchor_spans: tuple[EvidenceAnchorSpan, ...] = ()

    @model_serializer(mode="wrap")
    def _serialize_without_empty_diagram_anchors(self, handler):
        """Drop the quarantined-anchor key when empty: v4/v5 payloads and every
        diagram-free dump stay byte-identical to the pre-v6 shape."""
        data = handler(self)
        if isinstance(data, dict) and not data.get("controlled_diagram_anchor_spans"):
            data.pop("controlled_diagram_anchor_spans", None)
        return data

    @property
    def fingerprint_sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"projection_id"})
        if self.projection_version == LEGACY_STEP2A_SEMANTIC_PROJECTION_VERSION:
            # v4 manifests predate continuation records. Preserve their stable
            # identity so persisted audit-only requests remain readable.
            payload.pop("continuation_records", None)
        return _canonical_sha256(payload)

    @model_validator(mode="after")
    def validate_projection(self) -> "Step2ASemanticProjection":
        if self.projection_version not in {
            LEGACY_STEP2A_SEMANTIC_PROJECTION_VERSION,
            PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION,
            STEP2A_SEMANTIC_PROJECTION_VERSION,
        }:
            raise ValueError("semantic projection uses an incompatible version")
        if self.controlled_diagram_anchor_spans and self.projection_version != STEP2A_SEMANTIC_PROJECTION_VERSION:
            raise ValueError("controlled-diagram node anchors require the v6 projection contract")
        decision_ids = [decision.evidence_id for decision in self.decisions]
        if not self.decisions or len(decision_ids) != len(set(decision_ids)):
            raise ValueError("semantic projections require one policy decision for every source Evidence ID")
        if len(self.direct_evidence_ids) != len(set(self.direct_evidence_ids)):
            raise ValueError("semantic projection direct Evidence IDs must be unique")
        decisions = {decision.evidence_id: decision for decision in self.decisions}
        if not set(self.direct_evidence_ids) <= set(decisions):
            raise ValueError("semantic projection direct Evidence IDs must have policy decisions")
        if any(ProjectionSelection.DIRECT_INPUT not in decisions[evidence_id].selections for evidence_id in self.direct_evidence_ids):
            raise ValueError("direct Evidence IDs must be marked direct_input")
        if len(self.context_only_evidence_ids) != len(set(self.context_only_evidence_ids)):
            raise ValueError("semantic projection context-only Evidence IDs must be unique")
        if not set(self.context_only_evidence_ids) <= set(self.direct_evidence_ids):
            raise ValueError("semantic projection context-only Evidence must remain direct input")
        span_ids = [span.anchor_span_id for span in self.anchor_spans]
        if not self.anchor_spans or len(span_ids) != len(set(span_ids)):
            raise ValueError("semantic projections require unique selectable anchor spans")
        repair_ids = [record.repair_id for record in self.table_grid_integrity if record.repair_id is not None]
        if len(self.reviewed_table_repair_ids) != len(set(self.reviewed_table_repair_ids)):
            raise ValueError("semantic projections require unique reviewed table repair IDs")
        if set(repair_ids) != set(self.reviewed_table_repair_ids):
            raise ValueError("semantic projection reviewed repair IDs must match grid records")
        if bool(self.reviewed_table_repair_ids) != bool(self.table_repair_sidecar_sha256):
            raise ValueError("semantic projection table repair sidecar hash must match reviewed repair presence")
        direct_text_or_cells = {
            evidence_id
            for evidence_id in self.direct_evidence_ids
            if decisions[evidence_id].source_kind in (_DIRECT_TEXT_KINDS | {EvidenceKind.TABLE_CELL})
        }
        direct_text = {
            evidence_id
            for evidence_id in self.direct_evidence_ids
            if decisions[evidence_id].source_kind in _DIRECT_TEXT_KINDS
        }
        spans_by_parent: dict[str, list[EvidenceAnchorSpan]] = {}
        for span in self.anchor_spans:
            if span.parent_evidence_id not in direct_text_or_cells:
                raise ValueError("anchor spans must bind direct textual Evidence or structured table cells")
            if span.parent_evidence_kind != decisions[span.parent_evidence_id].source_kind:
                raise ValueError("anchor span kind must match its parent Evidence decision")
            if span.source_manifest_sha256 != self.source_manifest_sha256:
                raise ValueError("anchor spans must bind the projection source manifest")
            if span.content_policy_id != self.content_policy_id or span.content_policy_revision != self.content_policy_revision:
                raise ValueError("anchor spans must bind the projection Content Policy revision")
            if span.content_policy_fingerprint_sha256 != self.content_policy_fingerprint_sha256:
                raise ValueError("anchor spans must bind the projection Content Policy fingerprint")
            if span.policy_decision != decisions[span.parent_evidence_id].policy_decision:
                raise ValueError("anchor span policy decision must equal the parent projection decision")
            spans_by_parent.setdefault(span.parent_evidence_id, []).append(span)
        if not direct_text <= set(spans_by_parent):
            raise ValueError("every direct textual Evidence requires at least one anchor span")
        continuation_ids = [record.continuation_id for record in self.continuation_records]
        if len(continuation_ids) != len(set(continuation_ids)):
            raise ValueError("semantic projection continuation records must be unique")
        for record in self.continuation_records:
            if record.page_num != self.page_num or record.source_manifest_sha256 != self.source_manifest_sha256:
                raise ValueError("continuation records must bind the projected source page")
            if not set(record.anchor_span_ids) <= set(span_ids):
                raise ValueError("continuation records must bind projection-owned anchor spans")
        table_ids = [record.table_evidence_id for record in self.table_grid_integrity]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("semantic projection table grid records must be unique")
        source_table_ids = {decision.evidence_id for decision in self.decisions if decision.source_kind == EvidenceKind.TABLE}
        if set(table_ids) != source_table_ids:
            raise ValueError("semantic projection requires one table grid record for every table Evidence")
        diagram_ids = [diagram.review_id for diagram in self.controlled_diagrams]
        if len(diagram_ids) != len(set(diagram_ids)):
            raise ValueError("semantic projection diagram review IDs must be unique")
        if self.controlled_diagrams and self.diagram_review_sidecar_sha256 is None:
            raise ValueError("controlled diagrams require the source sidecar hash")
        for diagram in self.controlled_diagrams:
            if not set(diagram.source_evidence_ids) <= set(decisions):
                raise ValueError("controlled diagram anchors must have policy decisions")
            if any(ProjectionSelection.CONTROLLED_DIAGRAM_SUMMARY not in decisions[evidence_id].selections for evidence_id in diagram.source_evidence_ids):
                raise ValueError("controlled diagram anchors must be explicitly marked in the projection")
        if self.controlled_diagram_anchor_spans:
            if not self.controlled_diagrams:
                raise ValueError("controlled-diagram node anchors require a reviewed controlled diagram")
            reviewed_node_ids = {
                evidence_id
                for diagram in self.controlled_diagrams
                for node in diagram.nodes
                for evidence_id in node.evidence_ids
            }
            quarantined_ids = [span.anchor_span_id for span in self.controlled_diagram_anchor_spans]
            if len(quarantined_ids) != len(set(quarantined_ids)) or set(quarantined_ids) & set(span_ids):
                raise ValueError("controlled-diagram node anchors must be unique and disjoint from selectable anchor spans")
            for span in self.controlled_diagram_anchor_spans:
                decision = decisions.get(span.parent_evidence_id)
                if decision is None or span.parent_evidence_id not in reviewed_node_ids:
                    raise ValueError("controlled-diagram node anchors must bind reviewed diagram-node Evidence")
                if span.parent_evidence_kind != EvidenceKind.DIAGRAM_NODE or decision.source_kind != EvidenceKind.DIAGRAM_NODE:
                    raise ValueError("controlled-diagram node anchors admit only diagram-node Evidence")
                if span.source_manifest_sha256 != self.source_manifest_sha256:
                    raise ValueError("controlled-diagram node anchors must bind the projection source manifest")
                if span.content_policy_id != self.content_policy_id or span.content_policy_revision != self.content_policy_revision:
                    raise ValueError("controlled-diagram node anchors must bind the projection Content Policy revision")
                if span.content_policy_fingerprint_sha256 != self.content_policy_fingerprint_sha256:
                    raise ValueError("controlled-diagram node anchors must bind the projection Content Policy fingerprint")
                if span.policy_decision != decision.policy_decision:
                    raise ValueError("controlled-diagram node anchor policy decision must equal the parent projection decision")
        expected_id = f"step2a-projection:{self.fingerprint_sha256[:24]}"
        if self.projection_id != expected_id:
            raise ValueError("semantic projection ID does not match its immutable content")
        return self


def _validate_parent_anchor_spans(
    *,
    parent_evidence_id: str,
    parent_evidence_kind: EvidenceKind,
    canonical_text: str,
    anchor_spans: tuple[EvidenceAnchorSpan, ...],
) -> None:
    if not anchor_spans:
        return
    span_ids = [span.anchor_span_id for span in anchor_spans]
    if len(span_ids) != len(set(span_ids)):
        raise ValueError("textual inputs require unique anchor span IDs")
    previous_end = 0
    for span in anchor_spans:
        if span.parent_evidence_id != parent_evidence_id or span.parent_evidence_kind != parent_evidence_kind:
            raise ValueError("input anchor span must bind its exact parent Evidence")
        if span.end > len(canonical_text) or canonical_text[span.start : span.end] != span.canonical_text:
            raise ValueError("input anchor span range must match immutable canonical text")
        if span.start < previous_end:
            raise ValueError("input anchor spans must not overlap")
        previous_end = span.end
