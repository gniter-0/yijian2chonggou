"""Step 3 V3 source-owned card contracts.

This module is deliberately data-only.  It is the boundary between the
reviewed typed-evidence fixture and the compiler/response lanes; in
particular, ``threshold`` is a source presentation and is not a synonym for
the renderer's ``constraint`` presentation.
"""
from __future__ import annotations

from typing import Any, Final, Literal, TypeAlias

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from .admission_legacy_hash import canonical_hash_r10x as canonical_hash

HASH: Final = r"^[0-9a-f]{64}$"
ID: Final = r"^[a-z0-9][a-z0-9._:-]{2,255}$"
CONTRACT_ID: Final = r"^step3-card-contract:[0-9a-f]{24}$"
MODEL_INPUT_ID: Final = r"^step3-v3-model-input:[0-9a-f]{24}$"

PRESENTATIONS: Final[tuple[str, ...]] = (
    "diagram",
    "table",
    "ordered_list",
    "constraint",
    "threshold",
    "formula",
)
PresentationV3: TypeAlias = Literal["diagram", "table", "ordered_list", "constraint", "threshold", "formula"]
SourceRoleV3: TypeAlias = Literal[
    "diagram_node", "table_cell", "ordered_list_item", "constraint_token", "threshold_token", "formula_token"
]


class SourceProvenanceV3(StrictModel):
    schema_version: Literal["phase2r9_source_provenance_v3"] = "phase2r9_source_provenance_v3"
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
    def validate_anchors(self) -> "SourceProvenanceV3":
        if len(self.anchor_ids) != len(set(self.anchor_ids)):
            raise ValueError("V3 source anchor IDs must be unique")
        return self


class DisplayElementV3(StrictModel):
    schema_version: Literal["phase2r9_display_element_v3"] = "phase2r9_display_element_v3"
    element_id: str = Field(pattern=ID)
    text: str = Field(min_length=1)
    source_role: SourceRoleV3
    source_order: int = Field(ge=1)
    provenance: SourceProvenanceV3
    text_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_text_hash(self) -> "DisplayElementV3":
        from .admission_legacy_hash import text_hash

        if not self.text.strip():
            raise ValueError("V3 display element text must not be blank")
        if self.text_sha256 != text_hash(self.text):
            raise ValueError("V3 display element source text hash mismatch")
        return self


class TableCellRefV3(StrictModel):
    schema_version: Literal["phase2r9_table_cell_ref_v3"] = "phase2r9_table_cell_ref_v3"
    element_id: str = Field(pattern=ID)
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    reading_order: int = Field(ge=1)


class ListItemRefV3(StrictModel):
    schema_version: Literal["phase2r9_list_item_ref_v3"] = "phase2r9_list_item_ref_v3"
    element_id: str = Field(pattern=ID)
    order: int = Field(ge=1)
    marker: str = Field(min_length=1)


class TokenRefV3(StrictModel):
    schema_version: Literal["phase2r9_token_ref_v3"] = "phase2r9_token_ref_v3"
    element_id: str = Field(pattern=ID)
    token_order: int = Field(ge=1)


class DiagramNodeRefV3(StrictModel):
    schema_version: Literal["phase2r9_diagram_node_ref_v3"] = "phase2r9_diagram_node_ref_v3"
    element_id: str = Field(pattern=ID)
    reading_order: int = Field(ge=1)


class DiagramEdgeRefV3(StrictModel):
    schema_version: Literal["phase2r9_diagram_edge_ref_v3"] = "phase2r9_diagram_edge_ref_v3"
    edge_id: str = Field(pattern=ID)
    source_node_id: str = Field(pattern=ID)
    target_node_id: str = Field(pattern=ID)
    provenance: SourceProvenanceV3
    layout_only: Literal[True] = True
    visible_text_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_edge(self) -> "DiagramEdgeRefV3":
        if self.source_node_id == self.target_node_id:
            raise ValueError("V3 diagram edge cannot self-link")
        return self


