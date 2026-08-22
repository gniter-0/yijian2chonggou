"""Cross-bucket pure-data admission contracts (ruling: plan A).

Verbatim extractions from the legacy phase modules (r84/r810/r9/r98/r10/r22/
r2241/r2242/r319). schema_version literals, field shapes and self-hash
protocols are untouched; only three colliding FIXTURE_VERSION constant
identifiers gained phase prefixes and one r84. module qualifier was dropped
(identifier-level edits, invisible to serialization and hashing).
"""
from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from .admission_legacy_hash import canonical_hash, canonical_hash_r10x, text_hash


# --- phase2r84 typed-evidence kernel ---
HASH_PATTERN = r"^[0-9a-f]{64}$"
ID_PATTERN = r"^[a-z0-9][a-z0-9._:-]{2,255}$"
NOISE_MARKERS = (
    "水印",
    "二维码",
    "扫码",
    "微信",
    "QQ",
    "联系电话",
    "联系地址",
    "环球网校",
    "嗨学网",
    "荣胜教育",
    "www.",
)
SOURCE_PAGE_VERSION: Final = "phase2r84_source_page_binding_v1"
PROVENANCE_VERSION: Final = "phase2r84_typed_element_provenance_v1"
TYPED_TEXT_VERSION: Final = "phase2r84_typed_text_base_v1"
NODE_VERSION: Final = "phase2r84_diagram_node_v1"
EDGE_VERSION: Final = "phase2r84_diagram_edge_v1"
DIAGRAM_VERSION: Final = "phase2r84_diagram_contract_v1"
TABLE_CELL_VERSION: Final = "phase2r84_table_cell_v1"
TABLE_VERSION: Final = "phase2r84_table_contract_v1"
LIST_ITEM_VERSION: Final = "phase2r84_ordered_list_item_v1"
LIST_VERSION: Final = "phase2r84_ordered_list_contract_v1"
CONSTRAINT_TOKEN_VERSION: Final = "phase2r84_constraint_token_v1"
CONSTRAINT_VERSION: Final = "phase2r84_constraint_contract_v1"
FORMULA_TOKEN_VERSION: Final = "phase2r84_formula_token_v1"
FORMULA_VERSION: Final = "phase2r84_formula_contract_v1"
REVIEWED_CANDIDATE_VERSION: Final = "phase2r84_reviewed_typed_evidence_candidate_v1"

# --- phase2r810 (FIXTURE_VERSION renamed with phase prefix) ---
R810_FIXTURE_VERSION: Final = "phase2r810_reviewed_typed_evidence_fixture_v1"

# --- phase2r9 Step3 card-contract family ---
CONTRACT_VERSION: Final = "phase2r9_step3_card_contract_v1"
DRAFT_VERSION: Final = "phase2r9_step3_deterministic_draft_v1"
HASH = r"^[0-9a-f]{64}$"
ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"
SOURCE_CANDIDATE = r"^candidate:[a-z0-9][a-z0-9._:-]{2,255}$"
CONTRACT_ID = r"^step3-card-contract:[0-9a-f]{24}$"
FORBIDDEN_TEXT = ("watermark", "水印", "evidence-only", "evidence_only", "brand mark")

# --- phase2r98 deterministic draft / release bundle ---
DRAFT_CARD_VERSION: Final = "phase2r98_deterministic_draft_card_v1"
RELEASE_VERSION: Final = "phase2r98_deterministic_release_bundle_v1"

# --- phase2r10 reviewed Step 3 admission (FIXTURE_VERSION renamed) ---
DECISION_VERSION: Final = "phase2r10_step3_production_human_decision_v1"
REVIEWED_CARD_VERSION: Final = "phase2r10_reviewed_step3_card_v1"
R10_FIXTURE_VERSION: Final = "phase2r10_reviewed_step3_fixture_v1"

# --- phase2r103 empty refusal-form fixture (FIXTURE_VERSION renamed) ---
R103_FIXTURE_VERSION: Final = "phase2r103_reviewed_step3_fixture_v1"

# --- shared release-fixture patterns (phase2r22/r2241/r2242 values coincide) ---
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_FACT_ID_PATTERN = r"^fact:[0-9a-f]{24}$"
_CARD_ID_PATTERN = r"^v25-release-card:[0-9a-f]{24}$"
_ANCHOR_ID_PATTERN = r"^span:[0-9a-f]{24}$"

# --- phase2r2242 unified release fixture (FIXTURE_VERSION renamed; source
# fixture paths inlined with the exact legacy values) ---
R2242_FIXTURE_VERSION = "phase2r2242_step3_reviewed_release_fixture_v1"
UNIFIED_FIXTURE = Path(
    "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml"
)
SOURCE_PHASE224_FIXTURE = Path(
    "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r224_step3_reviewed_release.yaml"
)
SOURCE_PHASE2241_FIXTURE = Path(
    "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2241_reviewed_source_text_remediation.yaml"
)
REVIEWER = "codex_operator_phase2r2242_step3_release_merge_audit"
REVIEWED_AT_UTC = "2026-07-30T23:00:00Z"
EXPECTED_FACT_COUNT: Final = 28
EXPECTED_CARD_COUNT: Final = 16
EXPECTED_DISPLAY_TEXT_COUNT: Final = 52
EXPECTED_SEMANTIC_EDGE_COUNT: Final = 24
EXPECTED_ORIGINAL_CARD_COUNT = 8
EXPECTED_REMEDIATION_CARD_COUNT = 8

