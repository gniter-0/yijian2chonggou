"""pilot handoff 禁带字段 + r2242 双层合并判定。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from examcard.gates.review.handoff import (
    FORBIDDEN_HANDOFF_FIELDS,
    forbidden_handoff_fields_absent,
    validate_handoff_fields,
)
from examcard.gates.review.release_merge import (
    Step3ReviewedSourceTier,
    merge_failure_codes,
    reconcile_release_tiers,
    release_draft_projection,
)


def test_forbidden_handoff_fields_detection() -> None:
    assert forbidden_handoff_fields_absent({"cards": [{"source_card_id": "x", "image_sha256": "y"}]}) is True
    assert forbidden_handoff_fields_absent({"cards": [{"golden_title": "泄漏"}]}) is False
    assert forbidden_handoff_fields_absent({"nested": [{"deep": {"Watermark": True}}]}) is False
    with pytest.raises(ValueError, match="forbidden review-semantic fields"):
        validate_handoff_fields({"review_state": "reviewed"})
    assert "image_prompt" in FORBIDDEN_HANDOFF_FIELDS


def _card(index: int, tier: Step3ReviewedSourceTier, fact_ids: tuple[str, ...], *, with_request: bool) -> SimpleNamespace:
    return SimpleNamespace(
        card_index=index,
        source_card_id=f"v25-release-card:{index:024x}",
        source_tier=tier,
        fact_ids=fact_ids,
        display_text_provenance=(SimpleNamespace(text=f"文本{index}"),),
        semantic_edge_provenance=(),
        source_step3_request_id=f"request:{index}" if with_request else None,
        source_step3_response_sha256=("a" * 64) if with_request else None,
        canonical_title=f"标题{index}",
        presentation="diagram",
        expected_texts=(f"文本{index}",),
        image_prompt=f"包含 文本{index} 的提示词",
        layout_description="布局描述不少于十二个字符",
    )


def test_merge_failure_codes_pass_and_detect() -> None:
    cards = (
        _card(1, Step3ReviewedSourceTier.PHASE224, ("fact:0001",), with_request=True),
        _card(2, Step3ReviewedSourceTier.PHASE2241, ("fact:0002",), with_request=False),
    )
    assert merge_failure_codes(cards) == ()
    gap = (cards[0], _card(3, Step3ReviewedSourceTier.PHASE2241, ("fact:0002",), with_request=False))
    assert "card_indices_not_contiguous" in merge_failure_codes(gap)
    duplicated_fact = (cards[0], _card(2, Step3ReviewedSourceTier.PHASE2241, ("fact:0001",), with_request=False))
    assert "facts_not_exactly_once" in merge_failure_codes(duplicated_fact)
    lost_provenance = (
        _card(1, Step3ReviewedSourceTier.PHASE224, ("fact:0001",), with_request=False),
        _card(2, Step3ReviewedSourceTier.PHASE2241, ("fact:0002",), with_request=False),
    )
    assert "phase224_card_lost_request_provenance" in merge_failure_codes(lost_provenance)
    fake_request = (
        _card(1, Step3ReviewedSourceTier.PHASE224, ("fact:0001",), with_request=True),
        _card(2, Step3ReviewedSourceTier.PHASE2241, ("fact:0002",), with_request=True),
    )
    assert "remediation_card_claims_new_request" in merge_failure_codes(fake_request)


def test_reconcile_release_tiers_summary() -> None:
    cards = (
        _card(1, Step3ReviewedSourceTier.PHASE224, ("fact:0001",), with_request=True),
        _card(2, Step3ReviewedSourceTier.PHASE2241, ("fact:0002", "fact:0003"), with_request=False),
    )
    summary = reconcile_release_tiers(cards)
    assert summary["card_count"] == 2
    assert summary["tier_counts"] == {"phase2r224_reviewed": 1, "phase2r2241_remediation": 1}
    assert summary["fact_count"] == 3 and summary["fact_ids_unique"] is True
    assert summary["display_text_count"] == 2


def test_release_draft_projection_shape() -> None:
    cards = (
        _card(1, Step3ReviewedSourceTier.PHASE224, ("fact:0001",), with_request=True),
        _card(2, Step3ReviewedSourceTier.PHASE2241, ("fact:0002",), with_request=False),
    )
    fixture = SimpleNamespace(cards=cards)
    projection = release_draft_projection(fixture)  # type: ignore[arg-type]
    assert len(projection) == 2
    assert projection[0]["source_tier"] == "phase2r224_reviewed"
    assert projection[1]["fact_ids"] == ("fact:0002",)
    assert "review_basis" not in projection[0]
