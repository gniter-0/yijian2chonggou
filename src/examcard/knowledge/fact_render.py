"""证据渲染与内容单元切分（拆分层 1）。"""
from __future__ import annotations

import re

from ..evidence.schema import EvidenceKind, EvidenceUnit
from ..materials.schema import ContentPolicy, PresentationTransformAction

from .schema import PresentationTransform

from .fact_lexicon import _VISUAL_KINDS
from .fact_types import RenderedEvidence
from .fact_support import _text_sha256


def _render_evidence(unit: EvidenceUnit, policy: ContentPolicy) -> RenderedEvidence:
    """Return safe derived text while leaving canonical Evidence immutable.

    Every rule is recorded with the evidence ID, source span and before/after
    hashes. This is deliberately a rendering transform, not an OCR correction.
    """

    text = unit.canonical_text.strip()
    if not text or text.startswith("image:"):
        return RenderedEvidence(unit=unit, text="")

    transforms: list[PresentationTransform] = []
    for rule in policy.presentation_transform_rules:
        matcher = re.compile(rule.pattern)
        matches = list(matcher.finditer(text))
        if not matches:
            continue
        if rule.action == PresentationTransformAction.REMOVE_MATCHES:
            for match in reversed(matches):
                before = text
                text = before[: match.start()] + before[match.end() :]
                transforms.append(
                    PresentationTransform(
                        evidence_id=unit.evidence_id,
                        start=match.start(),
                        end=match.end(),
                        rule_id=rule.rule_id,
                        before_sha256=_text_sha256(before),
                        after_sha256=_text_sha256(text),
                    )
                )
        elif rule.action == PresentationTransformAction.TRUNCATE_FROM_MATCH:
            match = matches[0]
            before = text
            text = before[: match.start()].rstrip()
            transforms.append(
                PresentationTransform(
                    evidence_id=unit.evidence_id,
                    start=match.start(),
                    end=match.end(),
                    rule_id=rule.rule_id,
                    before_sha256=_text_sha256(before),
                    after_sha256=_text_sha256(text),
                )
            )
    return RenderedEvidence(unit=unit, text=text.strip(), presentation_transforms=tuple(transforms))


def _render_evidence_map(units: tuple[EvidenceUnit, ...], policy: ContentPolicy) -> dict[str, RenderedEvidence]:
    return {unit.evidence_id: _render_evidence(unit, policy) for unit in units}


def _display_text(unit: EvidenceUnit, rendered: dict[str, RenderedEvidence] | None = None) -> str:
    if rendered is not None:
        return rendered[unit.evidence_id].text
    text = unit.canonical_text.strip()
    if text and not text.startswith("image:"):
        return text
    return ""


def _content_units(
    units: tuple[EvidenceUnit, ...], rendered: dict[str, RenderedEvidence] | None = None
) -> tuple[EvidenceUnit, ...]:
    """Choose one textual layer while retaining every source anchor in the fact.

    Tables expose a complete table, rows, and cells; rendering all three repeats
    the same text. Figures expose an image marker plus nodes/edges; only visible
    node/edge labels belong in deterministic card content.
    """

    table_units = tuple(unit for unit in units if unit.kind == EvidenceKind.TABLE and _display_text(unit, rendered))
    if table_units:
        return table_units
    table_rows = tuple(unit for unit in units if unit.kind == EvidenceKind.TABLE_ROW and _display_text(unit, rendered))
    if table_rows:
        return table_rows
    table_cells = tuple(unit for unit in units if unit.kind == EvidenceKind.TABLE_CELL and _display_text(unit, rendered))
    if table_cells:
        return table_cells
    diagram_labels = tuple(
        unit
        for unit in units
        if unit.kind in {EvidenceKind.DIAGRAM_NODE, EvidenceKind.DIAGRAM_EDGE} and _display_text(unit, rendered)
    )
    if diagram_labels:
        return diagram_labels
    return tuple(unit for unit in units if _display_text(unit, rendered))


def _group_text(units: tuple[EvidenceUnit, ...], rendered: dict[str, RenderedEvidence]) -> str:
    texts = [_display_text(unit, rendered) for unit in _content_units(units, rendered)]
    if texts:
        return "\n".join(texts)
    if any(unit.kind in _VISUAL_KINDS for unit in units):
        return "图示结构"
    return units[0].kind.value
