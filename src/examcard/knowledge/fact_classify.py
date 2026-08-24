"""事实类型、完备度与数字声明的判定（拆分层 2）。"""
from __future__ import annotations

from ..evidence.schema import EvidenceKind, EvidenceUnit

from .schema import CompletenessMode, FactType, NumberClaim

from .fact_lexicon import _CONSTRAINT_TERMS, _NUMBER_RE, _VISUAL_KINDS
from .fact_types import RenderedEvidence
from .fact_render import _display_text


def _fact_type(units: tuple[EvidenceUnit, ...], text: str) -> FactType:
    kinds = {unit.kind for unit in units}
    if kinds & _VISUAL_KINDS:
        return FactType.DIAGRAM_RELATION
    if EvidenceKind.FORMULA in kinds or "=" in text:
        return FactType.FORMULA
    if EvidenceKind.TABLE in kinds or EvidenceKind.TABLE_ROW in kinds or EvidenceKind.TABLE_CELL in kinds:
        return FactType.COMPOSITION
    if all(unit.kind == EvidenceKind.LIST_ITEM for unit in units):
        return FactType.ENUMERATION
    if any(term in text for term in _CONSTRAINT_TERMS):
        return FactType.EXCEPTION
    if _NUMBER_RE.search(text):
        return FactType.THRESHOLD
    return FactType.DEFINITION


def _completeness_mode(fact_type: FactType) -> CompletenessMode:
    if fact_type == FactType.FORMULA:
        return CompletenessMode.COMPLETE_FORMULA
    if fact_type == FactType.ENUMERATION:
        return CompletenessMode.ALL_ITEMS
    if fact_type == FactType.COMPOSITION:
        return CompletenessMode.FULL_TABLE_ROW
    if fact_type == FactType.DIAGRAM_RELATION:
        return CompletenessMode.FULL_DIAGRAM_SUBGRAPH
    return CompletenessMode.ATOMIC


def _number_claims(
    *, text: str, units: tuple[EvidenceUnit, ...], rendered: dict[str, RenderedEvidence]
) -> tuple[NumberClaim, ...]:
    claims: list[NumberClaim] = []
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0)
        evidence_id = next((unit.evidence_id for unit in units if raw in _display_text(unit, rendered)), units[0].evidence_id)
        comparator = next((term for term in _CONSTRAINT_TERMS if term in text), None)
        unit = next((item for item in units if item.evidence_id == evidence_id), units[0])
        normalized = raw.replace("％", "%").replace("．", ".").replace(" ", "")
        claims.append(NumberClaim(raw=raw, normalized=normalized, comparator=comparator, evidence_id=unit.evidence_id))
    return tuple(claims)


def _label(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:64] if len(compact) >= 2 else "图示结构"
