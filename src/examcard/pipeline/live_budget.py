"""单价表与双护栏预算账本（拆分层 0；设计 §6：双账本取严，fail-closed）。

调用数与美元两本账各自设上限、取严者生效，且一律 charge-before-call：先记账再外呼，
进程中途死掉也不会漏计。账本持久化，续跑时从磁盘接着扣。
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..common.io import atomic_write_json, read_json


# ---------------------------------------------------------------------------
# 单价表与预算护栏（§6：双账本取严，fail-closed）
# ---------------------------------------------------------------------------


def load_live_pricing(path: Path) -> dict[str, float]:
    """读取单价表（configs/v2/live_pricing_v1.yaml；估价仅用于护栏与报告，不进 identity）。"""

    import yaml

    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    prices = payload.get("prices_usd")
    if not isinstance(prices, Mapping) or not prices:
        raise ValueError(f"live pricing table has no prices_usd mapping: {path}")
    return {str(key): float(value) for key, value in prices.items()}


class LiveBudgetExceeded(RuntimeError):
    """预算触顶（fail-closed，决不降级）；stop_reason 区分调用数/美元两护栏。"""

    def __init__(self, message: str, *, stop_reason: str) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason


class LiveRunBudget:
    """双护栏预算账本（VisionCallBudget 同型：原子落盘、跨进程续跑不重置、cap 不一致拒续）。"""

    def __init__(self, state_path: Path, *, usd_cap: float, stage_call_caps: Mapping[str, int]) -> None:
        if usd_cap <= 0:
            raise ValueError("live budget usd cap must be positive")
        if not stage_call_caps or any(cap < 1 for cap in stage_call_caps.values()):
            raise ValueError("live budget stage call caps must be positive")
        self.state_path = Path(state_path)
        self.usd_cap = float(usd_cap)
        self.stage_call_caps = {stage: int(cap) for stage, cap in stage_call_caps.items()}
        self.spent_usd = 0.0
        self.stage_calls: dict[str, int] = {stage: 0 for stage in self.stage_call_caps}
        self.stage_usd: dict[str, float] = {stage: 0.0 for stage in self.stage_call_caps}
        if self.state_path.is_file():
            state = read_json(self.state_path)
            if float(state.get("budget_usd_cap", -1.0)) != self.usd_cap:
                raise ValueError("resumed live budget usd cap mismatch")
            recorded_caps = {stage: int(entry["call_cap"]) for stage, entry in state.get("per_stage", {}).items()}
            if recorded_caps != self.stage_call_caps:
                raise ValueError("resumed live budget stage call caps mismatch")
            self.spent_usd = float(state.get("spent_usd_estimate", 0.0))
            for stage, entry in state.get("per_stage", {}).items():
                self.stage_calls[stage] = int(entry.get("calls", 0))
                self.stage_usd[stage] = float(entry.get("usd_estimate", 0.0))
        else:
            self._persist()

    def _persist(self) -> None:
        atomic_write_json(
            self.state_path,
            {
                "budget_usd_cap": self.usd_cap,
                "spent_usd_estimate": round(self.spent_usd, 6),
                "per_stage": {
                    stage: {
                        "call_cap": self.stage_call_caps[stage],
                        "calls": self.stage_calls.get(stage, 0),
                        "usd_estimate": round(self.stage_usd.get(stage, 0.0), 6),
                    }
                    for stage in self.stage_call_caps
                },
            },
        )

    @property
    def total_calls(self) -> int:
        return sum(self.stage_calls.values())

    def charge(self, stage: str, *, calls: int = 1, usd_estimate: float) -> None:
        """每次外呼前扣账；任一护栏触顶抛 ``LiveBudgetExceeded``（先判后记，决不透支）。"""

        if stage not in self.stage_call_caps:
            raise ValueError(f"live budget has no call cap for stage: {stage}")
        if calls < 1 or usd_estimate < 0:
            raise ValueError("live budget charge requires positive calls and non-negative usd")
        if self.stage_calls[stage] + calls > self.stage_call_caps[stage]:
            raise LiveBudgetExceeded(
                f"stage {stage} call budget exhausted: {self.stage_calls[stage]}+{calls} > {self.stage_call_caps[stage]}",
                stop_reason="stage_call_budget_reached",
            )
        if self.spent_usd + usd_estimate > self.usd_cap:
            raise LiveBudgetExceeded(
                f"usd budget exhausted: {self.spent_usd:.4f}+{usd_estimate:.4f} > cap {self.usd_cap:.4f}",
                stop_reason="usd_budget_reached",
            )
        self.stage_calls[stage] += calls
        self.stage_usd[stage] = self.stage_usd.get(stage, 0.0) + usd_estimate
        self.spent_usd += usd_estimate
        self._persist()

    def snapshot(self) -> dict[str, Any]:
        return {
            "budget_usd_cap": self.usd_cap,
            "spent_usd_estimate": round(self.spent_usd, 6),
            "total_calls": self.total_calls,
            "per_stage": {
                stage: {
                    "call_cap": self.stage_call_caps[stage],
                    "calls": self.stage_calls.get(stage, 0),
                    "usd_estimate": round(self.stage_usd.get(stage, 0.0), 6),
                }
                for stage in self.stage_call_caps
            },
        }
