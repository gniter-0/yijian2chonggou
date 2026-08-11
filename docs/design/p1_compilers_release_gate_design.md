# P1 提炼设计：Step3/Step4 编译器（4.1）+ release gate（4.4）

- 日期：2026-08-11；作者：P1·compilers+release-gate 设计任务
- 输入：`docs/extraction_manifest.md`（阶段1清单）；yijian2 旧仓 `scripts/knowledge_v2/` 下 9 个模块源码全文精读
- 范围：清单 4.1 的 4 个编译器（phase2r4 / r9 / r92 / r11）+ 清单 4.4 的 4 个 release-gate（phase2r15 / r98 / r152 / r6）
- 范围备注：任务书把 4.4 写作「15/152/16/6」，与清单 4.4（15/98/152/6）不一致。本文按**并集**处理：r98 完整设计；phase2r16 在清单中归 4.3（distill:gates-review），本文只做**边界设计**（§7），完整提炼设计归 gates-review 工作流，避免两边重复。

---

## 0. 结论摘要（TL;DR）

1. **迁移形态 = 复制改造（copy-adapt），不是搬移，也不做旧仓 shim。** 旧仓 phase 脚本本体一个字节都不能动：后继 phase 已落盘的 freeze 记录直接锁了前置 phase `.py` 文件的 sha256（r11 锁 `*phase2r10*.py`，r98 锁 `phase2r97*.py`，r152 锁 `phase2r151*.py`，r9 锁 `phase2r8*.py`；重验入口 `verify_prior_freeze/verify_freeze/audit_frozen_history` 都会重新哈希这些文件）。把旧脚本改成「从新仓 re-export」会击穿整条冻结链。
2. **哈希稳定性是 v1 验收红线。** 所有 `schema_version` Literal（含 `phase2r9_*`/`phase2r4_*` 等历史命名）与 ID 前缀（`step3-card-contract:`、`step4-image-request:r11:` 等）**原样带入新仓**。这些字符串全部参与 `canonical_hash` → 改名即改 `contract_id/gate_sha256/report_sha256`，黄金对照全崩。引擎中性的命名留给 v2。
3. **布局终裁**（公告 **bc-888c6031** + 总指挥 2026-08-11 直裁增补）：顶层 10 包；**common = 冻结底座，终态模块集 {hashing, gates, io, freeze, junit, schema}**（本桶提的 io/freeze/junit 增补三项全批，strict_schema 落 common/schema.py；章程：只收通用、签名落地即冻、变更报总指挥）；utils = 非底座杂项；providers 顶层；common/utils 为叶包。**叠加冻结哈希铁律**（公告 bc-b8b9aca6，继续有效）：旧契约法定哈希/指纹实现一律逐字节随模块搬运、禁止折叠进 common/hashing——本桶 8 个模块的 canonical/门禁/回归哈希实现全部随模块原样进新仓，common 底座仅供不参与既有指纹的新代码；合流延后到黄金重放平价后（§2.1）。另 R1：requires-python 已上调 >=3.11。
4. **切分原则：纯函数与契约模型进新仓；一切「读旧仓某条历史运行/锁死某组期望值」的粘合留旧仓。** 每个模块的逐符号去向见 §4–§6 的去向表。
5. **跨桶契约终裁（总指挥直裁，撤回此前 Protocol 合意）**：跨桶持久化数据契约（`ReviewedTypedEvidenceFixture`、r2242 三型、`ReviewedStep3Fixture`、`Step4PilotHandoffRefresh`、**`Step3CardContract` 同族**）统一下沉 **`knowledge/contracts/admission_fixtures.py`**（只收纯数据模型）；compilers 与 gates 两桶均向下 import 具体类型，零横向边——持久化契约要唯一具体类型，不用鸭子类型削弱静态保证（§8.1）。

---

## 1. 硬约束与总体策略

### 1.1 冻结链约束（决定迁移形态）

旧仓的 phase 体系用「前置 phase 冻结审计」保证历史不可变，且**已落盘**的 freeze 记录中直接包含源码文件哈希：

| 冻结方 | 被锁文件（举例） | 重验入口 |
|---|---|---|
| phase2r9 `_freeze_paths` | `scripts/knowledge_v2/phase2r8*.py`、`schemas/v2/phase2r8*.json`、`data/.../phase2r8*` 全目录 | `verify_prior_freeze` |
| phase2r92 `_prior_targets` | r9/r91 运行目录 + 验收文档（不含 .py，但含 run 全目录） | `verify_prior_freeze`（含 git merge-base 校验） |
| phase2r98 `R97_CODE_FILES` | `phase2r97_*.py`、其 tests/tools `.py` 字节 | `verify_r97_freeze` |
| phase2r11 `_frozen_paths` | `scripts/{knowledge_v2,tests,tools}/*phase2r10*.py`、schemas、docs | `verify_freeze` |
| phase2r152 `_REMEDIATION_OWNED_PATHS` | `phase2r151_gonglu24_remediation_live.py` 等 6 项字节 | freeze audit before/after |
| phase2r4 `FROZEN_R33_R319_HASHES` | R3.3–R3.19 期工件哈希常量表（含 phase2r319 等 `.py` 字节；`FROZEN_ARTIFACT_COUNT` 动态取 len） | `audit_frozen_history` |

推论：
- **旧仓不动**：9 个模块的 `.py`、伴生 tools/tests、schemas、docs、data 运行目录全部原样留守（与清单「脚本本体仍留旧仓」一致，且是被冻结链强制的）。
- **新仓复制改造**：把通用逻辑抄入新仓后按 §2–§6 改造；新旧两份实现共存，旧仓继续为历史 phase 服务，新仓面向新科目/新流水线。
- **验收动作**：迁移完成后在旧仓抽查重验（`pytest scripts/tests/test_phase2r9_*.py` 等 + 直接调 `verify_*_freeze`），证明提炼过程零漂移。

### 1.2 提炼改造的统一手法

对每个模块做同一套四步改造：

