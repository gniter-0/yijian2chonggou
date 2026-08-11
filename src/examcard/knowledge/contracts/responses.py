"""Step 2A responses and versioned relay-bound result payload shapes."""
from __future__ import annotations
from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from .enums import Step2FailureState, Step2ResultState, Step2Stage
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
from .request_identity import Step2RequestIdentity
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


class Step2AFailure(StrictModel):
    state: Step2FailureState
    message: str = Field(min_length=1)
    retryable: bool = False

    @model_validator(mode="after")
    def validate_failure(self) -> "Step2AFailure":
        if self.state == Step2FailureState.UNKNOWN_OUTCOME and self.retryable:
            raise ValueError("unknown Step 2 outcomes require manual resolution, not automatic retry")
        return self


class Step2AResponse(StrictModel):
    request: Step2RequestIdentity
    state: Step2ResultState
    proposals: tuple[Step2AFactProposal, ...] = ()
    failure: Step2AFailure | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> "Step2AResponse":
        proposal_keys = [proposal.proposal_key for proposal in self.proposals]
        if len(proposal_keys) != len(set(proposal_keys)):
            raise ValueError("Step 2A proposal keys must be unique")
        if self.request.stage != Step2Stage.FACT_PLANNING:
            raise ValueError("Step 2A responses must bind a Step 2A request")
        if self.state == Step2ResultState.SUCCEEDED:
            if not self.proposals or self.failure is not None:
                raise ValueError("successful Step 2A responses require proposals and no failure")
        elif self.proposals or self.failure is None:
            raise ValueError("failed Step 2A responses require exactly one failure and no proposals")
        return self


class Step2AResultPayload(StrictModel):
    """The only JSON shape a real Step 2A model may return."""

    proposals: tuple[Step2AModelFactProposal, ...] = Field(
        description=(
            "The required non-empty Step 2A Fact proposal array. This is the only permitted top-level field; "
            "legacy top-level fields such as sample_id, facts, and fact_proposals are forbidden."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AResultPayload":
        proposal_keys = [proposal.proposal_key for proposal in self.proposals]
        if not self.proposals or len(proposal_keys) != len(set(proposal_keys)):
            raise ValueError("Step 2A result payload requires unique proposals")
        return self


class Step2AV13ResultPayload(StrictModel):
    """The only JSON shape a V13 minimal Step 2A model may return."""

    proposals: tuple[Step2AV13ModelFactProposal, ...] = Field(
        description=(
            "Non-empty minimal proposal array. Model-owned fields are limited to semantic text, Fact type, "
            "primary/secondary anchor span selections, and numeric claims."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV13ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V13 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV14ResultPayload(StrictModel):
    """The only JSON shape a V14 expression-grounded Step 2A model may return."""

    proposals: tuple[Step2AV14ModelFactProposal, ...] = Field(
        description=(
            "Non-empty minimal proposal array. Numeric claims select complete quantitative expressions; "
            "all audit metadata remains locally derived."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV14ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V14 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV15ResultPayload(StrictModel):
    """The only JSON shape a V15 single-role Step 2A model may return."""

    proposals: tuple[Step2AV15ModelFactProposal, ...] = Field(
        description=(
            "Non-empty minimal proposal array. primary_anchor_span_ids is the complete model-selected set; "
            "secondary anchor and Evidence roles are not model-owned."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV15ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V15 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV16ResultPayload(StrictModel):
    """The only JSON shape a V16 deterministic-numeric Step 2A model may return."""

    proposals: tuple[Step2AV16ModelFactProposal, ...] = Field(
        description=(
            "Non-empty minimal proposal array. Numeric claims are locally derived from assertion text and selected "
            "anchor spans; the model cannot return numeric claim metadata."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV16ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V16 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV17ResultPayload(StrictModel):
    """The only JSON shape a V17 controlled-table Step 2A model may return."""

    proposals: tuple[Step2AV17ModelFactProposal, ...] = Field(
        description=(
            "Non-empty minimal proposal array. Parent Evidence, source sections, numeric claims, and controlled "
            "table provenance are derived locally from selected anchor spans."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV17ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V17 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV18ResultPayload(StrictModel):
    """The only JSON shape a V18 coverage-obligation Step 2A model may return."""

    proposals: tuple[Step2AV18ModelFactProposal, ...] = Field(
        description=(
            "Non-empty minimal proposal array. Coverage obligations are request input only; the model must not "
            "return obligation metadata or any additional field."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV18ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V18 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV19ResultPayload(StrictModel):
    """The only JSON shape a V19 source-context Step 2A model may return."""

    proposals: tuple[Step2AV19ModelFactProposal, ...] = Field(
        description=(
            "Non-empty minimal proposal array. Source-derived obligation context is request input only; the model "
            "must not return context or obligation metadata."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV19ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V19 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV20ResultPayload(StrictModel):
    """The only JSON shape a V20 stiffness-threshold Step 2A model may return."""

    proposals: tuple[Step2AV20ModelFactProposal, ...] = Field(
        description=(
            "Non-empty minimal proposal array. Source-derived stiffness coverage context is request input only; the "
            "model must not return context, obligation, or other metadata."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV20ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V20 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV21ResultPayload(StrictModel):
    """The only JSON shape a V21 occurrence-grounded Step 2A model may return."""

    proposals: tuple[Step2AV21ModelFactProposal, ...] = Field(
        description=(
            "Non-empty four-field proposal array. Table occurrences, grid metadata, numeric claims, coverage "
            "obligations, and all provenance are local-only and forbidden in the model response."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV21ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V21 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV22ResultPayload(StrictModel):
    """The only JSON shape a V22 structural-index Step 2A model may return."""

    proposals: tuple[Step2AV22ModelFactProposal, ...] = Field(
        description=(
            "Non-empty four-field proposal array. Structural-index occurrences, table numeric occurrences, grid metadata, "
            "numeric claims, coverage obligations, and all provenance are local-only and forbidden in the model response."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV22ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V22 Step 2A result payload requires unique locally-derived proposals")
        return self


class Step2AV23ResultPayload(StrictModel):
    """The only JSON shape a V23 source-owned-projection model may return."""

    proposals: tuple[Step2AV23ModelFactProposal, ...] = Field(
        description=(
            "Non-empty four-field proposal array. Complete-table structure, list-index occurrences, cell occurrences, "
            "grid metadata, numeric claims, coverage obligations, and all provenance are local-only and forbidden in the "
            "model response."
        )
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2AV23ResultPayload":
        keys = [proposal.derived_proposal_key for proposal in self.proposals]
        if not self.proposals or len(keys) != len(set(keys)):
            raise ValueError("V23 Step 2A result payload requires unique locally-derived proposals")
        return self


def step2a_result_payload_model(request_version: str):
    """Return the exact relay-bound payload model for one immutable request version."""

    if request_version == MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION:
        return Step2AV13ResultPayload
    if request_version == NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION:
        return Step2AV14ResultPayload
    if request_version == SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION:
        return Step2AV15ResultPayload
    if request_version == DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION:
        return Step2AV16ResultPayload
    if request_version == TABLE_CONTINUATION_STEP2A_REQUEST_VERSION:
        return Step2AV17ResultPayload
    if request_version == TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION:
        return Step2AV18ResultPayload
    if request_version == TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION:
        return Step2AV19ResultPayload
    if request_version == TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION:
        return Step2AV20ResultPayload
    if request_version == COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION:
        return Step2AV21ResultPayload
    if request_version == STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION:
        return Step2AV22ResultPayload
    if request_version == SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION:
        return Step2AV23ResultPayload
    return Step2AResultPayload
