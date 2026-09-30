"""Semantic planner contracts (Step 2A/2B), split from legacy semantic_contracts.py.

Facade: every externally used name (blueprint section 6, 147 names) plus the two
historical re-exports (CompletenessMode, normalized_compare_text) is explicitly
re-exported here so callers only change their import prefix.
"""
from ...evidence.layout import normalized_compare_text
from ..schema import CompletenessMode
from .anchors import EvidenceAnchorSpan, make_evidence_anchor_span_id
from .audits import (
    AnchorSpanSelectionDerivationState,
    ConditionClaimDerivationState,
    ContinuationAllItemsAuditState,
    CoverageObligationCoverageState,
    CoverageObligationForensicCategory,
    NumericClaimDerivationState,
    Step2AAnchorSpanDerivationResult,
    Step2AAnchorSpanSelectionAudit,
    Step2ACompleteTableStructuralProjectionAudit,
    Step2AConditionClaimAudit,
    Step2AConditionClaimDerivationResult,
    Step2AContinuationAudit,
    Step2ACoverageObligationAudit,
    Step2ACoverageObligationForensic,
    Step2ANumericClaimAudit,
    Step2ANumericClaimDerivationResult,
    Step2ATableAtomicSubfactAudit,
    Step2ATableCellAnchorAudit,
    Step2ATableCellDerivationResult,
    Step2ATableNumericOccurrenceAudit,
    Step2ATableSemanticCoverageAudit,
    Step2ATableStructuralIndexOccurrenceAudit,
    TableAtomicSubfactCoverageState,
    TableCellAnchorDerivationState,
    TableSemanticCoverageState,
    assess_step2a_continuations,
    assess_step2a_coverage_obligations,
    assess_step2a_table_atomic_subfacts,
    assess_step2a_table_semantic_coverage,
    derive_step2a_coverage_obligation_forensics,
)
from .claims import (
    GroundedConditionClaim,
    GroundedNumericClaim,
    ModelNumericClaim,
    ProposalNumericTokenAudit,
)
from .continuation import (
    ContinuationBinding,
    ContinuationBindingFailureCode,
    ContinuationBindingState,
    ContinuationRecord,
    ContinuationState,
    TableGridIntegrityRecord,
    TableGridIntegrityStatus,
    build_continuation_binding,
    build_reviewed_continuation_bindings,
    make_continuation_binding_id,
    make_continuation_record_id,
)
from .derivation import (
    derive_step2a_anchor_span_selection,
    derive_step2a_condition_claims,
    derive_step2a_numeric_claims,
    derive_step2a_table_cell_anchors,
)
from .enums import (
    CardPresentation,
    ConditionOperator,
    NumericTokenClass,
    Step2DecoderConfig,
    Step2FailureState,
    Step2ResponseFormat,
    Step2ResultState,
    Step2Stage,
    condition_operators_in_text,
    default_step2_decoder,
)
from .hashing import (
    canonical_step2_sha256,
    fingerprint_step2_prompt,
    fingerprint_step2_relay,
)
from .numeric_tokens import NumericTokenClassificationRecord, classify_numeric_tokens
from .obligations import (
    Step2ACoverageObligation,
    Step2ACoverageObligationAnchorContext,
    Step2ACoverageObligationAnchorRole,
    Step2ACoverageObligationContext,
    Step2ACoverageObligationContextInput,
    Step2ACoverageObligationInput,
    Step2ACoverageObligationKind,
    derive_step2a_coverage_obligation_input,
    make_step2a_coverage_obligation_context_id,
    make_step2a_coverage_obligation_context_input_id,
    make_step2a_coverage_obligation_id,
    make_step2a_coverage_obligation_input_id,
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
from .projection import (
    ProjectionDecisionRecord,
    ProjectionSelection,
    ReviewedDiagramEdgeInput,
    ReviewedDiagramNodeInput,
    ReviewedStructuralDiagramInput,
    Step2ASemanticProjection,
)
from .prompts import default_step2_prompt_template
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
    Step2AProductionSourceOwnedArbitraryPDFModelFactProposal,
    make_step2a_v13_proposal_key,
    make_step2a_v14_proposal_key,
    make_step2a_v15_proposal_key,
    make_step2a_v16_proposal_key,
    make_step2a_v17_proposal_key,
    make_step2a_v18_proposal_key,
)
from .request_helpers import derive_step2a_coverage_obligation_context_input
from .request_identity import Step2RequestIdentity, make_step2_request_identity
from .responses import (
    Step2AFailure,
    Step2AResponse,
    Step2AResultPayload,
    Step2AV13ResultPayload,
    Step2AV14ResultPayload,
    Step2AV15ResultPayload,
    Step2AV16ResultPayload,
    Step2AV17ResultPayload,
    Step2AV18ResultPayload,
    Step2AV19ResultPayload,
    Step2AV20ResultPayload,
    Step2AV21ResultPayload,
    Step2AV22ResultPayload,
    Step2AV23ResultPayload,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    step2a_result_payload_model,
)
from .step2a_builders import build_step2a_request, build_step2a_section
from .step2a_request import Step2ARequest
from .source_owned_contract import (
    CanonicalFactAssemblyStatus,
    SourceOwnedCanonicalProposal,
    SourceOwnedCanonicalFact,
    SourceOwnedAnchorSelectionResult,
    SourceOwnedAnchorSelectionStatus,
    SourceOwnedLocalDerivedFacts,
    SourceOwnedLocalDerivationResult,
    SourceOwnedModelProposal,
    SourceOwnedTableAtomicDerivationStatus,
    SourceOwnedStep2ACanonicalizer,
    Step2AContractProfile,
    Step2AExtractionPath,
    Step2AResponseHandlingMode,
    canonicalize_source_owned_model_proposals,
    source_owned_assemble_canonical_fact,
    source_owned_derive_atomic_numeric_claims,
    source_owned_derive_table_atomic_numeric_claims,
    source_owned_select_anchor_spans,
    select_step2a_extraction_path,
    source_owned_derive_from_evidence,
    source_owned_local_derivation,
    step2a_request_version_for_profile,
    step2a_response_handling_mode,
)
from .source_owned_card_adapter import (
    SourceOwnedCardCandidateGenerationStatus,
    SourceOwnedKnowledgeCardCandidate,
    source_owned_fact_to_card_candidate,
)
from .source_owned_card_admission import (
    SourceOwnedCardAdmissionRejectionReason,
    SourceOwnedCardAdmissionResult,
    SourceOwnedCardAdmissionStatus,
    source_owned_admit_card_candidate,
)
from .source_owned_card_pipeline_bridge import (
    SourceOwnedKnowledgeCardBridgeResult,
    SourceOwnedKnowledgeCardBridgeStatus,
    source_owned_bridge_to_knowledge_card,
)
from .source_owned_card_materialization import (
    SourceOwnedKnowledgeCardMaterializationInput,
    SourceOwnedKnowledgeCardMaterializationResult,
    SourceOwnedKnowledgeCardMaterializationStatus,
    source_owned_materialize_knowledge_card,
)
from .source_owned_extraction import (
    SourceOwnedEvidenceCollection,
    source_owned_extract_document_proposals_from_evidence,
)
from .source_owned_provider_dispatch import (
    SourceOwnedProviderCompletion,
    SourceOwnedProviderDispatchResult,
    SourceOwnedProviderDispatchStatus,
    SourceOwnedProviderRequest,
    SourceOwnedProviderRequestIdentity,
    SourceOwnedProviderResponseMetadata,
    SourceOwnedProviderResponseStatus,
    SourceOwnedProviderTransport,
    SOURCE_OWNED_PROVIDER_MAX_RETRIES,
    build_source_owned_provider_request,
    decode_source_owned_provider_completion,
    dispatch_source_owned_provider_request,
)
from .step2b import (
    ContinuationTitleProjectionProvenance,
    FactSummaryInput,
    ReviewedCanonicalTitleProjection,
    Step2BCardProposal,
    Step2BRequest,
    Step2BResponse,
    Step2BResultPayload,
    build_step2b_request,
    fact_summary_from_atomic_fact,
    fact_summary_from_proposal,
)
from .table_inputs import (
    CompleteTableSemanticCoverage,
    SectionTextInput,
    Step2ASectionInput,
    StructuredTableInput,
    TableAtomicSubfactKind,
    TableAtomicSubfactRequirement,
    TableCellInput,
    TableContinuationRequestInput,
    TableContinuationSourceCellInput,
    TableContinuationSourcePageInput,
    TableRowInput,
    derive_table_atomic_subfact_requirements,
    make_table_atomic_subfact_requirement_id,
    make_table_continuation_controlled_section_id,
    make_table_continuation_request_input_id,
)
from .testing import DeterministicFakeLLM
from .validation import (
    ContractValidationReport,
    validate_step2a_response,
    validate_step2b_response,
)
from .versions import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_PROMPT_VERSION,
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION,
    CONTROLLED_DIAGRAM_ANCHOR_STEP2A_MODEL_INPUT_VERSION,
    CONTROLLED_DIAGRAM_ANCHOR_STEP2A_REQUEST_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    EVIDENCE_ANCHOR_SPAN_VERSION,
    LEGACY_STEP2A_MODEL_INPUT_VERSION,
    LEGACY_STEP2A_REQUEST_VERSION,
    LEGACY_STEP2A_SEMANTIC_PROJECTION_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    MULTIPAGE_STEP2A_REQUEST_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
    PREVIOUS_STEP2A_REQUEST_VERSION,
    PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION,
    PREVIOUS_STEP2B_REQUEST_VERSION,
    REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_PROMPT_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_MODEL_INPUT_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_PROMPT_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    STEP2A_COVERAGE_OBLIGATION_CONTEXT_INPUT_VERSION,
    STEP2A_COVERAGE_OBLIGATION_INPUT_VERSION,
    STEP2A_MODEL_INPUT_VERSION,
    STEP2A_REQUEST_VERSION,
    STEP2A_SEMANTIC_PROJECTION_VERSION,
    STEP2B_REQUEST_VERSION,
    STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_PROMPT_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
    TABLE_CONTINUATION_REQUEST_INPUT_VERSION,
    TABLE_CONTINUATION_STEP2A_MODEL_INPUT_VERSION,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_MODEL_INPUT_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_MODEL_INPUT_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_MODEL_INPUT_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
)

import types as _types

__all__ = sorted(
    name
    for name, value in globals().items()
    if not name.startswith("_") and not isinstance(value, _types.ModuleType)
)
