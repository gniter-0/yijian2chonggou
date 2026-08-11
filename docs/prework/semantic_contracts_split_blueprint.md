# semantic_contracts.py 拆分蓝图（P0 · 阶段2搬运前预研）

- 生成日期：2026-08-11；方法：对旧仓只读 AST 分析（顶层定义图、SCC、注解/运行期引用分类、外部导入面扫描）
- 对象：`yijian2/scripts/knowledge_v2/semantic_contracts.py`（7258 行 / 389,114 B，261 个顶层定义：123 class / 74 def / 64 常量）
- 去向：`src/examcard/knowledge/contracts/`（见 `extraction_manifest.md` 第一节、`engineering_skeleton.md` 第 3 节）
- 结论：**可无损拆成 23 个子模块 + 1 个 facade `__init__.py`；运行期依赖可分层为严格 DAG（已校验 0 回边）；外部 147 个被导入名字全部经 facade 重导出，调用方只改模块前缀。**

## 1. 现状盘点（依据，全部机器扫描）

**上游依赖（本文件 import 谁）**——全部单向、无回边：

```
semantic_contracts → evidence_v2.layout (normalized_compare_text)
                   → evidence_v2.schema (BBox, EvidenceKind, EvidenceUnit)
                   → materials_v2.schema (ContentPolicy, PolicyDecision, SemanticRole, StrictModel)
                   → knowledge_v2.schema (AtomicFact, CompletenessMode, FactType, ShortCardException)
                   → knowledge_v2.identity (make_fact_id)
                   → knowledge_v2.continuation_review (sidecar 装载/校验 6 个名字)
```

对应新仓路径：`examcard.evidence.*`、`examcard.materials.schema`、`examcard.knowledge.schema`、
`examcard.knowledge.identity`、`examcard.knowledge.continuation.review`。方向合法（contracts 在
knowledge 层内，可依赖同层 schema/identity/continuation 与下层 evidence/materials）。

**下游引用（谁 import 本文件）**：51 测试 / 29 phase / 16 核心模块 / 38 tools。关键事实：

- 外部实际导入 **147 个不同名字**（清单见 §6）；
- **无** `import semantic_contracts` 整模块别名用法，**无** `import *`，**无** pickle/getattr/importlib 动态引用；
- 有 2 个**转口 re-export** 正被外部使用：`CompletenessMode`（真身在 `knowledge_v2.schema`，1 处测试引用）、
  `normalized_compare_text`（真身在 `evidence_v2.layout`，3 处 core/phase 引用）→ facade 必须继续转口。

⇒ facade 全量重导出即可 100% 覆盖外部使用面；调用方改写是纯前缀替换。

## 2. 拆分原则

1. **契约字节不变**：全部 `*_VERSION` 字符串、prompt 模板文本、哈希算法逐字节保留。
   `canonical_step2_sha256` 是纯值 JSON 规范化（不含类名/模块路径），故**搬迁本身不改变任何
   哈希/ID/缓存键**；唯一能破坏平价的是改动 prompt 字符串或版本常量本身。
2. **运行期依赖单向分层**：子模块只允许 import 排在自己前面的层；注解性回引（PEP 563 下永不求值）
   一律走 `if TYPE_CHECKING:`，**不引入任何运行期懒 import**。
3. **pydantic 字段注解必须在定义模块可解析**（pydantic 在建模时按定义模块命名空间解析字段类型，
   TYPE_CHECKING 技巧只适用于普通函数签名，不适用于模型字段）。下表分层已按
   「class 体内全部名字须运行期可解析」口径校验，0 违规。
4. 单模块 ≤ 55KB，按领域内聚命名，不再保留巨石文件。

## 3. 目标布局（层序即 import 允许方向，只许引用上方）

`src/examcard/knowledge/contracts/` 下 23 个子模块（«体量»为按定义行精确求和）：

