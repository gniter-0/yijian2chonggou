"""Span references and source-owned table occurrences / structural projections.

Note R4: TableGridMetadataReference is ordered before
CompleteTableStructuralProjection so pydantic resolves the field type without
model_rebuild.
"""
from __future__ import annotations
from enum import StrEnum

from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from .enums import NumericTokenClass
from .hashing import _canonical_sha256
from .request_identity import _SHA256_PATTERN


class ConditionSpanReference(StrictModel):
    """Offset and hash only; rejected model text is never persisted verbatim."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_span(self) -> "ConditionSpanReference":
        if self.end <= self.start:
            raise ValueError("condition span references require a non-empty range")
        return self


class NumericSpanReference(StrictModel):
    """Offset and hash for one complete quantitative expression."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_span(self) -> "NumericSpanReference":
        if self.end <= self.start:
            raise ValueError("numeric span references require a non-empty range")
        return self


class TableNumericOccurrenceState(StrEnum):
    """Outcomes for numeric text inside a complete-table composition Fact."""

    DERIVED_TABLE_CELL_OCCURRENCES = "derived_table_cell_occurrences"
    DERIVED_GRID_METADATA = "derived_grid_metadata"
    ASSERTION_WITHOUT_TABLE_OCCURRENCE = "assertion_without_table_occurrence"
    AMBIGUOUS_GRID_METADATA = "ambiguous_grid_metadata"


def make_table_numeric_occurrence_id(
    *,
    table_evidence_id: str,
    cell_evidence_id: str,
    page_num: int,
    row_index: int,
    column_index: int,
    row_span: int,
    col_span: int,
    anchor_span_id: str,
    source_span: NumericSpanReference,
    source_manifest_sha256: str,
) -> str:
    digest = _canonical_sha256(
        {
            "table_evidence_id": table_evidence_id,
            "cell_evidence_id": cell_evidence_id,
            "page_num": page_num,
            "row_index": row_index,
            "column_index": column_index,
            "row_span": row_span,
            "col_span": col_span,
            "anchor_span_id": anchor_span_id,
            "source_span": source_span.model_dump(mode="json"),
            "source_manifest_sha256": source_manifest_sha256,
        }
    )
    return f"table-numeric-occurrence:{digest[:24]}"


class TableNumericOccurrenceReference(StrictModel):
    """One exact numeric occurrence in a selected immutable table cell."""

    occurrence_id: str = Field(pattern=r"^table-numeric-occurrence:[0-9a-f]{24}$")
    table_evidence_id: str
    cell_evidence_id: str
    page_num: int = Field(ge=1)
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(ge=1)
    col_span: int = Field(ge=1)
    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    source_span: NumericSpanReference
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_occurrence(self) -> "TableNumericOccurrenceReference":
        expected_id = make_table_numeric_occurrence_id(
            table_evidence_id=self.table_evidence_id,
            cell_evidence_id=self.cell_evidence_id,
            page_num=self.page_num,
            row_index=self.row_index,
            column_index=self.column_index,
            row_span=self.row_span,
            col_span=self.col_span,
            anchor_span_id=self.anchor_span_id,
            source_span=self.source_span,
            source_manifest_sha256=self.source_manifest_sha256,
        )
        if self.occurrence_id != expected_id:
            raise ValueError("table numeric occurrence ID must match its immutable table-cell binding")
        return self


class TableStructuralIndexOccurrenceState(StrEnum):
    """Fail-closed outcomes for table-list indexes in composition assertions."""

    DERIVED = "derived_structural_index_occurrences"
    ASSERTION_WITHOUT_OCCURRENCE = "assertion_without_structural_index_occurrence"
    QUANTITY_CLAIM_NOT_STRUCTURAL = "quantity_claim_not_structural_index"
    AMBIGUOUS_SOURCE_ROLE = "ambiguous_structural_numeric_source_role"


def make_table_structural_index_occurrence_id(
    *,
    table_evidence_id: str,
    cell_evidence_id: str,
    page_num: int,
    row_index: int,
    column_index: int,
    row_span: int,
    col_span: int,
    anchor_span_id: str,
    source_span: NumericSpanReference,
    source_manifest_sha256: str,
) -> str:
    digest = _canonical_sha256(
        {
            "occurrence_kind": "structural_index",
            "table_evidence_id": table_evidence_id,
            "cell_evidence_id": cell_evidence_id,
            "page_num": page_num,
            "row_index": row_index,
            "column_index": column_index,
            "row_span": row_span,
            "col_span": col_span,
            "anchor_span_id": anchor_span_id,
            "source_span": source_span.model_dump(mode="json"),
            "source_manifest_sha256": source_manifest_sha256,
        }
    )
    return f"table-structural-index-occurrence:{digest[:24]}"


