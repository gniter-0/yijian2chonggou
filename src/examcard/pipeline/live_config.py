"""运行配置与 run_state.json 状态机（拆分层 2；设计 §2 / §5.1）。

``LiveRunConfig`` 校验 run_label 与 reason 字面量、并按 ``_STAGE_ORDER`` 给 stage 排序；
``RunState`` 支撑断点续跑，状态文件损坏时可从各 stage 报告重建。
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Sequence

from ..common.hashing import canonical_hash, file_hash
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
    step3_contract: Literal["v1", "v2", "v3"] = "v2"
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


class RunStateBindingError(ValueError):
    """Existing run state does not belong to the exact current run contract."""


def _file_binding(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"configured": False}
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"configured run input is missing: {path}")
    return {"configured": True, "sha256": file_hash(path)}


def build_run_resume_contract(
    config: LiveRunConfig,
    *,
    resolved_sample_ids: Sequence[str],
    step3_card_count: int,
    step4_card_count: int,
    stage_call_caps: Mapping[str, int],
    image_base_url: str,
    image_model: str,
    step4_handoff: Path | None = None,
    step4_release_fixture: Path | None = None,
    admission_contract: Literal["v2"] | None = None,
    step3_typed_fixture_origin: Literal["admission_checkpoint"] | None = None,
) -> dict[str, Any]:
    """Build the secret-free immutable identity used to validate resume state."""

    if (admission_contract is None) != (step3_typed_fixture_origin is None):
        raise ValueError("admission contract and Step3 fixture origin must be supplied together")
    admission_lane = admission_contract == "v2"
    if admission_lane and not (
        config.mode == "live"
        and "step2b" in config.stages
        and "step3" in config.stages
        and config.step3_contract == "v3"
    ):
        raise ValueError("V2 admission checkpoint is only valid for live same-run Step2B to Step3 V3")
    same_run_step3_echo = admission_lane and config.step3_mode == "echo"
    same_run_echo_lane = same_run_step3_echo and "step4" in config.stages
    file_inputs = {
        "step3_typed_fixture": {"configured": False, "origin": "admission_checkpoint"}
        if admission_lane
        else _file_binding(config.step3_typed_fixture),
        # The Admission + Echo Step3 lane resolves its release from the
        # authenticated same-run checkpoint.  Release-bypass still consumes
        # the configured external release and must bind it for resume.
        "step3_release_fixture": {"configured": False}
        if same_run_step3_echo
        else _file_binding(config.step3_release_fixture),
        "step4_handoff": {"configured": False}
        if same_run_echo_lane
        else _file_binding(step4_handoff or config.step4_handoff),
        "step4_release_fixture": {"configured": False}
        if same_run_echo_lane
        else _file_binding(step4_release_fixture),
    }
    return {
        "schema_version": 3 if admission_lane else 2,
        "exam_id": config.exam_id,
        "mode": config.mode,
        "stages": list(config.stages),
        "run_label": config.run_label,
        "budget_usd": config.budget_usd,
        "only_samples": list(config.only_samples) if config.only_samples is not None else None,
        "channel": config.channel,
        "step3_contract": config.step3_contract,
        "step3_mode": config.step3_mode,
        "prompt_variant": config.prompt_variant,
        "skip_preflight": config.skip_preflight,
        "max_attempts": config.max_attempts,
        "resolved_sample_ids": list(resolved_sample_ids),
        "step3_card_count": step3_card_count,
        "step4_card_count": step4_card_count,
        "stage_call_caps": dict(stage_call_caps),
        "image_base_url": image_base_url,
        "image_model": image_model,
        "admission_contract": admission_contract,
        "step3_typed_fixture_origin": step3_typed_fixture_origin,
        "file_inputs": file_inputs,
    }


class RunState:
    """run_state.json 状态机（§5.1）：stage 粒度状态 + 检查点票据；原子写盘。"""

    STAGE_STATES = ("pending", "running", "completed", "completed_with_failures", "waiting_human", "skipped", "failed")

    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = Path(path)
        self.payload = payload

    @classmethod
    def load_or_create(
        cls,
        path: Path,
        *,
        run_id: str,
        exam_id: str,
        stages: Sequence[str],
        run_config: Mapping[str, Any],
        run_config_sha256: str,
    ) -> "RunState":
        path = Path(path)
        current_config = dict(run_config)
        if canonical_hash(current_config) != run_config_sha256:
            raise RunStateBindingError("current run configuration binding is invalid")
        if path.is_file():
            payload = read_json(path)
            if not isinstance(payload, dict):
                raise RunStateBindingError("run state is not a JSON object; start a fresh run")
            if payload.get("run_id") != run_id:
                raise RunStateBindingError(f"run state belongs to another run: {payload.get('run_id')} != {run_id}")
            if payload.get("exam_id") != exam_id:
                raise RunStateBindingError("run state belongs to another exam")
            stored_config = payload.get("run_config")
            stored_sha256 = payload.get("run_config_sha256")
            if not isinstance(stored_config, dict) or not isinstance(stored_sha256, str):
                raise RunStateBindingError("run state is legacy or unbound; start a fresh run")
            if canonical_hash(stored_config) != stored_sha256:
                raise RunStateBindingError("run state configuration binding is tampered")
            if stored_sha256 != run_config_sha256:
                raise RunStateBindingError("run state configuration does not match the current run")
            stored_stages = payload.get("stages_requested")
            if tuple(stored_stages or ()) != tuple(stages):
                raise RunStateBindingError("resume cannot append or remove requested stages")
            for stage in stages:
                payload.setdefault("stage_states", {}).setdefault(stage, {"status": "pending"})
            return cls(path, payload)
        payload = {
            "run_id": run_id,
            "exam_id": exam_id,
            "run_config": current_config,
            "run_config_sha256": run_config_sha256,
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
