"""Step 2A model fact proposals (V8-V23) and the canonical derived proposal."""
from __future__ import annotations
from collections.abc import Iterable

from pydantic import Field, model_validator

from ...evidence.layout import normalized_compare_text
from ...materials.schema import StrictModel
from ..schema import CompletenessMode, FactType

from .claims import GroundedConditionClaim, GroundedNumericClaim, ModelNumericClaim
from .hashing import _canonical_sha256
from .table_inputs import CompleteTableSemanticCoverage
from .versions import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
    PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
)


class Step2AModelFactProposal(StrictModel):
    """Strict Step 2A model-output shape before local condition derivation.

    The model never owns condition claims. Keeping this type separate from the
    canonical Fact proposal makes ``condition_claims`` an unknown field at the
    relay boundary while still allowing the local contract to persist derived
    claims as part of the audited Fact identity.
    """

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    source_section_ids: tuple[str, ...]
    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    completeness_mode: CompletenessMode = CompletenessMode.ATOMIC
    primary_anchor_span_ids: tuple[str, ...]
    secondary_anchor_span_ids: tuple[str, ...] = ()
    numeric_claims: tuple[ModelNumericClaim, ...] = ()

    @model_validator(mode="after")
    def validate_proposal_shape(self) -> "Step2AModelFactProposal":
        if not self.source_section_ids or not self.primary_anchor_span_ids:
            raise ValueError("Fact proposals require source sections and primary anchor spans")
        all_ids = [*self.primary_anchor_span_ids, *self.secondary_anchor_span_ids]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Fact proposal anchor spans must be unique across primary and secondary roles")
        return self


def make_step2a_v13_proposal_key(
    *,
    fact_type: FactType,
    assertion: str,
    primary_anchor_span_ids: Iterable[str],
    secondary_anchor_span_ids: Iterable[str] = (),
) -> str:
    """Derive model-independent proposal identity from semantic content and selected spans."""

    digest = _canonical_sha256(
        {
            "contract": MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
            "fact_type": fact_type.value,
            "normalized_assertion": normalized_compare_text(assertion),
            "primary_anchor_span_ids": sorted(primary_anchor_span_ids),
            "secondary_anchor_span_ids": sorted(secondary_anchor_span_ids),
        }
    )
    return f"proposal:{digest[:24]}"


def make_step2a_v14_proposal_key(
    *,
    fact_type: FactType,
    assertion: str,
    primary_anchor_span_ids: Iterable[str],
    secondary_anchor_span_ids: Iterable[str] = (),
) -> str:
    """Derive V14 proposal identity without reinterpreting historical V13 identities."""

    digest = _canonical_sha256(
        {
            "contract": NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
            "fact_type": fact_type.value,
            "normalized_assertion": normalized_compare_text(assertion),
            "primary_anchor_span_ids": sorted(primary_anchor_span_ids),
            "secondary_anchor_span_ids": sorted(secondary_anchor_span_ids),
        }
    )
    return f"proposal:{digest[:24]}"


def make_step2a_v15_proposal_key(
    *,
    fact_type: FactType,
    assertion: str,
    primary_anchor_span_ids: Iterable[str],
) -> str:
    """Derive V15 identity from the only model-selected anchor role."""

    digest = _canonical_sha256(
        {
            "contract": SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
            "fact_type": fact_type.value,
            "normalized_assertion": normalized_compare_text(assertion),
            "primary_anchor_span_ids": sorted(primary_anchor_span_ids),
        }
    )
    return f"proposal:{digest[:24]}"


def make_step2a_v16_proposal_key(
    *,
    fact_type: FactType,
    assertion: str,
    primary_anchor_span_ids: Iterable[str],
) -> str:
    """Derive V16 identity without carrying model-owned numeric metadata."""

    digest = _canonical_sha256(
        {
            "contract": DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
            "fact_type": fact_type.value,
            "normalized_assertion": normalized_compare_text(assertion),
            "primary_anchor_span_ids": sorted(primary_anchor_span_ids),
        }
    )
    return f"proposal:{digest[:24]}"