class TableStructuralIndexOccurrenceReference(StrictModel):
    """One exact list-index occurrence in a selected immutable table cell."""

    occurrence_id: str = Field(pattern=r"^table-structural-index-occurrence:[0-9a-f]{24}$")
    table_evidence_id: str
    cell_evidence_id: str
    page_num: int = Field(ge=1)
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(ge=1)
    col_span: int = Field(ge=1)
    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    source_span: NumericSpanReference
    source_classification: NumericTokenClass = NumericTokenClass.LIST_INDEX
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_occurrence(self) -> "TableStructuralIndexOccurrenceReference":
        if self.source_classification != NumericTokenClass.LIST_INDEX:
            raise ValueError("table structural-index occurrences require a list_index source token")
        expected_id = make_table_structural_index_occurrence_id(
            table_evidence_id=self.table_evidence_id,
            cell_evidence_id=self.cell_evidence_id,
            page_num=self.page_num,
            row_index=self.row_index,
            column_index=self.column_index,
            row_span=self.row_span,
            col_span=self.col_span,
            anchor_span_id=self.anchor_span_id,
            source_span=self.source_span,
            source_manifest_sha256=self.source_manifest_sha256,
        )
        if self.occurrence_id != expected_id:
            raise ValueError("table structural-index occurrence ID must match its immutable table-cell binding")
        return self


class TableStructuralCellRole(StrEnum):
    """Geometry-owned role of a cell in a complete selected table grid."""

    HEADER_ROW = "header_row"
    BODY = "body"


def make_table_structural_cell_occurrence_id(
    *,
    table_evidence_id: str,
    cell_evidence_id: str,
    page_num: int,
    row_index: int,
    column_index: int,
    row_span: int,
    col_span: int,
    cell_text_sha256: str,
    anchor_span_ids: tuple[str, ...],
    source_manifest_sha256: str,
) -> str:
    digest = _canonical_sha256(
        {
            "occurrence_kind": "structural_table_cell",
            "table_evidence_id": table_evidence_id,
            "cell_evidence_id": cell_evidence_id,
            "page_num": page_num,
            "row_index": row_index,
            "column_index": column_index,
            "row_span": row_span,
            "col_span": col_span,
            "cell_text_sha256": cell_text_sha256,
            "anchor_span_ids": anchor_span_ids,
            "source_manifest_sha256": source_manifest_sha256,
        }
    )
    return f"table-structural-cell-occurrence:{digest[:24]}"


class TableStructuralCellOccurrence(StrictModel):
    """One source-owned cell retained by a selected complete table grid."""

    occurrence_id: str = Field(pattern=r"^table-structural-cell-occurrence:[0-9a-f]{24}$")
    table_evidence_id: str
    cell_evidence_id: str
    page_num: int = Field(ge=1)
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(ge=1)
    col_span: int = Field(ge=1)
    role: TableStructuralCellRole
    cell_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    anchor_span_ids: tuple[str, ...]
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_occurrence(self) -> "TableStructuralCellOccurrence":
        if len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("table structural cell occurrences require unique anchor span IDs")
        expected_role = (
            TableStructuralCellRole.HEADER_ROW if self.row_index == 0 else TableStructuralCellRole.BODY
        )
        if self.role != expected_role:
            raise ValueError("table structural cell role must be deterministically derived from its row index")
        expected_id = make_table_structural_cell_occurrence_id(
            table_evidence_id=self.table_evidence_id,
            cell_evidence_id=self.cell_evidence_id,
            page_num=self.page_num,
            row_index=self.row_index,
            column_index=self.column_index,
            row_span=self.row_span,
            col_span=self.col_span,
            cell_text_sha256=self.cell_text_sha256,
            anchor_span_ids=self.anchor_span_ids,
            source_manifest_sha256=self.source_manifest_sha256,
        )
        if self.occurrence_id != expected_id:
            raise ValueError("table structural cell occurrence ID must match its immutable complete-grid binding")
        return self


def make_table_grid_metadata_id(
    *,
    table_evidence_id: str,
    page_nums: tuple[int, ...],
    source_manifest_sha256s: tuple[str, ...],
    row_count: int,
    column_count: int,
    cell_evidence_ids: tuple[str, ...],
    cell_anchor_span_ids: tuple[str, ...],
) -> str:
    digest = _canonical_sha256(
        {
            "table_evidence_id": table_evidence_id,
            "page_nums": page_nums,
            "source_manifest_sha256s": source_manifest_sha256s,
            "row_count": row_count,
            "column_count": column_count,
            "cell_evidence_ids": cell_evidence_ids,
            "cell_anchor_span_ids": cell_anchor_span_ids,
        }
    )
    return f"table-grid-metadata:{digest[:24]}"


