"""shadow 分段实跑 w2·第一遍（两遍式修正 bc-31b3117c，覆盖 bc-658b8def 旧语义）：公路章 33→18 倒序，零 API。

- 第一遍语义：全量 native+OCR+fusion 全程本地（vision 通道注入 None，不读 .env、不触密钥、零外呼）；
  额外产出逐章 source_conflicts 普查文件 source-conflicts-census.json：逐条按
  shadow.classify_text_conflict 分诊（typography_only / high_risk / normal），含计数汇总，
  供 s2 第二遍跨路汇总造 VisualReviewHint（仅对 high_risk 打真实 vision）；
- 目录即认领：章目录 data/shadow_gonglu_65/<章>/ 已存在且非本路认领则跳过；本路认领即建目录
  并写 claim.json（lane=w2）；本路崩溃重跑只续跑「本路认领且无完成标记」的章；
- 完成标记 chapter-report.json 存在一律跳过（若缺普查文件则就地补齐，不重算）；
- 预算：第一遍不打 vision，vision-budget-w2.json 仅因接线签名保留，计数恒 0；
- yijian2 全仓只读；只写新仓 data/shadow_gonglu_65。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from examcard.common.io import atomic_write_json, read_json  # noqa: E402
from examcard.pipeline.shadow import classify_text_conflict  # noqa: E402
from examcard.pipeline.shadow_wiring import (  # noqa: E402
    VisionCallBudget,
    make_v2_evidence_runner,
)

LANE = "w2"
YIJIAN2 = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
OUTPUT_ROOT = REPO / "data" / "shadow_gonglu_65"
BUDGET_PATH = OUTPUT_ROOT / f"vision-budget-{LANE}.json"
HARD_CAP = 100
CHAPTERS = [f"{n:02d}" for n in range(33, 17, -1)]  # 33→18 倒序
CENSUS_VERSION = "shadow_source_conflicts_census_v1"
CENSUS_NAME = "source-conflicts-census.json"


def _resolve_gonglu_pdf_dir() -> Path:
    """按「pdf 数 == 65」稳健定位公路目录，规避 PowerShell/CLI 的 unicode 路径乱码。"""
    candidates = []
    for child in YIJIAN2.iterdir():
        if child.is_dir() and child.name.startswith("input_pdf_"):
            count = len(list(child.glob("*.pdf")))
            if count == 65:
                candidates.append(child)
    if len(candidates) != 1:
        raise RuntimeError(f"gonglu pdf dir (65 chapters) not uniquely resolved: {candidates}")
    return candidates[0]


def write_census(chapter_dir: Path, prefix: str) -> dict[str, int]:
    """从已落盘 evidence/manifest.json 生成本章 source_conflicts 普查文件（幂等重写）。"""
    manifest = read_json(chapter_dir / "evidence" / "manifest.json")
    counts = {"total": 0, "high_risk": 0, "typography_only": 0, "normal": 0}
    conflicts = []
    for page in manifest["pages"]:
        for conflict in page.get("source_conflicts") or ():
            severity = classify_text_conflict(conflict.get("native_text") or "", conflict.get("visual_text") or "")
            counts["total"] += 1
            counts[severity] += 1
            conflicts.append(
                {
                    "page_num": page["page_num"],
                    "conflict_id": conflict.get("conflict_id"),
                    "severity": severity,
                    "native_text": conflict.get("native_text"),
                    "visual_text": conflict.get("visual_text"),
                    "status": conflict.get("status"),
                    "locator": conflict.get("locator"),
                }
            )
    atomic_write_json(
        chapter_dir / CENSUS_NAME,
        {
            "census_version": CENSUS_VERSION,
            "lane": LANE,
            "chapter_prefix": prefix,
            "pdf": manifest["source"]["relative_path"],
            "document_sha256": manifest["source"]["sha256"],
            "manifest_id": manifest["manifest_id"],
            "page_count": len(manifest["pages"]),
            "counts": counts,
            "conflicts": conflicts,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return counts


def main() -> int:
    pdf_dir = _resolve_gonglu_pdf_dir()
    print(f"[{LANE}] gonglu pdf dir = {pdf_dir}", flush=True)
    budget = VisionCallBudget(BUDGET_PATH, hard_cap=HARD_CAP)
    print(f"[{LANE}] pass1 zero-API: vision verifier = None; budget stays {budget.calls}/{budget.hard_cap}", flush=True)

    runner = make_v2_evidence_runner(pdf_dir, OUTPUT_ROOT, vision_verifier=None, budget=budget)

    done, patched, skipped, failed = [], [], [], []
    started = time.time()
    for prefix in CHAPTERS:
        chapter_dir = OUTPUT_ROOT / prefix
        report_path = chapter_dir / "chapter-report.json"
        census_path = chapter_dir / CENSUS_NAME
        claim_path = chapter_dir / "claim.json"
        if report_path.is_file():
            if census_path.is_file():
                skipped.append(prefix)
                print(f"[{LANE}] {prefix} SKIP (report exists)", flush=True)
            else:
                counts = write_census(chapter_dir, prefix)
                patched.append(prefix)
                print(f"[{LANE}] {prefix} SKIP (report exists) census patched: {counts}", flush=True)
            continue
        if chapter_dir.exists():
            mine = claim_path.is_file() and read_json(claim_path).get("lane") == LANE
            if not mine:
                skipped.append(prefix)
                print(f"[{LANE}] {prefix} SKIP (dir claimed by peer)", flush=True)
                continue
            print(f"[{LANE}] {prefix} RESUME (own stale claim, no report)", flush=True)
        else:
            try:
                chapter_dir.mkdir(parents=True, exist_ok=False)  # 目录即认领
            except FileExistsError:
                skipped.append(prefix)
                print(f"[{LANE}] {prefix} SKIP (claim race lost)", flush=True)
                continue
            atomic_write_json(claim_path, {"lane": LANE, "claimed_at": datetime.now(timezone.utc).isoformat()})
        print(f"[{LANE}] {prefix} START", flush=True)
        try:
            metrics = runner(prefix)
            counts = write_census(chapter_dir, prefix)
        except Exception as exc:  # noqa: BLE001 — 单章失败登记后继续，不阻断其余章
            print(f"[{LANE}] {prefix} FAIL: {type(exc).__name__}: {exc}", flush=True)
            failed.append((prefix, f"{type(exc).__name__}: {exc}"))
            continue
        done.append(prefix)
        elapsed = time.time() - started
        print(
            f"[{LANE}] {prefix} DONE pages={metrics['page_count']} "
            f"units={metrics['evidence_unit_count']} conflicts={metrics['source_conflict_count']} "
            f"high_risk={counts['high_risk']} vision={metrics['vision_calls_used']} | "
            f"elapsed {elapsed:.0f}s",
            flush=True,
        )

    print(
        f"[{LANE}] SUMMARY done={done} patched={patched} skipped={skipped} failed={failed} "
        f"vision_total={budget.calls}/{budget.hard_cap}",
        flush=True,
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