def make_step2a_v17_proposal_key(
    *,
    fact_type: FactType,
    assertion: str,
    primary_anchor_span_ids: Iterable[str],
) -> str:
    """Derive V17 identity without sharing V16 proposal provenance."""

    digest = _canonical_sha256(
        {
            "contract": TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
            "fact_type": fact_type.value,
            "normalized_assertion": normalized_compare_text(assertion),
            "primary_anchor_span_ids": sorted(primary_anchor_span_ids),
        }
    )
    return f"proposal:{digest[:24]}"


def make_step2a_v18_proposal_key(
    *,
    fact_type: FactType,
    assertion: str,
    primary_anchor_span_ids: Iterable[str],
) -> str:
    """Derive V18 identity without sharing V17 proposal provenance."""

    digest = _canonical_sha256(
        {
            "contract": TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
            "fact_type": fact_type.value,
            "normalized_assertion": normalized_compare_text(assertion),
            "primary_anchor_span_ids": sorted(primary_anchor_span_ids),
        }
    )
    return f"proposal:{digest[:24]}"


class Step2AV13ModelFactProposal(StrictModel):
    """Minimal V13 model-owned semantics; all audit metadata is locally derived."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]
    secondary_anchor_span_ids: tuple[str, ...] = ()
    numeric_claims: tuple[ModelNumericClaim, ...] = ()

    @model_validator(mode="after")
    def validate_minimal_proposal(self) -> "Step2AV13ModelFactProposal":
        all_ids = [*self.primary_anchor_span_ids, *self.secondary_anchor_span_ids]
        if not self.primary_anchor_span_ids:
            raise ValueError("V13 Fact proposals require primary anchor spans")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("V13 anchor spans must be unique across primary and secondary roles")
        return self

    @property
    def derived_proposal_key(self) -> str:
        return make_step2a_v13_proposal_key(
            fact_type=self.fact_type,
            assertion=self.assertion,
            primary_anchor_span_ids=self.primary_anchor_span_ids,
            secondary_anchor_span_ids=self.secondary_anchor_span_ids,
        )


class Step2AV14ModelFactProposal(StrictModel):
    """V14 minimal model semantics with expression-level numeric claim grounding."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]
    secondary_anchor_span_ids: tuple[str, ...] = ()
    numeric_claims: tuple[ModelNumericClaim, ...] = ()

    @model_validator(mode="after")
    def validate_minimal_proposal(self) -> "Step2AV14ModelFactProposal":
        all_ids = [*self.primary_anchor_span_ids, *self.secondary_anchor_span_ids]
        if not self.primary_anchor_span_ids:
            raise ValueError("V14 Fact proposals require primary anchor spans")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("V14 anchor spans must be unique across primary and secondary roles")
        return self

    @property
    def derived_proposal_key(self) -> str:
        return make_step2a_v14_proposal_key(
            fact_type=self.fact_type,
            assertion=self.assertion,
            primary_anchor_span_ids=self.primary_anchor_span_ids,
            secondary_anchor_span_ids=self.secondary_anchor_span_ids,
        )


class Step2AV15ModelFactProposal(StrictModel):
    """V15 model semantics with one exhaustive selected-anchor role."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]
    numeric_claims: tuple[ModelNumericClaim, ...] = ()

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV15ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V15 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V15 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        return make_step2a_v15_proposal_key(
            fact_type=self.fact_type,
            assertion=self.assertion,
            primary_anchor_span_ids=self.primary_anchor_span_ids,
        )


class Step2AV16ModelFactProposal(StrictModel):
    """V16 model semantics with no model-owned numeric claim selection."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV16ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V16 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V16 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        return make_step2a_v16_proposal_key(
            fact_type=self.fact_type,
            assertion=self.assertion,
            primary_anchor_span_ids=self.primary_anchor_span_ids,
        )