class LayoutContractV3(StrictModel):
    schema_version: Literal["phase2r9_layout_contract_v3"] = "phase2r9_layout_contract_v3"
    presentation: PresentationV3
    reading_order: Literal["source_order"] = "source_order"
    table_rows: int | None = Field(default=None, ge=1)
    table_columns: int | None = Field(default=None, ge=1)
    table_cells: tuple[TableCellRefV3, ...] = ()
    list_items: tuple[ListItemRefV3, ...] = ()
    token_refs: tuple[TokenRefV3, ...] = ()
    constraint_blocks: tuple[TokenRefV3, ...] = ()
    threshold_blocks: tuple[TokenRefV3, ...] = ()
    diagram_nodes: tuple[DiagramNodeRefV3, ...] = ()
    diagram_edges: tuple[DiagramEdgeRefV3, ...] = ()

    @model_validator(mode="after")
    def validate_layout(self) -> "LayoutContractV3":
        active = {
            "table": bool(self.table_cells or self.table_rows or self.table_columns),
            "ordered_list": bool(self.list_items),
            "formula": bool(self.token_refs),
            "constraint": bool(self.constraint_blocks),
            "threshold": bool(self.threshold_blocks),
            "diagram": bool(self.diagram_nodes),
        }
        if not active[self.presentation]:
            raise ValueError("V3 presentation layout is incomplete")
        if sum(active.values()) != 1:
            raise ValueError("V3 layout mixes presentation types")
        if self.presentation == "table":
            if self.table_rows is None or self.table_columns is None:
                raise ValueError("V3 table layout requires dimensions")
            ids = [item.element_id for item in self.table_cells]
            orders = [item.reading_order for item in self.table_cells]
            if len(ids) != len(set(ids)) or orders != list(range(1, len(orders) + 1)):
                raise ValueError("V3 table cells must be unique and source ordered")
        elif self.presentation == "ordered_list":
            orders = [item.order for item in self.list_items]
            if orders != list(range(1, len(orders) + 1)):
                raise ValueError("V3 ordered-list layout must be contiguous")
        elif self.presentation in {"constraint", "threshold", "formula"}:
            refs = {
                "constraint": self.constraint_blocks,
                "threshold": self.threshold_blocks,
                "formula": self.token_refs,
            }[self.presentation]
            orders = [item.token_order for item in refs]
            if orders != list(range(1, len(orders) + 1)):
                raise ValueError("V3 token layout must be contiguous")
        else:
            ids = [item.element_id for item in self.diagram_nodes]
            if len(ids) != len(set(ids)) or [item.reading_order for item in self.diagram_nodes] != list(range(1, len(ids) + 1)):
                raise ValueError("V3 diagram nodes must be source ordered and unique")
            node_ids = set(ids)
            if any(edge.source_node_id not in node_ids or edge.target_node_id not in node_ids for edge in self.diagram_edges):
                raise ValueError("V3 diagram edge escaped source nodes")
        return self


class GeometryConstraintsV3(StrictModel):
    schema_version: Literal["phase2r9_geometry_constraints_v3"] = "phase2r9_geometry_constraints_v3"
    width: Literal[1536] = 1536
    height: Literal[1024] = 1024
    overlap_allowed: Literal[False] = False
    inferred_geometry: Literal[False] = False
    exact_source_text_instances: Literal[True] = True
    extra_text_allowed: Literal[False] = False
    watermark_allowed: Literal[False] = False
    branding_allowed: Literal[False] = False
    relation_text_allowed: Literal[False] = False


