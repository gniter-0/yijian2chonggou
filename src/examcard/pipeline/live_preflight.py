"""预检放行矩阵（拆分层 1；设计 §8.2）。

纯函数：只读预检报告与待跑 stage 列表，返回放行/停跑与 stop_reason。**预检执行器由
CLI 注入**，本模块不认识任何客户端，因此可用伪报告离线测全部分支。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Sequence

from .live_support import _STAGE_PREFLIGHT_KEYS


# ---------------------------------------------------------------------------
# 预检放行矩阵（§8.2；纯函数，预检执行器由 CLI 注入）
# ---------------------------------------------------------------------------


def evaluate_preflight_gate(report: Mapping[str, Any], stages: Sequence[str]) -> dict[str, Any]:
    """按需求画像结论裁决放行：fail/unknown 一律拒开（fail-closed）。"""

    conclusions = report.get("requirement_conclusions") or {}
    for stage in stages:
        key = _STAGE_PREFLIGHT_KEYS.get(stage)
        if key is None:
            continue
        verdict = str((conclusions.get(key) or {}).get("verdict_code", "unknown"))
        if verdict not in {"pass", "likely_pass"}:
            digit = {"step2a": "2", "step2b": "2", "step3": "3", "step4": "4"}[stage]
            return {
                "allowed": False,
                "stop_reason": f"preflight_step{digit}_unmet",
                "stage": stage,
                "requirement": key,
                "verdict_code": verdict,
            }
    return {"allowed": True, "stop_reason": None}
