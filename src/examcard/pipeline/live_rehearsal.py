"""预算预演、stage 调用上限推导与报告物化（拆分层 2；设计 §6-4）。

dry-run 侧打印预计调用数与美元区间，真跑侧把 run 报告与 freeze 落盘（自证哈希）。
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

from ..common.freeze import build_freeze
from ..common.io import atomic_write_json

from .live_reports import LiveRunReport


# ---------------------------------------------------------------------------
# 预算预演（§6-4：dry-run 打印预计调用数与美元区间）
# ---------------------------------------------------------------------------


def budget_rehearsal_estimate(
    *,
    stages: Sequence[str],
    sample_count: int,
    step3_card_count: int,
    step4_card_count: int,
    pricing: Mapping[str, float],
    max_attempts: int,
) -> dict[str, Any]:
    per_stage: dict[str, dict[str, Any]] = {}
    for stage in stages:
        if stage == "step1":
            calls_max, price = 0, 0.0
        elif stage in {"step2a", "step2b"}:
            calls_max = sample_count * max_attempts + 2
            price = pricing.get(f"{stage}_call", 0.0)
        elif stage == "step3":
            calls_max = step3_card_count * 2 + 1
            price = pricing.get("step3_card_call", 0.0)
        else:
            calls_max = step4_card_count * max_attempts + 1
            price = pricing.get("step4_image_call", 0.0)
        per_stage[stage] = {
            "calls_max": calls_max,
            "usd_max": round(calls_max * price, 4),
        }
    return {
        "per_stage": per_stage,
        "usd_max_total": round(sum(entry["usd_max"] for entry in per_stage.values()), 4),
    }


def derive_stage_call_caps(
    *,
    stages: Sequence[str],
    sample_count: int,
    step3_card_count: int,
    step4_card_count: int,
    max_attempts: int,
) -> dict[str, int]:
    """每 stage 静态调用上限（样本数×轮数+探针+重发余量；pilot NETWORK_CALL_BUDGET 谱系）。"""

    caps: dict[str, int] = {"preflight": 16}
    for stage in stages:
        if stage == "step1":
            continue
        if stage in {"step2a", "step2b"}:
            caps[stage] = max(1, sample_count * max_attempts + 2)
        elif stage == "step3":
            caps[stage] = max(1, step3_card_count * 2 + 1)
        else:
            caps[stage] = max(1, step4_card_count * max_attempts + 1)
    return caps


def materialize_live_run(
    *,
    run_root: Path,
    report: LiveRunReport,
    extras: Mapping[str, Any] | None = None,
) -> None:
    """报告 + freeze 物化（§3.3：live_run_report.json / live_run_freeze.json）。"""

    run_root = Path(run_root)
    payload = report.model_dump(mode="json")
    if extras:
        payload = {**payload, "extras": dict(extras)}
    atomic_write_json(run_root / "live_run_report.json", payload)
    entries = [("live_run_report.json", "report")]
    if (run_root / "budget.json").is_file():
        entries.append(("budget.json", "budget"))
    if (run_root / "run_state.json").is_file():
        entries.append(("run_state.json", "run_state"))
    freeze = build_freeze(run_root, entries)
    atomic_write_json(run_root / "live_run_freeze.json", freeze)