| # | 模块 | 主要内容（关键符号） | 源行区间 | 体量 | 运行期依赖（包内） |
|---|---|---|---|---|---|
| 1 | `versions.py` | 全部 40+ 个 `*_VERSION` 常量与 `_..._VERSIONS` frozenset；**含 4 个散落中段的输入版本常量**（L1452/1453/1656/1657，上提至此） | 37–98 + 4 处散点 | 5.2KB | – |
| 2 | `hashing.py` | `_canonicalize` `canonical_step2_sha256` `_canonical_sha256` `fingerprint_step2_relay` `fingerprint_step2_prompt` `_OFFLINE_RELAY_BASE_URL` | 527–558 | 1.3KB | – |
| 3 | `enums.py` | `Step2Stage` `CardPresentation` `NumericTokenClass` `ConditionOperator` `condition_operators_in_text` `Step2ResponseFormat` `Step2DecoderConfig` `default_step2_decoder` `Step2FailureState` `Step2ResultState` | 143–218, 509–525 | 2.9KB | – |
| 4 | `numeric_tokens.py` | 数字令牌正则常量（L112–140）+ `NumericTokenClassificationRecord` + `classify_numeric_tokens` | 112–140, 2114–2126, 6958–7002 | 4.1KB | enums |
| 5 | `prompts.py` | `default_step2_prompt_template`（30KB 巨型模板，**逐字节保留**，见 R2） | 220–508 | 30.3KB | enums, versions |
| 6 | `request_identity.py` | `_SHA256_PATTERN` `Step2RequestIdentity` `make_step2_request_identity` | 99, 560–678 | 5.8KB | enums, hashing, versions, prompts |
| 7 | `anchors.py` | `make_evidence_anchor_span_id` `EvidenceAnchorSpan` `_TABLE_KINDS` `_DIRECT_TEXT_KINDS` | 100–110, 750–812 | 2.8KB | hashing |
| 8 | `continuation.py` | `ContinuationState/BindingState/BindingFailureCode` `ContinuationRecord` `ContinuationBinding` `build_continuation_binding` `build_reviewed_continuation_bindings` `TableGridIntegrityStatus/Record` 及 id 构造器 | 703–725, 866–1146 | 13.6KB | hashing, anchors；外部 `..continuation.review` |
| 9 | `projection.py` | `ProjectionSelection` `ProjectionDecisionRecord` `ReviewedDiagram*Input`×3 `Step2ASemanticProjection` `_validate_parent_anchor_spans` | 679–702, 726–749, 1147–1344 | 13.1KB | versions, anchors |
| 10 | `table_inputs.py` | `SectionTextInput` `TableCellInput/RowInput` `StructuredTableInput` `TableContinuationSource*Input` `TableContinuationRequestInput` `TableAtomicSubfactKind/Requirement` `derive_table_atomic_subfact_requirements` `_table_atomic_subfact_kind` `Step2ASectionInput` `CompleteTableSemanticCoverage` `_geometry_header_cell_ids` `_make_section_id` 及 id 构造器 | 695–702, 813–865, 1345–1659, 1979–2113, 5101–5158 | 28.5KB | versions, hashing, enums, numeric_tokens, anchors, continuation |
| 11 | `obligations.py` | `Step2ACoverageObligationKind` `Step2ACoverageObligation(+Input/Context/ContextInput/AnchorRole/AnchorContext)` `derive_step2a_coverage_obligation_input` 及 id 构造器 | 1660–1978 | 16.1KB | versions, hashing, table_inputs |
| 12 | `claims.py` | `ProposalNumericTokenAudit` `ModelNumericClaim` `GroundedNumericClaim` `GroundedConditionClaim` | 2127–2184 | 2.3KB | enums, numeric_tokens |
| 13 | `proposals.py` | `Step2AModelFactProposal` + `Step2AV13–V23ModelFactProposal` + `make_step2a_v13–v18_proposal_key` + `Step2AFactProposal` | 2185–2694 | 20.9KB | versions, hashing, enums, claims |
| 14 | `responses.py` | `Step2AFailure` `Step2AResponse` `Step2AResultPayload` + `Step2AV13–V23ResultPayload` + `step2a_result_payload_model` | 2695–2972 | 12.0KB | versions, enums, request_identity, proposals |
| 15 | `occurrences.py` | `ConditionSpanReference` `NumericSpanReference` `TableNumericOccurrence*` `TableStructuralIndexOccurrence*` `TableStructuralCellOccurrence/Role` `CompleteTableStructuralProjection(+State)` `TableGridMetadataReference` 及 id 构造器（**注意 R4 定义顺序**） | 3702–3715, 3778–3941, 3972–4211 | 15.9KB | hashing, enums |
| 16 | `request_helpers.py` | `_step2a_anchor_sources` `_request_structured_tables` `_ordered_parent_evidence_ids` `_derive_v13_source_section_ids` `_derive_v13_completeness_mode` `derive_step2a_coverage_obligation_context_input`（对 `Step2ARequest` 仅 TYPE_CHECKING） | 5692–5880 | 10.1KB | anchors, table_inputs, obligations |
| 17 | `audits.py` | 全部 `*Audit/*State/*DerivationResult/*Forensic` + `assess_step2a_table_semantic_coverage / _table_atomic_subfacts / _coverage_obligations / _continuations` + `derive_step2a_coverage_obligation_forensics` | 2973–3701 (去 occurrences/claims 部分), 3942–3971, 4212–4310 | 48.8KB | enums, obligations, occurrences, request_helpers 等上层 |
| 18 | `step2b.py` | `ContinuationTitleProjectionProvenance` `ReviewedCanonicalTitleProjection` `FactSummaryInput` `Step2BCardProposal/Response/ResultPayload` `Step2BRequest` `_step2b_payload` `fact_summary_from_atomic_fact/from_proposal` `build_step2b_request` | 4311–4447, 4971–5014, 5080–5100, 5578–5675 | 14.2KB | versions, hashing, enums, request_identity, responses |
| 19 | `step2a_request.py` | `Step2ARequest`（523 行巨模型）+ `_step2a_payload`（其运行期私有序列化器，**必须同模块**） | 4448–4970, 5015–5079 | 39.7KB | 以上全部所需层（runtime 调 request_helpers/obligations） |
| 20 | `step2a_builders.py` | `build_step2a_section` `_default_manual_continuation_bindings` `build_step2a_request` | 5159–5577 | 20.0KB | step2a_request, request_helpers, table_inputs, continuation… |
| 21 | `derivation.py` | `derive_step2a_anchor_span_selection` `derive_step2a_table_cell_anchors` `derive_step2a_condition_claims` `derive_step2a_numeric_claims` + 私有 span/occurrence 候选与匹配助手 | 5882–6957, 7003–7006 | 54.2KB | numeric_tokens, claims, occurrences, audits, table_inputs |
| 22 | `validation.py` | `ContractValidationReport` `validate_step2a_response` `validate_step2b_response` `_claim_values` | 5676–5691, 7003–7230 | 14.9KB | derivation, audits, responses, step2b |
| 23 | `testing.py` | `DeterministicFakeLLM`（**外部零引用**，仅供测试装置） | 7231–7258 | 1.5KB | responses, step2b, request_identity |
| – | `__init__.py` | facade：全量显式重导出（见 §5） | – | ~8KB | 全部 |

