"""事实链的词表与正则常量（拆分层 0）。

原 fact_engine.py 顶部的模块级常量原样搬来：数字与约束词、容器与视觉证据种类、
内嵌例题正则、中日韩字符正则、图示占位符。判定口径分毫不动，只换落脚位置。
"""
from __future__ import annotations

import re

from ..evidence.schema import EvidenceKind


_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[.．]\d+)?(?:\s*(?:%|％|mm|cm|m|MPa|kPa|h|小时|年|天|万元|亿元|倍))?(?![A-Za-z])"
)
_CONSTRAINT_TERMS = ("不得", "不应", "严禁", "除外", "至少", "不少于", "不超过")
_CONTAINER_KINDS = frozenset({EvidenceKind.FIGURE, EvidenceKind.TABLE})
_VISUAL_KINDS = frozenset({EvidenceKind.FIGURE, EvidenceKind.DIAGRAM_NODE, EvidenceKind.DIAGRAM_EDGE})
_EMBEDDED_EXAMPLE_RE = re.compile(r"(?:【?例题】?|例题[：:]|例如[：:]|【?答案】?|答案[：:]|答[：:])")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_DIAGRAM_PLACEHOLDER = "图示待人工结构化"
