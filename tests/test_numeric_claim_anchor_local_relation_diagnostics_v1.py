"""Offline tests for anchor-local numeric-claim relation diagnostics."""
from __future__ import annotations

from pathlib import Path

from examcard.knowledge.contracts import ContractValidationReport, validate_step2a_response
from examcard.knowledge.contracts.validation import _numeric_claim_failure_diagnostic

from tests.test_numeric_claim_failure_diagnostics_v1 import _request, _response


_ANCHOR_ID = "span:" + "a" * 24


def _diagnostic(*, claim_value: str, anchor_text: str):
    return _numeric_claim_failure_diagnostic(
        proposal_key="proposal:anchor-local-diagnostic-001",
        claim_index=0,
        claim_value=claim_value,
        anchor_span_id=_ANCHOR_ID,
        classified_tokens=(),
        selected_span_ids=(_ANCHOR_ID,),
        known_anchor_span_ids={_ANCHOR_ID},
        anchor_canonical_text=anchor_text,
    )


def test_exact_anchor_quantitative_token_is_reported() -> None:
    diagnostic = _diagnostic(claim_value="45题", anchor_text="数量为45题")

    assert diagnostic.anchor_quantitative_count == 1
    assert diagnostic.anchor_quantitative_count_category == "single"
    assert diagnostic.anchor_relation_state == "exact_anchor_quantitative_token"


def test_claim_wrapping_single_anchor_token_is_reported() -> None:
    diagnostic = _diagnostic(claim_value="数量为45题", anchor_text="数量为45题")

    assert diagnostic.anchor_relation_state == "claim_wraps_single_anchor_token"


def test_claim_subexpression_of_single_anchor_token_is_reported() -> None:
    diagnostic = _diagnostic(claim_value="45", anchor_text="数量为45题")

    assert diagnostic.anchor_relation_state == "claim_is_subexpression_of_single_anchor_token"


def test_multiple_anchor_quantitative_tokens_are_reported() -> None:
    diagnostic = _diagnostic(claim_value="45", anchor_text="数量为45题和30题")

    assert diagnostic.anchor_quantitative_count == 2
    assert diagnostic.anchor_quantitative_count_category == "multiple"
    assert diagnostic.anchor_relation_state == "multiple_anchor_quantitative_tokens"


def test_anchor_without_quantitative_token_is_reported() -> None:
    diagnostic = _diagnostic(claim_value="45", anchor_text="施工准备")

    assert diagnostic.anchor_quantitative_count == 0
    assert diagnostic.anchor_quantitative_count_category == "none"
    assert diagnostic.anchor_relation_state == "no_anchor_quantitative_token"


def test_anchor_local_relation_unknown_fallback_is_safe() -> None:
    diagnostic = _diagnostic(claim_value="99", anchor_text="数量为45题")

    assert diagnostic.anchor_quantitative_count == 1
    assert diagnostic.anchor_quantitative_count_category == "single"
    assert diagnostic.anchor_relation_state == "unknown"


def test_anchor_local_diagnostics_do_not_leak_content() -> None:
    diagnostic = _diagnostic(claim_value="SECRET_CLAIM_VALUE", anchor_text="SECRET_ANCHOR_TEXT 45题")
    serialized = diagnostic.model_dump(mode="json")
    serialized_text = str(serialized)

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
    }
    for forbidden in (
        "SECRET_CLAIM_VALUE",
        "SECRET_ANCHOR_TEXT",
        "45题",
        _ANCHOR_ID,
        "anchor_span_id",
        "Evidence",
        "prompt",
        "response",
    ):
        assert forbidden not in serialized_text


def test_validation_outcome_and_numeric_audit_are_unchanged(tmp_path: Path) -> None:
    request, built = _request(tmp_path, "数量为45题。")
    report = validate_step2a_response(
        request=request,
        response=_response(request, built, assertion="数量为45、", values=("45",)),
    )

    assert report.valid is False
    assert report.errors.count(
        "proposal:numeric-diagnostic-001: numeric claim is not classified as a quantitative value"
    ) == 1
    assert len(report.numeric_token_audit) == 1
    assert report.numeric_claim_failure_diagnostics[0].anchor_quantitative_count == 1
    assert (
        report.numeric_claim_failure_diagnostics[0].anchor_relation_state
        == "claim_is_subexpression_of_single_anchor_token"
    )


def test_anchor_local_sidecar_is_not_in_published_schema() -> None:
    schema = ContractValidationReport.model_json_schema()

    assert "numeric_claim_failure_diagnostics" not in schema["properties"]
    assert "NumericClaimFailureDiagnostic" not in schema.get("$defs", {})


def test_valid_numeric_claim_has_no_anchor_local_diagnostic(tmp_path: Path) -> None:
    request, built = _request(tmp_path, "数量为45题。")
    report = validate_step2a_response(
        request=request,
        response=_response(request, built, assertion="数量为45题。", values=("45题",)),
    )

    assert report.valid is True
    assert report.errors == ()
    assert report.numeric_claim_failure_diagnostics == ()
