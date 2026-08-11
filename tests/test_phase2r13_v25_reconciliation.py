from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from examcard.knowledge.learning_fixture import load_learning_golden_fixture
from examcard.knowledge.contracts.phase2r13_v25_reconciliation import (
    _SOURCE_GROUPING_PATH,
    _TABLE_REPAIR_PATHS,
    _TARGET_REVIEWED_FIXTURE,
    _TARGET_SEMANTIC_SIDECAR,
    _WATERMARK_REVIEW_PATH,
    V25SourceGroupingReviewSidecar,
    V25WatermarkDisposition,
    V25WatermarkReviewSidecar,
    build_phase2r13_report,
)
from examcard.knowledge.continuation.repair_sidecar import load_table_repair_sidecar, verify_reviewed_table_repair


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_phase2r13_reconciles_all_five_samples_without_network_or_legacy_mutation():
    frozen = (ROOT / _TARGET_REVIEWED_FIXTURE, ROOT / _TARGET_SEMANTIC_SIDECAR)
    before = {path: _sha256(path) for path in frozen}
    report = build_phase2r13_report(repository_root=ROOT, generated_at_utc="2026-07-29T18:00:00Z")
    assert report.accepted is True
    assert report.network_calls == report.real_step2a_requests == report.real_step2b_requests == 0
    assert report.source_mutations == 0
    assert report.frozen_inputs_unchanged is True
    assert report.semantic_diagram_review_bridge_count == 5
    assert report.semantic_diagram_fact_summary_count == 4
    assert report.semantic_diagram_evidence_only_count == 1
    assert len(report.semantic_diagram_bridges) == 5
    assert {path: _sha256(path) for path in frozen} == before
    assert len(report.samples) == 5
    assert all(item.v25_step2a_gate.valid and item.v25b_grouping_gate.valid for item in report.samples)
    assert all(item.reviewed_card_drift_count == item.unresolved_watermark_count == 0 for item in report.samples)
    assert all(item.teaching_gate.publishable for item in report.samples)


def test_phase2r13_repairs_only_the_two_reviewed_tables_and_admits_the_held_facts():
    report = build_phase2r13_report(repository_root=ROOT, generated_at_utc="2026-07-29T18:00:00Z")
    expected = {
        "gonglu_24_table_formula": ("formwork.arch-removal",),
        "gonglu_41_diagram_mixed": ("tunnel.span-classification",),
    }
    for sample_id, fact_keys in expected.items():
        audit = next(item for item in report.samples if item.sample_id == sample_id)
        assert audit.eligible_repaired_fact_keys == fact_keys
    for (sample_id, page_num), relative in _TABLE_REPAIR_PATHS.items():
        sidecar = load_table_repair_sidecar(ROOT / relative)
        source = ROOT / sidecar.source_manifest_path
        from examcard.evidence.schema import PageEvidence

        page = PageEvidence.model_validate_json(source.read_bytes())
        overrides = verify_reviewed_table_repair(sidecar=sidecar, page=page, source_manifest_path=source, repository_root=ROOT)
        assert len(overrides) == len(sidecar.cells)
        assert sidecar.sample_id == sample_id and sidecar.page_num == page_num


def test_phase2r13_preserves_semantic_diagram_evidence_and_keeps_unreviewed_watermarks_fail_closed():
    report = build_phase2r13_report(repository_root=ROOT, generated_at_utc="2026-07-29T18:00:00Z")
    assert sum(item.semantic_diagram_bridge_count for item in report.samples) == 4
    # The V25 fixture contains five reviewed diagrams, but the page-1 load-layout
    # diagram has no reviewed Fact target and therefore remains evidence-only.
    fixture = load_learning_golden_fixture(ROOT / _TARGET_REVIEWED_FIXTURE)
    assert sum(len(sample.diagrams) for sample in fixture.samples) == 5
    evidence_only = [item for item in report.semantic_diagram_bridges if item.disposition.value == "evidence_only"]
    assert len(evidence_only) == 1
    assert evidence_only[0].sample_id == "gonglu_24_table_formula"
    assert evidence_only[0].fact_key is None
    assert evidence_only[0].bridge.figure_evidence_id == "ev:ef46c363269d2e99c929b38c"
    watermarks = V25WatermarkReviewSidecar.model_validate(yaml.safe_load((ROOT / _WATERMARK_REVIEW_PATH).read_text(encoding="utf-8")))
    assert len(watermarks.reviews) == 3
    assert all(item.disposition == V25WatermarkDisposition.EXCLUDED_AS_WATERMARK for item in watermarks.reviews)
    grouping_text = (ROOT / _SOURCE_GROUPING_PATH).read_text(encoding="utf-8")
    grouping = V25SourceGroupingReviewSidecar.model_validate(yaml.safe_load(grouping_text))
    assert len(grouping.reviews) == 6
    assert "card_key" not in grouping_text and "title:" not in grouping_text and "presentation" not in grouping_text