# --- phase2r104 admission run-evidence family (constants gain R104_ prefixes;
# string values untouched) ---
R104_PHASE: Final = "2R.10.4"
R104_FREEZE_VERSION: Final = "phase2r104_prior_freeze_v1"
R104_GATE_CHECK_VERSION: Final = "phase2r104_gate_check_v1"
R104_GATE_VERSION: Final = "phase2r104_offline_gate_v1"
R104_ACCEPTANCE_VERSION: Final = "phase2r104_acceptance_report_v1"

# --- phase2r319 Step 4 pilot handoff ---
HANDOFF_VERSION: Final = "step4_pilot_handoff_refresh_r319_v1"
SOURCE_R316_RUN = Path("data/semantic_llm_v2/phase2r316_step4_card3_reviewed_image_fixture_run1")
SOURCE_R316_HANDOFF = SOURCE_R316_RUN / "step4-pilot-handoff-refresh.json"
_SHA = r"^[0-9a-f]{64}$"
_CARD = r"^v25-release-card:[0-9a-f]{24}$"


class SourcePageBinding(StrictModel):
    schema_version: Literal["phase2r84_source_page_binding_v1"] = SOURCE_PAGE_VERSION
    page_ref_id: str = Field(pattern=ID_PATTERN)
    page: int = Field(ge=1)
    source_file: str = Field(min_length=1)
    image_path: str = Field(min_length=1)
    image_sha256: str = Field(pattern=HASH_PATTERN)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    page_sha256: str = Field(pattern=HASH_PATTERN)
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    page_binding_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_page(self) -> "SourcePageBinding":
        if self.page_binding_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"page_binding_sha256"})):
            raise ValueError("source page binding hash mismatch")
        return self


class TypedElementProvenance(StrictModel):
    schema_version: Literal["phase2r84_typed_element_provenance_v1"] = PROVENANCE_VERSION
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    page_ref_id: str = Field(pattern=ID_PATTERN)
    page: int = Field(ge=1)
    image_sha256: str = Field(pattern=HASH_PATTERN)
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    provenance_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_provenance(self) -> "TypedElementProvenance":
        if self.provenance_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"provenance_sha256"})):
            raise ValueError("typed element provenance hash mismatch")
        return self


class TypedTextBase(StrictModel):
    schema_version: Literal["phase2r84_typed_text_base_v1"] = TYPED_TEXT_VERSION
    element_id: str = Field(pattern=ID_PATTERN)
    text: str = Field(min_length=1)
    provenance: TypedElementProvenance
    human_confirmed: Literal[True] = True
    ocr_derived: Literal[False] = False
    assertion_derived: Literal[False] = False
    filename_derived: Literal[False] = False
    legacy_hint_derived: Literal[False] = False
    watermark: Literal[False] = False
    text_sha256: str = Field(pattern=HASH_PATTERN)
    element_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_text(self) -> "TypedTextBase":
        if not self.text.strip() or any(marker in self.text for marker in NOISE_MARKERS):
            raise ValueError("typed source text is empty or contains excluded noise")
        if self.text_sha256 != text_hash(self.text):
            raise ValueError("typed source text hash mismatch")
        if self.element_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"element_sha256"})):
            raise ValueError("typed source element hash mismatch")
        return self


class DiagramNode(TypedTextBase):
    schema_version: Literal["phase2r84_diagram_node_v1"] = NODE_VERSION
    source_role: Literal["diagram_node"] = "diagram_node"
    reading_order: int = Field(ge=1)


class DiagramEdge(StrictModel):
    schema_version: Literal["phase2r84_diagram_edge_v1"] = EDGE_VERSION
    edge_id: str = Field(pattern=ID_PATTERN)
    source_node_id: str = Field(pattern=ID_PATTERN)
    target_node_id: str = Field(pattern=ID_PATTERN)
    provenance: TypedElementProvenance
    source_role: Literal["diagram_edge"] = "diagram_edge"
    layout_only: Literal[True] = True
    edge_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_edge(self) -> "DiagramEdge":
        if self.source_node_id == self.target_node_id:
            raise ValueError("diagram edge cannot self-link")
        if self.edge_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"edge_sha256"})):
            raise ValueError("diagram edge hash mismatch")
        return self


class DiagramContract(StrictModel):
    schema_version: Literal["phase2r84_diagram_contract_v1"] = DIAGRAM_VERSION
    presentation: Literal["diagram"] = "diagram"
    nodes: tuple[DiagramNode, ...] = Field(min_length=1)
    edges: tuple[DiagramEdge, ...] = ()
    contract_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_graph(self) -> "DiagramContract":
        node_ids = [item.element_id for item in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("diagram node identities must be unique")
        if [item.reading_order for item in self.nodes] != list(range(1, len(self.nodes) + 1)):
            raise ValueError("diagram reading order must be contiguous")
        edge_ids = [item.edge_id for item in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("diagram edge identities must be unique")
        if any(edge.source_node_id not in node_ids or edge.target_node_id not in node_ids for edge in self.edges):
            raise ValueError("diagram edge references unknown node")
        if self.contract_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"contract_sha256"})):
            raise ValueError("diagram contract hash mismatch")
        return self


class TableCell(TypedTextBase):
    schema_version: Literal["phase2r84_table_cell_v1"] = TABLE_CELL_VERSION
    source_role: Literal["table_cell"] = "table_cell"
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    reading_order: int = Field(ge=1)


