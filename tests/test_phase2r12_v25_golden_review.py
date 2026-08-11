from __future__ import annotations

import hashlib
from pathlib import Path

from examcard.knowledge.contracts.phase2r12_v25_golden_review import (
    _SOURCE_DIAGRAM_SIDECAR,
    _SOURCE_FIXTURE,
    _TARGET_CONTINUATION_SIDECAR,
    _TARGET_REVIEWED_FIXTURE,
    _TARGET_SEMANTIC_SIDECAR,
    build_phase2r12_report,
)
from examcard.knowledge.continuation.review import load_continuation_review_sidecar
from examcard.knowledge.diagram.sidecar import DiagramSemanticContractVersion, load_diagram_review_sidecar
from examcard.knowledge.learning_fixture import load_learning_golden_fixture
import pytest

# T5 金样镜像守护：镜像缺席（冷克隆）时整模块优雅跳过，镜像在场则正常执行。
_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_MIRROR_REQUIRED = (
    _REPO_ROOT / "data/evidence_v2/golden/gonglu_phase0_v1",
)
pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in _GOLDEN_MIRROR_REQUIRED),
    reason="金样镜像缺席（T5/骨架§3：data/ 等 gitignore 大资产不入库，冷克隆合法跳过）",
)



ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_phase2r12_reviews_exactly_the_39_v25_draft_work_items_without_source_mutation():
    source_paths = (ROOT / _SOURCE_FIXTURE, ROOT / _SOURCE_DIAGRAM_SIDECAR)
    before = {path: _sha256(path) for path in source_paths}
    report = build_phase2r12_report(repository_root=ROOT, generated_at_utc="2026-07-29T15:00:00Z")
    assert report.accepted is True
    assert report.queue_entry_count == 39
    assert report.reviewed_queue_entry_count == 39
    assert report.retained_draft_queue_entry_count == 0
    assert len(report.decisions) == 39
    assert report.frozen_legacy_inputs_unchanged is True
    assert report.network_calls == report.real_step2a_requests == report.real_step2b_requests == 0
    assert {path: _sha256(path) for path in source_paths} == before


def test_phase2r12_reviewed_fixture_and_semantic_sidecar_are_independent_and_complete():
    build_phase2r12_report(repository_root=ROOT, generated_at_utc="2026-07-29T15:00:00Z")
    fixture = load_learning_golden_fixture(ROOT / _TARGET_REVIEWED_FIXTURE)
    sidecar = load_diagram_review_sidecar(ROOT / _TARGET_SEMANTIC_SIDECAR)
    assert fixture.fixture_id == "gonglu_phase2r12_v25_reviewed"
    assert fixture.ready_for_phase2r1 is True
    assert len(sidecar.entries) == 5
    assert all(item.semantic_contract_version == DiagramSemanticContractVersion.SEMANTIC_V2 for item in sidecar.entries)
    assert all(edge.has_semantic_evidence for item in sidecar.entries for edge in item.edges)


def test_phase2r12_v25_continuation_binds_raw_page1_and_adjacent_page2_with_source_and_closure_anchors():
    build_phase2r12_report(repository_root=ROOT, generated_at_utc="2026-07-29T15:00:00Z")
    sidecar = load_continuation_review_sidecar(ROOT / _TARGET_CONTINUATION_SIDECAR)
    marker = sidecar.marker_reviews[0]
    closure = sidecar.closure_reviews[0]
    assert sidecar.sample_id == "gonglu_24_table_formula"
    assert marker.page_num == 1 and closure.adjacent_page_num == 2
    assert marker.source_manifest_sha256 != closure.adjacent_manifest_sha256
    assert len(marker.marker_token_span_ids) == 2
    assert len(closure.closure_anchor_span_ids) == 6


def test_phase2r12_five_sample_v25_and_v25b_offline_gates_pass_but_card_reconciliation_is_reported():
    report = build_phase2r12_report(repository_root=ROOT, generated_at_utc="2026-07-29T15:00:00Z")
    assert len(report.samples) == 5
    assert all(item.v25_step2a_gate.valid for item in report.samples)
    assert all(item.v25b_grouping_gate.valid for item in report.samples)
    assert all(item.learning_reconciliation_valid for item in report.samples)
    assert sum(item.semantic_v2_diagram_count for item in report.samples) == 5
    assert next(item for item in report.samples if item.sample_id == "gonglu_24_table_formula").reviewed_continuation_binding_count == 1
