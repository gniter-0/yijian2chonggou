"""Regression coverage for the draft, reviewable Phase 2R learning fixture."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.golden import load_golden_registry
from examcard.knowledge.learning_fixture import LearningReviewState, load_learning_golden_fixture, validate_learning_fixture


REGISTRY_PATH = ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
FIXTURE_PATH = ROOT / "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r0_draft.yaml"
EVIDENCE_ROOT = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1"
DIAGRAM_SIDECAR = ROOT / "configs/v2/diagram_review_sidecars/gonglu_phase0_v1.phase25f_structural_reviewed.yaml"


def test_learning_fixture_is_grounded_in_existing_evidence_and_reviewed_diagrams():
    fixture = load_learning_golden_fixture(FIXTURE_PATH)
    registry = load_golden_registry(REGISTRY_PATH)

    report = validate_learning_fixture(
        fixture=fixture,
        registry=registry,
        evidence_root=EVIDENCE_ROOT,
        diagram_sidecar_path=DIAGRAM_SIDECAR,
    )

    assert report["valid"] is True
    assert report["ready_for_phase2r1"] is False
    assert {sample["sample_id"] for sample in report["samples"]} == {sample.sample_id for sample in registry.samples}
    assert sum(len(sample["pending_review_entries"]) for sample in report["samples"]) == 41
    assert all(not sample["errors"] for sample in report["samples"])


def test_learning_fixture_keeps_every_target_in_draft_and_exposes_review_entries():
    fixture = load_learning_golden_fixture(FIXTURE_PATH)

    assert fixture.ready_for_phase2r1 is False
    assert len(fixture.pending_review_entries) == 41
    assert all(
        review.state == LearningReviewState.DRAFT
        for sample in fixture.samples
        for review in (
            *(fact.review for fact in sample.facts),
            *(card.review for card in sample.cards),
            *(diagram.review for diagram in sample.diagrams),
        )
    )