class Step2AV17ModelFactProposal(StrictModel):
    """V17 keeps the V16 minimal model shape but has distinct provenance."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV17ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V17 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V17 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        return make_step2a_v17_proposal_key(
            fact_type=self.fact_type,
            assertion=self.assertion,
            primary_anchor_span_ids=self.primary_anchor_span_ids,
        )


class Step2AV18ModelFactProposal(StrictModel):
    """V18 retains the minimal V17 response shape with coverage-obligation provenance."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV18ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V18 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V18 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        return make_step2a_v18_proposal_key(
            fact_type=self.fact_type,
            assertion=self.assertion,
            primary_anchor_span_ids=self.primary_anchor_span_ids,
        )


class Step2AV19ModelFactProposal(StrictModel):
    """V19 retains V18's minimal response shape with source-derived obligation context."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV19ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V19 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V19 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        digest = _canonical_sha256(
            {
                "contract": TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
                "fact_type": self.fact_type.value,
                "normalized_assertion": normalized_compare_text(self.assertion),
                "primary_anchor_span_ids": sorted(self.primary_anchor_span_ids),
            }
        )
        return f"proposal:{digest[:24]}"


class Step2AV20ModelFactProposal(StrictModel):
    """V20 keeps V19's minimal shape with stiffness-row threshold obligations."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV20ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V20 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V20 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        digest = _canonical_sha256(
            {
                "contract": TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                "fact_type": self.fact_type.value,
                "normalized_assertion": normalized_compare_text(self.assertion),
                "primary_anchor_span_ids": sorted(self.primary_anchor_span_ids),
            }
        )
        return f"proposal:{digest[:24]}"


class Step2AV21ModelFactProposal(StrictModel):
    """V21 keeps the four-field model shape; occurrence grounding is local."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV21ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V21 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V21 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        digest = _canonical_sha256(
            {
                "contract": COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                "fact_type": self.fact_type.value,
                "normalized_assertion": normalized_compare_text(self.assertion),
                "primary_anchor_span_ids": sorted(self.primary_anchor_span_ids),
            }
        )
        return f"proposal:{digest[:24]}"


class Step2AV22ModelFactProposal(StrictModel):
    """V22 keeps the four-field shape; structural indexes are derived locally."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV22ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V22 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V22 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        digest = _canonical_sha256(
            {
                "contract": STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                "fact_type": self.fact_type.value,
                "normalized_assertion": normalized_compare_text(self.assertion),
                "primary_anchor_span_ids": sorted(self.primary_anchor_span_ids),
            }
        )
        return f"proposal:{digest[:24]}"


class Step2AV23ModelFactProposal(StrictModel):
    """V23 keeps the four-field model shape; table structure is source-owned."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AV23ModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("V23 Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V23 primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        digest = _canonical_sha256(
            {
                "contract": SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
                "fact_type": self.fact_type.value,
                "normalized_assertion": normalized_compare_text(self.assertion),
                "primary_anchor_span_ids": sorted(self.primary_anchor_span_ids),
            }
        )
        return f"proposal:{digest[:24]}"


class Step2AProductionSourceOwnedArbitraryPDFModelFactProposal(StrictModel):
    """Production arbitrary-PDF proposal with source-owned derived metadata."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    primary_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_single_role_proposal(self) -> "Step2AProductionSourceOwnedArbitraryPDFModelFactProposal":
        if not self.primary_anchor_span_ids:
            raise ValueError("production source-owned Fact proposals require primary anchor spans")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("production source-owned primary anchor spans must be unique")
        return self

    @property
    def derived_proposal_key(self) -> str:
        digest = _canonical_sha256(
            {
                "contract": PRODUCTION_SOURCE_OWNED_ARBITRARY_PDF_STEP2A_REQUEST_VERSION,
                "fact_type": self.fact_type.value,
                "normalized_assertion": normalized_compare_text(self.assertion),
                "primary_anchor_span_ids": sorted(self.primary_anchor_span_ids),
            }
        )
        return f"proposal:{digest[:24]}"


