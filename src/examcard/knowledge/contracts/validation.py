"""Contract validation for Step 2A/2B responses (fail-closed report)."""
from __future__ import annotations
from collections.abc import Iterable
import re
from typing import Any, Literal, cast
import unicodedata

from pydantic import Field
from pydantic.json_schema import GetJsonSchemaHandler, JsonSchemaValue

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


NumericClaimFailureClassification = Literal[
    "quantitative_value",
    "section_number",
    "list_index",
    "material_grade",
    "identifier",
    "unmatched",
    "unknown",
]
NumericClaimFailureCategory = Literal[
    "non_quantitative_token",
    "unmatched_assertion_expression",
]
NumericClaimFailureMatchState = Literal[
    "no_assertion_token",
    "normalization_mismatch",
    "matched_but_rejected",
    "unknown",
]
NumericClaimFailureMismatchReason = Literal[
    "whitespace_only",
    "punctuation_only",
    "unit_suffix_difference",
    "prefix_suffix_difference",
    "multiple_token_difference",
    "unknown",
]
NumericClaimCandidateClassification = Literal[
    "quantitative_value",
    "section_number",
    "list_index",
    "material_grade",
    "identifier",
    "unmatched",
]
NumericClaimCandidateCountCategory = Literal["none", "single", "multiple"]
NumericClaimFailureRelationState = Literal[
    "single_candidate_available",
    "multiple_candidates_available",
    "no_candidate_available",
    "rejected_candidate_available",
    "unknown",
]
NumericClaimAnchorQuantitativeCountCategory = Literal["none", "single", "multiple"]
NumericClaimAnchorRelationState = Literal[
    "exact_anchor_quantitative_token",
    "claim_wraps_single_anchor_token",
    "claim_is_subexpression_of_single_anchor_token",
    "multiple_anchor_quantitative_tokens",
    "no_anchor_quantitative_token",
    "unknown",
]


