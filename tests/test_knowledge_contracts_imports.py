"""Facade completeness for examcard.knowledge.contracts (blueprint section 8-2).

EXTERNAL_NAMES is the exact machine-scanned external usage surface of the legacy
scripts tree (147 distinct names imported via ``from ...semantic_contracts import``,
including the two historical re-exports CompletenessMode / normalized_compare_text).
Every name must stay importable from the facade and listed in ``__all__``.
"""
from __future__ import annotations

import examcard.knowledge.contracts as contracts
from examcard.evidence.layout import normalized_compare_text as evidence_normalized_compare_text
from examcard.knowledge.schema import CompletenessMode as schema_completeness_mode

EXTERNAL_NAMES = (
    "AnchorSpanSelectionDerivationState",
    "COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION",
    "COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_PROMPT_VERSION",
    "COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION",
    "CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION",
    "CardPresentation",
    "CompleteTableStructuralProjectionState",
    "CompletenessMode",
    "ConditionClaimDerivationState",
    "ContinuationAllItemsAuditState",
    "ContinuationBinding",
    "ContinuationBindingFailureCode",
    "ContinuationBindingState",
    "ContinuationRecord",
    "ContinuationTitleProjectionProvenance",
    "ContractValidationReport",
    "CoverageObligationCoverageState",
    "CoverageObligationForensicCategory",
    "DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION",
    "DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION",
    "EvidenceAnchorSpan",
    "FactSummaryInput",
    "MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION",
    "MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION",
    "ModelNumericClaim",
    "NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION",
    "NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION",
    "NumericClaimDerivationState",
    "NumericTokenClass",
    "ProjectionDecisionRecord",
    "ProjectionSelection",
    "REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION",
    "ReviewedCanonicalTitleProjection",
    "ReviewedDiagramEdgeInput",
    "ReviewedDiagramNodeInput",
    "ReviewedStructuralDiagramInput",
    "SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION",
    "SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION",
    "SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION",
    "SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_PROMPT_VERSION",
    "SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION",
    "STEP2A_REQUEST_VERSION",
    "STEP2A_SEMANTIC_PROJECTION_VERSION",
    "STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION",
    "STRUCTURAL_INDEX_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION",
    "STRUCTURAL_INDEX_OCCURRENCE_STEP2A_PROMPT_VERSION",
    "STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION",
    "SectionTextInput",
    "Step2AAnchorSpanSelectionAudit",
    "Step2ACompleteTableStructuralProjectionAudit",
    "Step2AConditionClaimAudit",
    "Step2ACoverageObligation",
    "Step2ACoverageObligationContextInput",
    "Step2ACoverageObligationForensic",
    "Step2ACoverageObligationInput",
    "Step2ACoverageObligationKind",
    "Step2AFactProposal",
    "Step2AFailure",
    "Step2AModelFactProposal",
    "Step2ANumericClaimAudit",
    "Step2ANumericClaimDerivationResult",
    "Step2ARequest",
    "Step2AResponse",
    "Step2AResultPayload",
    "Step2ASectionInput",
    "Step2ASemanticProjection",
    "Step2ATableCellAnchorAudit",
    "Step2ATableNumericOccurrenceAudit",
    "Step2ATableStructuralIndexOccurrenceAudit",
    "Step2AV13ModelFactProposal",
    "Step2AV13ResultPayload",
    "Step2AV14ModelFactProposal",
    "Step2AV14ResultPayload",
    "Step2AV15ModelFactProposal",
    "Step2AV15ResultPayload",
    "Step2AV16ModelFactProposal",
    "Step2AV16ResultPayload",
    "Step2AV17ResultPayload",
    "Step2AV18ResultPayload",
    "Step2AV19ResultPayload",
    "Step2AV20ModelFactProposal",
    "Step2AV20ResultPayload",
    "Step2AV21ResultPayload",
    "Step2AV22ResultPayload",
    "Step2AV23ModelFactProposal",
    "Step2AV23ResultPayload",
    "Step2BCardProposal",
    "Step2BRequest",
    "Step2BResponse",
    "Step2BResultPayload",
    "Step2DecoderConfig",
    "Step2FailureState",
    "Step2RequestIdentity",
    "Step2ResponseFormat",
    "Step2ResultState",
    "Step2Stage",
    "StructuredTableInput",
    "TABLE_CONTINUATION_STEP2A_REQUEST_VERSION",
    "TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_MODEL_INPUT_VERSION",
    "TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION",
    "TABLE_COVERAGE_OBLIGATION_STEP2A_MODEL_INPUT_VERSION",
    "TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION",
    "TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_MODEL_INPUT_VERSION",
    "TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION",
    "TableAtomicSubfactCoverageState",
    "TableAtomicSubfactKind",
    "TableAtomicSubfactRequirement",
    "TableCellInput",
    "TableContinuationRequestInput",
    "TableContinuationSourceCellInput",
    "TableContinuationSourcePageInput",
    "TableGridIntegrityRecord",
    "TableGridIntegrityStatus",
    "TableNumericOccurrenceState",
    "TableRowInput",
    "TableSemanticCoverageState",
    "TableStructuralIndexOccurrenceState",
    "build_continuation_binding",
    "build_reviewed_continuation_bindings",
    "build_step2a_request",
    "build_step2a_section",
    "build_step2b_request",
    "canonical_step2_sha256",
    "classify_numeric_tokens",
    "condition_operators_in_text",
    "default_step2_prompt_template",
    "derive_step2a_anchor_span_selection",
    "derive_step2a_condition_claims",
    "derive_step2a_coverage_obligation_forensics",
    "derive_step2a_coverage_obligation_input",
    "derive_step2a_numeric_claims",
    "derive_step2a_table_cell_anchors",
    "fact_summary_from_proposal",
    "fingerprint_step2_prompt",
    "fingerprint_step2_relay",
    "make_continuation_record_id",
    "make_evidence_anchor_span_id",
    "make_step2_request_identity",
    "make_step2a_v13_proposal_key",
    "make_table_atomic_subfact_requirement_id",
    "make_table_continuation_controlled_section_id",
    "make_table_continuation_request_input_id",
    "make_table_structural_index_occurrence_id",
    "normalized_compare_text",
    "step2a_result_payload_model",
    "validate_step2a_response",
    "validate_step2b_response",
)


def test_external_usage_surface_is_147_names() -> None:
    assert len(EXTERNAL_NAMES) == 147


def test_every_external_name_is_importable_from_the_facade() -> None:
    missing = [name for name in EXTERNAL_NAMES if not hasattr(contracts, name)]
    assert not missing, f"facade lost externally used names: {missing}"


def test_every_external_name_is_declared_in_all() -> None:
    exported = set(contracts.__all__)
    missing = [name for name in EXTERNAL_NAMES if name not in exported]
    assert not missing, f"__all__ lost externally used names: {missing}"


def test_all_entries_resolve() -> None:
    broken = [name for name in contracts.__all__ if not hasattr(contracts, name)]
    assert not broken, f"__all__ declares unresolvable names: {broken}"


def test_historical_re_exports_bind_their_true_definitions() -> None:
    assert contracts.CompletenessMode is schema_completeness_mode
    assert contracts.normalized_compare_text is evidence_normalized_compare_text