1. **模型层原样带走**：所有 `StrictModel` 契约类连同 `schema_version` Literal、字段约束、`model_validator` 原样复制（仅改 import：`materials_v2.schema.StrictModel` → `examcard.materials.schema.StrictModel`，以 P2·materials 迁移落点为准）。
2. **纯函数层参数化**：凡是 `build_*(root: Path)` 里「读盘 + 校验授权 + 纯计算」混合的，拆为
   - `load_*()`（读盘装载，旧仓粘合或调用方职责）
   - `compile_*/derive_*/audit_*(输入对象, *, 显式参数)`（纯计算，进新仓）。
   钉死的默认路径 / 期望数量 / 冻结哈希表一律变成**参数或配置对象**，旧仓的钉死值留在旧脚本里。
3. **公共原语按 §2.1 铁律口径分流**：法定指纹实现随模块原样搬运；不参与指纹的写盘/路径助手用 `examcard.utils`（非冻结杂项）；新代码的门禁/哈希定式用 `examcard.common`；合流延后到平价验证后。
4. **报告渲染与 materialize 留旧仓**：中文验收报告 `_render_report`、`materialize_*`（写 RUN_DIR、schemas 导出、junit 收编）都是 phase 交付件，不进引擎。

---

## 2. 公共原语层：已裁决冻结面 + 增补提案

**布局终裁**（总指挥公告 **bc-888c6031**，替代此前所有布局公告）：`examcard.common` = 冻结底座（仅 `hashing.py + gates.py`，签名按 providers 稿 §6.1/§9.1 冻结，模块增删与签名变更须报总指挥）；`examcard.utils` = 非冻结杂项；两包均为叶包。冻结哈希铁律 bc-b8b9aca6 继续有效。

本文 8 个模块的重复原语按此映射：

### 2.1 冻结面映射（受 bc-b8b9aca6 冻结哈希铁律约束）

**铁律修正**（总指挥公告 bc-b8b9aca6，与布局终裁 bc-888c6031 并行有效）：旧契约的**法定哈希/指纹实现**一律逐字节原样随包搬运，**禁止折叠进 common/hashing 或改调新实现**；common 冻结底座仅供四桶**新代码**使用；合流仅允许在黄金重放逐字节平价通过后以别名化方式进行，且不属阶段2范围。据此本桶执行口径：

| 重复实现 | 处置（v1） |
|---|---|
| canonical/哈希系列：r9/r92/r11/r98/r6 各一份 `canonical_json/canonical_hash/file_hash/sha256_*/_hash_model`；r4 `_json_bytes/_sha256/_canonical_hash`；r15/r16 `_sha256_bytes/_canonical_sha256` | **逐字节原样搬运、不 import common.hashing**。其中 r9 系实现（r10/r92/r98 在旧仓即 import r9 的 `canonical_hash/file_hash`）唯一落点为 `knowledge/contracts/admission_legacy_hash.py`（r9/r10x 变体），compilers 各模块向下 import 同一实现——字节不变、避免复制两份法定实现；r92/r11 等模块自带的局部实现（如 r92 自己的 `canonical_json`、r11 的 `_jsonable`）与 r4/r6/r15/r16 的副本仍随各自模块。合流延后到平价验证后的别名化阶段。**同源警示**（gates-review 勘察实证 r8x/r10x 两个不等价变体）：各副本即使外观雷同也**禁止以「看起来相同」为由跨模块合并或互换**——`_hash_model` 是否排除 schema_version、`_jsonable` 是否预排序等微差足以改变指纹；等价性只能由双跑对照裁定 |
| `GateCheck`/门禁组装、`RegressionResult`/`_fail_closed`、禁键/禁文扫描（r9 `_contains_forbidden`、r4 `_walk_mappings/_no_forbidden_*`、r92 `_collect_keys`、r15 `_collect_forbidden_keys`） | 结构与定式**同样随模块原样搬运**（其组装的 payload 参与 gate_sha256/regression_sha256 计算，哈希调用必须走模块内原样实现）。common/gates 的定式仅供新增代码（如新仓自建 CI 门禁）使用；**forbidden 禁键扫描不在冻结面，留桶自持**（骨架 2026-08-11 确认） |
| `_atomic_write_json`（r15/r152/r16）、`_atomic_write_bytes`（r92）、`write_json`（r9）、`_resolve/_relative`（7 份） | 写盘/路径助手**不参与指纹计算**：统一改用 **`common/io`**（总指挥 2026-08-11 批复增补）——写出字节由 payload 决定、与写盘助手无关，双跑对照会逐字节验证 |
| freeze 机制（r9 `PriorFreeze`、r11 `R10Freeze`、r98 `R97Freeze`、r15/r152/r16/r4 FreezeAudit） | 旧 freeze 记录的构建/重验**全部留旧仓**（§1.1 粘合），不迁移；新仓自身的新 freeze 需求用 **`common/freeze`**（已批复；按本文限定：不含各 phase 路径 glob 集、不依赖 git；gates-review 同步改为消费 common/io+freeze）。r104 历史产物的自哈希重验走 gates-review 桶 `legacy_hash` 导出的校验函数（§8.1 实施约束②） |

### 2.2 增补批复结果（总指挥 2026-08-11 直裁：三项全批 + schema 新模块）

| 小件 | 落点（已批） | 说明 |
|---|---|---|
| io 写盘/路径助手 | `common/io.py` | atomic_write_json / write_json / read_json / write_yaml / resolve / relative |
| freeze 机制 | `common/freeze.py` | FrozenArtifact + build/verify_freeze + 快照 diff；不含 phase 路径 glob 集、不依赖 git |
| junit 回执 | `common/junit.py` | JUnitReceipt + parse_junit_receipt |
| `strict_schema(model)` | `common/schema.py`（新模块） | 语义不属 hashing；schema 自动发现导出工具（dev 工具）import 它 |

common 终态模块集 = **{hashing, gates, io, freeze, junit, schema}**，章程不变（只收通用、签名落地即冻、变更报总指挥）。注意：提炼模块内**参与既有指纹**的哈希/门禁/回归实现仍按 §2.1 铁律随模块原样带走，common 仅供新代码与不参与指纹的机制件。

各模块自带的 `FORBIDDEN_KEYS/FORBIDDEN_TEXT` 常量集**不合并**（r9/r4/r15 集合不同、语义边界不同），仍归各自模块文件，只共用扫描函数。

---

## 3. 新仓包布局（以骨架 `docs/engineering_skeleton.md` 为准）

骨架已落地；布局依终裁 bc-888c6031 为顶层 10 包（`webui → pipeline → { compilers, gates } → knowledge → evidence → materials`，common/utils 为叶包全员可用，providers 顶层、只可 import common+utils，只许向下禁回边）。本桶落点：

