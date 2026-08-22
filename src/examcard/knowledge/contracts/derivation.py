"""Local deterministic derivations: span selection, table cells, condition/numeric claims."""
from __future__ import annotations
import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from ...evidence.layout import normalized_compare_text
from ..schema import FactType

from .audits import (
    AnchorSpanSelectionDerivationState,
    ConditionClaimDerivationState,
    NumericClaimDerivationState,
    Step2AAnchorSpanDerivationResult,
    Step2AAnchorSpanSelectionAudit,
    Step2ACompleteTableStructuralProjectionAudit,
    Step2AConditionClaimAudit,
    Step2AConditionClaimDerivationResult,
    Step2ANumericClaimAudit,
    Step2ANumericClaimDerivationResult,
    Step2ATableCellAnchorAudit,
    Step2ATableCellDerivationResult,
    Step2ATableNumericOccurrenceAudit,
    Step2ATableStructuralIndexOccurrenceAudit,
    TableCellAnchorDerivationState,
)
from .claims import GroundedConditionClaim, GroundedNumericClaim
from .enums import (
    ConditionOperator,
    NumericTokenClass,
    _condition_operator_occurrences,
    condition_operators_in_text,
)
from .numeric_tokens import (
    NumericTokenClassificationRecord,
    _TABLE_GRID_DIMENSION_PATTERNS,
    classify_numeric_tokens,
)
from .occurrences import (
    CompleteTableStructuralProjection,
    CompleteTableStructuralProjectionState,
    ConditionSpanReference,
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
from .proposals import (
    Step2AFactProposal,
    Step2AModelFactProposal,
    Step2AV13ModelFactProposal,
    Step2AV14ModelFactProposal,
    Step2AV15ModelFactProposal,
    Step2AV16ModelFactProposal,
    Step2AV17ModelFactProposal,
    Step2AV18ModelFactProposal,
    Step2AV19ModelFactProposal,
    Step2AV20ModelFactProposal,
    Step2AV21ModelFactProposal,
    Step2AV22ModelFactProposal,
    Step2AV23ModelFactProposal,
)
from .request_helpers import (
    _derive_v13_completeness_mode,
    _derive_v13_source_section_ids,
    _ordered_parent_evidence_ids,
    _request_structured_tables,
    _step2a_anchor_sources,
)
from .table_inputs import CompleteTableSemanticCoverage, _geometry_header_cell_ids
from .versions import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
)

if TYPE_CHECKING:
    from .step2a_request import Step2ARequest


