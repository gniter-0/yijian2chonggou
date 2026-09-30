"""Offline tests for safe numeric-claim relation diagnostics."""
from __future__ import annotations

from pathlib import Path

from examcard.knowledge.contracts import ContractValidationReport, validate_step2a_response
from examcard.knowledge.contracts.enums import NumericTokenClass
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


def _diagnostic(*, claim_value: str, tokens: tuple[object, ...]):
    return _numeric_claim_failure_diagnostic(
        proposal_key="proposal:relation-diagnostic-001",
        claim_index=0,
        claim_value=claim_value,
        anchor_span_id=_ANCHOR_ID,
        classified_tokens=tokens,
        selected_span_ids=(_ANCHOR_ID,),
        known_anchor_span_ids={_ANCHOR_ID},
    )


def test_single_candidate_relation_is_reported() -> None:
    diagnostic = _diagnostic(claim_value="45.0", tokens=(_token("45"),))

    assert diagnostic.relation_state == "single_candidate_available"
    assert diagnostic.candidate_count == 1


def test_multiple_candidate_relation_is_reported() -> None:
    diagnostic = _diagnostic(
        claim_value="20/30",
        tokens=(_token("20"), _token("30")),
    )

    assert diagnostic.relation_state == "multiple_candidates_available"
    assert diagnostic.candidate_count == 2


def test_relation_reuses_deduplicated_quantitative_candidate_set() -> None:
    diagnostic = _diagnostic(
        claim_value="45.0",
        tokens=(_token("45"), _token("45")),
    )

    assert diagnostic.relation_state == "single_candidate_available"
    assert diagnostic.candidate_count == 1


def test_rejected_candidate_relation_has_priority() -> None:
    diagnostic = _diagnostic(
        claim_value="1、",
        tokens=(_token("1、", NumericTokenClass.LIST_INDEX),),
    )

    assert diagnostic.match_state == "matched_but_rejected"
    assert diagnostic.relation_state == "rejected_candidate_available"
    assert diagnostic.candidate_count is None


def test_no_candidate_relation_is_reported() -> None:
    diagnostic = _diagnostic(claim_value="45", tokens=())

    assert diagnostic.match_state == "no_assertion_token"
    assert diagnostic.relation_state == "no_candidate_available"


def test_unknown_relation_fallback_is_safe() -> None:
    diagnostic = _diagnostic(claim_value="45", tokens=(_token("45"),))

    assert diagnostic.relation_state == "unknown"
    assert "relation_state" not in diagnostic.model_dump(mode="json")


def test_relation_state_is_whitelisted() -> None:
    allowed = {
        "single_candidate_available",
        "multiple_candidates_available",
        "no_candidate_available",
        "rejected_candidate_available",
        "unknown",
    }

    for diagnostic in (
        _diagnostic(claim_value="45.0", tokens=(_token("45"),)),
        _diagnostic(claim_value="20/30", tokens=(_token("20"), _token("30"))),
        _diagnostic(claim_value="1、", tokens=(_token("1、", NumericTokenClass.LIST_INDEX),)),
        _diagnostic(claim_value="45", tokens=()),
        _diagnostic(claim_value="45", tokens=(_token("45"),)),
    ):
        assert diagnostic.relation_state in allowed


def test_relation_diagnostic_does_not_leak_sensitive_fields() -> None:
    diagnostic = _diagnostic(
        claim_value="SECRET_CLAIM_VALUE",
        tokens=(_token("45"),),
    )
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
        "mismatch_reason",
        "candidate_count",
        "candidate_classifications",
        "candidate_count_category",
    }
    for forbidden in (
        "SECRET_CLAIM_VALUE",
        "45",
        "Evidence",
        "anchor_span_id",
        "prompt",
        "response",
    ):
        assert forbidden not in serialized_text


def test_relation_diagnostics_preserve_validation_outcome_and_token_audit(tmp_path: Path) -> None:
    request, built = _request(tmp_path, "数量为45。")
    report = validate_step2a_response(
        request=request,
        response=_response(request, built, assertion="数量为45。", values=("44",)),
    )

    assert report.valid is False
    assert report.errors.count(
        "proposal:numeric-diagnostic-001: numeric claim is not classified as a quantitative value"
    ) == 1
    assert len(report.numeric_token_audit) == 1
    assert report.numeric_token_audit[0].tokens[0].classification == NumericTokenClass.QUANTITATIVE_VALUE
    assert report.numeric_claim_failure_diagnostics[0].relation_state == "single_candidate_available"


def test_relation_sidecar_remains_outside_public_schema() -> None:
    schema = ContractValidationReport.model_json_schema()

    assert "numeric_claim_failure_diagnostics" not in schema["properties"]
    assert "NumericClaimFailureDiagnostic" not in schema.get("$defs", {})


def test_valid_numeric_claim_has_no_relation_diagnostic(tmp_path: Path) -> None:
    request, built = _request(tmp_path, "数量为45题。")
    report = validate_step2a_response(
        request=request,
        response=_response(request, built, assertion="数量为45题。", values=("45题",)),
    )

    assert report.valid is True
    assert report.errors == ()
    assert report.numeric_claim_failure_diagnostics == ()