class Step3CardContractV3(StrictModel):
    schema_version: Literal["phase2r9_step3_card_contract_v3"] = "phase2r9_step3_card_contract_v3"
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=r"^candidate:[a-z0-9][a-z0-9._:-]{2,255}$")
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: PresentationV3
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    source_typed_contract_sha256: str = Field(pattern=HASH)
    display_elements: tuple[DisplayElementV3, ...] = Field(min_length=1)
    layout: LayoutContractV3
    geometry: GeometryConstraintsV3
    contract_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_card(self) -> "Step3CardContractV3":
        if self.layout.presentation != self.presentation:
            raise ValueError("V3 card/layout presentation mismatch")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("V3 Fact bindings must be unique")
        element_ids = [item.element_id for item in self.display_elements]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("V3 display elements must be unique")
        if {item.provenance.fact_id for item in self.display_elements} != set(self.fact_ids):
            raise ValueError("V3 display provenance must cover every Fact exactly once")
        if self.presentation == "table":
            layout_element_ids = tuple(item.element_id for item in self.layout.table_cells)
        elif self.presentation == "ordered_list":
            layout_element_ids = tuple(item.element_id for item in self.layout.list_items)
        elif self.presentation == "formula":
            layout_element_ids = tuple(item.element_id for item in self.layout.token_refs)
        elif self.presentation == "constraint":
            layout_element_ids = tuple(item.element_id for item in self.layout.constraint_blocks)
        elif self.presentation == "threshold":
            layout_element_ids = tuple(item.element_id for item in self.layout.threshold_blocks)
        else:
            layout_element_ids = tuple(item.element_id for item in self.layout.diagram_nodes)
        if layout_element_ids != tuple(element_ids):
            raise ValueError("V3 layout element IDs must exactly match display elements")
        expected_id = f"step3-card-contract:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'contract_id', 'contract_sha256'}))[:24]}"
        if self.contract_id != expected_id:
            raise ValueError("V3 card contract identity mismatch")
        expected_hash = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "contract_sha256"}))
        if self.contract_sha256 != expected_hash:
            raise ValueError("V3 card contract hash mismatch")
        expected_roles = {
            "diagram": "diagram_node",
            "table": "table_cell",
            "ordered_list": "ordered_list_item",
            "constraint": "constraint_token",
            "threshold": "threshold_token",
            "formula": "formula_token",
        }
        if any(item.source_role != expected_roles[self.presentation] for item in self.display_elements):
            raise ValueError("V3 display source role does not match presentation")
        return self


class ModelSourceBindingV3(StrictModel):
    schema_version: Literal["phase2r9_model_source_binding_v3"] = "phase2r9_model_source_binding_v3"
    subject_id: str = Field(pattern=ID)
    record_id: str = Field(pattern=ID)
    fact_id: str = Field(pattern=ID)
    evidence_id: str = Field(pattern=ID)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    page_ref_id: str = Field(pattern=ID)
    page: int = Field(ge=1)
    image_sha256: str = Field(pattern=HASH)
    source_manifest_sha256: str = Field(pattern=HASH)


class ModelVisibleElementV3(StrictModel):
    schema_version: Literal["phase2r9_model_visible_element_v3"] = "phase2r9_model_visible_element_v3"
    element_id: str = Field(pattern=ID)
    source_text: str = Field(min_length=1)
    source_order: int = Field(ge=1)
    source_role: SourceRoleV3
    text_sha256: str = Field(pattern=HASH)
    provenance: SourceProvenanceV3

    @model_validator(mode="after")
    def validate_text(self) -> "ModelVisibleElementV3":
        from .admission_legacy_hash import text_hash

        if self.text_sha256 != text_hash(self.source_text):
            raise ValueError("V3 model visible element text hash mismatch")
        return self


def validate_model_binding_consistency(
    source_binding: ModelSourceBindingV3,
    visible_elements: tuple[ModelVisibleElementV3, ...],
    fact_ids: tuple[str, ...],
) -> None:
    """Cross-check the shared authority fields on both model surfaces.

    Page and image bindings are intentionally element-scoped: a card may draw
    from more than one page/image.  The identity fields that are common to a
    card-level binding and each visible element, however, must not diverge.
    """

    if source_binding.fact_id not in fact_ids:
        raise ValueError("V3 model source binding Fact is not in card")
    common_fields = (
        "subject_id",
        "record_id",
        "fact_id",
        "evidence_id",
        "anchor_ids",
        "source_manifest_sha256",
    )
    for element in visible_elements:
        provenance = element.provenance
        if provenance.fact_id not in fact_ids:
            raise ValueError("V3 model element crossed Fact binding")
        if any(getattr(source_binding, field) != getattr(provenance, field) for field in common_fields):
            raise ValueError("V3 card/model source binding contradicts element provenance")