def derive_step2a_anchor_span_selection(
    *,
    request: Step2ARequest,
    proposals: Iterable[
        Step2AModelFactProposal
        | Step2AV13ModelFactProposal
        | Step2AV14ModelFactProposal
        | Step2AV15ModelFactProposal
        | Step2AV16ModelFactProposal
        | Step2AV17ModelFactProposal
        | Step2AV18ModelFactProposal
        | Step2AV19ModelFactProposal
        | Step2AV20ModelFactProposal
        | Step2AV21ModelFactProposal
        | Step2AV22ModelFactProposal
        | Step2AV23ModelFactProposal
    ],
) -> Step2AAnchorSpanDerivationResult:
    """Map model-selected child span IDs to immutable parent Evidence locally.

    The model cannot select parent Evidence. V15+ has one exhaustive selected
    role, so parent-role collision is structurally unrepresentable there.
    Unknown, out-of-section, duplicate, and incomplete selections remain
    auditable failures and never create a canonical Fact proposal.
    """

    spans_by_id, table_cell_span_ids, section_ids, span_sections = _step2a_anchor_sources(request)
    canonical: list[Step2AFactProposal] = []
    audits: list[Step2AAnchorSpanSelectionAudit] = []
    for proposal in proposals:
        expected_minimal_type = {
            MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION: Step2AV13ModelFactProposal,
            NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION: Step2AV14ModelFactProposal,
            SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION: Step2AV15ModelFactProposal,
            DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION: Step2AV16ModelFactProposal,
            TABLE_CONTINUATION_STEP2A_REQUEST_VERSION: Step2AV17ModelFactProposal,
            TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION: Step2AV18ModelFactProposal,
            TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION: Step2AV19ModelFactProposal,
            TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION: Step2AV20ModelFactProposal,
            COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION: Step2AV21ModelFactProposal,
            STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION: Step2AV22ModelFactProposal,
            SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION: Step2AV23ModelFactProposal,
        }.get(request.identity.request_version)
        is_minimal = expected_minimal_type is not None
        # 注意这条 isinstance 的第二参是 `ModelMetaclass | None`（pydantic 模型类在 mypy
        # 眼里是 ModelMetaclass），因此本行会报一条 arg-type，并且 mypy 会把 proposal 在
        # 本行之后退化成 Any——那正好压住了循环体里约 50 条 union-attr。换句话说这条
        # arg-type 是一块「盖子」：把它按正解修掉（例如把校验挪进独立函数、或给字典标
        # `dict[str, type[Step2AFactProposal]]`），本文件立刻从 1 条涨到 51 条，露出的是
        # 「某字段只在某些请求版本上存在」这一族真问题，需要按版本逐个收窄才能收口。
        # 已实测两种改法都会掀开盖子，所以先保持现状、把盲区写在这里，不假装它不存在。
        if is_minimal and not isinstance(proposal, expected_minimal_type):
            raise ValueError("Step 2A model proposal shape does not match its immutable request version")
        if not is_minimal and isinstance(
            proposal,
            (Step2AV13ModelFactProposal, Step2AV14ModelFactProposal, Step2AV15ModelFactProposal, Step2AV16ModelFactProposal, Step2AV17ModelFactProposal, Step2AV18ModelFactProposal, Step2AV19ModelFactProposal, Step2AV20ModelFactProposal, Step2AV21ModelFactProposal, Step2AV22ModelFactProposal, Step2AV23ModelFactProposal),
        ):
            raise ValueError("Step 2A model proposal shape does not match its immutable request version")
        is_single_role = isinstance(proposal, (Step2AV15ModelFactProposal, Step2AV16ModelFactProposal, Step2AV17ModelFactProposal, Step2AV18ModelFactProposal, Step2AV19ModelFactProposal, Step2AV20ModelFactProposal, Step2AV21ModelFactProposal, Step2AV22ModelFactProposal, Step2AV23ModelFactProposal))
        is_deterministic_numeric = isinstance(proposal, (Step2AV16ModelFactProposal, Step2AV17ModelFactProposal, Step2AV18ModelFactProposal, Step2AV19ModelFactProposal, Step2AV20ModelFactProposal, Step2AV21ModelFactProposal, Step2AV22ModelFactProposal, Step2AV23ModelFactProposal))
        secondary_anchor_span_ids = () if is_single_role else proposal.secondary_anchor_span_ids
        proposal_key = proposal.derived_proposal_key if is_minimal else proposal.proposal_key
        selected_span_ids = tuple(dict.fromkeys((*proposal.primary_anchor_span_ids, *secondary_anchor_span_ids)))
        unknown_ids = tuple(span_id for span_id in selected_span_ids if span_id not in spans_by_id)
        if unknown_ids:
            audits.append(
                Step2AAnchorSpanSelectionAudit(
                    proposal_key=proposal_key,
                    state=AnchorSpanSelectionDerivationState.UNKNOWN_SPAN,
                    primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                    secondary_anchor_span_ids=secondary_anchor_span_ids,
                    basis="one or more model-selected anchor spans are absent from the immutable projection",
                )
            )
            continue
        source_section_ids = (
            _derive_v13_source_section_ids(request, selected_span_ids)
            if is_minimal
            else proposal.source_section_ids
        )
        declared_sections = set(source_section_ids)
        if not is_minimal and (
            not declared_sections <= section_ids
            or any(not (span_sections[span_id] & declared_sections) for span_id in selected_span_ids)
        ):
            audits.append(
                Step2AAnchorSpanSelectionAudit(
                    proposal_key=proposal_key,
                    state=AnchorSpanSelectionDerivationState.OUT_OF_SECTION,
                    primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                    secondary_anchor_span_ids=secondary_anchor_span_ids,
                    basis="a selected anchor span is not available from the proposal's declared source section",
                )
            )
            continue
        primary_parent_ids = _ordered_parent_evidence_ids(proposal.primary_anchor_span_ids, spans_by_id)
        secondary_parent_ids = _ordered_parent_evidence_ids(secondary_anchor_span_ids, spans_by_id)
        if set(primary_parent_ids) & set(secondary_parent_ids):
            audits.append(
                Step2AAnchorSpanSelectionAudit(
                    proposal_key=proposal_key,
                    state=AnchorSpanSelectionDerivationState.PARENT_ROLE_COLLISION,
                    primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                    secondary_anchor_span_ids=secondary_anchor_span_ids,
                    basis="one immutable parent Evidence cannot be primary and secondary through different selected child spans",
                )
            )
            continue
        if is_minimal:
            completeness_mode, completeness_basis = _derive_v13_completeness_mode(
                request=request,
                fact_type=proposal.fact_type,
                selected_span_ids=selected_span_ids,
            )
            if completeness_mode is None:
                audits.append(
                    Step2AAnchorSpanSelectionAudit(
                        proposal_key=proposal_key,
                        state=AnchorSpanSelectionDerivationState.COMPLETENESS_AMBIGUOUS,
                        primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                        secondary_anchor_span_ids=secondary_anchor_span_ids,
                        basis=completeness_basis,
                    )
                )
                continue
        else:
            completeness_mode = proposal.completeness_mode
            completeness_basis = "model-declared legacy completeness retained under the pre-V13 contract"
        model_numeric_claims = () if is_deterministic_numeric else proposal.numeric_claims
        numeric_span_ids = tuple(claim.anchor_span_id for claim in model_numeric_claims)
        if any(span_id not in selected_span_ids for span_id in numeric_span_ids):
            audits.append(
                Step2AAnchorSpanSelectionAudit(
                    proposal_key=proposal_key,
                    state=AnchorSpanSelectionDerivationState.NUMERIC_SPAN_UNSELECTED,
                    primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                    secondary_anchor_span_ids=secondary_anchor_span_ids,
                    basis="a numeric claim references an anchor span not selected by the same Fact",
                )
            )
            continue
        table_span_ids = tuple(span_id for span_id in selected_span_ids if span_id in table_cell_span_ids)
        table_parent_ids = _ordered_parent_evidence_ids(table_span_ids, spans_by_id)
        canonical.append(
            Step2AFactProposal(
                proposal_key=proposal_key,
                source_section_ids=source_section_ids,
                fact_type=proposal.fact_type,
                label=proposal.label,
                assertion=proposal.assertion,
                completeness_mode=completeness_mode,
                primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                secondary_anchor_span_ids=secondary_anchor_span_ids,
                primary_evidence_ids=primary_parent_ids,
                secondary_evidence_ids=secondary_parent_ids,
                table_cell_anchor_span_ids=table_span_ids,
                table_cell_evidence_ids=table_parent_ids,
                numeric_claims=tuple(
                    GroundedNumericClaim(
                        value=claim.value,
                        anchor_span_id=claim.anchor_span_id,
                        evidence_id=spans_by_id[claim.anchor_span_id].parent_evidence_id,
                    )
                    for claim in model_numeric_claims
                ),
            )
        )
        audits.append(
            Step2AAnchorSpanSelectionAudit(
                proposal_key=proposal_key,
                state=AnchorSpanSelectionDerivationState.DERIVED,
                primary_anchor_span_ids=proposal.primary_anchor_span_ids,
                secondary_anchor_span_ids=secondary_anchor_span_ids,
                primary_evidence_ids=primary_parent_ids,
                secondary_evidence_ids=secondary_parent_ids,
                table_cell_anchor_span_ids=table_span_ids,
                table_cell_evidence_ids=table_parent_ids,
                basis=(
                    "all selected spans are projection-owned; proposal ID, source sections, parent Evidence, and "
                    f"completeness were derived locally ({completeness_basis})"
                    if is_minimal
                    else "all model-selected child spans are projection-owned and parent Evidence IDs were derived locally"
                ),
            )
        )
    return Step2AAnchorSpanDerivationResult(proposals=tuple(canonical), audits=tuple(audits))


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


