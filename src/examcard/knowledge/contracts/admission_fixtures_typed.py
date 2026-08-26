"""cm35 切分产物：phase2r84 typed-evidence 契约家族（层 1）。"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator
from ...materials.schema import StrictModel
from .admission_legacy_hash import canonical_hash, text_hash
from .admission_fixtures_constants import (CONSTRAINT_TOKEN_VERSION, CONSTRAINT_VERSION, DIAGRAM_VERSION, EDGE_VERSION, FORMULA_TOKEN_VERSION, FORMULA_VERSION, HASH_PATTERN, ID_PATTERN, LIST_ITEM_VERSION, LIST_VERSION, NODE_VERSION, NOISE_MARKERS, PROVENANCE_VERSION, R810_FIXTURE_VERSION, REVIEWED_CANDIDATE_VERSION, SOURCE_PAGE_VERSION, TABLE_CELL_VERSION, TABLE_VERSION, TYPED_TEXT_VERSION)


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
    # 判别式字段：下面五个子类各自把它收窄成自己的字面量，每处带一条
    # `# type: ignore[assignment]`。mypy 按不变量看待属性覆盖，所以逐条报 assignment；
    # 但 StrictModel 是 frozen=True，字段构造后只读，收窄在运行时是安全的，而 Python
    # 类型系统目前没有「只读属性」可写——属工具限制，不是缺陷。不改成模块级
    # disable_error_code，那会把本文件其余 assignment 检查一并关掉。
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
    schema_version: Literal["phase2r84_diagram_node_v1"] = NODE_VERSION  # type: ignore[assignment]
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
    schema_version: Literal["phase2r84_table_cell_v1"] = TABLE_CELL_VERSION  # type: ignore[assignment]
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
    schema_version: Literal["phase2r84_ordered_list_item_v1"] = LIST_ITEM_VERSION  # type: ignore[assignment]
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
    schema_version: Literal["phase2r84_constraint_token_v1"] = CONSTRAINT_TOKEN_VERSION  # type: ignore[assignment]
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
    schema_version: Literal["phase2r84_formula_token_v1"] = FORMULA_TOKEN_VERSION  # type: ignore[assignment]
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
