"""r89/r810 授权装配半程·独立验证面（task-1391f3f2，总指挥裁决 A）。

角色分工（20260813 02:3x 裁决）：装配实现 = yijian2-4（authorization.py 装配
半程 + test_gates_review_authorization_assembly.py）；本文件 = 独立复核（_replay
后缀防撞名），**不依赖实现方测试**，两层独立验证：

- 层 A（仅既有内核，零新增 API）：旧仓 r810 生产语料 run1 的
  production-decision-manifest → 迁移模型再验证（全封印重算）→ export_r84_manifest
  重建 r84 清单+回执 → 内核严格摄取（validate_human_decision +
  build_reviewed_candidate/build_reviewed_fixture）内联重建 ingested fixture →
  ReviewedTypedEvidenceFixture 终态重建；四级产物与语料**逐字节对拍**（dump 全等
  + 封印哈希全等），并对拍 r84-ingestion-closure / r89-input-reference /
  prior-freeze 的链路收口哈希。语料缺席整组 skip（冷克隆纪律）。
- 层 B（实现方新 API，落库前 import 缺席则 skip）：strict_ingest_exported_manifest /
  build_reviewed_typed_evidence_fixture 在金标语料上必须与层 A 内联链逐字节同构；
  authorize_extraction_batch 合成端到端（提取批 → 授权 → r810 fixture）：冻结槽位
  绑定语义（模块 docstring 五槽位）、身份前缀/期隔离、处置守恒、fail-closed 边界。

哈希域纪律：r8x/r810 家族一律 admission_legacy_hash 谱系（铁律 bc-b8b9aca6），
本文件不使用 common 域封印任何 r8x 对象。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from examcard.gates.review.authorization import (
    AuthorizationAudit,
    DispositionProjection,
    PreservedDispositionQueue,
    ProductionAuthorizationManifest,
    assert_no_silent_upgrade,
    export_r84_manifest,
)
from examcard.gates.review.decisions import (
    HumanDecision,
    HumanDecisionManifest,
    validate_human_decision,
)
from examcard.gates.review.extraction import (
    ROLE_BY_PRESENTATION,
    extract_reviewed_fixture,
    make_extraction_source,
    make_manual_selection_spec,
)
from examcard.gates.review.fixtures import (
    ReviewedFixture,
    build_reviewed_candidate,
    build_reviewed_fixture,
)
from examcard.knowledge.contracts.admission_fixtures import (
    ReviewedTypedEvidenceFixture,
    SourcePageBinding,
)
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x

from tests.fixtures.admission_kernel import (
    ANCHOR_IDS,
    EVIDENCE_ID,
    FACT_ID,
    RECORD_ID,
    SOURCE_MANIFEST_SHA256,
    SUBJECT_ID,
    make_source_page,
)

# 实现方新 API（uncommitted 期间可能尚未就位/更名；缺席只跳层 B，不阻层 A）。
try:
    from examcard.gates.review.authorization import (
        authorize_extraction_batch,
        build_reviewed_typed_evidence_fixture,
        source_text_snapshot,
        strict_ingest_exported_manifest,
    )

    _ASSEMBLY_API = True
except ImportError:  # pragma: no cover - 落库后恒 True
    _ASSEMBLY_API = False

LEGACY_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
R810_RUN = LEGACY_ROOT / "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1"
R89_RUN = LEGACY_ROOT / "data/semantic_llm_v2/phase2r89_production_raw_draft_source_page_closure_run1"

requires_corpus = pytest.mark.skipif(
    not (R810_RUN.is_dir() and R89_RUN.is_dir()), reason="旧仓 r810/r89 生产语料不在本机，跳过金标对拍"
)
requires_assembly = pytest.mark.skipif(not _ASSEMBLY_API, reason="装配半程 API 尚未落库（实现方在飞）")


def _read(name: str) -> Any:
    return json.loads((R810_RUN / name).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class _QueueStub:
    """严格摄取的队列侧鸭子类型（字段集 = validate_human_decision + 契约绑定走查所需）。"""

    queue_item_id: str
    subject_id: str
    record_id: str
    fact_id: str
    evidence_id: str
    presentation: str
    source_manifest_sha256: str
    anchor_ids: tuple[str, ...]
    pages: tuple[SourcePageBinding, ...]


def _ordered_queue_stubs(manifest: ProductionAuthorizationManifest) -> tuple[_QueueStub, ...]:
    """从语料重建**权威队列序**的队列侧绑定视图。

    r84 定式：摄取按权威队列的顺序（fixture 封印随候选序）。权威序取自 r89 run 的
    admission-candidates.json（即 `r89_admission_bundle_sha256` 封印的清单本体；
    r810 run 自身的 manifest/projection 是按 candidate_id 排序的**另一序**，实测
    不是 fixture 候选序）。字段取自授权清单（导出前的权威绑定源）。
    """
    by_queue_item = {item.queue_item_id: item for item in manifest.decisions}
    ordered_ids = [
        item["queue_item_id"]
        for item in json.loads((R89_RUN / "admission-candidates.json").read_text(encoding="utf-8"))["candidates"]
    ]
    assert len(ordered_ids) == len(by_queue_item) == 6
    return tuple(
        _QueueStub(
            queue_item_id=item.queue_item_id,
            subject_id=item.subject_id,
            record_id=item.record_id,
            fact_id=item.fact_id,
            evidence_id=item.evidence_id,
            presentation=item.presentation,
            source_manifest_sha256=item.source_manifest_sha256,
            anchor_ids=item.anchor_ids,
            pages=item.pages,
        )
        for item in (by_queue_item[queue_item_id] for queue_item_id in ordered_ids)
    )


class _CandidateRef:
    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id


def _inline_strict_ingest(
    exported: HumanDecisionManifest, ordered_stubs: tuple[_QueueStub, ...]
) -> ReviewedFixture:
    """层 A 参照链：内核三件套按队列序逐决策重建（与实现方 API 零共享代码路径）。"""
    by_queue_item = {decision.queue_item_id: decision for decision in exported.decisions}
    candidates = []
    for stub in ordered_stubs:
        decision = by_queue_item.get(stub.queue_item_id)
        if decision is None:
            continue  # 无决策队列项守恒留队，不进 fixture
        validate_human_decision(decision, stub, _CandidateRef(decision.candidate_id))
        candidates.append(build_reviewed_candidate(stub, decision.candidate_id, decision))
    assert len(candidates) == len(exported.decisions), "every exported decision must bind a queue item"
    return build_reviewed_fixture(exported.queue_sha256, candidates)


def _inline_r810_fixture(
    *,
    admission_bundle_sha256: str,
    authorization_manifest_sha256: str,
    exported_manifest_sha256: str,
    ingested: ReviewedFixture,
) -> ReviewedTypedEvidenceFixture:
    payload = {
        "r89_admission_bundle_sha256": admission_bundle_sha256,
        "authorization_manifest_sha256": authorization_manifest_sha256,
        "exported_r84_manifest_sha256": exported_manifest_sha256,
        "r84_fixture_sha256": ingested.fixture_sha256,
        "candidates": ingested.candidates,
        "reviewed_candidate_count": ingested.reviewed_candidate_count,
        "fixture_authorized": True,
        "cards_created": False,
        "step3_authorized": False,
        "step4_authorized": False,
    }
    return hash_model_r8x(ReviewedTypedEvidenceFixture, payload, "fixture_sha256")  # type: ignore[return-value]


@pytest.fixture(scope="module")
def production_manifest() -> ProductionAuthorizationManifest:
    # model_validate 即全封印重算（decision/manifest 哈希不符直接拒绝）。
    return ProductionAuthorizationManifest.model_validate(_read("production-decision-manifest.json"))


@requires_corpus
class TestR810GoldenChainReplayKernelOnly:
    """层 A：production-decision-manifest → 终态 fixture 全链金标对拍（仅既有内核）。"""

    def test_production_manifest_slots_match_frozen_references(
        self, production_manifest: ProductionAuthorizationManifest
    ) -> None:
        reference = _read("r89-input-reference.json")
        freeze = _read("prior-freeze.json")
        admission_bundle = json.loads((R89_RUN / "admission-candidates.json").read_text(encoding="utf-8"))
        assert production_manifest.r89_frozen_input_audit_sha256 == reference["frozen_input_audit_sha256"]
        assert production_manifest.r89_admission_bundle_sha256 == reference["admission_bundle_sha256"]
        assert production_manifest.r89_admission_bundle_sha256 == admission_bundle["bundle_sha256"]
        assert production_manifest.r89_source_page_sidecar_sha256 == reference["sidecar_sha256"]
        assert production_manifest.r86_freeze_sha256 == freeze["r86_freeze_sha256"]
        assert production_manifest.decision_count == production_manifest.reviewed_candidate_count == 6

    def test_export_rebuilds_r84_manifest_and_receipt_byte_identical(
        self, production_manifest: ProductionAuthorizationManifest
    ) -> None:
        exported, receipt = export_r84_manifest(production_manifest)
        assert exported.model_dump(mode="json") == _read("phase2r84-human-decision-manifest.json")
        assert receipt.model_dump(mode="json") == _read("r84-export-receipt.json")

    def test_strict_ingest_rebuilds_ingestion_closure_hashes(
        self, production_manifest: ProductionAuthorizationManifest
    ) -> None:
        exported, _ = export_r84_manifest(production_manifest)
        ingested = _inline_strict_ingest(exported, _ordered_queue_stubs(production_manifest))
        closure = _read("r84-ingestion-closure.json")
        assert exported.manifest_id == closure["exported_manifest_id"]
        assert exported.manifest_sha256 == closure["exported_manifest_sha256"]
        assert ingested.fixture_sha256 == closure["r84_reviewed_fixture_sha256"]
        assert ingested.reviewed_candidate_count == closure["r84_reviewed_candidate_count"] == 6

    def test_final_r810_fixture_rebuilds_byte_identical(
        self, production_manifest: ProductionAuthorizationManifest
    ) -> None:
        exported, _ = export_r84_manifest(production_manifest)
        ingested = _inline_strict_ingest(exported, _ordered_queue_stubs(production_manifest))
        rebuilt = _inline_r810_fixture(
            admission_bundle_sha256=production_manifest.r89_admission_bundle_sha256,
            authorization_manifest_sha256=production_manifest.manifest_sha256,
            exported_manifest_sha256=exported.manifest_sha256,
            ingested=ingested,
        )
        assert rebuilt.model_dump(mode="json") == _read("reviewed-typed-evidence-fixture.json")

    def test_disposition_projection_and_preserved_queues_replay(self) -> None:
        projection = DispositionProjection.model_validate(_read("disposition-projection.json"))
        assert_no_silent_upgrade(projection.candidates)  # 处置守恒在真实生产语料上成立
        manual = PreservedDispositionQueue.model_validate(_read("manual-review-queue.json"))
        not_applicable = PreservedDispositionQueue.model_validate(_read("not-applicable-queue.json"))
        assert manual.disposition == "manual_review"
        assert not_applicable.disposition == "not_applicable"
        assert projection.reviewed_candidate_count == 6
        assert projection.manual_review_count == manual.queue_count
        assert projection.not_applicable_count == not_applicable.queue_count
        assert projection.candidate_count == (
            projection.reviewed_candidate_count
            + projection.manual_review_count
            + projection.blocked_count
            + projection.not_applicable_count
        )

    def test_authorization_audit_bundle_replays_model_fidelity(self) -> None:
        audits = tuple(AuthorizationAudit.model_validate(item) for item in _read("decision-audit.json"))
        projection = DispositionProjection.model_validate(_read("disposition-projection.json"))
        assert len(audits) == projection.candidate_count
        reviewed = [item for item in audits if item.effective_disposition == "reviewed_candidate"]
        assert len(reviewed) == 6
        assert all(item.admission_basis == "explicit_human_reviewed_candidate" for item in reviewed)
        assert all(
            item.admission_basis == "r89_disposition_preserved"
            for item in audits
            if item.effective_disposition != "reviewed_candidate"
        )


@requires_corpus
@requires_assembly
class TestAssemblyApiAgreesWithKernelChainOnCorpus:
    """层 B（金标侧）：实现方 API 与层 A 参照链在语料上逐字节同构。"""

    def test_strict_ingest_api_matches_inline_chain(self) -> None:
        manifest = ProductionAuthorizationManifest.model_validate(_read("production-decision-manifest.json"))
        exported, _ = export_r84_manifest(manifest)
        stubs = _ordered_queue_stubs(manifest)
        via_api = strict_ingest_exported_manifest(exported, stubs)
        inline = _inline_strict_ingest(exported, stubs)
        assert via_api.model_dump(mode="json") == inline.model_dump(mode="json")
        assert via_api.fixture_sha256 == inline.fixture_sha256

    def test_fixture_builder_api_matches_corpus(self) -> None:
        manifest = ProductionAuthorizationManifest.model_validate(_read("production-decision-manifest.json"))
        exported, _ = export_r84_manifest(manifest)
        ingested = strict_ingest_exported_manifest(exported, _ordered_queue_stubs(manifest))
        rebuilt = build_reviewed_typed_evidence_fixture(
            admission_bundle_sha256=manifest.r89_admission_bundle_sha256,
            authorization_manifest_sha256=manifest.manifest_sha256,
            exported_manifest_sha256=exported.manifest_sha256,
            ingested_fixture=ingested,
        )
        assert rebuilt.model_dump(mode="json") == _read("reviewed-typed-evidence-fixture.json")


def _source(presentation: str, **overrides: Any):
    payload: dict[str, Any] = {
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": presentation,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "anchor_ids": ANCHOR_IDS,
        "pages": (make_source_page(),),
    }
    payload.update(overrides)
    return make_extraction_source(**payload)


def _spec(presentation: str, texts: tuple[str, ...], **overrides: Any):
    payload: dict[str, Any] = {
        "selection_id": f"selection:replayv:{presentation}-0001",
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": presentation,
        "page_ref_id": "page:test:0001",
        "bindings": tuple(
            {"source_role": ROLE_BY_PRESENTATION[presentation], "text": text} for text in texts
        ),
        "excluded_noise_kinds": ("watermark",),
        "reviewer": "replay-verifier",
        "notes": "independent verification synthetic lane",
        "recorded_at": "2026-08-13T02:30:00Z",
    }
    payload.update(overrides)
    return make_manual_selection_spec(payload)


@pytest.fixture(scope="module")
def batch():
    sources = (
        _source("formula"),
        _source("table", evidence_id="evidence:test:0002"),
    )
    return extract_reviewed_fixture(sources, (_spec("formula", ("E=mc^2", "m=E/c^2")),), identity_period="v1")


@pytest.fixture(scope="module")
def bundle(batch):
    return authorize_extraction_batch(
        batch,
        identity_period="v1",
        reviewer="replay-verifier",
        reason="independent verification synthetic lane",
        recorded_at="2026-08-13T02:30:00Z",
    )


@requires_assembly
class TestAuthorizeExtractionBatchEndToEnd:
    """层 B（合成侧）：提取批 → authorize_extraction_batch → r810 fixture 全链闭环。"""

    def test_frozen_slot_semantics_bind_extraction_artifacts(self, batch, bundle) -> None:
        auth = bundle.authorization
        assert auth.r89_queue_sha256 == batch.queue_sha256
        assert auth.r86_freeze_sha256 == batch.manifest.manifest_sha256
        assert auth.r89_admission_bundle_sha256 == batch.fixture.fixture_sha256
        assert auth.r89_frozen_input_audit_sha256 == canonical_hash(
            [source.source_sha256 for source in batch.sources]
        )
        assert auth.r89_source_page_sidecar_sha256 == canonical_hash(
            [page.model_dump(mode="json") for source in batch.sources for page in source.pages]
        )

    def test_disposition_conservation_only_reviewed_candidates_authorized(self, batch, bundle) -> None:
        assert len(batch.queue_items) == 2  # 一圈选一未圈选
        assert bundle.authorization.decision_count == 1
        assert bundle.authorization.reviewed_candidate_count == 1
        assert bundle.fixture.reviewed_candidate_count == 1

    def test_identity_prefixes_and_period_scoping(self, bundle) -> None:
        decision = bundle.authorization.decisions[0]
        assert decision.production_decision_id.startswith("decision:r810:human:v1-")
        assert decision.r89_admission_candidate_id.startswith("admission:r89:production:v1-")
        assert bundle.authorization.manifest_id.startswith("manifest:r810:human:v1-")
        assert bundle.exported.manifest_id.startswith("manifest:r84:human:r810-")
        assert all(item.decision_id.startswith("decision:r84:human:r810-") for item in bundle.exported.decisions)

    def test_source_text_snapshot_follows_contract_storage_order(self, batch, bundle) -> None:
        decision = bundle.authorization.decisions[0]
        contract = batch.fixture.candidates[0].typed_contract
        assert decision.source_text_snapshot == source_text_snapshot(contract)
        assert decision.source_text_snapshot == tuple(token.text for token in contract.tokens)

    def test_export_receipt_binds_both_manifests(self, bundle) -> None:
        receipt = bundle.receipt
        assert receipt.source_manifest_id == bundle.authorization.manifest_id
        assert receipt.source_manifest_sha256 == bundle.authorization.manifest_sha256
        assert receipt.exported_manifest_id == bundle.exported.manifest_id
        assert receipt.exported_manifest_sha256 == bundle.exported.manifest_sha256
        assert receipt.exported_decision_count == receipt.exported_reviewed_candidate_count == 1

    def test_ingested_candidates_rebind_to_exported_decisions(self, batch, bundle) -> None:
        exported_ids = {item.decision_id for item in bundle.exported.decisions}
        for candidate in bundle.ingested_fixture.candidates:
            assert candidate.source_decision_id in exported_ids
        # 契约本体在授权/导出/摄取全程逐字节不变（身份换、内容不换）。
        assert (
            bundle.ingested_fixture.candidates[0].typed_contract.model_dump(mode="json")
            == batch.fixture.candidates[0].typed_contract.model_dump(mode="json")
        )

    def test_final_fixture_closes_the_chain(self, batch, bundle) -> None:
        fixture = bundle.fixture
        assert fixture.r89_admission_bundle_sha256 == batch.fixture.fixture_sha256
        assert fixture.authorization_manifest_sha256 == bundle.authorization.manifest_sha256
        assert fixture.exported_r84_manifest_sha256 == bundle.exported.manifest_sha256
        assert fixture.r84_fixture_sha256 == bundle.ingested_fixture.fixture_sha256
        assert fixture.cards_created is False
        assert fixture.step3_authorized is False
        assert fixture.step4_authorized is False

    def test_batch_without_reviewed_candidates_fails_closed(self) -> None:
        empty = extract_reviewed_fixture((_source("formula"),), (), identity_period="v1")
        with pytest.raises(ValueError, match="no reviewed candidates"):
            authorize_extraction_batch(
                empty,
                identity_period="v1",
                reviewer="replay-verifier",
                reason="independent verification synthetic lane",
                recorded_at="2026-08-13T02:30:00Z",
            )
