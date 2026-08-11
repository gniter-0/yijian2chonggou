"""One-shot mechanical splitter: knowledge_v2/semantic_contracts.py -> knowledge/contracts/*.

Blueprint: docs/prework/semantic_contracts_split_blueprint.md (23 submodules + facade).
Method: AST top-level symbol segmentation. Each symbol's source segment is sliced
byte-for-byte from the legacy file (decorators included, trailing comments kept with
the preceding symbol), so hash-bearing bodies (R7: canonical_step2_sha256/_canonicalize/
fingerprint_*, R2: the 30KB prompt template) are moved verbatim. Only module docstrings
and import headers are authored here.

The legacy repo is read-only (iron rule bc-b8b9aca6 / skeleton 6-0-2).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path

DEFAULT_OLD_FILE = Path(r"c:\Users\liushaocheng\Desktop\getinfo\yijian2\scripts\knowledge_v2\semantic_contracts.py")
CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "src" / "examcard" / "knowledge" / "contracts"

# ---------------------------------------------------------------------------
# Module plan: (filename, docstring, import header) + ordered symbol lists.
# Order within a module preserves legacy source order except occurrences.py,
# where TableGridMetadataReference precedes CompleteTableStructuralProjection (R4).
# ---------------------------------------------------------------------------

MODULES: dict[str, dict[str, object]] = {
    "versions.py": {
        "doc": "Frozen Step 2A/2B contract version strings (byte-exact; never edit).",
        "header": "",
        "symbols": [
            "LEGACY_STEP2A_REQUEST_VERSION",
            "PREVIOUS_STEP2A_REQUEST_VERSION",
            "STEP2A_REQUEST_VERSION",
            "MULTIPAGE_STEP2A_REQUEST_VERSION",
            "REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION",
            "MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION",
            "NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION",
            "SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION",
            "DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION",
            "TABLE_CONTINUATION_STEP2A_REQUEST_VERSION",
            "TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION",
            "TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION",
            "TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION",
            "COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION",
            "COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_PROMPT_VERSION",
            "STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION",
            "STRUCTURAL_INDEX_OCCURRENCE_STEP2A_PROMPT_VERSION",
            "SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION",
            "SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_PROMPT_VERSION",
            "PREVIOUS_STEP2B_REQUEST_VERSION",
            "STEP2B_REQUEST_VERSION",
            "CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION",
            "LEGACY_STEP2A_SEMANTIC_PROJECTION_VERSION",
            "STEP2A_SEMANTIC_PROJECTION_VERSION",
            "EVIDENCE_ANCHOR_SPAN_VERSION",
            "LEGACY_STEP2A_MODEL_INPUT_VERSION",
            "STEP2A_MODEL_INPUT_VERSION",
            "MULTIPAGE_STEP2A_MODEL_INPUT_VERSION",
            "REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION",
            "MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION",
            "NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION",
            "SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION",
            "DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION",
            "TABLE_CONTINUATION_STEP2A_MODEL_INPUT_VERSION",
            "TABLE_COVERAGE_OBLIGATION_STEP2A_MODEL_INPUT_VERSION",
            "TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_MODEL_INPUT_VERSION",
            "TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_MODEL_INPUT_VERSION",
            "COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION",
            "STRUCTURAL_INDEX_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION",
            "SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION",
            "_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS",
            "_MULTIPAGE_STEP2A_REQUEST_VERSIONS",
            "TABLE_CONTINUATION_REQUEST_INPUT_VERSION",
            "STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION",
            "STEP2A_COVERAGE_OBLIGATION_INPUT_VERSION",
            "STEP2A_COVERAGE_OBLIGATION_CONTEXT_INPUT_VERSION",
        ],
    },
    "hashing.py": {
        "doc": (
            "Legal legacy Step 2 canonical hash / fingerprint implementations.\n"
            "\n"
            "Frozen byte-for-byte per iron rule bc-b8b9aca6 and blueprint R7: never fold\n"
            "into examcard.common.hashing and never re-normalize; one byte of drift\n"
            "invalidates every persisted request_id / cache_key / golden fixture."
        ),
        "header": (
            "import hashlib\n"
            "import json\n"
            "from collections.abc import Mapping\n"
            "from enum import Enum\n"
            "from typing import Any\n"
        ),
        "symbols": [
            "_OFFLINE_RELAY_BASE_URL",
            "_canonicalize",
            "canonical_step2_sha256",
            "_canonical_sha256",
            "fingerprint_step2_relay",
            "fingerprint_step2_prompt",
        ],
    },
    "enums.py": {
        "doc": "Step 2 stage/presentation/failure enums, condition operators, decoder config.",
        "header": (
            "from enum import StrEnum\n"
            "\n"
            "from pydantic import Field\n"
            "\n"
            "from ...materials.schema import StrictModel\n"
        ),
        "symbols": [
            "Step2Stage",
            "CardPresentation",
            "NumericTokenClass",
            "ConditionOperator",
            "_condition_operator_occurrences",
            "condition_operators_in_text",
            "Step2ResponseFormat",
            "Step2DecoderConfig",
            "default_step2_decoder",
            "Step2FailureState",
            "Step2ResultState",
        ],
    },
    "numeric_tokens.py": {
        "doc": "Deterministic numeric-token classification (regex constants byte-exact).",
        "header": (
            "import re\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...materials.schema import StrictModel\n"
            "\n"
            "from .enums import NumericTokenClass\n"
        ),
        "symbols": [
            "_NUMERIC_DECIMAL",
            "_NUMERIC_UNIT",
            "_QUANTITATIVE_RATIO_PATTERN",
            "_QUANTITATIVE_RANGE_PATTERN",
            "_QUANTITATIVE_TOKEN_PATTERN",
            "_TABLE_GRID_DIMENSION_PATTERNS",
            "_MATERIAL_GRADE_PATTERN",
            "_IDENTIFIER_PATTERN",
            "_SECTION_NUMBER_PATTERN",
            "_LIST_INDEX_PATTERN",
            "_ORDINAL_REFERENCE_PATTERN",
            "_BARE_NUMBER_PATTERN",
            "NumericTokenClassificationRecord",
            "classify_numeric_tokens",
        ],
    },
    "prompts.py": {
        "doc": (
            "Frozen Step 2A/2B prompt templates.\n"
            "\n"
            "Byte-exact per blueprint R2: fingerprint_step2_prompt hashes this text, so any\n"
            "reflow/reformat invalidates every cache key. Exclude from auto-formatters."
        ),
        "header": (
            "from .enums import Step2Stage\n"
            "from .versions import (\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_PROMPT_VERSION,\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,\n"
            "    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,\n"
            "    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_PROMPT_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_PROMPT_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,\n"
            ")\n"
        ),
        "symbols": ["default_step2_prompt_template"],
    },
    "request_identity.py": {
        "doc": "Step 2 request identity: payload/prompt/schema hashes -> request_id/cache_key.",
        "header": (
            "import re\n"
            "from collections.abc import Mapping\n"
            "from typing import Any\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...materials.schema import StrictModel\n"
            "\n"
            "from .enums import Step2DecoderConfig, Step2Stage, default_step2_decoder\n"
            "from .hashing import (\n"
            "    _OFFLINE_RELAY_BASE_URL,\n"
            "    _canonical_sha256,\n"
            "    fingerprint_step2_prompt,\n"
            "    fingerprint_step2_relay,\n"
            ")\n"
            "from .prompts import default_step2_prompt_template\n"
            "from .versions import LEGACY_STEP2A_REQUEST_VERSION\n"
        ),
        "symbols": ["_SHA256_PATTERN", "Step2RequestIdentity", "make_step2_request_identity"],
    },
    "anchors.py": {
        "doc": "Evidence anchor spans: deterministic selectable subranges of immutable Evidence.",
        "header": (
            "import hashlib\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...evidence.schema import EvidenceKind\n"
            "from ...materials.schema import PolicyDecision, StrictModel\n"
            "\n"
            "from .hashing import _canonical_sha256\n"
            "from .request_identity import _SHA256_PATTERN\n"
            "from .versions import EVIDENCE_ANCHOR_SPAN_VERSION\n"
        ),
        "symbols": [
            "_TABLE_KINDS",
            "_DIRECT_TEXT_KINDS",
            "make_evidence_anchor_span_id",
            "EvidenceAnchorSpan",
        ],
    },
    "continuation.py": {
        "doc": "Continuation records/bindings: page-end markers and adjacent-page closures.",
        "header": (
            "from collections.abc import Iterable\n"
            "from enum import StrEnum\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...materials.schema import StrictModel\n"
            "from ..continuation.review import ContinuationBindingReviewSidecar, ContinuationMarkerDisposition\n"
            "\n"
            "from .hashing import _canonical_sha256\n"
            "from .request_identity import _SHA256_PATTERN\n"
        ),
        "symbols": [
            "TableGridIntegrityStatus",
            "ContinuationState",
            "ContinuationBindingState",
            "ContinuationBindingFailureCode",
            "make_continuation_record_id",
            "ContinuationRecord",
            "make_continuation_binding_id",
            "ContinuationBinding",
            "build_continuation_binding",
            "build_reviewed_continuation_bindings",
            "TableGridIntegrityRecord",
        ],
    },
    "projection.py": {
        "doc": "Step 2A semantic projection: immutable policy-filtered page source manifests.",
        "header": (
            "from enum import StrEnum\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...evidence.schema import EvidenceKind\n"
            "from ...materials.schema import PolicyDecision, SemanticRole, StrictModel\n"
            "\n"
            "from .anchors import _DIRECT_TEXT_KINDS, EvidenceAnchorSpan\n"
            "from .continuation import ContinuationRecord, TableGridIntegrityRecord\n"
            "from .hashing import _canonical_sha256\n"
            "from .request_identity import _SHA256_PATTERN\n"
            "from .versions import (\n"
            "    LEGACY_STEP2A_SEMANTIC_PROJECTION_VERSION,\n"
            "    STEP2A_SEMANTIC_PROJECTION_VERSION,\n"
            ")\n"
        ),
        "symbols": [
            "ProjectionSelection",
            "ProjectionDecisionRecord",
            "ReviewedDiagramNodeInput",
            "ReviewedDiagramEdgeInput",
            "ReviewedStructuralDiagramInput",
            "Step2ASemanticProjection",
            "_validate_parent_anchor_spans",
        ],
    },
    "table_inputs.py": {
        "doc": "Structured table / section / controlled table-continuation request inputs.",
        "header": (
            "import hashlib\n"
            "from collections.abc import Iterable\n"
            "from enum import StrEnum\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...evidence.schema import BBox, EvidenceKind\n"
            "from ...materials.schema import StrictModel\n"
            "from ..schema import FactType\n"
            "\n"
            "from .anchors import _TABLE_KINDS, EvidenceAnchorSpan\n"
            "from .enums import NumericTokenClass, condition_operators_in_text\n"
            "from .hashing import _canonical_sha256\n"
            "from .numeric_tokens import classify_numeric_tokens\n"
            "from .projection import ReviewedStructuralDiagramInput, _validate_parent_anchor_spans\n"
            "from .request_identity import _SHA256_PATTERN\n"
            "from .versions import (\n"
            "    STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION,\n"
            "    TABLE_CONTINUATION_REQUEST_INPUT_VERSION,\n"
            ")\n"
        ),
        "symbols": [
            "TableAtomicSubfactKind",
            "make_table_atomic_subfact_requirement_id",
            "TableAtomicSubfactRequirement",
            "SectionTextInput",
            "TableCellInput",
            "TableRowInput",
            "StructuredTableInput",
            "TableContinuationSourceCellInput",
            "TableContinuationSourcePageInput",
            "make_table_continuation_controlled_section_id",
            "make_table_continuation_request_input_id",
            "TableContinuationRequestInput",
            "_geometry_header_cell_ids",
            "CompleteTableSemanticCoverage",
            "Step2ASectionInput",
            "_make_section_id",
            "_table_atomic_subfact_kind",
            "derive_table_atomic_subfact_requirements",
        ],
    },
    "obligations.py": {
        "doc": "V18/V19 coverage obligations: deterministic mandatory Fact checklists.",
        "header": (
            "import hashlib\n"
            "from collections.abc import Iterable\n"
            "from enum import StrEnum\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...evidence.schema import EvidenceKind\n"
            "from ...materials.schema import StrictModel\n"
            "from ..schema import FactType\n"
            "\n"
            "from .continuation import ContinuationBinding, ContinuationBindingState\n"
            "from .hashing import _canonical_sha256\n"
            "from .request_identity import _SHA256_PATTERN\n"
            "from .table_inputs import Step2ASectionInput, TableContinuationRequestInput\n"
            "from .versions import (\n"
            "    STEP2A_COVERAGE_OBLIGATION_CONTEXT_INPUT_VERSION,\n"
            "    STEP2A_COVERAGE_OBLIGATION_INPUT_VERSION,\n"
            ")\n"
        ),
        "symbols": [
            "Step2ACoverageObligationKind",
            "make_step2a_coverage_obligation_id",
            "Step2ACoverageObligation",
            "make_step2a_coverage_obligation_input_id",
            "Step2ACoverageObligationInput",
            "Step2ACoverageObligationAnchorRole",
            "Step2ACoverageObligationAnchorContext",
            "make_step2a_coverage_obligation_context_id",
            "Step2ACoverageObligationContext",
            "make_step2a_coverage_obligation_context_input_id",
            "Step2ACoverageObligationContextInput",
            "derive_step2a_coverage_obligation_input",
        ],
    },
    "claims.py": {
        "doc": "Model-owned numeric claims and locally grounded numeric/condition claims.",
        "header": (
            "from pydantic import Field, model_validator\n"
            "from pydantic_core import PydanticCustomError\n"
            "\n"
            "from ...evidence.layout import normalized_compare_text\n"
            "from ...materials.schema import StrictModel\n"
            "\n"
            "from .enums import condition_operators_in_text\n"
            "from .numeric_tokens import NumericTokenClassificationRecord\n"
        ),
        "symbols": [
            "ProposalNumericTokenAudit",
            "ModelNumericClaim",
            "GroundedNumericClaim",
            "GroundedConditionClaim",
        ],
    },
    "proposals.py": {
        "doc": "Step 2A model fact proposals (V8-V23) and the canonical derived proposal.",
        "header": (
            "from collections.abc import Iterable\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...evidence.layout import normalized_compare_text\n"
            "from ...materials.schema import StrictModel\n"
            "from ..schema import CompletenessMode, FactType\n"
            "\n"
            "from .claims import GroundedConditionClaim, GroundedNumericClaim, ModelNumericClaim\n"
            "from .hashing import _canonical_sha256\n"
            "from .table_inputs import CompleteTableSemanticCoverage\n"
            "from .versions import (\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,\n"
            "    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,\n"
            "    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,\n"
            ")\n"
        ),
        "symbols": [
            "Step2AModelFactProposal",
            "make_step2a_v13_proposal_key",
            "make_step2a_v14_proposal_key",
            "make_step2a_v15_proposal_key",
            "make_step2a_v16_proposal_key",
            "make_step2a_v17_proposal_key",
            "make_step2a_v18_proposal_key",
            "Step2AV13ModelFactProposal",
            "Step2AV14ModelFactProposal",
            "Step2AV15ModelFactProposal",
            "Step2AV16ModelFactProposal",
            "Step2AV17ModelFactProposal",
            "Step2AV18ModelFactProposal",
            "Step2AV19ModelFactProposal",
            "Step2AV20ModelFactProposal",
            "Step2AV21ModelFactProposal",
            "Step2AV22ModelFactProposal",
            "Step2AV23ModelFactProposal",
            "Step2AFactProposal",
        ],
    },
    "responses.py": {
        "doc": "Step 2A responses and versioned relay-bound result payload shapes.",
        "header": (
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...materials.schema import StrictModel\n"
            "\n"
            "from .enums import Step2FailureState, Step2ResultState, Step2Stage\n"
            "from .proposals import (\n"
            "    Step2AFactProposal,\n"
            "    Step2AModelFactProposal,\n"
            "    Step2AV13ModelFactProposal,\n"
            "    Step2AV14ModelFactProposal,\n"
            "    Step2AV15ModelFactProposal,\n"
            "    Step2AV16ModelFactProposal,\n"
            "    Step2AV17ModelFactProposal,\n"
            "    Step2AV18ModelFactProposal,\n"
            "    Step2AV19ModelFactProposal,\n"
            "    Step2AV20ModelFactProposal,\n"
            "    Step2AV21ModelFactProposal,\n"
            "    Step2AV22ModelFactProposal,\n"
            "    Step2AV23ModelFactProposal,\n"
            ")\n"
            "from .request_identity import Step2RequestIdentity\n"
            "from .versions import (\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,\n"
            "    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,\n"
            "    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,\n"
            ")\n"
        ),
        "symbols": [
            "Step2AFailure",
            "Step2AResponse",
            "Step2AResultPayload",
            "Step2AV13ResultPayload",
            "Step2AV14ResultPayload",
            "Step2AV15ResultPayload",
            "Step2AV16ResultPayload",
            "Step2AV17ResultPayload",
            "Step2AV18ResultPayload",
            "Step2AV19ResultPayload",
            "Step2AV20ResultPayload",
            "Step2AV21ResultPayload",
            "Step2AV22ResultPayload",
            "Step2AV23ResultPayload",
            "step2a_result_payload_model",
        ],
    },
    "occurrences.py": {
        "doc": (
            "Span references and source-owned table occurrences / structural projections.\n"
            "\n"
            "Note R4: TableGridMetadataReference is ordered before\n"
            "CompleteTableStructuralProjection so pydantic resolves the field type without\n"
            "model_rebuild."
        ),
        "header": (
            "from enum import StrEnum\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...materials.schema import StrictModel\n"
            "\n"
            "from .enums import NumericTokenClass\n"
            "from .hashing import _canonical_sha256\n"
            "from .request_identity import _SHA256_PATTERN\n"
        ),
        "symbols": [
            "ConditionSpanReference",
            "NumericSpanReference",
            "TableNumericOccurrenceState",
            "make_table_numeric_occurrence_id",
            "TableNumericOccurrenceReference",
            "TableStructuralIndexOccurrenceState",
            "make_table_structural_index_occurrence_id",
            "TableStructuralIndexOccurrenceReference",
            "TableStructuralCellRole",
            "make_table_structural_cell_occurrence_id",
            "TableStructuralCellOccurrence",
            "make_table_grid_metadata_id",
            "TableGridMetadataReference",
            "make_complete_table_structural_projection_id",
            "CompleteTableStructuralProjection",
            "CompleteTableStructuralProjectionState",
        ],
    },
    "request_helpers.py": {
        "doc": (
            "Request-scoped helpers shared by audits/derivation/validation.\n"
            "\n"
            "Annotation-only references to Step2ARequest stay behind TYPE_CHECKING (PEP 563)\n"
            "to keep the runtime import DAG acyclic (blueprint section 4)."
        ),
        "header": (
            "from collections.abc import Iterable, Mapping\n"
            "from typing import TYPE_CHECKING\n"
            "\n"
            "from ...evidence.schema import EvidenceKind\n"
            "from ..schema import CompletenessMode, FactType\n"
            "\n"
            "from .anchors import EvidenceAnchorSpan\n"
            "from .continuation import ContinuationBindingState\n"
            "from .obligations import (\n"
            "    Step2ACoverageObligationAnchorContext,\n"
            "    Step2ACoverageObligationAnchorRole,\n"
            "    Step2ACoverageObligationContext,\n"
            "    Step2ACoverageObligationContextInput,\n"
            "    Step2ACoverageObligationKind,\n"
            "    make_step2a_coverage_obligation_context_id,\n"
            "    make_step2a_coverage_obligation_context_input_id,\n"
            ")\n"
            "from .table_inputs import StructuredTableInput\n"
            "from .versions import STEP2A_COVERAGE_OBLIGATION_CONTEXT_INPUT_VERSION\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from .step2a_request import Step2ARequest\n"
        ),
        "symbols": [
            "_step2a_anchor_sources",
            "_ordered_parent_evidence_ids",
            "_derive_v13_source_section_ids",
            "_request_structured_tables",
            "derive_step2a_coverage_obligation_context_input",
            "_derive_v13_completeness_mode",
        ],
    },
    "audits.py": {
        "doc": "Fail-closed Step 2A audit models and assessment functions.",
        "header": (
            "import hashlib\n"
            "from collections.abc import Iterable\n"
            "from enum import StrEnum\n"
            "from typing import TYPE_CHECKING\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...evidence.layout import normalized_compare_text\n"
            "from ...materials.schema import StrictModel\n"
            "from ..continuation.review import ContinuationMarkerDisposition\n"
            "from ..schema import CompletenessMode, FactType\n"
            "\n"
            "from .claims import GroundedConditionClaim, GroundedNumericClaim\n"
            "from .continuation import ContinuationBindingState\n"
            "from .enums import ConditionOperator, NumericTokenClass\n"
            "from .obligations import Step2ACoverageObligationKind\n"
            "from .occurrences import (\n"
            "    CompleteTableStructuralProjection,\n"
            "    CompleteTableStructuralProjectionState,\n"
            "    ConditionSpanReference,\n"
            "    NumericSpanReference,\n"
            "    TableGridMetadataReference,\n"
            "    TableNumericOccurrenceReference,\n"
            "    TableNumericOccurrenceState,\n"
            "    TableStructuralIndexOccurrenceReference,\n"
            "    TableStructuralIndexOccurrenceState,\n"
            ")\n"
            "from .proposals import Step2AFactProposal\n"
            "from .request_helpers import _request_structured_tables\n"
            "from .request_identity import _SHA256_PATTERN\n"
            "from .table_inputs import TableAtomicSubfactKind, _geometry_header_cell_ids\n"
            "from .versions import _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from .step2a_request import Step2ARequest\n"
        ),
        "symbols": [
            "AnchorSpanSelectionDerivationState",
            "Step2AAnchorSpanSelectionAudit",
            "Step2AAnchorSpanDerivationResult",
            "TableCellAnchorDerivationState",
            "Step2ATableCellAnchorAudit",
            "Step2ATableCellDerivationResult",
            "TableSemanticCoverageState",
            "Step2ATableSemanticCoverageAudit",
            "assess_step2a_table_semantic_coverage",
            "TableAtomicSubfactCoverageState",
            "Step2ATableAtomicSubfactAudit",
            "assess_step2a_table_atomic_subfacts",
            "CoverageObligationCoverageState",
            "Step2ACoverageObligationAudit",
            "CoverageObligationForensicCategory",
            "Step2ACoverageObligationForensic",
            "assess_step2a_coverage_obligations",
            "derive_step2a_coverage_obligation_forensics",
            "ContinuationAllItemsAuditState",
            "Step2AContinuationAudit",
            "assess_step2a_continuations",
            "ConditionClaimDerivationState",
            "Step2AConditionClaimAudit",
            "Step2AConditionClaimDerivationResult",
            "NumericClaimDerivationState",
            "Step2ATableStructuralIndexOccurrenceAudit",
            "TableStructuralCellRole_UNUSED_SENTINEL",
            "Step2ACompleteTableStructuralProjectionAudit",
            "Step2ATableNumericOccurrenceAudit",
            "Step2ANumericClaimAudit",
            "Step2ANumericClaimDerivationResult",
        ],
    },
    "step2b.py": {
        "doc": "Step 2B card-planning contracts: fact summaries, requests, responses.",
        "header": (
            "import hashlib\n"
            "from collections.abc import Iterable, Mapping\n"
            "from typing import Any\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...evidence.schema import EvidenceUnit\n"
            "from ...materials.schema import StrictModel\n"
            "from ..identity import make_fact_id\n"
            "from ..schema import AtomicFact, CompletenessMode, FactType, ShortCardException\n"
            "\n"
            "from .enums import CardPresentation, Step2DecoderConfig, Step2ResultState, Step2Stage\n"
            "from .hashing import _OFFLINE_RELAY_BASE_URL, _canonical_sha256\n"
            "from .proposals import Step2AFactProposal\n"
            "from .request_identity import _SHA256_PATTERN, Step2RequestIdentity, make_step2_request_identity\n"
            "from .responses import Step2AFailure\n"
            "from .versions import (\n"
            "    CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION,\n"
            "    PREVIOUS_STEP2B_REQUEST_VERSION,\n"
            "    STEP2B_REQUEST_VERSION,\n"
            ")\n"
        ),
        "symbols": [
            "ContinuationTitleProjectionProvenance",
            "ReviewedCanonicalTitleProjection",
            "FactSummaryInput",
            "Step2BCardProposal",
            "Step2BResponse",
            "Step2BResultPayload",
            "Step2BRequest",
            "_step2b_payload",
            "fact_summary_from_atomic_fact",
            "fact_summary_from_proposal",
            "build_step2b_request",
        ],
    },
    "step2a_request.py": {
        "doc": "Step2ARequest (the giant validated request model) plus its payload serializer.",
        "header": (
            "from collections.abc import Iterable\n"
            "from typing import Any\n"
            "\n"
            "from pydantic import Field, model_validator\n"
            "\n"
            "from ...materials.schema import StrictModel\n"
            "from ..continuation.review import (\n"
            "    ContinuationBindingReviewSidecar,\n"
            "    ContinuationMarkerDisposition,\n"
            "    ContinuationReviewState,\n"
            ")\n"
            "\n"
            "from .continuation import ContinuationBinding, ContinuationBindingState\n"
            "from .enums import Step2Stage\n"
            "from .hashing import _canonical_sha256\n"
            "from .obligations import (\n"
            "    Step2ACoverageObligationContextInput,\n"
            "    Step2ACoverageObligationInput,\n"
            "    derive_step2a_coverage_obligation_input,\n"
            ")\n"
            "from .projection import Step2ASemanticProjection\n"
            "from .request_helpers import derive_step2a_coverage_obligation_context_input\n"
            "from .request_identity import _SHA256_PATTERN, Step2RequestIdentity\n"
            "from .responses import step2a_result_payload_model\n"
            "from .table_inputs import Step2ASectionInput, TableContinuationRequestInput\n"
            "from .versions import (\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,\n"
            "    LEGACY_STEP2A_MODEL_INPUT_VERSION,\n"
            "    LEGACY_STEP2A_REQUEST_VERSION,\n"
            "    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION,\n"
            "    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,\n"
            "    PREVIOUS_STEP2A_REQUEST_VERSION,\n"
            "    REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION,\n"
            "    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,\n"
            "    STEP2A_MODEL_INPUT_VERSION,\n"
            "    STEP2A_REQUEST_VERSION,\n"
            "    STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_MODEL_INPUT_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_MODEL_INPUT_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_MODEL_INPUT_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,\n"
            "    _MULTIPAGE_STEP2A_REQUEST_VERSIONS,\n"
            "    _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS,\n"
            ")\n"
        ),
        "symbols": ["Step2ARequest", "_step2a_payload"],
    },
    "step2a_builders.py": {
        "doc": "Builders assembling Step 2A sections/bindings/requests from Evidence units.",
        "header": (
            "from collections.abc import Iterable, Mapping\n"
            "from pathlib import Path\n"
            "\n"
            "from ...evidence.schema import EvidenceKind, EvidenceUnit\n"
            "from ..continuation.review import (\n"
            "    ContinuationBindingReviewSidecar,\n"
            "    load_continuation_review_sidecar,\n"
            "    sha256_file,\n"
            "    verify_continuation_review_source_pdf,\n"
            ")\n"
            "\n"
            "from .anchors import _TABLE_KINDS, EvidenceAnchorSpan\n"
            "from .continuation import (\n"
            "    ContinuationBinding,\n"
            "    ContinuationBindingFailureCode,\n"
            "    ContinuationBindingState,\n"
            "    build_continuation_binding,\n"
            "    build_reviewed_continuation_bindings,\n"
            ")\n"
            "from .enums import Step2DecoderConfig, Step2Stage\n"
            "from .hashing import _OFFLINE_RELAY_BASE_URL, _canonical_sha256\n"
            "from .obligations import derive_step2a_coverage_obligation_input\n"
            "from .projection import ReviewedStructuralDiagramInput, Step2ASemanticProjection\n"
            "from .request_helpers import derive_step2a_coverage_obligation_context_input\n"
            "from .request_identity import Step2RequestIdentity, make_step2_request_identity\n"
            "from .responses import step2a_result_payload_model\n"
            "from .step2a_request import Step2ARequest, _step2a_payload\n"
            "from .table_inputs import (\n"
            "    SectionTextInput,\n"
            "    Step2ASectionInput,\n"
            "    StructuredTableInput,\n"
            "    TableCellInput,\n"
            "    TableContinuationRequestInput,\n"
            "    TableRowInput,\n"
            "    _make_section_id,\n"
            "    derive_table_atomic_subfact_requirements,\n"
            ")\n"
            "from .versions import (\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,\n"
            "    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION,\n"
            "    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,\n"
            "    REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION,\n"
            "    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,\n"
            "    STEP2A_MODEL_INPUT_VERSION,\n"
            "    STEP2A_REQUEST_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_MODEL_INPUT_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_MODEL_INPUT_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_MODEL_INPUT_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_MODEL_INPUT_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,\n"
            ")\n"
        ),
        "symbols": [
            "build_step2a_section",
            "_default_manual_continuation_bindings",
            "build_step2a_request",
        ],
    },
    "derivation.py": {
        "doc": "Local deterministic derivations: span selection, table cells, condition/numeric claims.",
        "header": (
            "import hashlib\n"
            "import re\n"
            "from collections.abc import Iterable, Mapping\n"
            "from dataclasses import dataclass\n"
            "from typing import TYPE_CHECKING\n"
            "\n"
            "from ...evidence.layout import normalized_compare_text\n"
            "from ..schema import FactType\n"
            "\n"
            "from .audits import (\n"
            "    AnchorSpanSelectionDerivationState,\n"
            "    ConditionClaimDerivationState,\n"
            "    NumericClaimDerivationState,\n"
            "    Step2AAnchorSpanDerivationResult,\n"
            "    Step2AAnchorSpanSelectionAudit,\n"
            "    Step2ACompleteTableStructuralProjectionAudit,\n"
            "    Step2AConditionClaimAudit,\n"
            "    Step2AConditionClaimDerivationResult,\n"
            "    Step2ANumericClaimAudit,\n"
            "    Step2ANumericClaimDerivationResult,\n"
            "    Step2ATableCellAnchorAudit,\n"
            "    Step2ATableCellDerivationResult,\n"
            "    Step2ATableNumericOccurrenceAudit,\n"
            "    Step2ATableStructuralIndexOccurrenceAudit,\n"
            "    TableCellAnchorDerivationState,\n"
            ")\n"
            "from .claims import GroundedConditionClaim, GroundedNumericClaim\n"
            "from .enums import (\n"
            "    ConditionOperator,\n"
            "    NumericTokenClass,\n"
            "    _condition_operator_occurrences,\n"
            "    condition_operators_in_text,\n"
            ")\n"
            "from .numeric_tokens import (\n"
            "    NumericTokenClassificationRecord,\n"
            "    _TABLE_GRID_DIMENSION_PATTERNS,\n"
            "    classify_numeric_tokens,\n"
            ")\n"
            "from .occurrences import (\n"
            "    CompleteTableStructuralProjection,\n"
            "    CompleteTableStructuralProjectionState,\n"
            "    ConditionSpanReference,\n"
            "    NumericSpanReference,\n"
            "    TableGridMetadataReference,\n"
            "    TableNumericOccurrenceReference,\n"
            "    TableNumericOccurrenceState,\n"
            "    TableStructuralCellOccurrence,\n"
            "    TableStructuralCellRole,\n"
            "    TableStructuralIndexOccurrenceReference,\n"
            "    TableStructuralIndexOccurrenceState,\n"
            "    make_complete_table_structural_projection_id,\n"
            "    make_table_grid_metadata_id,\n"
            "    make_table_numeric_occurrence_id,\n"
            "    make_table_structural_cell_occurrence_id,\n"
            "    make_table_structural_index_occurrence_id,\n"
            ")\n"
            "from .proposals import (\n"
            "    Step2AFactProposal,\n"
            "    Step2AModelFactProposal,\n"
            "    Step2AV13ModelFactProposal,\n"
            "    Step2AV14ModelFactProposal,\n"
            "    Step2AV15ModelFactProposal,\n"
            "    Step2AV16ModelFactProposal,\n"
            "    Step2AV17ModelFactProposal,\n"
            "    Step2AV18ModelFactProposal,\n"
            "    Step2AV19ModelFactProposal,\n"
            "    Step2AV20ModelFactProposal,\n"
            "    Step2AV21ModelFactProposal,\n"
            "    Step2AV22ModelFactProposal,\n"
            "    Step2AV23ModelFactProposal,\n"
            ")\n"
            "from .request_helpers import (\n"
            "    _derive_v13_completeness_mode,\n"
            "    _derive_v13_source_section_ids,\n"
            "    _ordered_parent_evidence_ids,\n"
            "    _request_structured_tables,\n"
            "    _step2a_anchor_sources,\n"
            ")\n"
            "from .table_inputs import CompleteTableSemanticCoverage, _geometry_header_cell_ids\n"
            "from .versions import (\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,\n"
            "    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,\n"
            "    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,\n"
            "    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,\n"
            ")\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from .step2a_request import Step2ARequest\n"
        ),
        "symbols": [
            "derive_step2a_anchor_span_selection",
            "derive_step2a_table_cell_anchors",
            "_CONDITION_SPAN_BOUNDARIES",
            "_ConditionSpanCandidate",
            "_condition_span_reference",
            "_complete_condition_span",
            "_exact_common_condition_span",
            "derive_step2a_condition_claims",
            "_NumericSpanCandidate",
            "_TableNumericOccurrenceCandidate",
            "_TableStructuralIndexOccurrenceCandidate",
            "_numeric_span_reference",
            "_table_numeric_occurrence_sources",
            "_table_grid_metadata_token_bindings",
            "_STRUCTURAL_INDEX_COUNT_UNIT_PATTERN",
            "_assertion_token_is_quantity_count",
            "derive_step2a_numeric_claims",
        ],
    },
    "validation.py": {
        "doc": "Contract validation for Step 2A/2B responses (fail-closed report).",
        "header": (
            "from collections.abc import Iterable\n"
            "\n"
            "from ...evidence.layout import normalized_compare_text\n"
            "from ...materials.schema import StrictModel\n"
            "\n"
            "from .audits import (\n"
            "    ConditionClaimDerivationState,\n"
            "    ContinuationAllItemsAuditState,\n"
            "    CoverageObligationCoverageState,\n"
            "    NumericClaimDerivationState,\n"
            "    Step2ACompleteTableStructuralProjectionAudit,\n"
            "    Step2AConditionClaimAudit,\n"
            "    Step2AContinuationAudit,\n"
            "    Step2ACoverageObligationAudit,\n"
            "    Step2ANumericClaimAudit,\n"
            "    Step2ATableAtomicSubfactAudit,\n"
            "    Step2ATableNumericOccurrenceAudit,\n"
            "    Step2ATableSemanticCoverageAudit,\n"
            "    Step2ATableStructuralIndexOccurrenceAudit,\n"
            "    TableAtomicSubfactCoverageState,\n"
            "    TableSemanticCoverageState,\n"
            "    assess_step2a_continuations,\n"
            "    assess_step2a_coverage_obligations,\n"
            "    assess_step2a_table_atomic_subfacts,\n"
            "    assess_step2a_table_semantic_coverage,\n"
            ")\n"
            "from .claims import GroundedNumericClaim, ProposalNumericTokenAudit\n"
            "from .derivation import derive_step2a_condition_claims, derive_step2a_numeric_claims\n"
            "from .enums import CardPresentation, NumericTokenClass, Step2ResultState, Step2Stage\n"
            "from .numeric_tokens import classify_numeric_tokens\n"
            "from .occurrences import (\n"
            "    CompleteTableStructuralProjectionState,\n"
            "    TableNumericOccurrenceState,\n"
            "    TableStructuralIndexOccurrenceState,\n"
            ")\n"
            "from .request_helpers import _ordered_parent_evidence_ids, _step2a_anchor_sources\n"
            "from .responses import Step2AResponse\n"
            "from .step2a_request import Step2ARequest\n"
            "from .step2b import Step2BRequest, Step2BResponse\n"
            "from .versions import (\n"
            "    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,\n"
            "    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,\n"
            "    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,\n"
            "    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,\n"
            ")\n"
        ),
        "symbols": [
            "ContractValidationReport",
            "_claim_values",
            "validate_step2a_response",
            "validate_step2b_response",
        ],
    },
    "testing.py": {
        "doc": "Fixture-only deterministic fake LLM (no network path).",
        "header": (
            "from collections.abc import Mapping\n"
            "\n"
            "from .enums import Step2FailureState, Step2ResultState, Step2Stage\n"
            "from .request_identity import Step2RequestIdentity\n"
            "from .responses import Step2AFailure, Step2AResponse\n"
            "from .step2a_request import Step2ARequest\n"
            "from .step2b import Step2BRequest, Step2BResponse\n"
        ),
        "symbols": ["DeterministicFakeLLM"],
    },
}

# audits.py contains a placeholder sentinel entry that must be removed before use.
MODULES["audits.py"]["symbols"] = [  # type: ignore[index]
    name
    for name in MODULES["audits.py"]["symbols"]  # type: ignore[index]
    if name != "TableStructuralCellRole_UNUSED_SENTINEL"
]


def collect_segments(source: str) -> dict[str, str]:
    """Return top-level symbol name -> exact source segment (verbatim slice)."""
    tree = ast.parse(source)
    lines = source.split("\n")
    entries: list[tuple[str, int]] = []  # (name, start_line 1-based incl. decorators)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr):  # module docstring
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            if node.decorator_list:
                start = min(start, min(d.lineno for d in node.decorator_list))
            entries.append((node.name, start))
        elif isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise SystemExit(f"unsupported top-level assign at line {node.lineno}")
            entries.append((node.targets[0].id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            entries.append((node.target.id, node.lineno))
        else:
            raise SystemExit(f"unsupported top-level node {type(node).__name__} at line {node.lineno}")

    segments: dict[str, str] = {}
    for index, (name, start) in enumerate(entries):
        end = entries[index + 1][1] - 1 if index + 1 < len(entries) else len(lines)
        chunk = lines[start - 1 : end]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        if name in segments:
            raise SystemExit(f"duplicate top-level symbol {name}")
        segments[name] = "\n".join(chunk)
    return segments


def assemble(module_doc: str, header: str, bodies: list[str]) -> str:
    doc = f'"""{module_doc}\n"""\n' if "\n" in module_doc else f'"""{module_doc}"""\n'
    parts = [doc, "from __future__ import annotations\n"]
    if header:
        parts.append(header)
    prologue = "\n".join(part.rstrip("\n") for part in parts)
    return prologue + "\n\n\n" + "\n\n\n".join(bodies) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-file", type=Path, default=DEFAULT_OLD_FILE)
    parser.add_argument("--out-dir", type=Path, default=CONTRACTS_DIR)
    args = parser.parse_args()

    raw = args.old_file.read_bytes()
    print(f"legacy file: {args.old_file}")
    print(f"legacy sha256: {hashlib.sha256(raw).hexdigest()}  bytes: {len(raw)}")
    source = raw.decode("utf-8")

    segments = collect_segments(source)
    planned: set[str] = set()
    for spec in MODULES.values():
        planned.update(spec["symbols"])  # type: ignore[arg-type]

    missing = sorted(planned - set(segments))
    unassigned = sorted(set(segments) - planned)
    if missing:
        raise SystemExit(f"planned symbols missing from legacy file: {missing}")
    if unassigned:
        raise SystemExit(f"legacy symbols not assigned to any module: {unassigned}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for filename, spec in MODULES.items():
        bodies = [segments[name] for name in spec["symbols"]]  # type: ignore[index]
        content = assemble(spec["doc"], spec["header"], bodies)  # type: ignore[arg-type]
        path = args.out_dir / filename
        path.write_bytes(content.encode("utf-8"))
        total += len(spec["symbols"])  # type: ignore[arg-type]
        print(f"wrote {path.name}: {len(spec['symbols'])} symbols, {len(content)} chars")  # type: ignore[arg-type]
    print(f"total symbols distributed: {total} / {len(segments)}")


if __name__ == "__main__":
    main()