```
src/examcard/
  common/                  # 终裁冻结底座六件：hashing/gates/io/freeze/junit/schema（仅供新代码，§2）
  utils/                   # 非底座杂项（writeback/lineage/日志等）；本桶不投放
  knowledge/
    contracts/
      admission_fixtures.py    # 总指挥终裁：跨桶持久化数据契约下沉于此（Step3CardContract 同族、
                               #   ReviewedTypedEvidenceFixture、r2242 三型、ReviewedStep3Fixture、handoff、
                               #   r104 证物三型、r98 两型 DeterministicDraftCard/Bundle + 内嵌 FakeArtifact）
      admission_legacy_hash.py # 法定哈希随契约下沉（r8x 变体 + r9/r10x 变体各一份，逐字节原样；
                               #   放 knowledge 层避免 knowledge→gates 向上引用）
  compilers/               # 骨架既定（manifest 4.1）
    step3_card.py          # ← phase2r9（编译逻辑 + 批工件模型；契约模型自 knowledge/contracts import）
    step3_response.py      # ← phase2r92_generic_step3_response_contract
    step4_contract.py      # ← phase2r4_step4_generic_contract_compiler
    step4_image.py         # ← phase2r11_generic_step4_source_derived_image_compiler
  gates/
    release/               # 骨架既定（manifest 4.4）
      readiness.py         # ← phase2r15 + phase2r152（数据驱动合并）
      closure.py           # ← phase2r98
      checkpoint.py        # ← phase2r6
      # 发布 fixture 契约模型（r16 形态）落点归 gates-review 设计裁量，见 §7
```

依赖方向（新仓内，均合规向下、零横向边）：`compilers/* 与 gates/* → knowledge/contracts/admission_fixtures`（跨桶契约唯一具体类型）；`gates/release/closure → compilers/step3_card`（批工件 DraftBundle 等；同层内 gates→compilers 单向，为原有 gates-review queue 依赖的同向）；`compilers/step3_response → compilers/step3_card`（复用 ModelLayout/GeometryConstraints 等）；全员 → common、knowledge(schema/StrictModel 经 materials)。**compilers 不 import gates**，无环。

---

## 4. 编译器提炼设计（清单 4.1）

### 4.1 `compilers/step3_card.py` ← phase2r9（59KB；全组枢纽）

**画像**：消费 R8.10 已审定 typed-Evidence fixture，把五种表达族（diagram/table/ordered_list/constraint/formula）投影成统一的 `Step3CardContract`，产出 model_input / prompt / 离线 request / 确定性 draft / batch manifest / 离线门禁 / 15 条 fail-closed 回归。**被 10 个 phase 模块 + tools + tests 引用**，是 Step3 链的类型与哈希底座。

**符号去向表**：

| 去向 | 符号 |
|---|---|
| 新仓·契约下沉（总指挥终裁 → `knowledge/contracts/admission_fixtures.py`） | **Step3CardContract 同族**：`SourceProvenance, DisplayElement, TableCellRef, ListItemRef, TokenRef, DiagramNodeRef, DiagramEdgeRef, LayoutContract, GeometryConstraints, Step3CardContract`（含 contract_sha256 自哈希 validator）。canonical 法定实现随契约下沉至 `admission_legacy_hash.py`（r9/r10x 变体；旧仓中 r10/r92/r98 本就 import r9 的 `canonical_hash/file_hash`——同一实现改为大家向下 import，字节不变）。compilers 与 gates 均向下 import |
| 新仓·随发布契约下沉（2026-08-11 与 gates-review 定）| `FakeArtifact` → `admission_fixtures.py`：它被 r98 的 `DeterministicDraftCard` 内嵌持久化、随 r98 两型下沉（见 §5.2），若留 compilers 会给下沉后的发布契约留反向依赖 |
| 新仓（批工件模型，留 compilers） | `ModelSourceBinding, ModelVisibleElement, ModelLayout, ModelCard, Step3ModelInput, PromptContract, OfflineRequestPlan, DraftBundle, PresentationCounts, BatchManifest, OfflineGate, Readiness, Artifacts, Report` + 常量 `PRESENTATIONS, DISPLAY_ROLES, FORBIDDEN_KEYS, FORBIDDEN_TEXT` 及 ID 正则组（`DraftBundle` 仅编译期容器、无跨桶持久化消费者，留驻） |
| 新仓（函数，原样或轻参数化） | `_provenance, _display, _layout_from_contract, compile_candidate, _model_card, _make_model_input, _make_prompt, _make_request, _make_draft, _presentation_counts, _make_batch, build_regression_matrix, _make_gate, _contains_forbidden`（哈希/门禁/扫描实现依 §2.1 铁律随模块原样带走） |
| 新仓（重组入口） | `build_batch` 的纯计算尾段 → `compile_reviewed_fixture(fixture, *, source_fixture_sha256) -> Step3Compilation`（见下） |
| 旧仓留守 | `RUN_DIR / SOURCE_FIXTURE_PATH / REPORT_PATH`、`_freeze_paths`（glob phase2r8*）、`build_prior_freeze / verify_prior_freeze`、`_render_report`（中文验收报告）、`_junit_receipt` 调用点、`materialize_r9`、`FREEZE_VERSION` |

**新入口签名**：

```python
@dataclass(frozen=True)
class Step3Compilation:  # 与旧 Compilation 同构，去掉 root/source_fixture_path/freeze 三个盘面字段
    source_fixture_sha256: str
    contracts: tuple[Step3CardContract, ...]
    model_input: Step3ModelInput
    prompt: PromptContract
    request: OfflineRequestPlan
    draft: DraftBundle
    batch: BatchManifest
    regressions: tuple[RegressionResult, ...]
    gate: OfflineGate
    readiness: Readiness
    artifacts: Artifacts
    report: Report

def compile_reviewed_fixture(fixture: ReviewedTypedEvidenceLike, *, source_fixture_sha256: str) -> Step3Compilation:
    """纯计算：授权断言（fixture_authorized 且未越权）→ 逐 candidate compile → 汇编全套工件。不读盘、不做 prior freeze。"""
```