class TableContract(StrictModel):
    schema_version: Literal["phase2r84_table_contract_v1"] = TABLE_VERSION
    presentation: Literal["table"] = "table"
    rows: int = Field(gt=0)
    columns: int = Field(gt=0)
    cells: tuple[TableCell, ...] = Field(min_length=1)
    contract_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_grid(self) -> "TableContract":
        coordinates: set[tuple[int, int]] = set()
        orders: list[int] = []
        for cell in self.cells:
            if cell.row + cell.row_span - 1 > self.rows or cell.column + cell.column_span - 1 > self.columns:
                raise ValueError("table cell is outside grid")
            for row in range(cell.row, cell.row + cell.row_span):
                for column in range(cell.column, cell.column + cell.column_span):
                    if (row, column) in coordinates:
                        raise ValueError("table cells overlap")
                    coordinates.add((row, column))
            orders.append(cell.reading_order)
        if len(orders) != len(set(orders)) or sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("table reading order must be contiguous")
        if self.contract_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"contract_sha256"})):
            raise ValueError("table contract hash mismatch")
        return self


class OrderedListItem(TypedTextBase):
    schema_version: Literal["phase2r84_ordered_list_item_v1"] = LIST_ITEM_VERSION
    source_role: Literal["ordered_list_item"] = "ordered_list_item"
    order: int = Field(ge=1)
    marker: str = ""


class OrderedListContract(StrictModel):
    schema_version: Literal["phase2r84_ordered_list_contract_v1"] = LIST_VERSION
    presentation: Literal["ordered_list"] = "ordered_list"
    items: tuple[OrderedListItem, ...] = Field(min_length=1)
    continuation_binding_ids: tuple[str, ...] = ()
    contract_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_list(self) -> "OrderedListContract":
        orders = [item.order for item in self.items]
        if orders != list(range(1, len(self.items) + 1)):
            raise ValueError("ordered-list order must be contiguous")
        if len({item.element_id for item in self.items}) != len(self.items):
            raise ValueError("ordered-list item identities must be unique")
        if self.continuation_binding_ids:
            raise ValueError("implicit cross-page continuation is not admitted")
        if self.contract_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"contract_sha256"})):
            raise ValueError("ordered-list contract hash mismatch")
        return self


class ConstraintToken(TypedTextBase):
    schema_version: Literal["phase2r84_constraint_token_v1"] = CONSTRAINT_TOKEN_VERSION
    source_role: Literal["constraint_token"] = "constraint_token"
    token_order: int = Field(ge=1)


class ConstraintContract(StrictModel):
    schema_version: Literal["phase2r84_constraint_contract_v1"] = CONSTRAINT_VERSION
    presentation: Literal["constraint"] = "constraint"
    tokens: tuple[ConstraintToken, ...] = Field(min_length=1)
    contract_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_constraint(self) -> "ConstraintContract":
        orders = [item.token_order for item in self.tokens]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("constraint token order must be contiguous")
        if self.contract_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"contract_sha256"})):
            raise ValueError("constraint contract hash mismatch")
        return self


class FormulaToken(TypedTextBase):
    schema_version: Literal["phase2r84_formula_token_v1"] = FORMULA_TOKEN_VERSION
    source_role: Literal["formula_token"] = "formula_token"
    token_order: int = Field(ge=1)


class FormulaContract(StrictModel):
    schema_version: Literal["phase2r84_formula_contract_v1"] = FORMULA_VERSION
    presentation: Literal["formula"] = "formula"
    tokens: tuple[FormulaToken, ...] = Field(min_length=1)
    contract_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_formula(self) -> "FormulaContract":
        orders = [item.token_order for item in self.tokens]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("formula token order must be contiguous")
        if self.contract_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"contract_sha256"})):
            raise ValueError("formula contract hash mismatch")
        return self


TypedContract = Annotated[
    DiagramContract | TableContract | OrderedListContract | ConstraintContract | FormulaContract,
    Field(discriminator="presentation"),
]


class ReviewedCandidate(StrictModel):
    schema_version: Literal["phase2r84_reviewed_typed_evidence_candidate_v1"] = REVIEWED_CANDIDATE_VERSION
    candidate_id: str = Field(pattern=ID_PATTERN)
    queue_item_id: str = Field(pattern=ID_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    pages: tuple[SourcePageBinding, ...] = Field(min_length=1)
    typed_contract: TypedContract
    typed_contract_sha256: str = Field(pattern=HASH_PATTERN)
    source_decision_id: str = Field(pattern=ID_PATTERN)
    reviewer: str = Field(min_length=1)
    reviewed_candidate: Literal[True] = True
    fixture_authorized: Literal[True] = True
    candidate_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_candidate(self) -> "ReviewedCandidate":
        if self.typed_contract_sha256 != canonical_hash(self.typed_contract.model_dump(mode="json")):
            raise ValueError("reviewed candidate typed contract hash mismatch")
        if self.typed_contract.presentation != self.presentation:
            raise ValueError("reviewed candidate presentation mismatch")
        if any(page.source_manifest_sha256 != self.source_manifest_sha256 for page in self.pages):
            raise ValueError("reviewed candidate crossed source manifest")
        if self.candidate_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"candidate_sha256"})):
            raise ValueError("reviewed candidate hash mismatch")
        return self


class ReviewedTypedEvidenceFixture(StrictModel):
    schema_version: Literal["phase2r810_reviewed_typed_evidence_fixture_v1"] = R810_FIXTURE_VERSION
    r89_admission_bundle_sha256: str = Field(pattern=HASH_PATTERN)
    authorization_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    exported_r84_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    r84_fixture_sha256: str = Field(pattern=HASH_PATTERN)
    candidates: tuple[ReviewedCandidate, ...] = Field(min_length=1)
    reviewed_candidate_count: int = Field(ge=0)
    fixture_authorized: Literal[True] = True
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    fixture_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedTypedEvidenceFixture":
        if self.reviewed_candidate_count != len(self.candidates) or not self.candidates:
            raise ValueError("R8.10 reviewed fixture count mismatch")
        if len({item.candidate_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("R8.10 reviewed fixture candidate identities must be unique")
        if self.fixture_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"fixture_sha256"})):
            raise ValueError("R8.10 reviewed fixture hash mismatch")
        return self


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


