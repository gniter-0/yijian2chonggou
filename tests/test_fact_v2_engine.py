"""Golden tests for the deterministic Phase 2 V2 Fact/Card/Coverage runner."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from examcard.knowledge.fact_engine import _dedup_audit, run_golden_fact_pipeline
from examcard.knowledge.schema import (
    AtomicFact,
    ContentSegment,
    EvidenceRef,
    FactType,
    GroundingStatus,
    KnowledgeCard,
    Renderability,
)
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



def _fact(*, fact_id: str, evidence_id: str, title: str) -> AtomicFact:
    return AtomicFact(
        fact_id=fact_id,
        fact_type=FactType.DEFINITION,
        label=title,
        assertion=title,
        primary_evidence_ids=(evidence_id,),
        confidence=1.0,
    )


def _card(*, raw_id: str, fact_id: str, evidence_id: str, title: str) -> KnowledgeCard:
    return KnowledgeCard(
        id=raw_id,
        raw_id=raw_id,
        title=title,
        content=title,
        content_segments=(ContentSegment(text=title, fact_ids=(fact_id,), evidence_ids=(evidence_id,), renderer="text"),),
        evidence_refs=(EvidenceRef(evidence_id=evidence_id, page_num=1),),
        grounding_status=GroundingStatus.VERIFIED,
        source_pages=(1,),
    )


def test_anchor_span_dedup_does_not_merge_same_title_with_different_evidence():
    title = "安全生产费用"
    fact_a = _fact(fact_id="fact:" + "a" * 24, evidence_id="ev:" + "1" * 24, title=title)
    fact_b = _fact(fact_id="fact:" + "b" * 24, evidence_id="ev:" + "2" * 24, title=title)
    card_a = _card(raw_id="kp:" + "a" * 24, fact_id=fact_a.fact_id, evidence_id="ev:" + "1" * 24, title=title)
    card_b = _card(raw_id="kp:" + "b" * 24, fact_id=fact_b.fact_id, evidence_id="ev:" + "2" * 24, title=title)

    audit = _dedup_audit((card_a, card_b), (fact_a, fact_b))

    assert audit["auto_merged"] == []
    assert audit["exact_primary_span_duplicates"] == []
    assert audit["title_similarity_candidates"] == [
        {
            "title": title,
            "card_raw_ids": [card_a.raw_id, card_b.raw_id],
            "decision": "candidate_only_distinct_anchor_span",
        }
    ]


def test_phase2_golden_run_preserves_grounding_and_non_destructive_policy_routing(tmp_path):
    result = run_golden_fact_pipeline(
        registry_path=ROOT / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml",
        sidecar_path=ROOT / "scripts/tests/fixtures/v2/golden_annotations/gonglu_phase0_v1.yaml",
        content_policy_path=ROOT / "configs/v2/content_policies/core_only_v1.yaml",
        evidence_root=ROOT / "data/evidence_v2/golden/gonglu_phase0_v1",
        output_root=tmp_path / "phase2",
    )

    assert result["sample_count"] == 5
    assert result["selected_page_count"] == 20
    assert result["quality_gates_passed"] is False
    assert result["machine_assertions_passed"] is True
    assert (tmp_path / "phase2" / "golden_fact_run_report.json").is_file()

    all_cards = [card for sample in result["samples"] for card in sample["cards"]]
    assert not any(
        any(marker in card["content"] for marker in ("3849178", "嗨字", "嗨学", "【例题】", "例如：", "答案：", "答："))
        for card in all_cards
    )
    manual_diagrams = [
        card
        for card in all_cards
        if card["renderability"] == Renderability.MANUAL_REVIEW.value
    ]
    assert manual_diagrams
    assert all(card["content"] == "图示待人工结构化" for card in manual_diagrams)
    assert all(card["grounding_status"] == GroundingStatus.UNRESOLVED.value for card in manual_diagrams)

    roadbed = next(sample for sample in result["samples"] if sample["sample_id"] == "gonglu_01_text_list")
    watermark_transforms = [
        transform
        for fact in roadbed["facts"]
        for transform in fact["presentation_transforms"]
        if transform["rule_id"] == "watermark-haixue-embedded"
    ]
    assert len(watermark_transforms) == 1
    assert watermark_transforms[0]["end"] > watermark_transforms[0]["start"]
    assert watermark_transforms[0]["before_sha256"] != watermark_transforms[0]["after_sha256"]

    tunnel = next(sample for sample in result["samples"] if sample["sample_id"] == "gonglu_41_diagram_mixed")
    excluded_ids = {
        decision["evidence_id"]
        for decision in tunnel["policy_decisions"]
        if decision["semantic_role"]
        in {"worked_example", "exercise_question", "exercise_option", "exercise_answer", "exercise_explanation"}
    }
    coverage_by_id = {record["evidence_id"]: record for record in tunnel["coverage"]["records"]}
    assert excluded_ids
    assert all(coverage_by_id[evidence_id]["disposition"] == "excluded_by_policy" for evidence_id in excluded_ids)
    assert not any(
        excluded_ids & {ref["evidence_id"] for ref in card["evidence_refs"]}
        for card in tunnel["cards"]
    )

    cost = next(sample for sample in result["samples"] if sample["sample_id"] == "gonglu_62_cost_flow_incident")
    corrected = next(fact for fact in cost["facts"] if "目标成本）和月度施工成本计划" in fact["assertion"])
    assert "目标戚本" not in corrected["assertion"]
    assert len(corrected["primary_evidence_ids"]) == 2
    assert any(
        transform["rule_id"] == "example-tail-embedded"
        for fact in cost["facts"]
        for transform in fact["presentation_transforms"]
    )

    for sample in result["samples"]:
        gate = sample["quality_gate"]
        assert gate["must_cover_primary_assigned"] == gate["must_cover_total"]
        assert gate["known_noise_leaks"] == 0
        assert gate["embedded_example_leaks"] == 0
        assert gate["unrenderable_diagrams"] > 0
        assert gate["blockers"] == ["unrenderable_diagrams"]