- 输入类型走 §8.1 的契约方案（gates-review 拥有模型，或 Protocol 兜底：candidate 仅用到 `candidate_id/subject_id/record_id/fact_id/evidence_id/anchor_ids/source_manifest_sha256/presentation/typed_contract/typed_contract_sha256`；fixture 仅用到 `fixture_authorized/cards_created/step3_authorized/step4_authorized/candidates`）。
- `build_regression_matrix` 对 fixture 内容的既有假设（`contracts[0]` 存在；缺 table/list/formula 族时回退 baseline）**保持原行为**并在 docstring 写明。
- 旧 `build_batch(root, source_fixture_path)` 在旧仓保持原样（它还要做 prior freeze 与授权链）；新仓入口只承担「已装载 fixture → 全套工件」这段。

### 4.2 `compilers/step3_response.py` ← phase2r92（45KB）

**画像**：Step3 严格响应契约：把「模型响应」限定为字节级精确的 OpenAI 信封（choices→message→content 为字符串化 JSON 文档），`inspect_response` 逐层校验并产出 12 项 `ResponseGate`，`build_regression_cases` 生成 17 个变异响应字节串。R9.1 失败响应仅作不透明证据。**是 providers 兼容层（r93–r97，4.2 范围）的共同底座**。

**符号去向表**：

| 去向 | 符号 |
|---|---|
| 新仓（模型） | `ResponseInputCard, ResponseModelInput, PromptContract, ResponseDraftCard, ResponseDocument, ResponseMessage, ResponseChoice, ResponseEnvelope, ResponseGate, OfflineGate, RequestIdentity, MachineReport, AcceptanceReport(结构), FORBIDDEN_KEYS` |
| 新仓（函数） | `strict_schema(→§2.2), encode_response, build_fake_result, _checks, inspect_response, build_request, build_regression_cases, canonical_json/sha256_*/_collect_keys`（依 §2.1 铁律随模块原样带走） |
| 新仓（新增适配器） | `make_inner_inspector(model_input) -> Callable[[bytes], ResponseGate]`：providers 稿 §6.3 约定其 `inspect_chat_envelope` 的 `inner_inspector` 回调由本模块提供（`ResponseGate` 含 valid/failure_codes 最小面），即 `inspect_response` 绑定 model_input 后的闭包。**接口已双方锁定**（2026-08-11）：providers 源码实测其转发形态为完整最小信封字节——r94 转发 `canonical_json({"choices": raw["choices"]})`（choices 原样）、r96 转发重投影信封（剥 transport-only 的 `finish_reason`，content 原文不动）——均可直接喂 `inspect_response`，单入口即可，无需文档层切片 |
| 新仓（参数化改造） | `build_model_input`：旧版读 R91 冻结运行且断言 `card_count==3`、`PRESENTATIONS==(table,ordered_list,formula)`（试点钉死）→ 新版 `build_response_model_input(cards: Sequence[...], *, source_fixture_sha256)`，卡数与表达族不限；`_checks` 中 relation 泄漏检测等逻辑不变 |
| 旧仓留守 | `CHECKPOINT_COMMIT` + `_checkpoint_is_ancestor`（git 依赖）、`_prior_targets/_target_files/build_prior_freeze/verify_prior_freeze`、`build_failure_evidence`（读 R91 失败记录）、`materialize_offline / finalize_acceptance`、`RUN_DIR/REPORT_PATH` 及 R91/R9 运行路径常量 |

**兼容注意**：r94/r96 直接 import `inspect_response/build_fake_result/ResponseEnvelope` 等（providers 范围）。providers 提炼时应改依赖新仓 `examcard.compilers.step3_response`——已在对齐消息中知会。

### 4.3 `compilers/step4_contract.py` ← phase2r4（69KB）

**画像**：从「已审定 Step3 统一发布 fixture」（r2242 YAML）+ R3.19 handoff 派生 Step4 通用图卡契约：`GenericCardContract`（visible_texts/semantic_layout_edges/table/list/token bindings/geometry）→ model_input → prompt/request → 确定性 fake artifact manifest → batch manifest → 离线门禁 + 回归矩阵 + 历史冻结审计。

**符号去向表**：

| 去向 | 符号 |
|---|---|
| 新仓（模型） | `GenericVisibleText, GenericSemanticEdge, GenericTableCell, GenericTableContract, GenericOrderedListItem, GenericOrderedListContract, GenericTokenBinding, GenericGeometry, GenericCardContract, Phase2R4ContractBundle, Phase2R4ModelInput, Phase2R4PromptContract, Phase2R4Request, Phase2R4FakeArtifact(+Manifest), Phase2R4BatchEntry(+Manifest), Phase2R4OfflineGate, Phase2R4RegressionResult, Phase2R4Report`（类名保持，schema_version 原样） + 常量 `SOURCE_PRESENTATIONS, RENDER_PRESENTATIONS, SOURCE_KINDS, FORBIDDEN_MODEL_KEYS, FORBIDDEN_TEXT, MARKERS(①–⑳), TOKEN_PATTERN, OUTPUT_WIDTH/HEIGHT` |
| 新仓（函数） | `_extract_tokens, _derive_visible_text, _derive_table, _derive_list, _derive_edge, _derive_card, _derive_contracts(参数化 fixture+handoff 哈希), _derive_model_input, _derive_request, _derive_fake_artifact, _derive_fake_manifest, _derive_batch_manifest, _no_forbidden_model_keys/_no_forbidden_text（依 §2.1 铁律随模块原样带走）, build_regression_matrix, assess_gate 中与盘面无关的检查段` |
| 新仓（参数化） | `_derive_fake_artifact` 的文件名前缀 `"gonglu-step4-card-…"` → `filename_prefix` 参数（默认保留原值以保哈希；新科目传新前缀）；`threshold→constraint` 的表达映射保持 |
| 旧仓留守 | `FROZEN_R33_R319_HASHES / FROZEN_ARTIFACT_COUNT / HISTORICAL_IDENTITY_COUNT / _historical_identities / audit_frozen_history`（gonglu 历史专属）、`_read_fixture`（锁 reviewed inventory=(2,3,4,6) 与 EXPECTED_* 数量）、`R319_*` 路径、`RUN_DIR` 等 11 个输出路径、`materialize_phase2r4`、`assess_gate` 里的「重算期望束并与传入束比对 + freeze 审计」外壳 |

