"""Teaching-quality gates for future semantic Step 2 planning.

These checks are intentionally narrower than publication gates. They diagnose
planning quality without changing Evidence, sidecars, or deterministic card
assembly. Compact cards and single-character labels are routed by type and
review state rather than rejected by a blanket length rule.
"""
from __future__ import annotations

import re
from collections import defaultdict
from enum import StrEnum
from statistics import mean
from typing import Iterable

from pydantic import Field, computed_field

from ..evidence.layout import normalized_compare_text
from ..evidence.schema import BBox
from ..materials.schema import StrictModel

from .diagram.sidecar import DiagramReviewEntry, DiagramReviewState, DiagramSemanticContractVersion
from .schema import FactType, ShortCardException
from .contracts import StructuredTableInput


class QualityStatus(StrEnum):
    PASS = "pass"
    MANUAL_REVIEW = "manual_review"
    FAIL = "fail"


class TeachingQualityEvaluationState(StrEnum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"


class TitleQualityResult(StrictModel):
    title: str
    status: QualityStatus
    reason: str


class HeadingOnlyFactResult(StrictModel):
    proposal_key: str
    status: QualityStatus
    selected_anchor_span_ids: tuple[str, ...]
    reason: str


def assess_heading_only_fact(
    *,
    proposal_key: str,
    selected_anchor_span_ids: Iterable[str],
    context_only_anchor_span_ids: Iterable[str],
    allowed_heading_only_anchor_span_ids: Iterable[str] = (),
) -> HeadingOnlyFactResult:
    selected = tuple(dict.fromkeys(selected_anchor_span_ids))
    context_only = frozenset(context_only_anchor_span_ids)
    allowed = frozenset(allowed_heading_only_anchor_span_ids)
    if not selected or not set(selected) <= context_only:
        return HeadingOnlyFactResult(
            proposal_key=proposal_key,
            status=QualityStatus.PASS,
            selected_anchor_span_ids=selected,
            reason="Fact has at least one non-context Evidence anchor",
        )
    if set(selected) <= allowed:
        return HeadingOnlyFactResult(
            proposal_key=proposal_key,
            status=QualityStatus.PASS,
            selected_anchor_span_ids=selected,
            reason="heading-only Fact is explicitly allowed by a reviewed golden assertion",
        )
    return HeadingOnlyFactResult(
        proposal_key=proposal_key,
        status=QualityStatus.FAIL,
        selected_anchor_span_ids=selected,
        reason="title/context Evidence cannot be the sole Fact anchor without a reviewed golden allowance",
    )


_GENERIC_TITLES = frozenset({"知识点", "考点", "重点", "内容", "图", "表", "公式", "未命名"})


def _meaningful_characters(value: str) -> set[str]:
    return {char for char in value if "\u4e00" <= char <= "\u9fff" or char.isalnum()}


def assess_title_quality(
    *,
    title: str,
    evidence_texts: Iterable[str],
    short_card_exception: ShortCardException | None = None,
) -> TitleQualityResult:
    stripped = title.strip()
    if not stripped:
        return TitleQualityResult(title=title, status=QualityStatus.FAIL, reason="title is blank after stripping")
    normalized_title = normalized_compare_text(stripped)
    if normalized_title in {normalized_compare_text(value) for value in _GENERIC_TITLES}:
        return TitleQualityResult(title=stripped, status=QualityStatus.FAIL, reason="title is a generic teaching label")
    source = "\n".join(evidence_texts)
    normalized_source = normalized_compare_text(source)
    if normalized_title not in normalized_source:
        overlap = _meaningful_characters(stripped) & _meaningful_characters(source)
        if len(overlap) < 2:
            return TitleQualityResult(title=stripped, status=QualityStatus.FAIL, reason="title lacks grounded content terms")
    if len(_meaningful_characters(stripped)) == 1 and short_card_exception != ShortCardException.IDENTIFIER_ONLY:
        return TitleQualityResult(
            title=stripped,
            status=QualityStatus.MANUAL_REVIEW,
            reason="single-character title needs identifier_only review instead of automatic rejection",
        )
    return TitleQualityResult(title=stripped, status=QualityStatus.PASS, reason="title has grounded content terms")


class RenderedTableInput(StrictModel):
    source_table_evidence_id: str
    rows: tuple[tuple[str, ...], ...]


class TableStructureResult(StrictModel):
    source_table_evidence_id: str
    status: QualityStatus
    reason: str
    expected_rows: int = Field(ge=1)
    expected_columns: int = Field(ge=1)
    rendered_rows: int = Field(ge=0)
    rendered_columns: int = Field(ge=0)


def assess_table_structure(*, source: StructuredTableInput, rendered: RenderedTableInput) -> TableStructureResult:
    if rendered.source_table_evidence_id != source.table_evidence_id:
        return TableStructureResult(
            source_table_evidence_id=source.table_evidence_id,
            status=QualityStatus.FAIL,
            reason="rendered table does not bind the source table Evidence",
            expected_rows=source.row_count,
            expected_columns=source.column_count,
            rendered_rows=len(rendered.rows),
            rendered_columns=max((len(row) for row in rendered.rows), default=0),
        )
    rendered_columns = max((len(row) for row in rendered.rows), default=0)
    if len(rendered.rows) != source.row_count or any(len(row) != source.column_count for row in rendered.rows):
        return TableStructureResult(
            source_table_evidence_id=source.table_evidence_id,
            status=QualityStatus.FAIL,
            reason="rendered table row/column geometry does not preserve source geometry",
            expected_rows=source.row_count,
            expected_columns=source.column_count,
            rendered_rows=len(rendered.rows),
            rendered_columns=rendered_columns,
        )
    for source_row in source.rows:
        if source_row.row_index >= len(rendered.rows):
            return TableStructureResult(
                source_table_evidence_id=source.table_evidence_id,
                status=QualityStatus.FAIL,
                reason="rendered table omits an observed source row",
                expected_rows=source.row_count,
                expected_columns=source.column_count,
                rendered_rows=len(rendered.rows),
                rendered_columns=rendered_columns,
            )
        for cell in source_row.cells:
            rendered_cell = rendered.rows[cell.row_index][cell.column_index]
            if normalized_compare_text(cell.canonical_text) not in normalized_compare_text(rendered_cell):
                return TableStructureResult(
                    source_table_evidence_id=source.table_evidence_id,
                    status=QualityStatus.FAIL,
                    reason=f"rendered table loses cell Evidence {cell.evidence_id}",
                    expected_rows=source.row_count,
                    expected_columns=source.column_count,
                    rendered_rows=len(rendered.rows),
                    rendered_columns=rendered_columns,
                )
    return TableStructureResult(
        source_table_evidence_id=source.table_evidence_id,
        status=QualityStatus.PASS,
        reason="rendered table preserves source geometry and observed cells",
        expected_rows=source.row_count,
        expected_columns=source.column_count,
        rendered_rows=len(rendered.rows),
        rendered_columns=rendered_columns,
    )


class CardGranularityResult(StrictModel):
    status: QualityStatus
    content_length: int = Field(ge=0)
    reason: str


def assess_card_granularity(
    *,
    content: str,
    fact_types: Iterable[FactType],
    short_card_exception: ShortCardException | None = None,
    short_threshold: int = 12,
) -> CardGranularityResult:
    if short_threshold < 1:
        raise ValueError("short card threshold must be positive")
    length = len(re.sub(r"\s+", "", content))
    types = frozenset(fact_types)
    if length > short_threshold:
        return CardGranularityResult(status=QualityStatus.PASS, content_length=length, reason="card has normal teaching granularity")
    allowed = {
        ShortCardException.ATOMIC_FORMULA: FactType.FORMULA in types,
        ShortCardException.DIAGRAM_SUMMARY: FactType.DIAGRAM_RELATION in types,
        ShortCardException.COMPACT_ENUMERATION: FactType.ENUMERATION in types,
        ShortCardException.IDENTIFIER_ONLY: bool(types),
    }
    if short_card_exception is None:
        return CardGranularityResult(
            status=QualityStatus.MANUAL_REVIEW,
            content_length=length,
            reason="short card is retained but needs a typed exception or reviewer decision",
        )
    if allowed[short_card_exception]:
        return CardGranularityResult(
            status=QualityStatus.PASS,
            content_length=length,
            reason=f"short card allowed by typed exception: {short_card_exception.value}",
        )
    return CardGranularityResult(
        status=QualityStatus.FAIL,
        content_length=length,
        reason=f"short card exception {short_card_exception.value} does not match Fact type",
    )


class SpatialTextToken(StrictModel):
    evidence_id: str
    page_num: int = Field(ge=1)
    text: str = Field(min_length=1)
    bbox: BBox


class SpatialWatermarkCandidate(StrictModel):
    text: str
    evidence_ids: tuple[str, ...]
    page_nums: tuple[int, ...]
    mean_x0: float
    mean_y0: float
    reason: str


def detect_spatial_watermarks(
    tokens: Iterable[SpatialTextToken],
    *,
    minimum_pages: int = 3,
    coordinate_tolerance: float = 12.0,
) -> tuple[SpatialWatermarkCandidate, ...]:
    """Find repeat-position short glyphs without treating glyph value as proof.

    A token such as ``题`` is only a candidate when it repeats on enough pages
    at almost the same coordinates. Ordinary single-character table cells and
    list markers therefore remain untouched.
    """
    if minimum_pages < 2 or coordinate_tolerance <= 0:
        raise ValueError("spatial watermark thresholds are invalid")
    grouped: dict[str, list[SpatialTextToken]] = defaultdict(list)
    for token in tokens:
        compact = re.sub(r"\s+", "", token.text)
        if 1 <= len(compact) <= 2:
            grouped[compact].append(token)
    candidates: list[SpatialWatermarkCandidate] = []
    for text, values in grouped.items():
        clusters: list[list[SpatialTextToken]] = []
        for token in sorted(values, key=lambda item: (item.bbox.x0, item.bbox.y0, item.page_num)):
            for cluster in clusters:
                x0 = mean(item.bbox.x0 for item in cluster)
                y0 = mean(item.bbox.y0 for item in cluster)
                if abs(token.bbox.x0 - x0) <= coordinate_tolerance and abs(token.bbox.y0 - y0) <= coordinate_tolerance:
                    cluster.append(token)
                    break
            else:
                clusters.append([token])
        for cluster in clusters:
            pages = tuple(sorted({token.page_num for token in cluster}))
            if len(pages) < minimum_pages:
                continue
            candidates.append(
                SpatialWatermarkCandidate(
                    text=text,
                    evidence_ids=tuple(token.evidence_id for token in cluster),
                    page_nums=pages,
                    mean_x0=round(mean(token.bbox.x0 for token in cluster), 4),
                    mean_y0=round(mean(token.bbox.y0 for token in cluster), 4),
                    reason="short token repeats across pages at a stable spatial position",
                )
            )
    return tuple(sorted(candidates, key=lambda item: (item.text, item.mean_x0, item.mean_y0)))


class DiagramSemanticResult(StrictModel):
    review_id: str
    status: QualityStatus
    reason: str
    missing_edge_count: int = Field(ge=0)


def assess_diagram_semantics(entry: DiagramReviewEntry) -> DiagramSemanticResult:
    if entry.state != DiagramReviewState.REVIEWED:
        return DiagramSemanticResult(
            review_id=entry.review_id,
            status=QualityStatus.MANUAL_REVIEW,
            reason="diagram remains draft",
            missing_edge_count=len(entry.edges),
        )
    if entry.semantic_contract_version != DiagramSemanticContractVersion.SEMANTIC_V2:
        return DiagramSemanticResult(
            review_id=entry.review_id,
            status=QualityStatus.MANUAL_REVIEW,
            reason="legacy diagram review lacks semantic_v2 edge provenance",
            missing_edge_count=sum(not edge.has_semantic_evidence for edge in entry.edges),
        )
    node_evidence_ids = {evidence_id for node in entry.nodes for evidence_id in node.evidence_ids}
    allowed_evidence_ids = node_evidence_ids | {entry.figure_evidence_id}
    missing = [edge for edge in entry.edges if not edge.has_semantic_evidence]
    unknown = [edge for edge in entry.edges if not set(edge.evidence_ids) <= allowed_evidence_ids]
    if missing or unknown:
        reason = "diagram edges lack evidence/basis/confidence" if missing else "diagram edge references unknown Evidence"
        return DiagramSemanticResult(
            review_id=entry.review_id,
            status=QualityStatus.FAIL,
            reason=reason,
            missing_edge_count=len(missing) + len(unknown),
        )
    return DiagramSemanticResult(
        review_id=entry.review_id,
        status=QualityStatus.PASS,
        reason="semantic_v2 diagram edges have grounded evidence, basis and confidence",
        missing_edge_count=0,
    )


class TeachingQualityGate(StrictModel):
    evaluation_state: TeachingQualityEvaluationState = TeachingQualityEvaluationState.EVALUATED
    title_failures: int = Field(ge=0)
    table_structure_failures: int = Field(ge=0)
    diagram_semantic_failures: int = Field(ge=0)
    heading_only_fact_failures: int = Field(ge=0)
    manual_review_items: int = Field(ge=0)
    spatial_watermark_candidates: int = Field(ge=0)

    @computed_field
    @property
    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.evaluation_state == TeachingQualityEvaluationState.NOT_EVALUATED:
            blockers.append("not_evaluated")
        if self.title_failures:
            blockers.append("title_quality")
        if self.table_structure_failures:
            blockers.append("table_structure")
        if self.diagram_semantic_failures:
            blockers.append("diagram_semantics")
        if self.heading_only_fact_failures:
            blockers.append("heading_only_fact")
        if self.manual_review_items:
            blockers.append("manual_teaching_review")
        if self.spatial_watermark_candidates:
            blockers.append("spatial_watermark_review")
        return tuple(blockers)

    @computed_field
    @property
    def publishable(self) -> bool:
        return not self.blockers


def build_teaching_quality_gate(
    *,
    titles: Iterable[TitleQualityResult] = (),
    tables: Iterable[TableStructureResult] = (),
    granularities: Iterable[CardGranularityResult] = (),
    heading_only_facts: Iterable[HeadingOnlyFactResult] = (),
    diagrams: Iterable[DiagramSemanticResult] = (),
    watermark_candidates: Iterable[SpatialWatermarkCandidate] = (),
) -> TeachingQualityGate:
    # 五种结果模型的公共基类只有 StrictModel，而 StrictModel 上没有 status；不注解的话
    # mypy 把列表元素推成 StrictModel，下面那句不带 isinstance 收窄的 item.status 就报
    # attr-defined。五种模型各自都声明了 status: QualityStatus，如实写成联合即可，四处
    # isinstance 收窄照旧生效。
    results: list[TitleQualityResult | TableStructureResult | CardGranularityResult | HeadingOnlyFactResult | DiagramSemanticResult] = [*titles, *tables, *granularities, *heading_only_facts, *diagrams]
    return TeachingQualityGate(
        title_failures=sum(isinstance(item, TitleQualityResult) and item.status == QualityStatus.FAIL for item in results),
        table_structure_failures=sum(isinstance(item, TableStructureResult) and item.status == QualityStatus.FAIL for item in results),
        diagram_semantic_failures=sum(isinstance(item, DiagramSemanticResult) and item.status == QualityStatus.FAIL for item in results),
        heading_only_fact_failures=sum(isinstance(item, HeadingOnlyFactResult) and item.status == QualityStatus.FAIL for item in results),
        manual_review_items=sum(item.status == QualityStatus.MANUAL_REVIEW for item in results),
        spatial_watermark_candidates=len(tuple(watermark_candidates)),
    )
