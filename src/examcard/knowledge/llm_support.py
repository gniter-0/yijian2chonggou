"""Step 2 执行链的通用小工具与脱敏落纸口（拆分层 0）。

``_sanitized_error_message`` 是不变式 #12 在「会真联网」这条通道上的唯一入口：
错误原文落纸前必过 providers 的脱敏器，``str(exc)[:N]`` 一律禁止。该禁令由
tests/test_llm_error_redaction.py 按 llm_* 家族逐文件 AST 封印，本模块只是它的落点。
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..providers.base import sanitized_error_summary


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sanitized_error_message(error: BaseException) -> str:
    """错误原文落纸前必过脱敏器（providers 的不变式 #12），本模块是活的外呼路径。

    为什么必须在这里接线，而不是照抄 ``str(exc)[:500]``：
      1) 供应商/中继的错误正文会回显凭据——``Incorrect API key provided: sk-...``
         与 ``Authorization: Bearer ...`` 都是常见形态，而 openai SDK 的
         ``APIStatusError.__str__`` 直接带上响应体；
      2) 本模块的 ``_append`` 会把 ``error_message`` 逐行写进 step2 journal 文件，
         回显一旦被原样收录，凭据就落到磁盘上，并随 journal 一起被审阅、归档；
      3) ``providers/base.py`` 已为同一条口径配了强制校验器（``sanitized_detail``
         与脱敏结果不一致即抛 ``probe detail is not sanitized``），而真外呼的传输层
         没有接线——等于规则只在离线录制通道成立，在会真联网的通道失效。
    截断口径一并交给脱敏器（300 字），不另立长度，避免同一条不变式出现两套尺子。
    """
    return sanitized_error_summary(error)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
