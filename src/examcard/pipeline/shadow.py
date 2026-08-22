"""shadow run + V1/V2 diff 报告骨架（规范源 scripts/tools/run_v2_shadow.py 的形态提炼）。

本节点只落判定与报告骨架：章前缀/样本 id 口径、V1 章指标快照、排版差异归一
（NFKC + 排版字符剥离）、高危冲突分诊正则、逐章指标 diff 与汇总封印。
实际 65 章跑批、evidence 重绑定、vision 复核循环不在本节点范围（留待跑批节点，
彼时 v1_loader/v2_runner 以真实实现注入——本骨架保持注入式与零网络）。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Final, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ..common.hashing import SHA256_HEX, canonical_hash, hash_model
from ..materials.schema import StrictModel

# Final（不带显式注解）让 mypy 保留字面量类型，这个常量才能当 Literal 字段的默认值用；
# 写成裸赋值会推成 str，赋给 Literal[...] 字段即报 assignment。
DIFF_REPORT_VERSION: Final = "examcard_shadow_diff_report_v1"

# 老工具冒烟章前缀（01/24/41/55/62 五章代表集）。
SMOKE_CHAPTER_PREFIXES: tuple[str, ...] = ("01", "24", "41", "55", "62")

CHAPTER_PREFIX_PATTERN = re.compile(r"^(\d{2})\s")
# 高危冲突分诊（数字/比较与运算符号/义务性措辞/答案例题——命中即 high_risk）。
HIGH_RISK_CONFLICT = re.compile(r"[0-9]|[=＝≈≠≤≥<>±×÷+\-−—–]|不得|不应|严禁|必须|不超过|不少于|大于|小于|答案|例题")
# 排版字符集（空白/全角空格/中英标点/引号），剥离后等值即视为排版差异。
TYPOGRAPHY_ONLY = re.compile(r"[\s\u3000,，.。:：;；'\"“”‘’`~～]")

Severity = Literal["normal", "typography_only", "high_risk"]


def chapter_prefix(name: str) -> str | None:
    match = CHAPTER_PREFIX_PATTERN.match(name)
    return match.group(1) if match else None


def shadow_sample_id(prefix: str, subject: str = "gonglu") -> str:
    return f"{subject}_{prefix}_shadow"


def typography_only_difference(left: str, right: str) -> bool:
    normalized_left = TYPOGRAPHY_ONLY.sub("", unicodedata.normalize("NFKC", left))
    normalized_right = TYPOGRAPHY_ONLY.sub("", unicodedata.normalize("NFKC", right))
    return bool(normalized_left) and normalized_left == normalized_right


def classify_text_conflict(native: str, visual: str) -> Severity:
    if typography_only_difference(native, visual):
        return "typography_only"
    if HIGH_RISK_CONFLICT.search(native) or HIGH_RISK_CONFLICT.search(visual):
        return "high_risk"
    return "normal"


def v1_chapter_metrics(path: Path, repository_root: Path) -> dict[str, Any]:
    """V1 结构化产物的章指标快照（缺席时 None 形态；kp 判定口径与老工具一致）。"""
    if not path.is_file():
        return {"path": None, "sha256": None, "knowledge_point_count": None, "content_kp_count": None, "exercise_kp_count": None}
    payload = json.loads(path.read_text(encoding="utf-8"))
    points = payload.get("knowledge_points", []) if isinstance(payload, dict) else []
    if not isinstance(points, list):
        points = []
    return {
        "path": path.resolve().relative_to(Path(repository_root).resolve()).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "knowledge_point_count": len(points),
        "content_kp_count": sum(
            1
            for point in points
            if isinstance(point, dict) and point.get("kp_type") != "exercise" and "_q" not in str(point.get("id") or "")
        ),
        "exercise_kp_count": sum(
            1
            for point in points
            if isinstance(point, dict) and (point.get("kp_type") == "exercise" or "_q" in str(point.get("id") or ""))
        ),
    }


class ChapterDiffEntry(StrictModel):
    chapter_prefix: str = Field(pattern=r"^\d{2}$")
    field: str = Field(min_length=1)
    v1_value: str | None = None
    v2_value: str | None = None
    severity: Severity
    detail: str = Field(min_length=1)


class ChapterShadowResult(StrictModel):
    chapter_prefix: str = Field(pattern=r"^\d{2}$")
    sample_id: str = Field(min_length=3)
    compared_field_count: int = Field(ge=0)
    entries: tuple[ChapterDiffEntry, ...] = ()
    matched: bool

    @model_validator(mode="after")
    def validate_result(self) -> "ChapterShadowResult":
        if self.matched != (not self.entries):
            raise ValueError("chapter match flag must reflect recorded differences")
        if any(item.chapter_prefix != self.chapter_prefix for item in self.entries):
            raise ValueError("chapter diff entry crossed chapter prefix")
        return self


class ShadowDiffReport(StrictModel):
    schema_version: Literal["examcard_shadow_diff_report_v1"] = DIFF_REPORT_VERSION
    run_id: str = Field(min_length=3)
    chapter_count: int = Field(ge=1)
    chapters: tuple[ChapterShadowResult, ...] = Field(min_length=1)
    matched_chapter_count: int = Field(ge=0)
    differing_chapter_count: int = Field(ge=0)
    high_risk_entry_count: int = Field(ge=0)
    typography_only_entry_count: int = Field(ge=0)
    normal_entry_count: int = Field(ge=0)
    all_matched: bool
    network_calls: Literal[0] = 0
    report_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_report(self) -> "ShadowDiffReport":
        if self.chapter_count != len(self.chapters):
            raise ValueError("shadow chapter count mismatch")
        if len({item.chapter_prefix for item in self.chapters}) != len(self.chapters):
            raise ValueError("shadow chapters must be exactly once")
        matched = sum(1 for item in self.chapters if item.matched)
        if (self.matched_chapter_count, self.differing_chapter_count) != (matched, len(self.chapters) - matched):
            raise ValueError("shadow chapter summary mismatch")
        severities = [entry.severity for item in self.chapters for entry in item.entries]
        expected = (
            severities.count("high_risk"),
            severities.count("typography_only"),
            severities.count("normal"),
        )
        if (self.high_risk_entry_count, self.typography_only_entry_count, self.normal_entry_count) != expected:
            raise ValueError("shadow severity summary mismatch")
        if self.all_matched != (matched == len(self.chapters)):
            raise ValueError("shadow all-matched flag mismatch")
        if self.report_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "report_sha256"})):
            raise ValueError("shadow diff report hash mismatch")
        return self


def _render_value(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def diff_metric_mappings(prefix: str, v1: Mapping[str, Any], v2: Mapping[str, Any]) -> tuple[ChapterDiffEntry, ...]:
    """逐键比对两侧指标；缺键与值漂移都记 normal 差异（文本冲突另走分诊）。"""
    entries: list[ChapterDiffEntry] = []
    for key in sorted(set(v1) | set(v2)):
        left, right = v1.get(key), v2.get(key)
        if left == right:
            continue
        detail = "missing on one side" if key not in v1 or key not in v2 else "value drift"
        entries.append(
            ChapterDiffEntry(
                chapter_prefix=prefix,
                field=key,
                v1_value=_render_value(left),
                v2_value=_render_value(right),
                severity="normal",
                detail=detail,
            )
        )
    return tuple(entries)


def diff_text_conflicts(prefix: str, conflicts: Sequence[tuple[str, str, str]]) -> tuple[ChapterDiffEntry, ...]:
    """文本冲突分诊：(field, native, visual) → 排版/高危/普通三档（等值不记）。"""
    entries: list[ChapterDiffEntry] = []
    for field_name, native, visual in conflicts:
        if native == visual:
            continue
        severity = classify_text_conflict(native, visual)
        entries.append(
            ChapterDiffEntry(
                chapter_prefix=prefix,
                field=field_name,
                v1_value=native,
                v2_value=visual,
                severity=severity,
                detail={"typography_only": "typography-only drift", "high_risk": "high-risk conflict", "normal": "text drift"}[severity],
            )
        )
    return tuple(entries)


def build_chapter_result(prefix: str, entries: Sequence[ChapterDiffEntry], *, compared_field_count: int, subject: str = "gonglu") -> ChapterShadowResult:
    return ChapterShadowResult(
        chapter_prefix=prefix,
        sample_id=shadow_sample_id(prefix, subject),
        compared_field_count=compared_field_count,
        entries=tuple(entries),
        matched=not entries,
    )


def build_shadow_report(run_id: str, chapters: Sequence[ChapterShadowResult]) -> ShadowDiffReport:
    severities = [entry.severity for item in chapters for entry in item.entries]
    matched = sum(1 for item in chapters if item.matched)
    return hash_model(
        ShadowDiffReport,
        {
            "run_id": run_id,
            "chapter_count": len(chapters),
            "chapters": tuple(chapters),
            "matched_chapter_count": matched,
            "differing_chapter_count": len(chapters) - matched,
            "high_risk_entry_count": severities.count("high_risk"),
            "typography_only_entry_count": severities.count("typography_only"),
            "normal_entry_count": severities.count("normal"),
            "all_matched": matched == len(chapters),
        },
        "report_sha256",
    )


def run_shadow(
    run_id: str,
    chapter_prefixes: Sequence[str],
    v1_loader: Callable[[str], Mapping[str, Any]],
    v2_runner: Callable[[str], Mapping[str, Any]],
    *,
    subject: str = "gonglu",
) -> ShadowDiffReport:
    """shadow run 骨架：逐章加载 V1 基线、运行注入的 V2 管线、指标 diff、汇总封印。"""
    if not chapter_prefixes:
        raise ValueError("shadow run requires at least one chapter prefix")
    chapters: list[ChapterShadowResult] = []
    for prefix in chapter_prefixes:
        v1_metrics = dict(v1_loader(prefix))
        v2_metrics = dict(v2_runner(prefix))
        entries = diff_metric_mappings(prefix, v1_metrics, v2_metrics)
        compared = len(set(v1_metrics) | set(v2_metrics))
        chapters.append(build_chapter_result(prefix, entries, compared_field_count=compared, subject=subject))
    return build_shadow_report(run_id, chapters)
