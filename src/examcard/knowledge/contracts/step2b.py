"""Step 2B card-planning contracts: fact summaries, requests, responses."""
from __future__ import annotations
import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import Field, model_validator

from ...evidence.schema import EvidenceUnit
from ...materials.schema import StrictModel
from ..identity import make_fact_id
from ..schema import AtomicFact, CompletenessMode, FactType, ShortCardException

from .enums import CardPresentation, Step2DecoderConfig, Step2ResultState, Step2Stage
from .hashing import _OFFLINE_RELAY_BASE_URL, _canonical_sha256
from .proposals import Step2AFactProposal
from .request_identity import _SHA256_PATTERN, Step2RequestIdentity, make_step2_request_identity
from .responses import Step2AFailure
from .versions import (
    CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION,
    PREVIOUS_STEP2B_REQUEST_VERSION,
    STEP2B_REQUEST_VERSION,
)


class ContinuationTitleProjectionProvenance(StrictModel):
    """Immutable Phase 2R continuation-title audit carried into Step 2B."""

    projection_id: str = Field(pattern=r"^continuation-title-projection:[0-9a-f]{24}$")
    projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    fixture_id: str = Field(min_length=1)
    fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    fact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_request_id: str = Field(pattern=r"^step2a-request:[0-9a-f]{64}$")
    source_request_version: str = Field(min_length=1)
    source_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    model_label: str = Field(min_length=2, max_length=64)
    model_label_sha256: str = Field(pattern=_SHA256_PATTERN)
    canonical_title: str = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def validate_provenance(self) -> "ContinuationTitleProjectionProvenance":
        if self.model_label != self.model_label.strip() or self.canonical_title != self.canonical_title.strip():
            raise ValueError("continuation title provenance requires stripped titles")
        if self.model_label_sha256 != hashlib.sha256(self.model_label.encode("utf-8")).hexdigest():
            raise ValueError("continuation title provenance model label hash does not match")
        return self


class ReviewedCanonicalTitleProjection(StrictModel):
    """Reviewed title authority carried into the otherwise model-free Step 2B input."""

    fixture_id: str = Field(min_length=1)
    fact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    canonical_title: str = Field(min_length=2, max_length=64)
    source_label: str = Field(min_length=2, max_length=64)
    source_label_sha256: str = Field(pattern=_SHA256_PATTERN)
    continuation_title_projection: ContinuationTitleProjectionProvenance | None = None

    @model_validator(mode="after")
    def validate_projection(self) -> "ReviewedCanonicalTitleProjection":
        if self.canonical_title != self.canonical_title.strip() or self.source_label != self.source_label.strip():
            raise ValueError("reviewed canonical title projection requires stripped titles")
        if self.source_label_sha256 != hashlib.sha256(self.source_label.encode("utf-8")).hexdigest():
            raise ValueError("reviewed canonical title projection source hash does not match its label")
        if self.continuation_title_projection is not None:
            provenance = self.continuation_title_projection
            if (
                provenance.fixture_id != self.fixture_id
                or provenance.fact_key != self.fact_key
                or provenance.model_label != self.source_label
                or provenance.model_label_sha256 != self.source_label_sha256
                or provenance.canonical_title != self.canonical_title
            ):
                raise ValueError("reviewed canonical title projection does not match continuation provenance")
        return self


