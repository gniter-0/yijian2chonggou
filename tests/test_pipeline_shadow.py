"""pipeline/shadow：V1/V2 diff 判定与报告骨架。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from examcard.pipeline.shadow import (
    SMOKE_CHAPTER_PREFIXES,
    ShadowDiffReport,
    build_chapter_result,
    build_shadow_report,
    chapter_prefix,
    classify_text_conflict,
    diff_metric_mappings,
    diff_text_conflicts,
    run_shadow,
    shadow_sample_id,
    typography_only_difference,
    v1_chapter_metrics,
)


def test_chapter_prefix_and_sample_id() -> None:
    assert chapter_prefix("01 建筑工程识图") == "01"
    assert chapter_prefix("no-prefix") is None
    assert shadow_sample_id("01") == "gonglu_01_shadow"
    assert shadow_sample_id("24", subject="jizhu") == "jizhu_24_shadow"
    assert SMOKE_CHAPTER_PREFIXES == ("01", "24", "41", "55", "62")


def test_typography_only_difference_semantics() -> None:
    assert typography_only_difference("混凝土 强度，等级。", "混凝土强度等级") is True
    assert typography_only_difference("强度等级 C30", "强度等级 C35") is False
    assert typography_only_difference("，。", "，，") is False  # 剥离后为空不算等值


def test_classify_text_conflict_tiers() -> None:
    assert classify_text_conflict("排版 差异，", "排版差异") == "typography_only"
    assert classify_text_conflict("不得大于 5", "不得大于 6") == "high_risk"
    assert classify_text_conflict("必须设置围挡", "应设置围挡") == "high_risk"
    assert classify_text_conflict("砌体结构裂缝", "砌体结构裂纹") == "normal"


def test_v1_chapter_metrics_snapshot(tmp_path: Path) -> None:
    missing = v1_chapter_metrics(tmp_path / "absent.json", tmp_path)
    assert missing == {"path": None, "sha256": None, "knowledge_point_count": None, "content_kp_count": None, "exercise_kp_count": None}
    target = tmp_path / "01.json"
    target.write_text(
        json.dumps(
            {
                "knowledge_points": [
                    {"id": "kp_1", "kp_type": "content"},
                    {"id": "kp_2_q1", "kp_type": "content"},
                    {"id": "kp_3", "kp_type": "exercise"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    metrics = v1_chapter_metrics(target, tmp_path)
    assert metrics["path"] == "01.json"
    assert metrics["knowledge_point_count"] == 3
    assert metrics["content_kp_count"] == 1  # "_q" 与 exercise 均按老口径剔除
    assert metrics["exercise_kp_count"] == 2


def test_diff_metric_mappings_records_only_drift() -> None:
    entries = diff_metric_mappings("01", {"cards": 16, "facts": 28}, {"cards": 16, "facts": 27, "images": 3})
    fields = {item.field: item for item in entries}
    assert set(fields) == {"facts", "images"}
    assert fields["facts"].detail == "value drift"
    assert fields["images"].detail == "missing on one side"
    assert fields["facts"].v1_value == "28" and fields["facts"].v2_value == "27"


def test_diff_text_conflicts_classifies() -> None:
    entries = diff_text_conflicts(
        "01",
        [
            ("kp_title", "同一 文本，", "同一文本"),
            ("kp_value", "不得大于 5", "不得大于 6"),
            ("kp_same", "一致", "一致"),
        ],
    )
    assert [item.severity for item in entries] == ["typography_only", "high_risk"]


def test_chapter_result_consistency() -> None:
    matched = build_chapter_result("01", (), compared_field_count=5)
    assert matched.matched is True
    payload = matched.model_dump(mode="json")
    payload["matched"] = False
    with pytest.raises(ValidationError, match="match flag"):
        type(matched).model_validate(payload)


def test_shadow_report_summary_and_seal() -> None:
    diff_entries = diff_metric_mappings("24", {"facts": 28}, {"facts": 27})
    chapters = (
        build_chapter_result("01", (), compared_field_count=2),
        build_chapter_result("24", diff_entries, compared_field_count=1),
    )
    report = build_shadow_report("shadow-run-1", chapters)
    assert report.chapter_count == 2
    assert report.matched_chapter_count == 1 and report.differing_chapter_count == 1
    assert report.normal_entry_count == 1 and report.all_matched is False
    tampered = report.model_dump(mode="json")
    tampered["all_matched"] = True
    with pytest.raises(ValidationError):
        ShadowDiffReport.model_validate(tampered)


def test_run_shadow_with_injected_loaders() -> None:
    v1 = {"01": {"facts": 28, "cards": 16}, "24": {"facts": 30, "cards": 16}}
    v2 = {"01": {"facts": 28, "cards": 16}, "24": {"facts": 29, "cards": 16}}
    report = run_shadow("shadow-run-2", ("01", "24"), v1.__getitem__, v2.__getitem__)
    assert report.all_matched is False
    by_prefix = {item.chapter_prefix: item for item in report.chapters}
    assert by_prefix["01"].matched is True
    assert by_prefix["24"].entries[0].field == "facts"
    with pytest.raises(ValueError, match="at least one chapter"):
        run_shadow("shadow-run-3", (), v1.__getitem__, v2.__getitem__)
