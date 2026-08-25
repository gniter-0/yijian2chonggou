"""Source-derived generic V25/V25B offline contracts.

The module deliberately keeps Learning Golden data outside the Step 2 model
payloads. Golden review state is consumed only by the offline preflight layer.

cm28 拆分后本文件只做再导出：实现搬到同级 v25_* 兄弟模块（15 个，层序见
docs/prework/v25_generic_contracts_split_blueprint.md 第 3 节），导入路径
examcard.knowledge.contracts.v25_generic_contracts 一字不改。__all__ 连私有名一并列出——
拆分前的 79 个顶层名要 100% 兜住，门面不是趁搬家重新定义什么算公开的地方。
phase2r14_v25_live 里那处 __import__(..., fromlist=["build_v25b_source_fake_response"])
取到的正是本门面模块对象，属性由下面的再导出兜住（封印见 DYNAMIC_SIBLING_IMPORTS）。
"""
from __future__ import annotations

from .v25_versions import (
    V25_STEP2A_REQUEST_VERSION,
    V25_STEP2A_MODEL_INPUT_VERSION,
    V25_STEP2A_PROMPT_VERSION,
    V25_STEP2A_JOURNAL_VERSION,
    V25_ATOMIC_SOURCE_STEP2A_REQUEST_VERSION,
    V25_ATOMIC_SOURCE_STEP2A_MODEL_INPUT_VERSION,
    V25_ATOMIC_SOURCE_STEP2A_PROMPT_VERSION,
    V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_JOURNAL_VERSION,
    V25_LIST_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_LIST_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_LIST_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_LIST_CANONICAL_STEP2A_JOURNAL_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_JOURNAL_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_JOURNAL_VERSION,
    V25B_STEP2B_REQUEST_VERSION,
    V25B_STEP2B_MODEL_INPUT_VERSION,
    V25B_STEP2B_PROMPT_VERSION,
    V25B_STEP2B_JOURNAL_VERSION,
)
from .v25_support import _SHA256_PATTERN, _OFFLINE_RELAY_BASE_URL, _v25_id, _label_from_source
from .v25_enums import (
    V25Step2AContractProfile,
    V25AtomicConditionOperator,
    V25FactReviewState,
    V25BGroupingKind,
    V25BSourceFamilyKind,
    _SOURCE_FAMILY_PRESENTATION,
)
from .v25_step2a_obligations import (
    V25AnchorContext,
    V25CoverageObligationContext,
    V25AtomicNumericExpression,
    V25AtomicSourceObligation,
    normalize_v25_source_assertion,
    V25TextSourceObligation,
    V25ListSourceObligation,
    V25ReviewedContinuationBinding,
)
from .v25_step2a_prompt import (
    _section_anchor_map,
    _table_by_id,
    _v25_contexts,
    v25_step2a_prompt_template,
    v25_step2a_request_payload,
)
from .v25_step2a_request import (
    V25Step2AModelFactProposal,
    V25Step2AResultPayload,
    V25Step2ARequest,
    V25Step2AGate,
    V25JournalRecord,
)
from .v25_step2a_build import build_v25_step2a_request
from .v25_step2a_assess import assess_v25_step2a_response
from .v25_step2a_offline import build_v25_source_fake_response, make_v25_offline_journal
from .v25_step2b_diagram import V25SemanticDiagramNode, V25SemanticDiagramEdge, V25SemanticDiagramBridge
from .v25_step2b_prompt import (
    _presentation_for_fact_type,
    v25b_step2b_prompt_template,
    v25b_step2b_request_payload,
)
from .v25_step2b_models import (
    V25Step2BFactSummary,
    V25BSourceFamilyBinding,
    V25BGroupingObligation,
    V25BModelCardProposal,
    V25BStep2BResultPayload,
    V25BStep2BRequest,
    V25BGroupingGate,
    V25BBlockedPreflight,
)
from .v25_step2b_build import derive_v25b_grouping_obligations, build_v25b_step2b_request
from .v25_step2b_assess import assess_v25b_step2b_response
from .v25_step2b_offline import build_v25b_source_fake_response, make_v25b_offline_journal


