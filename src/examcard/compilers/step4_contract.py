"""Generic Step 4 source-derived contract compiler (distilled from phase2r4).

复制改造自 yijian2 `scripts/knowledge_v2/phase2r4_step4_generic_contract_compiler.py`
（distill:compilers，设计依据 docs/design/p1_compilers_release_gate_design.md §4.3）。

- 输入契约（r2242 三型、Step4PilotHandoffRefresh）按 A 案自
  `knowledge/contracts/admission_fixtures.py` 向下 import（EXPECTED_* 计数常量随
  r2242 契约同处）。
- r4 法定哈希三件套 `_sha256/_json_bytes/_canonical_hash` 依铁律 bc-b8b9aca6 随
  模块逐字节原样（与 r9 血统、r8x 血统禁互换）。
- 参数化（设计 §4.3）：`derive_step4_bundle(fixture, handoff, *, ...)` 纯计算入口
  替代读盘 `_expected_bundle`；`assess_offline_gate` 的 freeze 结果与期望数量改为
  参数注入；fake artifact 文件名前缀参数化（默认保留 "gonglu-step4-card-" 以保
  哈希；模型 pattern 做哈希安全的最小放宽以接受新科目前缀）。
- 旧仓留守（不在本模块）：FROZEN_R33_R319_HASHES 冻结哈希表本体、
  `_historical_identities`/`audit_frozen_history`、`_read_fixture` 读盘断言、
  RUN_DIR 等 11 个输出路径与 `materialize_phase2r4`。本模块仅携带
  `FROZEN_ARTIFACT_COUNT/HISTORICAL_IDENTITY_COUNT` 两个整数（FreezeAudit/Report
  数据模型的 Literal 载荷，用于装载校验历史产物；冻结表校验本身留旧仓）。
- 回归矩阵对 16 卡 gonglu 试点 fixture 的卡位假设（cards[5]=table 等）保持原行为。

cm34 拆分后本文件只做再导出：实现搬到同级 step4_* 兄弟模块（层序见
docs/prework/step4_contract_split_blueprint.md 第 3 节），导入路径
examcard.compilers.step4_contract 一字不改。__all__ 连私有助手一并列出，
因为随迁测试确实从本模块 import 私有函数（_canonical_hash/_extract_tokens/
_no_forbidden_text/_walk_mappings），门面的职责是把原有名字面 100% 兜住。
"""
from __future__ import annotations

from .step4_contract_constants import (
    PHASE,
    SCHEMA_VERSION,
    CONTRACT_BUNDLE_VERSION,
    MODEL_INPUT_VERSION,
    PROMPT_VERSION,
    REQUEST_VERSION,
    FAKE_ARTIFACT_VERSION,
    CACHE_VERSION,
    JOURNAL_VERSION,
    BATCH_VERSION,
    GATE_VERSION,
    REGRESSION_VERSION,
    REPORT_VERSION,
    OUTPUT_WIDTH,
    OUTPUT_HEIGHT,
    _SHA,
    _FACT,
    _CARD,
    _REQUEST,
    _MODEL_INPUT,
    _JOB,
    _FAKE_FILENAME,
    DEFAULT_FAKE_FILENAME_PREFIX,
    SOURCE_PRESENTATIONS,
    RENDER_PRESENTATIONS,
    SOURCE_KINDS,
    FORBIDDEN_MODEL_KEYS,
    FORBIDDEN_TEXT,
    MARKERS,
    TOKEN_PATTERN,
    FROZEN_ARTIFACT_COUNT,
    HISTORICAL_IDENTITY_COUNT,
)
from .step4_contract_hashing import _sha256, _json_bytes, _canonical_hash
from .step4_contract_models_generic import (
    GenericVisibleText,
    GenericSemanticEdge,
    GenericTableCell,
    GenericTableContract,
    GenericOrderedListItem,
    GenericOrderedListContract,
    GenericTokenBinding,
    GenericGeometry,
    GenericCardContract,
)
from .step4_contract_models_phase import (
    Phase2R4FrozenArtifact,
    Phase2R4FreezeAudit,
    Phase2R4ContractBundle,
    Phase2R4ModelInput,
    Phase2R4PromptContract,
    Phase2R4Request,
    Phase2R4FakeArtifact,
    Phase2R4FakeArtifactManifest,
    Phase2R4CacheRecord,
    Phase2R4JournalRecord,
    Phase2R4BatchEntry,
    Phase2R4BatchManifest,
    Phase2R4OfflineGate,
    Phase2R4RegressionResult,
    Phase2R4Report,
)
from .step4_contract_derive import (
    _source_text_payload,
    _source_card_payload,
    _extract_tokens,
    _derive_visible_text,
    _derive_table,
    _derive_list,
    _derive_edge,
    _derive_card,
    derive_contracts,
    _derive_model_input,
    _derive_request,
    _derive_fake_artifact,
    _derive_fake_manifest,
    _derive_batch_manifest,
    derive_step4_bundle,
)
from .step4_contract_gate import (
    ExpectedCounts,
    _walk_mappings,
    _no_forbidden_model_keys,
    _no_forbidden_text,
    assess_offline_gate,
    build_regression_matrix,
)


