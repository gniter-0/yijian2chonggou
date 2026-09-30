"""Offline tests for safe numeric-claim candidate diagnostics."""
from __future__ import annotations

from types import SimpleNamespace

from examcard.knowledge.contracts import ContractValidationReport
from examcard.knowledge.contracts.enums import NumericTokenClass
from examcard.knowledge.contracts.numeric_tokens import NumericTokenClassificationRecord
from examcard.knowledge.contracts.validation import _numeric_claim_failure_diagnostic


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
        proposal_key="proposal:candidate-diagnostic-001",
        claim_index=0,
        claim_value=claim_value,
        anchor_span_id=_ANCHOR_ID,
        classified_tokens=tokens,
        selected_span_ids=(_ANCHOR_ID,),
        known_anchor_span_ids={_ANCHOR_ID},
    )


def test_zero_candidates_are_reported_as_none_without_content() -> None:
    # A malformed internal token object is used only to exercise the defensive
    # zero-candidate category while preserving the existing mismatch branch.
    diagnostic = _diagnostic(
        claim_value="99",
        tokens=(SimpleNamespace(classification=NumericTokenClass.QUANTITATIVE_VALUE, value=None),),
    )

    assert diagnostic.match_state == "normalization_mismatch"
    assert diagnostic.candidate_count == 0
    assert diagnostic.candidate_classifications == ()
    assert diagnostic.candidate_count_category == "none"


def test_single_quantitative_candidate_is_reported_without_value() -> None:
    diagnostic = _diagnostic(claim_value="45.0", tokens=(_token("45"),))

    assert diagnostic.match_state == "normalization_mismatch"
    assert diagnostic.candidate_count == 1
    assert diagnostic.candidate_classifications == ("quantitative_value",)
    assert diagnostic.candidate_count_category == "single"


def test_multiple_quantitative_candidates_are_reported_without_values() -> None:
    diagnostic = _diagnostic(
        claim_value="20/30",
        tokens=(_token("20"), _token("30")),
    )

    assert diagnostic.match_state == "normalization_mismatch"
    assert diagnostic.candidate_count == 2
    assert diagnostic.candidate_classifications == ("quantitative_value", "quantitative_value")
    assert diagnostic.candidate_count_category == "multiple"


def test_candidate_classifications_are_whitelisted() -> None:
    diagnostic = _diagnostic(claim_value="45.0", tokens=(_token("45"),))

    assert set(diagnostic.candidate_classifications or ()) <= {
        "quantitative_value",
        "section_number",
        "list_index",
        "material_grade",
        "identifier",
        "unmatched",
    }


def test_candidate_diagnostics_do_not_leak_sensitive_content() -> None:
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
        "normalized",
        "Evidence",
        "anchor_span_id",
        "prompt",
        "response",
    ):
        assert forbidden not in serialized_text


def test_existing_non_mismatch_diagnostics_have_no_candidate_sidecar() -> None:
    diagnostic = _diagnostic(
        claim_value="1、",
        tokens=(_token("1、", NumericTokenClass.LIST_INDEX),),
    )

    assert diagnostic.match_state == "matched_but_rejected"
    assert diagnostic.candidate_count is None
    assert diagnostic.candidate_classifications is None
    assert diagnostic.candidate_count_category is None
    assert "candidate_count" not in diagnostic.model_dump(mode="json")


def test_valid_claims_have_no_candidate_diagnostics() -> None:
    diagnostic = _diagnostic(claim_value="45", tokens=(_token("45"),))

    # The helper is only called by the existing failure path; an exact match
    # therefore remains a non-mismatch diagnostic and carries no candidates.
    assert diagnostic.match_state == "unknown"
    assert diagnostic.candidate_count is None
    assert diagnostic.candidate_classifications is None
    assert diagnostic.candidate_count_category is None


def test_candidate_sidecar_does_not_change_public_report_schema() -> None:
    schema = ContractValidationReport.model_json_schema()

    assert "numeric_claim_failure_diagnostics" not in schema["properties"]
    assert "NumericClaimFailureDiagnostic" not in schema.get("$defs", {})
