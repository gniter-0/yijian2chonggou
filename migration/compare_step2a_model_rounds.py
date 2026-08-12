"""step2a 多模型轮次对比表（pilot 模型选型，零 API 成本）。

汇总各轮 storage 的报告与质量底稿，输出统一对比指标：
传输成功率 / 平均时延 / 提案量 / 基准精确命中 / out-of-scope / 考情噪声 / 拆行碎片 / 水印污染。

运行：uv run python migration/compare_step2a_model_rounds.py <storage1> <storage2> ...
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def analyze(storage: Path) -> dict[str, object]:
    report = json.loads((storage / "pilot_gonglu5_step2_report.json").read_text(encoding="utf-8"))
    workpad_path = storage / "step2a_quality_workpad.json"
    workpad = json.loads(workpad_path.read_text(encoding="utf-8")) if workpad_path.is_file() else []
    samples = report["samples"]
    step2a = [s["step2a"] for s in samples]
    matched = sum(
        ((s.get("step2a_gates") or {}).get("learning_golden") or {}).get("matched_target_count", 0) for s in samples
    )
    targets = sum(
        ((s.get("step2a_gates") or {}).get("learning_golden") or {}).get("reviewed_target_count", 0) for s in samples
    )
    noise = frag = watermark = proposals = 0
    for item in workpad:
        for p in item["live_proposals"]:
            proposals += 1
            if re.search(r"(考查年份|题量|年份表|历年题量|考情)", p["label"]) or re.fullmatch(
                r"（?\d{4}[^）]*(单|多|案)）?", p["assertion"].strip()
            ):
                noise += 1
            if re.search(r"(前段|后段|第[一二三]行|上半|下半)", p["label"]):
                frag += 1
            if re.search(r"、(信|微|系|联|题)、", p["assertion"]) or re.search(r"[信微系联题](、|，|。)", p["assertion"]):
                watermark += 1
    return {
        "round": storage.name,
        "model": report["model"],
        "transport_ok": f"{sum(r['state'] == 'success' for r in step2a)}/{len(step2a)}",
        "avg_latency_s": round(sum(r["latency_ms"] for r in step2a) / len(step2a) / 1000, 1) if step2a else None,
        "proposals": proposals,
        "matched_exact": f"{matched}/{targets}",
        "noise_exam_stats": noise,
        "fragments": frag,
        "watermark_hits": watermark,
    }


def main() -> int:
    rows = [analyze(Path(arg).resolve()) for arg in sys.argv[1:]]
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