**新入口**：`derive_step4_bundle(fixture, handoff, *, unified_fixture_sha256, handoff_sha256) -> (bundle, model_input, request, fake_manifest, batch)`；门禁改 `assess_offline_gate(bundle, model_input, request, artifacts, batch, *, freeze_valid: bool, expected: ExpectedCounts)`，把「期望数量」做成参数对象（旧仓传 EXPECTED_CARD_COUNT=6 等）。

**输入类型**：`Step3UnifiedReviewedReleaseFixture / Step3MergedReviewedReleaseCard / Step3MergedSourceOwnedDisplayText`（r2242）与 `Step4PilotHandoffRefreshR319`（r319）均属 4.3 范围 → §8.1。

**来自 providers 桶的边界移交（其稿 §2.4/§5 判定）**：r34/r36/r311/r42 的卡片特定 prompt/几何契约（Card2 exact-label 与 `Step4Card2ExactLayoutEdge` 拓扑边、Card6 边框几何族 coordinate/bounding_box/grid_cell/border_segment、Card14 公式源文本保真）不归 providers。本桶处置：这些是**钉死在具体 gonglu 卡片上的契约实例**，按 §1.2 切分原则 v1 **留旧仓**、不提炼；其通用形态在 v1 已由本模块 `Generic*` 族覆盖（表格网格含 row/column span、`GenericSemanticEdge` 承载拓扑、`GenericTokenBinding` 承载 exact-label/公式 token 保真）。边框段/像素级 bounding box 是 Generic 族尚未包含的表达——登记为 **v2 几何扩展素材**（参考实现留旧仓可读），若新科目出现同类需求，扩展 `GenericGeometry/GenericTableContract` 时以 r311/r36 为蓝本。

### 4.4 `compilers/step4_image.py` ← phase2r11（47KB）

**画像**：从 R10.4 授权的 reviewed Step3 fixture 派生 Step4 图像离线请求：`SourceCard`（源卡投影+五组哈希锚）→ `PromptCard` → 确定性 prompt 渲染 → `ImageRequest` → 确定性 fake backend（只产元数据、不产图、不可授权真图/批量）→ `validate_response` 精确回环校验 → 23 条回归。

**符号去向表**：

| 去向 | 符号 |
|---|---|
| 新仓（模型） | `SourceCard, ModelInput, PromptCard, Prompt, ImageRequest, FakeResponseCard, FakeResponse, CacheRecord, JournalRecord, OfflineGate, MachineReport, AcceptanceReport(结构)` + `PROMPT_INSTRUCTIONS`（默认值）+ ID 正则组（前缀含 `:r11:`，原样保留） |
| 新仓（函数） | `_source_card, _prompt_card, _render_prompt, _response_schema_sha256, _fake_response_card, deterministic_fake_backend, validate_response, parse_strict_response, _validate_prompt, _mutated_response, build_regression_matrix`，以及 `build_batch` 中「fixture→cards→prompt→request→response→regressions→gate」的纯计算段 → `compile_image_batch(cards: Sequence[ReviewedStep3CardLike], *, fixture_sha256) -> ImageBatch` |
| 旧仓留守 | `_frozen_paths`（锁 `*phase2r10*.py` 等）/`build_freeze`/`verify_freeze` 的路径集、`load_inputs`（读 R10.4 acceptance/gate/freeze 并断言授权链）、`SOURCE_FIXTURE_PATH / R104_* / RUN_DIR / REPORT_PATH`、`SCHEMA_FILENAMES`（新仓改自动发现后以数量断言替代）、`materialize_r11` |

**输入类型**：`ReviewedStep3Card / ReviewedStep3Fixture`（r10，4.3 范围）→ §8.1；其 `contract` 字段就是新仓 `Step3CardContract`，天然闭环。

---

## 5. release gate 提炼设计（清单 4.4）

### 5.1 `gates/release/readiness.py` ← phase2r15（43KB）+ phase2r152（20KB）

**画像**：r15 对「五个已持久化的 V25/V25B 真实运行」做只读发布就绪审计：传输审计（request/record/cache/journal 四件套一致性 + journal 起止事件配对）、fact/card dispositions、V25B bridge 审计、冻结审计、聚合门禁（唯一请求身份 / 无未知字段 / 无伪造 anchor / 无跨源合并 / 无水印泄漏）。r152 是 r15 的**刷新**：冻结前一 bundle + 校验 gonglu_24 补救记录后，仅替换该样本的审计输入重跑同一套审计（它直接 import r15 的 `_audit_sample/_audit_stage_transport/_aggregate_gate`）。

**提炼判断：r15 与 r152 合并为一个数据驱动模块**，「刷新」不再是新文件而是换参数重跑。

| 去向 | 符号 |
|---|---|
| 新仓（模型） | `Phase2R15TransportAudit, Phase2R15FactDisposition, Phase2R15CardDisposition, Phase2R15BridgeAudit, Phase2R15SampleAudit, Phase2R15ReleaseReadinessReport`（及 r152 的 PreviousBundleAudit/RemediationAudit 结构）+ `SampleSpec` dataclass（字段：sample_id / step2a_report / step2b_report / expected_obligation_count / expected_projected_fact_count / expected_card_count / expected_diagram_nodes / expected_diagram_edges） |
| 新仓（函数） | `_expected_response_fields, _audit_stage_transport, _projection_fact_ids, _fact_dispositions, _gate_valid, _audit_v25b_bridge_from_document, _audit_sample(root, spec), _aggregate_gate, build_release_readiness_report(root, specs)`（SAMPLE_SPECS 由调用方传入）；`_atomic_write_json →桶内 _support.py（§2.1）`；常量 `_ALLOWED_FACT_DISPOSITIONS, _ALLOWED_STAGE2A/2B_PROPOSAL_FIELDS, _FORBIDDEN_MODEL_INPUT_KEYS`（V25 通用契约面） |
| 新仓（参数化） | r152 的 `_audit_remediation_document`：机制通用（禁用历史请求身份、逐门禁校验、网络调用预算）→ `audit_remediation_document(document, *, expectations: RemediationExpectations, forbidden_request_ids)`；钉死值（network_calls==4、obligation 42、projections 11、card 4、样本名 gonglu_24）打包成旧仓侧的 expectations 常量 |
| 旧仓留守 | 五金样本 `SAMPLE_SPECS` 常量表、`REPORT_NAME/SCHEMA_VERSION`、`_PREVIOUS_BUNDLE/_REMEDIATION_REPORT/_REMEDIATION_OWNED_PATHS` 路径、`_snapshot_frozen_inputs / _freeze_paths` 的具体路径集、`run_phase2r15_* / run_phase2r152_*`（含 storage 必须为空目录的断言） |

