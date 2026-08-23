"""shadow 分段实跑通用驱动（广播 bc-658b8def 规则；w2/w3/w4 共用）。

用法：uv run python migration/run_shadow_seg.py --from 34 --to 49 --budget-suffix w3
  - 章序：--from..--to **倒序**跑（减少与升序方碰同章）；
  - 目录即认领：开跑某章前若 data/shadow_gonglu_65/<章>/ 已存在则跳过；否则先建目录占坑；
    完成标记 chapter-report.json 存在也跳；
  - 预算：data/shadow_gonglu_65/vision-budget-<suffix>.json（cap 可配，默认 100），超限熔断停止；
  - yijian2 全仓只读；.env 密钥只进内存不落盘；只写新仓。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from examcard.pipeline.shadow_wiring import (  # noqa: E402
    VisionBudgetExceeded,
    VisionCallBudget,
    make_live_vision_verifier,
    make_v2_evidence_runner,
)
from examcard.providers.base import sanitized_error_summary  # noqa: E402
from examcard.utils.legacy_root import resolve_legacy_root  # noqa: E402

YIJIAN2 = resolve_legacy_root(REPO)
ENV_PATH = YIJIAN2 / ".env"
OUTPUT_ROOT = REPO / "data" / "shadow_gonglu_65"


def _resolve_gonglu_pdf_dir() -> Path:
    """按「pdf 数 == 65」稳健定位公路目录，规避 CLI 的 unicode 路径乱码。"""
    candidates = [
        child
        for child in YIJIAN2.iterdir()
        if child.is_dir() and child.name.startswith("input_pdf_") and len(list(child.glob("*.pdf"))) == 65
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"gonglu pdf dir (65 chapters) not uniquely resolved: {candidates}")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="lo", type=int, required=True)
    parser.add_argument("--to", dest="hi", type=int, required=True)
    parser.add_argument("--budget-suffix", required=True)
    parser.add_argument("--cap", type=int, default=100)
    args = parser.parse_args()

    chapters = [f"{n:02d}" for n in range(args.hi, args.lo - 1, -1)]  # 倒序
    budget_path = OUTPUT_ROOT / f"vision-budget-{args.budget_suffix}.json"

    pdf_dir = _resolve_gonglu_pdf_dir()
    print(f"[{args.budget_suffix}] gonglu pdf dir = {pdf_dir}", flush=True)
    budget = VisionCallBudget(budget_path, hard_cap=args.cap)
    print(f"[{args.budget_suffix}] budget = {budget_path} calls={budget.calls} cap={budget.hard_cap}", flush=True)

    verifier = make_live_vision_verifier(ENV_PATH)
    runner = make_v2_evidence_runner(pdf_dir, OUTPUT_ROOT, vision_verifier=verifier, budget=budget)

    done, skipped, failed = [], [], []
    started = time.time()
    for prefix in chapters:
        chapter_dir = OUTPUT_ROOT / prefix
        report = chapter_dir / "chapter-report.json"
        if report.is_file():
            skipped.append(prefix)
            print(f"[{args.budget_suffix}] {prefix} SKIP (report exists)", flush=True)
            continue
        if chapter_dir.exists():
            skipped.append(prefix)
            print(f"[{args.budget_suffix}] {prefix} SKIP (dir claimed by peer)", flush=True)
            continue
        chapter_dir.mkdir(parents=True, exist_ok=False)  # 目录即认领
        print(f"[{args.budget_suffix}] {prefix} START", flush=True)
        try:
            metrics = runner(prefix)
        except VisionBudgetExceeded as exc:
            print(f"[{args.budget_suffix}] {prefix} BUDGET-FUSED: {sanitized_error_summary(exc)}", flush=True)
            failed.append((prefix, "vision_budget_exceeded"))
            break
        except Exception as exc:  # noqa: BLE001 — 单章失败登记后继续
            print(f"[{args.budget_suffix}] {prefix} FAIL: {sanitized_error_summary(exc)}", flush=True)
            failed.append((prefix, sanitized_error_summary(exc)))
            continue
        done.append(prefix)
        print(
            f"[{args.budget_suffix}] {prefix} DONE pages={metrics['page_count']} "
            f"units={metrics['evidence_unit_count']} conflicts={metrics['source_conflict_count']} "
            f"vision={metrics['vision_calls_used']} | budget {budget.calls}/{budget.hard_cap} | "
            f"elapsed {time.time() - started:.0f}s",
            flush=True,
        )

    print(
        f"[{args.budget_suffix}] SUMMARY done={done} skipped={skipped} failed={failed} "
        f"vision_total={budget.calls}/{budget.hard_cap}",
        flush=True,
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