行区间为主体定位（个别符号跨区已在「主要内容」列明确归属）；执行拆分时以**符号清单**为准、不以行号硬切。

## 4. 环的事实与解法（本蓝图核心结论)

全文件 261 个顶层定义间**只有一个强连通分量**：

```
Step2ARequest ⇄ { _step2a_anchor_sources, _request_structured_tables,
                  derive_step2a_coverage_obligation_context_input }
```

引用方向分类（AST 区分注解位/运行位）：

- helpers → `Step2ARequest`：**全部 annotation-only**（函数签名类型注解；文件启用
  `from __future__ import annotations`，永不求值）；
- `Step2ARequest` → helpers：**运行期调用**（model_validator 内调
  `derive_step2a_coverage_obligation_context_input`、`derive_step2a_coverage_obligation_input`；
  方法内调 `_step2a_payload`）。

解法（零懒 import）：

1. helpers 下沉到 `request_helpers.py`，对 `Step2ARequest` 的注解引用改
   `if TYPE_CHECKING: from .step2a_request import Step2ARequest`；
2. `_step2a_payload` 与 `Step2ARequest` 同模块（`_step2b_payload` 同理进 `step2b.py`）；
3. `assess_step2a_*`（audits 层）对 `Step2ARequest` 同为 annotation-only → 同样 TYPE_CHECKING；
   其运行期依赖只有 `_request_structured_tables` → `request_helpers` 排在 `audits` 之前；
4. 4 个中段版本常量上提 `versions.py`，消除 table_inputs/obligations 对常量位置的耦合。

以上分层已整体机器校验：**运行期回边 0 条**（含 pydantic 类体注解按运行期口径计）。

## 5. facade 与外部改写策略

`contracts/__init__.py` 显式重导出（禁止 `import *` 链）：

```python
"""Semantic planner contracts (Step 2A/2B), split from legacy semantic_contracts.py."""
from ..schema import CompletenessMode          # 转口：外部既有用法
from ...evidence.layout import normalized_compare_text  # 转口：外部既有用法
from .versions import (...)
from .hashing import (...)
# ...按 §3 层序逐模块列出全部公共符号...
__all__ = [...]  # 与 §6 清单 + 内部公共符号并集
```

- 迁移的 16 个核心模块与 76 个随迁测试：`from scripts.knowledge_v2.semantic_contracts import X` /
  `from knowledge_v2.semantic_contracts import X` → `from examcard.knowledge.contracts import X`，
  **纯前缀替换**，X 不变；