class Step2AFactProposal(StrictModel):
    """Canonical Step 2A proposal after local span-to-Evidence derivation."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    source_section_ids: tuple[str, ...]
    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2)
    completeness_mode: CompletenessMode = CompletenessMode.ATOMIC
    primary_anchor_span_ids: tuple[str, ...]
    secondary_anchor_span_ids: tuple[str, ...] = ()
    primary_evidence_ids: tuple[str, ...]
    secondary_evidence_ids: tuple[str, ...] = ()
    table_cell_anchor_span_ids: tuple[str, ...] = ()
    table_cell_evidence_ids: tuple[str, ...] = ()
    selected_table_evidence_ids: tuple[str, ...] = ()
    complete_table_semantic_coverages: tuple[CompleteTableSemanticCoverage, ...] = ()
    numeric_claims: tuple[GroundedNumericClaim, ...] = ()
    condition_claims: tuple[GroundedConditionClaim, ...] = ()

    @model_validator(mode="after")
    def validate_canonical_proposal(self) -> "Step2AFactProposal":
        span_ids = [*self.primary_anchor_span_ids, *self.secondary_anchor_span_ids]
        evidence_ids = [*self.primary_evidence_ids, *self.secondary_evidence_ids]
        if not self.source_section_ids or not self.primary_anchor_span_ids or not self.primary_evidence_ids:
            raise ValueError("canonical Fact proposals require source sections, primary spans, and primary Evidence")
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("canonical Fact anchor spans must be unique across primary and secondary roles")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("canonical Fact Evidence IDs must be unique across primary and secondary roles")
        if len(self.table_cell_anchor_span_ids) != len(set(self.table_cell_anchor_span_ids)):
            raise ValueError("canonical table-cell anchor span IDs must be unique")
        if len(self.table_cell_evidence_ids) != len(set(self.table_cell_evidence_ids)):
            raise ValueError("canonical table-cell Evidence IDs must be unique")
        if len(self.selected_table_evidence_ids) != len(set(self.selected_table_evidence_ids)):
            raise ValueError("canonical selected table Evidence IDs must be unique")
        if not set(self.table_cell_anchor_span_ids) <= set(span_ids):
            raise ValueError("canonical table-cell anchor spans must be Fact-selected")
        if not set(self.table_cell_evidence_ids) <= set(evidence_ids):
            raise ValueError("canonical table-cell Evidence must be Fact-selected")
        coverage_table_ids = [coverage.table_evidence_id for coverage in self.complete_table_semantic_coverages]
        if len(coverage_table_ids) != len(set(coverage_table_ids)):
            raise ValueError("canonical complete-table semantic coverages must be unique by table")
        if self.complete_table_semantic_coverages and self.fact_type != FactType.COMPOSITION:
            raise ValueError("only composition Facts may claim complete-table semantic coverage")
        if not set(coverage_table_ids) <= set(self.selected_table_evidence_ids):
            raise ValueError("complete-table semantic coverages must bind selected source tables")
        for coverage in self.complete_table_semantic_coverages:
            if not set(coverage.cell_evidence_ids) <= set(self.table_cell_evidence_ids):
                raise ValueError("complete-table semantic coverage cells must be Fact-selected")
            if not set(coverage.cell_anchor_span_ids) <= set(self.table_cell_anchor_span_ids):
                raise ValueError("complete-table semantic coverage anchor spans must be Fact-selected")
        if any(claim.anchor_span_id not in span_ids for claim in self.numeric_claims):
            raise ValueError("numeric claims must bind Fact-selected anchor spans")
        if any(claim.anchor_span_id not in span_ids for claim in self.condition_claims):
            raise ValueError("condition claims must bind Fact-selected anchor spans")
        return self
