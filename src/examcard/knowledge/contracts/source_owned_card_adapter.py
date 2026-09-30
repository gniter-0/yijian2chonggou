"""Adapter from source-owned canonical facts to card-pipeline candidates."""
from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from .claims import GroundedNumericClaim
from .hashing import _canonical_sha256
from .source_owned_contract import SourceOwnedCanonicalFact, _source_owned_canonical_fact_id


SourceOwnedCardCandidateGenerationStatus: TypeAlias = Literal["ready", "failed_closed"]

_FACT_ID_PATTERN = re.compile(r"^fact:[0-9a-f]{24}$")
_PROPOSAL_KEY_PATTERN = re.compile(r"^proposal:[a-z0-9][a-z0-9._-]*$")
_ANCHOR_SPAN_ID_PATTERN = re.compile(r"^span:[0-9a-f]{24}$")


def _source_owned_card_id(fact_id: str) -> str:
    digest = _canonical_sha256({"fact_id": fact_id})
    return f"card:{digest[:24]}"


class SourceOwnedKnowledgeCardCandidate(StrictModel):
    """Minimal card-pipeline hand-off that cannot carry model authority."""

    card_id: str = Field(min_length=1)
    fact_id: str = ""
    title: str = ""
    body_assertion: str = ""
    grounded_numeric_claims: tuple[GroundedNumericClaim, ...] = ()
    provenance_anchor_span_ids: tuple[str, ...] = ()
    generation_status: SourceOwnedCardCandidateGenerationStatus

    @model_validator(mode="after")
    def validate_candidate(self) -> "SourceOwnedKnowledgeCardCandidate":
        if self.generation_status == "ready":
            if not _FACT_ID_PATTERN.fullmatch(self.fact_id):
                raise ValueError("ready source-owned card candidates require a canonical fact ID")
            if self.card_id != _source_owned_card_id(self.fact_id):
                raise ValueError("source-owned card candidate ID must be derived from fact ID")
            if not self.title or not self.body_assertion:
                raise ValueError("ready source-owned card candidates require fact content")
            if not self.provenance_anchor_span_ids:
                raise ValueError("ready source-owned card candidates require anchor provenance")
            if len(self.provenance_anchor_span_ids) != len(set(self.provenance_anchor_span_ids)):
                raise ValueError("source-owned card candidate anchors must be unique")
            if any(
                not _ANCHOR_SPAN_ID_PATTERN.fullmatch(anchor_span_id)
                for anchor_span_id in self.provenance_anchor_span_ids
            ):
                raise ValueError("source-owned card candidate anchors must be valid span IDs")
            if any(
                claim.anchor_span_id not in self.provenance_anchor_span_ids
                or not claim.evidence_id
                for claim in self.grounded_numeric_claims
            ):
                raise ValueError("source-owned card candidate claims must retain valid provenance")
        elif self.grounded_numeric_claims or self.provenance_anchor_span_ids:
            raise ValueError("failed source-owned card candidates cannot carry claims or provenance")
        return self


def _failed_source_owned_card_candidate(fact_id: str = "") -> SourceOwnedKnowledgeCardCandidate:
    safe_fact_id = fact_id if _FACT_ID_PATTERN.fullmatch(fact_id) else ""
    return SourceOwnedKnowledgeCardCandidate(
        card_id=_source_owned_card_id(safe_fact_id) if safe_fact_id else "card:failed-closed",
        fact_id=safe_fact_id,
        generation_status="failed_closed",
    )


def _has_valid_ready_provenance(canonical_fact: SourceOwnedCanonicalFact) -> bool:
    anchor_span_ids = canonical_fact.anchor_span_ids
    if not isinstance(anchor_span_ids, tuple) or not anchor_span_ids:
        return False
    if len(anchor_span_ids) != len(set(anchor_span_ids)):
        return False
    if any(not _ANCHOR_SPAN_ID_PATTERN.fullmatch(anchor_span_id) for anchor_span_id in anchor_span_ids):
        return False
    claims = canonical_fact.grounded_numeric_claims
    if not isinstance(claims, tuple) or any(not isinstance(claim, GroundedNumericClaim) for claim in claims):
        return False
    return all(
        claim.anchor_span_id in anchor_span_ids and bool(claim.evidence_id)
        for claim in claims
    )


def source_owned_fact_to_card_candidate(
    canonical_fact: SourceOwnedCanonicalFact,
) -> SourceOwnedKnowledgeCardCandidate:
    """Project one assembled source-owned fact without rewriting its content."""

    if not isinstance(canonical_fact, SourceOwnedCanonicalFact):
        raise ValueError("source-owned card adaptation requires a canonical source-owned fact")

    fact_id = canonical_fact.fact_id if isinstance(canonical_fact.fact_id, str) else ""
    if canonical_fact.derivation_status != "assembled":
        return _failed_source_owned_card_candidate(fact_id)
    if not _FACT_ID_PATTERN.fullmatch(fact_id):
        return _failed_source_owned_card_candidate()
    try:
        expected_fact_id = _source_owned_canonical_fact_id(
            proposal_key=canonical_fact.proposal_key,
            fact_type=canonical_fact.fact_type,
            anchor_span_ids=canonical_fact.anchor_span_ids,
        )
    except (AttributeError, TypeError):
        return _failed_source_owned_card_candidate(fact_id)
    if fact_id != expected_fact_id:
        return _failed_source_owned_card_candidate(fact_id)
    if not isinstance(canonical_fact.proposal_key, str) or not _PROPOSAL_KEY_PATTERN.fullmatch(
        canonical_fact.proposal_key
    ):
        return _failed_source_owned_card_candidate(fact_id)
    if not isinstance(canonical_fact.label, str) or not canonical_fact.label:
        return _failed_source_owned_card_candidate(fact_id)
    if not isinstance(canonical_fact.assertion, str) or not canonical_fact.assertion:
        return _failed_source_owned_card_candidate(fact_id)
    if not _has_valid_ready_provenance(canonical_fact):
        return _failed_source_owned_card_candidate(fact_id)

    return SourceOwnedKnowledgeCardCandidate(
        card_id=_source_owned_card_id(fact_id),
        fact_id=fact_id,
        title=canonical_fact.label,
        body_assertion=canonical_fact.assertion,
        grounded_numeric_claims=canonical_fact.grounded_numeric_claims,
        provenance_anchor_span_ids=canonical_fact.anchor_span_ids,
        generation_status="ready",
    )
