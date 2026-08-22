"""Contract validation for Step 2A/2B responses (fail-closed report)."""
from __future__ import annotations
from collections.abc import Iterable

from ...evidence.layout import normalized_compare_text
from ...materials.schema import StrictModel

from .audits import (
    ConditionClaimDerivationState,
    ContinuationAllItemsAuditState,
    CoverageObligationCoverageState,
    NumericClaimDerivationState,
    Step2ACompleteTableStructuralProjectionAudit,
    Step2AConditionClaimAudit,
    Step2AContinuationAudit,
    Step2ACoverageObligationAudit,
    Step2ANumericClaimAudit,
    Step2ATableAtomicSubfactAudit,
    Step2ATableNumericOccurrenceAudit,
    Step2ATableSemanticCoverageAudit,
    Step2ATableStructuralIndexOccurrenceAudit,
    TableAtomicSubfactCoverageState,
    TableSemanticCoverageState,
    assess_step2a_continuations,
    assess_step2a_coverage_obligations,
    assess_step2a_table_atomic_subfacts,
    assess_step2a_table_semantic_coverage,
)
from .claims import GroundedNumericClaim, ProposalNumericTokenAudit
from .derivation import derive_step2a_condition_claims, derive_step2a_numeric_claims
from .enums import CardPresentation, NumericTokenClass, Step2ResultState, Step2Stage
from .numeric_tokens import classify_numeric_tokens
from .occurrences import (
    CompleteTableStructuralProjectionState,
    TableNumericOccurrenceState,
    TableStructuralIndexOccurrenceState,
)
from .request_helpers import _ordered_parent_evidence_ids, _step2a_anchor_sources
from .responses import Step2AResponse
from .step2a_request import Step2ARequest
from .step2b import Step2BRequest, Step2BResponse
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


class ContractValidationReport(StrictModel):
    stage: Step2Stage
    valid: bool
    errors: tuple[str, ...] = ()
    numeric_token_audit: tuple[ProposalNumericTokenAudit, ...] = ()
    numeric_claim_derivation_audit: tuple[Step2ANumericClaimAudit, ...] = ()
    table_numeric_occurrence_audit: tuple[Step2ATableNumericOccurrenceAudit, ...] = ()
    table_structural_index_occurrence_audit: tuple[Step2ATableStructuralIndexOccurrenceAudit, ...] = ()
    table_structural_projection_audit: tuple[Step2ACompleteTableStructuralProjectionAudit, ...] = ()
    condition_claim_audit: tuple[Step2AConditionClaimAudit, ...] = ()
    table_semantic_coverage_audit: tuple[Step2ATableSemanticCoverageAudit, ...] = ()
    table_atomic_subfact_audit: tuple[Step2ATableAtomicSubfactAudit, ...] = ()
    coverage_obligation_audit: tuple[Step2ACoverageObligationAudit, ...] = ()
    continuation_audit: tuple[Step2AContinuationAudit, ...] = ()


def _claim_values(claims: Iterable[GroundedNumericClaim]) -> set[str]:
    return {normalized_compare_text(claim.value) for claim in claims}


