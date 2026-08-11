"""Draft-only Card Golden contracts for a reviewed continuation Step 2B bridge."""
from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from ..materials.schema import StrictModel, V2_SCHEMA_VERSION

from .schema import FactType, ShortCardException
from .contracts import (
    CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION,
    CardPresentation,
    Step2BRequest,
    Step2BResponse,
)


CONTINUATION_CARD_GOLDEN_DRAFT_VERSION = "continuation_card_golden_draft_v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class CardGoldenReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class CardGoldenReview(StrictModel):
    state: CardGoldenReviewState = CardGoldenReviewState.DRAFT
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    basis: str = "待人工复核"

    @model_validator(mode="after")
    def validate_review(self) -> "CardGoldenReview":
        if self.state == CardGoldenReviewState.REVIEWED:
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed Card Golden entries require reviewer metadata")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft Card Golden entries cannot contain reviewer metadata")
        return self


class ContinuationCardGoldenFact(StrictModel):
    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=2, max_length=64)
    fact_type: FactType
    review: CardGoldenReview = CardGoldenReview()


class ContinuationCardGoldenTarget(StrictModel):
    card_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=2, max_length=64)
    primary_proposal_keys: tuple[str, ...]
    presentation: CardPresentation
    split_reason: str = Field(min_length=6, max_length=240)
    short_card_exception: ShortCardException | None = None
    review: CardGoldenReview = CardGoldenReview()

    @model_validator(mode="after")
    def validate_target(self) -> "ContinuationCardGoldenTarget":
        if not self.primary_proposal_keys or len(self.primary_proposal_keys) != len(set(self.primary_proposal_keys)):
            raise ValueError("Card Golden targets require unique primary proposal keys")
        return self


