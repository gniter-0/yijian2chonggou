"""Live 编排辅助库（P3 T2 落地基，T4b 通用全链驱动主体）。

T2 两件（任务书 `docs/prework/P3_优化施工方案_T1-T5_20260812.md` §2 第 3 条）：

- ``ResendPlan``：失败样本重发的身份管理。``manual_reissue_reason`` 参与 Step2
  请求 identity 摘要（`contracts/request_identity.py`），因此「重发」= 换新 reason
  后缀的**新身份**；本类把 pilot 实测口径（首发用 base reason 原文、重发 ``-rN``
  后缀、失败身份禁复用、轮数上限）抽为可复用对象。
- ``load_run_env()`` 与双通道取值函数：运行配置**默认读新仓根 `.env`**（gitignored；
  自 yijian2/.env 迁值，从此原仓零接触），支持显式传路径；文本/图像双通道取值，
  ``IMAGE_*`` 显式键优先、回落通用键。密钥值仅驻内存，调用方不得打印/落盘/入报告。

T4b 编排主体（设计稿 `docs/design/live_pipeline_driver_design.md`，含 R1/R2/R3 修订）：

- ``ExamPaths``：exams/<exam_id>/ 目录契约解析（runbook §1；registry_id 互锁）；
- ``LiveRunBudget``：双护栏预算账本（调用数 × 美元，charge-before-call，持久化续跑）；
- ``RunState``：run_state.json 状态机（断点续跑；损坏可从 stage 报告重建）；
- ``StageAdapter`` 注入协议：**编排层不 import migration**——具体 stage 业务（经
  实测的 pilot 驱动）由 CLI 侧注入；未注入的 stage 一律 fail-closed
  ``stage_adapter_unavailable``（对未来新考试即"资产链未就绪"的显式信号，
  对应设计稿 R1 的转换器开口纪律：宁可拒开，不得发明）；
- 检查点①（准入）/②（图像评审）：文件为契约（sheet 产出 → 退出码 2 暂停 →
  人工决定文件 → 严格摄取），webui 只是可选检视器；
- ``orchestrate()``：固定 DAG 排序 → 预检门禁 → 逐 stage → 报告物化（自证哈希）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .shadow_wiring import load_env_file

_REPO_ROOT = Path(__file__).resolve().parents[3]

# 驱动侧统一执行 ≤96 的 reason 上限（identity 模型本身允许 ≤160；pilot/T4a 口径取 96）。
_REASON_MAX_LENGTH = 96
_REASON_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")


# ---------------------------------------------------------------------------
# 运行配置（.env）读取与双通道取值
# ---------------------------------------------------------------------------


def default_env_path() -> Path:
    """新仓根 `.env` 的法定缺省路径（exam-card-engine/.env）。"""

    return _REPO_ROOT / ".env"


def load_run_env(env_file: Path | str | None = None) -> dict[str, str]:
    """读取运行配置；缺省新仓根 `.env`，可显式传路径。值仅驻内存，不得记录。

    解析口径与 ``shadow_wiring.load_env_file`` 完全一致（同键 last-wins）——新仓
    `.env` 约定通用键只留一对（文本通道），图像通道走显式 ``IMAGE_API_*`` 键。
    """

    path = Path(env_file) if env_file is not None else default_env_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"run env file does not exist: {path} "
            "(the new-repo .env is gitignored — recreate it per new_exam_onboarding_runbook.md, "
            "or pass env_file explicitly)"
        )
    return load_env_file(path)


def _channel_value(env: Mapping[str, str], keys: tuple[str, ...], *, channel: str) -> str:
    for key in keys:
        value = (env.get(key) or "").strip()
        if value:
            return value
    raise ValueError(f"{channel} channel is not configured: none of {list(keys)} is set in the run env")


def text_base_url(env: Mapping[str, str]) -> str:
    return _channel_value(env, ("API_BASE_URL",), channel="text")


def text_api_key(env: Mapping[str, str]) -> str:
    return _channel_value(env, ("API_KEY",), channel="text")


def image_base_url(env: Mapping[str, str]) -> str:
    """图像通道 base_url：``IMAGE_API_BASE_URL`` 显式键优先，缺席回落通用键。"""

    return _channel_value(env, ("IMAGE_API_BASE_URL", "API_BASE_URL"), channel="image")


def image_api_key(env: Mapping[str, str]) -> str:
    """图像通道密钥：``IMAGE_API_KEY`` 显式键优先，缺席回落通用键。"""

    return _channel_value(env, ("IMAGE_API_KEY", "API_KEY"), channel="image")


def image_channel_fallback(env: Mapping[str, str]) -> bool:
    """图像通道是否回落到了文本通道同源（T4a §8.1 要求报告登记此事实）。"""

    return not ((env.get("IMAGE_API_BASE_URL") or "").strip() and (env.get("IMAGE_API_KEY") or "").strip())


# ---------------------------------------------------------------------------
# 失败样本重发的身份管理
# ---------------------------------------------------------------------------


class ResendRoundsExhausted(RuntimeError):
    """样本重发轮数耗尽：调用方必须升级处置（改派/取消/接受现状），不得复用失败身份。"""


class ResendPlan:
    """失败样本重发的身份管理（新 reason 后缀 → 新身份；禁复用；轮数上限）。

    不变式（与 step3-v2 驱动的重发四不变式及 pilot 实测口径对齐）：

    - 首发 attempt=1 使用 base reason 原文（live 身份必须携带 reason）；
    - 重发 attempt≥2 派生 ``<base>-rN``——因 ``manual_reissue_reason`` 参与
      identity 摘要，派生 reason 即新身份；
    - 每（样本, 轮次）的 reason 只签发一次，失败身份从结构上不可复用；
    - 每样本轮数上限（缺省 3，62 章实证口径：首发 ~60%、重发累计 >95%）。
    """

    def __init__(self, *, base_reason: str, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("resend plan requires at least one attempt per sample")
        # fail-closed：构造期即验证最长派生 reason 仍满足审计标识符文法与长度。
        self.reason_for_attempt(base_reason, max_attempts)
        self.base_reason = base_reason
        self.max_attempts = max_attempts
        self._issued: dict[str, list[str]] = {}

    @staticmethod
    def reason_for_attempt(base_reason: str, attempt: int) -> str:
        """attempt=1 → base reason 原文；attempt≥2 → ``<base>-rN`` 新后缀。"""

        if attempt < 1:
            raise ValueError("resend attempts are 1-based")
        reason = base_reason if attempt == 1 else f"{base_reason}-r{attempt}"
        if len(reason) > _REASON_MAX_LENGTH or _REASON_PATTERN.fullmatch(reason) is None:
            raise ValueError(
                "manual reissue reason must be an ASCII audit identifier "
                f"([a-z0-9][a-z0-9._-]*) of at most {_REASON_MAX_LENGTH} characters: {reason!r}"
            )
        return reason

    def attempts_used(self, sample_id: str) -> int:
        return len(self._issued.get(sample_id, ()))

    def can_issue(self, sample_id: str) -> bool:
        return self.attempts_used(sample_id) < self.max_attempts

    def issued_reasons(self, sample_id: str) -> tuple[str, ...]:
        return tuple(self._issued.get(sample_id, ()))

    def next_reason(self, sample_id: str) -> str:
        """为该样本签发下一轮的 reason（新身份）；轮数耗尽抛 ``ResendRoundsExhausted``。"""

        issued = self._issued.setdefault(sample_id, [])
        attempt = len(issued) + 1
        if attempt > self.max_attempts:
            raise ResendRoundsExhausted(
                f"sample {sample_id!r} exhausted its {self.max_attempts} identity rounds; "
                "escalate instead of reusing a failed identity"
            )
        reason = self.reason_for_attempt(self.base_reason, attempt)
        if reason in issued:  # 结构上不可达；保留为最后一道硬性守卫。
            raise ValueError(f"resend reason was already issued for sample {sample_id!r}: {reason}")
        issued.append(reason)
        return reason


# ===========================================================================
# T4b：通用全链 live 驱动编排（设计 docs/design/live_pipeline_driver_design.md）
#
# 分层纪律（设计 §1/§12）：本模块承担全部可测编排逻辑；CLI（migration/
# run_exam_pipeline.py）只做参数装配与注入。所有外呼客户端/预检执行器/
# 决策构建器一律注入（provider-injection，与 pipeline/run.py 同哲学），
# 故全部函数可用伪 transport 离线测试；src 决不 import migration。
# ===========================================================================

import json
import random
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ..common.freeze import build_freeze
from ..common.hashing import SHA256_HEX, canonical_hash, hash_model
from ..common.io import atomic_write_json, read_json
from ..materials.schema import StrictModel

LIVE_STAGES: tuple[str, ...] = ("step1", "step2a", "step2b", "step3", "step4")
LIVE_STAGE_REPORT_VERSION = "examcard_live_stage_report_v1"
LIVE_RUN_REPORT_VERSION = "examcard_live_run_report_v1"
DEFAULT_PRICING_RELATIVE = "configs/v2/live_pricing_v1.yaml"
_RUN_LABEL_MAX = 64

# 预检需求键（§8.2 放行矩阵）：step1 纯离线不设预检项；step3 v1/v2 同用 step3_v2
# 画像（strict 强制 + 窗口/流式是两版共同的传输前置）。
_STAGE_PREFLIGHT_KEYS = {"step2a": "step2a", "step2b": "step2a", "step3": "step3_v2", "step4": "step4"}


def _utc_now_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_run_id(run_label: str) -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{_utc_now_compact()}-{run_label}-{suffix}"


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


# ---------------------------------------------------------------------------
# exams/ 目录契约解析（§3；漂移核查 N1 互锁纪律）
# ---------------------------------------------------------------------------


class MissingUpstreamArtifact(RuntimeError):
    """前置产物/资产缺席：消息必须指名缺哪件、该跑哪段（§9 missing_upstream_artifact）。"""


@dataclass(frozen=True)
class ExamPaths:
    """目录契约解析器：一切路径仓根相对派生；evidence/facts 一律从 registry_id 派生。"""

    repo_root: Path
    exam_id: str
    exam_root: Path
    input_pdf_dir: Path
    registry_path: Path
    annotation_sidecar_path: Path
    registry_id: str
    material_profile_id: str
    material_profile_path: Path
    content_policy_id: str
    content_policy_path: Path
    evidence_root: Path
    facts_root: Path
    live_runs_root: Path

    @classmethod
    def resolve(cls, repo_root: Path, exam_id: str) -> "ExamPaths":
        repo_root = Path(repo_root).resolve()
        exam_root = repo_root / "exams" / exam_id
        if not exam_root.is_dir():
            raise MissingUpstreamArtifact(
                f"exam workspace is missing: exams/{exam_id}/ — create it per runbook §1 (exams/ directory contract)"
            )
        registry_path = exam_root / "fixtures" / "golden_samples" / f"{exam_id}_phase0.yaml"
        if not registry_path.is_file():
            raise MissingUpstreamArtifact(
                f"golden registry is missing: {registry_path.relative_to(repo_root).as_posix()} — author it per runbook §3"
            )
        from ..evidence.golden import load_golden_registry
        from ..materials.catalog import load_material_profile

        registry = load_golden_registry(registry_path)
        # 互锁纪律（漂移核查 N1）：evidence/facts 路径一律 registry_id 派生，勿手写字面量。
        profile_path = repo_root / "configs" / "v2" / "material_profiles" / f"{registry.material_profile_id}.yaml"
        if not profile_path.is_file():
            raise MissingUpstreamArtifact(
                f"material profile is missing: {profile_path.relative_to(repo_root).as_posix()} — author it per runbook §2"
            )
        profile = load_material_profile(profile_path)
        if profile.profile_id != registry.material_profile_id:
            raise ValueError(
                "material profile interlock violated: registry.material_profile_id="
                f"{registry.material_profile_id!r} != profile.profile_id={profile.profile_id!r}"
            )
        annotation_path = exam_root / "fixtures" / "golden_annotations" / f"{registry.registry_id}.yaml"
        policy_path = repo_root / "configs" / "v2" / "content_policies" / f"{profile.content_policy_id}.yaml"
        if not policy_path.is_file():
            raise MissingUpstreamArtifact(
                f"content policy is missing: {policy_path.relative_to(repo_root).as_posix()} — reuse or author per runbook §2"
            )
        return cls(
            repo_root=repo_root,
            exam_id=exam_id,
            exam_root=exam_root,
            input_pdf_dir=exam_root / "input_pdf",
            registry_path=registry_path,
            annotation_sidecar_path=annotation_path,
            registry_id=registry.registry_id,
            material_profile_id=profile.profile_id,
            material_profile_path=profile_path,
            content_policy_id=profile.content_policy_id,
            content_policy_path=policy_path,
            evidence_root=exam_root / "data" / "evidence_v2" / "golden" / registry.registry_id,
            facts_root=exam_root / "data" / "facts_v2" / "golden" / registry.registry_id / profile.content_policy_id,
            live_runs_root=exam_root / "data" / "live_runs",
        )

    def missing_for_stage(self, stage: str, *, planned_stages: Sequence[str] = ()) -> list[str]:
        """`--stages` 子集缺前置产物时的指名清单（§9 missing_upstream_artifact）。

        本 run 计划内将由更早 stage 产出的产物不判缺（如 step1 与 step2a 同场时，
        evidence 产物按 satisfied-by-plan 处理）；人工签字物（注册表/标注侧车）
        没有产出者，恒检查。
        """

        missing: list[str] = []
        rel = lambda path: Path(path).relative_to(self.repo_root).as_posix()  # noqa: E731
        if stage == "step1":
            if not self.input_pdf_dir.is_dir() or not any(self.input_pdf_dir.iterdir()):
                missing.append(f"{rel(self.input_pdf_dir)} (source PDFs; runbook §1)")
        if stage in {"step2a", "step2b"}:
            if not self.evidence_root.is_dir() and "step1" not in planned_stages:
                missing.append(f"{rel(self.evidence_root)} (step1 evidence output; run --stages step1)")
            if not self.annotation_sidecar_path.is_file():
                missing.append(f"{rel(self.annotation_sidecar_path)} (golden annotation sidecar; runbook §3)")
        return missing


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


# ---------------------------------------------------------------------------
# 报告模型（谱系对齐 PipelineStepResult/PipelineRunReport；新版本号，不复用离线钉扎）
# ---------------------------------------------------------------------------


class LiveStageReport(StrictModel):
    schema_version: Literal["examcard_live_stage_report_v1"] = LIVE_STAGE_REPORT_VERSION
    stage: str = Field(min_length=1)
    status: Literal["completed", "completed_with_failures", "failed", "skipped", "waiting_human", "pending"]
    detail: str = Field(min_length=1)
    samples_total: int = Field(ge=0, default=0)
    samples_succeeded: int = Field(ge=0, default=0)
    samples_failed: int = Field(ge=0, default=0)
    network_calls: int = Field(ge=0, default=0)
    result_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_result(self) -> "LiveStageReport":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("live stage report hash mismatch")
        return self


class LiveRunReport(StrictModel):
    schema_version: Literal["examcard_live_run_report_v1"] = LIVE_RUN_REPORT_VERSION
    run_id: str = Field(min_length=3)
    exam_id: str = Field(min_length=1)
    mode: Literal["dry_run", "live"]
    stages: tuple[LiveStageReport, ...] = Field(min_length=1)
    completed: bool
    stop_reason: str = Field(min_length=1)
    network_calls: int = Field(ge=0)
    usd_estimate: float = Field(ge=0)
    report_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_report(self) -> "LiveRunReport":
        seen_failure = False
        for item in self.stages:
            if seen_failure and item.status not in {"skipped", "pending"}:
                raise ValueError("stages after a failure must be skipped")
            if item.status == "failed":
                seen_failure = True
        if self.completed != (self.stop_reason == "completed"):
            raise ValueError("live run completion flag mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "report_sha256"}))
        if self.report_sha256 != expected:
            raise ValueError("live run report hash mismatch")
        return self


def build_stage_report(stage: str, status: str, detail: str, **counts: int) -> LiveStageReport:
    payload = {"stage": stage, "status": status, "detail": detail, **counts}
    return hash_model(LiveStageReport, payload, "result_sha256")  # type: ignore[return-value]


def build_live_run_report(
    *,
    run_id: str,
    exam_id: str,
    mode: str,
    stages: Sequence[LiveStageReport],
    stop_reason: str,
    network_calls: int,
    usd_estimate: float,
) -> LiveRunReport:
    payload = {
        "run_id": run_id,
        "exam_id": exam_id,
        "mode": mode,
        "stages": tuple(stages),
        "completed": stop_reason == "completed",
        "stop_reason": stop_reason,
        "network_calls": network_calls,
        "usd_estimate": round(usd_estimate, 6),
    }
    return hash_model(LiveRunReport, payload, "report_sha256")  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# step2a/step2b 样本执行（§5：cache-state 跳过 + 失败重发 + 门禁登记不熔断）
# ---------------------------------------------------------------------------


def _cache_state(storage: Path, cache_key: str) -> str | None:
    cache_path = storage / "cache" / f"{cache_key.replace(':', '_')}.json"
    if not cache_path.is_file():
        return None
    return str(read_json(cache_path).get("state"))


def execute_stage_samples(
    *,
    stage: Literal["step2a", "step2b"],
    sample_ids: Sequence[str],
    context_builder: Callable[..., Sequence[Any]],
    transport: Any,
    storage: Path,
    budget: LiveRunBudget,
    resend: ResendPlan,
    stage_executor: Callable[..., dict[str, Any]] | None = None,
    probe: Callable[[str], dict[str, Any]] | None = None,
    gate_evaluator: Callable[[str, Any, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    price_per_call: float = 0.0,
    probe_price: float = 0.0,
) -> dict[str, Any]:
    """step2a/step2b 共用样本执行器。

    - 断点续跑：同 (样本, reason) 身份的 cache `state=success` 直接复用跳过；
      `failed/unknown_outcome` 落入重发道（ResendPlan 新 reason → 新身份），
      identity 拒绝复用语义由 `_execute_stage` 的三件存在性检查兜底（§5.1）。
    - 探针：stage 前置 strict 探针连败（重发 1 次的现行口径）→ 整 stage fail-closed，
      不进样本重发道；样本级失败登记不熔断（质量测量），重发轮耗尽记 failed。
    - 门禁登记：gate_evaluator 结果只登记不影响 outcome（pilot 口径）。
    """

    if stage_executor is None:
        from ..knowledge.contracts.phase2r14_v25_live import _execute_stage as stage_executor  # type: ignore[assignment]

    storage.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {"stage": stage, "probe": None, "samples": [], "status": "completed"}
    if probe is not None:
        probe_results = []
        for _ in range(2):  # 现行口径：探针失败重发一次
            budget.charge(stage, calls=1, usd_estimate=probe_price)
            result = probe(stage)
            probe_results.append(result)
            if result.get("outcome") == "success":
                break
        outcome["probe"] = probe_results
        if probe_results[-1].get("outcome") != "success":
            outcome["status"] = "failed"
            outcome["stop_reason"] = f"{stage}_probe_failed"
            return outcome

    failed_samples = 0
    for sample_id in sample_ids:
        entry: dict[str, Any] = {"sample_id": sample_id, "attempts": [], "outcome": "failed"}
        outcome["samples"].append(entry)
        while True:
            if not resend.can_issue(sample_id):
                break
            reason = resend.next_reason(sample_id)
            contexts = context_builder(sample_ids=(sample_id,), reason=reason)
            context = contexts[0]
            request = context.step2a_request if stage == "step2a" else context.step2b_request
            cache_key = request.identity.cache_key
            attempt_entry: dict[str, Any] = {"reason": reason, "request_id": request.identity.request_id}
            cached = _cache_state(storage, cache_key)
            if cached == "success":
                attempt_entry["state"] = "success"
                attempt_entry["reused_cache"] = True
                entry["attempts"].append(attempt_entry)
                entry["outcome"] = "success"
                break
            if cached is not None:
                attempt_entry["state"] = cached
                attempt_entry["reused_cache"] = True
                entry["attempts"].append(attempt_entry)
                continue  # 已有失败身份：换下一轮新身份，不重放
            budget.charge(stage, calls=1, usd_estimate=price_per_call)
            record = stage_executor(storage_root=storage, transport=transport, request=request, stage=stage)
            attempt_entry["state"] = record.get("state")
            attempt_entry["http_status"] = record.get("http_status")
            attempt_entry["latency_ms"] = record.get("latency_ms")
            entry["attempts"].append(attempt_entry)
            if record.get("state") == "success":
                entry["outcome"] = "success"
                if gate_evaluator is not None:
                    try:
                        entry["gates"] = dict(gate_evaluator(stage, context, record))
                    except Exception as error:  # 门禁登记不熔断（测量面）
                        entry["gates"] = {"gate_error": f"{type(error).__name__}: {error}"}
                break
        if entry["outcome"] != "success":
            failed_samples += 1
    if failed_samples:
        outcome["status"] = "completed_with_failures"
        outcome["failed_samples"] = failed_samples
    return outcome


# ---------------------------------------------------------------------------
# step3：echo（v2 单卡缺省 / v1 三卡存量）与 release-bypass 双取材模式（§4）
# ---------------------------------------------------------------------------


def load_typed_fixture_compilation(typed_fixture_path: Path) -> Any:
    """echo 模式取材：显式 reviewed typed-evidence fixture → 离线编译。

    「检查点①放行产物 → typed fixture」的自动转换器在新仓缺席（设计 §7.1 已知
    开口，T4b 首项验证点已核实并 need_input 上报）；在总指挥裁决补搬运之前，
    echo 模式仅接受显式给定的既有 fixture（如 r810），缺席即 fail-closed。
    """

    from ..compilers.step3_card import compile_reviewed_fixture
    from ..knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture

    path = Path(typed_fixture_path)
    if not path.is_file():
        raise MissingUpstreamArtifact(
            f"step3 echo mode needs a reviewed typed-evidence fixture: {path} — pass --step3-typed-fixture "
            "(the admitted-step2→typed-fixture converter is absent from this repo; use --step3-mode release-bypass otherwise)"
        )
    fixture_bytes = path.read_bytes()
    import hashlib as _hashlib

    fixture = ReviewedTypedEvidenceFixture.model_validate_json(fixture_bytes.decode("utf-8"))
    return compile_reviewed_fixture(fixture, source_fixture_sha256=_hashlib.sha256(fixture_bytes).hexdigest())


def run_step3_stage(
    *,
    compilation: Any,
    contract_version: Literal["v1", "v2"],
    chat_strict: Callable[..., Mapping[str, Any]],
    storage: Path,
    budget: LiveRunBudget,
    base_reason: str,
    max_attempts: int = 2,
    price_per_call: float = 0.0,
    probe_price: float = 0.0,
    probe: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """step3 echo 逐卡循环（吸收 T3 独立驱动结构；N 卡通用，勿写死卡数）。

    ``chat_strict(messages=…, response_schema=…, schema_name=…, max_tokens=…)``
    由 CLI 注入（SSE 流式聚合最小信封字节返回 ``{"content": str, "http_status",
    "error_type", "latency_ms"}``）；离线测试注入确定性回显伪 transport。
    重发四不变式沿 T3 落地口径：attempt 进 identity seed、补强/流式不进。
    """

    storage.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {"stage": "step3", "mode": "echo", "contract": contract_version, "cards": [], "probe": None}
    if probe is not None:
        budget.charge("step3", calls=1, usd_estimate=probe_price)
        probe_result = dict(probe())
        outcome["probe"] = probe_result
        if probe_result.get("outcome") != "success":
            outcome["status"] = "failed"
            outcome["stop_reason"] = "step3_probe_failed"
            return outcome

    if contract_version == "v2":
        from ..compilers.step3_response_v2 import (
            RESULT_SCHEMA_V2,
            build_single_card_model_input,
            build_single_card_prompt,
            make_single_card_inspector,
        )

        prompt = build_single_card_prompt()
        reinforcement = (
            "You must return draft_cards with exactly 1 item that echoes the supplied card verbatim from "
            "MODEL_INPUT_JSON. An empty draft_cards array is invalid. The top-level schema_version of your "
            "reply must be exactly the value required by the response json schema; do not copy the "
            "schema_version value from MODEL_INPUT_JSON."
        )
        pairs = list(zip(compilation.contracts, compilation.model_input.cards, strict=True))
        passed = 0
        for index, (contract, projected) in enumerate(pairs, start=1):
            model_input = build_single_card_model_input(contract, projected)
            inspector = make_single_card_inspector(model_input)
            user_content = (
                prompt.prompt_text
                + "\n\nMODEL_INPUT_JSON:\n"
                + json.dumps(model_input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            card_dir = storage / "cards" / f"{index:02d}_{contract.contract_id.rsplit(':', 1)[-1]}"
            atomic_write_json(
                card_dir / "model-input.json",
                {"model_input": model_input.model_dump(mode="json"), "user_content_utf8_bytes": len(user_content.encode("utf-8"))},
            )
            card_report: dict[str, Any] = {
                "index": index,
                "contract_id": contract.contract_id,
                "presentation": contract.presentation,
                "attempts": [],
                "outcome": "failed",
            }
            outcome["cards"].append(card_report)
            for attempt in range(1, max_attempts + 1):
                reissue = None if attempt == 1 else f"{base_reason}-r{attempt}"
                seed = {
                    "driver": "run_exam_pipeline.step3.v2",
                    "model_input_id": model_input.model_input_id,
                    "attempt": attempt,
                    "manual_reissue_reason": reissue,
                }
                request_id = f"step3-v2-live-request:{canonical_hash(seed)}"
                budget.charge("step3", calls=1, usd_estimate=price_per_call)
                result = dict(
                    chat_strict(
                        messages=[
                            {"role": "system", "content": reinforcement},
                            {"role": "user", "content": user_content},
                        ],
                        response_schema=RESULT_SCHEMA_V2,
                        schema_name="step3_v2_single_card_result",
                        max_tokens=6000,
                    )
                )
                content = str(result.get("content") or "")
                envelope = json.dumps(
                    {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]},
                    ensure_ascii=False,
                ).encode("utf-8")
                (card_dir / f"attempt-{attempt}.response.raw.json").parent.mkdir(parents=True, exist_ok=True)
                (card_dir / f"attempt-{attempt}.response.raw.json").write_bytes(envelope)
                gate = inspector(envelope) if content else None
                if gate is not None:
                    atomic_write_json(card_dir / f"attempt-{attempt}.gate.json", gate.model_dump(mode="json"))
                attempt_entry = {
                    "attempt": attempt,
                    "request_id": request_id,
                    "http_status": result.get("http_status"),
                    "latency_ms": result.get("latency_ms"),
                    "error_type": result.get("error_type"),
                    "gate_valid": bool(gate and gate.valid),
                    "gate_failures": list(gate.failure_codes) if gate is not None else ["inner-not-reached"],
                }
                card_report["attempts"].append(attempt_entry)
                if gate is not None and gate.valid:
                    card_report["outcome"] = "success"
                    passed += 1
                    break
        outcome["passed_cards"] = passed
        outcome["card_count"] = len(pairs)
        outcome["status"] = "completed" if passed == len(pairs) else "completed_with_failures"
        return outcome

    # v1 三卡存量重放通道（仅存量重放使用；两中继证伪史见 runbook §8）。
    from types import SimpleNamespace

    from ..compilers.step3_response import (
        ResponseDocument,
        build_prompt,
        build_response_model_input,
        make_inner_inspector,
    )

    representatives = []
    for presentation in ("table", "ordered_list", "formula"):
        pair = next(
            (
                (contract, projected)
                for contract, projected in zip(compilation.contracts, compilation.model_input.cards, strict=True)
                if contract.presentation == presentation
            ),
            None,
        )
        if pair is None:
            raise MissingUpstreamArtifact(f"v1 step3 contract needs a {presentation} card in the fixture")
        contract, projected = pair
        representatives.append(
            SimpleNamespace(
                contract_id=contract.contract_id,
                source_candidate_id=contract.source_candidate_id,
                fact_ids=contract.fact_ids,
                presentation=contract.presentation,
                source_binding=projected.source_binding,
                visible_elements=projected.visible_elements,
                layout=projected.layout,
                geometry=projected.geometry,
            )
        )
    model_input = build_response_model_input(representatives, source_fixture_sha256=compilation.source_fixture_sha256)
    inspector = make_inner_inspector(model_input)
    prompt = build_prompt()
    user_content = (
        prompt.prompt_text
        + "\n\nMODEL_INPUT_JSON:\n"
        + json.dumps(model_input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    budget.charge("step3", calls=1, usd_estimate=price_per_call)
    result = dict(
        chat_strict(
            messages=[{"role": "user", "content": user_content}],
            response_schema=ResponseDocument.model_json_schema(),
            schema_name="step3_result",
            max_tokens=8000,
        )
    )
    content = str(result.get("content") or "")
    envelope = json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}, ensure_ascii=False
    ).encode("utf-8")
    (storage / "v1-response.raw.json").write_bytes(envelope)
    gate = inspector(envelope) if content else None
    valid = bool(gate and gate.valid)
    outcome.update(
        {
            "card_count": 3,
            "passed_cards": 3 if valid else 0,
            "gate_failures": list(gate.failure_codes) if gate is not None else ["inner-not-reached"],
            "status": "completed" if valid else "completed_with_failures",
        }
    )
    return outcome


def load_step3_release_bypass(*, release_fixture_path: Path) -> dict[str, Any]:
    """release-bypass 取材校验（方案 v1.2 勘误道）：既有 reviewed 发布 fixture 直进 step4。"""

    import hashlib as _hashlib

    import yaml

    from ..knowledge.contracts.admission_fixtures import Step3UnifiedReviewedReleaseFixture

    path = Path(release_fixture_path)
    if not path.is_file():
        raise MissingUpstreamArtifact(f"release-bypass fixture is missing: {path}")
    fixture_bytes = path.read_bytes()
    fixture = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(fixture_bytes.decode("utf-8")))
    return {
        "mode": "release-bypass",
        "fixture_path": str(path),
        "fixture_sha256": _hashlib.sha256(fixture_bytes).hexdigest(),
        "card_count": len(fixture.cards),
    }


# ---------------------------------------------------------------------------
# step4 出图（§4；providers 五字段投影 + 具名信封门禁；prompt 构建器注入）
# ---------------------------------------------------------------------------


def run_step4_stage(
    *,
    release_fixture_path: Path,
    handoff_path: Path,
    prompt_builder: Callable[[Any], str],
    image_caller: Callable[[Mapping[str, Any]], tuple[int, bytes]],
    models_probe: Callable[[], Mapping[str, Any]] | None,
    image_model: str,
    storage: Path,
    budget: LiveRunBudget,
    drift_classifier: Callable[[bytes, Any], bytes | None] | None = None,
    image_width: int = 1024,
    image_height: int = 1536,
    max_attempts: int = 2,
    price_per_call: float = 0.0,
    probe_price: float = 0.0,
    only_cards: set[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """step4 出图编排（pilot 驱动结构吸收进库；PNG 存在即跳过 = 断点续跑）。"""

    import base64

    import yaml

    from ..compilers.step4_contract import derive_step4_bundle
    from ..knowledge.contracts.admission_fixtures import (
        Step3UnifiedReviewedReleaseFixture,
        Step4PilotHandoffRefresh,
    )
    from ..providers import build_image_profile, inspect_image_envelope, project_image_request

    fixture_path = Path(release_fixture_path)
    handoff_file = Path(handoff_path)
    if not fixture_path.is_file():
        raise MissingUpstreamArtifact(f"step4 needs the reviewed release fixture: {fixture_path}")
    if not handoff_file.is_file():
        raise MissingUpstreamArtifact(f"step4 needs the pilot handoff refresh: {handoff_file}")
    fixture_bytes = fixture_path.read_bytes()
    handoff_bytes = handoff_file.read_bytes()
    import hashlib as _hashlib

    fixture = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(fixture_bytes.decode("utf-8")))
    handoff = Step4PilotHandoffRefresh.model_validate_json(handoff_bytes.decode("utf-8"))
    bundle, model_input, _request, _artifacts, _batch = derive_step4_bundle(
        fixture,
        handoff,
        unified_fixture_sha256=_hashlib.sha256(fixture_bytes).hexdigest(),
        handoff_sha256=_hashlib.sha256(handoff_bytes).hexdigest(),
    )
    profile = build_image_profile(model=image_model, image_width=image_width, image_height=image_height)
    storage.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {
        "stage": "step4",
        "card_count": len(bundle.cards),
        "fact_count": model_input.fact_count,
        "probe": None,
        "cards": [],
    }
    projections = []
    for card in bundle.cards:
        prompt = prompt_builder(card)
        payload = {
            "model": image_model,
            "n": 1,
            "prompt": prompt,
            "response_format": "b64_json",
            "size": f"{image_width}x{image_height}",
            "source_card_id": card.source_card_id,
            "card_index": card.card_index,
            "sample_id": card.sample_id,
        }
        projection, _sidecar = project_image_request(payload, profile)
        projections.append({"card": card, "prompt": prompt, "projection": projection})
    atomic_write_json(
        storage / "requests-manifest.json",
        [
            {
                "card_index": entry["card"].card_index,
                "source_card_id": entry["card"].source_card_id,
                "render_presentation": entry["card"].render_presentation,
                "prompt_utf8_bytes": len(entry["prompt"].encode("utf-8")),
                "http_payload_sha256": entry["projection"].http_payload_sha256,
            }
            for entry in projections
        ],
    )
    if dry_run:
        outcome["status"] = "completed"
        outcome["detail"] = "dry_run_assembled"
        return outcome

    if models_probe is not None:
        budget.charge("step4", calls=1, usd_estimate=probe_price)
        probe_result = dict(models_probe())
        outcome["probe"] = probe_result
        if not probe_result.get("model_advertised"):
            outcome["status"] = "failed"
            outcome["stop_reason"] = "step4_probe_failed"
            return outcome

    images_dir = storage / "images"
    succeeded = 0
    for entry in projections:
        card = entry["card"]
        if only_cards is not None and card.card_index not in only_cards:
            continue
        image_path = images_dir / f"step4-card-{card.card_index:02d}-{card.source_card_id.rsplit(':', 1)[-1][:8]}.png"
        record: dict[str, Any] = {
            "card_index": card.card_index,
            "source_card_id": card.source_card_id,
            "render_presentation": card.render_presentation,
            "outcome": "failed",
            "attempts": [],
        }
        outcome["cards"].append(record)
        if image_path.exists():
            record["outcome"] = "success"
            record["image_path"] = image_path.relative_to(storage).as_posix()
            record["reused_existing"] = True
            succeeded += 1
            continue
        payload = entry["projection"].http_request.model_dump(mode="json")
        for attempt in range(1, max_attempts + 1):
            budget.charge("step4", calls=1, usd_estimate=price_per_call)
            status_code, body = image_caller(payload)
            gate = inspect_image_envelope(status_code, body, entry["prompt"], profile)
            drift = None
            if not gate.valid and drift_classifier is not None:
                stripped = drift_classifier(body, profile)
                if stripped is not None:
                    regate = inspect_image_envelope(status_code, stripped, entry["prompt"], profile)
                    if regate.valid:
                        drift = "data0_extra_width_height_stripped"
                        gate = regate
                        body = stripped
            attempt_entry = {
                "attempt": attempt,
                "http_status": status_code,
                "gate_valid": gate.valid,
                "gate_failures": list(gate.failure_codes),
                "envelope_drift": drift,
            }
            record["attempts"].append(attempt_entry)
            if gate.valid:
                document = json.loads(body.decode("utf-8"))
                image_bytes = base64.b64decode(document["data"][0]["b64_json"], validate=True)
                images_dir.mkdir(parents=True, exist_ok=True)
                image_path.write_bytes(image_bytes)
                record["outcome"] = "success"
                record["image_path"] = image_path.relative_to(storage).as_posix()
                record["image_sha256"] = gate.image_sha256
                succeeded += 1
                break
    attempted = len(outcome["cards"])
    outcome["succeeded_cards"] = succeeded
    outcome["status"] = "completed" if succeeded == attempted and attempted else "completed_with_failures"
    return outcome


# ---------------------------------------------------------------------------
# 人工检查点（§7：文件为契约；驱动产出物料→退出码 2 暂停→严格摄取）
# ---------------------------------------------------------------------------


def build_admission_checkpoint(
    *,
    run_root: Path,
    queue_items: Sequence[Mapping[str, Any]],
    workbench_renderer: Callable[[Sequence[Mapping[str, Any]]], str] | None = None,
) -> dict[str, Any]:
    """检查点①物料（G-A1/G-A3/G-A4）：queue.json + workbench.html + progress + inbox。

    ``queue_items`` 逐条为 AdmissionQueueItem 的构造载荷（不含 entry_sha256，
    disposition 恒 manual_review）；封印走 r8x 法定哈希谱系。
    """

    from ..gates.review.queue import AdmissionQueueItem, ManualReviewQueue, queue_sha256 as _queue_sha256
    from ..knowledge.contracts.admission_legacy_hash import canonical_hash as legacy_hash, hash_model_r8x

    checkpoint_dir = Path(run_root) / "checkpoints" / "admission"
    inbox = checkpoint_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    sealed_items = []
    for payload in queue_items:
        document = dict(payload)
        document.setdefault("disposition", "manual_review")
        sealed_items.append(hash_model_r8x(AdmissionQueueItem, document, "entry_sha256"))
    queue_hash = _queue_sha256(sealed_items)
    queue = ManualReviewQueue(
        queue_sha256=queue_hash,
        candidates=tuple(sealed_items),
        queue_count=len(sealed_items),
        queue_sha256_projection=legacy_hash([item.model_dump(mode="json") for item in sealed_items]),
    )
    atomic_write_json(checkpoint_dir / "queue.json", queue.model_dump(mode="json"))
    workbench_path = None
    if workbench_renderer is not None:
        html = workbench_renderer([item.model_dump(mode="json") for item in sealed_items])
        workbench_path = checkpoint_dir / "workbench.html"
        workbench_path.write_text(html, encoding="utf-8")
    from ..gates.review.browser import build_progress

    progress = build_progress(
        queue_hash,
        [
            {
                "candidate_id": item.candidate_id,
                "queue_item_id": item.queue_item_id,
                "subject_id": item.subject_id,
                "presentation": item.presentation,
                "disposition": item.disposition,
                "explicit_decision": False,
            }
            for item in sealed_items
        ],
    )
    atomic_write_json(checkpoint_dir / "progress.json", progress.model_dump(mode="json"))
    instructions = (
        "checkpoint(1) admission: review the queue, then drop review-browser-decisions.json into inbox/ "
        "(or an authoritative human-decision-manifest.json), and resume the run with --resume <run_id>.\n"
        "workbench static server (run from repo root):\n"
        "  .venv\\Scripts\\python.exe -m http.server 8777 --bind 127.0.0.1 --directory <exam workspace root>\n"
    )
    (checkpoint_dir / "INSTRUCTIONS.txt").write_text(instructions, encoding="utf-8")
    return {
        "checkpoint": "admission",
        "queue_sha256": queue_hash,
        "queue_count": len(sealed_items),
        "queue_path": str(checkpoint_dir / "queue.json"),
        "workbench_path": str(workbench_path) if workbench_path else None,
        "inbox_path": str(inbox),
        "status": "waiting_human",
    }


def ingest_admission_decisions(
    *,
    run_root: Path,
    candidates_by_id: Mapping[str, Any],
    expected_queue_sha256: str,
    decision_builder: Callable[[Any, Any], Any],
) -> dict[str, Any]:
    """检查点①严格摄取（G-A2）：raw 浏览器信封 → 分类降格 → 权威决策重建。

    浏览器导出的 ``reviewed_candidate`` 一律降格 manual_review（r82 定式，本函数
    不新造语义）；权威放行集合只能来自 decision_builder 产出的 CLI 侧权威决策。
    决定文件非法/哈希不链 → ``gate_refused_admission``。
    """

    from ..gates.review.browser import RawReviewDraftManifest, classify_browser_decision, normalize_raw_draft

    inbox = Path(run_root) / "checkpoints" / "admission" / "inbox"
    raw_path = inbox / "review-browser-decisions.json"
    if not raw_path.is_file():
        return {"status": "waiting_human", "detail": f"decision file not found: {raw_path}"}
    try:
        raw = RawReviewDraftManifest.model_validate(read_json(raw_path))
        classified = [classify_browser_decision(decision) for decision in raw.decisions]
        normalized = normalize_raw_draft(
            raw,
            dict(candidates_by_id),
            expected_queue_sha256=expected_queue_sha256,
            decision_builder=decision_builder,
        )
    except Exception as error:
        return {
            "status": "refused",
            "stop_reason": "gate_refused_admission",
            "detail": f"{type(error).__name__}: {error}",
        }
    released = tuple(
        decision for decision in normalized if getattr(decision, "disposition", None) == "reviewed_candidate"
    )
    audit = {
        "decision_count": len(normalized),
        "classified": [{"disposition": disposition, "reason": reason} for disposition, reason in classified],
        "released_count": len(released),
        "ingested_at_utc": _utc_now_iso(),
    }
    atomic_write_json(Path(run_root) / "checkpoints" / "admission" / "ingest-audit.json", audit)
    return {"status": "ingested", "released_count": len(released), "released": released, "audit": audit}


def emit_image_review_sheet(*, run_root: Path, step4_outcome: Mapping[str, Any], storage: Path) -> Path:
    """检查点②评审底稿（G-B3）：逐图字段对齐 16 图人工评审底稿实践。"""

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for card in step4_outcome.get("cards", ()):  # 仅登记成功出图卡；失败卡留在 step4 报告
        if card.get("outcome") != "success":
            continue
        rows.append(
            {
                "card_index": card["card_index"],
                "source_card_id": card["source_card_id"],
                "render_presentation": card["render_presentation"],
                "image_path": (Path(storage) / str(card.get("image_path"))).as_posix() if card.get("image_path") else None,
                "image_sha256": card.get("image_sha256"),
                "disposition": None,
                "reviewer": None,
                "basis": None,
            }
        )
    sheet_path = checkpoint_dir / "image_review_sheet.json"
    atomic_write_json(sheet_path, {"schema": "image-review-sheet/1.0", "cards": rows})
    return sheet_path


def ingest_image_review_sheet(*, run_root: Path) -> dict[str, Any]:
    """检查点②摄取（G-B2）：人工回填 disposition 后构建 v2 批量授权清单。"""

    from ..gates.review.image_admission_batch import build_batch_image_authorization

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    sheet_path = checkpoint_dir / "image_review_sheet.json"
    if not sheet_path.is_file():
        return {"status": "waiting_human", "detail": f"image review sheet not found: {sheet_path}"}
    sheet = read_json(sheet_path)
    cards = sheet.get("cards", [])
    undecided = [card["card_index"] for card in cards if card.get("disposition") is None]
    if undecided:
        return {"status": "waiting_human", "detail": f"cards without disposition: {undecided}"}
    try:
        manifest = build_batch_image_authorization(cards)
    except Exception as error:
        return {"status": "refused", "stop_reason": "gate_refused_admission", "detail": f"{type(error).__name__}: {error}"}
    atomic_write_json(checkpoint_dir / "image-authorization-manifest.json", manifest.model_dump(mode="json"))
    approved = sum(1 for card in cards if card.get("disposition") == "approved")
    return {"status": "ingested", "approved_count": approved, "decision_count": len(cards)}


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
            calls_max = step4_card_count * 2 + 1
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
            caps[stage] = max(1, step4_card_count * 2 + 1)
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


# ---------------------------------------------------------------------------
# 总编排（§10.1 orchestrate：DAG 排序 → 逐 stage → 检查点暂停/续跑 → 报告物化）
# ---------------------------------------------------------------------------

_STATUS_TO_DETAIL = {
    "completed": "completed",
    "completed_with_failures": "completed with sample-level failures (resend rounds exhausted)",
}


def orchestrate(config: LiveRunConfig, providers: Mapping[str, Any]) -> tuple[LiveRunReport, int]:
    """总编排。退出码语义（§2）：0=全部完成；2=正常暂停在人工检查点；1=失败。

    ``providers`` 注入面（CLI 装配；离线测试注入伪件）：
    sample_ids / pricing / preflight_report / context_builder / transport /
    stage_executor / step2_probe / gate_evaluator / step1_runner / facts_runner /
    admission_queue_builder / workbench_renderer / candidates_by_id /
    decision_builder / chat_strict / step3_probe / step3_card_count /
    prompt_builder / image_caller / models_probe / drift_classifier /
    step4_card_count / image_model。
    """

    paths = ExamPaths.resolve(config.repo_root, config.exam_id)
    run_id = config.resume_run_id or new_run_id(config.run_label)
    run_root = paths.live_runs_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    state = RunState.load_or_create(run_root / "run_state.json", run_id=run_id, exam_id=config.exam_id, stages=config.stages)

    pricing: Mapping[str, float] = providers.get("pricing") or {}
    sample_ids: tuple[str, ...] = tuple(providers.get("sample_ids") or ())
    if config.only_samples:
        sample_ids = tuple(s for s in sample_ids if any(token in s for token in config.only_samples))
    step3_cards = int(providers.get("step3_card_count") or 0)
    step4_cards = int(providers.get("step4_card_count") or 0)
    caps = derive_stage_call_caps(
        stages=config.stages,
        sample_count=max(len(sample_ids), 1),
        step3_card_count=max(step3_cards, 1),
        step4_card_count=max(step4_cards, 1),
        max_attempts=config.max_attempts,
    )
    stage_reports: list[LiveStageReport] = []
    extras: dict[str, Any] = {"run_root": str(run_root), "stages_requested": list(config.stages)}
    usd_cap = config.budget_usd if config.budget_usd is not None else 1.0
    budget = LiveRunBudget(run_root / "budget.json", usd_cap=usd_cap, stage_call_caps=caps)

    def finish(stop_reason: str, exit_code: int) -> tuple[LiveRunReport, int]:
        report = build_live_run_report(
            run_id=run_id,
            exam_id=config.exam_id,
            mode=config.mode,
            stages=stage_reports or [build_stage_report("step1", "pending", "no stage executed")],
            stop_reason=stop_reason,
            network_calls=budget.total_calls,
            usd_estimate=budget.spent_usd,
        )
        extras["budget"] = budget.snapshot()
        materialize_live_run(run_root=run_root, report=report, extras=extras)
        return report, exit_code

    # 前置产物检查（§9 missing_upstream_artifact：指名缺件与应跑 stage）。
    for stage in config.stages:
        missing = paths.missing_for_stage(stage, planned_stages=config.stages)
        if missing:
            stage_reports.append(build_stage_report(stage, "failed", f"missing upstream artifacts: {'; '.join(missing)}"))
            state.set_stage(stage, "failed", missing=missing)
            return finish("missing_upstream_artifact", 1)

    # dry-run 预算预演（§6-4）。
    if config.mode == "dry_run":
        extras["budget_rehearsal"] = budget_rehearsal_estimate(
            stages=config.stages,
            sample_count=len(sample_ids),
            step3_card_count=step3_cards,
            step4_card_count=step4_cards,
            pricing=pricing,
            max_attempts=config.max_attempts,
        )

    # 开跑预检（§8.2；live 且未显式跳过；预检自身计入账本）。
    if config.mode == "live" and not config.skip_preflight:
        preflight_report = providers.get("preflight_report")
        if preflight_report is None:
            return finish("preflight_step2_unmet" if "step2a" in config.stages else "preflight_step3_unmet", 1)
        extras["preflight"] = {
            "report_path": preflight_report.get("report_path"),
            "network_calls": preflight_report.get("network_calls", 0),
        }
        calls = int(preflight_report.get("network_calls", 0) or 0)
        if calls:
            budget.charge("preflight", calls=calls, usd_estimate=calls * pricing.get("preflight_call", 0.0))
        verdict = evaluate_preflight_gate(preflight_report, config.stages)
        extras["preflight_verdict"] = verdict
        if not verdict["allowed"]:
            return finish(str(verdict["stop_reason"]), 1)

    failed = False
    for stage in config.stages:
        if failed:
            stage_reports.append(build_stage_report(stage, "skipped", "skipped after upstream failure"))
            state.set_stage(stage, "skipped")
            continue
        if state.stage_status(stage) == "completed":
            stage_reports.append(build_stage_report(stage, "completed", "already completed in this run (resume skip)"))
            continue
        state.set_stage(stage, "running")

        if stage == "step1":
            runner = providers.get("step1_runner")
            if runner is None:
                stage_reports.append(build_stage_report(stage, "failed", "no step1 runner injected"))
                state.set_stage(stage, "failed")
                failed = True
                continue
            result = dict(runner())
            status = "completed" if result.get("ok", True) else "failed"
            stage_reports.append(build_stage_report(stage, status, str(result.get("detail", "golden evidence run"))))
            state.set_stage(stage, status)
            failed = failed or status == "failed"
            continue

        if stage in {"step2a", "step2b"}:
            if stage == "step2a":
                facts_runner = providers.get("facts_runner")
                if facts_runner is not None:
                    summary = dict(facts_runner())
                    extras["facts_base"] = {
                        "quality_gates_passed": bool(summary.get("quality_gates_passed")),
                        "machine_assertions_passed": bool(summary.get("machine_assertions_passed")),
                    }
                    if not (summary.get("quality_gates_passed") and summary.get("machine_assertions_passed")):
                        stage_reports.append(build_stage_report(stage, "failed", "facts base quality gates failed (step2a precondition)"))
                        state.set_stage(stage, "failed")
                        failed = True
                        continue
            if config.mode == "dry_run":
                stage_reports.append(
                    build_stage_report(stage, "completed", f"dry-run planned {len(sample_ids)} samples", samples_total=len(sample_ids))
                )
                state.set_stage(stage, "completed")
                continue
            resend = ResendPlan(base_reason=config.base_reason(stage), max_attempts=config.max_attempts)
            outcome = execute_stage_samples(
                stage=stage,  # type: ignore[arg-type]
                sample_ids=sample_ids,
                context_builder=providers["context_builder"],
                transport=providers.get("transport"),
                storage=run_root / stage,
                budget=budget,
                resend=resend,
                stage_executor=providers.get("stage_executor"),
                probe=providers.get("step2_probe"),
                gate_evaluator=providers.get("gate_evaluator"),
                price_per_call=pricing.get(f"{stage}_call", 0.0),
                probe_price=pricing.get("probe", 0.0),
            )
            extras[stage] = outcome
            if outcome["status"] == "failed":
                stage_reports.append(build_stage_report(stage, "failed", str(outcome.get("stop_reason", "stage failed"))))
                state.set_stage(stage, "failed")
                failed = True
                continue
            succeeded = sum(1 for item in outcome["samples"] if item["outcome"] == "success")
            stage_reports.append(
                build_stage_report(
                    stage,
                    outcome["status"],
                    _STATUS_TO_DETAIL[outcome["status"]],
                    samples_total=len(outcome["samples"]),
                    samples_succeeded=succeeded,
                    samples_failed=len(outcome["samples"]) - succeeded,
                )
            )
            state.set_stage(stage, outcome["status"])
            if outcome["status"] == "completed_with_failures":
                failed = True  # 重发轮数耗尽仍失败 → 下游 skipped（§5.2）
                extras["stop_reason_hint"] = "completed_with_failures"
            # ◆检查点①：step2b 完成后产出物料并暂停（§7.1）。
            if stage == "step2b" and not failed and config.mode == "live":
                ticket = state.checkpoint("admission")
                if ticket.get("status") != "ingested":
                    queue_builder = providers.get("admission_queue_builder")
                    if queue_builder is not None:
                        ticket_info = build_admission_checkpoint(
                            run_root=run_root,
                            queue_items=queue_builder(),
                            workbench_renderer=providers.get("workbench_renderer"),
                        )
                        state.set_checkpoint("admission", "waiting_human", **{k: v for k, v in ticket_info.items() if k != "status"})
                        stage_reports.append(build_stage_report("admission_checkpoint", "waiting_human", "materials emitted; waiting for human admission decisions"))
                        return finish("waiting_human_admission", 2)
            continue

        if stage == "step3":
            # 检查点①门禁：同一 run 内跑过 step2b 才要求准入放行（bypass 语料自带 reviewed 身份）。
            if "step2b" in config.stages and config.mode == "live":
                ticket = state.checkpoint("admission")
                if ticket.get("status") == "waiting_human":
                    ingest = ingest_admission_decisions(
                        run_root=run_root,
                        candidates_by_id=providers.get("candidates_by_id") or {},
                        expected_queue_sha256=str(ticket.get("queue_sha256", "")),
                        decision_builder=providers.get("decision_builder") or (lambda raw, candidate: raw),
                    )
                    if ingest["status"] == "waiting_human":
                        stage_reports.append(build_stage_report("admission_checkpoint", "waiting_human", str(ingest["detail"])))
                        return finish("waiting_human_admission", 2)
                    if ingest["status"] == "refused":
                        stage_reports.append(build_stage_report("admission_checkpoint", "failed", str(ingest["detail"])))
                        state.set_checkpoint("admission", "refused")
                        return finish("gate_refused_admission", 1)
                    state.set_checkpoint("admission", "ingested", released_count=ingest.get("released_count", 0))
                    stage_reports.append(build_stage_report("admission_checkpoint", "completed", f"admission ingested; released={ingest.get('released_count', 0)}"))
            if config.step3_mode == "release-bypass":
                info = load_step3_release_bypass(release_fixture_path=config.step3_release_fixture)  # type: ignore[arg-type]
                extras["step3"] = info
                stage_reports.append(
                    build_stage_report(stage, "completed", f"release-bypass: {info['card_count']} reviewed release cards validated")
                )
                state.set_stage(stage, "completed")
                continue
            compilation = load_typed_fixture_compilation(config.step3_typed_fixture)  # type: ignore[arg-type]
            if config.mode == "dry_run":
                count = len(compilation.contracts)
                stage_reports.append(build_stage_report(stage, "completed", f"dry-run compiled {count} contracts (echo lane)", samples_total=count))
                state.set_stage(stage, "completed")
                continue
            outcome = run_step3_stage(
                compilation=compilation,
                contract_version=config.step3_contract,
                chat_strict=providers["chat_strict"],
                storage=run_root / "step3",
                budget=budget,
                base_reason=config.base_reason("step3"),
                price_per_call=pricing.get("step3_card_call", 0.0),
                probe_price=pricing.get("probe", 0.0),
                probe=providers.get("step3_probe"),
            )
            extras["step3"] = {key: value for key, value in outcome.items() if key != "cards"}
            extras["step3_cards"] = outcome.get("cards", [])
            if outcome["status"] == "failed":
                stage_reports.append(build_stage_report(stage, "failed", str(outcome.get("stop_reason", "step3 failed"))))
                state.set_stage(stage, "failed")
                failed = True
                continue
            stage_reports.append(
                build_stage_report(
                    stage,
                    outcome["status"],
                    f"echo {outcome.get('passed_cards', 0)}/{outcome.get('card_count', 0)} cards passed",
                    samples_total=int(outcome.get("card_count", 0)),
                    samples_succeeded=int(outcome.get("passed_cards", 0)),
                    samples_failed=int(outcome.get("card_count", 0)) - int(outcome.get("passed_cards", 0)),
                )
            )
            state.set_stage(stage, outcome["status"])
            if outcome["status"] == "completed_with_failures":
                failed = True
            continue

        if stage == "step4":
            outcome = run_step4_stage(
                release_fixture_path=config.step3_release_fixture or Path(str(providers.get("step4_release_fixture", ""))),
                handoff_path=config.step4_handoff or Path(str(providers.get("step4_handoff", ""))),
                prompt_builder=providers["prompt_builder"],
                image_caller=providers.get("image_caller") or (lambda payload: (0, b"")),
                models_probe=providers.get("models_probe") if config.mode == "live" else None,
                image_model=str(providers.get("image_model", "")),
                storage=run_root / "step4",
                budget=budget,
                drift_classifier=providers.get("drift_classifier"),
                price_per_call=pricing.get("step4_image_call", 0.0),
                probe_price=pricing.get("probe", 0.0),
                dry_run=config.mode == "dry_run",
            )
            extras["step4"] = {key: value for key, value in outcome.items() if key != "cards"}
            extras["step4_cards"] = outcome.get("cards", [])
            if outcome.get("status") == "failed":
                stage_reports.append(build_stage_report(stage, "failed", str(outcome.get("stop_reason", "step4 failed"))))
                state.set_stage(stage, "failed")
                failed = True
                continue
            stage_reports.append(
                build_stage_report(
                    stage,
                    str(outcome["status"]),
                    str(outcome.get("detail", f"images {outcome.get('succeeded_cards', 0)}/{len(outcome.get('cards', []))}")),
                    samples_total=len(outcome.get("cards", [])) or int(outcome.get("card_count", 0)),
                    samples_succeeded=int(outcome.get("succeeded_cards", 0)),
                )
            )
            state.set_stage(stage, str(outcome["status"]))
            if outcome["status"] == "completed_with_failures":
                failed = True
                continue
            # ◆检查点②：出图完成产出底稿并暂停（§7.2）。
            if config.mode == "live":
                ticket = state.checkpoint("image_review")
                if ticket.get("status") == "ingested":
                    continue
                sheet_ingest = ingest_image_review_sheet(run_root=run_root)
                if sheet_ingest["status"] == "ingested":
                    state.set_checkpoint("image_review", "ingested", approved_count=sheet_ingest.get("approved_count", 0))
                    stage_reports.append(build_stage_report("image_review_checkpoint", "completed", f"image review ingested; approved={sheet_ingest.get('approved_count', 0)}"))
                    continue
                if sheet_ingest["status"] == "refused":
                    stage_reports.append(build_stage_report("image_review_checkpoint", "failed", str(sheet_ingest["detail"])))
                    return finish("gate_refused_admission", 1)
                sheet_path = emit_image_review_sheet(run_root=run_root, step4_outcome=outcome, storage=run_root / "step4")
                state.set_checkpoint("image_review", "waiting_human", sheet_path=str(sheet_path))
                stage_reports.append(build_stage_report("image_review_checkpoint", "waiting_human", "image review sheet emitted; waiting for human dispositions"))
                return finish("waiting_human_image_review", 2)
            continue

    if failed:
        hint = extras.get("stop_reason_hint", "completed_with_failures")
        return finish(str(hint), 1)
    return finish("completed", 0)
