"""Local deterministic derivations: span selection, table cells, condition/numeric claims."""
from __future__ import annotations

from .derivation_anchor_span import (
    _MINIMAL_MODEL_PROPOSAL_TYPES,
    _DUAL_ROLE_ANCHOR_PROPOSAL_TYPES,
    _MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES,
    _MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION,
    derive_step2a_anchor_span_selection,
)
from .derivation_table_cell import (
    derive_step2a_table_cell_anchors,
)
from .derivation_condition import (
    _CONDITION_SPAN_BOUNDARIES,
    _ConditionSpanCandidate,
    _condition_span_reference,
    _complete_condition_span,
    _exact_common_condition_span,
    derive_step2a_condition_claims,
)
from .derivation_numeric import (
    _NumericSpanCandidate,
    _TableNumericOccurrenceCandidate,
    _TableStructuralIndexOccurrenceCandidate,
    _TableStructuralCellOccurrenceKwargs,
    _TableNumericOccurrenceKwargs,
    _numeric_span_reference,
    _table_numeric_occurrence_sources,
    _table_grid_metadata_token_bindings,
    _STRUCTURAL_INDEX_COUNT_UNIT_PATTERN,
    _assertion_token_is_quantity_count,
    derive_step2a_numeric_claims,
)

__all__ = [
    "_CONDITION_SPAN_BOUNDARIES",
    "_ConditionSpanCandidate",
    "_DUAL_ROLE_ANCHOR_PROPOSAL_TYPES",
    "_MINIMAL_MODEL_PROPOSAL_TYPES",
    "_MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION",
    "_MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES",
    "_NumericSpanCandidate",
    "_STRUCTURAL_INDEX_COUNT_UNIT_PATTERN",
    "_TableNumericOccurrenceCandidate",
    "_TableNumericOccurrenceKwargs",
    "_TableStructuralCellOccurrenceKwargs",
    "_TableStructuralIndexOccurrenceCandidate",
    "_assertion_token_is_quantity_count",
    "_complete_condition_span",
    "_condition_span_reference",
    "_exact_common_condition_span",
    "_numeric_span_reference",
    "_table_grid_metadata_token_bindings",
    "_table_numeric_occurrence_sources",
    "derive_step2a_anchor_span_selection",
    "derive_step2a_condition_claims",
    "derive_step2a_numeric_claims",
    "derive_step2a_table_cell_anchors",
]
