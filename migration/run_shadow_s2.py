"""s2 收口驱动（一次性运维工具，两遍式 shadow 实跑，bc-658b8def + bc-31b3117c 口径）。

子命令：
  census-seg   第一遍·跑本段（默认 01-17 升序）零 API 普查（census.json 为完成标记）
  backfill     第一遍·对已有 evidence 页 JSON 的章**只读派生** census.json（不重跑 OCR）
  status       两遍完成度盘点
  vision-pass  第二遍·探针 + 仅 high_risk 打真实 vision（预算 400 熔断，chapter-report 为标记）

用法（新仓根目录）：
  .venv\\Scripts\\python -X utf8 migration\\run_shadow_s2.py census-seg --start 1 --end 17
  .venv\\Scripts\\python -X utf8 migration\\run_shadow_s2.py backfill
  .venv\\Scripts\\python -X utf8 migration\\run_shadow_s2.py vision-pass
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from examcard.common.io import atomic_write_json, read_json  # noqa: E402
from examcard.evidence.schema import PageEvidence  # noqa: E402
from examcard.pipeline.shadow_wiring import (  # noqa: E402
    CENSUS_REPORT_NAME,
    VisionBudgetExceeded,
    VisionCallBudget,
    _conflict_records,
    high_risk_review_hints,
    make_census_runner,
    make_live_vision_verifier,
    make_vision_review_runner,
)

LEGACY = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
PDF_DIR = LEGACY / "input_pdf_公路"
ROOT = REPO_ROOT / "data" / "shadow_gonglu_65"
JOURNAL = ROOT / "journal-s2.jsonl"
ALL_PREFIXES = [f"{n:02d}" for n in range(1, 66)]


def _journal(event: dict) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}, ensure_ascii=False) + "\n")


def cmd_census_seg(start: int, end: int) -> int:
    runner = make_census_runner(PDF_DIR, ROOT)
    done, skipped, failed = [], [], []
    for number in range(start, end + 1):
        prefix = f"{number:02d}"
        chapter_dir = ROOT / prefix
        if (chapter_dir / CENSUS_REPORT_NAME).is_file():
            skipped.append(prefix)
            print(f"[skip-census] {prefix}", flush=True)
            continue
        chapter_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        try:
            payload = runner(prefix)
        except Exception as error:
            print(f"[FAIL] {prefix}: {type(error).__name__}: {str(error)[:160]}", flush=True)
            traceback.print_exc(limit=2)
            failed.append(prefix)
            _journal({"pass": "census", "chapter": prefix, "status": "failed", "error": str(error)[:200]})
            continue
        elapsed = round(time.perf_counter() - started, 1)
        done.append(prefix)
        line = (
            f"[census] {prefix} {elapsed}s pages={payload['page_count']} units={payload['evidence_unit_count']} "
            f"conflicts={payload['source_conflict_count']} high_risk={payload['high_risk_conflict_count']} hints={payload['high_risk_hint_count']}"
        )
        print(line, flush=True)
        _journal({"pass": "census", "chapter": prefix, "status": "done", "elapsed_s": elapsed, "metrics": {k: payload[k] for k in ("page_count", "evidence_unit_count", "source_conflict_count", "high_risk_conflict_count", "high_risk_hint_count")}})
    print("CENSUS_SEG_SUMMARY", json.dumps({"done": done, "skipped": skipped, "failed": failed}, ensure_ascii=False), flush=True)
    return 0 if not failed else 1


def _derive_census_from_pages(prefix: str) -> dict | None:
    """只读派生：从既有 evidence/pages/*.json + manifest.json 生成 census（零重算）。"""
    chapter_dir = ROOT / prefix
    pages_dir = chapter_dir / "evidence" / "pages"
    manifest_path = chapter_dir / "evidence" / "manifest.json"
    if not pages_dir.is_dir() or not manifest_path.is_file():
        return None
    manifest_raw = read_json(manifest_path)
    pages = [PageEvidence.model_validate(read_json(page)) for page in sorted(pages_dir.glob("page_*.json"))]
    records = _conflict_records(SimpleNamespace(pages=pages))
    high_risk = sum(1 for record in records if record["severity"] == "high_risk")
    return {
        "chapter_prefix": prefix,
        "pdf": manifest_raw.get("source", {}).get("relative_path", ""),
        "document_sha256": manifest_raw.get("source", {}).get("sha256", ""),
        "manifest_id": manifest_raw.get("manifest_id", ""),
        "pass": "census",
        "census_source": "derived_from_existing_pages",
        "page_count": len(pages),
        "evidence_unit_count": sum(len(page.evidence_units) for page in pages),
        "source_conflict_count": len(records),
        "high_risk_conflict_count": high_risk,
        "high_risk_hint_count": len(high_risk_review_hints(records)),
        "conflicts": records,
    }


def cmd_backfill() -> int:
    done, skipped, missing = [], [], []
    for prefix in ALL_PREFIXES:
        census_path = ROOT / prefix / CENSUS_REPORT_NAME
        if census_path.is_file():
            skipped.append(prefix)
            continue
        payload = _derive_census_from_pages(prefix)
        if payload is None:
            missing.append(prefix)
            continue
        atomic_write_json(census_path, payload)
        done.append(prefix)
        print(
            f"[backfill] {prefix} conflicts={payload['source_conflict_count']} high_risk={payload['high_risk_conflict_count']} hints={payload['high_risk_hint_count']}",
            flush=True,
        )
        _journal({"pass": "census", "chapter": prefix, "status": "backfilled", "metrics": {k: payload[k] for k in ("source_conflict_count", "high_risk_conflict_count", "high_risk_hint_count")}})
    print("BACKFILL_SUMMARY", json.dumps({"done": done, "skipped": skipped, "missing_evidence": missing}, ensure_ascii=False), flush=True)
    return 0


def cmd_status() -> int:
    census = [p for p in ALL_PREFIXES if (ROOT / p / CENSUS_REPORT_NAME).is_file()]
    vision = []
    legacy = []
    for prefix in ALL_PREFIXES:
        report = ROOT / prefix / "chapter-report.json"
        if not report.is_file():
            continue
        payload = read_json(report)
        (vision if payload.get("pass") == "vision_review" else legacy).append(prefix)
    hints_total = 0
    for prefix in census:
        hints_total += int(read_json(ROOT / prefix / CENSUS_REPORT_NAME).get("high_risk_hint_count", 0))
    budget_path = ROOT / "vision-budget.json"
    budget = read_json(budget_path) if budget_path.is_file() else None
    print(
        json.dumps(
            {
                "census_done": len(census),
                "census_missing": [p for p in ALL_PREFIXES if p not in census],
                "vision_pass_done": len(vision),
                "legacy_pass1_reports": len(legacy),
                "high_risk_hints_total": hints_total,
                "vision_budget": budget,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


def cmd_vision_pass(probe: bool) -> int:
    missing_census = [p for p in ALL_PREFIXES if not (ROOT / p / CENSUS_REPORT_NAME).is_file()]
    if missing_census:
        print(f"ABORT: census incomplete, missing {missing_census}", flush=True)
        return 2
    budget = VisionCallBudget(ROOT / "vision-budget.json", hard_cap=400)
    verifier = make_live_vision_verifier(LEGACY / ".env")
    if probe:
        # 最小探针（计入预算）：中继不可达即 fail-closed 停下上报。
        probe_png = (ROOT / "01" / "evidence" / "crops").glob("*.png")
        sample = next(iter(sorted(probe_png)), None)
        if sample is None:
            print("ABORT: no crop available for probe", flush=True)
            return 2
        budget.charge(1)
        try:
            outcome = verifier.verify(sample.read_bytes())
        except Exception as error:
            print(f"PROBE_FAILED: {type(error).__name__}: {str(error)[:200]}", flush=True)
            _journal({"pass": "vision", "event": "probe_failed", "error": str(error)[:200]})
            return 3
        print(f"PROBE_OK visible_text_len={len(outcome.visible_text)} uncertain={outcome.uncertain} budget={budget.calls}/{budget.hard_cap}", flush=True)
        _journal({"pass": "vision", "event": "probe_ok", "budget": budget.calls})
    runner = make_vision_review_runner(PDF_DIR, ROOT, vision_verifier=verifier, budget=budget)
    done, skipped, failed = [], [], []
    for prefix in ALL_PREFIXES:
        chapter_dir = ROOT / prefix
        report_path = chapter_dir / "chapter-report.json"
        if report_path.is_file():
            payload = read_json(report_path)
            if payload.get("pass") == "vision_review":
                skipped.append(prefix)
                print(f"[skip-v2] {prefix}", flush=True)
                continue
            # 旧协议第一遍报告：让位给第二遍标记（数据在 census/evidence 中完整保留）。
            report_path.unlink()
        started = time.perf_counter()
        try:
            metrics = runner(prefix)
        except VisionBudgetExceeded as error:
            print(f"[BREAKER] {prefix}: {error}", flush=True)
            _journal({"pass": "vision", "chapter": prefix, "status": "breaker", "error": str(error)[:200]})
            break
        except Exception as error:
            print(f"[FAIL] {prefix}: {type(error).__name__}: {str(error)[:160]}", flush=True)
            traceback.print_exc(limit=2)
            failed.append(prefix)
            _journal({"pass": "vision", "chapter": prefix, "status": "failed", "error": str(error)[:200]})
            continue
        elapsed = round(time.perf_counter() - started, 1)
        done.append(prefix)
        print(
            f"[v2] {prefix} {elapsed}s hints={metrics['high_risk_hint_count']} vision={metrics['vision_calls_used']} fail={metrics['vision_failures']} budget={budget.calls}/{budget.hard_cap}",
            flush=True,
        )
        _journal({"pass": "vision", "chapter": prefix, "status": "done", "elapsed_s": elapsed, "metrics": metrics, "budget": budget.calls})
    print("VISION_PASS_SUMMARY", json.dumps({"done": done, "skipped": skipped, "failed": failed, "vision_calls": budget.calls}, ensure_ascii=False), flush=True)
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="s2 shadow two-pass driver")
    sub = parser.add_subparsers(dest="command", required=True)
    seg = sub.add_parser("census-seg")
    seg.add_argument("--start", type=int, default=1)
    seg.add_argument("--end", type=int, default=17)
    sub.add_parser("backfill")
    sub.add_parser("status")
    vp = sub.add_parser("vision-pass")
    vp.add_argument("--no-probe", action="store_true")
    args = parser.parse_args()
    if args.command == "census-seg":
        return cmd_census_seg(args.start, args.end)
    if args.command == "backfill":
        return cmd_backfill()
    if args.command == "status":
        return cmd_status()
    if args.command == "vision-pass":
        return cmd_vision_pass(probe=not args.no_probe)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