def validate_step2a_response(*, request: Step2ARequest, response: Step2AResponse) -> ContractValidationReport:
    errors: list[str] = []
    numeric_token_audit: list[ProposalNumericTokenAudit] = []
    if response.request != request.identity:
        errors.append("response request identity does not match the source request")
    if response.state != Step2ResultState.SUCCEEDED:
        errors.append(f"response is not successful: {response.failure.state.value if response.failure else 'unknown'}")
        return ContractValidationReport(stage=Step2Stage.FACT_PLANNING, valid=False, errors=tuple(errors))
    spans_by_id, table_cell_span_ids, section_ids, _ = _step2a_anchor_sources(request)
    table_semantic_coverage = assess_step2a_table_semantic_coverage(request=request, proposals=response.proposals)
    for audit in table_semantic_coverage:
        if audit.state != TableSemanticCoverageState.COMPLETE:
            errors.append(f"{audit.table_evidence_id}: complete-table semantic coverage {audit.state.value}: {audit.basis}")
    table_atomic_subfacts = assess_step2a_table_atomic_subfacts(request=request, proposals=response.proposals)
    for subfact_audit in table_atomic_subfacts:
        if subfact_audit.state != TableAtomicSubfactCoverageState.COMPLETE:
            errors.append(
                f"{subfact_audit.requirement_id}: table atomic subfact {subfact_audit.state.value}: {subfact_audit.basis}"
            )
    coverage_obligation_audit = assess_step2a_coverage_obligations(request=request, proposals=response.proposals)
    for obligation_audit in coverage_obligation_audit:
        if obligation_audit.state != CoverageObligationCoverageState.COMPLETE:
            errors.append(
                f"{obligation_audit.obligation_id}: coverage obligation {obligation_audit.state.value}: {obligation_audit.basis}"
            )
    continuation_audit = assess_step2a_continuations(request=request, proposals=response.proposals)
    for all_items_audit in continuation_audit:
        if all_items_audit.state != ContinuationAllItemsAuditState.CLOSED:
            errors.append(
                f"{all_items_audit.proposal_key}: all_items Fact is blocked by continuation "
                f"{all_items_audit.state.value}: {all_items_audit.basis}"
            )
    condition_derivation = derive_step2a_condition_claims(request=request, proposals=response.proposals)
    expected_condition_claims = {
        proposal.proposal_key: proposal.condition_claims for proposal in condition_derivation.proposals
    }
    for condition_audit in condition_derivation.audits:
        if condition_audit.state != ConditionClaimDerivationState.DERIVED:
            errors.append(
                f"{condition_audit.proposal_key}: condition derivation {condition_audit.state.value}: {condition_audit.basis}"
            )
    numeric_derivation = (
        derive_step2a_numeric_claims(request=request, proposals=response.proposals)
        if request.identity.request_version
        in {
            DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
            TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
            TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
            COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
            STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
            SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
        }
        else None
    )
    expected_numeric_claims = (
        {proposal.proposal_key: proposal.numeric_claims for proposal in numeric_derivation.proposals}
        if numeric_derivation is not None
        else {}
    )
    if numeric_derivation is not None:
        for numeric_audit in numeric_derivation.audits:
            if numeric_audit.state != NumericClaimDerivationState.DERIVED:
                errors.append(
                    f"{numeric_audit.proposal_key}: numeric derivation {numeric_audit.state.value}: {numeric_audit.basis}"
                )
        for occurrence_audit in numeric_derivation.table_occurrence_audits:
            if occurrence_audit.state not in {
                TableNumericOccurrenceState.DERIVED_TABLE_CELL_OCCURRENCES,
                TableNumericOccurrenceState.DERIVED_GRID_METADATA,
            }:
                errors.append(
                    f"{occurrence_audit.proposal_key}: table numeric occurrence "
                    f"{occurrence_audit.state.value}: {occurrence_audit.basis}"
                )
        for index_occurrence_audit in numeric_derivation.table_structural_index_occurrence_audits:
            if index_occurrence_audit.state != TableStructuralIndexOccurrenceState.DERIVED:
                errors.append(
                    f"{index_occurrence_audit.proposal_key}: table structural-index occurrence "
                    f"{index_occurrence_audit.state.value}: {index_occurrence_audit.basis}"
                )
        for projection_audit in numeric_derivation.table_structural_projection_audits:
            if projection_audit.state != CompleteTableStructuralProjectionState.DERIVED:
                errors.append(
                    f"{projection_audit.proposal_key}: table structural projection "
                    f"{projection_audit.state.value}: {projection_audit.basis}"
                )
    known_anchor_span_ids = set(spans_by_id)
    for proposal in response.proposals:
        if not set(proposal.source_section_ids) <= section_ids:
            errors.append(f"{proposal.proposal_key}: references an unknown source section")
        selected_span_ids = tuple(dict.fromkeys((*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids)))
        unknown = set(selected_span_ids) - known_anchor_span_ids
        if unknown:
            errors.append(f"{proposal.proposal_key}: references unknown anchor spans: {sorted(unknown)}")
            continue
        primary_parent_ids = _ordered_parent_evidence_ids(proposal.primary_anchor_span_ids, spans_by_id)
        secondary_parent_ids = _ordered_parent_evidence_ids(proposal.secondary_anchor_span_ids, spans_by_id)
        if proposal.primary_evidence_ids != primary_parent_ids or proposal.secondary_evidence_ids != secondary_parent_ids:
            errors.append(f"{proposal.proposal_key}: parent Evidence IDs must be derived exactly from selected anchor spans")
        expected_table_span_ids = tuple(span_id for span_id in selected_span_ids if span_id in table_cell_span_ids)
        expected_table_evidence_ids = _ordered_parent_evidence_ids(expected_table_span_ids, spans_by_id)
        if proposal.table_cell_anchor_span_ids != expected_table_span_ids:
            errors.append(f"{proposal.proposal_key}: table cell anchor spans must equal selected structured cell spans")
        if proposal.table_cell_evidence_ids != expected_table_evidence_ids:
            errors.append(f"{proposal.proposal_key}: table cell parent Evidence must be derived from selected cell spans")
        if proposal.complete_table_semantic_coverages:
            covered_table_span_ids = {
                span_id
                for coverage in proposal.complete_table_semantic_coverages
                for span_id in coverage.cell_anchor_span_ids
            }
            unexpected_table_span_ids = set(proposal.table_cell_anchor_span_ids) - covered_table_span_ids
            if unexpected_table_span_ids:
                errors.append(
                    f"{proposal.proposal_key}: complete-table composition selects table-cell anchors outside "
                    f"its exact covered grids: {sorted(unexpected_table_span_ids)}"
                )
        for claim in proposal.numeric_claims:
            if claim.anchor_span_id not in selected_span_ids:
                errors.append(f"{proposal.proposal_key}: numeric claim anchor span must be selected by the proposal")
                continue
            source_span = spans_by_id[claim.anchor_span_id]
            if claim.evidence_id != source_span.parent_evidence_id:
                errors.append(f"{proposal.proposal_key}: numeric claim parent Evidence must equal its anchor span parent")
            if normalized_compare_text(claim.value) not in normalized_compare_text(source_span.canonical_text):
                errors.append(f"{proposal.proposal_key}: claim is not grounded in its selected anchor span")
            if normalized_compare_text(claim.value) not in normalized_compare_text(proposal.assertion):
                errors.append(f"{proposal.proposal_key}: grounded claim is absent from its assertion")
        if proposal.condition_claims != expected_condition_claims[proposal.proposal_key]:
            errors.append(
                f"{proposal.proposal_key}: condition claims must equal the deterministic assertion/Evidence derivation"
            )
        # Numeric claims bind assertion semantics only. Titles are presentation
        # metadata and cannot create an ungrounded numeric requirement.
        classified_tokens = classify_numeric_tokens(proposal.assertion)
        numeric_token_audit.append(ProposalNumericTokenAudit(proposal_key=proposal.proposal_key, tokens=classified_tokens))
        if numeric_derivation is not None:
            if proposal.numeric_claims != expected_numeric_claims[proposal.proposal_key]:
                errors.append(
                    f"{proposal.proposal_key}: numeric claims must equal the deterministic assertion/selected-anchor derivation"
                )
        else:
            numeric_claim_values = _claim_values(proposal.numeric_claims)
            quantitative_values = {
                normalized_compare_text(token.value)
                for token in classified_tokens
                if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
            }
            for claim in proposal.numeric_claims:
                if normalized_compare_text(claim.value) not in quantitative_values:
                    errors.append(f"{proposal.proposal_key}: numeric claim is not classified as a quantitative value")
            for token in classified_tokens:
                if token.classification == NumericTokenClass.QUANTITATIVE_VALUE and normalized_compare_text(token.value) not in numeric_claim_values:
                    errors.append(f"{proposal.proposal_key}: quantitative token {token.value!r} lacks an exact grounded numeric_claim")
    return ContractValidationReport(
        stage=Step2Stage.FACT_PLANNING,
        valid=not errors,
        errors=tuple(errors),
        numeric_token_audit=tuple(numeric_token_audit),
        numeric_claim_derivation_audit=numeric_derivation.audits if numeric_derivation is not None else (),
        table_numeric_occurrence_audit=(
            numeric_derivation.table_occurrence_audits if numeric_derivation is not None else ()
        ),
        table_structural_index_occurrence_audit=(
            numeric_derivation.table_structural_index_occurrence_audits
            if numeric_derivation is not None
            else ()
        ),
        table_structural_projection_audit=(
            numeric_derivation.table_structural_projection_audits
            if numeric_derivation is not None
            else ()
        ),
        condition_claim_audit=condition_derivation.audits,
        table_semantic_coverage_audit=table_semantic_coverage,
        table_atomic_subfact_audit=table_atomic_subfacts,
        coverage_obligation_audit=coverage_obligation_audit,
        continuation_audit=continuation_audit,
    )