class NumericClaimFailureDiagnostic(StrictModel):
    """Non-content diagnostics for an invalid model-owned numeric claim."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    claim_index: int = Field(ge=0)
    classification: NumericClaimFailureClassification
    match_state: NumericClaimFailureMatchState
    anchor_exists: bool
    failure_category: NumericClaimFailureCategory
    # This additive field is defaulted and omitted only for the unknown state so
    # older serialized sidecars remain readable without inventing a relation.
    relation_state: NumericClaimFailureRelationState = Field(
        default="unknown", exclude_if=lambda value: value == "unknown"
    )
    # Only normalization-mismatch diagnostics carry this field.  Omitting the
    # default keeps the pre-existing non-mismatch sidecar shape stable.
    mismatch_reason: NumericClaimFailureMismatchReason | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    # Only normalization-mismatch diagnostics carry candidate metadata.  The
    # values are deliberately limited to counts and tokenizer classifications.
    candidate_count: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    candidate_classifications: tuple[NumericClaimCandidateClassification, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    candidate_count_category: NumericClaimCandidateCountCategory | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    anchor_quantitative_count: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    anchor_quantitative_count_category: NumericClaimAnchorQuantitativeCountCategory | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    anchor_relation_state: NumericClaimAnchorRelationState = Field(
        default="unknown", exclude_if=lambda value: value == "unknown"
    )


class ContractValidationReport(StrictModel):
    # This sidecar is deliberately omitted from the published JSON schema.
    # It is local observability metadata, not part of the Step 2 contract or
    # any request identity/cache projection.
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
    numeric_claim_failure_diagnostics: tuple[NumericClaimFailureDiagnostic, ...] = ()

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("numeric_claim_failure_diagnostics", None)
        definitions = schema.get("$defs")
        if isinstance(definitions, dict):
            definitions.pop("NumericClaimFailureDiagnostic", None)
        return schema


_SAFE_NUMERIC_CLAIM_CLASSIFICATIONS = frozenset(
    {
        "quantitative_value",
        "section_number",
        "list_index",
        "material_grade",
        "identifier",
        "unmatched",
        "unknown",
    }
)


def _safe_numeric_claim_classification(classification: object) -> NumericClaimFailureClassification:
    """Return only the fixed classification vocabulary used by diagnostics."""

    candidate = classification.value if isinstance(classification, NumericTokenClass) else classification
    if isinstance(candidate, str) and candidate in _SAFE_NUMERIC_CLAIM_CLASSIFICATIONS:
        return cast(NumericClaimFailureClassification, candidate)
    return "unknown"


_DIAGNOSTIC_UNITS = (
    "mm/h",
    "分钟",
    "小时",
    "万元",
    "亿元",
    "MPa",
    "kPa",
    "mm",
    "cm",
    "Pa",
    "%",
    "‰",
    "天",
    "年",
    "倍",
    "万",
    "台",
    "个",
    "题",
    "m",
    "h",
)
_DIAGNOSTIC_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_DIAGNOSTIC_ZERO_WIDTH_WHITESPACE = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff"})


def _diagnostic_without_extended_whitespace(value: str) -> str:
    return "".join(
        character
        for character in value
        if not character.isspace()
        and not unicodedata.category(character).startswith("Z")
        and character not in _DIAGNOSTIC_ZERO_WIDTH_WHITESPACE
    )


def _diagnostic_without_punctuation(value: str) -> str:
    return "".join(
        character
        for character in _diagnostic_without_extended_whitespace(value)
        if not unicodedata.category(character).startswith("P")
    )


def _diagnostic_without_unit_suffix(value: str) -> str:
    normalized = normalized_compare_text(value)
    folded = normalized.casefold()
    for unit in sorted(_DIAGNOSTIC_UNITS, key=len, reverse=True):
        folded_unit = unit.casefold()
        if folded.endswith(folded_unit) and len(folded) > len(folded_unit):
            return folded[: -len(folded_unit)]
    return folded


def _pair_mismatch_reason(claim_value: str, candidate_value: str) -> NumericClaimFailureMismatchReason | None:
    """Classify one rejected pair without retaining either value."""

    normalized_claim = normalized_compare_text(claim_value)
    normalized_candidate = normalized_compare_text(candidate_value)
    if normalized_claim == normalized_candidate:
        return None
    if _diagnostic_without_extended_whitespace(claim_value) == _diagnostic_without_extended_whitespace(candidate_value):
        return "whitespace_only"
    if _diagnostic_without_punctuation(claim_value) == _diagnostic_without_punctuation(candidate_value):
        return "punctuation_only"
    if _diagnostic_without_unit_suffix(claim_value) == _diagnostic_without_unit_suffix(candidate_value):
        return "unit_suffix_difference"
    if (
        normalized_claim.startswith(normalized_candidate)
        or normalized_claim.endswith(normalized_candidate)
        or normalized_candidate.startswith(normalized_claim)
        or normalized_candidate.endswith(normalized_claim)
    ):
        return "prefix_suffix_difference"
    return None


def _numeric_claim_mismatch_reason(
    *, claim_value: str, quantitative_candidate_values: tuple[str, ...]
) -> NumericClaimFailureMismatchReason:
    """Return a conservative, non-content reason for a failed normalized match."""

    unmatched_candidates = tuple(
        candidate
        for candidate in quantitative_candidate_values
        if normalized_compare_text(candidate) != normalized_compare_text(claim_value)
    )
    if len(unmatched_candidates) > 1 and len(_DIAGNOSTIC_NUMBER_PATTERN.findall(claim_value)) > 1:
        return "multiple_token_difference"
    reasons = {
        reason
        for candidate in unmatched_candidates
        if (reason := _pair_mismatch_reason(claim_value, candidate)) is not None
    }
    if len(reasons) == 1:
        return next(iter(reasons))
    return "unknown"


def _numeric_claim_candidate_count_category(count: int) -> NumericClaimCandidateCountCategory:
    if count == 0:
        return "none"
    if count == 1:
        return "single"
    return "multiple"


def _numeric_claim_anchor_local_relation(
    *, claim_value: str, anchor_canonical_text: str | None
) -> tuple[int | None, NumericClaimAnchorQuantitativeCountCategory | None, NumericClaimAnchorRelationState]:
    """Classify a failed claim against quantitative tokens in its own anchor only."""

    if anchor_canonical_text is None:
        return None, None, "unknown"
    anchor_quantitative_values = tuple(
        token.value
        for token in classify_numeric_tokens(anchor_canonical_text)
        if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
    )
    anchor_quantitative_count = len(anchor_quantitative_values)
    anchor_quantitative_count_category = _numeric_claim_candidate_count_category(anchor_quantitative_count)
    if anchor_quantitative_count == 0:
        return anchor_quantitative_count, anchor_quantitative_count_category, "no_anchor_quantitative_token"
    if anchor_quantitative_count > 1:
        return anchor_quantitative_count, anchor_quantitative_count_category, "multiple_anchor_quantitative_tokens"
    normalized_claim = normalized_compare_text(claim_value)
    normalized_token = normalized_compare_text(anchor_quantitative_values[0])
    if normalized_claim == normalized_token:
        relation_state: NumericClaimAnchorRelationState = "exact_anchor_quantitative_token"
    elif normalized_token in normalized_claim:
        relation_state = "claim_wraps_single_anchor_token"
    elif normalized_claim in normalized_token:
        relation_state = "claim_is_subexpression_of_single_anchor_token"
    else:
        relation_state = "unknown"
    return anchor_quantitative_count, anchor_quantitative_count_category, relation_state


def _numeric_claim_failure_diagnostic(
    *,
    proposal_key: str,
    claim_index: int,
    claim_value: str,
    anchor_span_id: str,
    classified_tokens: tuple[object, ...],
    selected_span_ids: tuple[str, ...],
    known_anchor_span_ids: set[str],
    comparison_quantitative_values: set[str] | None = None,
    anchor_canonical_text: str | None = None,
) -> NumericClaimFailureDiagnostic:
    """Build a claim-level diagnostic without retaining any source content."""

    classification: NumericClaimFailureClassification = "unmatched"
    match_state: NumericClaimFailureMatchState = "unknown"
    category: NumericClaimFailureCategory = "unmatched_assertion_expression"
    mismatch_reason: NumericClaimFailureMismatchReason | None = None
    candidate_count: int | None = None
    candidate_classifications: tuple[NumericClaimCandidateClassification, ...] | None = None
    candidate_count_category: NumericClaimCandidateCountCategory | None = None
    relation_state: NumericClaimFailureRelationState = "unknown"
    (
        anchor_quantitative_count,
        anchor_quantitative_count_category,
        anchor_relation_state,
    ) = _numeric_claim_anchor_local_relation(
        claim_value=claim_value,
        anchor_canonical_text=anchor_canonical_text,
    )
    normalized_claim = normalized_compare_text(claim_value)
    quantitative_candidate_values_list: list[str] = []
    for token in classified_tokens:
        token_value = getattr(token, "value", None)
        if (
            isinstance(token_value, str)
            and getattr(token, "classification", None) == NumericTokenClass.QUANTITATIVE_VALUE
        ):
            quantitative_candidate_values_list.append(token_value)
    quantitative_candidate_values = tuple(quantitative_candidate_values_list)
    quantitative_candidate_set = (
        comparison_quantitative_values
        if comparison_quantitative_values is not None
        else {
            normalized_value
            for normalized_value in (
                normalized_compare_text(candidate) for candidate in quantitative_candidate_values_list
            )
        }
    )
    has_quantitative_candidate = any(
        getattr(token, "classification", None) == NumericTokenClass.QUANTITATIVE_VALUE
        for token in classified_tokens
    )
    has_any_token = bool(classified_tokens)
    for token in classified_tokens:
        token_value = getattr(token, "value", None)
        if not isinstance(token_value, str) or normalized_compare_text(token_value) != normalized_claim:
            continue
        classification = _safe_numeric_claim_classification(getattr(token, "classification", None))
        if classification != "quantitative_value":
            match_state = "matched_but_rejected"
            category = "non_quantitative_token"
            relation_state = "rejected_candidate_available"
        break
    else:
        if not has_any_token:
            match_state = "no_assertion_token"
            relation_state = "no_candidate_available"
        elif has_quantitative_candidate:
            match_state = "normalization_mismatch"
            mismatch_reason = _numeric_claim_mismatch_reason(
                claim_value=claim_value,
                quantitative_candidate_values=quantitative_candidate_values,
            )
            candidate_count = len(quantitative_candidate_set)
            candidate_classifications = tuple("quantitative_value" for _ in range(candidate_count))
            candidate_count_category = _numeric_claim_candidate_count_category(candidate_count)
            relation_state = cast(
                NumericClaimFailureRelationState,
                {
                    "single": "single_candidate_available",
                    "multiple": "multiple_candidates_available",
                    "none": "no_candidate_available",
                }[candidate_count_category],
            )
        else:
            relation_state = "no_candidate_available"
    return NumericClaimFailureDiagnostic(
        proposal_key=proposal_key,
        claim_index=claim_index,
        classification=classification,
        match_state=match_state,
        anchor_exists=anchor_span_id in selected_span_ids and anchor_span_id in known_anchor_span_ids,
        failure_category=category,
        relation_state=relation_state,
        mismatch_reason=mismatch_reason,
        candidate_count=candidate_count,
        candidate_classifications=candidate_classifications,
        candidate_count_category=candidate_count_category,
        anchor_quantitative_count=anchor_quantitative_count,
        anchor_quantitative_count_category=anchor_quantitative_count_category,
        anchor_relation_state=anchor_relation_state,
    )


def _claim_values(claims: Iterable[GroundedNumericClaim]) -> set[str]:
    return {normalized_compare_text(claim.value) for claim in claims}


def validate_step2a_response(*, request: Step2ARequest, response: Step2AResponse) -> ContractValidationReport:
    errors: list[str] = []
    numeric_token_audit: list[ProposalNumericTokenAudit] = []
    numeric_claim_failure_diagnostics: list[NumericClaimFailureDiagnostic] = []
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
            for claim_index, claim in enumerate(proposal.numeric_claims):
                if normalized_compare_text(claim.value) not in quantitative_values:
                    errors.append(f"{proposal.proposal_key}: numeric claim is not classified as a quantitative value")
                    anchor_span = spans_by_id.get(claim.anchor_span_id)
                    numeric_claim_failure_diagnostics.append(
                        _numeric_claim_failure_diagnostic(
                            proposal_key=proposal.proposal_key,
                            claim_index=claim_index,
                            claim_value=claim.value,
                            anchor_span_id=claim.anchor_span_id,
                            classified_tokens=classified_tokens,
                            selected_span_ids=selected_span_ids,
                            known_anchor_span_ids=known_anchor_span_ids,
                            comparison_quantitative_values=quantitative_values,
                            anchor_canonical_text=anchor_span.canonical_text if anchor_span is not None else None,
                        )
                    )
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
        numeric_claim_failure_diagnostics=tuple(numeric_claim_failure_diagnostics),
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