_CONDITION_SPAN_BOUNDARIES = frozenset("，,；;。！？!?：:\n\r")


@dataclass(frozen=True)
class _ConditionSpanCandidate:
    value: str
    assertion_start: int
    assertion_end: int
    evidence_id: str
    anchor_span_id: str
    parent_anchor_start: int
    source_start: int
    source_end: int
    operator: ConditionOperator


def _condition_span_reference(text: str, start: int, end: int, *, parent_offset: int = 0) -> ConditionSpanReference:
    return ConditionSpanReference(
        start=start + parent_offset,
        end=end + parent_offset,
        sha256=hashlib.sha256(text[start:end].encode("utf-8")).hexdigest(),
    )


def _complete_condition_span(value: str) -> bool:
    """A condition span must contain more than a recognized operator."""

    operators = condition_operators_in_text(value)
    if not operators:
        return False
    remainder = normalized_compare_text(value)
    for operator in set(operators):
        remainder = remainder.replace(normalized_compare_text(operator.value), "")
    return bool(remainder)


def _exact_common_condition_span(
    *,
    assertion: str,
    assertion_start: int,
    assertion_end: int,
    source_text: str,
    source_start: int,
    source_end: int,
    evidence_id: str,
    anchor_span_id: str,
    parent_anchor_start: int,
    operator: ConditionOperator,
) -> _ConditionSpanCandidate:
    """Find the maximal exact, punctuation-bounded common span around one operator."""

    left_assertion = assertion_start
    left_source = source_start
    while left_assertion > 0 and left_source > 0:
        assertion_char = assertion[left_assertion - 1]
        source_char = source_text[left_source - 1]
        if (
            assertion_char in _CONDITION_SPAN_BOUNDARIES
            or source_char in _CONDITION_SPAN_BOUNDARIES
            or assertion_char != source_char
        ):
            break
        left_assertion -= 1
        left_source -= 1

    right_assertion = assertion_end
    right_source = source_end
    while right_assertion < len(assertion) and right_source < len(source_text):
        assertion_char = assertion[right_assertion]
        source_char = source_text[right_source]
        if (
            assertion_char in _CONDITION_SPAN_BOUNDARIES
            or source_char in _CONDITION_SPAN_BOUNDARIES
            or assertion_char != source_char
        ):
            break
        right_assertion += 1
        right_source += 1

    while left_assertion < right_assertion and assertion[left_assertion].isspace():
        left_assertion += 1
        left_source += 1
    while right_assertion > left_assertion and assertion[right_assertion - 1].isspace():
        right_assertion -= 1
        right_source -= 1
    value = assertion[left_assertion:right_assertion]
    if value != source_text[left_source:right_source]:
        raise ValueError("condition candidate must remain an exact assertion/Evidence span")
    return _ConditionSpanCandidate(
        value=value,
        assertion_start=left_assertion,
        assertion_end=right_assertion,
        evidence_id=evidence_id,
        anchor_span_id=anchor_span_id,
        parent_anchor_start=parent_anchor_start,
        source_start=left_source,
        source_end=right_source,
        operator=operator,
    )


