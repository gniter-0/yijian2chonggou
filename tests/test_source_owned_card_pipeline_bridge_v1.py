"""Offline tests for the source-owned candidate-to-card-pipeline bridge."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from examcard.knowledge.contracts import (
    GroundedNumericClaim,
    SourceOwnedKnowledgeCardBridgeResult,
    SourceOwnedKnowledgeCardCandidate,
    source_owned_bridge_to_knowledge_card,
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
) -> SourceOwnedKnowledgeCardCandidate:
    return SourceOwnedKnowledgeCardCandidate(
        card_id=CARD_ID,
        fact_id=FACT_ID,
        title="Source-owned fact",
        body_assertion="The source states the fact.",
        grounded_numeric_claims=claims,
        provenance_anchor_span_ids=(ANCHOR_ID,),
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


def test_admitted_candidate_bridges_with_identity_and_provenance() -> None:
    candidate = _ready_candidate()

    result = source_owned_bridge_to_knowledge_card(candidate)

    assert result == SourceOwnedKnowledgeCardBridgeResult(
        status="bridged",
        knowledge_card_id=candidate.card_id,
        source_fact_id=candidate.fact_id,
        provenance_anchor_span_ids=candidate.provenance_anchor_span_ids,
    )
    assert not isinstance(result, KnowledgeCard)


def test_rejected_candidate_fails_closed_before_bridge() -> None:
    candidate = _unchecked_candidate(
        generation_status="failed_closed",
        grounded_numeric_claims=(),
        provenance_anchor_span_ids=(),
    )

    result = source_owned_bridge_to_knowledge_card(candidate)

    assert result == SourceOwnedKnowledgeCardBridgeResult.failed_closed()


@pytest.mark.parametrize(
    "updates",
    (
        {"card_id": ""},
        {"fact_id": ""},
        {"provenance_anchor_span_ids": ()},
    ),
)
def test_missing_identity_or_provenance_fails_closed(
    updates: dict[str, object],
) -> None:
    result = source_owned_bridge_to_knowledge_card(_unchecked_candidate(**updates))

    assert result.status == "failed_closed"
    assert result.knowledge_card_id == ""
    assert result.source_fact_id == ""
    assert result.provenance_anchor_span_ids == ()


def test_numeric_claim_provenance_is_preserved_on_untouched_candidate() -> None:
    claim = GroundedNumericClaim(
        value="45MPa",
        anchor_span_id=ANCHOR_ID,
        evidence_id=EVIDENCE_ID,
    )
    candidate = _ready_candidate(claims=(claim,))
    before = candidate.model_dump(mode="json")

    result = source_owned_bridge_to_knowledge_card(candidate)

    assert result.status == "bridged"
    assert candidate.model_dump(mode="json") == before
    assert candidate.grounded_numeric_claims == (claim,)
    assert result.provenance_anchor_span_ids == (ANCHOR_ID,)
    assert candidate.grounded_numeric_claims[0].anchor_span_id == ANCHOR_ID
    assert candidate.grounded_numeric_claims[0].evidence_id == EVIDENCE_ID


@pytest.mark.parametrize("forbidden_field", ("provider", "prompt", "raw_response", "generated_image"))
def test_authority_or_image_fields_cannot_cross_bridge(
    forbidden_field: str,
) -> None:
    candidate = _unchecked_candidate(**{forbidden_field: "SENSITIVE"})

    result = source_owned_bridge_to_knowledge_card(candidate)

    assert result.status == "failed_closed"
    assert result.provenance_anchor_span_ids == ()


def test_bridge_does_not_rewrite_title_or_assertion() -> None:
    candidate = _ready_candidate()
    before_title = candidate.title
    before_assertion = candidate.body_assertion

    result = source_owned_bridge_to_knowledge_card(candidate)

    assert result.status == "bridged"
    assert candidate.title == before_title
    assert candidate.body_assertion == before_assertion


def test_legacy_knowledgecard_is_not_a_source_owned_bridge_input() -> None:
    legacy_card = KnowledgeCard.model_construct()

    result = source_owned_bridge_to_knowledge_card(legacy_card)

    assert result == SourceOwnedKnowledgeCardBridgeResult.failed_closed()


def test_bridge_result_is_safe_and_has_no_published_card_schema_changes() -> None:
    before_schema = KnowledgeCard.model_json_schema()
    result = source_owned_bridge_to_knowledge_card(_ready_candidate())
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    assert set(result.model_dump(mode="json")) == {
        "status",
        "knowledge_card_id",
        "source_fact_id",
        "provenance_anchor_span_ids",
    }
    for sensitive_name in (
        "prompt",
        "provider",
        "raw_response",
        "raw_evidence",
        "generated_image",
    ):
        assert sensitive_name not in serialized
    assert KnowledgeCard.model_json_schema() == before_schema


def test_bridge_schema_is_strict_and_status_is_whitelisted() -> None:
    with pytest.raises(ValidationError):
        SourceOwnedKnowledgeCardBridgeResult(
            status="bridged",
            knowledge_card_id=CARD_ID,
            source_fact_id=FACT_ID,
            provenance_anchor_span_ids=(ANCHOR_ID,),
            unexpected="value",
        )

    result = source_owned_bridge_to_knowledge_card(_ready_candidate())
    assert result.status in {"bridged", "failed_closed"}