class ModelLayoutV3(StrictModel):
    schema_version: Literal["phase2r9_model_layout_v3"] = "phase2r9_model_layout_v3"
    presentation: PresentationV3
    typed_contract_sha256: str = Field(pattern=HASH)
    element_ids: tuple[str, ...] = Field(min_length=1)
    table_rows: int | None = Field(default=None, ge=1)
    table_columns: int | None = Field(default=None, ge=1)
    table_coordinates: tuple[TableCellRefV3, ...] = ()
    list_order: tuple[ListItemRefV3, ...] = ()
    token_order: tuple[TokenRefV3, ...] = ()
    constraint_blocks: tuple[TokenRefV3, ...] = ()
    threshold_blocks: tuple[TokenRefV3, ...] = ()
    diagram_nodes: tuple[DiagramNodeRefV3, ...] = ()
    diagram_edges: tuple[DiagramEdgeRefV3, ...] = ()

    @model_validator(mode="after")
    def validate_elements(self) -> "ModelLayoutV3":
        refs: tuple[Any, ...] = {
            "table": self.table_coordinates,
            "ordered_list": self.list_order,
            "constraint": self.constraint_blocks or self.token_order,
            "threshold": self.threshold_blocks,
            "formula": self.token_order,
            "diagram": self.diagram_nodes,
        }[self.presentation]
        if tuple(item.element_id for item in refs) != self.element_ids:
            raise ValueError("V3 model layout element order mismatch")
        if self.presentation == "threshold" and not self.threshold_blocks:
            raise ValueError("V3 threshold model layout requires threshold_blocks")
        return self


class ModelCardV3(StrictModel):
    schema_version: Literal["phase2r9_step3_model_input_v3"] = "phase2r9_step3_model_input_v3"
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=ID)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: PresentationV3
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    source_binding: ModelSourceBindingV3
    visible_elements: tuple[ModelVisibleElementV3, ...] = Field(min_length=1)
    layout: ModelLayoutV3
    geometry: GeometryConstraintsV3

    @model_validator(mode="after")
    def validate_card(self) -> "ModelCardV3":
        if self.presentation != self.layout.presentation:
            raise ValueError("V3 model presentation mismatch")
        if tuple(item.element_id for item in self.visible_elements) != self.layout.element_ids:
            raise ValueError("V3 model visible elements must match layout")
        validate_model_binding_consistency(self.source_binding, self.visible_elements, self.fact_ids)
        return self


class Step3ModelInputV3(StrictModel):
    schema_version: Literal["phase2r9_step3_model_input_v3"] = "phase2r9_step3_model_input_v3"
    model_input_id: str = Field(pattern=MODEL_INPUT_ID)
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    cards: tuple[ModelCardV3, ...] = Field(min_length=1)
    card_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_input(self) -> "Step3ModelInputV3":
        if self.card_count != len(self.cards):
            raise ValueError("V3 model input card count mismatch")
        if any(
            card.source_typed_fixture_file_sha256 != self.source_typed_fixture_file_sha256
            or card.source_typed_fixture_sha256 != self.source_typed_fixture_sha256
            for card in self.cards
        ):
            raise ValueError("V3 model input fixture binding mismatch")
        expected = f"step3-v3-model-input:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'model_input_id'}))[:24]}"
        if self.model_input_id != expected:
            raise ValueError("V3 model input identity mismatch")
        return self


__all__ = [
    "CONTRACT_ID",
    "GeometryConstraintsV3",
    "DiagramEdgeRefV3",
    "DiagramNodeRefV3",
    "DisplayElementV3",
    "ID",
    "LayoutContractV3",
    "ListItemRefV3",
    "ModelCardV3",
    "ModelLayoutV3",
    "ModelSourceBindingV3",
    "ModelVisibleElementV3",
    "PRESENTATIONS",
    "PresentationV3",
    "SourceProvenanceV3",
    "Step3CardContractV3",
    "Step3ModelInputV3",
    "TableCellRefV3",
    "TokenRefV3",
    "validate_model_binding_consistency",
]
