"""历史 reviewed 语料回放：新仓内核加载旧仓真实 run 产物并重跑全部自哈希/绑定校验。

旧仓 yijian2 全程只读（组公告路径铁律）；语料缺席时整组跳过（不阻塞他机 CI）。
覆盖三条链：r84（人工决策→审计→fixture）、r810（显式授权→r84 导出→守恒投影→fixture）、
r11101（图片目检→授权清单→reviewed image fixture），另含 r104 fixture（admission_fixtures 侧）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from examcard.gates.review.audit import BrowserInputAudit, DecisionAudit
from examcard.gates.review.authorization import (
    AuthorizationAudit,
    DispositionProjection,
    PreservedDispositionQueue,
    ProductionAuthorizationManifest as R810AuthorizationManifest,
    R84ExportReceipt,
    export_r84_manifest,
)
from examcard.gates.review.decisions import HumanDecisionManifest
from examcard.gates.review.fixtures import ReviewedFixture, ReviewedTypedEvidenceFixture
from examcard.gates.review.image_admission import (
    AdmissionAuditSession,
    PostReviewItem,
    ProductionAuthorizationManifest as ImageAuthorizationManifest,
    ReviewedImageFixture,
    SourceImageBinding,
    admit_images,
)
from examcard.gates.review.queue import ManualReviewQueue
from examcard.knowledge.contracts.admission_fixtures import ReviewedStep3Fixture

LEGACY_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
R84_RUN = LEGACY_ROOT / "data/semantic_llm_v2/phase2r84_selected_batch_human_decision_ingestion_run1"
R810_RUN = LEGACY_ROOT / "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1"
R104_RUN = LEGACY_ROOT / "data/semantic_llm_v2/phase2r104_step3_explicit_production_authorization_run1"
R11101_RUN = LEGACY_ROOT / "data/semantic_llm_v11101_step4_human_visual_review_strict_admission_run1"

pytestmark = pytest.mark.skipif(not R84_RUN.is_dir(), reason="旧仓 reviewed 语料不在本机，跳过回放")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for child in value.values():
            if isinstance(child, list):
                return child
    raise AssertionError("unrecognized corpus payload shape")


class TestR84Chain:
    def test_human_decision_manifest_replays(self) -> None:
        # r84 run1 是空清单期（零人工决策）；带决策的 r84 形态清单由 r810 链的
        # phase2r84-human-decision-manifest.json 覆盖（见 TestR810Chain 黄金回放）。
        manifest = HumanDecisionManifest.model_validate(_read(R84_RUN / "human-decision-manifest.json"))
        assert manifest.decision_count == len(manifest.decisions)

    def test_decision_audits_replay(self) -> None:
        audits = [DecisionAudit.model_validate(item) for item in _items(_read(R84_RUN / "decision-audits.json"))]
        assert audits
        assert all(not (item.browser_demo and item.effective_disposition == "reviewed_candidate") for item in audits)

    def test_reviewed_fixture_replays_and_binds_manifest_queue(self) -> None:
        fixture = ReviewedFixture.model_validate(_read(R84_RUN / "reviewed-typed-evidence-fixture.json"))
        manifest = HumanDecisionManifest.model_validate(_read(R84_RUN / "human-decision-manifest.json"))
        assert fixture.queue_sha256 == manifest.queue_sha256
        assert fixture.reviewed_candidate_count == len(fixture.candidates)

    def test_manual_review_queue_replays(self) -> None:
        queue = ManualReviewQueue.model_validate(_read(R84_RUN / "manual-review-queue.json"))
        assert queue.queue_count == len(queue.candidates)

    def test_browser_input_audit_replays(self) -> None:
        audit = BrowserInputAudit.model_validate(_read(R84_RUN / "browser-input-audit.json"))
        assert audit.production_authorized_count == 0


@pytest.mark.skipif(not R810_RUN.is_dir(), reason="r810 语料缺席")
class TestR810Chain:
    def test_authorization_manifest_replays(self) -> None:
        manifest = R810AuthorizationManifest.model_validate(_read(R810_RUN / "production-decision-manifest.json"))
        assert manifest.reviewed_candidate_count == manifest.decision_count

    def test_export_r84_manifest_golden_replay(self) -> None:
        manifest = R810AuthorizationManifest.model_validate(_read(R810_RUN / "production-decision-manifest.json"))
        exported, receipt = export_r84_manifest(manifest)
        corpus_exported = HumanDecisionManifest.model_validate(_read(R810_RUN / "phase2r84-human-decision-manifest.json"))
        corpus_receipt = R84ExportReceipt.model_validate(_read(R810_RUN / "r84-export-receipt.json"))
        assert exported.manifest_id == corpus_exported.manifest_id
        assert exported.manifest_sha256 == corpus_exported.manifest_sha256
        assert receipt.receipt_sha256 == corpus_receipt.receipt_sha256

    def test_disposition_projection_and_preserved_queues_replay(self) -> None:
        projection = DispositionProjection.model_validate(_read(R810_RUN / "disposition-projection.json"))
        assert projection.candidate_count == len(projection.candidates)
        for name in ("manual-review-queue.json", "not-applicable-queue.json"):
            queue = PreservedDispositionQueue.model_validate(_read(R810_RUN / name))
            assert queue.queue_count == len(queue.candidates)

    def test_authorization_audits_replay(self) -> None:
        audits = [AuthorizationAudit.model_validate(item) for item in _items(_read(R810_RUN / "decision-audit.json"))]
        assert audits

    def test_reviewed_typed_evidence_fixture_replays(self) -> None:
        fixture = ReviewedTypedEvidenceFixture.model_validate(_read(R810_RUN / "reviewed-typed-evidence-fixture.json"))
        assert fixture.reviewed_candidate_count == len(fixture.candidates) > 0


@pytest.mark.skipif(not R104_RUN.is_dir(), reason="r104 语料缺席")
class TestR104Chain:
    def test_reviewed_step3_fixture_replays(self) -> None:
        fixture = ReviewedStep3Fixture.model_validate(_read(R104_RUN / "reviewed-step3-fixture.json"))
        assert fixture.cards


@pytest.mark.skipif(not R11101_RUN.is_dir(), reason="r11101 语料缺席")
class TestR11101Chain:
    def test_image_authorization_manifest_replays(self) -> None:
        manifest = ImageAuthorizationManifest.model_validate(_read(R11101_RUN / "production-human-decision-manifest.json"))
        assert manifest.decision_count == 3

    def test_reviewed_image_fixture_replays(self) -> None:
        fixture = ReviewedImageFixture.model_validate(_read(R11101_RUN / "reviewed-image-fixture.json"))
        assert fixture.reviewed_card_count == len(fixture.cards)

    def test_admission_audit_session_replays(self) -> None:
        session = AdmissionAuditSession.model_validate(_read(R11101_RUN / "admission-audit.json"))
        assert session.admitted_count + session.manual_review_count + session.rejected_count == 3

    def test_post_review_queue_replays(self) -> None:
        items = [PostReviewItem.model_validate(item) for item in _items(_read(R11101_RUN / "post-review-queue.json"))]
        assert all(item.disposition in ("rejected", "manual_review") for item in items)

    def test_admit_images_golden_rebuild_matches_corpus_fixture(self) -> None:
        freeze_payload = _read(R11101_RUN / "input-freeze.json")
        bindings = tuple(SourceImageBinding.model_validate(item) for item in freeze_payload["bindings"])
        manifest = ImageAuthorizationManifest.model_validate(_read(R11101_RUN / "production-human-decision-manifest.json"))
        corpus_fixture = ReviewedImageFixture.model_validate(_read(R11101_RUN / "reviewed-image-fixture.json"))
        rebuilt, queue, session = admit_images(manifest, bindings, freeze_payload["freeze_sha256"])
        assert rebuilt is not None
        assert rebuilt.fixture_sha256 == corpus_fixture.fixture_sha256
        corpus_session = AdmissionAuditSession.model_validate(_read(R11101_RUN / "admission-audit.json"))
        assert session.audit_sha256 == corpus_session.audit_sha256
        corpus_queue = [PostReviewItem.model_validate(item) for item in _items(_read(R11101_RUN / "post-review-queue.json"))]
        assert [item.item_sha256 for item in queue] == [item.item_sha256 for item in corpus_queue]
