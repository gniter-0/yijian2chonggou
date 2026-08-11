"""Phase 0 contract tests. No V1 data is written and no API is called."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.golden import GoldenSampleRegistry, load_golden_registry, verify_golden_sources
from examcard.evidence.annotations import ConflictClassification, GoldenReviewSidecar, load_golden_review_sidecar
from examcard.evidence.identity import (
    make_cache_key,
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_id,
    make_evidence_revision_id,
    make_source_locator_id,
)
from examcard.evidence.schema import (
    BBox,
    EvidenceKind,
    EvidenceProvenance,
    EvidenceUnit,
    DocumentEvidenceManifest,
    PageEvidence,
    SourceChannel,
    SourceLocator,
    SourceQuality,
)
from examcard.knowledge.identity import make_card_raw_id, make_fact_id
from examcard.knowledge.diagram.sidecar import DiagramReviewSidecar
from examcard.knowledge.continuation.review import (
    ContinuationBindingReviewSidecar,
    ContinuationLearningGoldenAlignment,
    ContinuationLearningGoldenFixture,
)
from examcard.knowledge.step2b_card_golden import ContinuationCardGoldenAlignment, ContinuationCardGoldenFixture
from examcard.knowledge.learning_fixture import LearningGoldenFixture
from examcard.knowledge.contracts import (
    ContinuationRecord,
    EvidenceAnchorSpan,
    ReviewedCanonicalTitleProjection,
    Step2ARequest,
    Step2ASemanticProjection,
    Step2AResponse,
    Step2AResultPayload,
    Step2BRequest,
    Step2BResponse,
    Step2BResultPayload,
    TableAtomicSubfactRequirement,
)
from examcard.knowledge.llm import Step2AExecutionRecord, Step2BExecutionRecord, Step2JournalEvent
from examcard.knowledge.schema import (
    AtomicFact,
    CardPlan,
    ChapterQualityGate,
    CompletenessMode,
    CoverageDisposition,
    CoverageMap,
    CoverageRecord,
    FactType,
    KnowledgeCard,
)
from examcard.knowledge.quality import TeachingQualityGate
from examcard.knowledge.state_machine import (
    ChapterState,
    assert_chapter_transition,
    assert_policy_transition,
)
from examcard.materials.catalog import load_content_policy, load_material_profile
from examcard.materials.schema import (
    ContentPolicy,
    CoverageRequirement,
    DedupScope,
    EffectiveWindow,
    SourceKind,
    PolicyDecision,
    PolicyDecisionState,
    PolicyDisposition,
    SemanticRole,
    MaterialProfile,
)

# T5 金样镜像守护：镜像缺席（冷克隆）时整模块优雅跳过，镜像在场则正常执行。
_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_MIRROR_REQUIRED = (
    _REPO_ROOT / "docs/v2/baselines/gonglu_v1_baseline_20260724.json",
)
pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in _GOLDEN_MIRROR_REQUIRED),
    reason="金样镜像缺席（T5/骨架§3：data/ 等 gitignore 大资产不入库，冷克隆合法跳过）",
)



PDF_SHA = "a" * 64


def _locator(*, bbox: BBox | None = None) -> SourceLocator:
    return SourceLocator(document_sha256=PDF_SHA, page_num=4, bbox=bbox)


def _evidence_unit(
    *,
    quality: SourceQuality = SourceQuality.VISUAL_ONLY,
    bbox: BBox | None = BBox(x0=10, y0=20, x1=100, y1=50),
    channels: frozenset[SourceChannel] = frozenset({SourceChannel.VISUAL_OCR}),
    policy_decision: PolicyDecision | None = None,
) -> EvidenceUnit:
    locator = _locator(bbox=bbox)
    locator_id = make_source_locator_id(locator)
    fingerprint = make_evidence_fingerprint(
        kind="diagram_node",
        canonical_text="成本核算",
        semantic_context={"parent": "施工成本流程"},
    )
    evidence_id = make_evidence_id(locator_id=locator_id, fingerprint=fingerprint)
    revision_id = make_evidence_revision_id(
        fingerprint=fingerprint,
        raw_text="成本核算",
        source_channels=[str(channel) for channel in channels],
        engine_versions={"ocr": "test"},
        config_hash="cfg-test",
    )
    return EvidenceUnit(
        evidence_id=evidence_id,
        fingerprint=fingerprint,
        revision_id=revision_id,
        document_id=make_document_id(PDF_SHA),
        kind=EvidenceKind.DIAGRAM_NODE,
        locator=locator,
        raw_text="成本核算",
        canonical_text="成本核算",
        semantic_role=SemanticRole.CORE,
        source_quality=quality,
        confidence=0.98,
        order=1,
        provenance=EvidenceProvenance(
            source_channels=channels,
            engine_versions={"ocr": "test"},
            config_hash="cfg-test",
            generated_at_utc="2026-07-24T00:00:00Z",
        ),
        policy_decision=policy_decision,
    )


class TestMaterialProfilesAndPolicies:
    def test_checked_in_profile_and_policy_validate(self):
        policy = load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml")
        profile = load_material_profile(ROOT / "configs/v2/material_profiles/yijian_gonglu_2026.yaml")
        assert profile.content_policy_id == policy.policy_id
        assert profile.content_policy_revision == policy.revision
        assert SemanticRole.CORE in policy.included_roles
        assert SemanticRole.EXERCISE_QUESTION in policy.excluded_roles
        assert policy.allow_fact_promotion_from_excluded is False

    def test_same_evidence_can_be_projected_to_core_or_question_views(self):
        core = load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml")
        question_bank = load_content_policy(ROOT / "configs/v2/content_policies/question_bank_v1.yaml")
        examples = load_content_policy(ROOT / "configs/v2/content_policies/core_with_examples_v1.yaml")
        assert SemanticRole.EXERCISE_QUESTION in core.excluded_roles
        assert SemanticRole.EXERCISE_QUESTION in question_bank.included_roles
        assert SemanticRole.WORKED_EXAMPLE in examples.included_roles

    @pytest.mark.parametrize(
        ("profile_id", "collection_id", "exam_family", "subject_id", "subject_name"),
        [
            (
                "fire_engineer_2026_technical_practice",
                "registered_fire_engineer/2026/technical_practice",
                "注册消防工程师",
                "technical_practice",
                "消防安全技术实务",
            ),
            (
                "safety_engineer_2026_safety_law",
                "registered_safety_engineer/2026/safety_production_law",
                "中级注册安全工程师",
                "safety_production_law",
                "安全生产法律法规",
            ),
        ],
    )
    def test_non_yijian_exam_profiles_use_the_same_contract(
        self, profile_id, collection_id, exam_family, subject_id, subject_name
    ):
        profile = MaterialProfile(
            profile_id=profile_id,
            revision=1,
            display_name=subject_name,
            collection_id=collection_id,
            exam_family=exam_family,
            subject_id=subject_id,
            subject_name=subject_name,
            source_kind=SourceKind.REGULATION,
            edition="2026",
            jurisdiction="CN",
            effective_window=EffectiveWindow(effective_from="2026-01-01"),
            content_policy_id="core_only_v1",
            content_policy_revision=1,
            layout_profile_id="textbook_cn_v1",
            taxonomy_profile_id=f"{subject_id}_v1",
            validation_profile_id="regulation_cn_v1",
            card_profile_id="knowledge_diagram_v1",
            dedup_scope=DedupScope.CROSS_COLLECTION_CANDIDATE_ONLY,
        )
        assert profile.collection_id == collection_id
        assert profile.dedup_scope == DedupScope.CROSS_COLLECTION_CANDIDATE_ONLY

    def test_policy_role_sets_must_not_overlap(self):
        with pytest.raises(ValidationError, match="multiple policy sets"):
            ContentPolicy(
                policy_id="bad_policy",
                revision=1,
                display_name="bad",
                included_roles={SemanticRole.CORE},
                excluded_roles={SemanticRole.CORE},
            )

    def test_exercise_is_excluded_by_policy_not_ignored(self):
        decision = PolicyDecision(
            evidence_id="ev:" + "1" * 24,
            semantic_role=SemanticRole.EXERCISE_QUESTION,
            disposition=PolicyDisposition.EXCLUDED_BY_POLICY,
            coverage_requirement=CoverageRequirement.NOT_REQUIRED,
            policy_id="core_only_v1",
            policy_revision=1,
            reason="当前产品只生成核心知识卡",
        )
        assert decision.disposition == PolicyDisposition.EXCLUDED_BY_POLICY

        with pytest.raises(ValidationError, match="only noise evidence"):
            PolicyDecision(
                evidence_id="ev:" + "1" * 24,
                semantic_role=SemanticRole.EXERCISE_QUESTION,
                disposition=PolicyDisposition.IGNORED_NOISE,
                coverage_requirement=CoverageRequirement.NOT_REQUIRED,
                policy_id="core_only_v1",
                policy_revision=1,
                reason="错误地当作噪声",
            )

    def test_manual_decision_requires_reviewer(self):
        with pytest.raises(ValidationError, match="require reviewer"):
            PolicyDecision(
                evidence_id="ev:" + "1" * 24,
                semantic_role=SemanticRole.UNKNOWN,
                disposition=PolicyDisposition.MANUAL_REVIEW,
                coverage_requirement=CoverageRequirement.NOT_REQUIRED,
                decision_state=PolicyDecisionState.MANUALLY_RESOLVED,
                policy_id="core_only_v1",
                policy_revision=1,
                reason="人工处理未知内容",
            )


class TestEvidenceContracts:
    def test_visual_only_requires_bbox_and_visual_channel(self):
        unit = _evidence_unit()
        assert unit.locator.bbox is not None

        with pytest.raises(ValidationError, match="requires bbox"):
            _evidence_unit(bbox=None)

        with pytest.raises(ValidationError, match="visual source channel"):
            _evidence_unit(channels=frozenset({SourceChannel.NATIVE_TEXT}))

    def test_verified_agreement_requires_native_and_visual_channels(self):
        with pytest.raises(ValidationError, match="native and visual"):
            _evidence_unit(
                quality=SourceQuality.VERIFIED_AGREEMENT,
                channels=frozenset({SourceChannel.NATIVE_TEXT}),
            )

        unit = _evidence_unit(
            quality=SourceQuality.VERIFIED_AGREEMENT,
            channels=frozenset({SourceChannel.NATIVE_TEXT, SourceChannel.VISUAL_OCR}),
        )
        assert unit.source_quality == SourceQuality.VERIFIED_AGREEMENT

    def test_policy_decision_must_reference_same_evidence(self):
        decision = PolicyDecision(
            evidence_id="ev:" + "f" * 24,
            semantic_role=SemanticRole.CORE,
            disposition=PolicyDisposition.INCLUDED,
            coverage_requirement=CoverageRequirement.REQUIRED,
            policy_id="core_only_v1",
            policy_revision=1,
            reason="核心内容必须进入知识卡",
        )
        with pytest.raises(ValidationError, match="reference this evidence"):
            _evidence_unit(policy_decision=decision)


class TestIdentityAndCacheContracts:
    def test_fingerprint_is_stable_across_whitespace_and_dict_order(self):
        left = make_evidence_fingerprint(
            kind="paragraph",
            canonical_text="目标  成本\n计算",
            semantic_context={"section": "12.2", "ordinal": 1},
        )
        right = make_evidence_fingerprint(
            kind="paragraph",
            canonical_text="目标 成本 计算",
            semantic_context={"ordinal": 1, "section": "12.2"},
        )
        assert left == right

    def test_locator_id_changes_with_bbox_but_fingerprint_does_not(self):
        a = _locator(bbox=BBox(x0=1, y0=1, x1=10, y1=10))
        b = _locator(bbox=BBox(x0=2, y0=1, x1=11, y1=10))
        assert make_source_locator_id(a) != make_source_locator_id(b)
        assert make_evidence_fingerprint(kind="paragraph", canonical_text="相同内容") == make_evidence_fingerprint(
            kind="paragraph", canonical_text="相同内容"
        )

    def test_cache_key_invalidates_on_engine_or_config_change(self):
        kwargs = dict(
            pdf_sha256=PDF_SHA,
            page_num=4,
            region_bbox=BBox(x0=1, y0=2, x1=30, y1=40),
            engine_name="ocr",
            engine_version="1",
            config_hash="cfg-a",
        )
        baseline = make_cache_key(**kwargs)
        assert baseline != make_cache_key(**{**kwargs, "engine_version": "2"})
        assert baseline != make_cache_key(**{**kwargs, "config_hash": "cfg-b"})

    def test_revision_id_ignores_source_channel_order(self):
        kwargs = dict(
            fingerprint="evfp:" + "1" * 24,
            raw_text="成本核算",
            engine_versions={"ocr": "1", "layout": "2"},
            config_hash="cfg-a",
        )
        assert make_evidence_revision_id(
            **kwargs,
            source_channels=["native_text", "visual_ocr"],
        ) == make_evidence_revision_id(
            **kwargs,
            source_channels=["visual_ocr", "native_text"],
        )

    def test_fact_and_card_ids_ignore_input_order(self):
        fact_a = make_fact_id(
            fact_type="procedure",
            primary_evidence_fingerprints=["evfp:b", "evfp:a"],
            assertion_contract={"mode": "ordered_steps"},
        )
        fact_b = make_fact_id(
            fact_type="procedure",
            primary_evidence_fingerprints=["evfp:a", "evfp:b"],
            assertion_contract={"mode": "ordered_steps"},
        )
        assert fact_a == fact_b
        assert make_card_raw_id(
            material_profile_id="yijian_gonglu_2026",
            content_policy_id="core_only_v1",
            primary_fact_ids=["fact:b", "fact:a"],
        ) == make_card_raw_id(
            material_profile_id="yijian_gonglu_2026",
            content_policy_id="core_only_v1",
            primary_fact_ids=["fact:a", "fact:b"],
        )

    def test_overview_card_id_can_be_derived_from_child_cards(self):
        left = make_card_raw_id(
            material_profile_id="yijian_gonglu_2026",
            content_policy_id="core_only_v1",
            primary_fact_ids=[],
            child_kp_ids=["kp:b", "kp:a"],
            is_overview=True,
        )
        right = make_card_raw_id(
            material_profile_id="yijian_gonglu_2026",
            content_policy_id="core_only_v1",
            primary_fact_ids=[],
            child_kp_ids=["kp:a", "kp:b"],
            is_overview=True,
        )
        assert left == right


class TestFactCoverageAndGates:
    def test_non_atomic_fact_requires_complete_members(self):
        with pytest.raises(ValidationError, match="ordered member"):
            AtomicFact(
                fact_id="fact:" + "1" * 24,
                fact_type=FactType.ENUMERATION,
                label="完整枚举",
                assertion="原文包含完整枚举",
                completeness_mode=CompletenessMode.ALL_ITEMS,
                primary_evidence_ids=("ev:a",),
                confidence=1,
            )

    def test_coverage_owner_is_unique_but_references_are_many(self):
        coverage = CoverageMap(
            material_profile_id="yijian_gonglu_2026",
            content_policy_id="core_only_v1",
            content_policy_revision=1,
            records=(
                CoverageRecord(
                    evidence_id="ev:a",
                    disposition=CoverageDisposition.PRIMARY_ASSIGNED,
                    coverage_owner="kp:owner",
                    references=("kp:reference-a", "kp:reference-b"),
                    reason="正文由 owner 负责，其余卡仅引用",
                ),
            ),
        )
        assert coverage.records[0].coverage_owner == "kp:owner"
        assert len(coverage.records[0].references) == 2

    def test_duplicate_coverage_dispositions_are_rejected(self):
        record = CoverageRecord(
            evidence_id="ev:a",
            disposition=CoverageDisposition.EXCLUDED_BY_POLICY,
            reason="例题不进入核心知识卡",
        )
        with pytest.raises(ValidationError, match="exactly one"):
            CoverageMap(
                material_profile_id="yijian_gonglu_2026",
                content_policy_id="core_only_v1",
                content_policy_revision=1,
                records=(record, record),
            )

    def test_publish_gate_is_hard(self):
        passing = ChapterQualityGate(
            must_cover_total=10,
            must_cover_primary_assigned=10,
            unresolved_evidence=0,
            fallback_content=0,
            numeric_failures=0,
            enumeration_failures=0,
            formula_failures=0,
            unresolved_visual_only=0,
            unresolved_dedup_candidates=0,
            schema_valid=True,
            lineage_valid=True,
            adapter_valid=True,
        )
        assert passing.publishable is True

        blocked = passing.model_copy(update={"numeric_failures": 1})
        assert blocked.publishable is False
        assert "numeric_failures" in blocked.blockers


class TestStateMachines:
    def test_chapter_must_pass_publishable_before_published(self):
        passing = ChapterQualityGate(
            must_cover_total=1,
            must_cover_primary_assigned=1,
            unresolved_evidence=0,
            fallback_content=0,
            numeric_failures=0,
            enumeration_failures=0,
            formula_failures=0,
            unresolved_visual_only=0,
            unresolved_dedup_candidates=0,
            schema_valid=True,
            lineage_valid=True,
            adapter_valid=True,
        )
        assert_chapter_transition(
            ChapterState.PROCESSING,
            ChapterState.PUBLISHABLE,
            quality_gate=passing,
        )
        assert_chapter_transition(
            ChapterState.PUBLISHABLE,
            ChapterState.PUBLISHED,
            quality_gate=passing,
        )
        with pytest.raises(ValueError, match="passing chapter quality gate"):
            assert_chapter_transition(ChapterState.PROCESSING, ChapterState.PUBLISHABLE)
        with pytest.raises(ValueError, match="passing chapter quality gate"):
            assert_chapter_transition(
                ChapterState.PROCESSING,
                ChapterState.PUBLISHABLE,
                quality_gate=passing.model_copy(update={"formula_failures": 1}),
            )
        with pytest.raises(ValueError, match="invalid chapter transition"):
            assert_chapter_transition(
                ChapterState.PROCESSING,
                ChapterState.PUBLISHED,
                quality_gate=passing,
            )

    def test_policy_manual_resolution_cannot_skip_review(self):
        assert_policy_transition(
            PolicyDecisionState.UNCLASSIFIED,
            PolicyDecisionState.NEEDS_MANUAL_REVIEW,
        )
        assert_policy_transition(
            PolicyDecisionState.NEEDS_MANUAL_REVIEW,
            PolicyDecisionState.MANUALLY_RESOLVED,
        )
        with pytest.raises(ValueError, match="invalid policy transition"):
            assert_policy_transition(
                PolicyDecisionState.AUTO_CLASSIFIED,
                PolicyDecisionState.MANUALLY_RESOLVED,
            )


class TestGoldenRegistryAndJsonSchema:
    def test_golden_registry_has_five_documents_and_twenty_pages(self):
        registry = load_golden_registry(
            ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
        )
        assert len(registry.samples) == 5
        assert sum(len(sample.selected_pages) for sample in registry.samples) == 20
        assert registry.ready_for_phase1 is True
        assert verify_golden_sources(registry, ROOT) == []

    def test_cost_flow_incident_is_pinned(self):
        registry = load_golden_registry(
            ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
        )
        sample = next(item for item in registry.samples if item.sample_id == "gonglu_62_cost_flow_incident")
        assertions = {item.assertion_id: item for item in sample.assertions}
        assert "page4_cost_flow_region" in assertions
        assert "成本核算" in assertions["page4_cost_flow_region"].expected_texts
        assert ("目标戚本", "目标成本") in assertions["page4_cost_typo_conflict"].expected_conflicts

    def test_golden_review_sidecar_covers_every_selected_page_and_conflict(self):
        registry = load_golden_registry(
            ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
        )
        sidecar = load_golden_review_sidecar(
            ROOT / "scripts/tests/fixtures/v2/golden_annotations/gonglu_phase0_v1.yaml"
        )
        assert sidecar.registry_id == registry.registry_id
        assert sidecar.sidecar_id == "gonglu_phase0_v1.reviewed"
        assert sidecar.annotation_revision == 2
        assert sidecar.state.value == "reviewed"
        assert sidecar.reviewer == "codex_phase1_visual_audit"
        assert sidecar.reviewed_at_utc is not None
        assert {sample.sample_id for sample in sidecar.samples} == {sample.sample_id for sample in registry.samples}
        assert sum(len(sample.pages) for sample in sidecar.samples) == 20
        conflicts = [
            conflict
            for sample in sidecar.samples
            for page in sample.pages
            for conflict in page.conflicts
        ]
        assert len(conflicts) == 86
        assert sum(item.classification == ConflictClassification.REAL_ERROR for item in conflicts) == 82
        assert sum(item.classification == ConflictClassification.ACCEPTABLE_LAYOUT_EQUIVALENCE for item in conflicts) == 4
        assert sum(item.classification == ConflictClassification.MANUAL_REVIEW for item in conflicts) == 0
        assert all(page.state.value == "reviewed" and page.audit is not None for sample in sidecar.samples for page in sample.pages)
        assert all(item.audit is not None for item in conflicts)
        typo = next(item for item in conflicts if item.conflict_id == "conflict:036b1d1491b550bb1677acbd")
        assert typo.native_text == "目标戚本"
        assert typo.classification == ConflictClassification.REAL_ERROR
        assert typo.authoritative_channel.value == "vision_structure"
        assert typo.proposed_canonical_text == "目标成本"
        assert typo.requires_human_confirmation is False
        assert typo.audit is not None and "原页裁剪清晰显示“目标成本”" in typo.audit.basis

    def test_every_reviewed_sample_has_assertions_covering_its_selected_pages(self):
        registry = load_golden_registry(
            ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml"
        )
        assert registry.ready_for_phase1 is True
        for sample in registry.samples:
            assert sample.annotation_status.value == "reviewed"
            assert sample.reviewer == "codex_phase1_visual_audit"
            assert sample.reviewed_at_utc is not None
            asserted_pages = {
                int(assertion.assertion_id.split("_", 1)[0].removeprefix("page"))
                for assertion in sample.assertions
            }
            assert asserted_pages == set(sample.selected_pages)

    @pytest.mark.parametrize(
        "model",
        [
            ContentPolicy,
            PolicyDecision,
            EvidenceUnit,
            EvidenceAnchorSpan,
            ContinuationRecord,
            AtomicFact,
            CoverageMap,
            ChapterQualityGate,
            DiagramReviewSidecar,
            GoldenReviewSidecar,
            LearningGoldenFixture,
            ContinuationCardGoldenFixture,
            ContinuationCardGoldenAlignment,
            Step2ARequest,
            Step2ASemanticProjection,
            Step2AResponse,
            Step2AResultPayload,
            Step2AExecutionRecord,
            Step2BRequest,
            Step2BResponse,
            Step2BResultPayload,
            TableAtomicSubfactRequirement,
            Step2BExecutionRecord,
            Step2JournalEvent,
            TeachingQualityGate,
            ReviewedCanonicalTitleProjection,
        ],
    )
    def test_models_export_valid_json_schema(self, model):
        schema = model.model_json_schema()
        assert schema["type"] == "object"
        assert json.loads(json.dumps(schema, ensure_ascii=False))["type"] == "object"

    @pytest.mark.parametrize(
        ("name", "model"),
        [
            ("atomic_fact", AtomicFact),
            ("card_plan", CardPlan),
            ("chapter_quality_gate", ChapterQualityGate),
            ("content_policy", ContentPolicy),
            ("coverage_map", CoverageMap),
            ("diagram_review_sidecar", DiagramReviewSidecar),
            ("document_evidence_manifest", DocumentEvidenceManifest),
            ("evidence_unit", EvidenceUnit),
            ("evidence_anchor_span", EvidenceAnchorSpan),
            ("continuation_record", ContinuationRecord),
            ("continuation_binding_review_sidecar", ContinuationBindingReviewSidecar),
            ("continuation_learning_golden_fixture", ContinuationLearningGoldenFixture),
            ("continuation_learning_golden_alignment", ContinuationLearningGoldenAlignment),
            ("continuation_card_golden_fixture", ContinuationCardGoldenFixture),
            ("continuation_card_golden_alignment", ContinuationCardGoldenAlignment),
            ("golden_sample_registry", GoldenSampleRegistry),
            ("golden_review_sidecar", GoldenReviewSidecar),
            ("knowledge_card", KnowledgeCard),
            ("learning_golden_fixture", LearningGoldenFixture),
            ("material_profile", MaterialProfile),
            ("page_evidence", PageEvidence),
            ("policy_decision", PolicyDecision),
            ("step2a_request", Step2ARequest),
            ("step2a_semantic_projection", Step2ASemanticProjection),
            ("step2a_response", Step2AResponse),
            ("step2a_result_payload", Step2AResultPayload),
            ("step2a_execution_record", Step2AExecutionRecord),
            ("step2b_request", Step2BRequest),
            ("step2b_response", Step2BResponse),
            ("step2b_result_payload", Step2BResultPayload),
            ("step2b_execution_record", Step2BExecutionRecord),
            ("step2_journal_event", Step2JournalEvent),
            ("teaching_quality_gate", TeachingQualityGate),
            ("table_atomic_subfact_requirement", TableAtomicSubfactRequirement),
            ("reviewed_canonical_title_projection", ReviewedCanonicalTitleProjection),
        ],
    )
    def test_checked_in_json_schema_snapshots_are_current(self, name, model):
        snapshot = json.loads((ROOT / "schemas/v2" / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert snapshot == model.model_json_schema()

    def test_checked_in_v1_baseline_is_machine_readable(self):
        baseline = json.loads(
            (ROOT / "docs/v2/baselines/gonglu_v1_baseline_20260724.json").read_text(encoding="utf-8")
        )
        assert baseline["corpus"]["pdf_count"] == 65
        assert baseline["corpus"]["page_count"] == 444
        assert baseline["structured"]["chapter_count"] == 65
        assert baseline["performance"]["historical_api_cost"] is None
