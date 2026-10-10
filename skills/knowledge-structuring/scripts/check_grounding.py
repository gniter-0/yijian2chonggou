#!/usr/bin/env python3
"""check_grounding.py - 知识点逐字溯源核对（标准库实现，学科无关）。

用法:
    python check_grounding.py extraction.json structured.json [--report report.md] [--json out.json]

检查项（对应 SKILL.md 2.6 / 2.8）:
  1. 片段溯源  content 按换行/。/；切片，NFKC+去空白后必须是所引页原文的子串
  2. 数字溯源  content 中每个数字必须出现在所引页原文中
  3. 表格溯源  table 的每个非空单元格必须是所引页原文的子串
  4. 强调溯源  annotations 必须是 content 的子串
  5. 序号计数  content 中序号条目数 == point_count（point_count>0 时）
  6. 页码合法  source_pages 非空且在范围内
  7. 完全重复  不得有 content 完全相同的知识点
  8. 页面覆盖  有正文但无知识点引用的页（警告）
  9. 行覆盖    原文中没有被任何知识点（content/table/title）覆盖的行（警告；--strict-coverage 时算失败）
退出码: 0 = 全部通过；1 = 存在失败项。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

_WS = re.compile(r"\s+")
_SPLIT = re.compile(r"[\n。；;]+")
_NUM = re.compile(r"\d+(?:\.\d+)?")
_MARKERS = [
    re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]"),
    re.compile(r"[（(]\d{1,2}[）)]"),
    re.compile(r"(?<![\d.（(])\d{1,2}[）)]"),
]
SKIP_TYPES = {"fragment"}
_NUMERIC_CELL = re.compile(r"^[<>≥≤~±%.\d\-]+$")


def norm(text: str) -> str:
    return _WS.sub("", unicodedata.normalize("NFKC", text or ""))


def count_markers(text: str) -> int:
    return max(len(set(p.findall(text))) for p in _MARKERS)


def best_hint(segment: str, source: str) -> str:
    """给未溯源片段找原文中最像的一段，方便人工定位。"""
    if not source:
        return ""
    n = len(segment)
    best, best_r = "", 0.0
    step = max(1, n // 4)
    for i in range(0, max(1, len(source) - n + 1), step):
        cand = source[i:i + n]
        r = difflib.SequenceMatcher(None, segment, cand).ratio()
        if r > best_r:
            best, best_r = cand, r
    return f"{best}（相似度 {best_r:.2f}）" if best_r >= 0.5 else ""


def kp_text(kp: dict) -> str:
    table = kp.get("table") or {}
    parts = [kp.get("title", ""), kp.get("content", "")]
    parts += [g.get("text", "") if isinstance(g, dict) else str(g) for g in table.get("header_groups") or []]
    parts += [str(c) for c in table.get("headers") or []]
    parts += [str(c) for row in table.get("rows") or [] for c in row]
    return norm("".join(parts))


def line_coverage(pages: dict[int, str], points: list[dict], dropped: list[dict] | None = None) -> dict[int, list[str]]:
    """原文每一行（去空白后 >=2 字）应出现在引用该页的某个知识点里，否则视为可能遗漏。"""
    by_page: dict[int, str] = {}
    for kp in points:
        if kp.get("kp_type") in SKIP_TYPES:
            continue
        t = kp_text(kp)
        for x in kp.get("source_pages") or []:
            by_page[int(x)] = by_page.get(int(x), "") + "\u0000" + t
    for d in dropped or []:  # 有意删除并登记原因的原文行（页码标注、噪声等）
        by_page[int(d["page"])] = by_page.get(int(d["page"]), "") + "\u0000" + norm(d.get("text", ""))
    missing: dict[int, list[str]] = {}
    for num, text in pages.items():
        hay = by_page.get(num, "")
        for line in text.split("\n"):
            s = norm(line)
            if len(s) >= 2 and s not in hay:
                missing.setdefault(num, []).append(line.strip())
    return missing


def check(extraction: dict, structured: dict, strict_coverage: bool = False) -> dict:
    pages = {int(p["page_num"]): p.get("text", "") for p in extraction["pages"]}
    page_norm = {k: norm(v) for k, v in pages.items()}
    page_nums = {k: set(_NUM.findall(unicodedata.normalize("NFKC", v))) for k, v in pages.items()}
    points = structured.get("knowledge_points", [])
    failures: list[dict] = []
    warnings: list[str] = []
    seen: dict[str, str] = {}
    covered: set[int] = set()

    def fail(pid: str, kind: str, detail: str) -> None:
        failures.append({"id": pid, "kind": kind, "detail": detail})

    for idx, kp in enumerate(points):
        pid = str(kp.get("id") or f"#{idx + 1}")
        if kp.get("kp_type") in SKIP_TYPES:
            continue
        sp = [int(x) for x in kp.get("source_pages") or []]
        bad_pages = [x for x in sp if x not in pages]
        if not sp:
            fail(pid, "页码", "source_pages 为空")
            continue
        if bad_pages:
            fail(pid, "页码", f"超出范围: {bad_pages}")
        sp = [x for x in sp if x in pages]
        covered.update(sp)
        source = "".join(page_norm[x] for x in sp)
        src_nums = set().union(*(page_nums[x] for x in sp)) if sp else set()
        content = kp.get("content", "") or ""

        for seg in _SPLIT.split(content):
            s = norm(seg)
            if len(s) >= 2 and s not in source:
                hint = best_hint(s, source)
                fail(pid, "片段", f"「{seg.strip()}」" + (f" → 原文最接近: {hint}" if hint else ""))

        for n in sorted(set(_NUM.findall(unicodedata.normalize("NFKC", content)))):
            if n not in src_nums:
                fail(pid, "数字", f"{n} 不在第 {sp} 页原文中")

        table = kp.get("table") or {}
        groups = [g.get("text", "") if isinstance(g, dict) else str(g) for g in table.get("header_groups") or []]
        for cell in groups + list(table.get("headers") or []) + [c for row in table.get("rows") or [] for c in row]:
            c = norm(str(cell))
            if not c:
                continue
            if _NUMERIC_CELL.match(c):  # 纯数字格：逐个数字按"整数字"比对，防止 9 借 1.9 蒙混
                lost = [n for n in _NUM.findall(c) if n not in src_nums]
                if lost:
                    fail(pid, "表格", f"数字格「{cell}」中的 {lost} 不在原文中")
            elif c not in source:
                fail(pid, "表格", f"单元格「{cell}」不在原文中")

        cn = norm(content)
        for a in kp.get("annotations") or []:
            if norm(a) and norm(a) not in cn:
                fail(pid, "强调", f"「{a}」不是 content 的子串")

        pc = kp.get("point_count")
        try:
            pc = int(pc)
        except (TypeError, ValueError):
            pc = 0
        if pc > 0 and count_markers(content) != pc:
            fail(pid, "序号", f"point_count={pc}，content 中实际 {count_markers(content)} 条")

        if cn and cn in seen:
            fail(pid, "重复", f"与 {seen[cn]} 内容完全相同")
        seen.setdefault(cn, pid)

    uncovered = sorted(k for k, v in pages.items() if v.strip() and k not in covered)
    if uncovered:
        warnings.append(f"有正文但未被任何知识点引用的页: {uncovered}（请确认是练习题/目录/误删）")
    dropped = structured.get("dropped_lines") or []
    for d in dropped:
        if not d.get("reason"):
            fail(f"第{d.get('page')}页", "删除登记", f"「{d.get('text')}」缺少删除原因")
        elif norm(d.get("text", "")) not in page_norm.get(int(d.get("page", 0)), ""):
            fail(f"第{d.get('page')}页", "删除登记", f"「{d.get('text')}」不在该页原文中")
    missing_lines = line_coverage(pages, points, dropped)
    n_missing = sum(len(v) for v in missing_lines.values())
    for num, lines in sorted(missing_lines.items()):
        for line in lines:
            if strict_coverage:
                fail(f"第{num}页", "遗漏", f"原文行「{line}」未被任何知识点覆盖")
            else:
                warnings.append(f"第{num}页原文行未被覆盖（可能遗漏）:「{line}」")

    return {
        "total_points": len(points),
        "failed_points": len({f["id"] for f in failures}),
        "failures": failures,
        "warnings": warnings,
        "uncovered_lines": n_missing,
        "passed": not failures,
    }


def render(result: dict) -> str:
    lines = ["# 溯源核对报告", "",
             f"- 知识点总数: {result['total_points']}",
             f"- 未通过知识点: {result['failed_points']}",
             f"- 未被覆盖的原文行: {result['uncovered_lines']}",
             f"- 结论: {'✅ 全部通过' if result['passed'] else '❌ 存在未溯源内容'}", ""]
    if result["warnings"]:
        lines += ["## 警告", ""] + [f"- {w}" for w in result["warnings"]] + [""]
    if result["failures"]:
        lines += ["## 失败明细", "", "| 知识点 | 类型 | 说明 |", "|---|---|---|"]
        lines += [f"| {f['id']} | {f['kind']} | {f['detail'].replace('|', '/')} |" for f in result["failures"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 GBK，统一输出 UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="知识点逐字溯源核对")
    ap.add_argument("extraction")
    ap.add_argument("structured")
    ap.add_argument("--report", help="输出 Markdown 报告路径")
    ap.add_argument("--json", help="输出 JSON 结果路径")
    ap.add_argument("--strict-coverage", action="store_true", help="原文行未覆盖也算失败")
    args = ap.parse_args()
    ext = json.loads(Path(args.extraction).read_text(encoding="utf-8"))
    st = json.loads(Path(args.structured).read_text(encoding="utf-8"))
    result = check(ext, st, strict_coverage=args.strict_coverage)
    text = render(result)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.write(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
