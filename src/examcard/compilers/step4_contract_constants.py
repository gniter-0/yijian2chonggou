"""Step4 契约编译器的版本串、类型标签与判定常量（拆分层 0）。

原 step4_contract.py 顶部的模块级常量原样搬来：13 个版本串、画布尺寸、
6 个 sha 类型标签（_SHA/_FACT/_CARD/_REQUEST/_MODEL_INPUT/_JOB）、fake 文件名前缀、
呈现方式与来源种类、禁用键/文本、marker 与 token 正则、两个冻结计数。
判定口径、字面量、版本串分毫不动，只换落脚位置（字节权威不受影响）。
"""
from __future__ import annotations

import re
from typing import Final


PHASE: Final = "2R.4"
SCHEMA_VERSION: Final = "phase2r4_step4_generic_source_derived_contract_compiler_v1"
CONTRACT_BUNDLE_VERSION: Final = "step4_generic_contract_bundle_v1"
MODEL_INPUT_VERSION: Final = "step4_generic_model_input_v1"
PROMPT_VERSION: Final = "step4_generic_prompt_contract_v1"
REQUEST_VERSION: Final = "step4_generic_offline_request_v1"
FAKE_ARTIFACT_VERSION: Final = "step4_generic_fake_artifact_manifest_v1"
CACHE_VERSION: Final = "step4_generic_cache_v1"
JOURNAL_VERSION: Final = "step4_generic_journal_v1"
BATCH_VERSION: Final = "step4_generic_batch_manifest_v1"
GATE_VERSION: Final = "step4_generic_batch_offline_gate_v1"
REGRESSION_VERSION: Final = "step4_generic_regression_result_v1"
REPORT_VERSION: Final = "phase2r4_step4_generic_report_v1"


OUTPUT_WIDTH: Final = 1536
OUTPUT_HEIGHT: Final = 1024
_SHA = r"^[0-9a-f]{64}$"
_FACT = r"^fact:[0-9a-f]{24}$"
_CARD = r"^v25-release-card:[0-9a-f]{24}$"
_REQUEST = r"^step4-generic-offline-request:[0-9a-f]{64}$"
_MODEL_INPUT = r"^step4-generic-model-input:[0-9a-f]{24}$"
_JOB = r"^step4-generic-job:[0-9a-f]{24}$"
# 旧仓 pattern 钉死 gonglu 前缀；按设计 §4.3 做哈希安全放宽（pattern 不参与序列化，
# 既有 gonglu 文件名仍匹配），配合 filename_prefix 参数支持新科目。
_FAKE_FILENAME = r"^[a-z0-9][a-z0-9-]*step4-card-[0-9]{2}-[0-9a-f]{8}\.png$"
DEFAULT_FAKE_FILENAME_PREFIX = "gonglu-step4-card-"


SOURCE_PRESENTATIONS = ("table", "diagram", "ordered_list", "constraint", "threshold", "formula")
RENDER_PRESENTATIONS = ("table", "diagram", "ordered_list", "constraint", "formula")
SOURCE_KINDS = (
    "reviewed_fact_assertion",
    "reviewed_semantic_node_label",
    "reviewed_remediation_assertion",
    "reviewed_remediation_ordered_list",
    "reviewed_remediation_table_grid",
    "inherited_reviewed_fact_assertion",
)
FORBIDDEN_MODEL_KEYS = frozenset(
    {
        "golden_title",
        "learning_golden",
        "canonical_title",
        "card_key",
        "review_state",
        "review_basis",
        "reviewer",
        "reviewed_at_utc",
        "manual_decision",
        "expected_texts",
        "image_prompt",
        "layout_description",
        "unknown_field",
    }
)
FORBIDDEN_TEXT = ("watermark", "水印", "evidence-only", "evidence_only", "learning golden")
MARKERS = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")
TOKEN_PATTERN = re.compile(r"[A-Za-z]+[A-Za-z0-9%]*|[0-9]+(?:\.[0-9]+)?(?:[A-Za-z%]+)?|[=+\-*/×÷Σ∑≤≥<>~～^]+")


# 冻结表本体（FROZEN_R33_R319_HASHES）与 audit_frozen_history 留旧仓；此二整数为
# FreezeAudit/Report 数据模型的 Literal 载荷（装载校验历史产物所需）。
FROZEN_ARTIFACT_COUNT: Final = 501
HISTORICAL_IDENTITY_COUNT: Final = 19
