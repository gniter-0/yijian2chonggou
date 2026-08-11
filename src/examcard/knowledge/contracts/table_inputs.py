"""Structured table / section / controlled table-continuation request inputs."""
from __future__ import annotations
import hashlib
from collections.abc import Iterable
from enum import StrEnum

from pydantic import Field, model_validator

from ...evidence.schema import BBox, EvidenceKind
from ...materials.schema import StrictModel
from ..schema import FactType

from .anchors import _TABLE_KINDS, EvidenceAnchorSpan
from .enums import NumericTokenClass, condition_operators_in_text
from .hashing import _canonical_sha256
from .numeric_tokens import classify_numeric_tokens
from .projection import ReviewedStructuralDiagramInput, _validate_parent_anchor_spans
from .request_identity import _SHA256_PATTERN
from .versions import (
    STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION,
    TABLE_CONTINUATION_REQUEST_INPUT_VERSION,
)


class TableAtomicSubfactKind(StrEnum):
    """One independently teachable rule detected in a complete table cell."""

    CONDITION = "condition"
    THRESHOLD = "threshold"
    MATERIAL_RULE = "material_rule"


def make_table_atomic_subfact_requirement_id(
    *,
    table_evidence_id: str,
    cell_evidence_id: str,
    anchor_span_id: str,
    kind: TableAtomicSubfactKind,
    required_assertion_sha256: str,
) -> str:
    digest = _canonical_sha256(
        {
            "table_evidence_id": table_evidence_id,
            "cell_evidence_id": cell_evidence_id,
            "anchor_span_id": anchor_span_id,
            "kind": kind.value,
            "required_assertion_sha256": required_assertion_sha256,
        }
    )
    return f"table-subfact:{digest[:24]}"


class TableAtomicSubfactRequirement(StrictModel):
    """A deterministic table-cell clause that needs its own non-composition Fact."""

    requirement_id: str = Field(pattern=r"^table-subfact:[0-9a-f]{24}$")
    table_evidence_id: str
    cell_evidence_id: str
    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    kind: TableAtomicSubfactKind
    expected_fact_type: FactType
    required_assertion: str = Field(min_length=2)
    required_assertion_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_requirement(self) -> "TableAtomicSubfactRequirement":
        expected_fact_type = FactType.THRESHOLD if self.kind == TableAtomicSubfactKind.THRESHOLD else FactType.CONDITION
        if self.expected_fact_type != expected_fact_type:
            raise ValueError("table atomic subfact kind must use its required Fact type")
        if self.required_assertion != self.required_assertion.strip():
            raise ValueError("table atomic subfact assertion must be stripped")
        if self.required_assertion_sha256 != hashlib.sha256(self.required_assertion.encode("utf-8")).hexdigest():
            raise ValueError("table atomic subfact assertion hash does not match its text")
        expected_id = make_table_atomic_subfact_requirement_id(
            table_evidence_id=self.table_evidence_id,
            cell_evidence_id=self.cell_evidence_id,
            anchor_span_id=self.anchor_span_id,
            kind=self.kind,
            required_assertion_sha256=self.required_assertion_sha256,
        )
        if self.requirement_id != expected_id:
            raise ValueError("table atomic subfact ID does not match its immutable binding")
        return self


class SectionTextInput(StrictModel):
    evidence_id: str
    page_num: int = Field(ge=1)
    kind: EvidenceKind
    canonical_text: str = Field(min_length=1)
    anchor_spans: tuple[EvidenceAnchorSpan, ...]
    context_only: bool = False

    @model_validator(mode="after")
    def validate_non_table_text(self) -> "SectionTextInput":
        if self.kind in _TABLE_KINDS:
            raise ValueError("Step 2A table/row/cell Evidence must use structured table inputs")
        if not self.anchor_spans:
            raise ValueError("Step 2A textual inputs require anchor spans")
        _validate_parent_anchor_spans(
            parent_evidence_id=self.evidence_id,
            parent_evidence_kind=self.kind,
            canonical_text=self.canonical_text,
            anchor_spans=self.anchor_spans,
        )
        return self