class FakeArtifact(StrictModel):
    schema_version: Literal["phase2r9_step3_deterministic_draft_v1"] = DRAFT_VERSION
    contract_id: str = Field(pattern=CONTRACT_ID)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    rendered_element_ids: tuple[str, ...] = Field(min_length=1)
    rendered_source_texts: tuple[str, ...] = Field(min_length=1)
    relation_texts: tuple[str, ...] = ()
    extra_texts: tuple[str, ...] = ()
    materialized: Literal[False] = False
    real_image: Literal[False] = False
    artifact_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_artifact(self) -> "FakeArtifact":
        if self.relation_texts or self.extra_texts:
            raise ValueError("deterministic draft contains non-source text")
        if len(self.rendered_element_ids) != len(set(self.rendered_element_ids)):
            raise ValueError("deterministic draft element IDs must be unique")
        if self.artifact_sha256 != canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "artifact_sha256"})):
            raise ValueError("deterministic draft hash mismatch")
        return self


class DraftBundle(StrictModel):
    schema_version: Literal["phase2r9_step3_deterministic_draft_v1"] = DRAFT_VERSION
    source_fixture_sha256: str = Field(pattern=HASH)
    artifacts: tuple[FakeArtifact, ...] = Field(min_length=1)
    artifact_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_bundle(self) -> "DraftBundle":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("draft artifact count mismatch")
        return self


class DeterministicDraftCard(StrictModel):
    schema_version: Literal["phase2r98_deterministic_draft_card_v1"] = DRAFT_CARD_VERSION
    contract: Step3CardContract
    artifact: FakeArtifact
    draft_state: Literal["draft_manual_review"] = "draft_manual_review"
    reviewed_fixture_created: Literal[False] = False

    @model_validator(mode="after")
    def validate_draft_card(self) -> "DeterministicDraftCard":
        if self.artifact.contract_id != self.contract.contract_id:
            raise ValueError("deterministic artifact was grouped with another Card")
        if self.artifact.presentation != self.contract.presentation:
            raise ValueError("deterministic artifact presentation mismatch")
        if self.artifact.rendered_element_ids != tuple(item.element_id for item in self.contract.display_elements):
            raise ValueError("deterministic artifact element IDs are not exactly once")
        if self.artifact.rendered_source_texts != tuple(item.text for item in self.contract.display_elements):
            raise ValueError("deterministic artifact source text changed or truncated")
        return self


class DeterministicReleaseBundle(StrictModel):
    schema_version: Literal["phase2r98_deterministic_release_bundle_v1"] = RELEASE_VERSION
    source_fixture_sha256: str = Field(pattern=HASH)
    source_fixture_path: str = Field(min_length=1)
    r9_draft_sha256: str = Field(pattern=HASH)
    cards: tuple[DeterministicDraftCard, ...] = Field(min_length=1)
    card_count: int = Field(ge=1)
    draft_authority: Literal["local_deterministic_compiler"] = "local_deterministic_compiler"
    reviewed_fixture_created: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    release_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_bundle(self) -> "DeterministicReleaseBundle":
        if self.card_count != len(self.cards):
            raise ValueError("deterministic release Card count mismatch")
        ids = tuple(item.contract.contract_id for item in self.cards)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Cards are fail-closed")
        candidates = tuple(item.contract.source_candidate_id for item in self.cards)
        if len(candidates) != len(set(candidates)):
            raise ValueError("cross-sample grouping is fail-closed")
        if any(item.contract.source_fixture_sha256 != self.source_fixture_sha256 for item in self.cards):
            raise ValueError("source fixture provenance mismatch")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "release_sha256"}))
        if self.release_sha256 != expected:
            raise ValueError("deterministic release hash mismatch")
        return self


class Step3HumanDecision(StrictModel):
    schema_version: Literal["phase2r10_step3_production_human_decision_v1"] = DECISION_VERSION
    decision_id: str = Field(pattern=r"^step3-human-decision:[0-9a-f]{24}$")
    queue_item_id: str = Field(pattern=r"^step3-review-queue:[0-9a-f]{24}$")
    queue_sha256: str = Field(pattern=HASH)
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=ID)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    source_texts: tuple[str, ...] = Field(min_length=1)
    typed_contract_sha256: str = Field(pattern=HASH)
    provenance_sha256: str = Field(pattern=HASH)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    geometry_sha256: str = Field(pattern=HASH)
    disposition: Literal["reviewed_candidate", "manual_review", "blocked"]
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    source_text_confirmed: bool = False
    fact_card_confirmed: bool = False
    evidence_anchor_confirmed: bool = False
    typed_contract_confirmed: bool = False
    provenance_confirmed: bool = False
    presentation_confirmed: bool = False
    geometry_confirmed: bool = False
    explicit_human_authorization: bool = False
    browser_demo: Literal[False] = False
    decision_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_decision(self) -> "Step3HumanDecision":
        if self.disposition == "reviewed_candidate" and not all((
            self.source_text_confirmed, self.fact_card_confirmed, self.evidence_anchor_confirmed,
            self.typed_contract_confirmed, self.provenance_confirmed, self.presentation_confirmed,
            self.geometry_confirmed, self.explicit_human_authorization,
        )):
            raise ValueError("reviewed Step 3 decision requires all confirmations and explicit human authorization")
        if self.disposition != "reviewed_candidate" and self.explicit_human_authorization:
            raise ValueError("non-reviewed decision cannot authorize a fixture")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "decision_sha256"}))
        if self.decision_sha256 != expected:
            raise ValueError("Step 3 decision hash mismatch")
        return self


