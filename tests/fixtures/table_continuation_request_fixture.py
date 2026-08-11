"""Reviewable V17 Fact/Card candidates for Phase 2R.0.9.1.

This fixture is a new provenance root.  It intentionally cannot read or
promote the V16 response/Card Golden: every candidate binds one V17 request,
its raw fake response hash, and the reviewed table-continuation sidecar.

The fixture records a human review of the *candidate plan*, not a live-model
result.  A reviewed candidate remains ineligible for Step 2B until a future,
new V17 live Step 2A request independently clears its own contracts.
"""
from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel, V2_SCHEMA_VERSION

from examcard.knowledge.schema import FactType
from examcard.knowledge.contracts import (
    CardPresentation,
    Step2ARequest,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    canonical_step2_sha256,
)
from examcard.knowledge.llm import OpenAICompatibleStep2Transport, Step2AExecutionRecord


TABLE_CONTINUATION_REQUEST_CANDIDATE_FIXTURE_VERSION = "table_continuation_request_candidate_v3"
_SUPPORTED_FIXTURE_VERSIONS = frozenset(
    {
        "table_continuation_request_candidate_v1",
        "table_continuation_request_candidate_v2",
        TABLE_CONTINUATION_REQUEST_CANDIDATE_FIXTURE_VERSION,
    }
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class TableContinuationCandidateState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class TableContinuationCandidateReview(StrictModel):
    """Human audit metadata for one V17 Fact/Card candidate."""

    state: TableContinuationCandidateState = TableContinuationCandidateState.DRAFT
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    basis: str = "待人工复核"

    @model_validator(mode="after")
    def validate_review(self) -> "TableContinuationCandidateReview":
        if self.basis != self.basis.strip():
            raise ValueError("table continuation candidate review basis must be stripped")
        if self.state == TableContinuationCandidateState.REVIEWED:
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed table continuation candidates require reviewer metadata")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft table continuation candidates cannot contain reviewer metadata")
        return self


class TableContinuationRequestFactCandidate(StrictModel):
    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    canonical_title_candidate: str = Field(min_length=2, max_length=64)
    fact_type: FactType
    primary_anchor_span_ids: tuple[str, ...]
    review: TableContinuationCandidateReview = TableContinuationCandidateReview()

    @model_validator(mode="after")
    def validate_fact(self) -> "TableContinuationRequestFactCandidate":
        if (
            self.canonical_title_candidate != self.canonical_title_candidate.strip()
            or not self.primary_anchor_span_ids
            or len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids))
        ):
            raise ValueError("table continuation Fact candidates require stripped title and unique anchors")
        return self


class TableContinuationRequestCardCandidate(StrictModel):
    card_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=2, max_length=64)
    presentation: CardPresentation
    primary_proposal_keys: tuple[str, ...]
    review: TableContinuationCandidateReview = TableContinuationCandidateReview()

    @model_validator(mode="after")
    def validate_card(self) -> "TableContinuationRequestCardCandidate":
        if self.title != self.title.strip() or not self.primary_proposal_keys:
            raise ValueError("table continuation Card candidates require a stripped title and primary Facts")
        if len(self.primary_proposal_keys) != len(set(self.primary_proposal_keys)):
            raise ValueError("table continuation Card candidates require unique primary Fact proposals")
        return self


