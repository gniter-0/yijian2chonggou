"""Step 2A numeric-claim derivation, table occurrence and grid helpers (cm36 split)."""
from __future__ import annotations
import hashlib
import re

from collections.abc import (
    Iterable,
    Mapping,
)
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    TypedDict,
)

from .audits import (
    NumericClaimDerivationState,
    Step2ACompleteTableStructuralProjectionAudit,
    Step2ANumericClaimAudit,
    Step2ANumericClaimDerivationResult,
    Step2ATableNumericOccurrenceAudit,
    Step2ATableStructuralIndexOccurrenceAudit,
)
from .claims import GroundedNumericClaim
from .enums import NumericTokenClass
from .numeric_tokens import (
    NumericTokenClassificationRecord,
    _TABLE_GRID_DIMENSION_PATTERNS,
    classify_numeric_tokens,
)
from .occurrences import (
    CompleteTableStructuralProjection,
    CompleteTableStructuralProjectionState,
    NumericSpanReference,
    TableGridMetadataReference,
    TableNumericOccurrenceReference,
    TableNumericOccurrenceState,
    TableStructuralCellOccurrence,
    TableStructuralCellRole,
    TableStructuralIndexOccurrenceReference,
    TableStructuralIndexOccurrenceState,
    make_complete_table_structural_projection_id,
    make_table_grid_metadata_id,
    make_table_numeric_occurrence_id,
    make_table_structural_cell_occurrence_id,
    make_table_structural_index_occurrence_id,
)
from .proposals import Step2AFactProposal
from .request_helpers import (
    _request_structured_tables,
    _step2a_anchor_sources,
)
from .versions import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
)

if TYPE_CHECKING:
    from .step2a_request import Step2ARequest


@dataclass(frozen=True)
class _NumericSpanCandidate:
    value: str
    evidence_id: str
    anchor_span_id: str
    parent_anchor_start: int
    source_start: int
    source_end: int


@dataclass(frozen=True)
class _TableNumericOccurrenceCandidate:
    value: str
    occurrence: TableNumericOccurrenceReference


@dataclass(frozen=True)
class _TableStructuralIndexOccurrenceCandidate:
    value: str
    occurrence: TableStructuralIndexOccurrenceReference


class _TableStructuralCellOccurrenceKwargs(TypedDict):
    table_evidence_id: str
    cell_evidence_id: str
    page_num: int
    row_index: int
    column_index: int
    row_span: int
    col_span: int
    cell_text_sha256: str
    anchor_span_ids: tuple[str, ...]
    source_manifest_sha256: str


class _TableNumericOccurrenceKwargs(TypedDict):
    table_evidence_id: str
    cell_evidence_id: str
    page_num: int
    row_index: int
    column_index: int
    row_span: int
    col_span: int
    anchor_span_id: str
    source_span: NumericSpanReference
    source_manifest_sha256: str


def _numeric_span_reference(text: str, start: int, end: int, *, parent_offset: int = 0) -> NumericSpanReference:
    return NumericSpanReference(
        start=start + parent_offset,
        end=end + parent_offset,
        sha256=hashlib.sha256(text[start:end].encode("utf-8")).hexdigest(),
    )


