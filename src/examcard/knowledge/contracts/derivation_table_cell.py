"""Step 2A table-cell anchor derivation (cm36 split)."""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .audits import (
    Step2ATableCellAnchorAudit,
    Step2ATableCellDerivationResult,
    TableCellAnchorDerivationState,
)
from .proposals import Step2AFactProposal
from .request_helpers import (
    _request_structured_tables,
    _step2a_anchor_sources,
)
from .table_inputs import (
    CompleteTableSemanticCoverage,
    _geometry_header_cell_ids,
)
from ..schema import FactType

if TYPE_CHECKING:
    from .step2a_request import Step2ARequest


def derive_step2a_table_cell_anchors(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> Step2ATableCellDerivationResult:
    """Record the local table-cell parent derivation after span selection."""

    _, table_cell_span_ids, _, _ = _step2a_anchor_sources(request)
    tables = _request_structured_tables(request)
    table_span_ids = {
        table.table_evidence_id: tuple(
            span.anchor_span_id
            for row in table.rows
            for cell in row.cells
            for span in cell.anchor_spans
        )
        for table in tables
    }
    canonical_proposals: list[Step2AFactProposal] = []
    audits: list[Step2ATableCellAnchorAudit] = []
    for proposal in proposals:
        expected_spans = tuple(
            span_id
            for span_id in (*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids)
            if span_id in table_cell_span_ids
        )
        if proposal.table_cell_anchor_span_ids != expected_spans:
            raise ValueError("canonical table-cell spans must equal selected structured cell spans")
        if not proposal.table_cell_evidence_ids:
            state = TableCellAnchorDerivationState.NOT_APPLICABLE
        else:
            state = TableCellAnchorDerivationState.DERIVED_FROM_SELECTED_EVIDENCE
        selected_table_ids = tuple(
            table.table_evidence_id
            for table in tables
            if set(table_span_ids[table.table_evidence_id]) & set(expected_spans)
        )
        complete_coverages: list[CompleteTableSemanticCoverage] = []
        if proposal.fact_type == FactType.COMPOSITION:
            for table in tables:
                expected_table_spans = set(table_span_ids[table.table_evidence_id])
                if expected_table_spans <= set(expected_spans):
                    cells = tuple(cell for row in table.rows for cell in row.cells)
                    complete_coverages.append(
                        CompleteTableSemanticCoverage(
                            table_evidence_id=table.table_evidence_id,
                            row_count=table.row_count,
                            column_count=table.column_count,
                            row_evidence_ids=tuple(row.evidence_id for row in table.rows),
                            header_cell_evidence_ids=_geometry_header_cell_ids(table),
                            cell_evidence_ids=tuple(cell.evidence_id for cell in cells),
                            cell_anchor_span_ids=table_span_ids[table.table_evidence_id],
                        )
                    )
        canonical_proposals.append(
            proposal.model_copy(
                update={
                    "selected_table_evidence_ids": selected_table_ids,
                    "complete_table_semantic_coverages": tuple(complete_coverages),
                }
            )
        )
        audits.append(
            Step2ATableCellAnchorAudit(
                proposal_key=proposal.proposal_key,
                model_table_cell_evidence_ids=(),
                derived_table_cell_evidence_ids=proposal.table_cell_evidence_ids,
                state=state,
                basis="table-cell parent Evidence IDs were derived from the Fact-selected anchor spans",
            )
        )
    return Step2ATableCellDerivationResult(proposals=tuple(canonical_proposals), audits=tuple(audits))