class TableContinuationRequestCandidateFixture(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    fixture_version: str = TABLE_CONTINUATION_REQUEST_CANDIDATE_FIXTURE_VERSION
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    state: TableContinuationCandidateState
    review: TableContinuationCandidateReview = TableContinuationCandidateReview()
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    material_profile_id: str = Field(min_length=1)
    source_request_id: str = Field(pattern=r"^step2a-request:[0-9a-f]{64}$")
    source_request_version: str
    source_request_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_model_input_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    source_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    table_continuation_input_id: str = Field(pattern=r"^table-request-input:[0-9a-f]{24}$")
    table_continuation_binding_id: str = Field(pattern=r"^table-continuation:[0-9a-f]{24}$")
    table_continuation_binding_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    table_continuation_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    continuation_review_sidecar_id: str = Field(pattern=r"^continuation-sidecar:[0-9a-f]{24}$")
    continuation_review_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    facts: tuple[TableContinuationRequestFactCandidate, ...]
    cards: tuple[TableContinuationRequestCardCandidate, ...]
    review_basis: str = Field(min_length=16, max_length=1200)

    @model_validator(mode="after")
    def validate_fixture(self) -> "TableContinuationRequestCandidateFixture":
        if self.fixture_version not in _SUPPORTED_FIXTURE_VERSIONS:
            raise ValueError("unsupported table continuation request candidate fixture version")
        if self.source_request_version != TABLE_CONTINUATION_STEP2A_REQUEST_VERSION:
            raise ValueError("Phase 2R.0.9 candidate fixture must bind the V17 request contract")
        if (
            self.fixture_version == TABLE_CONTINUATION_REQUEST_CANDIDATE_FIXTURE_VERSION
            and self.source_model_input_sha256 is None
        ):
            raise ValueError("V17 live-review candidate fixtures require the immutable model-input hash")
        fact_keys = [item.proposal_key for item in self.facts]
        card_keys = [item.card_key for item in self.cards]
        if len(self.facts) != 11 or len(fact_keys) != len(set(fact_keys)):
            raise ValueError("Phase 2R.0.9 candidate fixture requires exactly eleven unique Facts")
        if len(self.cards) != 5 or len(card_keys) != len(set(card_keys)):
            raise ValueError("Phase 2R.0.9 candidate fixture requires exactly five unique Cards")
        assigned = [proposal_key for card in self.cards for proposal_key in card.primary_proposal_keys]
        if set(assigned) != set(fact_keys) or len(assigned) != len(set(assigned)):
            raise ValueError("candidate Card grouping must assign every Fact exactly once")
        if self.review_basis != self.review_basis.strip():
            raise ValueError("candidate fixture review basis must be stripped")
        if self.state != self.review.state:
            raise ValueError("candidate fixture state must match its root review state")
        if self.state == TableContinuationCandidateState.REVIEWED:
            unreviewed_facts = [item.proposal_key for item in self.facts if item.review.state != TableContinuationCandidateState.REVIEWED]
            unreviewed_cards = [item.card_key for item in self.cards if item.review.state != TableContinuationCandidateState.REVIEWED]
            if unreviewed_facts or unreviewed_cards:
                raise ValueError("reviewed candidate fixture requires every Fact and Card candidate to be reviewed")
        return self


class TableContinuationRequestCandidateAlignment(StrictModel):
    fixture_id: str
    request_id: str
    alignment_mode: str
    fresh_request_identity: bool
    valid: bool
    failure_codes: tuple[str, ...] = ()
    matched_fact_count: int = Field(ge=0)
    matched_card_candidate_count: int = Field(ge=0)
    candidate_review_ready: bool
    review_failure_codes: tuple[str, ...] = ()
    basis: str


def load_table_continuation_request_candidate_fixture(path: Path) -> TableContinuationRequestCandidateFixture:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return TableContinuationRequestCandidateFixture.model_validate(raw)


def canonical_response_sha256(raw_response: str) -> str:
    return hashlib.sha256(raw_response.encode("utf-8")).hexdigest()


def v17_model_input_sha256(request: Step2ARequest) -> str:
    """Fingerprint the model-visible V17 input, excluding relay/model identity."""

    return canonical_step2_sha256(OpenAICompatibleStep2Transport.model_payload(request))


def assess_table_continuation_request_candidates(
    *,
    fixture: TableContinuationRequestCandidateFixture,
    request: Step2ARequest,
    execution: Step2AExecutionRecord,
    raw_response_sha256: str,
    require_fresh_request: bool = False,
) -> TableContinuationRequestCandidateAlignment:
    """Validate V17 candidate alignment and its independent human review.

    The reviewed candidate fixture is anchored to a deterministic offline
    response.  A live validation must instead bind a *new* request identity
    while retaining the same immutable request payload and source sidecars.
    Its raw response hash is intentionally not compared with the fake source
    response hash.
    """

    failures: list[str] = []
    if request.identity.request_version != TABLE_CONTINUATION_STEP2A_REQUEST_VERSION:
        failures.append("v17_request_version_mismatch")
    if require_fresh_request:
        if request.identity.request_id == fixture.source_request_id:
            failures.append("live_request_identity_reused_fixture_source")
        if fixture.source_model_input_sha256 is None:
            failures.append("live_candidate_fixture_model_input_hash_missing")
        elif v17_model_input_sha256(request) != fixture.source_model_input_sha256:
            failures.append("v17_model_input_provenance_mismatch")
    elif (
        request.identity.request_id != fixture.source_request_id
        or request.identity.payload_sha256 != fixture.source_request_payload_sha256
        or raw_response_sha256 != fixture.source_response_sha256
    ):
        failures.append("v17_request_or_response_provenance_mismatch")
    if request.sample_id != fixture.sample_id or request.material_profile_id != fixture.material_profile_id:
        failures.append("sample_or_material_profile_mismatch")
    if len(request.table_continuation_inputs) != 1:
        failures.append("controlled_table_input_missing_or_ambiguous")
    else:
        controlled = request.table_continuation_inputs[0]
        if (
            controlled.input_id != fixture.table_continuation_input_id
            or controlled.binding_id != fixture.table_continuation_binding_id
            or controlled.binding_fingerprint_sha256 != fixture.table_continuation_binding_fingerprint_sha256
            or controlled.binding_sidecar_sha256 != fixture.table_continuation_sidecar_sha256
        ):
            failures.append("controlled_table_provenance_mismatch")
    if request.continuation_review_sidecar is None or request.continuation_review_sidecar_sha256 is None:
        failures.append("reviewed_page_continuation_provenance_missing")
    elif (
        request.continuation_review_sidecar.sidecar_id != fixture.continuation_review_sidecar_id
        or request.continuation_review_sidecar_sha256 != fixture.continuation_review_sidecar_sha256
    ):
        failures.append("reviewed_page_continuation_provenance_mismatch")
    if not execution.contract_report.valid or execution.response.state.value != "succeeded":
        failures.append("step2a_contract_not_valid")

    actual = {proposal.proposal_key: proposal for proposal in execution.response.proposals}
    matched_facts = 0
    for expected in fixture.facts:
        proposal = actual.get(expected.proposal_key)
        if proposal is None:
            failures.append(f"missing_fact:{expected.proposal_key}")
            continue
        if (
            proposal.fact_type != expected.fact_type
            or proposal.label != expected.canonical_title_candidate
            or proposal.primary_anchor_span_ids != expected.primary_anchor_span_ids
        ):
            failures.append(f"fact_candidate_mismatch:{expected.proposal_key}")
            continue
        matched_facts += 1
    if set(actual) != {item.proposal_key for item in fixture.facts}:
        failures.append("fact_set_mismatch")

    known_keys = set(actual)
    matched_cards = 0
    for card in fixture.cards:
        if not set(card.primary_proposal_keys) <= known_keys:
            failures.append(f"card_candidate_unknown_fact:{card.card_key}")
            continue
        matched_cards += 1
    review_failures: list[str] = []
    if fixture.state != TableContinuationCandidateState.REVIEWED:
        review_failures.append("fixture_review_not_reviewed")
    review_failures.extend(
        f"fact_review_not_reviewed:{item.proposal_key}"
        for item in fixture.facts
        if item.review.state != TableContinuationCandidateState.REVIEWED
    )
    review_failures.extend(
        f"card_review_not_reviewed:{item.card_key}"
        for item in fixture.cards
        if item.review.state != TableContinuationCandidateState.REVIEWED
    )
    failures.extend(review_failures)
    return TableContinuationRequestCandidateAlignment(
        fixture_id=fixture.fixture_id,
        request_id=request.identity.request_id,
        alignment_mode="reissued_live" if require_fresh_request else "source_fixture",
        fresh_request_identity=require_fresh_request and request.identity.request_id != fixture.source_request_id,
        valid=not failures,
        failure_codes=tuple(dict.fromkeys(failures)),
        matched_fact_count=matched_facts,
        matched_card_candidate_count=matched_cards,
        candidate_review_ready=not review_failures,
        review_failure_codes=tuple(review_failures),
        basis=(
            "V17 Fact titles, anchors, controlled-table provenance, Card grouping, and item-level human reviews match the independent candidate fixture."
            if not failures
            else "V17 Fact/Card candidates are not eligible until their independent provenance, exact Fact anchors, and item-level reviews match."
        ),
    )