**新入口**：

```python
def build_release_readiness_report(*, repository_root: Path, specs: Sequence[SampleSpec],
                                    frozen_paths: Sequence[Path] | None = None) -> ReleaseReadinessReport: ...
def audit_remediation_document(document: Mapping, *, expectations: RemediationExpectations,
                                forbidden_request_ids: set[str]) -> RemediationAudit: ...
```

注意：`_audit_sample` 会二次读盘（step2b request document）——保留 `root` 参数，这个模块本质是**对持久化运行的审计器**，读盘是其语义，不强行纯化；但所有被读路径都由 spec 携带。

### 5.2 `gates/release/closure.py` ← phase2r98（37KB）

**画像**：Step3 确定性发布闭环：R9 编译器是 Step3 草稿的**唯一权威**；历史 provider 输出只作不透明失败证据（hash-only，不得读取/重建/修复）。产出 `DeterministicReleaseBundle`（6 卡 contract+artifact 成对）、`AuthorityPolicy`、非权威模型候选的隔离审计（`submit_non_authoritative_candidate`：候选不能索权、不能引入跨样本卡）、13 项门禁 + 13 条回归。

| 去向 | 符号 |
|---|---|
| 新仓·契约下沉（2026-08-11 与 gates-review 定，同步 admission_fixtures 实施者） | `DeterministicDraftCard, DeterministicReleaseBundle` → `knowledge/contracts/admission_fixtures.py`：被跨桶消费（gates/review 的 r10 队列），且内嵌 `Step3CardContract + FakeArtifact`（均已下沉）——留在 gates/release 会残存横向边；gates-review 稿 §5 契约 3 改为「经 admission_fixtures 引用」 |
| 新仓（模型，留 closure） | `AuthorityPolicy, NonAuthoritativeModelCandidate, CandidateAudit, PresentationCounts, OfflineGate, MachineReport, AcceptanceReport(结构)` |
| 新仓（函数） | `_source_checks, submit_non_authoritative_candidate, build_regression_matrix`，以及 `build_release` 的纯段 → `build_deterministic_release(compilation: Step3Compilation, *, source_fixture_path_label: str) -> (bundle, gate输入件)`（依赖 §4.1 新入口；门禁中的 r97-freeze / opaque-failure / r96-alignment 三项检查由调用方以布尔+detail 注入） |
| 旧仓留守 | `R97_CODE_FILES / build_r97_freeze / verify_r97_freeze`、`build_opaque_failure_evidence`（读 R97 运行）、`build_r96_alignment`（锁 3 代表卡与 R96 model-input 路径）、`RUN_DIR/REPORT_PATH/R96_INPUT/R97_*`、`materialize_release` |

**跨界**：R96/R97 属 providers（4.2）——r98 对它们只做冻结与对齐审计，新仓 closure 以「外部检查注入」解耦；旧仓 phase2r98 原样继续跑全套。已知会 providers 设计任务。

### 5.3 `gates/release/checkpoint.py` ← phase2r6（17KB）

**画像**：多科目发布检查点：审计「将要发布的通用源码集」域中性（禁科目标记/科目数据路径/遗留 phase import）、导出 schema 严格性（所有 object 节点 `additionalProperties:false` 且 properties 全 required 且根必含 schema_version）、合成四域样本重编译、fail-closed 回归、冻结审计 → `CheckpointManifest`。

| 去向 | 符号 |
|---|---|
| 新仓（模型+函数） | `ArtifactDigest, GenericSourceAudit, SchemaAudit, ReleaseGate, CheckpointManifest, Phase2R6Report(结构)`；`audit_domain_neutrality(root, paths, *, forbidden_markers)`（原 `_source_audit`，marker 集参数化）；`audit_strict_schemas(root, paths)`（原 `_walk_objects/_schema_audits`，**直接服务新仓自身的 schema 导出门禁**，配合骨架任务的「自动发现 BaseModel 导出」）；`_digest`、manifest 组装模式 |
| 旧仓留守 | `GENERIC_SOURCE_FILES / GENERIC_DOCUMENT_FILES / FORBIDDEN_GENERIC_MARKERS` 常量表、`_build_regressions + build_phase2r6_checkpoint`（依赖 archive 范围 phase2r5 的合成重编译，r5 不迁移）、`MANIFEST_PATH / materialize_phase2r6` |

**新仓自用建议**：新仓 CI 直接用 `audit_strict_schemas` + `audit_domain_neutrality`（forbidden_markers 换成新仓自己的科目隔离清单）做发布 gate——这是 4.4 提炼对新仓最直接的增值。

---

## 6. 版本字符串与哈希稳定性策略（红线）

1. `schema_version` Literal、ID 前缀、`PROMPT_INSTRUCTIONS`、prompt 文本、`FORBIDDEN_*` 集合、`OUTPUT_WIDTH/HEIGHT`：**逐字节原样带入新仓 v1**。
2. 等价性验收（迁移 DoD）：对每个模块，用旧仓 pinned fixture 做**双跑对照**——同一输入下，旧 phase 函数与新包函数的 `model_dump(mode="json")` 经 `canonical_json` 后逐字节一致：
   - step3_card：R810 fixture（`data/.../reviewed-typed-evidence-fixture.json`）→ 比 `Compilation` 全部工件哈希；
   - step3_response：`build_fake_result + encode_response + inspect_response + build_regression_cases` 全字节比对（纯内存即可）；
   - step4_contract：`UNIFIED_FIXTURE` yaml + R319 handoff → 比 bundle/model_input/request/fake manifest/batch；
   - step4_image：R104 reviewed fixture → 比 batch 全件；
   - closure/readiness/checkpoint：分别用 R9 compilation、五金样本运行、r6 现有输入做对照。
   对照脚本作为**一次性迁移验证工具**放本仓 `migration/verify/`（对 yijian2 只读；依 bc-b8b9aca6 产出路径纪律，任何产出不得写入旧仓），不进常驻 CI。