class ReviewedStep3Card(StrictModel):
    schema_version: Literal["phase2r10_reviewed_step3_card_v1"] = REVIEWED_CARD_VERSION
    queue_item_id: str = Field(pattern=r"^step3-review-queue:[0-9a-f]{24}$")
    contract: Step3CardContract
    artifact: DeterministicDraftCard
    decision: Step3HumanDecision
    reviewed_state: Literal["reviewed_step3"] = "reviewed_step3"
    reviewed_card_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_reviewed_card(self) -> "ReviewedStep3Card":
        if self.decision.disposition != "reviewed_candidate":
            raise ValueError("only explicitly reviewed decisions may enter a reviewed Step 3 fixture")
        if self.decision.contract_id != self.contract.contract_id or self.artifact.contract.contract_id != self.contract.contract_id:
            raise ValueError("reviewed fixture Card grouping mismatch")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "reviewed_card_sha256"}))
        if self.reviewed_card_sha256 != expected:
            raise ValueError("reviewed Step 3 Card hash mismatch")
        return self


class ReviewedStep3Fixture(StrictModel):
    schema_version: Literal["phase2r10_reviewed_step3_fixture_v1"] = R10_FIXTURE_VERSION
    source_fixture_sha256: str = Field(pattern=HASH)
    deterministic_release_sha256: str = Field(pattern=HASH)
    decision_manifest_sha256: str = Field(pattern=HASH)
    cards: tuple[ReviewedStep3Card, ...] = ()
    reviewed_card_count: int = Field(ge=0)
    fixture_authorized: bool
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    fixture_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedStep3Fixture":
        if self.reviewed_card_count != len(self.cards):
            raise ValueError("reviewed Step 3 fixture count mismatch")
        if self.fixture_authorized != bool(self.cards):
            raise ValueError("empty Step 3 fixture cannot be authorized")
        if len({item.contract.contract_id for item in self.cards}) != self.reviewed_card_count:
            raise ValueError("reviewed Step 3 fixture duplicate Card")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "fixture_sha256"}))
        if self.fixture_sha256 != expected:
            raise ValueError("reviewed Step 3 fixture hash mismatch")
        return self


class ReviewedStep3FixtureR103(StrictModel):
    schema_version: Literal["phase2r103_reviewed_step3_fixture_v1"] = R103_FIXTURE_VERSION
    source_raw_draft_sha256: str = Field(pattern=HASH)
    source_queue_sha256: str = Field(pattern=HASH)
    source_typed_evidence_sha256: str = Field(pattern=HASH)
    reviewed_queue_item_ids: tuple[str, ...] = ()
    reviewed_card_count: Literal[0] = 0
    fixture_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    fixture_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ReviewedStep3FixtureR103":
        if self.reviewed_queue_item_ids:
            raise ValueError("R10.3 cannot create a reviewed fixture without an explicit human production manifest")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "fixture_sha256"}))
        if self.fixture_sha256 != expected:
            raise ValueError("R10.3 reviewed fixture hash mismatch")
        return self


class Step3SourceOwnedSemanticEdgeAudit(StrictModel):
    fact_id: str = Field(pattern=_FACT_ID_PATTERN)
    source_node_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    basis: str = Field(min_length=12)
    confidence: float = Field(ge=0.0, le=1.0)
    figure_evidence_id: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)


class ExtractionMode(StrEnum):
    EXACT = "exact"
    NORMALIZED_EXACT = "normalized_exact"
    CROP_CORRECTED = "crop_corrected"
    SUBSTRING_EXCLUDING_EXAM_TAG = "substring_excluding_exam_tag"


class RemediationEvidenceBinding(StrictModel):
    evidence_id: str = Field(pattern=r"^ev:[0-9a-f]{24}$")
    selected_text: str = Field(min_length=1)
    anchor_span_ids: tuple[str, ...] = ()
    extraction_mode: ExtractionMode
    manual_visual_confirmation: bool = False
    review_basis: str = Field(min_length=24, max_length=1200)

    @model_validator(mode="after")
    def validate_binding(self) -> "RemediationEvidenceBinding":
        if len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("remediation anchor spans must be unique")
        if any(not re.fullmatch(_ANCHOR_ID_PATTERN, item) for item in self.anchor_span_ids):
            raise ValueError("remediation anchor span id is malformed")
        visual_modes = {ExtractionMode.CROP_CORRECTED, ExtractionMode.SUBSTRING_EXCLUDING_EXAM_TAG}
        if (self.extraction_mode in visual_modes) != self.manual_visual_confirmation:
            raise ValueError("visual extraction modes require explicit manual confirmation")
        if not self.anchor_span_ids and self.extraction_mode != ExtractionMode.SUBSTRING_EXCLUDING_EXAM_TAG:
            raise ValueError("only an explicitly confirmed exam-tag suffix may remain unanchored")
        return self


