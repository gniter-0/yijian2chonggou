"""Teaching-quality regression tests for the Phase 2R semantic design."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError


from examcard.evidence.schema import BBox
from examcard.knowledge.diagram.sidecar import (
    DiagramEdge,
    DiagramNode,
    DiagramReviewAudit,
    DiagramReviewEntry,
    DiagramReviewState,
    DiagramSemanticContractVersion,
    DiagramType,
)
from examcard.knowledge.schema import FactType, ShortCardException
from examcard.knowledge.contracts import StructuredTableInput, TableCellInput, TableRowInput
from examcard.knowledge.quality import (
    QualityStatus,
    RenderedTableInput,
    SpatialTextToken,
    assess_card_granularity,
    assess_diagram_semantics,
    assess_table_structure,
    assess_title_quality,
    build_teaching_quality_gate,
    detect_spatial_watermarks,
)


def _table() -> StructuredTableInput:
    return StructuredTableInput(
        table_evidence_id="ev:table",
        page_num=1,
        row_count=2,
        column_count=2,
        rows=(
            TableRowInput(
                evidence_id="ev:row-0",
                row_index=0,
                cells=(
                    TableCellInput(evidence_id="ev:cell-00", row_index=0, column_index=0, canonical_text="荷载"),
                    TableCellInput(evidence_id="ev:cell-01", row_index=0, column_index=1, canonical_text="组合"),
                ),
            ),
            TableRowInput(
                evidence_id="ev:row-1",
                row_index=1,
                cells=(
                    TableCellInput(evidence_id="ev:cell-10", row_index=1, column_index=0, canonical_text="①"),
                    TableCellInput(evidence_id="ev:cell-11", row_index=1, column_index=1, canonical_text="②"),
                ),
            ),
        ),
    )


def _diagram(*, contract: DiagramSemanticContractVersion, semantic_edge: bool) -> DiagramReviewEntry:
    edge = {
        "source_node_id": "node-root",
        "target_node_id": "node-child",
        "relation": "包含",
    }
    if semantic_edge:
        edge.update({"evidence_ids": ["ev:figure"], "basis": "人工确认箭头从根节点指向子节点。", "confidence": 0.98})
    return DiagramReviewEntry(
        review_id="diagram:" + "a" * 24,
        sample_id="sample-a",
        pdf_sha256="b" * 64,
        page_num=1,
        figure_evidence_id="ev:figure",
        figure_crop_path="crops/figure.png",
        figure_crop_sha256="c" * 64,
        state=DiagramReviewState.REVIEWED,
        semantic_contract_version=contract,
        title="费用结构",
        diagram_type=DiagramType.HIERARCHY,
        nodes=(
            DiagramNode(node_id="node-root", label="总费用", evidence_ids=("ev:figure",)),
            DiagramNode(node_id="node-child", label="直接费", evidence_ids=("ev:figure",)),
        ),
        edges=(DiagramEdge(**edge),),
        audit=DiagramReviewAudit(
            reviewer="pytest",
            reviewed_at_utc="2026-07-27T00:00:00Z",
            crop_path="crops/figure.png",
            crop_sha256="c" * 64,
            basis="人工复核了图示节点和边。",
        ),
    )


def test_title_and_short_card_rules_are_typed_not_blanket_length_blocks():
    assert assess_title_quality(title="知识点", evidence_texts=("知识点说明",)).status == QualityStatus.FAIL
    assert assess_title_quality(title="A", evidence_texts=("A值",)).status == QualityStatus.MANUAL_REVIEW
    assert (
        assess_title_quality(
            title="A",
            evidence_texts=("A值",),
            short_card_exception=ShortCardException.IDENTIFIER_ONLY,
        ).status
        == QualityStatus.PASS
    )
    assert (
        assess_card_granularity(
            content="x=y",
            fact_types=(FactType.FORMULA,),
            short_card_exception=ShortCardException.ATOMIC_FORMULA,
        ).status
        == QualityStatus.PASS
    )
    assert assess_card_granularity(content="甲", fact_types=(FactType.ENUMERATION,)).status == QualityStatus.MANUAL_REVIEW
    assert (
        assess_card_granularity(
            content="甲",
            fact_types=(FactType.DEFINITION,),
            short_card_exception=ShortCardException.ATOMIC_FORMULA,
        ).status
        == QualityStatus.FAIL
    )


def test_table_gate_requires_row_column_and_cell_fidelity_not_flattened_text():
    source = _table()
    preserved = assess_table_structure(
        source=source,
        rendered=RenderedTableInput(source_table_evidence_id="ev:table", rows=(("荷载", "组合"), ("①", "②"))),
    )
    flattened = assess_table_structure(
        source=source,
        rendered=RenderedTableInput(source_table_evidence_id="ev:table", rows=(("荷载 组合 ① ②",),)),
    )

    assert preserved.status == QualityStatus.PASS
    assert flattened.status == QualityStatus.FAIL
    assert "geometry" in flattened.reason


def test_structured_table_contract_requires_exact_grid_coverage_and_supports_spans():
    with pytest.raises(ValidationError, match="exactly cover declared grid geometry"):
        StructuredTableInput(
            table_evidence_id="ev:partial",
            page_num=1,
            row_count=2,
            column_count=2,
            rows=(
                TableRowInput(
                    evidence_id="ev:partial-row-0",
                    row_index=0,
                    cells=(TableCellInput(evidence_id="ev:partial-00", row_index=0, column_index=0, canonical_text="A"),),
                ),
                TableRowInput(
                    evidence_id="ev:partial-row-1",
                    row_index=1,
                    cells=(TableCellInput(evidence_id="ev:partial-10", row_index=1, column_index=0, canonical_text="B"),),
                ),
            ),
        )

    spanned = StructuredTableInput(
        table_evidence_id="ev:spanned",
        page_num=1,
        row_count=2,
        column_count=2,
        rows=(
            TableRowInput(
                evidence_id="ev:spanned-row-0",
                row_index=0,
                cells=(
                    TableCellInput(evidence_id="ev:spanned-00", row_index=0, column_index=0, row_span=2, canonical_text="合并"),
                    TableCellInput(evidence_id="ev:spanned-01", row_index=0, column_index=1, canonical_text="上"),
                ),
            ),
            TableRowInput(
                evidence_id="ev:spanned-row-1",
                row_index=1,
                cells=(TableCellInput(evidence_id="ev:spanned-11", row_index=1, column_index=1, canonical_text="下"),),
            ),
        ),
    )

    assert spanned.row_count == 2


def test_watermark_detection_requires_repetition_and_stable_coordinates():
    tokens = (
        SpatialTextToken(evidence_id="ev:a", page_num=1, text="题", bbox=BBox(x0=10, y0=20, x1=12, y1=24)),
        SpatialTextToken(evidence_id="ev:b", page_num=2, text="题", bbox=BBox(x0=11, y0=21, x1=13, y1=25)),
        SpatialTextToken(evidence_id="ev:c", page_num=3, text="题", bbox=BBox(x0=9, y0=19, x1=11, y1=23)),
        SpatialTextToken(evidence_id="ev:d", page_num=1, text="联", bbox=BBox(x0=100, y0=20, x1=102, y1=24)),
        SpatialTextToken(evidence_id="ev:e", page_num=2, text="联", bbox=BBox(x0=200, y0=20, x1=202, y1=24)),
        SpatialTextToken(evidence_id="ev:f", page_num=3, text="联", bbox=BBox(x0=300, y0=20, x1=302, y1=24)),
    )

    candidates = detect_spatial_watermarks(tokens)
    assert len(candidates) == 1
    assert candidates[0].text == "题"
    assert candidates[0].page_nums == (1, 2, 3)


def test_semantic_v2_diagram_edges_require_evidence_basis_and_confidence():
    legacy = _diagram(contract=DiagramSemanticContractVersion.LEGACY_V1, semantic_edge=False)
    semantic = _diagram(contract=DiagramSemanticContractVersion.SEMANTIC_V2, semantic_edge=True)

    assert assess_diagram_semantics(legacy).status == QualityStatus.MANUAL_REVIEW
    assert assess_diagram_semantics(semantic).status == QualityStatus.PASS
    with pytest.raises(ValidationError, match="require edge evidence_ids"):
        _diagram(contract=DiagramSemanticContractVersion.SEMANTIC_V2, semantic_edge=False)


def test_teaching_gate_keeps_manual_review_separate_from_hard_failures():
    title = assess_title_quality(title="预算总成本", evidence_texts=("项目预算总成本=单价×工程量",))
    short = assess_card_granularity(content="甲", fact_types=(FactType.ENUMERATION,))
    diagram = assess_diagram_semantics(_diagram(contract=DiagramSemanticContractVersion.LEGACY_V1, semantic_edge=False))
    candidate = detect_spatial_watermarks(
        (
            SpatialTextToken(evidence_id="ev:1", page_num=1, text="题", bbox=BBox(x0=1, y0=1, x1=2, y1=2)),
            SpatialTextToken(evidence_id="ev:2", page_num=2, text="题", bbox=BBox(x0=1, y0=1, x1=2, y1=2)),
            SpatialTextToken(evidence_id="ev:3", page_num=3, text="题", bbox=BBox(x0=1, y0=1, x1=2, y1=2)),
        )
    )

    gate = build_teaching_quality_gate(titles=(title,), granularities=(short,), diagrams=(diagram,), watermark_candidates=candidate)
    assert gate.title_failures == 0
    assert gate.manual_review_items == 2
    assert gate.spatial_watermark_candidates == 1
    assert gate.publishable is False