def validate_step2b_response(*, request: Step2BRequest, response: Step2BResponse) -> ContractValidationReport:
    errors: list[str] = []
    if response.request != request.identity:
        errors.append("response request identity does not match the source request")
    if response.state != Step2ResultState.SUCCEEDED:
        errors.append(f"response is not successful: {response.failure.state.value if response.failure else 'unknown'}")
        return ContractValidationReport(stage=Step2Stage.CARD_PLANNING, valid=False, errors=tuple(errors))
    requested_fact_ids = {fact.fact_id for fact in request.facts}
    primary_fact_ids = [fact_id for proposal in response.proposals for fact_id in proposal.primary_fact_ids]
    all_referenced_ids = set(primary_fact_ids)
    all_referenced_ids.update(fact_id for proposal in response.proposals for fact_id in proposal.secondary_fact_ids)
    unknown = all_referenced_ids - requested_fact_ids
    if unknown:
        errors.append(f"card proposals reference unknown Facts: {sorted(unknown)}")
    if len(primary_fact_ids) != len(set(primary_fact_ids)):
        errors.append("each Fact summary must have exactly one primary card owner")
    if set(primary_fact_ids) != requested_fact_ids:
        errors.append("Step 2B card proposals do not cover every requested Fact exactly once")
    facts_by_id = {fact.fact_id: fact for fact in request.facts}
    canonical_table_fact_ids = {
        fact.fact_id
        for fact in request.facts
        if fact.complete_table_evidence_ids
    }
    for proposal in response.proposals:
        primary = tuple(facts_by_id[fact_id] for fact_id in proposal.primary_fact_ids if fact_id in facts_by_id)
        canonical_primary = tuple(fact for fact in primary if fact.complete_table_evidence_ids)
        if proposal.presentation == CardPresentation.TABLE and not canonical_primary:
            errors.append(f"{proposal.proposal_key}: table cards require a canonical complete-table Fact")
        if canonical_primary and proposal.presentation != CardPresentation.TABLE:
            errors.append(f"{proposal.proposal_key}: canonical complete-table Facts must use table presentation")
        covered_table_ids = {
            table_id
            for fact in canonical_primary
            for table_id in fact.complete_table_evidence_ids
        }
        row_level_substitutes = tuple(
            fact.fact_id
            for fact in primary
            if set(fact.selected_table_evidence_ids) & covered_table_ids
            and not (set(fact.complete_table_evidence_ids) & covered_table_ids)
        )
        if row_level_substitutes:
            errors.append(
                f"{proposal.proposal_key}: table card cannot aggregate row-level table Facts in place of its canonical complete-table Fact: {list(row_level_substitutes)}"
            )
    missing_table_card_owners = canonical_table_fact_ids - {
        fact_id
        for proposal in response.proposals
        if proposal.presentation == CardPresentation.TABLE
        for fact_id in proposal.primary_fact_ids
    }
    if missing_table_card_owners:
        errors.append(f"canonical complete-table Facts lack one table-card primary owner: {sorted(missing_table_card_owners)}")
    return ContractValidationReport(stage=Step2Stage.CARD_PLANNING, valid=not errors, errors=tuple(errors))
