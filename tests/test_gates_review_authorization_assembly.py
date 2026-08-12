"""授权装配半程测试（gates/review/authorization 构造调用，P3 §8.2 v1.3 补搬）。

两层覆盖，全离线（规范 T4）：
1. 合成端到端：提取批（extraction.ExtractionBatch）→ authorize_extraction_batch →
   r810 ReviewedTypedEvidenceFixture（链哈希绑定/摄取重建身份语义/fail-closed 边界）；
2. r810 金标回放（旧仓真实语料，缺席跳过）：用严格摄取重建器从语料导出清单重建
   r84 fixture（对拍 r84_fixture_sha256），再经新 fixture 构造器重建整个 r810
   fixture 逐字节对拍——这正是「全仓无 ReviewedTypedEvidenceFixture 构造调用」
   缺口的构造路径证明。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from examcard.gates.review.authorization import (
    ProductionAuthorizationManifest,
    authorize_extraction_batch,
    build_reviewed_typed_evidence_fixture,
    source_text_snapshot,
    strict_ingest_exported_manifest,
)
from examcard.gates.review.decisions import HumanDecisionManifest
from examcard.gates.review.extraction import (
    extract_reviewed_fixture,
    make_extraction_source,
    make_manual_selection_spec,
)
from examcard.gates.review.queue import validate_queue_chain
from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture

from tests.fixtures.admission_kernel import (
    ANCHOR_IDS,
    EVIDENCE_ID,
    FACT_ID,
    RECORD_ID,
    SOURCE_MANIFEST_SHA256,
    SUBJECT_ID,
    KernelQueueItem,
    make_source_page,
)

LEGACY_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
R810_RUN = LEGACY_ROOT / "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1"


def _batch():
    source = make_extraction_source(
        subject_id=SUBJECT_ID,
        record_id=RECORD_ID,
        fact_id=FACT_ID,
        evidence_id=EVIDENCE_ID,
        presentation="formula",
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        anchor_ids=ANCHOR_IDS,
        pages=(make_source_page(),),
    )
    spec = make_manual_selection_spec(
        {
            "selection_id": "selection:test:auth-0001",
            "subject_id": SUBJECT_ID,
            "record_id": RECORD_ID,
            "fact_id": FACT_ID,
            "evidence_id": EVIDENCE_ID,
            "presentation": "formula",
            "page_ref_id": "page:test:0001",
            "bindings": ({"source_role": "formula_token", "text": "E=mc^2"},),
            "excluded_noise_kinds": ("watermark",),
            "reviewer": "tester",
            "notes": "explicit human source-page selection",
            "recorded_at": "2026-08-13T00:00:00Z",
        }
    )
    return extract_reviewed_fixture((source,), (spec,), identity_period="x1")


class TestAuthorizeExtractionBatch:
    def test_end_to_end_builds_r810_fixture_with_bound_chain(self) -> None:
        batch = _batch()
        bundle = authorize_extraction_batch(
            batch,
            identity_period="x1",
            reviewer="tester",
            reason="explicit production authorization of the extraction batch",
            recorded_at="2026-08-13T00:00:00Z",
        )
        fixture = bundle.fixture
        assert isinstance(fixture, ReviewedTypedEvidenceFixture)
        assert fixture.reviewed_candidate_count == 1
        # 冻结槽位绑定提取面工件（§8.2 引擎期语义）。
        assert fixture.r89_admission_bundle_sha256 == batch.fixture.fixture_sha256
        assert fixture.authorization_manifest_sha256 == bundle.authorization.manifest_sha256
        assert fixture.exported_r84_manifest_sha256 == bundle.exported.manifest_sha256
        assert fixture.r84_fixture_sha256 == bundle.ingested_fixture.fixture_sha256
        assert bundle.authorization.r89_queue_sha256 == batch.queue_sha256
        assert bundle.authorization.r86_freeze_sha256 == batch.manifest.manifest_sha256
        # 队列哈希链贯穿授权决策与导出清单。
        validate_queue_chain(
            batch.queue_sha256,
            (*bundle.authorization.decisions, bundle.exported, *bundle.exported.decisions),
        )
        # 回执钉扎双向哈希。
        assert bundle.receipt.source_manifest_sha256 == bundle.authorization.manifest_sha256
        assert bundle.receipt.exported_manifest_sha256 == bundle.exported.manifest_sha256

    def test_ingested_candidates_bind_exported_decision_identities(self) -> None:
        batch = _batch()
        bundle = authorize_extraction_batch(
            batch,
            identity_period="x1",
            reviewer="tester",
            reason="explicit production authorization of the extraction batch",
            recorded_at="2026-08-13T00:00:00Z",
        )
        original = batch.fixture.candidates[0]
        ingested = bundle.fixture.candidates[0]
        exported_decision = bundle.exported.decisions[0]
        # r810 语料同构：终态候选的 source_decision_id 绑定导出决策（新鲜身份），
        # 契约与源绑定内容与提取期候选一致。
        assert ingested.source_decision_id == exported_decision.decision_id
        assert ingested.source_decision_id != original.source_decision_id
        assert exported_decision.decision_id.startswith("decision:r84:human:r810-")
        assert ingested.typed_contract_sha256 == original.typed_contract_sha256
        assert ingested.candidate_id == original.candidate_id
        assert ingested.anchor_ids == original.anchor_ids

    def test_snapshot_follows_element_storage_order(self) -> None:
        batch = _batch()
        bundle = authorize_extraction_batch(
            batch,
            identity_period="x1",
            reviewer="tester",
            reason="explicit production authorization of the extraction batch",
            recorded_at="2026-08-13T00:00:00Z",
        )
        decision = bundle.authorization.decisions[0]
        assert decision.source_text_snapshot == ("E=mc^2",)
        assert decision.source_text_snapshot == source_text_snapshot(decision.typed_contract)

    def test_batch_without_reviewed_candidates_fails_closed(self) -> None:
        source = make_extraction_source(
            subject_id=SUBJECT_ID,
            record_id=RECORD_ID,
            fact_id=FACT_ID,
            evidence_id=EVIDENCE_ID,
            presentation="formula",
            source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            anchor_ids=ANCHOR_IDS,
            pages=(make_source_page(),),
        )
        empty = extract_reviewed_fixture((source,), (), identity_period="x1")
        with pytest.raises(ValueError, match="no reviewed candidates"):
            authorize_extraction_batch(
                empty,
                identity_period="x1",
                reviewer="tester",
                reason="nothing to authorize",
                recorded_at="2026-08-13T00:00:00Z",
            )

    def test_identity_period_grammar_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="identity period"):
            authorize_extraction_batch(
                _batch(),
                identity_period="X1",
                reviewer="tester",
                reason="bad period grammar must fail",
                recorded_at="2026-08-13T00:00:00Z",
            )


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus() -> dict[str, Any]:
    return {
        "fixture": ReviewedTypedEvidenceFixture.model_validate(
            _read(R810_RUN / "reviewed-typed-evidence-fixture.json")
        ),
        "exported": HumanDecisionManifest.model_validate(
            _read(R810_RUN / "phase2r84-human-decision-manifest.json")
        ),
        "authorization": ProductionAuthorizationManifest.model_validate(
            _read(R810_RUN / "production-decision-manifest.json")
        ),
    }


@pytest.mark.skipif(not R810_RUN.is_dir(), reason="旧仓 r810 reviewed 语料不在本机，跳过金标回放")
class TestR810AuthorizationGoldenReplay:
    """语料导出清单 → 严格摄取重建 → r810 fixture 构造，逐字节对拍生产语料。"""

    @staticmethod
    def _queue_shims(fixture: ReviewedTypedEvidenceFixture) -> list[KernelQueueItem]:
        # 队列序 = 语料 fixture 候选序（r84 摄取按队列迭代；两序不同已被生产语料实证）。
        return [
            KernelQueueItem(
                queue_item_id=candidate.queue_item_id,
                subject_id=candidate.subject_id,
                record_id=candidate.record_id,
                fact_id=candidate.fact_id,
                evidence_id=candidate.evidence_id,
                presentation=candidate.presentation,
                source_manifest_sha256=candidate.source_manifest_sha256,
                anchor_ids=candidate.anchor_ids,
                pages=candidate.pages,
            )
            for candidate in fixture.candidates
        ]

    def test_strict_ingestion_rebuilds_r84_fixture_hash(self, corpus: dict[str, Any]) -> None:
        ingested = strict_ingest_exported_manifest(corpus["exported"], self._queue_shims(corpus["fixture"]))
        assert ingested.reviewed_candidate_count == 6
        assert ingested.fixture_sha256 == corpus["fixture"].r84_fixture_sha256

    def test_fixture_builder_rebuilds_r810_fixture_byte_identical(self, corpus: dict[str, Any]) -> None:
        ingested = strict_ingest_exported_manifest(corpus["exported"], self._queue_shims(corpus["fixture"]))
        rebuilt = build_reviewed_typed_evidence_fixture(
            admission_bundle_sha256=corpus["fixture"].r89_admission_bundle_sha256,
            authorization_manifest_sha256=corpus["fixture"].authorization_manifest_sha256,
            exported_manifest_sha256=corpus["fixture"].exported_r84_manifest_sha256,
            ingested_fixture=ingested,
        )
        assert rebuilt.fixture_sha256 == corpus["fixture"].fixture_sha256
        assert rebuilt.model_dump(mode="json") == corpus["fixture"].model_dump(mode="json")

    def test_authorization_snapshots_match_contract_storage_order(self, corpus: dict[str, Any]) -> None:
        by_candidate = {item.candidate_id: item for item in corpus["authorization"].decisions}
        for candidate in corpus["fixture"].candidates:
            decision = by_candidate[candidate.candidate_id]
            assert decision.source_text_snapshot == source_text_snapshot(candidate.typed_contract)