def _table_numeric_occurrence_sources(
    *, request: Step2ARequest, proposal: Step2AFactProposal
) -> tuple[
    tuple[_TableNumericOccurrenceCandidate, ...],
    tuple[_TableStructuralIndexOccurrenceCandidate, ...],
    dict[tuple[int, int], tuple[TableGridMetadataReference, ...]],
    tuple[CompleteTableStructuralProjection, ...],
]:
    """Return numeric/index cell occurrences and grid metadata for a composition Fact.

    This path is intentionally available only after complete-table semantic
    coverage has been locally derived. A composition Fact preserves a table
    structure; it cannot turn a repeated table value into one atomic claim.
    """

    tables = {table.table_evidence_id: table for table in _request_structured_tables(request)}
    controlled_pages_by_cell_id = {
        cell.evidence_id: source_page
        for controlled in request.table_continuation_inputs
        for source_page in controlled.source_pages
        for cell in source_page.cells
    }
    ordinary_manifests_by_page = {
        projection.page_num: projection.source_manifest_sha256
        for projection in (request.projection, *request.additional_projections)
    }
    selected_span_ids = set((*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids))
    occurrences: list[_TableNumericOccurrenceCandidate] = []
    structural_index_occurrences: list[_TableStructuralIndexOccurrenceCandidate] = []
    metadata_by_dimensions: dict[tuple[int, int], list[TableGridMetadataReference]] = {}
    structural_projections: list[CompleteTableStructuralProjection] = []

    for coverage in proposal.complete_table_semantic_coverages:
        table = tables.get(coverage.table_evidence_id)
        if table is None:
            raise ValueError("complete-table numeric occurrence derivation requires a request-local table")
        expected_cell_ids = tuple(cell.evidence_id for row in table.rows for cell in row.cells)
        expected_anchor_ids = tuple(
            span.anchor_span_id for row in table.rows for cell in row.cells for span in cell.anchor_spans
        )
        if coverage.cell_evidence_ids != expected_cell_ids or coverage.cell_anchor_span_ids != expected_anchor_ids:
            raise ValueError("complete-table numeric occurrence derivation requires the exact retained table grid")
        if not set(expected_anchor_ids) <= selected_span_ids:
            raise ValueError("complete-table numeric occurrence derivation requires every retained cell anchor to be selected")

        page_nums: set[int] = set()
        manifest_sha256s: set[str] = set()
        cell_occurrences: list[TableStructuralCellOccurrence] = []
        coverage_structural_index_occurrences: list[TableStructuralIndexOccurrenceReference] = []
        for row in table.rows:
            for cell in row.cells:
                source_page = controlled_pages_by_cell_id.get(cell.evidence_id)
                page_num = source_page.page_num if source_page is not None else table.page_num
                source_manifest_sha256 = (
                    source_page.source_manifest_sha256
                    if source_page is not None
                    else ordinary_manifests_by_page.get(table.page_num)
                )
                if source_manifest_sha256 is None:
                    raise ValueError("complete-table structural projection requires an immutable source manifest for every cell")
                page_nums.add(page_num)
                manifest_sha256s.add(source_manifest_sha256)
                cell_anchor_span_ids = tuple(span.anchor_span_id for span in cell.anchor_spans)
                cell_occurrence_kwargs: _TableStructuralCellOccurrenceKwargs = {
                    "table_evidence_id": table.table_evidence_id,
                    "cell_evidence_id": cell.evidence_id,
                    "page_num": page_num,
                    "row_index": cell.row_index,
                    "column_index": cell.column_index,
                    "row_span": cell.row_span,
                    "col_span": cell.col_span,
                    "cell_text_sha256": hashlib.sha256(cell.canonical_text.encode("utf-8")).hexdigest(),
                    "anchor_span_ids": cell_anchor_span_ids,
                    "source_manifest_sha256": source_manifest_sha256,
                }
                cell_occurrences.append(
                    TableStructuralCellOccurrence(
                        occurrence_id=make_table_structural_cell_occurrence_id(**cell_occurrence_kwargs),
                        role=(
                            TableStructuralCellRole.HEADER_ROW
                            if cell.row_index == 0
                            else TableStructuralCellRole.BODY
                        ),
                        **cell_occurrence_kwargs,
                    )
                )
                for anchor_span in cell.anchor_spans:
                    if anchor_span.source_manifest_sha256 != source_manifest_sha256:
                        raise ValueError("complete-table structural projection cell manifest must match every retained anchor")
                    for token in classify_numeric_tokens(anchor_span.canonical_text):
                        if token.classification not in {
                            NumericTokenClass.QUANTITATIVE_VALUE,
                            NumericTokenClass.LIST_INDEX,
                        }:
                            continue
                        source_span = _numeric_span_reference(
                            anchor_span.canonical_text,
                            token.start,
                            token.end,
                            parent_offset=anchor_span.start,
                        )
                        occurrence_kwargs: _TableNumericOccurrenceKwargs = {
                            "table_evidence_id": table.table_evidence_id,
                            "cell_evidence_id": cell.evidence_id,
                            "page_num": page_num,
                            "row_index": cell.row_index,
                            "column_index": cell.column_index,
                            "row_span": cell.row_span,
                            "col_span": cell.col_span,
                            "anchor_span_id": anchor_span.anchor_span_id,
                            "source_span": source_span,
                            "source_manifest_sha256": anchor_span.source_manifest_sha256,
                        }
                        if token.classification == NumericTokenClass.LIST_INDEX:
                            structural_occurrence = TableStructuralIndexOccurrenceReference(
                                occurrence_id=make_table_structural_index_occurrence_id(**occurrence_kwargs),
                                **occurrence_kwargs,
                            )
                            structural_index_occurrences.append(
                                _TableStructuralIndexOccurrenceCandidate(
                                    value=token.value,
                                    occurrence=structural_occurrence,
                                )
                            )
                            coverage_structural_index_occurrences.append(structural_occurrence)
                            continue
                        occurrence = TableNumericOccurrenceReference(
                            occurrence_id=make_table_numeric_occurrence_id(**occurrence_kwargs),
                            **occurrence_kwargs,
                        )
                        occurrences.append(_TableNumericOccurrenceCandidate(value=token.value, occurrence=occurrence))

        sorted_pages = tuple(sorted(page_nums))
        sorted_manifests = tuple(sorted(manifest_sha256s))
        metadata = TableGridMetadataReference(
            metadata_id=make_table_grid_metadata_id(
                table_evidence_id=table.table_evidence_id,
                page_nums=sorted_pages,
                source_manifest_sha256s=sorted_manifests,
                row_count=table.row_count,
                column_count=table.column_count,
                cell_evidence_ids=coverage.cell_evidence_ids,
                cell_anchor_span_ids=coverage.cell_anchor_span_ids,
            ),
            table_evidence_id=table.table_evidence_id,
            page_nums=sorted_pages,
            source_manifest_sha256s=sorted_manifests,
            row_count=table.row_count,
            column_count=table.column_count,
            cell_evidence_ids=coverage.cell_evidence_ids,
            cell_anchor_span_ids=coverage.cell_anchor_span_ids,
        )
        metadata_by_dimensions.setdefault((table.row_count, table.column_count), []).append(metadata)
        structural_projection_kwargs = {
            "proposal_key": proposal.proposal_key,
            "table_evidence_id": table.table_evidence_id,
            "grid_metadata": metadata,
            "cell_occurrences": tuple(cell_occurrences),
            "header_cell_occurrence_ids": tuple(
                item.occurrence_id
                for item in cell_occurrences
                if item.role == TableStructuralCellRole.HEADER_ROW
            ),
            "list_index_occurrences": tuple(coverage_structural_index_occurrences),
        }
        structural_projections.append(
            CompleteTableStructuralProjection(
                projection_id=make_complete_table_structural_projection_id(
                    proposal_key=proposal.proposal_key,
                    table_evidence_id=table.table_evidence_id,
                    grid_metadata_id=metadata.metadata_id,
                    cell_occurrence_ids=tuple(item.occurrence_id for item in cell_occurrences),
                    list_index_occurrence_ids=tuple(
                        item.occurrence_id for item in coverage_structural_index_occurrences
                    ),
                ),
                **structural_projection_kwargs,
            )
        )
    return (
        tuple(occurrences),
        tuple(structural_index_occurrences),
        {dimensions: tuple(items) for dimensions, items in metadata_by_dimensions.items()},
        tuple(structural_projections),
    )