3. 新科目/新流水线需要新语义时，**只允许新增版本号**（如 `examcard_step3_card_contract_v2`），不改 v1 字符串。
4. **与 providers 桶版本策略的边界**（2026-08-11 对齐）：本红线只约束「有落盘哈希/黄金基线锚定的数据契约模型」；providers 对其新传输抽象（信封画像等）采用中性新版本串 + `distilled_from` 字段溯源旧 phase 版本串——两制不冲突。本桶 v1 的 `gates/release/closure` 不引用任何信封门禁版本串（r98 触及 R96/R97 的粘合全部留旧仓）；未来若需引用，一律经 `distilled_from` 溯源、不硬编码旧串。
5. **冻结哈希铁律**（总指挥公告 bc-b8b9aca6）：法定哈希/指纹实现逐字节随模块搬运，禁止折叠进 utils/hashing 或改调新实现（§2.1 执行口径）；哪怕一个字节的规范化差异（浮点 round / frozenset 排序 / separators / ensure_ascii）都会让全部 ID/缓存键/golden 整体作废——这正是本节双跑对照要防的事。

---

## 7. phase2r16 边界说明（归 gates-review，本文不重复设计）

- r16 产出 `V25ReviewedReleaseFixture`（YAML 人工终审 fixture）+ 审计报告；其 `V25ReleaseFact / V25ReleaseCard / ReleaseReviewState / FIXTURE_RELATIVE_PATH / _snapshot_frozen_phase_inputs` 被**现役 Step3 链**消费（r2 preflight / r21 / r22 / r223 / r224 / r2241）。
- 即：r16 的「发布 fixture 契约模型」是 4.1/4.4 之外整条 Step3 现役链的上游契约。gates-review 设计稿已把 r16 划入其 D 家族并以其为准（其 §5.5）；落点由其裁量（`gates/review/` 下或 `gates/release/fixture.py`），`_review_fact/_review_cards` 的机制（逐条/逐卡失败码校验）可提炼，`REVIEWER/REVIEWED_AT_UTC/_EXPECTED_SAMPLE_IDS` 钉死值留旧仓。
- 本文其余部分不依赖 r16 的最终归属；`readiness.py` 与 r16 的耦合只有 `_snapshot_frozen_inputs`（§5.1：快照机制随模块/杂项承载 + 旧仓路径集）。

## 8. 跨工作流契约与开放问题

### 8.1 编译器输入契约模型：总指挥终裁——下沉 `knowledge/contracts/admission_fixtures.py`

**终裁**（总指挥 2026-08-11 直裁，撤回此前双方的 Protocol 合意）：跨桶持久化数据契约统一下沉 `knowledge/contracts/admission_fixtures.py`（只收纯数据模型），compilers 与 gates 均**向下 import 具体类型**，零横向边；持久化契约要唯一具体类型，不用鸭子类型削弱静态保证。schema_version 字面量原样、自哈希字段名不变（gates-review 稿 §4「生产契约保真」条款继续适用于下沉后的模型）。

| 输入模型 | 旧仓定义 | 新仓落点（终裁） | compilers 消费方式 |
|---|---|---|---|
| `ReviewedTypedEvidenceFixture`（r9 输入） | phase2r810 | `knowledge/contracts/admission_fixtures.py` | 直接 import 具体类型（向下合规） |
| `Step3UnifiedReviewedReleaseFixture / Step3Merged*`（r4 输入） | phase2r2242 | 同上 | 同上 |
| `Step4PilotHandoffRefresh`（r4 输入；类名去 R319 后缀——类名不参与序列化，schema_version 字面量不动，**哈希安全**） | phase2r319 | 同上 | 直接 import；r4 新入口仍只需 `handoff_sha256 + prior_reviewed` 两参 |
| `ReviewedStep3Fixture/Card`（r11 输入） | phase2r10/r103/r104 | 同上（其 `contract` 字段即同文件 `Step3CardContract`） | 直接 import |
| `Step3CardContract` 同族（本桶 r9 产出、gates queue 消费） | phase2r9 | 同上（自本桶 §4.1 下沉） | compilers 与 gates 都从此处 import |
| r104 `AcceptanceReport / OfflineGate / PriorFreeze+FrozenArtifact`（r11 装载校验） | phase2r104 | `knowledge/contracts/admission_fixtures.py`（gates-review 2026-08-11 同步：证物三型亦随下沉）；自哈希校验绑 `admission_legacy_hash` r10x 变体 | **不消费**：装载与授权链校验留旧仓/gates 侧，`compile_image_batch` 只收已装载对象 |

实施约束：① 下沉模型的字段形态以 gates-review 稿 §4 保真清单为准；② 自哈希校验统一走 `knowledge/contracts/admission_legacy_hash.py`（r8x + r9/r10x 两变体逐字节原样并存；放 knowledge 层避免 knowledge→gates 向上引用；r10 的 canonical_hash 旧仓即 import 自 r9——同源同变体，一份覆盖 Step3CardContract 与其 r10x 家族）；③ `admission_fixtures.py` 只收纯数据模型（含自哈希 validator），判定逻辑留 gates 桶、编译逻辑留本桶；④ S1 阶段两桶共建该模块：gates-review 供 fixture 族、本桶供 Step3CardContract 同族（写入前 acquire_write_lock 串行）；⑤ r16 完整设计归 gates-review D 家族、本文仅边界说明——双方一致。历史脉络备考：此前双方曾合意 B 案（Protocol 单侧变体），被本终裁替代；Protocol 方案留档为纯备忘，不再是备选。

### 8.2 其他对齐项