class RemediationTableCell(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    evidence_id: str = Field(pattern=r"^ev:[0-9a-f]{24}$")
    text: str = Field(min_length=1)
    extraction_mode: Literal["normalized_exact", "crop_corrected"] = "normalized_exact"
    manual_visual_confirmation: bool = False

    @model_validator(mode="after")
    def validate_cell(self) -> "RemediationTableCell":
        if (self.extraction_mode == "crop_corrected") != self.manual_visual_confirmation:
            raise ValueError("OCR-corrected table cells require explicit visual confirmation")
        return self


class RemediationTableGrid(StrictModel):
    table_evidence_id: str = Field(pattern=r"^ev:[0-9a-f]{24}$")
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    cell_count: int = Field(ge=1)
    cells: tuple[RemediationTableCell, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_grid(self) -> "RemediationTableGrid":
        if self.cell_count != len(self.cells):
            raise ValueError("table grid cell count must match cells")
        coordinates = {(item.row, item.column) for item in self.cells}
        if len(coordinates) != len(self.cells):
            raise ValueError("table grid coordinates must be unique")
        if any(item.row >= self.row_count or item.column >= self.column_count for item in self.cells):
            raise ValueError("table grid cell lies outside declared dimensions")
        if len({item.evidence_id for item in self.cells}) != len(self.cells):
            raise ValueError("table grid cell Evidence ids must be unique")
        return self


class Step3ReviewedSourceTier(StrEnum):
    PHASE224 = "phase2r224_reviewed"
    PHASE2241 = "phase2r2241_remediation"


class Step3MergedSourceOwnedDisplayText(StrictModel):
    fact_id: str = Field(pattern=_FACT_ID_PATTERN)
    text: str = Field(min_length=1)
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
    semantic_sidecar_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    semantic_node_id: str | None = None
    source_manifest_sha256s: tuple[str, ...] = ()
    crop_sha256s: tuple[str, ...] = ()
    evidence_bindings: tuple[RemediationEvidenceBinding, ...] = ()
    table_grid: RemediationTableGrid | None = None
    remediation_review_basis: str | None = Field(default=None, min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_display(self) -> "Step3MergedSourceOwnedDisplayText":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("merged display Evidence ids must be unique")
        if len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("merged display anchor spans must be unique")
        if self.source_kind == "reviewed_semantic_node_label":
            if self.anchor_span_ids or not self.semantic_sidecar_sha256 or not self.semantic_node_id:
                raise ValueError("semantic display text requires sidecar/node provenance and no text anchor")
            if self.source_manifest_sha256s or self.crop_sha256s or self.evidence_bindings or self.table_grid:
                raise ValueError("Phase 2R.2.4 semantic display cannot carry remediation provenance")
        elif self.source_kind == "reviewed_fact_assertion":
            if not self.anchor_span_ids or self.semantic_sidecar_sha256 or self.semantic_node_id:
                raise ValueError("reviewed Fact display requires anchors and no semantic-node metadata")
            if self.source_manifest_sha256s or self.crop_sha256s or self.evidence_bindings or self.table_grid:
                raise ValueError("Phase 2R.2.4 Fact display cannot carry remediation provenance")
        else:
            if not self.anchor_span_ids or not self.source_manifest_sha256s or not self.remediation_review_basis:
                raise ValueError("remediation display requires anchors, source manifest and review basis")
            if self.semantic_sidecar_sha256 or self.semantic_node_id:
                raise ValueError("remediation display cannot synthesize semantic-node provenance")
            if self.source_kind == "reviewed_remediation_table_grid":
                if self.table_grid is None or not self.complete_table_grid_verified or not self.source_table_ids:
                    raise ValueError("remediation table display requires complete grid provenance")
            elif self.table_grid is not None:
                raise ValueError("non-table remediation display cannot carry a table grid")
        return self


class Step3MergedReviewedReleaseCard(StrictModel):
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_tier: Step3ReviewedSourceTier
    source_fixture_path: str = Field(min_length=1)
    source_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_reviewed_card_id: str = Field(min_length=24)
    source_card_id: str = Field(pattern=_CARD_ID_PATTERN)
    source_projection_id: str = Field(min_length=24)
    source_draft_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_step3_request_id: str | None = None
    source_step3_response_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    sample_id: str = Field(min_length=1)
    canonical_title: str = Field(min_length=2, max_length=64)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: str = Field(min_length=2, max_length=32)
    image_prompt: str = Field(min_length=24)
    expected_texts: tuple[str, ...] = Field(min_length=1)
    layout_description: str = Field(min_length=12)
    display_text_provenance: tuple[Step3MergedSourceOwnedDisplayText, ...] = Field(min_length=1)
    semantic_edge_provenance: tuple[Step3SourceOwnedSemanticEdgeAudit, ...] = ()
    review_state: Literal["reviewed"] = "reviewed"
    review_basis: str = Field(min_length=32, max_length=2000)

    @model_validator(mode="after")
    def validate_card(self) -> "Step3MergedReviewedReleaseCard":
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("merged Card Fact ids must be unique")
        if {item.fact_id for item in self.display_text_provenance} != set(self.fact_ids):
            raise ValueError("merged display provenance must cover every Card Fact")
        expected = tuple(item.text for item in self.display_text_provenance)
        if self.expected_texts != expected:
            raise ValueError("merged expected texts must exactly equal source-owned display texts")
        if not all(text in self.image_prompt for text in expected):
            raise ValueError("merged image prompt must contain every source-owned display text")
        if any(item.fact_id not in self.fact_ids for item in self.semantic_edge_provenance):
            raise ValueError("semantic edge must bind a Fact in the same Card")
        if self.source_tier == Step3ReviewedSourceTier.PHASE224:
            if not self.source_step3_request_id or not self.source_step3_response_sha256:
                raise ValueError("Phase 2R.2.4 Card must preserve real Step 3 request provenance")
        elif self.source_step3_request_id is not None or self.source_step3_response_sha256 is not None:
            raise ValueError("remediation Card must not claim a new real Step 3 request")
        return self


class Step3UnifiedReviewedReleaseFixture(StrictModel):
    schema_version: str = R2242_FIXTURE_VERSION
    fixture_id: str = "gonglu_phase2r2242_step3_reviewed_release"
    source_phase224_fixture_path: str = SOURCE_PHASE224_FIXTURE.as_posix()
    source_phase224_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_phase2241_fixture_path: str = SOURCE_PHASE2241_FIXTURE.as_posix()
    source_phase2241_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewed_card_count: int = EXPECTED_CARD_COUNT
    reviewed_fact_count: int = EXPECTED_FACT_COUNT
    source_owned_display_text_count: int = EXPECTED_DISPLAY_TEXT_COUNT
    semantic_edge_count: int = EXPECTED_SEMANTIC_EDGE_COUNT
    cards: tuple[Step3MergedReviewedReleaseCard, ...]
    reviewer: str = REVIEWER
    reviewed_at_utc: str = Field(default=REVIEWED_AT_UTC, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    review_basis: str = Field(min_length=48, max_length=2000)

    @model_validator(mode="after")
    def validate_fixture(self) -> "Step3UnifiedReviewedReleaseFixture":
        if self.schema_version != R2242_FIXTURE_VERSION:
            raise ValueError("unified fixture schema version must remain bound to Phase 2R.2.4.2")
        if (
            self.reviewed_card_count != EXPECTED_CARD_COUNT
            or self.reviewed_fact_count != EXPECTED_FACT_COUNT
            or self.source_owned_display_text_count != EXPECTED_DISPLAY_TEXT_COUNT
            or self.semantic_edge_count != EXPECTED_SEMANTIC_EDGE_COUNT
        ):
            raise ValueError("unified fixture declared totals must remain exact")
        indices = tuple(item.card_index for item in self.cards)
        if indices != tuple(range(1, EXPECTED_CARD_COUNT + 1)):
            raise ValueError("unified fixture Cards must be ordered at indices 1..16")
        fact_ids = [fact_id for card in self.cards for fact_id in card.fact_ids]
        if len(fact_ids) != EXPECTED_FACT_COUNT or len(fact_ids) != len(set(fact_ids)):
            raise ValueError("unified fixture must cover 28 Facts exactly once")
        if len(self.cards) != EXPECTED_CARD_COUNT or len({item.source_card_id for item in self.cards}) != EXPECTED_CARD_COUNT:
            raise ValueError("unified fixture must cover 16 source Cards exactly once")
        tier_counts = {tier: sum(item.source_tier == tier for item in self.cards) for tier in Step3ReviewedSourceTier}
        if tier_counts != {
            Step3ReviewedSourceTier.PHASE224: EXPECTED_ORIGINAL_CARD_COUNT,
            Step3ReviewedSourceTier.PHASE2241: EXPECTED_REMEDIATION_CARD_COUNT,
        }:
            raise ValueError("unified fixture must retain the 8+8 reviewed source tiers")
        display_count = sum(len(item.display_text_provenance) for item in self.cards)
        edge_count = sum(len(item.semantic_edge_provenance) for item in self.cards)
        if display_count != EXPECTED_DISPLAY_TEXT_COUNT or edge_count != EXPECTED_SEMANTIC_EDGE_COUNT:
            raise ValueError("unified fixture display-text/semantic-edge totals do not close")
        return self


class FrozenArtifact(StrictModel):
    schema_version: Literal["phase2r104_prior_freeze_v1"] = R104_FREEZE_VERSION
    path: str = Field(min_length=1)
    role: Literal["r102_run", "r103_run", "code", "schema", "report", "source_queue"]
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=HASH)


class PriorFreeze(StrictModel):
    schema_version: Literal["phase2r104_prior_freeze_v1"] = R104_FREEZE_VERSION
    phase: Literal["2R.10.4"] = R104_PHASE
    artifacts: tuple[FrozenArtifact, ...] = Field(min_length=1)
    artifact_count: int = Field(gt=0)
    aggregate_sha256: str = Field(pattern=HASH)
    r102_inputs_unchanged: Literal[True] = True
    r103_inputs_unchanged: Literal[True] = True
    freeze_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_freeze(self) -> "PriorFreeze":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("R10.4 frozen artifact count mismatch")
        if len({item.path for item in self.artifacts}) != self.artifact_count:
            raise ValueError("R10.4 frozen artifact paths must be unique")
        if self.aggregate_sha256 != canonical_hash_r10x(self.artifacts):
            raise ValueError("R10.4 frozen artifact aggregate mismatch")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "freeze_sha256"}))
        if self.freeze_sha256 != expected:
            raise ValueError("R10.4 freeze hash mismatch")
        return self