class FactSummaryInput(StrictModel):
    fact_id: str = Field(pattern=r"^fact:[0-9a-f]{24}$")
    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion_summary: str = Field(min_length=2, max_length=500)
    completeness_mode: CompletenessMode
    must_cover: bool
    selected_table_evidence_ids: tuple[str, ...] = ()
    complete_table_evidence_ids: tuple[str, ...] = ()
    reviewed_title_projection: ReviewedCanonicalTitleProjection | None = None
    source_proposal_key: str | None = Field(default=None, pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")

    @model_validator(mode="after")
    def validate_table_semantics(self) -> "FactSummaryInput":
        if len(self.selected_table_evidence_ids) != len(set(self.selected_table_evidence_ids)):
            raise ValueError("Fact summaries require unique selected table Evidence IDs")
        if len(self.complete_table_evidence_ids) != len(set(self.complete_table_evidence_ids)):
            raise ValueError("Fact summaries require unique complete-table Evidence IDs")
        if not set(self.complete_table_evidence_ids) <= set(self.selected_table_evidence_ids):
            raise ValueError("complete-table Facts must bind selected table Evidence IDs")
        if self.complete_table_evidence_ids and self.fact_type != FactType.COMPOSITION:
            raise ValueError("only composition Fact summaries may represent complete tables")
        if self.reviewed_title_projection is not None and self.label != self.reviewed_title_projection.canonical_title:
            raise ValueError("Step 2B-facing Fact title must use the reviewed canonical title")
        if self.reviewed_title_projection is not None and self.reviewed_title_projection.continuation_title_projection is not None:
            if self.source_proposal_key != self.reviewed_title_projection.continuation_title_projection.source_proposal_key:
                raise ValueError("continuation title projection must bind the Step 2B Fact source proposal exactly")
        return self


class Step2BCardProposal(StrictModel):
    proposal_key: str = Field(pattern=r"^card-proposal:[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=2, max_length=64)
    primary_fact_ids: tuple[str, ...]
    secondary_fact_ids: tuple[str, ...] = ()
    presentation: CardPresentation
    split_reason: str = Field(min_length=6, max_length=240)
    short_card_exception: ShortCardException | None = None

    @model_validator(mode="after")
    def validate_card_proposal(self) -> "Step2BCardProposal":
        if not self.primary_fact_ids:
            raise ValueError("Step 2B card proposals require primary Fact IDs")
        all_ids = [*self.primary_fact_ids, *self.secondary_fact_ids]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Step 2B card proposal Fact IDs must be unique across roles")
        return self


class Step2BResponse(StrictModel):
    request: Step2RequestIdentity
    state: Step2ResultState
    proposals: tuple[Step2BCardProposal, ...] = ()
    failure: Step2AFailure | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> "Step2BResponse":
        proposal_keys = [proposal.proposal_key for proposal in self.proposals]
        if len(proposal_keys) != len(set(proposal_keys)):
            raise ValueError("Step 2B proposal keys must be unique")
        if self.request.stage != Step2Stage.CARD_PLANNING:
            raise ValueError("Step 2B responses must bind a Step 2B request")
        if self.state == Step2ResultState.SUCCEEDED:
            if not self.proposals or self.failure is not None:
                raise ValueError("successful Step 2B responses require proposals and no failure")
        elif self.proposals or self.failure is None:
            raise ValueError("failed Step 2B responses require exactly one failure and no proposals")
        return self


class Step2BResultPayload(StrictModel):
    """The only JSON shape a real Step 2B model may return."""

    proposals: tuple[Step2BCardProposal, ...]

    @model_validator(mode="after")
    def validate_payload(self) -> "Step2BResultPayload":
        proposal_keys = [proposal.proposal_key for proposal in self.proposals]
        if not self.proposals or len(proposal_keys) != len(set(proposal_keys)):
            raise ValueError("Step 2B result payload requires unique proposals")
        return self


class Step2BRequest(StrictModel):
    identity: Step2RequestIdentity
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    material_profile_id: str = Field(min_length=1)
    facts: tuple[FactSummaryInput, ...]

    @model_validator(mode="after")
    def validate_request(self) -> "Step2BRequest":
        fact_ids = [fact.fact_id for fact in self.facts]
        if self.identity.stage != Step2Stage.CARD_PLANNING or self.identity.request_version not in {
            PREVIOUS_STEP2B_REQUEST_VERSION,
            STEP2B_REQUEST_VERSION,
            CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION,
        }:
            raise ValueError("Step 2B request uses an incompatible identity contract")
        if not self.facts or len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Step 2B requests require unique Fact summaries")
        uses_continuation_projection = self.identity.request_version == CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION
        if uses_continuation_projection:
            if any(fact.source_proposal_key is None for fact in self.facts):
                raise ValueError("V5 Step 2B requests require every Fact summary to bind its Step 2A proposal")
            if not any(
                fact.reviewed_title_projection is not None
                and fact.reviewed_title_projection.continuation_title_projection is not None
                for fact in self.facts
            ):
                raise ValueError("V5 Step 2B requests require one projected continuation canonical title")
        elif any(
            fact.reviewed_title_projection is not None
            and fact.reviewed_title_projection.continuation_title_projection is not None
            for fact in self.facts
        ):
            raise ValueError("continuation title provenance requires the V5 Step 2B request contract")
        payload = _step2b_payload(
            sample_id=self.sample_id,
            material_profile_id=self.material_profile_id,
            facts=self.facts,
            include_continuation_title_projection=uses_continuation_projection,
        )
        if self.identity.payload_sha256 != _canonical_sha256(payload):
            raise ValueError("Step 2B request payload no longer matches its cache identity")
        return self


def _step2b_payload(
    *,
    sample_id: str,
    material_profile_id: str,
    facts: Iterable[FactSummaryInput],
    include_continuation_title_projection: bool = False,
) -> dict[str, Any]:
    fact_payloads = [fact.model_dump(mode="json") for fact in facts]
    if not include_continuation_title_projection:
        for payload in fact_payloads:
            payload.pop("source_proposal_key", None)
            title_projection = payload.get("reviewed_title_projection")
            if title_projection is not None:
                title_projection.pop("continuation_title_projection", None)
    return {
        "sample_id": sample_id,
        "material_profile_id": material_profile_id,
        "facts": fact_payloads,
    }


def fact_summary_from_atomic_fact(fact: AtomicFact) -> FactSummaryInput:
    return FactSummaryInput(
        fact_id=fact.fact_id,
        fact_type=fact.fact_type,
        label=fact.label,
        assertion_summary=fact.assertion,
        completeness_mode=fact.completeness_mode,
        must_cover=fact.must_cover,
    )


def fact_summary_from_proposal(
    *,
    proposal: Step2AFactProposal,
    evidence_by_id: Mapping[str, EvidenceUnit],
    reviewed_title_projection: ReviewedCanonicalTitleProjection | None = None,
) -> FactSummaryInput:
    """Derive a provisional Step 2B summary from exact V2 Evidence anchors.

    This does not write an AtomicFact. It only gives the card planner the same
    stable Fact identity that deterministic assembly will later materialize.
    """
    if reviewed_title_projection is not None and reviewed_title_projection.source_label != proposal.label:
        raise ValueError("reviewed title projection must bind the model proposal label exactly")
    primary_units = tuple(evidence_by_id[evidence_id] for evidence_id in proposal.primary_evidence_ids)
    fact_id = make_fact_id(
        fact_type=proposal.fact_type.value,
        primary_evidence_fingerprints=[unit.fingerprint for unit in primary_units],
        assertion_contract={
            "assertion": proposal.assertion,
            "completeness_mode": proposal.completeness_mode.value,
            "primary_anchor_span_ids": proposal.primary_anchor_span_ids,
            "secondary_anchor_span_ids": proposal.secondary_anchor_span_ids,
            "selected_table_evidence_ids": proposal.selected_table_evidence_ids,
            "complete_table_evidence_ids": tuple(
                coverage.table_evidence_id for coverage in proposal.complete_table_semantic_coverages
            ),
            "numbers": [claim.model_dump(mode="json") for claim in proposal.numeric_claims],
            "conditions": [claim.model_dump(mode="json") for claim in proposal.condition_claims],
        },
    )
    return FactSummaryInput(
        fact_id=fact_id,
        fact_type=proposal.fact_type,
        label=reviewed_title_projection.canonical_title if reviewed_title_projection is not None else proposal.label,
        assertion_summary=proposal.assertion,
        completeness_mode=proposal.completeness_mode,
        must_cover=any(unit.must_cover_by_source for unit in primary_units),
        selected_table_evidence_ids=proposal.selected_table_evidence_ids,
        complete_table_evidence_ids=tuple(
            coverage.table_evidence_id for coverage in proposal.complete_table_semantic_coverages
        ),
        reviewed_title_projection=reviewed_title_projection,
        source_proposal_key=proposal.proposal_key,
    )


def build_step2b_request(
    *,
    sample_id: str,
    material_profile_id: str,
    facts: Iterable[FactSummaryInput],
    model: str,
    relay_base_url: str = _OFFLINE_RELAY_BASE_URL,
    prompt_template: str | None = None,
    decoder: Step2DecoderConfig | None = None,
    use_continuation_title_projection_contract: bool = False,
) -> Step2BRequest:
    frozen_facts = tuple(facts)
    request_version = (
        CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION
        if use_continuation_title_projection_contract
        else STEP2B_REQUEST_VERSION
    )
    payload = _step2b_payload(
        sample_id=sample_id,
        material_profile_id=material_profile_id,
        facts=frozen_facts,
        include_continuation_title_projection=use_continuation_title_projection_contract,
    )
    identity = make_step2_request_identity(
        stage=Step2Stage.CARD_PLANNING,
        request_version=request_version,
        model=model,
        payload_sha256=_canonical_sha256(payload),
        relay_base_url=relay_base_url,
        prompt_template=prompt_template,
        response_schema=Step2BResultPayload.model_json_schema(),
        decoder=decoder,
    )
    return Step2BRequest(
        identity=identity,
        sample_id=sample_id,
        material_profile_id=material_profile_id,
        facts=frozen_facts,
    )