- **骨架**（zhishika·P1）：已落地 `docs/engineering_skeleton.md`——`compilers/`、`gates/release/` 落点与依赖方向、测试隔离规范 T1–T8 均以骨架为准；底座布局依终裁 bc-888c6031（common 冻结底座 + utils 非冻结杂项并存，providers 顶层），骨架同步修订。本文已按其改齐。
- **gates-review（三接口点，已按终裁收敛）**：① 底座共用——common 终态六件（§2.2），gates-review 同步改为消费 common/io+freeze，法定指纹实现按铁律随模块（§2.1）；② `Step3CardContract` 同族依终裁下沉 `knowledge/contracts/admission_fixtures.py`，两桶都从此 import（原「compilers 顶层导出」相应调整为 compilers 从下方 import + 门面 re-export 可选）；③ `DeterministicDraftCard / DeterministicReleaseBundle` 依 2026-08-11 双方合意随契约下沉 `admission_fixtures`（§5.2），gates-review 稿 §5 契约 3 改为「经 admission_fixtures 引用」；r2242 确定性发布草稿与就绪判定的衔接以其稿 D 家族为准。
- **providers**（P0）：r92 是 r93–r97 底座；r98/r152 冻结 r97/r151 字节 → providers 提炼同样必须保持旧仓脚本本体不动（已发消息知会）。其设计稿 §6.3 约定 `inspect_chat_envelope` 的 `inner_inspector: Callable[[bytes], InnerGate]` 由本桶 step3_response 提供（§4.2 `make_inner_inspector`）；providers 决不反向 import compilers 之外的方向约束与本文一致。providers 包层级已依终裁 bc-888c6031 为顶层 `examcard.providers.*`（只可 import common+utils）。版本串策略边界见 §6.4。
- **StrictModel 基类**：骨架与 providers 稿已冻结落点 `examcard.materials.schema.StrictModel`，本文直接采用。
- **schema 导出**：新仓改自动发现 BaseModel 导出后，r11 的 `SCHEMA_FILENAMES` 数量断言替换为「自动发现清单快照 + 数量」断言；`gates/release/checkpoint.audit_strict_schemas` 直接对导出产物做严格性 gate。
- **任务书 vs 清单差异**：4.4 任务书点名 r16、清单列 r98。本文并集覆盖（r98 全量、r16 边界），请总指挥在汇总时确认 r16 的设计归属维持在 gates-review。

## 9. 测试与迁移步骤

### 9.1 测试映射

清单第六节把 9 个模块的伴生测试全部归为「留旧仓（仅引 phase 脚本）」。新仓按新 API 重建单测（以旧测试为蓝本改造）：

| 新仓测试 | 蓝本（旧仓） | 说明 |
|---|---|---|
| `tests/test_compilers_step3_card.py` | `test_phase2r9_generic_step3_card_compiler.py` | fixture 改为内联构造/移植的合成 ReviewedTypedEvidence 样本；15 条回归全保 |
| `tests/test_compilers_step3_response.py` | `test_phase2r92_generic_step3_response_contract.py` | 纯内存，最易迁移；17 个变异响应字节断言全保 |
| `tests/test_compilers_step4_contract.py` | `test_phase2r4_step4_generic_contract_compiler.py` | 冻结审计相关用例留旧仓；派生/门禁/回归用例迁移 |
| `tests/test_compilers_step4_image.py` | `test_phase2r11_...py` | 23 条回归全保；装载/授权链用例留旧仓 |
| `tests/test_gates_release_readiness.py` | `test_phase2r15_*.py + test_phase2r152_*.py` | 用合成 spec + 合成运行目录 fixture（走 T3 `repo_tmp_path`）；五金样本实跑留旧仓 |
| `tests/test_gates_release_closure.py` | `test_phase2r98_...py` | R97/R96 相关用例留旧仓 |
| `tests/test_gates_release_checkpoint.py` | `test_phase2r6_...py` | 用新仓自身源码/schema 作为审计对象 |

命名与隔离遵守骨架 T1–T8（按被测子包命名、只写 `tmp_path`/`repo_tmp_path`、默认禁网；本桶全部用例离线，无需 live 标记）。

### 9.2 迁移顺序（依赖驱动）

1. 底座：`examcard.common` 六件落地（hashing/gates/io/freeze/junit/schema，§2.2）；**注意 bc-b8b9aca6**：法定哈希/门禁/回归实现随各模块原样带走、不走 common
2. `knowledge/contracts/admission_fixtures.py`（跨桶契约下沉，与 gates-review 共建：其 fixture 族 + 本桶 Step3CardContract 同族；含随行 canonical 实现）
3. `compilers/step3_card.py`（枢纽；契约自 admission_fixtures import，编译逻辑+批工件模型在本模块）
4. `compilers/step3_response.py`（依赖 step3_card 的 ModelLayout 等；同时交付 providers 要的 `make_inner_inspector` 适配器）
5. `gates/release/closure.py`（依赖 step3_card 的 Step3Compilation）
6. `compilers/step4_contract.py`、`compilers/step4_image.py`（输入自 admission_fixtures，可与 4/5 并行）
7. `gates/release/readiness.py`、`gates/release/checkpoint.py`（相互独立，可并行）
8. 每步跑 §6 双跑对照 + 新仓单测（骨架冒烟 `tests/test_skeleton.py` 始终绿）；全部完成后旧仓抽查 `verify_*_freeze` 族与全量 pytest，确认零漂移。

## 10. 风险清单

| 风险 | 缓解 |
|---|---|
| 改名/重排字段导致 canonical_hash 漂移 | §6 红线 + 双跑对照 DoD；v1 禁改任何参与哈希的字符串 |
| ~~小件（io/junit/freeze 等）落位各方解读不一~~（已批复消解） | 总指挥直裁三项全批 + schema 新模块，common 终态六件（§2.2）；gates-review 同步消费 common/io+freeze |
| ~~跨桶契约消费方式分歧~~（已终裁消解） | 总指挥直裁：契约下沉 `knowledge/contracts/admission_fixtures.py`，两桶向下 import（§8.1）；Protocol 方案撤回留档 |
| admission_fixtures 契约下沉的实施协作（与 gates-review 共建一个文件） | 明确分工：其 fixture 族由 gates-review 迁、Step3CardContract 同族由本桶迁，schema_version/自哈希字段保真互审；写入前 acquire_write_lock 串行 |
| 公共原语各来源实现存在微差（如 `_fail_closed` 捕获异常集、detail 截断） | 依 bc-b8b9aca6 铁律 v1 不再收敛：各模块原样带走自己的实现，微差保持原状；合流是平价验证后的别名化阶段问题 |
| 旧仓脚本被误改击穿冻结链 | 迁移全程只读旧仓；完成后跑冻结重验抽查（§1.1/§9.2-7） |
| r15 审计器对运行目录结构强耦合 | 保留 root+spec 读盘语义（§5.1），不强行纯化，合成 fixture 测试覆盖（T3 `repo_tmp_path`） |