class ContinuationCardGoldenFixture(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    fixture_version: str = CONTINUATION_CARD_GOLDEN_DRAFT_VERSION
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    material_profile_id: str = Field(min_length=1)
    source_request_id: str = Field(pattern=r"^step2a-request:[0-9a-f]{64}$")
    source_request_version: str = Field(min_length=1)
    source_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_execution_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    continuation_title_projection_id: str = Field(pattern=r"^continuation-title-projection:[0-9a-f]{24}$")
    continuation_title_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    facts: tuple[ContinuationCardGoldenFact, ...]
    cards: tuple[ContinuationCardGoldenTarget, ...]
    review: CardGoldenReview = CardGoldenReview()

    @model_validator(mode="after")
    def validate_fixture(self) -> "ContinuationCardGoldenFixture":
        if self.fixture_version != CONTINUATION_CARD_GOLDEN_DRAFT_VERSION:
            raise ValueError("unsupported continuation Card Golden fixture version")
        fact_keys = [fact.proposal_key for fact in self.facts]
        card_keys = [card.card_key for card in self.cards]
        if not self.facts or len(fact_keys) != len(set(fact_keys)):
            raise ValueError("Card Golden fixture requires unique Fact proposal keys")
        if not self.cards or len(card_keys) != len(set(card_keys)):
            raise ValueError("Card Golden fixture requires unique card keys")
        assigned = [key for card in self.cards for key in card.primary_proposal_keys]
        if set(assigned) != set(fact_keys) or len(assigned) != len(set(assigned)):
            raise ValueError("Card Golden fixture must assign every Fact proposal to exactly one primary card")
        return self


class ContinuationCardGoldenCardAlignment(StrictModel):
    card_key: str
    expected_title: str
    expected_presentation: CardPresentation
    expected_primary_proposal_keys: tuple[str, ...]
    match_state: str
    actual_card_proposal_key: str | None = None
    title_drift: bool = False
    presentation_drift: bool = False
    grouping_drift: bool = False


class ContinuationCardGoldenAlignment(StrictModel):
    fixture_id: str
    sample_id: str
    valid: bool
    failure_codes: tuple[str, ...] = ()
    fact_failure_codes: tuple[str, ...] = ()
    review_ready_for_real_step2b: bool
    review_failure_codes: tuple[str, ...] = ()
    card_alignments: tuple[ContinuationCardGoldenCardAlignment, ...]
    extra_card_proposal_keys: tuple[str, ...] = ()
    basis: str


def assess_continuation_card_golden(
    *, fixture: ContinuationCardGoldenFixture, request: Step2BRequest, response: Step2BResponse
) -> ContinuationCardGoldenAlignment:
    """Compare a V5 fake/real Step 2B response to its response-bound draft plan."""

    failures: list[str] = []
    fact_failures: list[str] = []
    if request.sample_id != fixture.sample_id:
        failures.append("sample_id_mismatch")
    if request.material_profile_id != fixture.material_profile_id:
        failures.append("material_profile_mismatch")
    if request.identity.request_version != CONTINUATION_TITLE_PROJECTION_STEP2B_REQUEST_VERSION:
        failures.append("step2b_contract_version_mismatch")
    facts_by_proposal = {fact.source_proposal_key: fact for fact in request.facts if fact.source_proposal_key is not None}
    if set(facts_by_proposal) != {fact.proposal_key for fact in fixture.facts}:
        fact_failures.append("source_proposal_set_mismatch")
    expected_by_proposal = {fact.proposal_key: fact for fact in fixture.facts}
    for proposal_key, expected in expected_by_proposal.items():
        actual = facts_by_proposal.get(proposal_key)
        if actual is None:
            continue
        if actual.fact_type != expected.fact_type:
            fact_failures.append(f"fact_type_mismatch:{proposal_key}")
        if actual.label != expected.title:
            fact_failures.append(f"fact_title_mismatch:{proposal_key}")
    projected = tuple(
        fact.reviewed_title_projection.continuation_title_projection
        for fact in request.facts
        if fact.reviewed_title_projection is not None
        and fact.reviewed_title_projection.continuation_title_projection is not None
    )
    if len(projected) != 1:
        fact_failures.append("continuation_projection_missing_or_ambiguous")
    else:
        provenance = projected[0]
        if (
            provenance.projection_id != fixture.continuation_title_projection_id
            or provenance.projection_sha256 != fixture.continuation_title_projection_sha256
            or provenance.source_request_id != fixture.source_request_id
            or provenance.source_request_version != fixture.source_request_version
            or provenance.source_response_sha256 != fixture.source_response_sha256
        ):
            fact_failures.append("continuation_projection_provenance_mismatch")
    if response.request != request.identity or response.state.value != "succeeded":
        failures.append("step2b_response_not_successful")
        actual_cards = ()
    else:
        actual_cards = response.proposals
    fact_ids_by_proposal = {key: fact.fact_id for key, fact in facts_by_proposal.items()}
    used_cards: set[str] = set()
    alignments: list[ContinuationCardGoldenCardAlignment] = []
    for expected in fixture.cards:
        expected_fact_ids = {fact_ids_by_proposal.get(key) for key in expected.primary_proposal_keys}
        if None in expected_fact_ids:
            alignments.append(
                ContinuationCardGoldenCardAlignment(
                    card_key=expected.card_key,
                    expected_title=expected.title,
                    expected_presentation=expected.presentation,
                    expected_primary_proposal_keys=expected.primary_proposal_keys,
                    match_state="missing_fact_dependency",
                )
            )
            continue
        candidates = [
            card
            for card in actual_cards
            if set(card.primary_fact_ids) == expected_fact_ids and card.proposal_key not in used_cards
        ]
        if len(candidates) != 1:
            alignments.append(
                ContinuationCardGoldenCardAlignment(
                    card_key=expected.card_key,
                    expected_title=expected.title,
                    expected_presentation=expected.presentation,
                    expected_primary_proposal_keys=expected.primary_proposal_keys,
                    match_state="missing" if not candidates else "ambiguous",
                )
            )
            continue
        actual = candidates[0]
        used_cards.add(actual.proposal_key)
        alignments.append(
            ContinuationCardGoldenCardAlignment(
                card_key=expected.card_key,
                expected_title=expected.title,
                expected_presentation=expected.presentation,
                expected_primary_proposal_keys=expected.primary_proposal_keys,
                match_state="matched",
                actual_card_proposal_key=actual.proposal_key,
                title_drift=actual.title != expected.title,
                presentation_drift=actual.presentation != expected.presentation,
                grouping_drift=set(actual.primary_fact_ids) != expected_fact_ids,
            )
        )
    extra = tuple(card.proposal_key for card in actual_cards if card.proposal_key not in used_cards)
    valid = (
        not failures
        and not fact_failures
        and not extra
        and all(
            item.match_state == "matched"
            and not item.title_drift
            and not item.presentation_drift
            and not item.grouping_drift
            for item in alignments
        )
    )
    review_failures: list[str] = []
    if fixture.review.state != CardGoldenReviewState.REVIEWED:
        review_failures.append("fixture_review_not_reviewed")
    review_failures.extend(
        f"fact_review_not_reviewed:{fact.proposal_key}"
        for fact in fixture.facts
        if fact.review.state != CardGoldenReviewState.REVIEWED
    )
    review_failures.extend(
        f"card_review_not_reviewed:{card.card_key}"
        for card in fixture.cards
        if card.review.state != CardGoldenReviewState.REVIEWED
    )
    review_ready = not review_failures
    return ContinuationCardGoldenAlignment(
        fixture_id=fixture.fixture_id,
        sample_id=fixture.sample_id,
        valid=valid,
        failure_codes=tuple(dict.fromkeys(failures)),
        fact_failure_codes=tuple(dict.fromkeys(fact_failures)),
        review_ready_for_real_step2b=review_ready,
        review_failure_codes=tuple(review_failures),
        card_alignments=tuple(alignments),
        extra_card_proposal_keys=extra,
        basis=(
            "continuation Card Golden exactly matches the V5 Step 2B request and response; all entries are reviewed"
            if valid and review_ready
            else "continuation Card Golden exactly matches the V5 Step 2B request and response but has pending manual review"
            if valid
            else "continuation Card Golden rejected the draft card plan"
        ),
    )
