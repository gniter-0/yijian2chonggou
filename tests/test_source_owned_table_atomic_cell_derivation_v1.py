"""Offline tests for source-owned table-cell atomic numeric derivation."""
from __future__ import annotations

import hashlib

import pytest

from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    SourceOwnedLocalDerivationResult,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    make_evidence_anchor_span_id,
    source_owned_derive_table_atomic_numeric_claims,
)
from examcard.knowledge.extraction_v1 import source_owned_extract_document_proposals
from examcard.materials.schema import CoverageRequirement, PolicyDecision, PolicyDisposition, SemanticRole


def _make_anchor(text: str, *, suffix: str) -> EvidenceAnchorSpan:
    parent_evidence_id = "ev:" + suffix * 24
    content_policy_fingerprint = "b" * 64
    source_manifest_sha256 = "c" * 64
    exact_text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    anchor_span_id = make_evidence_anchor_span_id(
        parent_evidence_id=parent_evidence_id,
        start=0,
        end=len(text),
        exact_text_sha256=exact_text_sha256,
        content_policy_fingerprint_sha256=content_policy_fingerprint,
        source_manifest_sha256=source_manifest_sha256,
    )
    policy_decision = PolicyDecision(
        evidence_id=parent_evidence_id,
        semantic_role=SemanticRole.CORE,
        disposition=PolicyDisposition.INCLUDED,
        coverage_requirement=CoverageRequirement.REQUIRED,
        policy_id="core-policy-v1",
        policy_revision=1,
        reason="selected table-cell source anchor",
    )
    return EvidenceAnchorSpan(
        anchor_span_id=anchor_span_id,
        parent_evidence_id=parent_evidence_id,
        parent_evidence_kind=EvidenceKind.TABLE_CELL,
        start=0,
        end=len(text),
        canonical_text=text,
        exact_text_sha256=exact_text_sha256,
        content_policy_id="core-policy-v1",
        content_policy_revision=1,
        content_policy_fingerprint_sha256=content_policy_fingerprint,
        policy_decision=policy_decision,
        source_manifest_sha256=source_manifest_sha256,
    )


def _canonical_proposal(*anchor_span_ids: str, fact_type: str = "definition"):
    payload = Step2AProductionSourceOwnedArbitraryPDFResultPayload.model_validate(
        {
            "proposals": [
                {
                    "fact_type": fact_type,
                    "label": "Table cell fact",
                    "assertion": "The selected table cells provide the values.",
                    "primary_anchor_span_ids": list(anchor_span_ids),
                }
            ]
        }
    )
    return source_owned_extract_document_proposals(response=payload)[0]


def test_single_table_cell_derives_one_atomic_claim() -> None:
    anchor = _make_anchor("45MPa", suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.table_atomic_derivation_status == "derived"
    assert result.numeric_derivation_status == "no_quantitative_tokens"
    assert len(result.grounded_numeric_claims) == 1
    claim = result.grounded_numeric_claims[0]
    assert claim.value == "45MPa"
    assert claim.anchor_span_id == anchor.anchor_span_id
    assert claim.evidence_id == anchor.parent_evidence_id


def test_multiple_independent_cells_derive_independent_claims() -> None:
    first = _make_anchor("45MPa", suffix="a")
    second = _make_anchor("20%", suffix="d")
    proposal = _canonical_proposal(first.anchor_span_id, second.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={first.anchor_span_id: first, second.anchor_span_id: second},
    )

    assert result.table_atomic_derivation_status == "derived"
    assert tuple(claim.anchor_span_id for claim in result.grounded_numeric_claims) == (
        first.anchor_span_id,
        second.anchor_span_id,
    )
    assert tuple(claim.evidence_id for claim in result.grounded_numeric_claims) == (
        first.parent_evidence_id,
        second.parent_evidence_id,
    )


@pytest.mark.parametrize("text", ("2020 45MPa", "45MPa and 20%"))
def test_cell_with_multiple_quantitative_tokens_fails_closed(text: str) -> None:
    anchor = _make_anchor(text, suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.table_atomic_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_composition_is_explicitly_unsupported_without_numeric_claims() -> None:
    year = _make_anchor("2020年", suffix="a")
    value = _make_anchor("45题", suffix="d")
    proposal = _canonical_proposal(year.anchor_span_id, value.anchor_span_id, fact_type="composition")

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={year.anchor_span_id: year, value.anchor_span_id: value},
    )

    assert result.table_atomic_derivation_status == "unsupported_composition"
    assert result.grounded_numeric_claims == ()


def test_duplicate_quantitative_token_across_cells_fails_closed() -> None:
    first = _make_anchor("45MPa", suffix="a")
    second = _make_anchor("45MPa", suffix="d")
    proposal = _canonical_proposal(first.anchor_span_id, second.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={first.anchor_span_id: first, second.anchor_span_id: second},
    )

    assert result.table_atomic_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_missing_anchor_fails_closed() -> None:
    anchor = _make_anchor("45MPa", suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(proposal=proposal, evidence_spans={})

    assert result.table_atomic_derivation_status == "failed_closed"
    assert result.derived_facts.local_extraction_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_anchor_identity_mismatch_fails_closed() -> None:
    selected = _make_anchor("45MPa", suffix="a")
    other = _make_anchor("45MPa", suffix="d")
    proposal = _canonical_proposal(selected.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={selected.anchor_span_id: other},
    )

    assert result.table_atomic_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_non_table_anchor_is_not_accepted_as_a_table_cell() -> None:
    anchor = _make_anchor("45MPa", suffix="a").model_copy(update={"parent_evidence_kind": EvidenceKind.PARAGRAPH})
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.table_atomic_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


@pytest.mark.parametrize("text", ("Q235", "3.2"))
def test_identifier_or_list_index_does_not_generate_numeric_claim(text: str) -> None:
    anchor = _make_anchor(text, suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.table_atomic_derivation_status == "failed_closed"
    assert result.grounded_numeric_claims == ()


def test_result_is_source_owned_and_keeps_only_claim_provenance() -> None:
    anchor = _make_anchor("PRIVATE_TABLE_CELL_TEXT 45MPa", suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )
    dumped = result.model_dump(mode="json")

    assert set(dumped["grounded_numeric_claims"][0]) == {"value", "anchor_span_id", "evidence_id"}
    assert "PRIVATE_TABLE_CELL_TEXT" not in str(dumped)
    assert "canonical_text" not in str(dumped)
    assert "table_occurrences" not in str(dumped)
    assert "prompt" not in str(dumped)
    assert "response" not in str(dumped)


def test_existing_atomic_numeric_status_remains_separate() -> None:
    anchor = _make_anchor("45MPa", suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )

    assert result.numeric_derivation_status == "no_quantitative_tokens"
    assert result.table_atomic_derivation_status == "derived"


def test_table_result_round_trips_with_table_status_without_changing_atomic_status() -> None:
    anchor = _make_anchor("45MPa", suffix="a")
    proposal = _canonical_proposal(anchor.anchor_span_id)

    result = source_owned_derive_table_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )
    round_tripped = SourceOwnedLocalDerivationResult.model_validate(result.model_dump())

    assert round_tripped.table_atomic_derivation_status == "derived"
    assert round_tripped.numeric_derivation_status == "no_quantitative_tokens"
    assert round_tripped.grounded_numeric_claims == result.grounded_numeric_claims
