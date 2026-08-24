"""live 驱动的常量底座与时间/run_id 助手（拆分层 0，不依赖任何兄弟）。

``_REPO_ROOT`` 取 ``parents[3]``，锚的是 src/examcard/<pkg>/<mod>.py 这一深度：本模块与
拆分前的 live_run.py 同在 pipeline/ 下，深度未变，故该字面量不许跟着「顺手」改——
tests/test_pipeline_live_run_imports.py 直接断言它等于仓根。``Final`` 那两个版本号
刻意不写显式注解，好让 mypy 保留字面量类型，常量才能当 Literal 字段的默认值。
"""
from __future__ import annotations

import random
import re
import string
from datetime import UTC, datetime
from pathlib import Path
from typing import Final


_REPO_ROOT = Path(__file__).resolve().parents[3]


# 驱动侧统一执行 ≤96 的 reason 上限（identity 模型本身允许 ≤160；pilot/T4a 口径取 96）。
_REASON_MAX_LENGTH = 96
_REASON_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")


# ===========================================================================
# T4b：通用全链 live 驱动编排（设计 docs/design/live_pipeline_driver_design.md）
#
# 分层纪律（设计 §1/§12）：本模块承担全部可测编排逻辑；CLI（migration/
# run_exam_pipeline.py）只做参数装配与注入。所有外呼客户端/预检执行器/
# 决策构建器一律注入（provider-injection，与 pipeline/run.py 同哲学），
# 故全部函数可用伪 transport 离线测试；src 决不 import migration。
# ===========================================================================

LIVE_STAGES: tuple[str, ...] = ("step1", "step2a", "step2b", "step3", "step4")
# Final（不带显式注解）让 mypy 保留字面量类型，常量才能当 Literal 字段的默认值；
# 裸赋值会推成 str，赋给 Literal[...] 即报 assignment。
LIVE_STAGE_REPORT_VERSION: Final = "examcard_live_stage_report_v1"
LIVE_RUN_REPORT_VERSION: Final = "examcard_live_run_report_v1"
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
