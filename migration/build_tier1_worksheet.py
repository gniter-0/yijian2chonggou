"""Tier-1 裁决预审工作台（一次性运维工具，s3 清单下游）。

把 s3 清单的 tier1_vision_disagreed 分成两桶：
  A. 排版级（归一化后 native == vision：下标/空格/全半角/装饰符差异）——机判免人工；
  B. 语义级（归一化后仍不同 / vision 转录为空）——逐条读裁片图人工/预审裁决。

子命令：
  bucket    归一化分桶，产出工作清单 JSON（B 桶待读图，verdict 留空）
  render    读回填好 verdict 的工作清单，渲染最终工作表 markdown

用法（新仓根目录）：
  .venv\\Scripts\\python -X utf8 migration\\build_tier1_worksheet.py bucket
  .venv\\Scripts\\python -X utf8 migration\\build_tier1_worksheet.py render
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STAMP = "20260812"
S3_JSON = REPO_ROOT / "docs" / "prework" / f"P2_s3_人工冲突清单_{STAMP}.json"
WORK_JSON = REPO_ROOT / "data" / "shadow_gonglu_65" / "_tier1_worksheet_work.json"
OUT_MD = REPO_ROOT / "docs" / "prework" / f"P2_s3_Tier1裁决建议_{STAMP}.md"

SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")
# 装饰性符号：出现与否不改变语义（列表符/箭头/项目符）
DECOR = set("➢►▶▷•·●○◦■□▪▫★☆–—―‐-‑‒−")


def normalize(text: str) -> str:
    out: list[str] = []
    for ch in unicodedata.normalize("NFKC", text or ""):
        if ch.isspace() or ch in DECOR:
            continue
        out.append(ch)
    return "".join(out).translate(SUBSCRIPT).lower()


def cmd_bucket() -> int:
    tier1 = json.loads(S3_JSON.read_text(encoding="utf-8"))["tier1_vision_disagreed"]
    items = []
    stats = Counter()
    for idx, entry in enumerate(tier1):
        native, vision = entry["native_text"], entry["vision_text"]
        if not vision.strip():
            bucket = "B_empty"
        elif normalize(native) == normalize(vision):
            bucket = "A_typo"
        else:
            bucket = "B_semantic"
        stats[bucket] += 1
        items.append({"idx": idx, **entry, "bucket": bucket, "verdict": "", "note": ""})
    WORK_JSON.parent.mkdir(parents=True, exist_ok=True)
    WORK_JSON.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"total": len(items), **stats}, ensure_ascii=False))
    for item in items:
        if item["bucket"] != "A_typo":
            print(f"[{item['idx']:03d}] ch{item['chapter']} p{item['page_num']} {item['bucket']}")
    return 0


VERDICT_LABEL = {
    "native_ok": "原文无误（vision 误读/幻读）",
    "layout": "排版/截取级差异，无实质问题",
    "pdf_text_suspect": "原生文本层与版面印刷不符，需人工核对",
    "real_issue": "实质差异，需人工裁决",
}


def cmd_render() -> int:
    items = json.loads(WORK_JSON.read_text(encoding="utf-8"))["items"]
    a_items = [i for i in items if i["bucket"] == "A_typo"]
    b_items = [i for i in items if i["bucket"] != "A_typo"]
    missing = [i["idx"] for i in b_items if not i["verdict"]]
    if missing:
        print(f"ABORT: {len(missing)} B-bucket items lack verdict: {missing}")
        return 2
    need_human = [i for i in b_items if i["verdict"] in ("pdf_text_suspect", "real_issue")]
    auto_ok = [i for i in b_items if i["verdict"] in ("native_ok", "layout")]

    def esc(text: str, limit: int = 90) -> str:
        flat = " ".join(str(text).split())
        if len(flat) > limit:
            flat = flat[: limit - 1] + "…"
        return flat.replace("|", "\\|")

    lines: list[str] = []
    lines.append(f"# Tier-1 裁决建议工作表（shadow65 s3 清单预审） — {STAMP}")
    lines.append("")
    lines.append("> 生成器：`migration/build_tier1_worksheet.py`（归一化机判 + 裁片图逐条预审）。")
    lines.append("> 目的：把 s3 清单 161 条 Tier-1 压缩到真正需要人工拍板的少数条目。")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 桶 | 条数 | 处置 |")
    lines.append("|---|---|---|")
    lines.append(f"| A. 排版级（机判：下标/空格/全半角/装饰符） | {len(a_items)} | 免人工，无需任何处理 |")
    lines.append(f"| B1. 读图预审=原文无误 / 截取级 | {len(auto_ok)} | 免人工（建议抽查 2-3 条复核预审质量） |")
    lines.append(f"| B2. 读图预审=真疑点 | **{len(need_human)}** | **请逐条人工拍板（下表）** |")
    lines.append("")
    lines.append("## B2：真疑点，请人工拍板")
    lines.append("")
    if need_human:
        lines.append("| # | 章 | 页 | 原生文本 | vision 转录 | 预审判断 | 裁片 |")
        lines.append("|---|---|---|---|---|---|---|")
        for item in need_human:
            lines.append(
                f"| {item['idx']:03d} | {item['chapter']} | {item['page_num']} | {esc(item['native_text'])} "
                f"| {esc(item['vision_text'])} | {VERDICT_LABEL[item['verdict']]}：{esc(item['note'], 160)} | {item['crop']} |"
            )
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## B1：读图预审已排除（原文无误 / 截取级）")
    lines.append("")
    lines.append("| # | 章 | 页 | 原生文本 | vision 转录 | 预审判断 |")
    lines.append("|---|---|---|---|---|---|")
    for item in auto_ok:
        lines.append(
            f"| {item['idx']:03d} | {item['chapter']} | {item['page_num']} | {esc(item['native_text'])} "
            f"| {esc(item['vision_text'])} | {VERDICT_LABEL[item['verdict']]}：{esc(item['note'], 120)} |"
        )
    lines.append("")
    lines.append("## A：排版级机判明细（免人工）")
    lines.append("")
    lines.append("归一化（NFKC + 去空白 + 去装饰符 + 下标折叠 + 大小写折叠）后 native == vision，语义零差异。")
    lines.append("")
    lines.append("| # | 章 | 页 | 原生文本（截断） |")
    lines.append("|---|---|---|---|")
    for item in a_items:
        lines.append(f"| {item['idx']:03d} | {item['chapter']} | {item['page_num']} | {esc(item['native_text'])} |")
    lines.append("")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"a_typo": len(a_items), "b1_cleared": len(auto_ok), "b2_need_human": len(need_human), "out": str(OUT_MD.relative_to(REPO_ROOT))}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="tier1 adjudication worksheet builder")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bucket")
    sub.add_parser("render")
    args = parser.parse_args()
    return cmd_bucket() if args.command == "bucket" else cmd_render()


if __name__ == "__main__":
    raise SystemExit(main())