__all__ = [
    "BATCH_VERSION",
    "CACHE_VERSION",
    "CONTRACT_BUNDLE_VERSION",
    "DEFAULT_FAKE_FILENAME_PREFIX",
    "ExpectedCounts",
    "FAKE_ARTIFACT_VERSION",
    "FORBIDDEN_MODEL_KEYS",
    "FORBIDDEN_TEXT",
    "FROZEN_ARTIFACT_COUNT",
    "GATE_VERSION",
    "GenericCardContract",
    "GenericGeometry",
    "GenericOrderedListContract",
    "GenericOrderedListItem",
    "GenericSemanticEdge",
    "GenericTableCell",
    "GenericTableContract",
    "GenericTokenBinding",
    "GenericVisibleText",
    "HISTORICAL_IDENTITY_COUNT",
    "JOURNAL_VERSION",
    "MARKERS",
    "MODEL_INPUT_VERSION",
    "OUTPUT_HEIGHT",
    "OUTPUT_WIDTH",
    "PHASE",
    "PROMPT_VERSION",
    "Phase2R4BatchEntry",
    "Phase2R4BatchManifest",
    "Phase2R4CacheRecord",
    "Phase2R4ContractBundle",
    "Phase2R4FakeArtifact",
    "Phase2R4FakeArtifactManifest",
    "Phase2R4FreezeAudit",
    "Phase2R4FrozenArtifact",
    "Phase2R4JournalRecord",
    "Phase2R4ModelInput",
    "Phase2R4OfflineGate",
    "Phase2R4PromptContract",
    "Phase2R4RegressionResult",
    "Phase2R4Report",
    "Phase2R4Request",
    "REGRESSION_VERSION",
    "RENDER_PRESENTATIONS",
    "REPORT_VERSION",
    "REQUEST_VERSION",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "SOURCE_PRESENTATIONS",
    "TOKEN_PATTERN",
    "_CARD",
    "_FACT",
    "_FAKE_FILENAME",
    "_JOB",
    "_MODEL_INPUT",
    "_REQUEST",
    "_SHA",
    "_canonical_hash",
    "_derive_batch_manifest",
    "_derive_card",
    "_derive_edge",
    "_derive_fake_artifact",
    "_derive_fake_manifest",
    "_derive_list",
    "_derive_model_input",
    "_derive_request",
    "_derive_table",
    "_derive_visible_text",
    "_extract_tokens",
    "_json_bytes",
    "_no_forbidden_model_keys",
    "_no_forbidden_text",
    "_sha256",
    "_source_card_payload",
    "_source_text_payload",
    "_walk_mappings",
    "assess_offline_gate",
    "build_regression_matrix",
    "derive_contracts",
    "derive_step4_bundle",
]
