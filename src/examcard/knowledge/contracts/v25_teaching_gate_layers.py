"""Separate non-blocking model-label forensics from canonical teaching release gates."""
from __future__ import annotations

from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from .phase2r142_v25_projection import V25GoldenTargetState, V25ResponseGoldenProjectionBundle


class V25ModelLabelForensicRecord(StrictModel):
    """A model-label quality outcome retained for audit, never release eligibility."""

    proposal_index: int = Field(ge=0)
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_label_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern=r"^(pass|fail|manual_review)$")
    reason: str = Field(min_length=1, max_length=320)


class V25ModelLabelForensicAudit(StrictModel):
    """Safe model-label audit; failures are diagnostics rather than hard blockers."""

    records: tuple[V25ModelLabelForensicRecord, ...]
    pass_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    basis: str = Field(min_length=48, max_length=1200)

    @model_validator(mode="after")
    def validate_counts(self) -> "V25ModelLabelForensicAudit":
        if len(self.records) != self.pass_count + self.failure_count + self.manual_review_count:
            raise ValueError("V25 model-label forensic counts must cover every proposal exactly once")
        if len({item.proposal_index for item in self.records}) != len(self.records):
            raise ValueError("V25 model-label forensic records must be unique per proposal")
        return self


class V25CanonicalTeachingGate(StrictModel):
    """The only teaching release gate for projected V25 Facts."""

    valid: bool
    expected_target_count: int = Field(ge=0)
    projected_target_count: int = Field(ge=0)
    eligible_fact_keys: tuple[str, ...]
    non_projected_fact_keys: tuple[str, ...]
    canonical_title_failure_count: int = Field(ge=0)
    canonical_title_manual_review_count: int = Field(ge=0)
    source_gate_valid: bool
    continuation_binding_valid: bool
    exactly_once_bridge_eligible: bool
    basis: str = Field(min_length=64, max_length=1600)

    @model_validator(mode="after")
    def validate_gate(self) -> "V25CanonicalTeachingGate":
        expected = (
            self.projected_target_count == self.expected_target_count
            and not self.non_projected_fact_keys
            and self.canonical_title_failure_count == 0
            and self.canonical_title_manual_review_count == 0
            and self.source_gate_valid
            and self.continuation_binding_valid
            and self.exactly_once_bridge_eligible
        )
        if self.valid != expected:
            raise ValueError("V25 canonical teaching gate must fail closed on any non-projected or non-canonical Fact")
        if len(self.eligible_fact_keys) != len(set(self.eligible_fact_keys)):
            raise ValueError("V25 canonical teaching gate Fact keys must be unique")
        return self


def assess_v25_teaching_gate_layers(
    bundle: V25ResponseGoldenProjectionBundle,
) -> tuple[V25ModelLabelForensicAudit, V25CanonicalTeachingGate]:
    """Return source-safe label diagnostics and a canonical release decision.

    The bundle already proves type, assertion, Evidence, anchors, table and
    continuation bindings. Only its exact projected canonical titles can enter
    the downstream Step 2B bridge.
    """

    proposal_audits = {item.proposal_index: item for item in bundle.proposal_audits}
    results = bundle.teaching_title_audit.model_label_results
    if set(proposal_audits) != set(range(len(results))):
        raise ValueError("V25 model-label audit must align one-to-one with proposal forensic hashes")
    records = tuple(
        V25ModelLabelForensicRecord(
            proposal_index=index,
            proposal_sha256=proposal_audits[index].proposal_sha256,
            model_label_sha256=proposal_audits[index].model_label_sha256,
            status=result.status.value,
            reason=result.reason,
        )
        for index, result in enumerate(results)
    )
    label_audit = V25ModelLabelForensicAudit(
        records=records,
        pass_count=sum(item.status == "pass" for item in records),
        failure_count=sum(item.status == "fail" for item in records),
        manual_review_count=sum(item.status == "manual_review" for item in records),
        basis="Model labels are retained as proposal-hash forensic diagnostics. They cannot grant eligibility and their quality result cannot block an independently exact projected canonical Fact.",
    )
    expected = len(bundle.target_audits)
    projected = tuple(item for item in bundle.target_audits if item.state == V25GoldenTargetState.PROJECTED)
    non_projected = tuple(item.fact_key for item in bundle.target_audits if item.state != V25GoldenTargetState.PROJECTED)
    canonical = V25CanonicalTeachingGate(
        valid=(
            len(projected) == expected
            and not non_projected
            and bundle.teaching_title_audit.canonical_title_failure_count == 0
            and bundle.teaching_title_audit.canonical_title_manual_review_count == 0
            and bundle.source_gate.valid
            and bundle.continuation_binding_valid
            and bundle.step2b_bridge_audit.eligible
        ),
        expected_target_count=expected,
        projected_target_count=len(projected),
        eligible_fact_keys=tuple(item.fact_key for item in bundle.projections),
        non_projected_fact_keys=non_projected,
        canonical_title_failure_count=bundle.teaching_title_audit.canonical_title_failure_count,
        canonical_title_manual_review_count=bundle.teaching_title_audit.canonical_title_manual_review_count,
        source_gate_valid=bundle.source_gate.valid,
        continuation_binding_valid=bundle.continuation_binding_valid,
        exactly_once_bridge_eligible=bundle.step2b_bridge_audit.eligible,
        basis="Only exact projected canonical Facts with complete source/table/continuation proof and passing canonical Card-facing title quality can enter the exactly-once Step 2B bridge. Non-projected dispositions remain fail-closed.",
    )
    return label_audit, canonical
