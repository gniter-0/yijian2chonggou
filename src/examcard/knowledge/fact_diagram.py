"""图示评审与处置的绑定判定（拆分层 2）。"""
from __future__ import annotations

import re

from ..evidence.schema import EvidenceKind, EvidenceUnit

from .diagram.sidecar import DiagramReviewEntry
from .schema import ContentQualityFlag, Renderability

from .fact_lexicon import _CJK_RE
from .fact_types import DiagramAssessment, DiagramDispositionBinding, RenderedEvidence
from .fact_render import _display_text


def _is_readable_diagram_label(text: str) -> bool:
    compact = " ".join(text.split())
    if len(compact) < 2 or "\ufffd" in compact:
        return False
    if _CJK_RE.search(compact):
        return True
    return not bool(re.search(r"[A-Za-z]", compact)) and bool(re.search(r"\d", compact))


def _reviewed_diagram_for_units(
    *,
    sample_id: str,
    units: tuple[EvidenceUnit, ...],
    reviews: dict[tuple[str, str], DiagramReviewEntry],
) -> DiagramReviewEntry | None:
    figures = [unit for unit in units if unit.kind == EvidenceKind.FIGURE]
    if len(figures) != 1:
        return None
    figure = figures[0]
    entry = reviews.get((sample_id, figure.evidence_id))
    if entry is None:
        return None
    if entry.pdf_sha256 != figure.locator.document_sha256 or entry.page_num != figure.locator.page_num:
        raise ValueError(f"{sample_id}: diagram review binding disagrees with source figure {figure.evidence_id}")
    if entry.figure_crop_path != figure.provenance.crop_path:
        raise ValueError(f"{sample_id}: diagram review crop path disagrees with source figure {figure.evidence_id}")
    return entry


def _diagram_disposition_for_units(
    *,
    sample_id: str,
    units: tuple[EvidenceUnit, ...],
    dispositions: dict[tuple[str, str], DiagramDispositionBinding],
) -> DiagramDispositionBinding | None:
    figures = [unit for unit in units if unit.kind == EvidenceKind.FIGURE]
    if len(figures) != 1:
        return None
    figure = figures[0]
    binding = dispositions.get((sample_id, figure.evidence_id))
    if binding is None:
        return None
    entry = binding.entry
    if entry.pdf_sha256 != figure.locator.document_sha256 or entry.page_num != figure.locator.page_num:
        raise ValueError(f"{sample_id}: diagram disposition binding disagrees with source figure {figure.evidence_id}")
    if entry.figure_crop_path != figure.provenance.crop_path:
        raise ValueError(f"{sample_id}: diagram disposition crop path disagrees with source figure {figure.evidence_id}")
    return binding


def _reviewed_diagram_text(review: DiagramReviewEntry) -> str:
    nodes = "、".join(node.label for node in review.nodes)
    node_by_id = {node.node_id: node.label for node in review.nodes}
    relations = "；".join(
        f"{node_by_id[edge.source_node_id]}{edge.relation}{node_by_id[edge.target_node_id]}"
        for edge in review.edges
    )
    return f"{review.title}\n节点：{nodes}\n关系：{relations}"


def _diagram_assessment(
    *, units: tuple[EvidenceUnit, ...], rendered: dict[str, RenderedEvidence], review: DiagramReviewEntry | None = None
) -> DiagramAssessment:
    """Fail closed when visual OCR cannot form a readable directed relation."""

    if review is not None:
        return DiagramAssessment(renderability=Renderability.PUBLISHABLE, review=review)

    node_labels = [
        _display_text(unit, rendered)
        for unit in units
        if unit.kind == EvidenceKind.DIAGRAM_NODE and _display_text(unit, rendered)
    ]
    readable_nodes = [label for label in node_labels if _is_readable_diagram_label(label)]
    edge_texts = [
        _display_text(unit, rendered)
        for unit in units
        if unit.kind == EvidenceKind.DIAGRAM_EDGE and _display_text(unit, rendered)
    ]
    readable_edges = []
    for edge in edge_texts:
        if edge.count("->") != 1:
            continue
        source, target = (part.strip() for part in edge.split("->"))
        if 2 <= len(source) <= 40 and 2 <= len(target) <= 40 and _is_readable_diagram_label(source) and _is_readable_diagram_label(target):
            readable_edges.append(edge)

    flags: list[ContentQualityFlag] = []
    if len(readable_nodes) < 2 or not readable_edges:
        flags.append(ContentQualityFlag.DIAGRAM_MISSING_STRUCTURE)
    if node_labels and len(readable_nodes) * 2 < len(node_labels):
        flags.append(ContentQualityFlag.DIAGRAM_UNREADABLE_OCR)
    return DiagramAssessment(
        renderability=Renderability.MANUAL_REVIEW if flags else Renderability.PUBLISHABLE,
        quality_flags=tuple(flags),
    )
