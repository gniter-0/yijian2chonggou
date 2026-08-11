"""Fail-closed Step 2A audit models and assessment functions."""
from __future__ import annotations
import hashlib
from collections.abc import Iterable
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from ...evidence.layout import normalized_compare_text
from ...materials.schema import StrictModel
from ..continuation.review import ContinuationMarkerDisposition
from ..schema import CompletenessMode, FactType

from .claims import GroundedConditionClaim, GroundedNumericClaim
from .continuation import ContinuationBindingState
from .enums import ConditionOperator, NumericTokenClass
from .obligations import Step2ACoverageObligationKind
from .occurrences import (
    CompleteTableStructuralProjection,
    CompleteTableStructuralProjectionState,
    ConditionSpanReference,
    NumericSpanReference,
    TableGridMetadataReference,
    TableNumericOccurrenceReference,
    TableNumericOccurrenceState,
    TableStructuralIndexOccurrenceReference,
    TableStructuralIndexOccurrenceState,
)
from .proposals import Step2AFactProposal
from .request_helpers import _request_structured_tables
from .request_identity import _SHA256_PATTERN
from .table_inputs import TableAtomicSubfactKind, _geometry_header_cell_ids
from .versions import _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS

if TYPE_CHECKING:
    from .step2a_request import Step2ARequest


class AnchorSpanSelectionDerivationState(StrEnum):
    DERIVED = "derived"
    UNKNOWN_SPAN = "unknown_span"
    OUT_OF_SECTION = "out_of_section"
    PARENT_ROLE_COLLISION = "parent_role_collision"
    NUMERIC_SPAN_UNSELECTED = "numeric_span_unselected"
    COMPLETENESS_AMBIGUOUS = "completeness_ambiguous"


class Step2AAnchorSpanSelectionAudit(StrictModel):
    """Local proof that model-selected child spans map to permitted parent Evidence."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    state: AnchorSpanSelectionDerivationState
    primary_anchor_span_ids: tuple[str, ...]
    secondary_anchor_span_ids: tuple[str, ...] = ()
    primary_evidence_ids: tuple[str, ...] = ()
    secondary_evidence_ids: tuple[str, ...] = ()
    table_cell_anchor_span_ids: tuple[str, ...] = ()
    table_cell_evidence_ids: tuple[str, ...] = ()
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_selection_audit(self) -> "Step2AAnchorSpanSelectionAudit":
        all_spans = [*self.primary_anchor_span_ids, *self.secondary_anchor_span_ids]
        if not self.primary_anchor_span_ids or len(all_spans) != len(set(all_spans)):
            raise ValueError("anchor selection audit requires unique primary/secondary span IDs")
        if self.state == AnchorSpanSelectionDerivationState.DERIVED:
            if not self.primary_evidence_ids:
                raise ValueError("derived anchor selection audits require primary parent Evidence")
        elif self.primary_evidence_ids or self.secondary_evidence_ids or self.table_cell_evidence_ids:
            raise ValueError("failed anchor selection audits cannot derive parent Evidence")
        return self


class Step2AAnchorSpanDerivationResult(StrictModel):
    proposals: tuple[Step2AFactProposal, ...]
    audits: tuple[Step2AAnchorSpanSelectionAudit, ...]

    @model_validator(mode="after")
    def validate_result(self) -> "Step2AAnchorSpanDerivationResult":
        proposal_keys = [proposal.proposal_key for proposal in self.proposals]
        audit_keys = [audit.proposal_key for audit in self.audits]
        if len(audit_keys) != len(set(audit_keys)) or not set(proposal_keys) <= set(audit_keys):
            raise ValueError("anchor-span derivation audits must uniquely cover every canonical Fact proposal")
        derived_keys = {audit.proposal_key for audit in self.audits if audit.state == AnchorSpanSelectionDerivationState.DERIVED}
        if set(proposal_keys) != derived_keys:
            raise ValueError("only successfully derived span selections may create canonical Fact proposals")
        return self


class TableCellAnchorDerivationState(StrEnum):
    """How the adapter reconciled model-declared cells with selected Evidence."""

    NOT_APPLICABLE = "not_applicable"
    MATCHED_MODEL_SELECTION = "matched_model_selection"
    DERIVED_FROM_SELECTED_EVIDENCE = "derived_from_selected_evidence"


class Step2ATableCellAnchorAudit(StrictModel):
    """Non-sensitive audit trail for canonical table-cell anchor derivation."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    model_table_cell_evidence_ids: tuple[str, ...] = ()
    derived_table_cell_evidence_ids: tuple[str, ...] = ()
    state: TableCellAnchorDerivationState
    basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2ATableCellAnchorAudit":
        if len(self.model_table_cell_evidence_ids) != len(set(self.model_table_cell_evidence_ids)):
            raise ValueError("table-cell anchor audit model IDs must be unique")
        if len(self.derived_table_cell_evidence_ids) != len(set(self.derived_table_cell_evidence_ids)):
            raise ValueError("table-cell anchor audit derived IDs must be unique")
        if self.state == TableCellAnchorDerivationState.NOT_APPLICABLE:
            if self.model_table_cell_evidence_ids or self.derived_table_cell_evidence_ids:
                raise ValueError("not-applicable table-cell audit cannot contain Evidence IDs")
        elif self.state == TableCellAnchorDerivationState.MATCHED_MODEL_SELECTION:
            if self.model_table_cell_evidence_ids != self.derived_table_cell_evidence_ids:
                raise ValueError("matched table-cell audit requires identical model and derived IDs")
        elif self.model_table_cell_evidence_ids == self.derived_table_cell_evidence_ids:
            raise ValueError("derived table-cell audit requires a recorded model/derived difference")
        return self


