"""Offline tests for the source-owned card-candidate admission gate."""
from __future__ import annotations

import json

import pytest

from examcard.knowledge.contracts import (
    GroundedNumericClaim,
    SourceOwnedCardAdmissionResult,
    SourceOwnedKnowledgeCardCandidate,
    source_owned_admit_card_candidate,
)
from examcard.knowledge.contracts.hashing import canonical_step2_sha256
from examcard.knowledge.schema import KnowledgeCard


FACT_ID = "fact:" + "a" * 24
ANCHOR_ID = "span:" + "b" * 24
EVIDENCE_ID = "ev:source-owned"
CARD_ID = "card:" + canonical_step2_sha256({"fact_id": FACT_ID})[:24]


def _ready_candidate(
    *,
    claims: tuple[GroundedNumericClaim, ...] = (),
    anchors: tuple[str, ...] = (ANCHOR_ID,),
) -> SourceOwnedKnowledgeCardCandidate:
    return SourceOwnedKnowledgeCardCandidate(
        card_id=CARD_ID,
        fact_id=FACT_ID,
        title="Source-owned fact",
        body_assertion="The source states the fact.",
        grounded_numeric_claims=claims,
        provenance_anchor_span_ids=anchors,
        generation_status="ready",
    )


def _unchecked_candidate(**updates: object) -> SourceOwnedKnowledgeCardCandidate:
    values: dict[str, object] = {
        "card_id": CARD_ID,
        "fact_id": FACT_ID,
        "title": "Source-owned fact",
        "body_assertion": "The source states the fact.",
        "grounded_numeric_claims": (),
        "provenance_anchor_span_ids": (ANCHOR_ID,),
        "generation_status": "ready",
    }
    extra_fields = {key: value for key, value in updates.items() if key not in values}
    values.update({key: value for key, value in updates.items() if key in values})
    candidate = SourceOwnedKnowledgeCardCandidate.model_construct(**values)
    for key, value in extra_fields.items():
        object.__setattr__(candidate, key, value)
    return candidate


def test_ready_candidate_is_admitted_with_its_card_identity() -> None:
    candidate = _ready_candidate()

    result = source_owned_admit_card_candidate(candidate)

    assert result == SourceOwnedCardAdmissionResult(
        status="admitted",
        candidate_id=CARD_ID,
        rejection_reason=None,
    )


def test_failed_candidate_is_rejected_at_generation_boundary() -> None:
    candidate = _unchecked_candidate(
        generation_status="failed_closed",
        grounded_numeric_claims=(),
        provenance_anchor_span_ids=(),
    )

    result = source_owned_admit_card_candidate(candidate)

    assert result.status == "rejected"
    assert result.rejection_reason == "failed_closed_candidate"
    assert result.candidate_id == CARD_ID


def test_missing_provenance_is_rejected() -> None:
    candidate = _unchecked_candidate(provenance_anchor_span_ids=())

    result = source_owned_admit_card_candidate(candidate)

    assert result.status == "rejected"
    assert result.rejection_reason == "missing_provenance"


def test_missing_identity_is_rejected() -> None:
    candidate = _unchecked_candidate(card_id="", fact_id="")

    result = source_owned_admit_card_candidate(candidate)

    assert result.status == "rejected"
    assert result.rejection_reason == "missing_identity"
    assert result.candidate_id == ""


def test_numeric_claim_without_evidence_identity_is_rejected() -> None:
    claim = GroundedNumericClaim(
        value="45MPa",
        anchor_span_id=ANCHOR_ID,
        evidence_id="",
    )
    candidate = _unchecked_candidate(grounded_numeric_claims=(claim,))

    result = source_owned_admit_card_candidate(candidate)

    assert result.status == "rejected"
    assert result.rejection_reason == "missing_provenance"


def test_admission_does_not_mutate_candidate_or_rewrite_content() -> None:
    claim = GroundedNumericClaim(
        value="45MPa",
        anchor_span_id=ANCHOR_ID,
        evidence_id=EVIDENCE_ID,
    )
    candidate = _ready_candidate(claims=(claim,))
    before = candidate.model_dump(mode="json")

    result = source_owned_admit_card_candidate(candidate)

    assert result.status == "admitted"
    assert candidate.model_dump(mode="json") == before
    assert result.candidate_id == candidate.card_id


@pytest.mark.parametrize("forbidden_field", ("provider", "prompt", "raw_response", "generated_image"))
def test_authority_and_delivery_fields_are_rejected(
    forbidden_field: str,
) -> None:
    candidate = _unchecked_candidate(**{forbidden_field: "SENSITIVE"})

    result = source_owned_admit_card_candidate(candidate)

    assert result.status == "rejected"
    assert result.rejection_reason == "unsupported_content"


def test_non_source_owned_card_objects_cannot_enter_the_gate() -> None:
    legacy_card = KnowledgeCard.model_construct()

    result = source_owned_admit_card_candidate(legacy_card)

    assert result.status == "rejected"
    assert result.rejection_reason == "unsupported_content"
    assert result.candidate_id == ""


def test_admission_result_has_only_safe_gate_fields() -> None:
    result = source_owned_admit_card_candidate(_ready_candidate())
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    assert set(result.model_dump(mode="json")) == {
        "status",
        "candidate_id",
        "rejection_reason",
    }
    for sensitive_name in (
        "provider",
        "prompt",
        "response",
        "raw_evidence",
        "generated_image",
    ):
        assert sensitive_name not in serialized


def test_admission_status_and_rejection_reason_are_whitelisted() -> None:
    candidate = _ready_candidate()
    admitted = source_owned_admit_card_candidate(candidate)
    rejected = source_owned_admit_card_candidate(
        _unchecked_candidate(generation_status="failed_closed", provenance_anchor_span_ids=())
    )

    assert admitted.status in {"admitted", "rejected"}
    assert admitted.rejection_reason is None
    assert rejected.status in {"admitted", "rejected"}
    assert rejected.rejection_reason in {
        "failed_closed_candidate",
        "missing_provenance",
        "missing_identity",
        "unsupported_content",
    }
