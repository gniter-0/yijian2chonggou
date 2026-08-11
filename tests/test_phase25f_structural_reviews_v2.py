"""Acceptance and fail-closed regression tests for Phase 2.5F structural reviews."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

from examcard.knowledge.diagram.disposition import DiagramDisposition, load_diagram_disposition_sidecar
from examcard.knowledge.diagram.sidecar import DiagramReviewState, load_diagram_review_sidecar
from examcard.knowledge.fact_engine import run_golden_fact_pipeline
from examcard.knowledge.schema import GroundingStatus, Renderability


REGISTRY_PATH = ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
EVIDENCE_ROOT = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1"
ANNOTATION_SIDECAR = ROOT / "scripts/tests/fixtures/v2/golden_annotations/gonglu_phase0_v1.yaml"
POLICY_PATH = ROOT / "configs/v2/content_policies/core_only_v1.yaml"
REVIEW_SIDECAR = ROOT / "configs/v2/diagram_review_sidecars/gonglu_phase0_v1.phase25f_structural_reviewed.yaml"
DISPOSITION_SIDECAR = ROOT / "configs/v2/diagram_disposition_sidecars/gonglu_phase0_v1.phase25f_remaining_audited.yaml"
DECISIONS_PATH = ROOT / "configs/v2/diagram_review_sidecars/gonglu_phase0_v1.phase25f_structural_decisions.yaml"


def _run(tmp_path: Path, *, review_sidecar: Path = REVIEW_SIDECAR, disposition_sidecar: Path = DISPOSITION_SIDECAR) -> dict[str, object]:
    return run_golden_fact_pipeline(
        registry_path=REGISTRY_PATH,
        sidecar_path=ANNOTATION_SIDECAR,
        content_policy_path=POLICY_PATH,
        evidence_root=EVIDENCE_ROOT,
        output_root=tmp_path / "facts",
        diagram_review_sidecar_path=review_sidecar,
        diagram_disposition_sidecar_path=disposition_sidecar,
    )


def test_phase25f_releases_all_reviewed_structural_diagrams_and_closes_diagram_gates(tmp_path: Path):
    review = load_diagram_review_sidecar(REVIEW_SIDECAR)
    disposition = load_diagram_disposition_sidecar(DISPOSITION_SIDECAR)
    decisions = yaml.safe_load(DECISIONS_PATH.read_text(encoding="utf-8"))
    assert isinstance(decisions, dict)
    decision_ids = {entry["review_id"] for entry in decisions["decisions"]}

    assert len(review.entries) == 23
    assert sum(entry.state == DiagramReviewState.REVIEWED for entry in review.entries) == 17
    promoted = [entry for entry in review.entries if entry.review_id in decision_ids]
    assert len(promoted) == 13
    assert all(entry.state == DiagramReviewState.REVIEWED for entry in promoted)
    assert all(entry.audit is not None and entry.audit.review_asset is not None for entry in promoted)
    assert all(entry.audit is not None and entry.audit.review_crop_manifest is not None for entry in promoted)
    assert all(entry.audit is not None and entry.audit.review_asset.render_dpi == 600 for entry in promoted)
    assert all(entry.figure_evidence_id in node.evidence_ids for entry in promoted for node in entry.nodes)

    remaining_drafts = {entry.review_id for entry in review.entries if entry.state == DiagramReviewState.DRAFT}
    assert {entry.review_id for entry in disposition.entries} == remaining_drafts
    assert len(disposition.entries) == 6
    assert all(entry.disposition == DiagramDisposition.PHOTO_EXAMPLE for entry in disposition.entries)

    result = _run(tmp_path)
    assert result["quality_gates_passed"] is True
    assert result["machine_assertions_passed"] is True
    assert result["diagram_reviewed_entries"] == 17
    assert result["diagram_disposition_entries"] == 6
    assert all(sample["quality_gate"]["unrenderable_diagrams"] == 0 for sample in result["samples"])

    cards = [card for sample in result["samples"] for card in sample["cards"]]
    structural_cards = [card for card in cards if card["diagram_review_id"] in decision_ids]
    assert len(structural_cards) == 13
    assert all(card["grounding_status"] == GroundingStatus.MANUAL_VERIFIED.value for card in structural_cards)
    assert all(card["renderability"] == Renderability.PUBLISHABLE.value for card in structural_cards)


def test_phase25f_highres_review_crop_hash_mismatch_fails_closed_before_fact_generation(tmp_path: Path):
    payload = yaml.safe_load(REVIEW_SIDECAR.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    entry = next(item for item in payload["entries"] if item["review_id"] == "diagram:1e2f8c831d4edb33e43dfd80")
    entry["audit"]["review_asset"]["review_crop_sha256"] = "0" * 64
    bad_sidecar = tmp_path / "bad_phase25f_review.yaml"
    bad_sidecar.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="high-resolution binding disagrees with manifest"):
        _run(tmp_path, review_sidecar=bad_sidecar)
