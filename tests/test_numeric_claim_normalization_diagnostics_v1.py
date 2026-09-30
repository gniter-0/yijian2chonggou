"""Offline tests for safe numeric-claim normalization diagnostics."""
from __future__ import annotations

import pytest

from examcard.knowledge.contracts import (
    NumericTokenClass,
    validate_step2a_response,
)
from examcard.knowledge.contracts.numeric_tokens import NumericTokenClassificationRecord
from examcard.knowledge.contracts.validation import _numeric_claim_failure_diagnostic

from tests.test_numeric_claim_failure_diagnostics_v1 import _request, _response


_ANCHOR_ID = "span:" + "a" * 24


def _token(value: str, classification: NumericTokenClass = NumericTokenClass.QUANTITATIVE_VALUE):
    return NumericTokenClassificationRecord(
        value=value,
        classification=classification,
        start=0,
        end=max(1, len(value)),
    )


def _diagnostic(*, claim_value: str, candidate_values: tuple[str, ...]):
    return _numeric_claim_failure_diagnostic(
        proposal_key="proposal:normalization-diagnostic-001",
        claim_index=0,
        claim_value=claim_value,
        anchor_span_id=_ANCHOR_ID,
        classified_tokens=tuple(_token(value) for value in candidate_values),
        selected_span_ids=(_ANCHOR_ID,),
        known_anchor_span_ids={_ANCHOR_ID},
    )


@pytest.mark.parametrize(
    ("claim_value", "candidate_values", "expected"),
    [
        ("45\u200b", ("45",), "whitespace_only"),
        ("45?", ("45",), "punctuation_only"),
        ("45", ("45题",), "unit_suffix_difference"),
        ("abc45", ("45",), "prefix_suffix_difference"),
        ("20/30", ("20", "30"), "multiple_token_difference"),
    ],
)
def test_normalization_mismatch_reason_is_conservative(
    claim_value: str, candidate_values: tuple[str, ...], expected: str
) -> None:
    diagnostic = _diagnostic(claim_value=claim_value, candidate_values=candidate_values)

    assert diagnostic.match_state == "normalization_mismatch"
    assert diagnostic.mismatch_reason == expected
    assert diagnostic.anchor_exists is True


def test_normalization_mismatch_reason_unknown_fallback_is_safe() -> None:
    diagnostic = _diagnostic(claim_value="SECRET_VALUE45X", candidate_values=("45",))

    assert diagnostic.match_state == "normalization_mismatch"
    assert diagnostic.mismatch_reason == "unknown"
    serialized = diagnostic.model_dump(mode="json")
    assert set(serialized) == {
        "proposal_key",
        "claim_index",
        "classification",
        "match_state",
        "anchor_exists",
        "failure_category",
        "relation_state",
        "mismatch_reason",
        "candidate_count",
        "candidate_classifications",
        "candidate_count_category",
    }
    serialized_text = str(serialized)
    for forbidden in ("SECRET_VALUE45X", "45", "anchor_span_id", "Evidence", "prompt", "response"):
        assert forbidden not in serialized_text


def test_mismatch_reason_is_only_present_for_normalization_mismatch() -> None:
    rejected = _numeric_claim_failure_diagnostic(
        proposal_key="proposal:normalization-diagnostic-001",
        claim_index=0,
        claim_value="1、",
        anchor_span_id=_ANCHOR_ID,
        classified_tokens=(
            _token("1、", NumericTokenClass.LIST_INDEX),
        ),
        selected_span_ids=(_ANCHOR_ID,),
        known_anchor_span_ids={_ANCHOR_ID},
    )
    no_token = _numeric_claim_failure_diagnostic(
        proposal_key="proposal:normalization-diagnostic-001",
        claim_index=0,
        claim_value="99",
        anchor_span_id=_ANCHOR_ID,
        classified_tokens=(),
        selected_span_ids=(_ANCHOR_ID,),
        known_anchor_span_ids={_ANCHOR_ID},
    )

    assert rejected.match_state == "matched_but_rejected"
    assert rejected.mismatch_reason is None
    assert no_token.match_state == "no_assertion_token"
    assert no_token.mismatch_reason is None


def test_validation_outcome_and_numeric_token_audit_remain_unchanged(tmp_path) -> None:
    request, built = _request(tmp_path, "数量为45。")
    report = validate_step2a_response(
        request=request,
        response=_response(request, built, assertion="数量为45。", values=("45.0",)),
    )

    assert report.valid is False
    assert report.errors.count(
        "proposal:numeric-diagnostic-001: numeric claim is not classified as a quantitative value"
    ) == 1
    assert len(report.numeric_token_audit) == 1
    assert len(report.numeric_token_audit[0].tokens) == 1
    assert report.numeric_token_audit[0].tokens[0].classification == NumericTokenClass.QUANTITATIVE_VALUE
    assert len(report.numeric_claim_failure_diagnostics) == 1
    assert report.numeric_claim_failure_diagnostics[0].mismatch_reason == "prefix_suffix_difference"


def test_valid_quantitative_claim_has_no_normalization_diagnostic(tmp_path) -> None:
    request, built = _request(tmp_path, "路基工程共计45题。")
    report = validate_step2a_response(
        request=request,
        response=_response(request, built, assertion="路基工程共计45题。", values=("45题",)),
    )

    assert report.valid is True
    assert report.errors == ()
    assert report.numeric_token_audit[0].tokens[0].classification == NumericTokenClass.QUANTITATIVE_VALUE
    assert report.numeric_claim_failure_diagnostics == ()
