"""Step4 通用图卡契约模型（拆分层 1）。

GenericVisibleText/SemanticEdge/TableCell/TableContract/OrderedListItem/
OrderedListContract/TokenBinding/Geometry/CardContract 九个 StrictModel，
彼此做字段注解、只向下引用常量与哈希；从不引用 Phase2R4* 包装层。
"""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from examcard.materials.schema import StrictModel
from examcard.knowledge.contracts.admission_fixtures import EXPECTED_CARD_COUNT

from .step4_contract_constants import (
    CONTRACT_BUNDLE_VERSION,
    MARKERS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    _CARD,
    _FACT,
    _JOB,
    _SHA,
)
from .step4_contract_hashing import _canonical_hash, _sha256


class GenericVisibleText(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    visible_text: str = Field(min_length=1)
    source_kind: Literal[
        "reviewed_fact_assertion",
        "reviewed_semantic_node_label",
        "reviewed_remediation_assertion",
        "reviewed_remediation_ordered_list",
        "reviewed_remediation_table_grid",
        "inherited_reviewed_fact_assertion",
    ]
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    anchor_span_ids: tuple[str, ...] = ()
    source_table_ids: tuple[str, ...] = ()
    continuation_binding_ids: tuple[str, ...] = ()
    complete_table_grid_verified: bool
    semantic_sidecar_sha256: str | None = Field(default=None, pattern=_SHA)
    semantic_node_id: str | None = None
    source_manifest_sha256s: tuple[str, ...] = ()
    crop_sha256s: tuple[str, ...] = ()
    source_text_sha256: str = Field(pattern=_SHA)
    source_display_contract_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_text(self) -> "GenericVisibleText":
        if self.source_text_sha256 != _sha256(self.visible_text.encode("utf-8")):
            raise ValueError("visible text hash does not match source text")
        provenance = (
            self.evidence_ids,
            self.anchor_span_ids,
            self.source_table_ids,
            self.continuation_binding_ids,
            self.source_manifest_sha256s,
            self.crop_sha256s,
        )
        if any(len(item) != len(set(item)) for item in provenance):
            raise ValueError("source provenance values must be unique")
        if self.source_kind == "reviewed_semantic_node_label":
            if self.anchor_span_ids or not self.semantic_sidecar_sha256 or not self.semantic_node_id:
                raise ValueError("semantic node label requires sidecar/node provenance and no text anchor")
        elif not self.anchor_span_ids:
            raise ValueError("non-diagram source text requires an anchor")
        return self


class GenericSemanticEdge(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    layout_relation: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    figure_evidence_id: str = Field(min_length=1)
    semantic_sidecar_sha256: str = Field(pattern=_SHA)
    source_manifest_sha256: str = Field(pattern=_SHA)
    source_edge_contract_sha256: str = Field(pattern=_SHA)
    layout_only: Literal[True] = True
    visible_text_authorized: Literal[False] = False


class GenericTableCell(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    text: str
    evidence_id: str = Field(min_length=1)
    source_cell_contract_sha256: str = Field(pattern=_SHA)


class GenericTableContract(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    source_table_ids: tuple[str, ...] = Field(min_length=1)
    source_text_sha256: str = Field(pattern=_SHA)
    table_state: Literal["reviewed_grid", "source_text_only"]
    table_evidence_id: str | None = None
    row_count: int | None = Field(default=None, ge=1)
    column_count: int | None = Field(default=None, ge=1)
    cells: tuple[GenericTableCell, ...] = ()
    source_grid_contract_sha256: str | None = Field(default=None, pattern=_SHA)

    @model_validator(mode="after")
    def validate_table(self) -> "GenericTableContract":
        if self.table_state == "reviewed_grid":
            if not self.table_evidence_id or not self.row_count or not self.column_count or not self.cells or not self.source_grid_contract_sha256:
                raise ValueError("reviewed table grid requires dimensions, cells and source hash")
            coords = [(cell.row, cell.column) for cell in self.cells]
            if len(coords) != len(set(coords)):
                raise ValueError("table cell coordinates must be unique")
        elif any((self.table_evidence_id, self.row_count, self.column_count, self.cells, self.source_grid_contract_sha256)):
            raise ValueError("source-text-only table cannot invent grid metadata")
        return self


class GenericOrderedListItem(StrictModel):
    ordinal: int = Field(ge=1, le=20)
    marker: str = Field(min_length=1, max_length=1)
    text: str = Field(min_length=1)
    source_item_sha256: str = Field(pattern=_SHA)


class GenericOrderedListContract(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    source_text_sha256: str = Field(pattern=_SHA)
    preamble: str = ""
    items: tuple[GenericOrderedListItem, ...] = Field(min_length=1)
    source_list_contract_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_list(self) -> "GenericOrderedListContract":
        if tuple(item.ordinal for item in self.items) != tuple(range(1, len(self.items) + 1)):
            raise ValueError("ordered-list items must remain sequential")
        if tuple(item.marker for item in self.items) != MARKERS[: len(self.items)]:
            raise ValueError("ordered-list markers must remain complete")
        return self


class GenericTokenBinding(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    text_index: int = Field(ge=0)
    source_text_sha256: str = Field(pattern=_SHA)
    tokens: tuple[str, ...] = ()
    tokens_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_tokens(self) -> "GenericTokenBinding":
        expected = _canonical_hash(self.tokens)
        if self.tokens_sha256 != expected:
            raise ValueError("source token hash does not match token projection")
        return self


class GenericGeometry(StrictModel):
    width: Literal[1536] = OUTPUT_WIDTH
    height: Literal[1024] = OUTPUT_HEIGHT
    contract_kind: Literal[
        "table_grid", "ordered_list_rows", "diagram_nodes_edges", "constraint_blocks", "formula_blocks"
    ]
    element_count: int = Field(ge=1)
    reading_order: Literal["source_order"] = "source_order"
    stable_canvas: Literal[True] = True
    inferred_geometry: Literal[False] = False
    overlap_allowed: Literal[False] = False


class GenericCardContract(StrictModel):
    schema_version: Literal["step4_generic_contract_bundle_v1"] = CONTRACT_BUNDLE_VERSION
    job_id: str = Field(pattern=_JOB)
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_card_id: str = Field(pattern=_CARD)
    sample_id: str = Field(min_length=1)
    source_presentation: Literal["table", "diagram", "ordered_list", "constraint", "threshold", "formula"]
    render_presentation: Literal["table", "diagram", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    visible_texts: tuple[GenericVisibleText, ...] = Field(min_length=1)
    semantic_layout_edges: tuple[GenericSemanticEdge, ...] = ()
    diagram_node_ids: tuple[str, ...] = ()
    table_contracts: tuple[GenericTableContract, ...] = ()
    ordered_list_contracts: tuple[GenericOrderedListContract, ...] = ()
    token_bindings: tuple[GenericTokenBinding, ...] = ()
    geometry: GenericGeometry
    source_fixture_sha256: str = Field(pattern=_SHA)
    source_card_contract_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_card(self) -> "GenericCardContract":
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("Card Fact ids must be unique")
        visible_fact_ids = {item.fact_id for item in self.visible_texts}
        if visible_fact_ids != set(self.fact_ids):
            raise ValueError("visible text provenance must cover Card Facts exactly once")
        if self.render_presentation != "diagram" and len(self.visible_texts) != len(self.fact_ids):
            raise ValueError("non-diagram Cards must bind one visible text to each Fact")
        if self.source_presentation == "threshold" and self.render_presentation != "constraint":
            raise ValueError("threshold must normalize to the constraint renderer")
        if self.source_presentation != "threshold" and self.render_presentation != self.source_presentation:
            raise ValueError("source and render presentation diverged")
        if any(item.fact_id not in self.fact_ids for item in self.semantic_layout_edges):
            raise ValueError("semantic edge escaped its source Card")
        if self.render_presentation == "diagram":
            if not self.diagram_node_ids or not self.semantic_layout_edges:
                raise ValueError("diagram requires nodes and semantic layout edges")
            if len(self.diagram_node_ids) != len(set(self.diagram_node_ids)):
                raise ValueError("diagram node ids must be unique")
        elif self.diagram_node_ids or self.semantic_layout_edges:
            raise ValueError("non-diagram contract cannot carry diagram structure")
        if self.render_presentation == "table" and not self.table_contracts:
            raise ValueError("table requires a source table contract")
        if self.render_presentation != "table" and self.table_contracts:
            raise ValueError("non-table contract cannot carry table structure")
        if self.render_presentation == "ordered_list" and not self.ordered_list_contracts:
            raise ValueError("ordered-list requires a source list contract")
        if self.render_presentation != "ordered_list" and self.ordered_list_contracts:
            raise ValueError("non-list contract cannot carry list structure")
        if self.render_presentation in {"constraint", "formula"}:
            if len(self.token_bindings) != len(self.visible_texts):
                raise ValueError("constraint/formula token bindings must cover every source text")
        elif self.token_bindings:
            raise ValueError("only constraint/formula contracts may carry token bindings")
        return self