class Step2ATableCellDerivationResult(StrictModel):
    """Canonicalized Fact proposals plus the proof of each table-cell derivation."""

    proposals: tuple[Step2AFactProposal, ...]
    audits: tuple[Step2ATableCellAnchorAudit, ...]

    @model_validator(mode="after")
    def validate_result(self) -> "Step2ATableCellDerivationResult":
        proposal_keys = [proposal.proposal_key for proposal in self.proposals]
        audit_keys = [audit.proposal_key for audit in self.audits]
        if proposal_keys != audit_keys:
            raise ValueError("table-cell derivation audits must exactly align with Fact proposals")
        return self


class TableSemanticCoverageState(StrEnum):
    """Disposition of one complete structured table in a Step 2A response."""

    COMPLETE = "complete"
    MISSING_COMPOSITION = "missing_composition"
    INCOMPLETE_COMPOSITION = "incomplete_composition"
    ROW_LEVEL_SUBSTITUTION = "row_level_substitution"
    MULTIPLE_COMPOSITION_FACTS = "multiple_composition_facts"


class Step2ATableSemanticCoverageAudit(StrictModel):
    """Proof that a complete structured table has one canonical Fact owner."""

    table_evidence_id: str
    row_evidence_ids: tuple[str, ...]
    header_cell_evidence_ids: tuple[str, ...]
    cell_evidence_ids: tuple[str, ...]
    cell_anchor_span_ids: tuple[str, ...]
    state: TableSemanticCoverageState
    candidate_proposal_keys: tuple[str, ...] = ()
    canonical_proposal_key: str | None = Field(
        default=None,
        pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$",
    )
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2ATableSemanticCoverageAudit":
        for label, identifiers in {
            "row": self.row_evidence_ids,
            "header cell": self.header_cell_evidence_ids,
            "cell": self.cell_evidence_ids,
            "cell anchor span": self.cell_anchor_span_ids,
        }.items():
            if not identifiers or len(identifiers) != len(set(identifiers)):
                raise ValueError(f"complete-table coverage audits require unique {label} IDs")
        if len(self.candidate_proposal_keys) != len(set(self.candidate_proposal_keys)):
            raise ValueError("complete-table coverage audits require unique candidate proposal keys")
        if not set(self.header_cell_evidence_ids) <= set(self.cell_evidence_ids):
            raise ValueError("complete-table coverage audit header cells must be retained cells")
        if self.state == TableSemanticCoverageState.COMPLETE:
            if self.canonical_proposal_key is None or self.candidate_proposal_keys != (self.canonical_proposal_key,):
                raise ValueError("complete-table coverage audits require exactly one canonical composition proposal")
        elif self.canonical_proposal_key is not None:
            raise ValueError("failed complete-table coverage audits cannot claim a canonical proposal")
        if self.state == TableSemanticCoverageState.MISSING_COMPOSITION and self.candidate_proposal_keys:
            raise ValueError("missing complete-table coverage audits cannot claim candidates")
        if self.state not in {TableSemanticCoverageState.COMPLETE, TableSemanticCoverageState.MISSING_COMPOSITION} and not self.candidate_proposal_keys:
            raise ValueError("non-missing complete-table coverage audits require candidate proposals")
        return self