def _table_grid_metadata_token_bindings(
    *, assertion: str, metadata_by_dimensions: Mapping[tuple[int, int], tuple[TableGridMetadataReference, ...]]
) -> tuple[dict[tuple[int, int], TableGridMetadataReference], set[tuple[int, int]]]:
    """Map only explicit `R×C` / `R行C列` text to one reviewed grid geometry."""

    bindings: dict[tuple[int, int], TableGridMetadataReference] = {}
    ambiguous: set[tuple[int, int]] = set()
    for pattern in _TABLE_GRID_DIMENSION_PATTERNS:
        for match in pattern.finditer(assertion):
            dimensions = (int(match.group(1)), int(match.group(2)))
            candidates = metadata_by_dimensions.get(dimensions, ())
            token_ranges = (match.span(1), match.span(2))
            if len(candidates) == 1:
                bindings.update({token_range: candidates[0] for token_range in token_ranges})
            elif len(candidates) > 1:
                ambiguous.update(token_ranges)
    return bindings, ambiguous


_STRUCTURAL_INDEX_COUNT_UNIT_PATTERN = re.compile(
    r"\s*(?:项|条|个|类|种|组|列|行|页|张|次|处|点|根|套)(?![A-Za-z0-9])"
)


def _assertion_token_is_quantity_count(
    *, assertion: str, token: NumericTokenClassificationRecord
) -> bool:
    """Keep a quantity such as ``共8项`` out of structural-index provenance."""

    return (
        token.classification == NumericTokenClass.QUANTITATIVE_VALUE
        and bool(_STRUCTURAL_INDEX_COUNT_UNIT_PATTERN.match(assertion[token.end :]))
    )


