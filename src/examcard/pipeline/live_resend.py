"""失败样本重发的身份管理（拆分层 1）。

``manual_reissue_reason`` 参与 Step2 请求 identity 摘要，所以「重发」不是重试同一身份，
而是换 ``-rN`` 后缀的**新身份**：首发用 base reason 原文、失败身份禁复用、轮数用尽即
``ResendRoundsExhausted``。
"""
from __future__ import annotations

from .live_support import _REASON_MAX_LENGTH, _REASON_PATTERN


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
