"""cm35 切分产物：step3 卡片 / 版面契约家族（层 2）。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from ...materials.schema import StrictModel
from .admission_legacy_hash import canonical_hash_r10x, text_hash
from .admission_fixtures_constants import (CONTRACT_ID, CONTRACT_VERSION, FORBIDDEN_TEXT, HASH, ID, SOURCE_CANDIDATE)


class SourceProvenance(StrictModel):
    schema_version: Literal["phase2r9_source_provenance_v1"] = "phase2r9_source_provenance_v1"
    subject_id: str = Field(pattern=ID)
    record_id: str = Field(pattern=ID)
    fact_id: str = Field(pattern=ID)
    evidence_id: str = Field(pattern=ID)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    page_ref_id: str = Field(pattern=ID)
    page: int = Field(ge=1)
    image_sha256: str = Field(pattern=HASH)
    source_manifest_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_ids(self) -> "SourceProvenance":
        if len(self.anchor_ids) != len(set(self.anchor_ids)):
            raise ValueError("source anchor IDs must be unique")
        return self


class DisplayElement(StrictModel):
    schema_version: Literal["phase2r9_display_element_v1"] = "phase2r9_display_element_v1"
    element_id: str = Field(pattern=ID)
    text: str = Field(min_length=1)
    source_role: Literal["diagram_node", "table_cell", "ordered_list_item", "constraint_token", "formula_token"]
    source_order: int = Field(ge=1)
    provenance: SourceProvenance
    text_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_text(self) -> "DisplayElement":
        if not self.text.strip() or any(marker in self.text.lower() for marker in FORBIDDEN_TEXT):
            raise ValueError("display element contains forbidden text")
        if self.text_sha256 != text_hash(self.text):
            raise ValueError("display element source text hash mismatch")
        return self


class TableCellRef(StrictModel):
    schema_version: Literal["phase2r9_table_cell_ref_v1"] = "phase2r9_table_cell_ref_v1"
    element_id: str = Field(pattern=ID)
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    reading_order: int = Field(ge=1)


class ListItemRef(StrictModel):
    schema_version: Literal["phase2r9_list_item_ref_v1"] = "phase2r9_list_item_ref_v1"
    element_id: str = Field(pattern=ID)
    order: int = Field(ge=1)
    marker: str = Field(min_length=1)


class TokenRef(StrictModel):
    schema_version: Literal["phase2r9_token_ref_v1"] = "phase2r9_token_ref_v1"
    element_id: str = Field(pattern=ID)
    token_order: int = Field(ge=1)


class DiagramNodeRef(StrictModel):
    schema_version: Literal["phase2r9_diagram_node_ref_v1"] = "phase2r9_diagram_node_ref_v1"
    element_id: str = Field(pattern=ID)
    reading_order: int = Field(ge=1)


class DiagramEdgeRef(StrictModel):
    schema_version: Literal["phase2r9_diagram_edge_ref_v1"] = "phase2r9_diagram_edge_ref_v1"
    edge_id: str = Field(pattern=ID)
    source_node_id: str = Field(pattern=ID)
    target_node_id: str = Field(pattern=ID)
    provenance: SourceProvenance
    layout_only: Literal[True] = True
    visible_text_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_edge(self) -> "DiagramEdgeRef":
        if self.source_node_id == self.target_node_id:
            raise ValueError("diagram edge cannot self-link")
        return self


class LayoutContract(StrictModel):
    schema_version: Literal["phase2r9_layout_contract_v1"] = "phase2r9_layout_contract_v1"
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    reading_order: Literal["source_order"] = "source_order"
    table_rows: int | None = Field(default=None, ge=1)
    table_columns: int | None = Field(default=None, ge=1)
    table_cells: tuple[TableCellRef, ...] = ()
    list_items: tuple[ListItemRef, ...] = ()
    token_refs: tuple[TokenRef, ...] = ()
    diagram_nodes: tuple[DiagramNodeRef, ...] = ()
    diagram_edges: tuple[DiagramEdgeRef, ...] = ()
    constraint_blocks: tuple[TokenRef, ...] = ()

    @model_validator(mode="after")
    def validate_layout(self) -> "LayoutContract":
        active = {
            "table": bool(self.table_cells or self.table_rows or self.table_columns),
            "ordered_list": bool(self.list_items),
            "formula": bool(self.token_refs),
            "constraint": bool(self.constraint_blocks),
            "diagram": bool(self.diagram_nodes),
        }
        if not active[self.presentation]:
            raise ValueError("presentation layout is incomplete")
        for name, enabled in active.items():
            if name != self.presentation and enabled:
                raise ValueError("layout contract mixed presentation types")
        if self.presentation == "table" and (self.table_rows is None or self.table_columns is None):
            raise ValueError("table layout requires dimensions")
        if self.presentation == "table":
            ids = [item.element_id for item in self.table_cells]
            orders = [item.reading_order for item in self.table_cells]
            if len(ids) != len(set(ids)) or orders != list(range(1, len(orders) + 1)):
                raise ValueError("table layout cells must be unique and source ordered")
        if self.presentation == "ordered_list":
            orders = [item.order for item in self.list_items]
            if orders != list(range(1, len(orders) + 1)):
                raise ValueError("ordered-list layout must be contiguous")
        if self.presentation in {"formula", "constraint"}:
            refs = self.token_refs if self.presentation == "formula" else self.constraint_blocks
            orders = [item.token_order for item in refs]
            if orders != list(range(1, len(orders) + 1)):
                raise ValueError("token layout must be contiguous")
        if self.presentation == "diagram":
            ids = [item.element_id for item in self.diagram_nodes]
            if len(ids) != len(set(ids)) or [item.reading_order for item in self.diagram_nodes] != list(range(1, len(ids) + 1)):
                raise ValueError("diagram nodes must be source ordered and unique")
            node_ids = set(ids)
            if any(edge.source_node_id not in node_ids or edge.target_node_id not in node_ids for edge in self.diagram_edges):
                raise ValueError("diagram edge escaped source nodes")
        return self


class GeometryConstraints(StrictModel):
    schema_version: Literal["phase2r9_geometry_constraints_v1"] = "phase2r9_geometry_constraints_v1"
    width: Literal[1536] = 1536
    height: Literal[1024] = 1024
    overlap_allowed: Literal[False] = False
    inferred_geometry: Literal[False] = False
    exact_source_text_instances: Literal[True] = True
    extra_text_allowed: Literal[False] = False
    watermark_allowed: Literal[False] = False
    branding_allowed: Literal[False] = False
    relation_text_allowed: Literal[False] = False


class Step3CardContract(StrictModel):
    schema_version: Literal["phase2r9_step3_card_contract_v1"] = CONTRACT_VERSION
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=SOURCE_CANDIDATE)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    source_fixture_sha256: str = Field(pattern=HASH)
    source_typed_contract_sha256: str = Field(pattern=HASH)
    display_elements: tuple[DisplayElement, ...] = Field(min_length=1)
    layout: LayoutContract
    geometry: GeometryConstraints
    contract_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_card(self) -> "Step3CardContract":
        if self.layout.presentation != self.presentation:
            raise ValueError("Card layout presentation mismatch")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("Card Fact bindings must be unique")
        element_ids = [item.element_id for item in self.display_elements]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("Card display elements must be exactly once")
        if {item.provenance.fact_id for item in self.display_elements} != set(self.fact_ids):
            raise ValueError("Card Fact binding must cover every Fact exactly once")
        layout_ids: set[str] = set()
        if self.presentation == "table":
            layout_ids = {item.element_id for item in self.layout.table_cells}
        elif self.presentation == "ordered_list":
            layout_ids = {item.element_id for item in self.layout.list_items}
        elif self.presentation == "formula":
            layout_ids = {item.element_id for item in self.layout.token_refs}
        elif self.presentation == "constraint":
            layout_ids = {item.element_id for item in self.layout.constraint_blocks}
        elif self.presentation == "diagram":
            layout_ids = {item.element_id for item in self.layout.diagram_nodes}
        if layout_ids != set(element_ids):
            raise ValueError("layout element binding is not exactly once")
        identity_payload = self.model_dump(mode="json", exclude={"schema_version", "contract_id", "contract_sha256"})
        expected_id = f"step3-card-contract:{canonical_hash_r10x(identity_payload)[:24]}"
        if self.contract_id != expected_id:
            raise ValueError("Card contract identity mismatch")
        if self.contract_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "contract_sha256"})):
            raise ValueError("Card contract hash mismatch")
        return self