class GateCheck(StrictModel):
    schema_version: Literal["phase2r104_gate_check_v1"] = R104_GATE_CHECK_VERSION
    check_id: str = Field(min_length=3)
    passed: bool
    detail: str = Field(min_length=3)


class OfflineGate(StrictModel):
    schema_version: Literal["phase2r104_offline_gate_v1"] = R104_GATE_VERSION
    phase: Literal["2R.10.4"] = R104_PHASE
    valid: bool
    frozen_inputs_valid: bool
    raw_manifest_valid: bool
    prior_acceptance_valid: bool
    source_chain_valid: bool
    explicit_authorization_valid: bool
    exactly_once_valid: bool
    reviewed_fixture_valid: bool
    strict_schema_valid: bool
    regressions_fail_closed: bool
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    reviewed_fixture_created: Literal[True] = True
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "OfflineGate":
        expected_valid = all(item.passed for item in self.checks) and not self.failure_codes
        if self.valid != expected_valid:
            raise ValueError("R10.4 offline Gate validity mismatch")
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
        if self.gate_sha256 != expected:
            raise ValueError("R10.4 offline Gate hash mismatch")
        return self


class JUnitReceipt(StrictModel):
    schema_version: Literal["phase2r104_acceptance_report_v1"] = R104_ACCEPTANCE_VERSION
    label: Literal["targeted", "full"]
    sha256: str = Field(pattern=HASH)
    tests: int = Field(ge=1)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class AcceptanceReport(StrictModel):
    schema_version: Literal["phase2r104_acceptance_report_v1"] = R104_ACCEPTANCE_VERSION
    phase: Literal["2R.10.4"] = R104_PHASE
    accepted: bool
    report_sha256: str = Field(pattern=HASH)
    receipts: tuple[JUnitReceipt, JUnitReceipt]
    reviewed_fixture_authorized: Literal[True] = True
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    acceptance_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_acceptance(self) -> "AcceptanceReport":
        expected = canonical_hash_r10x(self.model_dump(mode="json", exclude={"schema_version", "acceptance_sha256"}))
        if self.acceptance_sha256 != expected:
            raise ValueError("R10.4 acceptance hash mismatch")
        return self