def derive_step2a_condition_claims(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> Step2AConditionClaimDerivationResult:
    """Derive condition claims only from one exact assertion/Evidence span.

    There is deliberately no best-effort selection. A condition is accepted only
    when one complete span is uniquely shared by the assertion and one selected
    projection anchor span. Every other state is retained as non-sensitive audit data and
    is rejected by the Step 2A grounding contract.
    """

    spans_by_id, _, _, _ = _step2a_anchor_sources(request)
    canonical_proposals: list[Step2AFactProposal] = []
    audits: list[Step2AConditionClaimAudit] = []
    for proposal in proposals:
        if proposal.complete_table_semantic_coverages:
            # A complete-table composition Fact preserves every cell through
            # its structured grid. It is not an assertion of each individual
            # row rule, so condition claims remain the responsibility of any
            # separately selected row-level Fact.
            canonical_proposals.append(proposal.model_copy(update={"condition_claims": ()}))
            continue
        selected_anchor_span_ids = tuple(dict.fromkeys((*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids)))
        selected_sources = tuple(
            (anchor_span_id, spans_by_id[anchor_span_id])
            for anchor_span_id in selected_anchor_span_ids
            if anchor_span_id in spans_by_id and spans_by_id[anchor_span_id].canonical_text
        )
        assertion_occurrences = _condition_operator_occurrences(proposal.assertion)
        source_occurrences = tuple(
            (anchor_span_id, start, end, operator)
            for anchor_span_id, anchor_span in selected_sources
            for start, end, operator in _condition_operator_occurrences(anchor_span.canonical_text)
        )
        source_occurrences_seen: set[tuple[str, int, int, ConditionOperator]] = set()
        claims: list[GroundedConditionClaim] = []

        for assertion_start, assertion_end, operator in assertion_occurrences:
            raw_candidates: list[_ConditionSpanCandidate] = []
            for anchor_span_id, source_start, source_end, source_operator in source_occurrences:
                if source_operator != operator:
                    continue
                anchor_span = spans_by_id[anchor_span_id]
                candidate = _exact_common_condition_span(
                    assertion=proposal.assertion,
                    assertion_start=assertion_start,
                    assertion_end=assertion_end,
                    source_text=anchor_span.canonical_text,
                    source_start=source_start,
                    source_end=source_end,
                    evidence_id=anchor_span.parent_evidence_id,
                    anchor_span_id=anchor_span_id,
                    parent_anchor_start=anchor_span.start,
                    operator=operator,
                )
                source_occurrences_seen.add((anchor_span_id, source_start, source_end, source_operator))
                raw_candidates.append(candidate)

            candidates = tuple(
                dict.fromkeys(
                    candidate
                    for candidate in raw_candidates
                    if _complete_condition_span(candidate.value)
                )
            )
            assertion_operator_span = _condition_span_reference(proposal.assertion, assertion_start, assertion_end)
            if len(candidates) == 1:
                candidate = candidates[0]
                claim = GroundedConditionClaim(
                    value=candidate.value,
                    evidence_id=candidate.evidence_id,
                    anchor_span_id=candidate.anchor_span_id,
                )
                if claim not in claims:
                    claims.append(claim)
                audits.append(
                    Step2AConditionClaimAudit(
                        proposal_key=proposal.proposal_key,
                        operator=operator,
                        state=ConditionClaimDerivationState.DERIVED,
                        assertion_span=_condition_span_reference(
                            proposal.assertion, candidate.assertion_start, candidate.assertion_end
                        ),
                        evidence_id=candidate.evidence_id,
                        anchor_span_id=candidate.anchor_span_id,
                        source_span=_condition_span_reference(
                            spans_by_id[candidate.anchor_span_id].canonical_text,
                            candidate.source_start,
                            candidate.source_end,
                            parent_offset=candidate.parent_anchor_start,
                        ),
                        derived_claim=claim,
                        basis="one unique punctuation-bounded exact condition span shared by assertion and selected Evidence",
                    )
                )
                continue

            audit_candidates = candidates or tuple(dict.fromkeys(raw_candidates))
            candidate_evidence_ids = tuple(dict.fromkeys(candidate.evidence_id for candidate in audit_candidates))
            candidate_anchor_span_ids = tuple(
                dict.fromkeys(candidate.anchor_span_id for candidate in audit_candidates)
            )
            candidate_span_sha256s = tuple(
                dict.fromkeys(
                    hashlib.sha256(candidate.value.encode("utf-8")).hexdigest() for candidate in audit_candidates
                )
            )
            if candidates:
                state = (
                    ConditionClaimDerivationState.AMBIGUOUS_EVIDENCE
                    if len(candidate_evidence_ids) > 1
                    else ConditionClaimDerivationState.MULTIPLE_CANDIDATE_SPANS
                )
                basis = "more than one exact complete condition candidate matched; no anchor span was selected"
            elif raw_candidates:
                state = ConditionClaimDerivationState.ISOLATED_OPERATOR
                basis = "the only assertion/Evidence overlap was an isolated condition operator"
            else:
                state = ConditionClaimDerivationState.ASSERTION_WITHOUT_EVIDENCE
                basis = "no exact complete condition span matched any selected anchor span"
            audits.append(
                Step2AConditionClaimAudit(
                    proposal_key=proposal.proposal_key,
                    operator=operator,
                    state=state,
                    assertion_span=(
                        _condition_span_reference(
                            proposal.assertion, raw_candidates[0].assertion_start, raw_candidates[0].assertion_end
                        )
                        if state == ConditionClaimDerivationState.ISOLATED_OPERATOR and len(raw_candidates) == 1
                        else assertion_operator_span
                    ),
                    evidence_id=(
                        raw_candidates[0].evidence_id
                        if state == ConditionClaimDerivationState.ISOLATED_OPERATOR and len(raw_candidates) == 1
                        else None
                    ),
                    anchor_span_id=(
                        raw_candidates[0].anchor_span_id
                        if state == ConditionClaimDerivationState.ISOLATED_OPERATOR and len(raw_candidates) == 1
                        else None
                    ),
                    source_span=(
                        _condition_span_reference(
                            spans_by_id[raw_candidates[0].anchor_span_id].canonical_text,
                            raw_candidates[0].source_start,
                            raw_candidates[0].source_end,
                            parent_offset=raw_candidates[0].parent_anchor_start,
                        )
                        if state == ConditionClaimDerivationState.ISOLATED_OPERATOR and len(raw_candidates) == 1
                        else None
                    ),
                    candidate_evidence_ids=candidate_evidence_ids,
                    candidate_anchor_span_ids=candidate_anchor_span_ids,
                    candidate_span_sha256s=candidate_span_sha256s,
                    basis=basis,
                )
            )

        for anchor_span_id, source_start, source_end, operator in source_occurrences:
            source_key = (anchor_span_id, source_start, source_end, operator)
            if source_key in source_occurrences_seen:
                continue
            anchor_span = spans_by_id[anchor_span_id]
            audits.append(
                Step2AConditionClaimAudit(
                    proposal_key=proposal.proposal_key,
                    operator=operator,
                    state=ConditionClaimDerivationState.EVIDENCE_WITHOUT_ASSERTION,
                    evidence_id=anchor_span.parent_evidence_id,
                    anchor_span_id=anchor_span_id,
                    source_span=_condition_span_reference(
                        anchor_span.canonical_text,
                        source_start,
                        source_end,
                        parent_offset=anchor_span.start,
                    ),
                    basis="selected anchor span contains a condition operator with no matching assertion operator",
                )
            )
        canonical_proposals.append(proposal.model_copy(update={"condition_claims": tuple(claims)}))
    return Step2AConditionClaimDerivationResult(proposals=tuple(canonical_proposals), audits=tuple(audits))


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


# 下面两个 TypedDict 描述「一份 kwargs 同时喂给 occurrence_id 构造器和 occurrence 模型」
# 这个复用姿态。以前这两处写成裸字典，被推成 dict[str, object]，`**` 解包进强类型形参时
# mypy 无法按键分辨 str/int/tuple，于是每个形参各报一条 arg-type。键名与
# occurrences.make_table_* 的形参逐字对齐；改那边的签名必须同步改这里，否则解包处直接报错。
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


# make_table_structural_index_occurrence_id 与 make_table_numeric_occurrence_id 形参完全相同，
# 共用一个形状。
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
