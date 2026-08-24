"""step2 journal 的事件模型与逐行追加（拆分层 1）。

journal 是磁盘上逐行追加的文件，会随审阅与归档一起被带走，因此 ``error_message``
只许接 None 或脱敏结果（见 llm_support）。事件 id 是请求 id + 结局 + 序号的内容寻址。
"""
from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from pydantic import Field, model_validator

from ..materials.schema import StrictModel

from .contracts import Step2RequestIdentity

from .llm_support import _json_dumps, _sha256


class Step2JournalOutcome(StrEnum):
    REQUEST_STARTED = "request_started"
    CACHE_HIT = "cache_hit"
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"
    RECONCILED_TERMINAL = "reconciled_terminal"


class Step2JournalEvent(StrictModel):
    event_id: str = Field(pattern=r"^step2-event:[0-9a-f]{64}$")
    recorded_at_utc: str = Field(pattern=r"^.+Z$")
    outcome: Step2JournalOutcome
    request: Step2RequestIdentity
    provider_request_id: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    error_type: str | None = None
    error_message: str | None = None
    reconciled_terminal_outcome: Step2JournalOutcome | None = None

    @model_validator(mode="after")
    def validate_reconciliation(self) -> "Step2JournalEvent":
        terminal_outcomes = {
            Step2JournalOutcome.SUCCESS,
            Step2JournalOutcome.FAILED,
            Step2JournalOutcome.UNKNOWN_OUTCOME,
        }
        if self.outcome == Step2JournalOutcome.RECONCILED_TERMINAL:
            if self.reconciled_terminal_outcome not in terminal_outcomes:
                raise ValueError("reconciled terminal journal events must declare the recovered terminal outcome")
        elif self.reconciled_terminal_outcome is not None:
            raise ValueError("only reconciled terminal journal events may declare a recovered outcome")
        return self


def _make_event_id(*, request_id: str, outcome: Step2JournalOutcome, ordinal: int) -> str:
    return f"step2-event:{_sha256(_json_dumps({'request_id': request_id, 'outcome': outcome.value, 'ordinal': ordinal}))}"


def _append_event(path: Path, event: Step2JournalEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(event.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