def assess_step2a_table_semantic_coverage(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> tuple[Step2ATableSemanticCoverageAudit, ...]:
    """Fail closed unless each supplied complete grid has one full composition Fact.

    A partial row Fact remains valid source-grounded data, but it cannot satisfy
    the semantic ownership requirement for a table. The table parent and rows
    are retained in the audit even though model output may only select child
    anchor spans.
    """

    frozen_proposals = tuple(proposals)
    audits: list[Step2ATableSemanticCoverageAudit] = []
    for table in _request_structured_tables(request):
        cells = tuple(cell for row in table.rows for cell in row.cells)
        expected_spans = {
            span.anchor_span_id
            for cell in cells
            for span in cell.anchor_spans
        }
        relevant = tuple(
            proposal
            for proposal in frozen_proposals
            if table.table_evidence_id in proposal.selected_table_evidence_ids
        )
        complete = tuple(
            proposal
            for proposal in relevant
            if any(
                coverage.table_evidence_id == table.table_evidence_id
                and set(coverage.cell_evidence_ids) == {cell.evidence_id for cell in cells}
                and set(coverage.cell_anchor_span_ids) == expected_spans
                for coverage in proposal.complete_table_semantic_coverages
            )
        )
        incomplete_compositions = tuple(
            proposal for proposal in relevant if proposal.fact_type == FactType.COMPOSITION
        )
        if len(complete) == 1:
            state = TableSemanticCoverageState.COMPLETE
            canonical_key = complete[0].proposal_key
            candidate_keys = (canonical_key,)
            basis = "one composition Fact selected every immutable table-cell anchor span and therefore retains the full header, rows, and grid."
        elif len(complete) > 1:
            state = TableSemanticCoverageState.MULTIPLE_COMPOSITION_FACTS
            canonical_key = None
            candidate_keys = tuple(proposal.proposal_key for proposal in complete)
            basis = "more than one composition Fact selected the same complete table grid; ownership is ambiguous."
        elif incomplete_compositions:
            state = TableSemanticCoverageState.INCOMPLETE_COMPOSITION
            canonical_key = None
            candidate_keys = tuple(proposal.proposal_key for proposal in incomplete_compositions)
            basis = "composition Fact candidates selected only part of the declared table grid."
        elif relevant:
            state = TableSemanticCoverageState.ROW_LEVEL_SUBSTITUTION
            canonical_key = None
            candidate_keys = tuple(proposal.proposal_key for proposal in relevant)
            basis = "row-level or cell-level Facts selected table content but no complete composition Fact owns the full grid."
        else:
            state = TableSemanticCoverageState.MISSING_COMPOSITION
            canonical_key = None
            candidate_keys = ()
            basis = "no Fact selected this complete structured table."
        audits.append(
            Step2ATableSemanticCoverageAudit(
                table_evidence_id=table.table_evidence_id,
                row_evidence_ids=tuple(row.evidence_id for row in table.rows),
                header_cell_evidence_ids=_geometry_header_cell_ids(table),
                cell_evidence_ids=tuple(cell.evidence_id for cell in cells),
                cell_anchor_span_ids=tuple(span.anchor_span_id for cell in cells for span in cell.anchor_spans),
                state=state,
                candidate_proposal_keys=candidate_keys,
                canonical_proposal_key=canonical_key,
                basis=basis,
            )
        )
    return tuple(audits)


class TableAtomicSubfactCoverageState(StrEnum):
    COMPLETE = "complete"
    MISSING = "missing"
    COMPOSITION_SUBSTITUTION = "composition_substitution"
    FACT_TYPE_MISMATCH = "fact_type_mismatch"
    ASSERTION_MISMATCH = "assertion_mismatch"
    AMBIGUOUS = "ambiguous"


class Step2ATableAtomicSubfactAudit(StrictModel):
    """Proof that a rule in a complete table also has one atomic Fact owner."""

    requirement_id: str = Field(pattern=r"^table-subfact:[0-9a-f]{24}$")
    table_evidence_id: str
    cell_evidence_id: str
    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    kind: TableAtomicSubfactKind
    expected_fact_type: FactType
    state: TableAtomicSubfactCoverageState
    candidate_proposal_keys: tuple[str, ...] = ()
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2ATableAtomicSubfactAudit":
        if len(self.candidate_proposal_keys) != len(set(self.candidate_proposal_keys)):
            raise ValueError("table atomic subfact audits require unique candidate proposal keys")
        if self.state == TableAtomicSubfactCoverageState.COMPLETE and len(self.candidate_proposal_keys) != 1:
            raise ValueError("complete table atomic subfact audits require exactly one proposal")
        if self.state == TableAtomicSubfactCoverageState.MISSING and self.candidate_proposal_keys:
            raise ValueError("missing table atomic subfact audits cannot claim candidates")
        return self


def assess_step2a_table_atomic_subfacts(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> tuple[Step2ATableAtomicSubfactAudit, ...]:
    """Require one source-grounded atomic Fact for every projected table rule."""

    frozen_proposals = tuple(proposals)
    audits: list[Step2ATableAtomicSubfactAudit] = []
    for table in _request_structured_tables(request):
        for requirement in table.atomic_subfact_requirements:
            selected = tuple(
                proposal
                for proposal in frozen_proposals
                if requirement.anchor_span_id in proposal.primary_anchor_span_ids
                and requirement.table_evidence_id in proposal.selected_table_evidence_ids
            )
            typed = tuple(proposal for proposal in selected if proposal.fact_type == requirement.expected_fact_type)
            asserted = tuple(
                proposal
                for proposal in typed
                if normalized_compare_text(requirement.required_assertion) in normalized_compare_text(proposal.assertion)
            )
            if len(asserted) == 1:
                state = TableAtomicSubfactCoverageState.COMPLETE
                candidates = (asserted[0].proposal_key,)
                basis = "one non-composition Fact selects the required table cell anchor, has the required type, and retains the exact source rule in its assertion."
            elif len(asserted) > 1:
                state = TableAtomicSubfactCoverageState.AMBIGUOUS
                candidates = tuple(proposal.proposal_key for proposal in asserted)
                basis = "multiple atomic Facts claim the same required table rule."
            elif not selected:
                state = TableAtomicSubfactCoverageState.MISSING
                candidates = ()
                basis = "no Fact selects the required table rule anchor as a primary source."
            elif not typed:
                candidates = tuple(proposal.proposal_key for proposal in selected)
                if all(proposal.fact_type == FactType.COMPOSITION for proposal in selected):
                    state = TableAtomicSubfactCoverageState.COMPOSITION_SUBSTITUTION
                    basis = "the rule anchor is selected only by a complete composition Fact; a composition Fact cannot replace an atomic rule Fact."
                else:
                    state = TableAtomicSubfactCoverageState.FACT_TYPE_MISMATCH
                    basis = "the rule anchor is selected only by Facts with a different type; an atomic rule must use its required Fact type."
            else:
                state = TableAtomicSubfactCoverageState.ASSERTION_MISMATCH
                candidates = tuple(proposal.proposal_key for proposal in typed)
                basis = "the required source rule is absent from the typed Fact assertion."
            audits.append(
                Step2ATableAtomicSubfactAudit(
                    requirement_id=requirement.requirement_id,
                    table_evidence_id=requirement.table_evidence_id,
                    cell_evidence_id=requirement.cell_evidence_id,
                    anchor_span_id=requirement.anchor_span_id,
                    kind=requirement.kind,
                    expected_fact_type=requirement.expected_fact_type,
                    state=state,
                    candidate_proposal_keys=candidates,
                    basis=basis,
                )
            )
    return tuple(audits)


class CoverageObligationCoverageState(StrEnum):
    COMPLETE = "complete"
    MISSING = "missing"
    INCOMPLETE_ANCHORS = "incomplete_anchors"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    AMBIGUOUS = "ambiguous"


class Step2ACoverageObligationAudit(StrictModel):
    """Local proof that one V18 request obligation has an eligible Fact owner."""

    obligation_id: str = Field(pattern=r"^coverage-obligation:[0-9a-f]{24}$")
    kind: Step2ACoverageObligationKind
    state: CoverageObligationCoverageState
    expected_fact_type: FactType
    required_primary_anchor_span_ids: tuple[str, ...]
    candidate_proposal_keys: tuple[str, ...] = ()
    basis: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2ACoverageObligationAudit":
        if len(self.candidate_proposal_keys) != len(set(self.candidate_proposal_keys)):
            raise ValueError("coverage-obligation audits require unique candidate proposal keys")
        if self.state == CoverageObligationCoverageState.COMPLETE and len(self.candidate_proposal_keys) != 1:
            raise ValueError("complete coverage-obligation audits require exactly one Fact owner")
        if self.state == CoverageObligationCoverageState.MISSING and self.candidate_proposal_keys:
            raise ValueError("missing coverage-obligation audits cannot claim candidate Facts")
        return self


class CoverageObligationForensicCategory(StrEnum):
    COMPLETE = "complete"
    COMPLETE_TABLE_OMITTED = "complete_table_omitted"
    MISSING_REQUIRED_ANCHOR = "missing_required_anchor"
    FACT_TYPE_MISMATCH = "fact_type_mismatch"
    ASSERTION_MISSING_SOURCE_RULE = "assertion_missing_source_rule"
    COMPLETE_TABLE_SEMANTICS_MISMATCH = "complete_table_semantics_mismatch"
    CONTINUATION_SEMANTICS_MISMATCH = "continuation_semantics_mismatch"
    AMBIGUOUS_FACT_OWNER = "ambiguous_fact_owner"


class Step2ACoverageObligationForensic(StrictModel):
    """Safe V19 coverage diagnostics with no persisted model assertion text."""

    obligation_id: str = Field(pattern=r"^coverage-obligation:[0-9a-f]{24}$")
    kind: Step2ACoverageObligationKind
    category: CoverageObligationForensicCategory
    candidate_proposal_keys: tuple[str, ...] = ()
    observed_fact_types: tuple[FactType, ...] = ()
    missing_primary_anchor_span_ids: tuple[str, ...] = ()
    selected_primary_anchor_span_ids: tuple[str, ...] = ()
    required_source_assertion_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    model_assertion_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_forensic(self) -> "Step2ACoverageObligationForensic":
        if (
            len(self.candidate_proposal_keys) != len(set(self.candidate_proposal_keys))
            or len(self.observed_fact_types) != len(set(self.observed_fact_types))
            or len(self.missing_primary_anchor_span_ids) != len(set(self.missing_primary_anchor_span_ids))
            or len(self.selected_primary_anchor_span_ids) != len(set(self.selected_primary_anchor_span_ids))
        ):
            raise ValueError("coverage-obligation forensic collections must be unique")
        if self.category == CoverageObligationForensicCategory.ASSERTION_MISSING_SOURCE_RULE:
            if self.required_source_assertion_sha256 is None or self.model_assertion_sha256 is None:
                raise ValueError("assertion-mismatch forensics require only source and model assertion hashes")
        elif self.model_assertion_sha256 is not None:
            raise ValueError("only assertion-mismatch forensics may retain a model assertion hash")
        return self


def assess_step2a_coverage_obligations(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> tuple[Step2ACoverageObligationAudit, ...]:
    """Verify V18 obligations only from local Fact anchors and source-derived constraints."""

    obligation_input = request.coverage_obligation_input
    if obligation_input is None:
        return ()
    frozen_proposals = tuple(proposals)
    requirements = {
        requirement.requirement_id: requirement
        for table in _request_structured_tables(request)
        for requirement in table.atomic_subfact_requirements
    }
    audits: list[Step2ACoverageObligationAudit] = []
    for obligation in obligation_input.obligations:
        required = set(obligation.required_primary_anchor_span_ids)
        touched = tuple(
            proposal
            for proposal in frozen_proposals
            if required & set(proposal.primary_anchor_span_ids)
        )
        typed_touched = tuple(
            proposal for proposal in touched if proposal.fact_type == obligation.expected_fact_type
        )
        fully_anchored = tuple(
            proposal
            for proposal in typed_touched
            if required <= set(proposal.primary_anchor_span_ids)
        )

        def satisfies_semantics(proposal: Step2AFactProposal) -> bool:
            if proposal.fact_type != obligation.expected_fact_type:
                return False
            if obligation.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION:
                return any(
                    coverage.table_evidence_id == obligation.table_evidence_id
                    for coverage in proposal.complete_table_semantic_coverages
                )
            if obligation.kind == Step2ACoverageObligationKind.ATOMIC_TABLE_RULE:
                requirement = requirements.get(obligation.atomic_requirement_id or "")
                return (
                    requirement is not None
                    and obligation.table_evidence_id in proposal.selected_table_evidence_ids
                    and normalized_compare_text(requirement.required_assertion)
                    in normalized_compare_text(proposal.assertion)
                )
            return proposal.completeness_mode == CompletenessMode.ALL_ITEMS

        complete = tuple(proposal for proposal in fully_anchored if satisfies_semantics(proposal))
        if len(complete) == 1:
            state = CoverageObligationCoverageState.COMPLETE
            candidates = (complete[0].proposal_key,)
            basis = "one Fact selected every required primary anchor span and satisfied the locally-derived obligation semantics."
        elif len(complete) > 1:
            state = CoverageObligationCoverageState.AMBIGUOUS
            candidates = tuple(proposal.proposal_key for proposal in complete)
            basis = "multiple Facts satisfy the same immutable coverage obligation, so ownership is ambiguous."
        elif fully_anchored:
            state = CoverageObligationCoverageState.SEMANTIC_MISMATCH
            candidates = tuple(proposal.proposal_key for proposal in fully_anchored)
            basis = "required anchors were selected, but Fact type, complete-table ownership, exact source rule, or all-items semantics did not match."
        elif typed_touched:
            state = CoverageObligationCoverageState.INCOMPLETE_ANCHORS
            candidates = tuple(proposal.proposal_key for proposal in typed_touched)
            basis = "a Fact of the required type selected part of the required anchor set but did not close the obligation."
        elif touched:
            state = CoverageObligationCoverageState.SEMANTIC_MISMATCH
            candidates = tuple(proposal.proposal_key for proposal in touched)
            basis = "source anchors were selected only by Facts with an incompatible type; they cannot substitute for this obligation."
        else:
            state = CoverageObligationCoverageState.MISSING
            candidates = ()
            basis = "no Fact selected any required primary anchor span for this immutable coverage obligation."
        audits.append(
            Step2ACoverageObligationAudit(
                obligation_id=obligation.obligation_id,
                kind=obligation.kind,
                state=state,
                expected_fact_type=obligation.expected_fact_type,
                required_primary_anchor_span_ids=obligation.required_primary_anchor_span_ids,
                candidate_proposal_keys=candidates,
                basis=basis,
            )
        )
    return tuple(audits)


def derive_step2a_coverage_obligation_forensics(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> tuple[Step2ACoverageObligationForensic, ...]:
    """Classify V19 coverage failures without retaining response text or values."""

    obligation_input = request.coverage_obligation_input
    if obligation_input is None:
        return ()
    frozen_proposals = tuple(proposals)
    requirements = {
        requirement.requirement_id: requirement
        for table in _request_structured_tables(request)
        for requirement in table.atomic_subfact_requirements
    }
    forensics: list[Step2ACoverageObligationForensic] = []
    for obligation in obligation_input.obligations:
        required = set(obligation.required_primary_anchor_span_ids)
        touched = tuple(
            proposal
            for proposal in frozen_proposals
            if required & set(proposal.primary_anchor_span_ids)
        )
        typed = tuple(proposal for proposal in touched if proposal.fact_type == obligation.expected_fact_type)
        fully = tuple(proposal for proposal in typed if required <= set(proposal.primary_anchor_span_ids))
        selected = set().union(*(set(item.primary_anchor_span_ids) for item in typed)) if typed else set()
        missing = tuple(span_id for span_id in obligation.required_primary_anchor_span_ids if span_id not in selected)
        candidate_keys = tuple(item.proposal_key for item in (typed or touched))
        observed_types = tuple(dict.fromkeys(item.fact_type for item in touched))
        category: CoverageObligationForensicCategory
        source_assertion_hash: str | None = None
        model_assertion_hash: str | None = None
        if not touched:
            category = (
                CoverageObligationForensicCategory.COMPLETE_TABLE_OMITTED
                if obligation.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION
                else CoverageObligationForensicCategory.MISSING_REQUIRED_ANCHOR
            )
            missing = obligation.required_primary_anchor_span_ids
        elif not typed:
            # Atomic Facts may legitimately select individual table cells, but they
            # cannot mask a missing composition Fact for the immutable whole-table
            # obligation.
            category = (
                CoverageObligationForensicCategory.COMPLETE_TABLE_OMITTED
                if obligation.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION
                else CoverageObligationForensicCategory.FACT_TYPE_MISMATCH
            )
        elif not fully:
            category = CoverageObligationForensicCategory.MISSING_REQUIRED_ANCHOR
        else:
            requirement = requirements.get(obligation.atomic_requirement_id or "")
            semantic_matches = tuple(
                proposal
                for proposal in fully
                if (
                    any(
                        coverage.table_evidence_id == obligation.table_evidence_id
                        for coverage in proposal.complete_table_semantic_coverages
                    )
                    if obligation.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION
                    else normalized_compare_text(requirement.required_assertion)
                    in normalized_compare_text(proposal.assertion)
                    if obligation.kind == Step2ACoverageObligationKind.ATOMIC_TABLE_RULE and requirement is not None
                    else proposal.completeness_mode == CompletenessMode.ALL_ITEMS
                )
            )
            if len(semantic_matches) == 1:
                category = CoverageObligationForensicCategory.COMPLETE
                candidate_keys = (semantic_matches[0].proposal_key,)
            elif len(semantic_matches) > 1:
                category = CoverageObligationForensicCategory.AMBIGUOUS_FACT_OWNER
                candidate_keys = tuple(item.proposal_key for item in semantic_matches)
            elif obligation.kind == Step2ACoverageObligationKind.ATOMIC_TABLE_RULE and requirement is not None:
                category = CoverageObligationForensicCategory.ASSERTION_MISSING_SOURCE_RULE
                source_assertion_hash = requirement.required_assertion_sha256
                model_assertion_hash = hashlib.sha256(fully[0].assertion.encode("utf-8")).hexdigest()
            elif obligation.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION:
                category = CoverageObligationForensicCategory.COMPLETE_TABLE_SEMANTICS_MISMATCH
            else:
                category = CoverageObligationForensicCategory.CONTINUATION_SEMANTICS_MISMATCH
        if category != CoverageObligationForensicCategory.COMPLETE:
            forensics.append(
                Step2ACoverageObligationForensic(
                    obligation_id=obligation.obligation_id,
                    kind=obligation.kind,
                    category=category,
                    candidate_proposal_keys=candidate_keys,
                    observed_fact_types=observed_types,
                    missing_primary_anchor_span_ids=missing,
                    selected_primary_anchor_span_ids=tuple(sorted(selected)),
                    required_source_assertion_sha256=source_assertion_hash,
                    model_assertion_sha256=model_assertion_hash,
                )
            )
    return tuple(forensics)


class ContinuationAllItemsAuditState(StrEnum):
    CLOSED = "closed"
    MANUAL_REVIEW = "manual_review"
    ADJACENT_SECTION_MISSING = "adjacent_section_missing"
    SOURCE_ANCHOR_MISSING = "source_anchor_missing"
    CLOSURE_ANCHOR_MISSING = "closure_anchor_missing"


class Step2AContinuationAudit(StrictModel):
    """Explain why one all-items Fact may or may not cross a page boundary."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    continuation_id: str = Field(pattern=r"^continuation:[0-9a-f]{24}$")
    state: ContinuationAllItemsAuditState
    binding_id: str | None = Field(default=None, pattern=r"^continuation-binding:[0-9a-f]{24}$")
    source_page_num: int = Field(ge=1)
    adjacent_page_num: int = Field(ge=1)
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_continuation_audit(self) -> "Step2AContinuationAudit":
        if self.state == ContinuationAllItemsAuditState.CLOSED and self.binding_id is None:
            raise ValueError("closed continuation audits require a continuation binding")
        return self


def assess_step2a_continuations(
    *, request: Step2ARequest, proposals: Iterable[Step2AFactProposal]
) -> tuple[Step2AContinuationAudit, ...]:
    """Fail closed unless an all-items Fact proves its adjacent-page closure."""

    records = tuple(
        (record, projection)
        for projection in request.source_projections
        for record in projection.continuation_records
        if not (
            request.identity.request_version in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS
            and request.continuation_review_sidecar is not None
            and next(
                item.disposition
                for item in request.continuation_review_sidecar.marker_reviews
                if item.continuation_id == record.continuation_id
            )
            == ContinuationMarkerDisposition.INLINE_DIRECTION_MARKER
        )
    )
    bindings_by_continuation = {binding.continuation_id: binding for binding in request.continuation_bindings}
    audits: list[Step2AContinuationAudit] = []
    for proposal in proposals:
        if proposal.completeness_mode != CompletenessMode.ALL_ITEMS:
            continue
        selected_span_ids = set((*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids))
        declared_sections = set(proposal.source_section_ids)
        for record, source_projection in records:
            binding = bindings_by_continuation.get(record.continuation_id)
            source_section_ids = (
                (binding.source_section_id,)
                if binding is not None
                else tuple(
                    section.section_id
                    for section in request.sections
                    if section.page_num == source_projection.page_num
                    and set(record.anchor_span_ids) <= set(section.anchor_span_ids)
                )
            )
            if not set(source_section_ids) & declared_sections:
                continue
            if binding is None or binding.state != ContinuationBindingState.CLOSED:
                audits.append(
                    Step2AContinuationAudit(
                        proposal_key=proposal.proposal_key,
                        continuation_id=record.continuation_id,
                        state=ContinuationAllItemsAuditState.MANUAL_REVIEW,
                        binding_id=binding.binding_id if binding is not None else None,
                        source_page_num=record.page_num,
                        adjacent_page_num=record.required_adjacent_page_num,
                        basis=(
                            binding.closure_basis
                            if binding is not None
                            else "no V11 continuation binding is available for the page-local continuation record"
                        ),
                    )
                )
                continue
            if binding.adjacent_section_id not in declared_sections:
                audits.append(
                    Step2AContinuationAudit(
                        proposal_key=proposal.proposal_key,
                        continuation_id=record.continuation_id,
                        state=ContinuationAllItemsAuditState.ADJACENT_SECTION_MISSING,
                        binding_id=binding.binding_id,
                        source_page_num=record.page_num,
                        adjacent_page_num=record.required_adjacent_page_num,
                        basis="all-items Fact must declare the bound adjacent source section",
                    )
                )
                continue
            if not set(binding.source_anchor_span_ids) <= selected_span_ids:
                audits.append(
                    Step2AContinuationAudit(
                        proposal_key=proposal.proposal_key,
                        continuation_id=record.continuation_id,
                        state=ContinuationAllItemsAuditState.SOURCE_ANCHOR_MISSING,
                        binding_id=binding.binding_id,
                        source_page_num=record.page_num,
                        adjacent_page_num=record.required_adjacent_page_num,
                        basis="all-items Fact must select every source continuation anchor span",
                    )
                )
                continue
            if not set(binding.closure_anchor_span_ids) <= selected_span_ids:
                audits.append(
                    Step2AContinuationAudit(
                        proposal_key=proposal.proposal_key,
                        continuation_id=record.continuation_id,
                        state=ContinuationAllItemsAuditState.CLOSURE_ANCHOR_MISSING,
                        binding_id=binding.binding_id,
                        source_page_num=record.page_num,
                        adjacent_page_num=record.required_adjacent_page_num,
                        basis="all-items Fact must select every bound adjacent-page closure anchor span",
                    )
                )
                continue
            audits.append(
                Step2AContinuationAudit(
                    proposal_key=proposal.proposal_key,
                    continuation_id=record.continuation_id,
                    state=ContinuationAllItemsAuditState.CLOSED,
                    binding_id=binding.binding_id,
                    source_page_num=record.page_num,
                    adjacent_page_num=record.required_adjacent_page_num,
                    basis="all-items Fact selects the closed source and adjacent-page anchor spans from both bound sections",
                )
            )
    return tuple(audits)


class ConditionClaimDerivationState(StrEnum):
    """Fail-closed states for local assertion/Evidence condition matching."""

    DERIVED = "derived"
    ISOLATED_OPERATOR = "isolated_operator"
    ASSERTION_WITHOUT_EVIDENCE = "assertion_without_evidence"
    EVIDENCE_WITHOUT_ASSERTION = "evidence_without_assertion"
    AMBIGUOUS_EVIDENCE = "ambiguous_evidence"
    MULTIPLE_CANDIDATE_SPANS = "multiple_candidate_spans"


class Step2AConditionClaimAudit(StrictModel):
    """Audit one local condition derivation attempt without retaining rejected text."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    operator: ConditionOperator
    state: ConditionClaimDerivationState
    assertion_span: ConditionSpanReference | None = None
    evidence_id: str | None = None
    anchor_span_id: str | None = Field(default=None, pattern=r"^span:[0-9a-f]{24}$")
    source_span: ConditionSpanReference | None = None
    derived_claim: GroundedConditionClaim | None = None
    candidate_evidence_ids: tuple[str, ...] = ()
    candidate_anchor_span_ids: tuple[str, ...] = ()
    candidate_span_sha256s: tuple[str, ...] = ()
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2AConditionClaimAudit":
        if len(self.candidate_evidence_ids) != len(set(self.candidate_evidence_ids)):
            raise ValueError("condition derivation candidate Evidence IDs must be unique")
        if len(self.candidate_anchor_span_ids) != len(set(self.candidate_anchor_span_ids)):
            raise ValueError("condition derivation candidate anchor span IDs must be unique")
        if len(self.candidate_span_sha256s) != len(set(self.candidate_span_sha256s)):
            raise ValueError("condition derivation candidate span hashes must be unique")
        if self.state == ConditionClaimDerivationState.DERIVED:
            if self.assertion_span is None or self.evidence_id is None or self.anchor_span_id is None or self.source_span is None or self.derived_claim is None:
                raise ValueError("derived condition audits require assertion/source spans, anchor span, Evidence ID, and claim")
            if self.derived_claim.evidence_id != self.evidence_id:
                raise ValueError("derived condition audit claim must bind its source Evidence")
            if self.derived_claim.anchor_span_id != self.anchor_span_id:
                raise ValueError("derived condition audit claim must bind its selected anchor span")
            if self.assertion_span.sha256 != self.source_span.sha256:
                raise ValueError("derived condition audit spans must hash the same exact text")
        elif self.derived_claim is not None:
            raise ValueError("failed condition derivation audits cannot contain a derived claim")
        return self


class Step2AConditionClaimDerivationResult(StrictModel):
    """Canonical Fact proposals plus all local condition derivation attempts."""

    proposals: tuple[Step2AFactProposal, ...]
    audits: tuple[Step2AConditionClaimAudit, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> "Step2AConditionClaimDerivationResult":
        proposal_keys = {proposal.proposal_key for proposal in self.proposals}
        if not {audit.proposal_key for audit in self.audits} <= proposal_keys:
            raise ValueError("condition derivation audits must bind a canonical Fact proposal")
        return self


class NumericClaimDerivationState(StrEnum):
    """Fail-closed states for V16 local numeric assertion/Evidence matching."""

    DERIVED = "derived"
    ASSERTION_WITHOUT_EVIDENCE = "assertion_without_evidence"
    EVIDENCE_WITHOUT_ASSERTION = "evidence_without_assertion"
    AMBIGUOUS_EVIDENCE = "ambiguous_evidence"
    MULTIPLE_CANDIDATE_SPANS = "multiple_candidate_spans"


class Step2ATableStructuralIndexOccurrenceAudit(StrictModel):
    """Non-numeric provenance for list indexes in a complete-table Fact."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    state: TableStructuralIndexOccurrenceState
    assertion_span: NumericSpanReference
    assertion_classification: NumericTokenClass
    occurrences: tuple[TableStructuralIndexOccurrenceReference, ...] = ()
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2ATableStructuralIndexOccurrenceAudit":
        if self.assertion_classification not in {
            NumericTokenClass.QUANTITATIVE_VALUE,
            NumericTokenClass.LIST_INDEX,
        }:
            raise ValueError("table structural-index audits accept only numeric-looking or list-index assertion tokens")
        occurrence_ids = [item.occurrence_id for item in self.occurrences]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("table structural-index audits require unique occurrence IDs")
        if self.state == TableStructuralIndexOccurrenceState.DERIVED:
            if not self.occurrences:
                raise ValueError("derived structural-index audits require one or more exact table-cell occurrences")
            if any(item.source_span.sha256 != self.assertion_span.sha256 for item in self.occurrences):
                raise ValueError("structural-index source and assertion tokens must have identical exact-text hashes")
        elif self.occurrences:
            raise ValueError("failed structural-index audits cannot retain derived occurrences")
        return self


class Step2ACompleteTableStructuralProjectionAudit(StrictModel):
    """Local V23 table structure projection, independent of model assertion wording."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    state: CompleteTableStructuralProjectionState
    projections: tuple[CompleteTableStructuralProjection, ...]
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2ACompleteTableStructuralProjectionAudit":
        if self.state != CompleteTableStructuralProjectionState.DERIVED or not self.projections:
            raise ValueError("complete-table structural projection audits must retain derived projections")
        table_ids = tuple(item.table_evidence_id for item in self.projections)
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("complete-table structural projection audits require unique source tables")
        if any(item.proposal_key != self.proposal_key for item in self.projections):
            raise ValueError("complete-table structural projections must bind their audited proposal")
        return self


class Step2ATableNumericOccurrenceAudit(StrictModel):
    """Non-claim numeric provenance for a complete-table composition Fact."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    state: TableNumericOccurrenceState
    assertion_span: NumericSpanReference
    occurrences: tuple[TableNumericOccurrenceReference, ...] = ()
    grid_metadata: TableGridMetadataReference | None = None
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2ATableNumericOccurrenceAudit":
        occurrence_ids = [item.occurrence_id for item in self.occurrences]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("table numeric occurrence audits require unique occurrence IDs")
        if self.state == TableNumericOccurrenceState.DERIVED_TABLE_CELL_OCCURRENCES:
            if not self.occurrences or self.grid_metadata is not None:
                raise ValueError("derived table-cell occurrences require one or more occurrences and no grid metadata")
            if any(item.source_span.sha256 != self.assertion_span.sha256 for item in self.occurrences):
                raise ValueError("table numeric occurrence spans must hash the same exact assertion expression")
        elif self.state == TableNumericOccurrenceState.DERIVED_GRID_METADATA:
            if self.occurrences or self.grid_metadata is None:
                raise ValueError("grid metadata derivation requires exactly one metadata binding and no cell occurrence")
        elif self.occurrences or self.grid_metadata is not None:
            raise ValueError("failed table numeric occurrence audits cannot contain derived bindings")
        return self


class Step2ANumericClaimAudit(StrictModel):
    """Audit one V16 deterministic numeric claim derivation attempt."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    classification: NumericTokenClass
    state: NumericClaimDerivationState
    assertion_span: NumericSpanReference | None = None
    evidence_id: str | None = None
    anchor_span_id: str | None = Field(default=None, pattern=r"^span:[0-9a-f]{24}$")
    source_span: NumericSpanReference | None = None
    derived_claim: GroundedNumericClaim | None = None
    candidate_evidence_ids: tuple[str, ...] = ()
    candidate_anchor_span_ids: tuple[str, ...] = ()
    candidate_span_sha256s: tuple[str, ...] = ()
    basis: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_audit(self) -> "Step2ANumericClaimAudit":
        if self.classification != NumericTokenClass.QUANTITATIVE_VALUE:
            raise ValueError("numeric claim derivation audits only accept quantitative values")
        if len(self.candidate_evidence_ids) != len(set(self.candidate_evidence_ids)):
            raise ValueError("numeric derivation candidate Evidence IDs must be unique")
        if len(self.candidate_anchor_span_ids) != len(set(self.candidate_anchor_span_ids)):
            raise ValueError("numeric derivation candidate anchor span IDs must be unique")
        if len(self.candidate_span_sha256s) != len(set(self.candidate_span_sha256s)):
            raise ValueError("numeric derivation candidate span hashes must be unique")
        if self.state == NumericClaimDerivationState.DERIVED:
            if (
                self.assertion_span is None
                or self.evidence_id is None
                or self.anchor_span_id is None
                or self.source_span is None
                or self.derived_claim is None
            ):
                raise ValueError("derived numeric audits require source span, anchor span, Evidence ID, and claim")
            if self.derived_claim.evidence_id != self.evidence_id:
                raise ValueError("derived numeric audit claim must bind its source Evidence")
            if self.derived_claim.anchor_span_id != self.anchor_span_id:
                raise ValueError("derived numeric audit claim must bind its selected anchor span")
            if self.assertion_span.sha256 != self.source_span.sha256:
                raise ValueError("derived numeric audit spans must hash the same exact text")
        elif self.derived_claim is not None:
            raise ValueError("failed numeric derivation audits cannot contain a derived claim")
        return self


class Step2ANumericClaimDerivationResult(StrictModel):
    """Canonical Fact proposals plus all V16 numeric derivation attempts."""

    proposals: tuple[Step2AFactProposal, ...]
    audits: tuple[Step2ANumericClaimAudit, ...] = ()
    table_occurrence_audits: tuple[Step2ATableNumericOccurrenceAudit, ...] = ()
    table_structural_index_occurrence_audits: tuple[Step2ATableStructuralIndexOccurrenceAudit, ...] = ()
    table_structural_projection_audits: tuple[Step2ACompleteTableStructuralProjectionAudit, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> "Step2ANumericClaimDerivationResult":
        proposal_keys = {proposal.proposal_key for proposal in self.proposals}
        if not {audit.proposal_key for audit in self.audits} <= proposal_keys:
            raise ValueError("numeric derivation audits must bind a canonical Fact proposal")
        if not {audit.proposal_key for audit in self.table_occurrence_audits} <= proposal_keys:
            raise ValueError("table numeric occurrence audits must bind a canonical Fact proposal")
        if not {
            audit.proposal_key for audit in self.table_structural_index_occurrence_audits
        } <= proposal_keys:
            raise ValueError("table structural-index occurrence audits must bind a canonical Fact proposal")
        if not {audit.proposal_key for audit in self.table_structural_projection_audits} <= proposal_keys:
            raise ValueError("table structural projection audits must bind a canonical Fact proposal")
        return self
