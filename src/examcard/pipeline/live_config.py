"""运行配置与 run_state.json 状态机（拆分层 2；设计 §2 / §5.1）。

``LiveRunConfig`` 校验 run_label 与 reason 字面量、并按 ``_STAGE_ORDER`` 给 stage 排序；
``RunState`` 支撑断点续跑，状态文件损坏时可从各 stage 报告重建。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from ..common.io import atomic_write_json, read_json

from .live_support import LIVE_STAGES, _REASON_PATTERN, _REPO_ROOT, _RUN_LABEL_MAX, _utc_now_iso
from .live_resend import ResendPlan


# ---------------------------------------------------------------------------
# 运行配置与运行状态机（§2 / §5.1）
# ---------------------------------------------------------------------------


_STAGE_ORDER = {stage: index for index, stage in enumerate(LIVE_STAGES)}


@dataclass(frozen=True)
class LiveRunConfig:
    """CLI 参数全量映射（§2）；构造期即校验互斥/必填组合与审计标识文法。"""

    exam_id: str
    stages: tuple[str, ...]
    mode: Literal["dry_run", "live"]
    run_label: str
    budget_usd: float | None = None
    only_samples: tuple[str, ...] | None = None
    channel: str = "last"
    env_file: Path | None = None
    step3_contract: Literal["v1", "v2"] = "v2"
    step3_mode: Literal["echo", "release-bypass"] = "echo"
    step3_typed_fixture: Path | None = None
    step3_release_fixture: Path | None = None
    step4_handoff: Path | None = None
    prompt_variant: Literal["v1", "v2"] = "v2"
    skip_preflight: bool = False
    resume_run_id: str | None = None
    max_attempts: int = 3
    repo_root: Path = _REPO_ROOT

    def __post_init__(self) -> None:
        unknown = [stage for stage in self.stages if stage not in LIVE_STAGES]
        if unknown or not self.stages:
            raise ValueError(f"unknown or empty stages: {unknown or self.stages}")
        ordered = tuple(sorted(dict.fromkeys(self.stages), key=_STAGE_ORDER.__getitem__))
        object.__setattr__(self, "stages", ordered)
        if self.mode == "live":
            if self.budget_usd is None or self.budget_usd <= 0:
                raise ValueError("live mode requires a positive --budget-usd (§6 hard cap)")
            if not self.run_label:
                raise ValueError("live mode requires --run-label (audit identifier)")
        label = self.run_label or "dry-run"
        if len(label) > _RUN_LABEL_MAX or _REASON_PATTERN.fullmatch(label) is None:
            raise ValueError(f"run label must match [a-z0-9][a-z0-9._-]* and be at most {_RUN_LABEL_MAX} chars: {label!r}")
        object.__setattr__(self, "run_label", label)
        if self.step3_mode == "release-bypass" and "step3" in self.stages and self.step3_release_fixture is None:
            raise ValueError("--step3-mode release-bypass requires --step3-release-fixture")
        # 识别身份 reason 由驱动派生为 <exam>-<stage>-<label>（§2）；构造期即验证最长派生仍合法。
        for stage in self.stages:
            ResendPlan.reason_for_attempt(self.base_reason(stage), self.max_attempts)

    def base_reason(self, stage: str) -> str:
        return f"{self.exam_id}-{stage}-{self.run_label}".replace("_", "-").lower()


class RunState:
    """run_state.json 状态机（§5.1）：stage 粒度状态 + 检查点票据；原子写盘。"""

    STAGE_STATES = ("pending", "running", "completed", "completed_with_failures", "waiting_human", "skipped", "failed")

    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = Path(path)
        self.payload = payload

    @classmethod
    def load_or_create(cls, path: Path, *, run_id: str, exam_id: str, stages: Sequence[str]) -> "RunState":
        path = Path(path)
        if path.is_file():
            payload = read_json(path)
            if payload.get("run_id") != run_id:
                raise ValueError(f"run state belongs to another run: {payload.get('run_id')} != {run_id}")
            for stage in stages:
                payload.setdefault("stage_states", {}).setdefault(stage, {"status": "pending"})
            return cls(path, payload)
        payload = {
            "run_id": run_id,
            "exam_id": exam_id,
            "stages_requested": list(stages),
            "stage_states": {stage: {"status": "pending"} for stage in stages},
            "checkpoints": {},
            "created_at_utc": _utc_now_iso(),
            "updated_at_utc": _utc_now_iso(),
        }
        state = cls(path, payload)
        state.save()
        return state

    def save(self) -> None:
        self.payload["updated_at_utc"] = _utc_now_iso()
        atomic_write_json(self.path, self.payload)

    def stage_status(self, stage: str) -> str:
        return self.payload.get("stage_states", {}).get(stage, {}).get("status", "pending")

    def set_stage(self, stage: str, status: str, **extra: Any) -> None:
        if status not in self.STAGE_STATES:
            raise ValueError(f"unknown stage status: {status}")
        entry = self.payload.setdefault("stage_states", {}).setdefault(stage, {})
        entry["status"] = status
        entry.update(extra)
        self.save()

    def checkpoint(self, name: str) -> dict[str, Any]:
        return self.payload.setdefault("checkpoints", {}).setdefault(name, {"status": "pending"})

    def set_checkpoint(self, name: str, status: str, **extra: Any) -> None:
        entry = self.checkpoint(name)
        entry["status"] = status
        entry.update(extra)
        self.save()