class Step4ReviewedImageReferenceR319(StrictModel):
    card_index: Literal[2, 3, 4, 6]
    source_card_id: str = Field(pattern=_CARD)
    fixture_id: str = Field(min_length=32)
    fixture_path: str = Field(min_length=10)
    fixture_sha256: str = Field(pattern=_SHA)
    image_path: str = Field(min_length=10)
    image_sha256: str = Field(pattern=_SHA)
    presentation: Literal["diagram", "ordered_list", "constraint", "table"]
    render_contract_kind: Literal[
        "diagram_connector_topology", "ordered_list_exact_eight_rows", "constraint_exact_five_blocks", "complete_table_grid"
    ]

    @model_validator(mode="after")
    def validate_reference(self) -> "Step4ReviewedImageReferenceR319":
        expected = {
            2: ("diagram", "diagram_connector_topology"),
            3: ("ordered_list", "ordered_list_exact_eight_rows"),
            4: ("constraint", "constraint_exact_five_blocks"),
            6: ("table", "complete_table_grid"),
        }
        if (self.presentation, self.render_contract_kind) != expected[self.card_index]:
            raise ValueError("R3.19 contract reference mismatch")
        return self


class Step4PilotCardDispositionR319(StrictModel):
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_card_id: str = Field(pattern=_CARD)
    sample_id: str = Field(min_length=1)
    presentation: str = Field(min_length=2, max_length=32)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    source_owned_display_text_count: int = Field(ge=1)
    semantic_edge_count: int = Field(ge=0)
    disposition: Literal["reviewed", "ungenerated"]
    reviewed_image_fixture_id: str | None = None
    reviewed_image_fixture_path: str | None = None
    reviewed_image_fixture_sha256: str | None = Field(default=None, pattern=_SHA)
    render_contract_kind: str | None = None
    render_contract_reused_from_other_card: Literal[False] = False
    generation_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_disposition(self) -> "Step4PilotCardDispositionR319":
        reviewed = self.card_index in {2, 3, 4, 6}
        refs = (
            self.reviewed_image_fixture_id,
            self.reviewed_image_fixture_path,
            self.reviewed_image_fixture_sha256,
            self.render_contract_kind,
        )
        if reviewed != (self.disposition == "reviewed"):
            raise ValueError("R3.19 reviewed inventory mismatch")
        if reviewed != all(item is not None for item in refs):
            raise ValueError("R3.19 fixture reference closure mismatch")
        if not reviewed and any(item is not None for item in refs):
            raise ValueError("R3.19 ungenerated Card cannot reference a fixture")
        return self


class Step4PilotHandoffRefresh(StrictModel):
    schema_version: Literal["step4_pilot_handoff_refresh_r319_v1"] = HANDOFF_VERSION
    manifest_id: str = Field(pattern=r"^step4-pilot-handoff-r319:[0-9a-f]{24}$")
    source_r316_handoff_path: Literal[SOURCE_R316_HANDOFF.as_posix()] = SOURCE_R316_HANDOFF.as_posix()
    source_r316_handoff_sha256: str = Field(pattern=_SHA)
    source_step3_fixture_path: Literal[UNIFIED_FIXTURE.as_posix()] = UNIFIED_FIXTURE.as_posix()
    source_step3_fixture_sha256: str = Field(pattern=_SHA)
    reviewed_image_fixtures: tuple[Step4ReviewedImageReferenceR319, ...] = Field(min_length=4, max_length=4)
    card_count: Literal[16] = 16
    reviewed_card_count: Literal[4] = 4
    ungenerated_card_count: Literal[12] = 12
    cards: tuple[Step4PilotCardDispositionR319, ...] = Field(min_length=16, max_length=16)
    card2_image_reviewed: Literal[True] = True
    card3_image_reviewed: Literal[True] = True
    card4_image_reviewed: Literal[True] = True
    card6_image_reviewed: Literal[True] = True
    other_cards_require_independent_contracts: Literal[True] = True
    ordered_list_contract_generalization_allowed: Literal[False] = False
    table_contract_generalization_allowed: Literal[False] = False
    diagram_contract_generalization_allowed: Literal[False] = False
    constraint_contract_generalization_allowed: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    real_pilot_authorized: Literal[False] = False
    real_http_call_budget: Literal[0] = 0

    @model_validator(mode="after")
    def validate_manifest(self) -> "Step4PilotHandoffRefresh":
        if tuple(item.card_index for item in self.cards) != tuple(range(1, 17)):
            raise ValueError("R3.19 Cards must be ordered 1..16")
        if tuple(item.card_index for item in self.cards if item.disposition == "reviewed") != (2, 3, 4, 6):
            raise ValueError("R3.19 reviewed inventory must be Card 2, 3, 4, 6")
        if tuple(item.card_index for item in self.reviewed_image_fixtures) != (2, 3, 4, 6):
            raise ValueError("R3.19 fixture references must be ordered 2, 3, 4, 6")
        if any(item.render_contract_reused_from_other_card or item.generation_authorized for item in self.cards):
            raise ValueError("R3.19 handoff cannot authorize or reuse contracts")
        return self