def derive_step2a_numeric_claims(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> Step2ANumericClaimDerivationResult:
    """Derive atomic numeric claims and composition table numeric occurrences.

    A selected source number that the model did not assert is intentionally not a
    claim. Conversely, every asserted quantitative expression must resolve to
    exactly one complete quantitative token in one selected anchor span. This
    applies to atomic Facts. A complete-table composition Fact has a separate
    occurrence audit because repeated cell values are structural table content,
    not a model-owned choice of one atomic number.
    """

    frozen_proposals = tuple(proposals)
    deterministic_atomic_contracts = {
        DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
        TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
        TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
        TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
        TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
        STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    }
    if (
        request.identity.request_version not in deterministic_atomic_contracts
        and any(not proposal.complete_table_semantic_coverages for proposal in frozen_proposals)
    ):
        raise ValueError("atomic deterministic numeric claim derivation requires the V16-V21 Step 2A contracts")
    spans_by_id, _, _, _ = _step2a_anchor_sources(request)
    canonical_proposals: list[Step2AFactProposal] = []
    audits: list[Step2ANumericClaimAudit] = []
    table_occurrence_audits: list[Step2ATableNumericOccurrenceAudit] = []
    table_structural_index_occurrence_audits: list[Step2ATableStructuralIndexOccurrenceAudit] = []
    table_structural_projection_audits: list[Step2ACompleteTableStructuralProjectionAudit] = []
    for proposal in frozen_proposals:
        if proposal.complete_table_semantic_coverages:
            (
                table_occurrences,
                structural_index_occurrences,
                metadata_by_dimensions,
                structural_projections,
            ) = _table_numeric_occurrence_sources(request=request, proposal=proposal)
            if request.identity.request_version == SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION:
                table_structural_projection_audits.append(
                    Step2ACompleteTableStructuralProjectionAudit(
                        proposal_key=proposal.proposal_key,
                        state=CompleteTableStructuralProjectionState.DERIVED,
                        projections=structural_projections,
                        basis=(
                            "the complete selected table grid locally derived its headers, rows, columns, "
                            "cell occurrences, and list-index occurrences without requiring assertion wording"
                        ),
                    )
                )
            metadata_bindings, ambiguous_metadata_ranges = _table_grid_metadata_token_bindings(
                assertion=proposal.assertion,
                metadata_by_dimensions=metadata_by_dimensions,
            )
            for assertion_token in classify_numeric_tokens(proposal.assertion):
                if assertion_token.classification not in {
                    NumericTokenClass.QUANTITATIVE_VALUE,
                    NumericTokenClass.LIST_INDEX,
                }:
                    continue
                assertion_span = _numeric_span_reference(
                    proposal.assertion, assertion_token.start, assertion_token.end
                )
                token_key = (assertion_token.start, assertion_token.end)
                if (
                    assertion_token.classification == NumericTokenClass.QUANTITATIVE_VALUE
                    and token_key in metadata_bindings
                ):
                    table_occurrence_audits.append(
                        Step2ATableNumericOccurrenceAudit(
                            proposal_key=proposal.proposal_key,
                            state=TableNumericOccurrenceState.DERIVED_GRID_METADATA,
                            assertion_span=assertion_span,
                            grid_metadata=metadata_bindings[token_key],
                            basis="the assertion token is one coordinate of an explicit unique reviewed table-grid dimension",
                        )
                    )
                    continue
                if (
                    assertion_token.classification == NumericTokenClass.QUANTITATIVE_VALUE
                    and token_key in ambiguous_metadata_ranges
                ):
                    table_occurrence_audits.append(
                        Step2ATableNumericOccurrenceAudit(
                            proposal_key=proposal.proposal_key,
                            state=TableNumericOccurrenceState.AMBIGUOUS_GRID_METADATA,
                            assertion_span=assertion_span,
                            basis="the assertion grid dimension matches more than one complete selected table",
                        )
                    )
                    continue
                matching_occurrences = tuple(
                    candidate.occurrence for candidate in table_occurrences if candidate.value == assertion_token.value
                )
                matching_structural_occurrences = tuple(
                    candidate.occurrence
                    for candidate in structural_index_occurrences
                    if candidate.value == assertion_token.value
                )
                if matching_occurrences and matching_structural_occurrences:
                    table_structural_index_occurrence_audits.append(
                        Step2ATableStructuralIndexOccurrenceAudit(
                            proposal_key=proposal.proposal_key,
                            state=TableStructuralIndexOccurrenceState.AMBIGUOUS_SOURCE_ROLE,
                            assertion_span=assertion_span,
                            assertion_classification=assertion_token.classification,
                            basis="the exact assertion token matches both quantitative and list-index cells in the selected table",
                        )
                    )
                elif matching_occurrences:
                    table_occurrence_audits.append(
                        Step2ATableNumericOccurrenceAudit(
                            proposal_key=proposal.proposal_key,
                            state=TableNumericOccurrenceState.DERIVED_TABLE_CELL_OCCURRENCES,
                            assertion_span=assertion_span,
                            occurrences=matching_occurrences,
                            basis="one or more exact quantitative table-cell occurrences are retained by the selected complete grid",
                        )
                    )
                elif matching_structural_occurrences:
                    if _assertion_token_is_quantity_count(
                        assertion=proposal.assertion,
                        token=assertion_token,
                    ):
                        table_structural_index_occurrence_audits.append(
                            Step2ATableStructuralIndexOccurrenceAudit(
                                proposal_key=proposal.proposal_key,
                                state=TableStructuralIndexOccurrenceState.QUANTITY_CLAIM_NOT_STRUCTURAL,
                                assertion_span=assertion_span,
                                assertion_classification=assertion_token.classification,
                                basis="a count expression cannot borrow an equal-valued table list index as provenance",
                            )
                        )
                    else:
                        table_structural_index_occurrence_audits.append(
                            Step2ATableStructuralIndexOccurrenceAudit(
                                proposal_key=proposal.proposal_key,
                                state=TableStructuralIndexOccurrenceState.DERIVED,
                                assertion_span=assertion_span,
                                assertion_classification=assertion_token.classification,
                                occurrences=matching_structural_occurrences,
                                basis="all exact selected table-cell list-index occurrences are retained as non-numeric provenance",
                            )
                        )
                elif assertion_token.classification == NumericTokenClass.LIST_INDEX:
                    table_structural_index_occurrence_audits.append(
                        Step2ATableStructuralIndexOccurrenceAudit(
                            proposal_key=proposal.proposal_key,
                            state=TableStructuralIndexOccurrenceState.ASSERTION_WITHOUT_OCCURRENCE,
                            assertion_span=assertion_span,
                            assertion_classification=assertion_token.classification,
                            basis="no selected complete-table list-index cell exactly matches the assertion token",
                        )
                    )
                else:
                    table_occurrence_audits.append(
                        Step2ATableNumericOccurrenceAudit(
                            proposal_key=proposal.proposal_key,
                            state=TableNumericOccurrenceState.ASSERTION_WITHOUT_TABLE_OCCURRENCE,
                            assertion_span=assertion_span,
                            basis="no selected complete-table cell occurrence or uniquely bound grid metadata matches the assertion token",
                        )
                    )
            canonical_proposals.append(proposal.model_copy(update={"numeric_claims": ()}))
            continue
        selected_anchor_span_ids = tuple(
            dict.fromkeys((*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids))
        )
        source_candidates = tuple(
            _NumericSpanCandidate(
                value=token.value,
                evidence_id=spans_by_id[anchor_span_id].parent_evidence_id,
                anchor_span_id=anchor_span_id,
                parent_anchor_start=spans_by_id[anchor_span_id].start,
                source_start=token.start,
                source_end=token.end,
            )
            for anchor_span_id in selected_anchor_span_ids
            for token in classify_numeric_tokens(spans_by_id[anchor_span_id].canonical_text)
            if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
        )
        claims: list[GroundedNumericClaim] = []
        source_candidates_seen: set[tuple[str, int, int]] = set()
        for assertion_token in classify_numeric_tokens(proposal.assertion):
            if assertion_token.classification != NumericTokenClass.QUANTITATIVE_VALUE:
                continue
            candidates = tuple(candidate for candidate in source_candidates if candidate.value == assertion_token.value)
            source_candidates_seen.update(
                (candidate.anchor_span_id, candidate.source_start, candidate.source_end) for candidate in candidates
            )
            assertion_span = _numeric_span_reference(proposal.assertion, assertion_token.start, assertion_token.end)
            if len(candidates) == 1:
                candidate = candidates[0]
                claim = GroundedNumericClaim(
                    value=candidate.value,
                    evidence_id=candidate.evidence_id,
                    anchor_span_id=candidate.anchor_span_id,
                )
                if claim not in claims:
                    claims.append(claim)
                audits.append(
                    Step2ANumericClaimAudit(
                        proposal_key=proposal.proposal_key,
                        classification=NumericTokenClass.QUANTITATIVE_VALUE,
                        state=NumericClaimDerivationState.DERIVED,
                        assertion_span=assertion_span,
                        evidence_id=candidate.evidence_id,
                        anchor_span_id=candidate.anchor_span_id,
                        source_span=_numeric_span_reference(
                            spans_by_id[candidate.anchor_span_id].canonical_text,
                            candidate.source_start,
                            candidate.source_end,
                            parent_offset=candidate.parent_anchor_start,
                        ),
                        derived_claim=claim,
                        basis="one exact complete quantitative expression is shared by assertion and one selected anchor span",
                    )
                )
                continue

            candidate_evidence_ids = tuple(dict.fromkeys(candidate.evidence_id for candidate in candidates))
            candidate_anchor_span_ids = tuple(dict.fromkeys(candidate.anchor_span_id for candidate in candidates))
            candidate_span_sha256s = tuple(
                dict.fromkeys(hashlib.sha256(candidate.value.encode("utf-8")).hexdigest() for candidate in candidates)
            )
            if candidates:
                state = (
                    NumericClaimDerivationState.AMBIGUOUS_EVIDENCE
                    if len(candidate_evidence_ids) > 1
                    else NumericClaimDerivationState.MULTIPLE_CANDIDATE_SPANS
                )
                basis = "more than one selected source token exactly matches the asserted quantitative expression"
            else:
                state = NumericClaimDerivationState.ASSERTION_WITHOUT_EVIDENCE
                basis = "no selected anchor span contains the asserted complete quantitative expression"
            audits.append(
                Step2ANumericClaimAudit(
                    proposal_key=proposal.proposal_key,
                    classification=NumericTokenClass.QUANTITATIVE_VALUE,
                    state=state,
                    assertion_span=assertion_span,
                    candidate_evidence_ids=candidate_evidence_ids,
                    candidate_anchor_span_ids=candidate_anchor_span_ids,
                    candidate_span_sha256s=candidate_span_sha256s,
                    basis=basis,
                )
            )
        if not proposal.complete_table_semantic_coverages:
            for candidate in source_candidates:
                candidate_key = (candidate.anchor_span_id, candidate.source_start, candidate.source_end)
                if candidate_key in source_candidates_seen:
                    continue
                audits.append(
                    Step2ANumericClaimAudit(
                        proposal_key=proposal.proposal_key,
                        classification=NumericTokenClass.QUANTITATIVE_VALUE,
                        state=NumericClaimDerivationState.EVIDENCE_WITHOUT_ASSERTION,
                        evidence_id=candidate.evidence_id,
                        anchor_span_id=candidate.anchor_span_id,
                        source_span=_numeric_span_reference(
                            spans_by_id[candidate.anchor_span_id].canonical_text,
                            candidate.source_start,
                            candidate.source_end,
                            parent_offset=candidate.parent_anchor_start,
                        ),
                        candidate_evidence_ids=(candidate.evidence_id,),
                        candidate_anchor_span_ids=(candidate.anchor_span_id,),
                        candidate_span_sha256s=(hashlib.sha256(candidate.value.encode("utf-8")).hexdigest(),),
                        basis="a selected quantitative source expression was not expressed by the Fact assertion",
                    )
                )
        canonical_proposals.append(proposal.model_copy(update={"numeric_claims": tuple(claims)}))
    return Step2ANumericClaimDerivationResult(
        proposals=tuple(canonical_proposals),
        audits=tuple(audits),
        table_occurrence_audits=tuple(table_occurrence_audits),
        table_structural_index_occurrence_audits=tuple(table_structural_index_occurrence_audits),
        table_structural_projection_audits=tuple(table_structural_projection_audits),
    )
