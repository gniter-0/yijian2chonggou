"""Offline tests for the source-owned arbitrary-PDF vertical slice runner."""
from __future__ import annotations

import json

import pytest
from migration.run_source_owned_vertical_slice_v1 import (
    DEFAULT_EVIDENCE_FIXTURE,
    SourceOwnedVerticalSliceSummary,
    run_source_owned_vertical_slice,
)


def test_successful_offline_vertical_slice_reaches_knowledge_card() -> None:
    summary = run_source_owned_vertical_slice()

    assert isinstance(summary, SourceOwnedVerticalSliceSummary)
    assert summary.extraction_status == "extracted"
    assert summary.anchor_selection_status == "selected"
    assert summary.derivation_status == "derived"
    assert summary.assembly_status == "assembled"
    assert summary.admission_status == "admitted"
    assert summary.bridge_status == "bridged"
    assert summary.materialization_status == "materialized"
    assert summary.knowledge_card_id.startswith("card:")


@pytest.mark.parametrize(
    ("scenario", "expected_selection", "expected_derivation", "expected_assembly"),
    (
        ("missing_anchor", "failed_closed", "failed_closed", "failed_closed"),
        ("numeric_derivation_failure", "selected", "failed_closed", "failed_closed"),
    ),
)
def test_upstream_failures_fail_closed_without_downstream_admission(
    scenario: str,
    expected_selection: str,
    expected_derivation: str,
    expected_assembly: str,
) -> None:
    summary = run_source_owned_vertical_slice(scenario=scenario)  # type: ignore[arg-type]

    assert summary.anchor_selection_status == expected_selection
    assert summary.derivation_status == expected_derivation
    assert summary.assembly_status == expected_assembly
    assert summary.admission_status == "rejected"
    assert summary.bridge_status == "failed_closed"
    assert summary.materialization_status == "failed_closed"
    assert summary.knowledge_card_id == ""


@pytest.mark.parametrize("scenario", ("identity_mismatch", "incomplete_materialization"))
def test_materialization_boundary_failures_are_reported_without_card(
    scenario: str,
) -> None:
    summary = run_source_owned_vertical_slice(scenario=scenario)  # type: ignore[arg-type]

    assert summary.extraction_status == "extracted"
    assert summary.anchor_selection_status == "selected"
    assert summary.derivation_status == "derived"
    assert summary.assembly_status == "assembled"
    assert summary.admission_status == "admitted"
    assert summary.bridge_status == "bridged"
    assert summary.materialization_status == "failed_closed"
    assert summary.knowledge_card_id == ""


def test_runner_uses_existing_evidence_fixture() -> None:
    assert DEFAULT_EVIDENCE_FIXTURE.is_file()
    summary = run_source_owned_vertical_slice(evidence_fixture=DEFAULT_EVIDENCE_FIXTURE)

    assert summary.materialization_status == "materialized"


def test_summary_is_typed_and_contains_only_safe_stage_fields() -> None:
    summary = run_source_owned_vertical_slice()
    dumped = summary.model_dump(mode="json")
    serialized = json.dumps(dumped, sort_keys=True)

    assert set(dumped) == {
        "extraction_status",
        "anchor_selection_status",
        "derivation_status",
        "assembly_status",
        "admission_status",
        "bridge_status",
        "materialization_status",
        "knowledge_card_id",
    }
    for forbidden in (
        "raw evidence",
        "canonical_text",
        "prompt",
        "provider",
        "response",
        "secret",
        "credential",
        "assertion",
    ):
        assert forbidden not in serialized.lower()


def test_runner_rejects_unknown_scenario() -> None:
    with pytest.raises(ValueError, match="unsupported source-owned vertical slice scenario"):
        run_source_owned_vertical_slice(scenario="not_a_scenario")  # type: ignore[arg-type]