- `v25_generic_contracts` / `v25_teaching_gate_layers` 同入 `contracts/` 包后，改为包内导入
  （`from . import ...` 或直引子模块）；
- 旧仓 29 个 phase、93 个留守测试、289 个伴生工具**一律不动**（继续用旧仓原文件）；
- 新仓 schema 自动发现导出（替代 `export_v2_schemas.py`）扫描 `contracts` 包全部子模块即可拿到
  全部 BaseModel 子类，facade 已保证可达。

## 6. 外部 147 名 → 新子模块归属（验收对照表）

| 新模块 | 外部正在导入的符号 |
|---|---|
| `versions` | 29 个 `*_VERSION` 常量（`STEP2A_REQUEST_VERSION`、`SOURCE_OWNED_STRUCTURAL_PROJECTION_*`、`TABLE_COVERAGE_OBLIGATION_*`、`STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION` 等，全清单见任务附录脚本输出，逐一保留原名） |
| `hashing` | `canonical_step2_sha256`（71 处，全库最热）、`fingerprint_step2_relay`（34）、`fingerprint_step2_prompt` |
| `enums` | `Step2Stage` `CardPresentation` `NumericTokenClass` `Step2DecoderConfig` `Step2FailureState` `Step2ResponseFormat` `Step2ResultState`（41 处）`condition_operators_in_text` |
| `numeric_tokens` | `classify_numeric_tokens` |
| `prompts` | `default_step2_prompt_template` |
| `request_identity` | `Step2RequestIdentity` `make_step2_request_identity` |
| `anchors` | `EvidenceAnchorSpan` `make_evidence_anchor_span_id` |
| `continuation` | `ContinuationBinding(+State/FailureCode)` `ContinuationRecord` `TableGridIntegrityRecord/Status` `build_continuation_binding` `build_reviewed_continuation_bindings` `make_continuation_record_id` |
| `projection` | `ProjectionSelection` `ProjectionDecisionRecord` `ReviewedDiagram*Input` `Step2ASemanticProjection` |
| `table_inputs` | `SectionTextInput` `Step2ASectionInput` `StructuredTableInput` `TableCellInput/RowInput` `TableContinuation*Input` `TableAtomicSubfactKind/Requirement` + 3 个 `make_table_*_id` |
| `obligations` | `Step2ACoverageObligation(+Kind/Input/ContextInput)` `derive_step2a_coverage_obligation_input` |
| `claims` | `ModelNumericClaim` |
| `proposals` | `Step2AFactProposal` `Step2AModelFactProposal` `Step2AV13/14/15/16/20/23ModelFactProposal` `make_step2a_v13_proposal_key` |
| `responses` | `Step2AFailure` `Step2AResponse` `Step2AResultPayload` `Step2AV13–V23ResultPayload` `step2a_result_payload_model` |
| `occurrences` | `TableNumericOccurrenceState` `TableStructuralIndexOccurrenceState` `CompleteTableStructuralProjectionState` `make_table_structural_index_occurrence_id` |
| `audits` | 18 个 `*Audit/*State/*Forensic*` + `derive_step2a_coverage_obligation_forensics` `Step2ANumericClaimDerivationResult` |
| `step2b` | `FactSummaryInput` `Step2B*`×5 `ReviewedCanonicalTitleProjection` `ContinuationTitleProjectionProvenance` `fact_summary_from_proposal` `build_step2b_request` |
| `step2a_request` | `Step2ARequest` |
| `step2a_builders` | `build_step2a_request`（21 处）`build_step2a_section` |
| `derivation` | `derive_step2a_anchor_span_selection` `derive_step2a_table_cell_anchors` `derive_step2a_condition_claims` `derive_step2a_numeric_claims` |
| `validation` | `ContractValidationReport` `validate_step2a_response` `validate_step2b_response` |
| （转口） | `CompletenessMode`（→ `..schema`）、`normalized_compare_text`（→ `...evidence.layout`） |

## 7. 迁移步骤（自底向上，每步可独立验证）

1. 按 §3 层序 1→23 建子模块（每建一个即 `python -c "import examcard.knowledge.contracts.<m>"` 冒烟）；
2. 写 facade `__init__.py`，跑 facade 完整性断言（§8-2）；
3. 同批改写依赖 contracts 的 16 个核心模块（llm/projection/quality/continuation.integration 等，
   见 manifest 第一节）与 76 个随迁测试的 import 前缀，随手满足 T1–T8；