__all__ = [
    "V25AnchorContext",
    "V25AtomicConditionOperator",
    "V25AtomicNumericExpression",
    "V25AtomicSourceObligation",
    "V25BBlockedPreflight",
    "V25BGroupingGate",
    "V25BGroupingKind",
    "V25BGroupingObligation",
    "V25BModelCardProposal",
    "V25BSourceFamilyBinding",
    "V25BSourceFamilyKind",
    "V25BStep2BRequest",
    "V25BStep2BResultPayload",
    "V25B_STEP2B_JOURNAL_VERSION",
    "V25B_STEP2B_MODEL_INPUT_VERSION",
    "V25B_STEP2B_PROMPT_VERSION",
    "V25B_STEP2B_REQUEST_VERSION",
    "V25CoverageObligationContext",
    "V25FactReviewState",
    "V25JournalRecord",
    "V25ListSourceObligation",
    "V25ReviewedContinuationBinding",
    "V25SemanticDiagramBridge",
    "V25SemanticDiagramEdge",
    "V25SemanticDiagramNode",
    "V25Step2AContractProfile",
    "V25Step2AGate",
    "V25Step2AModelFactProposal",
    "V25Step2ARequest",
    "V25Step2AResultPayload",
    "V25Step2BFactSummary",
    "V25TextSourceObligation",
    "V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION",
    "V25_ATOMIC_SOURCE_STEP2A_MODEL_INPUT_VERSION",
    "V25_ATOMIC_SOURCE_STEP2A_PROMPT_VERSION",
    "V25_ATOMIC_SOURCE_STEP2A_REQUEST_VERSION",
    "V25_COST_FLOW_CANONICAL_STEP2A_JOURNAL_VERSION",
    "V25_COST_FLOW_CANONICAL_STEP2A_MODEL_INPUT_VERSION",
    "V25_COST_FLOW_CANONICAL_STEP2A_PROMPT_VERSION",
    "V25_COST_FLOW_CANONICAL_STEP2A_REQUEST_VERSION",
    "V25_DIAGRAM_CANONICAL_STEP2A_JOURNAL_VERSION",
    "V25_DIAGRAM_CANONICAL_STEP2A_MODEL_INPUT_VERSION",
    "V25_DIAGRAM_CANONICAL_STEP2A_PROMPT_VERSION",
    "V25_DIAGRAM_CANONICAL_STEP2A_REQUEST_VERSION",
    "V25_LIST_CANONICAL_STEP2A_JOURNAL_VERSION",
    "V25_LIST_CANONICAL_STEP2A_MODEL_INPUT_VERSION",
    "V25_LIST_CANONICAL_STEP2A_PROMPT_VERSION",
    "V25_LIST_CANONICAL_STEP2A_REQUEST_VERSION",
    "V25_STANDARD_CANONICAL_STEP2A_JOURNAL_VERSION",
    "V25_STANDARD_CANONICAL_STEP2A_MODEL_INPUT_VERSION",
    "V25_STANDARD_CANONICAL_STEP2A_PROMPT_VERSION",
    "V25_STANDARD_CANONICAL_STEP2A_REQUEST_VERSION",
    "V25_STEP2A_JOURNAL_VERSION",
    "V25_STEP2A_MODEL_INPUT_VERSION",
    "V25_STEP2A_PROMPT_VERSION",
    "V25_STEP2A_REQUEST_VERSION",
    "_OFFLINE_RELAY_BASE_URL",
    "_SHA256_PATTERN",
    "_SOURCE_FAMILY_PRESENTATION",
    "_label_from_source",
    "_presentation_for_fact_type",
    "_section_anchor_map",
    "_table_by_id",
    "_v25_contexts",
    "_v25_id",
    "assess_v25_step2a_response",
    "assess_v25b_step2b_response",
    "build_v25_source_fake_response",
    "build_v25_step2a_request",
    "build_v25b_source_fake_response",
    "build_v25b_step2b_request",
    "derive_v25b_grouping_obligations",
    "make_v25_offline_journal",
    "make_v25b_offline_journal",
    "normalize_v25_source_assertion",
    "v25_step2a_prompt_template",
    "v25_step2a_request_payload",
    "v25b_step2b_prompt_template",
    "v25b_step2b_request_payload",
]
