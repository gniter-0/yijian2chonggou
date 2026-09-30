"""Offline tests for safe numeric-claim matching diagnostics."""
from __future__ import annotations

from pathlib import Path

from examcard.knowledge.contracts import (
    ModelNumericClaim,
    Step2AModelFactProposal,
    Step2AResponse,
    Step2ResultState,
    derive_step2a_anchor_span_selection,
    derive_step2a_condition_claims,
    validate_step2a_response,
)
from examcard.knowledge.contracts.enums import NumericTokenClass
from examcard.knowledge.contracts.validation import _safe_numeric_claim_classification
from examcard.knowledge.schema import FactType

from tests.test_numeric_claim_failure_diagnostics_v1 import _request


def _response(request, built, *, assertion: str, values: tuple[str, ...]) -> Step2AResponse:
    span = built.projection.anchor_spans[0]
    proposal = Step2AModelFactProposal(
        proposal_key="proposal:numeric-matching-001",
        source_section_ids=(request.sections[0].section_id,),
        fact_type=FactType.THRESHOLD,
        label="Numeric matching diagnostic test",
        assertion=assertion,
        primary_anchor_span_ids=(span.anchor_span_id,),
        numeric_claims=tuple(
            ModelNumericClaim(value=value, anchor_span_id=span.anchor_span_id)
            for value in values
        ),
    )
    selected = derive_step2a_anchor_span_selection(request=request, proposals=(proposal,))
    canonical = derive_step2a_condition_claims(request=request, proposals=selected.proposals)
    return Step2AResponse(
        request=request.identity,
        state=Step2ResultState.SUCCEEDED,
        proposals=canonical.proposals,
    )


def _diagnostic(tmp_path: Path, *, assertion: str, values: tuple[str, ...]):
    request, built = _request(tmp_path, assertion)
    report = validate_step2a_response(
        request=request,
        response=_response(request, built, assertion=assertion, values=values),
    )
    assert report.valid is False
    assert len(report.errors) > 0
    assert len(report.numeric_token_audit) == 1
    assert len(report.numeric_claim_failure_diagnostics) == 1
    return report.numeric_claim_failure_diagnostics[0], report


def test_no_assertion_token_is_distinguished_without_content_leakage(tmp_path: Path) -> None:
    diagnostic, report = _diagnostic(
        tmp_path,
        assertion="施工准备 SECRET_ASSERTION_TEXT",
        values=("99",),
    )

    assert diagnostic.match_state == "no_assertion_token"
    assert diagnostic.classification == "unmatched"
    assert diagnostic.failure_category == "unmatched_assertion_expression"
    assert diagnostic.anchor_exists is True
    serialized = diagnostic.model_dump(mode="json")
    assert set(serialized) == {
        "proposal_key",
        "claim_index",
        "classification",
        "match_state",
            "anchor_exists",
            "failure_category",
            "relation_state",
            "anchor_quantitative_count",
            "anchor_quantitative_count_category",
            "anchor_relation_state",
        }
    serialized_text = str(serialized)
    for forbidden in ("99", "SECRET_ASSERTION_TEXT", "施工准备", "anchor_span_id"):
        assert forbidden not in serialized_text
    assert report.errors.count("proposal:numeric-matching-001: numeric claim is not classified as a quantitative value") == 1


def test_quantitative_candidate_with_failed_normalized_match_is_distinguished(tmp_path: Path) -> None:
    diagnostic, report = _diagnostic(
        tmp_path,
        assertion="数量为45。",
        values=("45.0",),
    )

    assert diagnostic.match_state == "normalization_mismatch"
    assert diagnostic.classification == "unmatched"
    assert diagnostic.failure_category == "unmatched_assertion_expression"
    assert diagnostic.relation_state == "single_candidate_available"
    assert report.valid is False
    assert len(report.numeric_token_audit[0].tokens) == 1


def test_non_quantitative_exact_match_is_marked_rejected(tmp_path: Path) -> None:
    diagnostic, report = _diagnostic(
        tmp_path,
        assertion="1、施工准备；数量为45。",
        values=("1、",),
    )

    assert diagnostic.match_state == "matched_but_rejected"
    assert diagnostic.classification == NumericTokenClass.LIST_INDEX
    assert diagnostic.failure_category == "non_quantitative_token"
    assert diagnostic.relation_state == "rejected_candidate_available"
    assert report.valid is False


def test_unknown_matching_state_fallback_is_safe(tmp_path: Path) -> None:
    diagnostic, report = _diagnostic(
        tmp_path,
        assertion="3.2-1",
        values=("44",),
    )

    assert diagnostic.match_state == "unknown"
    assert diagnostic.classification == "unmatched"
    assert diagnostic.failure_category == "unmatched_assertion_expression"
    assert report.valid is False
    assert _safe_numeric_claim_classification("future_numeric_class") == "unknown"


def test_valid_quantitative_claim_has_no_matching_diagnostic(tmp_path: Path) -> None:
    request, built = _request(tmp_path, "数量为45题。")
    response = _response(request, built, assertion="数量为45题。", values=("45题",))

    report = validate_step2a_response(request=request, response=response)

    assert report.valid is True
    assert report.errors == ()
    assert report.numeric_claim_failure_diagnostics == ()
