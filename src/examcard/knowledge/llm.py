"""OpenAI-compatible, audit-first execution for V2 Step 2A/2B planning.

This module is deliberately separate from V1 Step 2. It persists every V2
request/response under an independent root, never mutates Evidence or review
sidecars, and treats a sent request without a durable response as
``unknown_outcome`` instead of retrying it automatically.

cm25 拆分后本文件只做再导出：实现搬到同级 llm_* 兄弟模块（层序见
docs/prework/llm_split_blueprint.md 第 3 节），导入路径 examcard.knowledge.llm
一字不改。__all__ 连私有助手一并列出，因为随迁测试确实从本模块 import
``_sanitized_error_message``，门面的职责是把原有名字面 100% 兜住，
不是重新定义什么算公开。
"""
from __future__ import annotations

from .llm_support import (
    _SHA256_PATTERN,
    _utc_now,
    _sha256,
    _sanitized_error_message,
    _json_dumps,
    _atomic_write_json,
)
from .llm_journal import Step2JournalOutcome, Step2JournalEvent, _make_event_id, _append_event
from .llm_review import SemanticReviewState, Step2TeachingReview
from .llm_forensics import Step2SchemaErrorLocation, Step2RejectedResponseForensics
from .llm_transport import (
    _ChatResponseFormatParam,
    TransportCompletion,
    Step2Transport,
    OpenAICompatibleStep2Transport,
    CallableStep2Transport,
)
from .llm_records import Step2AExecutionRecord, Step2BExecutionRecord
from .llm_adapter import Step2OpenAIAdapter


__all__ = [
    "CallableStep2Transport",
    "OpenAICompatibleStep2Transport",
    "SemanticReviewState",
    "Step2AExecutionRecord",
    "Step2BExecutionRecord",
    "Step2JournalEvent",
    "Step2JournalOutcome",
    "Step2OpenAIAdapter",
    "Step2RejectedResponseForensics",
    "Step2SchemaErrorLocation",
    "Step2TeachingReview",
    "Step2Transport",
    "TransportCompletion",
    "_ChatResponseFormatParam",
    "_SHA256_PATTERN",
    "_append_event",
    "_atomic_write_json",
    "_json_dumps",
    "_make_event_id",
    "_sanitized_error_message",
    "_sha256",
    "_utc_now",
]