class TableGridMetadataReference(StrictModel):
    """Geometry-derived metadata for a fully selected structured table."""

    metadata_id: str = Field(pattern=r"^table-grid-metadata:[0-9a-f]{24}$")
    table_evidence_id: str
    page_nums: tuple[int, ...]
    source_manifest_sha256s: tuple[str, ...]
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    cell_evidence_ids: tuple[str, ...]
    cell_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_metadata(self) -> "TableGridMetadataReference":
        if not self.page_nums or tuple(sorted(set(self.page_nums))) != self.page_nums:
            raise ValueError("table grid metadata page numbers must be sorted and unique")
        if not self.source_manifest_sha256s or tuple(sorted(set(self.source_manifest_sha256s))) != self.source_manifest_sha256s:
            raise ValueError("table grid metadata source manifests must be sorted and unique")
        if not self.cell_evidence_ids or len(self.cell_evidence_ids) != len(set(self.cell_evidence_ids)):
            raise ValueError("table grid metadata requires unique cell Evidence IDs")
        if not self.cell_anchor_span_ids or len(self.cell_anchor_span_ids) != len(set(self.cell_anchor_span_ids)):
            raise ValueError("table grid metadata requires unique cell anchor span IDs")
        expected_id = make_table_grid_metadata_id(
            table_evidence_id=self.table_evidence_id,
            page_nums=self.page_nums,
            source_manifest_sha256s=self.source_manifest_sha256s,
            row_count=self.row_count,
            column_count=self.column_count,
            cell_evidence_ids=self.cell_evidence_ids,
            cell_anchor_span_ids=self.cell_anchor_span_ids,
        )
        if self.metadata_id != expected_id:
            raise ValueError("table grid metadata ID must match its complete-table binding")
        return self


def make_complete_table_structural_projection_id(
    *,
    proposal_key: str,
    table_evidence_id: str,
    grid_metadata_id: str,
    cell_occurrence_ids: tuple[str, ...],
    list_index_occurrence_ids: tuple[str, ...],
) -> str:
    digest = _canonical_sha256(
        {
            "proposal_key": proposal_key,
            "table_evidence_id": table_evidence_id,
            "grid_metadata_id": grid_metadata_id,
            "cell_occurrence_ids": cell_occurrence_ids,
            "list_index_occurrence_ids": list_index_occurrence_ids,
        }
    )
    return f"complete-table-structural-projection:{digest[:24]}"


class CompleteTableStructuralProjection(StrictModel):
    """Auditable, source-owned table structure for one complete composition Fact."""

    projection_id: str = Field(pattern=r"^complete-table-structural-projection:[0-9a-f]{24}$")
    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    table_evidence_id: str
    grid_metadata: TableGridMetadataReference
    cell_occurrences: tuple[TableStructuralCellOccurrence, ...]
    header_cell_occurrence_ids: tuple[str, ...]
    list_index_occurrences: tuple[TableStructuralIndexOccurrenceReference, ...] = ()

    @model_validator(mode="after")
    def validate_projection(self) -> "CompleteTableStructuralProjection":
        occurrence_ids = tuple(item.occurrence_id for item in self.cell_occurrences)
        if not self.cell_occurrences or len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("complete-table structural projections require unique retained cell occurrences")
        if self.table_evidence_id != self.grid_metadata.table_evidence_id:
            raise ValueError("complete-table structural projection must bind its grid metadata table")
        if tuple(item.cell_evidence_id for item in self.cell_occurrences) != self.grid_metadata.cell_evidence_ids:
            raise ValueError("complete-table structural projection cells must preserve exact grid cell order")
        flattened_anchor_ids = tuple(
            anchor_span_id for item in self.cell_occurrences for anchor_span_id in item.anchor_span_ids
        )
        if flattened_anchor_ids != self.grid_metadata.cell_anchor_span_ids:
            raise ValueError("complete-table structural projection anchors must preserve exact grid anchor order")
        header_ids = tuple(
            item.occurrence_id
            for item in self.cell_occurrences
            if item.role == TableStructuralCellRole.HEADER_ROW
        )
        if self.header_cell_occurrence_ids != header_ids:
            raise ValueError("complete-table structural projection headers must be exactly the first grid row")
        index_ids = tuple(item.occurrence_id for item in self.list_index_occurrences)
        if len(index_ids) != len(set(index_ids)):
            raise ValueError("complete-table structural projection list-index occurrences must be unique")
        cell_ids = {item.cell_evidence_id for item in self.cell_occurrences}
        cell_anchor_ids = set(flattened_anchor_ids)
        if any(
            item.table_evidence_id != self.table_evidence_id
            or item.cell_evidence_id not in cell_ids
            or item.anchor_span_id not in cell_anchor_ids
            for item in self.list_index_occurrences
        ):
            raise ValueError("complete-table structural projection list indexes must bind retained grid cells")
        expected_id = make_complete_table_structural_projection_id(
            proposal_key=self.proposal_key,
            table_evidence_id=self.table_evidence_id,
            grid_metadata_id=self.grid_metadata.metadata_id,
            cell_occurrence_ids=occurrence_ids,
            list_index_occurrence_ids=index_ids,
        )
        if self.projection_id != expected_id:
            raise ValueError("complete-table structural projection ID must match its source-owned bindings")
        return self


class CompleteTableStructuralProjectionState(StrEnum):
    DERIVED = "derived_source_owned_complete_table_structure"
