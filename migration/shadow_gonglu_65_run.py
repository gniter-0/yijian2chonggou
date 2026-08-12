"""公路 65 章 shadow 实跑驱动（两遍式；A 案裁决 bc-658b8def）。

子命令：
  census  <start> <end> [--desc]   第一遍零 API 普查（native+OCR+fusion + 冲突分诊），
                                    产物 data/shadow_gonglu_65/<章>/census.json
  gate                             汇总全部已存在 census.json 的 high_risk 总数，判 ≤400 门禁
  probe                            探测 .env 中继可达性（不打 vision，仅连通性）
  vision  [--start N --end M]      第二遍：对已有 census 的章打真实 vision（400-cap），
                                    产物 <章>/chapter-report.json
  aggregate                        四路 + 两遍汇总 → data/shadow_gonglu_65/shadow_run_journal.json

纪律：yijian2 只读（只读 input_pdf_公路 与 structured_*）；密钥仅驻内存不落盘/入日志；
只写新仓 data/shadow_gonglu_65/。零 API 仅限第二遍。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
YIJIAN = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
PDF_DIR = YIJIAN / "input_pdf_公路"
ENV_PATH = YIJIAN / ".env"
OUTPUT_ROOT = REPO / "data" / "shadow_gonglu_65"

sys.path.insert(0, str(REPO / "src"))

from examcard.common.io import atomic_write_json, read_json  # noqa: E402
from examcard.pipeline.shadow_wiring import (  # noqa: E402
    DEFAULT_VISION_HARD_CAP,
    VisionCallBudget,
    load_env_file,
    make_census_runner,
    make_live_vision_verifier,
    make_vision_review_runner,
)

TOTAL_CHAPTERS = 65
HIGH_RISK_GATE = 400


def _prefixes(start: int, end: int, *, desc: bool = False) -> list[str]:
    order = range(end, start - 1, -1) if desc else range(start, end + 1)
    return [f"{n:02d}" for n in order]


def cmd_census(args: argparse.Namespace) -> int:
    runner = make_census_runner(PDF_DIR, OUTPUT_ROOT)
    done = 0
    for prefix in _prefixes(args.start, args.end, desc=args.desc):
        census_path = OUTPUT_ROOT / prefix / "census.json"
        if census_path.is_file():
            print(f"[skip] {prefix} census exists")
            done += 1
            continue
        started = time.perf_counter()
        payload = runner(prefix)
        done += 1
        print(
            f"[census] {prefix} pages={payload['page_count']} conflicts={payload['source_conflict_count']} "
            f"high_risk={payload['high_risk_conflict_count']} hints={payload['high_risk_hint_count']} "
            f"{time.perf_counter() - started:.1f}s"
        )
    print(f"census done: {done} chapters in [{args.start:02d}-{args.end:02d}]")
    return 0


def _all_census() -> list[dict]:
    return [read_json(path) for path in sorted(OUTPUT_ROOT.glob("*/census.json"))]


def cmd_gate(args: argparse.Namespace) -> int:
    censuses = _all_census()
    total_high_risk = sum(int(c["high_risk_conflict_count"]) for c in censuses)
    total_hints = sum(int(c["high_risk_hint_count"]) for c in censuses)
    total_conflicts = sum(int(c["source_conflict_count"]) for c in censuses)
    report = {
        "chapters_censused": len(censuses),
        "chapters_expected": TOTAL_CHAPTERS,
        "census_complete": len(censuses) == TOTAL_CHAPTERS,
        "total_source_conflicts": total_conflicts,
        "total_high_risk_conflicts": total_high_risk,
        "total_high_risk_hints": total_hints,
        "vision_gate_cap": HIGH_RISK_GATE,
        "vision_pass_allowed": len(censuses) == TOTAL_CHAPTERS and total_hints <= HIGH_RISK_GATE,
        "per_chapter": {
            c["chapter_prefix"]: {
                "high_risk": c["high_risk_conflict_count"],
                "hints": c["high_risk_hint_count"],
            }
            for c in sorted(censuses, key=lambda c: c["chapter_prefix"])
        },
    }
    atomic_write_json(OUTPUT_ROOT / "gate-report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "per_chapter"}, ensure_ascii=False, indent=2))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    import httpx

    values = load_env_file(ENV_PATH)
    base = values.get("API_BASE_URL", "")
    if not base:
        print("PROBE FAIL: API_BASE_URL missing in .env")
        return 1
    try:
        response = httpx.get(base, timeout=8.0)
        print(f"PROBE OK: {base} reachable (HTTP {response.status_code})")
        return 0
    except Exception as error:  # noqa: BLE001
        try:
            response = httpx.get(base.rsplit("/", 1)[0] + "/models", timeout=8.0)
            print(f"PROBE OK: {base}/models reachable (HTTP {response.status_code})")
            return 0
        except Exception as error2:  # noqa: BLE001
            print(f"PROBE FAIL: {base} unreachable: {type(error).__name__}: {error} / {type(error2).__name__}: {error2}")
            return 1


def cmd_vision(args: argparse.Namespace) -> int:
    gate = read_json(OUTPUT_ROOT / "gate-report.json") if (OUTPUT_ROOT / "gate-report.json").is_file() else None
    if gate is None or not gate.get("vision_pass_allowed"):
        print("VISION BLOCKED: run gate first and confirm vision_pass_allowed=true (census complete & hints<=400)")
        return 2
    verifier = make_live_vision_verifier(ENV_PATH)
    budget = VisionCallBudget(OUTPUT_ROOT / "vision-budget.json", hard_cap=DEFAULT_VISION_HARD_CAP)
    runner = make_vision_review_runner(PDF_DIR, OUTPUT_ROOT, vision_verifier=verifier, budget=budget)
    prefixes = _prefixes(args.start, args.end)
    done = 0
    for prefix in prefixes:
        if not (OUTPUT_ROOT / prefix / "census.json").is_file():
            print(f"[skip] {prefix} no census")
            continue
        if (OUTPUT_ROOT / prefix / "chapter-report.json").is_file():
            print(f"[skip] {prefix} vision done")
            done += 1
            continue
        started = time.perf_counter()
        metrics = runner(prefix)
        done += 1
        print(
            f"[vision] {prefix} hints={metrics['high_risk_hint_count']} vision_calls={metrics['vision_calls_used']} "
            f"budget={budget.calls}/{budget.hard_cap} {time.perf_counter() - started:.1f}s"
        )
    print(f"vision done: {done} chapters; budget used {budget.calls}/{budget.hard_cap}")
    return 0


def cmd_aggregate(args: argparse.Namespace) -> int:
    censuses = _all_census()
    reports = [read_json(path) for path in sorted(OUTPUT_ROOT.glob("*/chapter-report.json"))]
    journal = {
        "output_root": str(OUTPUT_ROOT),
        "chapters_censused": len(censuses),
        "chapters_vision_reviewed": len(reports),
        "chapters_expected": TOTAL_CHAPTERS,
        "complete": len(censuses) == TOTAL_CHAPTERS and len(reports) == TOTAL_CHAPTERS,
        "totals": {
            "pages": sum(int(c["page_count"]) for c in censuses),
            "evidence_units": sum(int(c["evidence_unit_count"]) for c in censuses),
            "source_conflicts": sum(int(c["source_conflict_count"]) for c in censuses),
            "high_risk_hints": sum(int(c["high_risk_hint_count"]) for c in censuses),
            "vision_calls_used": sum(int(r["metrics"]["vision_calls_used"]) for r in reports),
        },
        "per_chapter": {
            c["chapter_prefix"]: {
                "pages": c["page_count"],
                "conflicts": c["source_conflict_count"],
                "high_risk_hints": c["high_risk_hint_count"],
                "vision_calls": next(
                    (r["metrics"]["vision_calls_used"] for r in reports if r["chapter_prefix"] == c["chapter_prefix"]),
                    None,
                ),
            }
            for c in sorted(censuses, key=lambda c: c["chapter_prefix"])
        },
    }
    atomic_write_json(OUTPUT_ROOT / "shadow_run_journal.json", journal)
    print(json.dumps({k: v for k, v in journal.items() if k != "per_chapter"}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_census = sub.add_parser("census")
    p_census.add_argument("start", type=int)
    p_census.add_argument("end", type=int)
    p_census.add_argument("--desc", action="store_true")
    p_census.set_defaults(func=cmd_census)

    sub.add_parser("gate").set_defaults(func=cmd_gate)
    sub.add_parser("probe").set_defaults(func=cmd_probe)

    p_vision = sub.add_parser("vision")
    p_vision.add_argument("--start", type=int, default=1)
    p_vision.add_argument("--end", type=int, default=TOTAL_CHAPTERS)
    p_vision.set_defaults(func=cmd_vision)

    sub.add_parser("aggregate").set_defaults(func=cmd_aggregate)

    args = parser.parse_args()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
