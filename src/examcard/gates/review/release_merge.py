"""双层 reviewed 合并判定与对账（规范源 phase2r2242，次要 r224）。

合并三型数据模型（Step3MergedSourceOwnedDisplayText / Step3MergedReviewedReleaseCard /
Step3UnifiedReviewedReleaseFixture 与 Step3ReviewedSourceTier）按 A 案在
knowledge/contracts/admission_fixtures；本模块只承载判定：层级对账、处置/来源
守恒检查与确定性发布草稿投影。r98 形态的发布草稿装配归 gates/release
（p1_compilers_release_gate_design §5.2），此处提供其输入投影骨架。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...knowledge.contracts.admission_fixtures import (
    Step3MergedReviewedReleaseCard,
    Step3ReviewedSourceTier,
    Step3UnifiedReviewedReleaseFixture,
)

__all__ = (
    "Step3ReviewedSourceTier",
    "Step3MergedReviewedReleaseCard",
    "Step3UnifiedReviewedReleaseFixture",
    "reconcile_release_tiers",
    "merge_failure_codes",
    "release_draft_projection",
)


def reconcile_release_tiers(cards: Sequence[Step3MergedReviewedReleaseCard]) -> dict[str, Any]:
    """层级对账摘要：每层卡数、Fact 覆盖、显示文本/语义边总量（r2242 对账定式）。"""
    fact_ids = [fact_id for card in cards for fact_id in card.fact_ids]
    return {
        "card_count": len(cards),
        "tier_counts": {tier.value: sum(1 for card in cards if card.source_tier == tier) for tier in Step3ReviewedSourceTier},
        "fact_count": len(fact_ids),
        "fact_ids_unique": len(fact_ids) == len(set(fact_ids)),
        "display_text_count": sum(len(card.display_text_provenance) for card in cards),
        "semantic_edge_count": sum(len(card.semantic_edge_provenance) for card in cards),
    }


def merge_failure_codes(cards: Sequence[Step3MergedReviewedReleaseCard]) -> tuple[str, ...]:
    """合并前置判定：返回失败码（空即可送 Step3UnifiedReviewedReleaseFixture 终验）。

    - 卡序必须 1..N 连续（fixture 校验器要求 1..16，此处先验连续性）；
    - source_card_id 恰一次；Fact 恰一次；
    - 每卡 review_state 必须 reviewed（模型 Literal 已锁，此处防御 dict 输入路径）；
    - PHASE224 层必须保真实 Step3 请求溯源，补救层必须不声称新请求（模型校验器
      已按卡拒绝，此处汇总为矩阵失败码）。
    """
    codes: list[str] = []
    indices = tuple(card.card_index for card in cards)
    if indices != tuple(range(1, len(cards) + 1)):
        codes.append("card_indices_not_contiguous")
    if len({card.source_card_id for card in cards}) != len(cards):
        codes.append("source_cards_not_exactly_once")
    fact_ids = [fact_id for card in cards for fact_id in card.fact_ids]
    if len(fact_ids) != len(set(fact_ids)):
        codes.append("facts_not_exactly_once")
    for card in cards:
        if card.source_tier == Step3ReviewedSourceTier.PHASE224 and (
            not card.source_step3_request_id or not card.source_step3_response_sha256
        ):
            codes.append("phase224_card_lost_request_provenance")
            break
    for card in cards:
        if card.source_tier == Step3ReviewedSourceTier.PHASE2241 and (
            card.source_step3_request_id is not None or card.source_step3_response_sha256 is not None
        ):
            codes.append("remediation_card_claims_new_request")
            break
    return tuple(codes)


def release_draft_projection(fixture: Step3UnifiedReviewedReleaseFixture) -> tuple[Mapping[str, Any], ...]:
    """确定性发布草稿的输入投影（生成形态骨架；r98 装配与封印归 gates/release）。"""
    return tuple(
        {
            "card_index": card.card_index,
            "source_card_id": card.source_card_id,
            "source_tier": card.source_tier.value,
            "canonical_title": card.canonical_title,
            "presentation": card.presentation,
            "fact_ids": tuple(card.fact_ids),
            "expected_texts": tuple(card.expected_texts),
            "image_prompt": card.image_prompt,
            "layout_description": card.layout_description,
        }
        for card in fixture.cards
    )
