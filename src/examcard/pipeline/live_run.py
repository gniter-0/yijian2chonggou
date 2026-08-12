"""Live 编排辅助库（P3 T2 先落地基，T4b 通用全链驱动在此扩展）。

本轨落两件（任务书 `docs/prework/P3_优化施工方案_T1-T5_20260812.md` §2 第 3 条）：

- ``ResendPlan``：失败样本重发的身份管理。``manual_reissue_reason`` 参与 Step2
  请求 identity 摘要（`contracts/request_identity.py`），因此「重发」= 换新 reason
  后缀的**新身份**；本类把 pilot 实测口径（首发用 base reason 原文、重发 ``-rN``
  后缀、失败身份禁复用、轮数上限）抽为可复用对象。
- ``load_run_env()`` 与双通道取值函数：运行配置**默认读新仓根 `.env`**（gitignored；
  自 yijian2/.env 迁值，从此原仓零接触），支持显式传路径；文本/图像双通道取值，
  ``IMAGE_*`` 显式键优先、回落通用键。密钥值仅驻内存，调用方不得打印/落盘/入报告。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

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