4. 跑哈希平价黄金校验（§8-1）+ 全量 `uv run pytest`；
5. `v25_generic_contracts` / `v25_teaching_gate_layers` 入包并改包内导入，重复 4。

## 8. 不变量验收（拆分算成的标准）

1. **哈希平价**（一票否决）：新增 `migration/verify_contracts_hash_parity.py`——取旧仓
   learning_fixture 黄金样本重放，逐字节比对 `prompt_sha256` / `payload_sha256` / `request_id` /
   `cache_key` / 各 `make_*_id`。论证基础：`_canonicalize` 只序列化值（Enum 取 .value、Mapping 键
   字符串化、集合排序），产出不含任何类名/模块路径，搬迁本身零影响。
2. **facade 完整性**：`tests/test_knowledge_contracts_imports.py` 断言 §6 全部 147 名 + 2 转口可从
   `examcard.knowledge.contracts` 导入，且 `__all__` 无缺漏。
3. **分层守卫**：断言 contracts 子模块运行期 import 无回边（AST 扫描即可，或引 import-linter）。
4. 旧仓不动性：本次预研与后续拆分对 yijian2 零写入（29 phase/93 测试继续引用旧文件）。

## 9. 风险清单

| # | 风险 | 处置 |
|---|---|---|
| R1 | 本文件用 `enum.StrEnum`（需 Python ≥3.11），而新仓 pyproject 原为 `requires-python >=3.10` | **已裁决（2026-08-11 总指挥）**：新仓 requires-python 提至 `>=3.11`（venv/CI 建议 3.12），不重写任何 StrEnum 代码；骨架负责人同步修改 pyproject 与 skeleton 文档 |
| R2 | `prompts.py` 30KB 模板字符串被 autoformat/换行重排 → `fingerprint_step2_prompt` 漂移、全部缓存键失效 | 迁移后立即跑 §8-1；若引入 formatter，将 `prompts.py` 加入排除清单 |
| R3 | pydantic 字段注解须在定义模块可解析（PEP 563 下 TYPE_CHECKING 不适用于模型字段） | 分层已按此口径校验 0 违规；执行时不得把字段引用的模型改成 TYPE_CHECKING |
| R4 | `CompleteTableStructuralProjection` 字段前向引用 `TableGridMetadataReference`（原文件中后者定义在 L4176、前者 L4072） | `occurrences.py` 内把 `TableGridMetadataReference` 排到前面，消除对 `model_rebuild` 的依赖 |
| R5 | `Step2ARequest`（39.7KB）与 `derivation.py`（54.2KB）仍偏大 | 可接受（<55KB 且职责单一）；`derivation.py` 如需再切，按 anchor/condition/numeric 三段自然边界二次拆分，互不依赖 |
| R6 | 黄金装置若序列化了模块路径会被搬迁破坏 | 已扫描：全仓无 pickle/动态导入/字符串模块引用；装置为 JSON，pydantic 序列化不含类路径。§8-1 兜底 |
| R7 | 与 2026-08-11 冻结的共享底座 `examcard/common/`（canonical_json/canonical_hash/hash_model + GateCheck，四个 distill 桶共用；总指挥终裁广播 bc-888c6031：底座定名 common/，utils/ 另收非底座杂项）的边界：`contracts/hashing.py` 的 `canonical_step2_sha256/_canonicalize` 是 Step2 缓存键**法定旧契约**（§8-1 字节级平价门禁），与 common 的新底座是不同谱系 | **已裁决（2026-08-11 总指挥，边界冻结）**：common/ 只收四桶**新**底座；knowledge/contracts 自带法定旧契约哈希（canonical_step2_sha256/_canonicalize/fingerprint_*）**逐字节原样随包搬运，不折叠、不改调 common 实现**——任何一字节规范化差异都会漂移全部 request_id/cache_key。两者合流仅允许在黄金重放逐字节平价通过后以别名化方式进行，且明确不属阶段2范围。〔总指挥勘误 2026-08-11：原稿此处写 utils/，按终裁广播 bc-888c6031 更正为 common/〕 |

## 附：本蓝图分析口径

- 顶层定义图：`ast` 解析 261 个顶层 def/class/常量，收集互相引用 → Tarjan SCC → 唯一 4 元环（§4）；
- 注解/运行期分类：区分名字出现在注解位（PEP 563 惰性）与运行位（含 pydantic 类体按运行期计）；
- 分区校验：按 §3 的 23 模块分配全部 261 符号（0 未分配），层序回边 0 条；
- 外部使用面：全仓（scripts 含 tests/tools）AST 扫描 `from ...semantic_contracts import` 共 147 名，
  无整模块导入、无 `import *`。
