"""s3/s4 收口工具（一次性运维，shadow65 两遍式后处理，bc-31b3117c 口径）。

在第二遍 vision 复核（run_shadow_s2.py vision-pass）之后运行：

  s3  人工冲突清单：分诊三档 + 优先级排序
      Tier-1  vision 不一致（复核后新增冲突，需人工裁决，最高优先）
              + 无法定位复核区域的高危 hint（vision 打不了，只能人工）
      Tier-2  normal 冲突（OCR 与原生文本语义差异，未达高危）
      Tier-3  typography_only（纯排版噪声，仅备查）
  s4  V1/V2 差异总报告：65 章 v1 结构化指标 vs v2 证据指标 + vision 复核结论

只读输入：data/shadow_gonglu_65/**（census.json / chapter-report.json / evidence/）
          yijian2/data/structured_公路/**（老仓只读）
输出：    docs/prework/P2_s3_人工冲突清单_20260812.{md,json}
          docs/prework/P2_s4_V1V2差异总报告_20260812.md

用法（新仓根目录）：
  .venv\\Scripts\\python -X utf8 migration\\build_s3_s4_reports.py [--allow-partial]

未完成第二遍的章节默认中止（保证清单完整）；--allow-partial 时标记 pending 并继续。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from examcard.common.io import read_json  # noqa: E402
from examcard.pipeline.shadow import v1_chapter_metrics  # noqa: E402
from examcard.pipeline.shadow_wiring import _index_by_prefix, high_risk_review_hints  # noqa: E402
from examcard.utils.legacy_root import resolve_legacy_root  # noqa: E402

LEGACY = resolve_legacy_root(REPO_ROOT)
STRUCTURED_DIR = LEGACY / "data" / "structured_公路"
ROOT = REPO_ROOT / "data" / "shadow_gonglu_65"
DOCS = REPO_ROOT / "docs" / "prework"
ALL_PREFIXES = [f"{n:02d}" for n in range(1, 66)]
STAMP = "20260812"

SEVERITY_ORDER = ("high_risk", "normal", "typography_only", "unlocatable")


def _page_conflicts(chapter_dir: Path) -> list[dict]:
    conflicts: list[dict] = []
    pages_dir = chapter_dir / "evidence" / "pages"
    for page_path in sorted(pages_dir.glob("page_*.json")):
        payload = read_json(page_path)
        for conflict in payload.get("source_conflicts", []) or []:
            conflicts.append(conflict)
    return conflicts


def _crop_hint_ids(chapter_dir: Path) -> set[str]:
    crops_dir = chapter_dir / "evidence" / "crops"
    ids: set[str] = set()
    for crop in crops_dir.glob("p*_review_*.png"):
        # 文件名形如 p0004_review_triage_0004_001.png → hint_id = triage_0004_001
        name = crop.stem
        _, _, hint_id = name.partition("_review_")
        if hint_id:
            ids.add(hint_id)
    return ids


def collect_chapter(prefix: str) -> dict:
    chapter_dir = ROOT / prefix
    census = read_json(chapter_dir / "census.json")
    report_path = chapter_dir / "chapter-report.json"
    report = read_json(report_path) if report_path.is_file() else None
    vision_done = bool(report and report.get("pass") == "vision_review")

    records = list(census.get("conflicts", []))
    by_severity: dict[str, list[dict]] = {key: [] for key in SEVERITY_ORDER}
    for record in records:
        by_severity.setdefault(record.get("severity", "normal"), []).append(record)

    hints = high_risk_review_hints(records)
    hint_by_key = {(hint.page_num, hint.observed_text): hint.hint_id for hint in hints}

    added: list[dict] = []
    attempted_hint_ids: set[str] = set()
    if vision_done:
        census_ids = {record.get("conflict_id") for record in records}
        for conflict in _page_conflicts(chapter_dir):
            if conflict.get("conflict_id") not in census_ids:
                page_num = (conflict.get("locator") or {}).get("page_num")
                native_text = conflict.get("native_text") or ""
                hint_id = hint_by_key.get((page_num, native_text))
                added.append(
                    {
                        "chapter": prefix,
                        "page_num": page_num,
                        "native_text": native_text,
                        "vision_text": conflict.get("visual_text") or "",
                        "conflict_id": conflict.get("conflict_id"),
                        "hint_id": hint_id,
                        "crop": (
                            f"data/shadow_gonglu_65/{prefix}/evidence/crops/p{page_num:04d}_review_{hint_id}.png"
                            if hint_id and isinstance(page_num, int)
                            else None
                        ),
                    }
                )
        attempted_hint_ids = _crop_hint_ids(chapter_dir)

    unlocated_hints = [
        {"chapter": prefix, "page_num": hint.page_num, "native_text": hint.observed_text, "hint_id": hint.hint_id}
        for hint in hints
        if vision_done and hint.hint_id not in attempted_hint_ids
    ]

    return {
        "prefix": prefix,
        "pdf": census.get("pdf", ""),
        "vision_done": vision_done,
        "page_count": census.get("page_count"),
        "evidence_unit_count": census.get("evidence_unit_count"),
        "conflict_total": len(records),
        "severity_counts": {key: len(by_severity.get(key, [])) for key in SEVERITY_ORDER},
        "hint_count": len(hints),
        "vision_metrics": (report or {}).get("metrics"),
        "vision_added": added,
        "unlocated_hints": unlocated_hints,
        "normal_records": by_severity.get("normal", []),
        "typo_records": by_severity.get("typography_only", []),
        "unlocatable_records": by_severity.get("unlocatable", []),
    }


def _md_escape(text: str, limit: int = 120) -> str:
    flat = " ".join(str(text).split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return flat.replace("|", "\\|")


def build_s3(chapters: list[dict]) -> tuple[str, dict]:
    tier1 = [entry for chapter in chapters for entry in chapter["vision_added"]]
    tier1b = [entry for chapter in chapters for entry in chapter["unlocated_hints"]]
    tier2 = [
        {"chapter": chapter["prefix"], **{k: record.get(k) for k in ("page_num", "native_text", "visual_text", "conflict_id")}}
        for chapter in chapters
        for record in chapter["normal_records"]
    ]
    tier3_count = sum(chapter["severity_counts"]["typography_only"] for chapter in chapters)
    unlocatable_count = sum(chapter["severity_counts"]["unlocatable"] for chapter in chapters)
    pending = [chapter["prefix"] for chapter in chapters if not chapter["vision_done"]]

    confirmed = sum(
        1
        for chapter in chapters
        if chapter["vision_done"]
        for _ in range(max(chapter["hint_count"] - len(chapter["unlocated_hints"]), 0))
    ) - len(tier1)

    machine = {
        "generated": STAMP,
        "pending_chapters": pending,
        "tier1_vision_disagreed": tier1,
        "tier1b_unlocated_high_risk_hints": tier1b,
        "tier2_normal": tier2,
        "tier3_typography_only_count": tier3_count,
        "census_unlocatable_count": unlocatable_count,
        "vision_confirmed_hint_count": confirmed,
    }

    lines: list[str] = []
    lines.append(f"# s3 人工冲突清单（公路 65 章 shadow 两遍式） — {STAMP}")
    lines.append("")
    lines.append("> 生成器：`migration/build_s3_s4_reports.py`（census + vision 复核产物聚合，只读）。")
    lines.append("> 优先级自上而下处理；Tier-1 为必须人工裁决项。")
    lines.append("")
    if pending:
        lines.append(f"**⚠ 部分章节第二遍未完成（{len(pending)}）**：{', '.join(pending)}；本清单为部分结果。")
        lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 指标 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| Tier-1 vision 不一致（人工裁决） | {len(tier1)} |")
    lines.append(f"| Tier-1b 高危 hint 无法定位复核区域（人工核对原页） | {len(tier1b)} |")
    lines.append(f"| Tier-2 normal 冲突（低优先级抽查） | {len(tier2)} |")
    lines.append(f"| Tier-3 typography_only（纯排版，仅备查，不列明细） | {tier3_count} |")
    lines.append(f"| census 缺 native 文本（unlocatable，仅备查） | {unlocatable_count} |")
    lines.append(f"| vision 确认原文无误的高危 hint | {confirmed} |")
    lines.append("")

    lines.append("## Tier-1：vision 不一致（最高优先）")
    lines.append("")
    if tier1:
        lines.append("| 章 | 页 | 原生文本 | vision 转录 | 裁片 |")
        lines.append("|---|---|---|---|---|")
        for entry in sorted(tier1, key=lambda item: (item["chapter"], item.get("page_num") or 0)):
            crop = entry.get("crop") or "-"
            lines.append(
                f"| {entry['chapter']} | {entry.get('page_num', '-')} | {_md_escape(entry['native_text'])} "
                f"| {_md_escape(entry['vision_text'])} | {crop} |"
            )
    else:
        lines.append("（无 — 全部高危 hint 均被 vision 确认或落入 Tier-1b）")
    lines.append("")

    lines.append("## Tier-1b：高危 hint 无法定位复核区域")
    lines.append("")
    if tier1b:
        lines.append("| 章 | 页 | 原生文本 | hint |")
        lines.append("|---|---|---|---|")
        for entry in sorted(tier1b, key=lambda item: (item["chapter"], item["page_num"])):
            lines.append(
                f"| {entry['chapter']} | {entry['page_num']} | {_md_escape(entry['native_text'])} | {entry['hint_id']} |"
            )
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## Tier-2：normal 冲突（抽查即可）")
    lines.append("")
    lines.append(f"共 {len(tier2)} 条，明细见同名 `.json`（`tier2_normal`）。分章分布：")
    lines.append("")
    lines.append("| 章 | normal 条数 |")
    lines.append("|---|---|")
    for chapter in chapters:
        count = chapter["severity_counts"]["normal"]
        if count:
            lines.append(f"| {chapter['prefix']} | {count} |")
    lines.append("")
    lines.append("## Tier-3：typography_only")
    lines.append("")
    lines.append(f"共 {tier3_count} 条纯排版差异（①/空格/全半角等），不需人工处理，明细可按需从各章 census.json 提取。")
    lines.append("")
    return "\n".join(lines) + "\n", machine


def build_s4(chapters: list[dict], structured_index: dict[str, Path]) -> str:
    total = {
        "pages": sum(chapter["page_count"] or 0 for chapter in chapters),
        "units": sum(chapter["evidence_unit_count"] or 0 for chapter in chapters),
        "conflicts": sum(chapter["conflict_total"] for chapter in chapters),
        "high_risk": sum(chapter["severity_counts"]["high_risk"] for chapter in chapters),
        "normal": sum(chapter["severity_counts"]["normal"] for chapter in chapters),
        "typo": sum(chapter["severity_counts"]["typography_only"] for chapter in chapters),
        "hints": sum(chapter["hint_count"] for chapter in chapters),
        "disagreed": sum(len(chapter["vision_added"]) for chapter in chapters),
        "unlocated": sum(len(chapter["unlocated_hints"]) for chapter in chapters),
    }
    done = [chapter for chapter in chapters if chapter["vision_done"]]
    pending = [chapter["prefix"] for chapter in chapters if not chapter["vision_done"]]
    vision_calls = sum((chapter["vision_metrics"] or {}).get("vision_calls_used", 0) for chapter in done)
    vision_failures = sum((chapter["vision_metrics"] or {}).get("vision_failures", 0) for chapter in done)

    v1_totals = {"knowledge_point_count": 0, "content_kp_count": 0, "exercise_kp_count": 0}
    v1_rows: dict[str, dict] = {}
    for chapter in chapters:
        path = structured_index.get(chapter["prefix"])
        metrics = v1_chapter_metrics(path, LEGACY) if path else v1_chapter_metrics(Path("__missing__"), LEGACY)
        v1_rows[chapter["prefix"]] = metrics
        for key in v1_totals:
            if metrics.get(key):
                v1_totals[key] += metrics[key]

    lines: list[str] = []
    lines.append(f"# s4 V1/V2 差异总报告（公路 65 章 shadow 两遍式） — {STAMP}")
    lines.append("")
    lines.append("> 生成器：`migration/build_s3_s4_reports.py`。V1 = 老仓 structured_公路 结构化产物（只读）；")
    lines.append("> V2 = exam-card-engine 证据引擎（native+RapidOCR+fusion，两遍式 vision 复核）。")
    lines.append("")
    if pending:
        lines.append(f"**⚠ 第二遍未完成章节（{len(pending)}）**：{', '.join(pending)}；vision 结论为部分结果。")
        lines.append("")
    lines.append("## 全局结论")
    lines.append("")
    lines.append("| 口径 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| V2 页面 / 证据单元 | {total['pages']} / {total['units']} |")
    lines.append(f"| V1 知识点（内容/例题） | {v1_totals['knowledge_point_count']}（{v1_totals['content_kp_count']}/{v1_totals['exercise_kp_count']}） |")
    lines.append(f"| 源冲突（第一遍普查） | {total['conflicts']} = high_risk {total['high_risk']} + normal {total['normal']} + typography_only {total['typo']} |")
    lines.append(f"| 高危复核 hint（去重后） | {total['hints']} |")
    lines.append(f"| vision 实际调用 / 失败 | {vision_calls} / {vision_failures}（完成 {len(done)}/65 章） |")
    lines.append(f"| vision 不一致（转人工 Tier-1） | {total['disagreed']} |")
    lines.append(f"| 无法定位复核区域（转人工 Tier-1b） | {total['unlocated']} |")
    confirmed = total["hints"] - total["disagreed"] - total["unlocated"] if not pending else None
    if confirmed is not None:
        rate = (confirmed / total["hints"] * 100) if total["hints"] else 100.0
        lines.append(f"| vision 确认原文无误 | {confirmed}（{rate:.1f}% of hints） |")
    lines.append("")
    lines.append("解读：V2 证据引擎的高危冲突绝大多数为 OCR 噪声而非原文错误时，vision 确认率应显著偏高；")
    lines.append("vision 不一致项才是真正需要人工与 V1 老产物对照修正的疑点（见 s3 清单 Tier-1）。")
    lines.append("")
    lines.append("## 分章明细")
    lines.append("")
    lines.append("| 章 | V1 KP(内容/例题) | V2 单元 | 冲突(高危) | hints | vision 调用/失败 | 不一致 | 状态 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for chapter in chapters:
        v1 = v1_rows[chapter["prefix"]]
        v1_cell = (
            f"{v1['knowledge_point_count']}({v1['content_kp_count']}/{v1['exercise_kp_count']})"
            if v1.get("knowledge_point_count") is not None
            else "缺"
        )
        metrics = chapter["vision_metrics"] or {}
        vision_cell = (
            f"{metrics.get('vision_calls_used', 0)}/{metrics.get('vision_failures', 0)}" if chapter["vision_done"] else "-"
        )
        status = "done" if chapter["vision_done"] else "pending"
        lines.append(
            f"| {chapter['prefix']} | {v1_cell} | {chapter['evidence_unit_count']} "
            f"| {chapter['conflict_total']}({chapter['severity_counts']['high_risk']}) | {chapter['hint_count']} "
            f"| {vision_cell} | {len(chapter['vision_added'])} | {status} |"
        )
    lines.append("")
    lines.append("## 方法与边界")
    lines.append("")
    lines.append("- 两遍式（bc-31b3117c）：第一遍零 API 普查产出 census + 分诊；第二遍仅对 high_risk 造 hint 打真实 vision。")
    lines.append("- vision 从不改写原生证据：转录包含原生文本 → 确认；否则新增冲突并把重叠单元标 `vision_conflict` 送人工。")
    lines.append("- 预算熔断器持久化于 `data/shadow_gonglu_65/vision-budget.json`（用户裁决 A：上限 700）。")
    lines.append("- 老仓 yijian2 全程只读；跑批数据不入库；本报告与 s3 清单入库。")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="build s3 conflict worklist and s4 diff report")
    parser.add_argument("--allow-partial", action="store_true", help="允许部分章节第二遍未完成")
    args = parser.parse_args()

    missing_census = [prefix for prefix in ALL_PREFIXES if not (ROOT / prefix / "census.json").is_file()]
    if missing_census:
        print(f"ABORT: census incomplete, missing {missing_census}", flush=True)
        return 2

    chapters = [collect_chapter(prefix) for prefix in ALL_PREFIXES]
    pending = [chapter["prefix"] for chapter in chapters if not chapter["vision_done"]]
    if pending and not args.allow_partial:
        print(f"ABORT: vision pass incomplete for {pending}; rerun later or pass --allow-partial", flush=True)
        return 2

    structured_index = _index_by_prefix(STRUCTURED_DIR, ".json")

    s3_md, s3_json = build_s3(chapters)
    s4_md = build_s4(chapters, structured_index)

    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / f"P2_s3_人工冲突清单_{STAMP}.md").write_text(s3_md, encoding="utf-8")
    (DOCS / f"P2_s3_人工冲突清单_{STAMP}.json").write_text(
        json.dumps(s3_json, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (DOCS / f"P2_s4_V1V2差异总报告_{STAMP}.md").write_text(s4_md, encoding="utf-8")
    print(
        json.dumps(
            {
                "pending_chapters": pending,
                "tier1": len(s3_json["tier1_vision_disagreed"]),
                "tier1b": len(s3_json["tier1b_unlocated_high_risk_hints"]),
                "tier2": len(s3_json["tier2_normal"]),
                "tier3": s3_json["tier3_typography_only_count"],
                "outputs": [
                    f"docs/prework/P2_s3_人工冲突清单_{STAMP}.md",
                    f"docs/prework/P2_s3_人工冲突清单_{STAMP}.json",
                    f"docs/prework/P2_s4_V1V2差异总报告_{STAMP}.md",
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