class TableCellInput(StrictModel):
    evidence_id: str
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    canonical_text: str
    anchor_spans: tuple[EvidenceAnchorSpan, ...] = ()

    @model_validator(mode="after")
    def validate_anchor_spans(self) -> "TableCellInput":
        _validate_parent_anchor_spans(
            parent_evidence_id=self.evidence_id,
            parent_evidence_kind=EvidenceKind.TABLE_CELL,
            canonical_text=self.canonical_text,
            anchor_spans=self.anchor_spans,
        )
        return self


class TableRowInput(StrictModel):
    evidence_id: str
    row_index: int = Field(ge=0)
    cells: tuple[TableCellInput, ...]

    @model_validator(mode="after")
    def validate_cells(self) -> "TableRowInput":
        cell_ids = [cell.evidence_id for cell in self.cells]
        columns = [cell.column_index for cell in self.cells]
        if len(cell_ids) != len(set(cell_ids)) or len(columns) != len(set(columns)):
            raise ValueError("structured table rows require unique cells and columns")
        if any(cell.row_index != self.row_index for cell in self.cells):
            raise ValueError("structured table cell row_index must match its row")
        return self


class StructuredTableInput(StrictModel):
    table_evidence_id: str
    page_num: int = Field(ge=1)
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    rows: tuple[TableRowInput, ...]
    atomic_subfact_requirements: tuple[TableAtomicSubfactRequirement, ...] = ()

    @model_validator(mode="after")
    def validate_geometry(self) -> "StructuredTableInput":
        row_ids = [row.evidence_id for row in self.rows]
        row_indexes = [row.row_index for row in self.rows]
        if not self.rows or len(row_ids) != len(set(row_ids)) or len(row_indexes) != len(set(row_indexes)):
            raise ValueError("structured tables require unique retained row inputs")
        expected_rows = set(range(self.row_count))
        if set(row_indexes) != expected_rows:
            raise ValueError("structured table rows must exactly cover declared row geometry")
        covered: set[tuple[int, int]] = set()
        for row in self.rows:
            for cell in row.cells:
                for row_index in range(cell.row_index, cell.row_index + cell.row_span):
                    for column_index in range(cell.column_index, cell.column_index + cell.col_span):
                        if row_index >= self.row_count or column_index >= self.column_count:
                            raise ValueError("structured table cell span exceeds declared geometry")
                        coordinate = (row_index, column_index)
                        if coordinate in covered:
                            raise ValueError("structured table cell spans overlap")
                        covered.add(coordinate)
        expected_coordinates = {(row_index, column_index) for row_index in range(self.row_count) for column_index in range(self.column_count)}
        if covered != expected_coordinates:
            raise ValueError("structured table cells must exactly cover declared grid geometry")
        requirement_ids = [item.requirement_id for item in self.atomic_subfact_requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("structured table atomic subfact requirements must be unique")
        cell_ids = set(self.cell_ids)
        cell_span_ids = {span.anchor_span_id for row in self.rows for cell in row.cells for span in cell.anchor_spans}
        for requirement in self.atomic_subfact_requirements:
            if requirement.table_evidence_id != self.table_evidence_id:
                raise ValueError("table atomic subfact requirement must bind its containing table")
            if requirement.cell_evidence_id not in cell_ids or requirement.anchor_span_id not in cell_span_ids:
                raise ValueError("table atomic subfact requirement must bind a retained cell anchor")
        return self

    @property
    def cell_ids(self) -> tuple[str, ...]:
        return tuple(cell.evidence_id for row in self.rows for cell in row.cells)


class TableContinuationSourceCellInput(StrictModel):
    """One immutable cell admitted into a controlled adjacent-page table slice."""

    evidence_id: str
    bbox: BBox
    canonical_text_sha256: str = Field(pattern=_SHA256_PATTERN)


class TableContinuationSourcePageInput(StrictModel):
    """Exact source hierarchy and section identity for one side of a continued table."""

    controlled_section_id: str = Field(pattern=r"^section:[0-9a-f]{24}$")
    page_num: int = Field(ge=1)
    source_manifest_path: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    table_evidence_id: str
    table_bbox: BBox
    row_evidence_id: str
    row_bbox: BBox
    cells: tuple[TableContinuationSourceCellInput, ...]

    @model_validator(mode="after")
    def validate_page(self) -> "TableContinuationSourcePageInput":
        cell_ids = [item.evidence_id for item in self.cells]
        if not self.cells or len(cell_ids) != len(set(cell_ids)):
            raise ValueError("controlled table source pages require unique retained cells")
        return self


def make_table_continuation_controlled_section_id(
    *, page_num: int, source_manifest_sha256: str, binding_id: str, evidence_ids: Iterable[str]
) -> str:
    """Derive a source-section namespace that cannot collide with ordinary page sections."""

    digest = _canonical_sha256(
        {
            "kind": "controlled_table_continuation_section",
            "page_num": page_num,
            "source_manifest_sha256": source_manifest_sha256,
            "binding_id": binding_id,
            "evidence_ids": sorted(evidence_ids),
        }
    )[:24]
    return f"section:{digest}"


def make_table_continuation_request_input_id(payload: object) -> str:
    return f"table-request-input:{_canonical_sha256(payload)[:24]}"


class TableContinuationRequestInput(StrictModel):
    """Reviewed table-only input admitted beside, never inside, normal page projections.

    The source manifests remain immutable.  This object is the narrow V17 view
    of one reviewed adjacent-page table and deliberately contains no unrelated
    Evidence from the later page.
    """

    input_version: str = TABLE_CONTINUATION_REQUEST_INPUT_VERSION
    input_id: str = Field(pattern=r"^table-request-input:[0-9a-f]{24}$")
    binding_id: str = Field(pattern=r"^table-continuation:[0-9a-f]{24}$")
    binding_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    binding_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_policy_id: str = Field(min_length=1)
    content_policy_revision: int = Field(ge=1)
    content_policy_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_pages: tuple[TableContinuationSourcePageInput, ...]
    table: StructuredTableInput
    anchor_spans: tuple[EvidenceAnchorSpan, ...]
    complete_table_anchor_span_ids: tuple[str, ...]
    item_three_source_anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    item_three_closure_anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    item_three_required_assertion: str = Field(min_length=8, max_length=500)

    @property
    def controlled_section_ids(self) -> tuple[str, ...]:
        return tuple(item.controlled_section_id for item in self.source_pages)

    @model_validator(mode="after")
    def validate_input(self) -> "TableContinuationRequestInput":
        if self.input_version not in {
            TABLE_CONTINUATION_REQUEST_INPUT_VERSION,
            STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION,
        }:
            raise ValueError("unsupported controlled table continuation request-input version")
        if self.item_three_required_assertion != self.item_three_required_assertion.strip():
            raise ValueError("controlled table item-three assertion must be stripped")
        page_numbers = tuple(item.page_num for item in self.source_pages)
        manifests = [item.source_manifest_sha256 for item in self.source_pages]
        section_ids = [item.controlled_section_id for item in self.source_pages]
        if len(self.source_pages) != 2 or page_numbers[1] != page_numbers[0] + 1:
            raise ValueError("controlled table continuation input requires exactly two adjacent source pages")
        if len(manifests) != len(set(manifests)) or len(section_ids) != len(set(section_ids)):
            raise ValueError("controlled table continuation source manifests and sections must be unique")
        for page in self.source_pages:
            expected_section = make_table_continuation_controlled_section_id(
                page_num=page.page_num,
                source_manifest_sha256=page.source_manifest_sha256,
                binding_id=self.binding_id,
                evidence_ids=(page.table_evidence_id, page.row_evidence_id, *(cell.evidence_id for cell in page.cells)),
            )
            if page.controlled_section_id != expected_section:
                raise ValueError("controlled table source section ID must be deterministic")
        spans_by_id = {item.anchor_span_id: item for item in self.anchor_spans}
        if not self.anchor_spans or len(spans_by_id) != len(self.anchor_spans):
            raise ValueError("controlled table continuation input requires unique anchor spans")
        if set(self.complete_table_anchor_span_ids) != set(spans_by_id) or len(self.complete_table_anchor_span_ids) != len(set(self.complete_table_anchor_span_ids)):
            raise ValueError("controlled table complete anchors must exactly equal its retained anchor spans")
        cells_by_id = {
            cell.evidence_id: (page, cell)
            for page in self.source_pages
            for cell in page.cells
        }
        for span in self.anchor_spans:
            source = cells_by_id.get(span.parent_evidence_id)
            if source is None:
                raise ValueError("controlled table anchor must bind a retained source cell")
            page, _ = source
            if span.source_manifest_sha256 != page.source_manifest_sha256:
                raise ValueError("controlled table anchor manifest must match its source page")
            if (
                span.content_policy_id != self.content_policy_id
                or span.content_policy_revision != self.content_policy_revision
                or span.content_policy_fingerprint_sha256 != self.content_policy_fingerprint_sha256
            ):
                raise ValueError("controlled table anchor must match the request Content Policy")
        if self.table.table_evidence_id != self.binding_id or self.table.page_num != self.source_pages[0].page_num:
            raise ValueError("controlled virtual table must identify the reviewed binding and its source page")
        source_row_ids = {page.row_evidence_id for page in self.source_pages}
        source_cell_ids = set(cells_by_id)
        if {row.evidence_id for row in self.table.rows} != source_row_ids or set(self.table.cell_ids) != source_cell_ids:
            raise ValueError("controlled virtual table must retain exactly the reviewed rows and cells")
        table_span_ids = {
            span.anchor_span_id
            for row in self.table.rows
            for cell in row.cells
            for span in cell.anchor_spans
        }
        if table_span_ids != set(self.complete_table_anchor_span_ids):
            raise ValueError("controlled virtual table cells must expose every reviewed continuation anchor exactly once")
        source = spans_by_id.get(self.item_three_source_anchor_span_id)
        closure = spans_by_id.get(self.item_three_closure_anchor_span_id)
        if source is None or closure is None or source.source_manifest_sha256 == closure.source_manifest_sha256:
            raise ValueError("controlled table item three requires source and closure anchors on separate pages")
        requirements = self.table.atomic_subfact_requirements
        expected_requirement_count = (
            3
            if self.input_version == STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION
            else 1
        )
        if len(requirements) != expected_requirement_count:
            raise ValueError("controlled table continuation requires its versioned reviewed atomic requirements")
        requirements_by_anchor = {item.anchor_span_id: item for item in requirements}
        if len(requirements_by_anchor) != len(requirements):
            raise ValueError("controlled table atomic requirements must use unique anchor spans")
        requirement = requirements_by_anchor.get(self.item_three_source_anchor_span_id)
        if requirement is None:
            raise ValueError("controlled table atomic requirements must include the reviewed item-three threshold")
        if (
            requirement.table_evidence_id != self.binding_id
            or requirement.anchor_span_id != self.item_three_source_anchor_span_id
            or requirement.expected_fact_type != FactType.THRESHOLD
            or requirement.required_assertion != self.item_three_required_assertion
        ):
            raise ValueError("controlled table atomic requirement must be the reviewed item-three threshold")
        if self.input_version == TABLE_CONTINUATION_REQUEST_INPUT_VERSION:
            if len(requirements_by_anchor) != 1:
                raise ValueError("V1 controlled table input must retain only item-three threshold requirements")
        else:
            complete_row_ids = tuple(
                anchor_span_id
                for anchor_span_id in requirements_by_anchor
                if anchor_span_id != self.item_three_source_anchor_span_id
            )
            if len(complete_row_ids) != 2 or len(complete_row_ids) != len(set(complete_row_ids)):
                raise ValueError("V2 controlled table input requires exactly two unique complete-row threshold requirements")
            if set(requirements_by_anchor) != {*complete_row_ids, self.item_three_source_anchor_span_id}:
                raise ValueError("V2 controlled table requirements must exactly cover the two complete rows and item three")
            for anchor_span_id in complete_row_ids:
                span = spans_by_id.get(anchor_span_id)
                complete_row_requirement = requirements_by_anchor.get(anchor_span_id)
                if (
                    span is None
                    or complete_row_requirement is None
                    or span.source_manifest_sha256 != source.source_manifest_sha256
                    or span.parent_evidence_id != source.parent_evidence_id
                    or complete_row_requirement.table_evidence_id != self.binding_id
                    or complete_row_requirement.cell_evidence_id != span.parent_evidence_id
                    or complete_row_requirement.expected_fact_type != FactType.THRESHOLD
                    or complete_row_requirement.required_assertion != span.canonical_text
                ):
                    raise ValueError("V2 complete-row threshold requirements must exactly bind reviewed source spans")
        expected_id = make_table_continuation_request_input_id(self.model_dump(mode="json", exclude={"input_id"}))
        if self.input_id != expected_id:
            raise ValueError("controlled table request input ID does not match immutable content")
        return self


def _geometry_header_cell_ids(table: StructuredTableInput) -> tuple[str, ...]:
    """Return the conservative top header band from reviewed table geometry.

    A header may use merged cells across multiple rows. Keep the first row and
    each following row that starts a merged cell or is covered by a prior row
    span, stopping before the first ordinary unmerged body row. This uses only
    reviewed geometry, never inferred text semantics.
    """

    header_rows: set[int] = {0}
    for row_index in range(1, table.row_count):
        row = next(row for row in table.rows if row.row_index == row_index)
        continued_from_above = any(
            cell.row_index < row_index < cell.row_index + cell.row_span
            for prior_row in table.rows
            for cell in prior_row.cells
        )
        starts_merged_cell = any(cell.row_span > 1 or cell.col_span > 1 for cell in row.cells)
        if not continued_from_above and not starts_merged_cell:
            break
        header_rows.add(row_index)
    return tuple(
        cell.evidence_id
        for row in table.rows
        if row.row_index in header_rows
        for cell in row.cells
    )


class CompleteTableSemanticCoverage(StrictModel):
    """Locally-derived proof that one composition Fact covers one full table.

    The model selects only child anchor spans. This record is assembled from
    the immutable structured table after selection, so it cannot be fabricated
    by a relay response or confused with an OCR reconstruction.
    """

    table_evidence_id: str
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    row_evidence_ids: tuple[str, ...]
    header_cell_evidence_ids: tuple[str, ...]
    cell_evidence_ids: tuple[str, ...]
    cell_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_coverage(self) -> "CompleteTableSemanticCoverage":
        values = {
            "row": self.row_evidence_ids,
            "header cell": self.header_cell_evidence_ids,
            "cell": self.cell_evidence_ids,
            "cell anchor span": self.cell_anchor_span_ids,
        }
        for label, identifiers in values.items():
            if not identifiers or len(identifiers) != len(set(identifiers)):
                raise ValueError(f"complete table semantic coverage requires unique {label} IDs")
        if len(self.row_evidence_ids) != self.row_count:
            raise ValueError("complete table semantic coverage must retain every declared row")
        if not set(self.header_cell_evidence_ids) <= set(self.cell_evidence_ids):
            raise ValueError("complete table semantic header cells must be retained cells")
        return self


class Step2ASectionInput(StrictModel):
    section_id: str = Field(pattern=r"^section:[0-9a-f]{24}$")
    page_num: int = Field(ge=1)
    heading_evidence_ids: tuple[str, ...] = ()
    context_only_evidence_ids: tuple[str, ...] = ()
    text_evidence: tuple[SectionTextInput, ...] = ()
    tables: tuple[StructuredTableInput, ...] = ()
    controlled_diagrams: tuple[ReviewedStructuralDiagramInput, ...] = ()

    @model_validator(mode="after")
    def validate_section(self) -> "Step2ASectionInput":
        text_ids = [item.evidence_id for item in self.text_evidence]
        table_ids = [table.table_evidence_id for table in self.tables]
        row_ids = [row.evidence_id for table in self.tables for row in table.rows]
        cell_ids = [cell_id for table in self.tables for cell_id in table.cell_ids]
        direct_ids = [*text_ids, *table_ids, *row_ids, *cell_ids]
        if not direct_ids or len(direct_ids) != len(set(direct_ids)):
            raise ValueError("Step 2A sections require unique direct source Evidence IDs")
        # A reviewed diagram may reuse policy-approved text as an anchor while
        # that text is also direct Step 2A input. Diagram anchors are references,
        # not duplicate content records.
        # Diagram anchor Evidence can legitimately be shared by multiple
        # reviewed diagrams; they remain references rather than duplicate input.
        if any(item.page_num != self.page_num for item in self.text_evidence):
            raise ValueError("Step 2A text Evidence must remain on its section page")
        if any(table.page_num != self.page_num for table in self.tables):
            raise ValueError("Step 2A structured tables must remain on their section page")
        if not set(self.heading_evidence_ids) <= set(text_ids):
            raise ValueError("section headings must be represented in text_evidence")
        if len(self.context_only_evidence_ids) != len(set(self.context_only_evidence_ids)):
            raise ValueError("section context-only Evidence IDs must be unique")
        if not set(self.context_only_evidence_ids) <= set(text_ids):
            raise ValueError("section context-only Evidence must be represented in text_evidence")
        actual_context_only = {item.evidence_id for item in self.text_evidence if item.context_only}
        if actual_context_only != set(self.context_only_evidence_ids):
            raise ValueError("section context-only markers must match text Evidence flags")
        if any(
            cell.canonical_text.strip() and not cell.anchor_spans
            for table in self.tables
            for row in table.rows
            for cell in row.cells
        ):
            raise ValueError("Step 2A non-empty structured table cells require anchor spans")
        anchor_ids = [span.anchor_span_id for item in self.text_evidence for span in item.anchor_spans]
        anchor_ids.extend(span.anchor_span_id for table in self.tables for row in table.rows for cell in row.cells for span in cell.anchor_spans)
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("Step 2A sections require unique selectable anchor spans")
        review_ids = [diagram.review_id for diagram in self.controlled_diagrams]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("section controlled diagram reviews must be unique")
        return self

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        identifiers = [item.evidence_id for item in self.text_evidence]
        identifiers.extend(table.table_evidence_id for table in self.tables)
        identifiers.extend(row.evidence_id for table in self.tables for row in table.rows)
        identifiers.extend(cell_id for table in self.tables for cell_id in table.cell_ids)
        identifiers.extend(evidence_id for diagram in self.controlled_diagrams for evidence_id in diagram.source_evidence_ids)
        return tuple(dict.fromkeys(identifiers))

    @property
    def anchor_spans(self) -> tuple[EvidenceAnchorSpan, ...]:
        spans = [span for item in self.text_evidence for span in item.anchor_spans]
        spans.extend(span for table in self.tables for row in table.rows for cell in row.cells for span in cell.anchor_spans)
        return tuple(spans)

    @property
    def anchor_span_ids(self) -> tuple[str, ...]:
        return tuple(span.anchor_span_id for span in self.anchor_spans)


def _make_section_id(*, page_num: int, evidence_ids: Iterable[str]) -> str:
    digest = _canonical_sha256({"page_num": page_num, "evidence_ids": sorted(evidence_ids)})[:24]
    return f"section:{digest}"


def _table_atomic_subfact_kind(text: str) -> TableAtomicSubfactKind | None:
    """Classify only independently teachable table-cell rules from source text."""

    classifications = classify_numeric_tokens(text)
    if any(item.classification == NumericTokenClass.MATERIAL_GRADE for item in classifications):
        return TableAtomicSubfactKind.MATERIAL_RULE
    if any(item.classification == NumericTokenClass.QUANTITATIVE_VALUE for item in classifications):
        return TableAtomicSubfactKind.THRESHOLD
    if condition_operators_in_text(text) or "应" in text or "宜" in text:
        return TableAtomicSubfactKind.CONDITION
    return None


def derive_table_atomic_subfact_requirements(table: StructuredTableInput) -> tuple[TableAtomicSubfactRequirement, ...]:
    """Derive required table child Facts from exact, already-projected cell spans.

    This is intentionally conservative. A complete composition Fact owns the
    grid; this function only identifies rule/threshold/material spans which must
    additionally be represented as an atomic Fact. It never changes Evidence or
    manufactures a span below the immutable anchor-span boundary.
    """

    requirements: list[TableAtomicSubfactRequirement] = []
    for row in table.rows:
        for cell in row.cells:
            for anchor_span in cell.anchor_spans:
                required_assertion = anchor_span.canonical_text.strip()
                kind = _table_atomic_subfact_kind(required_assertion)
                if kind is None:
                    continue
                expected_fact_type = FactType.THRESHOLD if kind == TableAtomicSubfactKind.THRESHOLD else FactType.CONDITION
                required_assertion_sha256 = hashlib.sha256(required_assertion.encode("utf-8")).hexdigest()
                requirements.append(
                    TableAtomicSubfactRequirement(
                        requirement_id=make_table_atomic_subfact_requirement_id(
                            table_evidence_id=table.table_evidence_id,
                            cell_evidence_id=cell.evidence_id,
                            anchor_span_id=anchor_span.anchor_span_id,
                            kind=kind,
                            required_assertion_sha256=required_assertion_sha256,
                        ),
                        table_evidence_id=table.table_evidence_id,
                        cell_evidence_id=cell.evidence_id,
                        anchor_span_id=anchor_span.anchor_span_id,
                        kind=kind,
                        expected_fact_type=expected_fact_type,
                        required_assertion=required_assertion,
                        required_assertion_sha256=required_assertion_sha256,
                    )
                )
    return tuple(requirements)
