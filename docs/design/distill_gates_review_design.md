# gates/review（门禁与人工准入）提炼设计 — distill:gates-review

- 范围：`extraction_manifest.md` 第 4.3 节全部 27 个模块（1558KB，实测 29,744 行）
- 方法：全部 27 个模块的文件头 docstring + 顶层类/函数结构逐一勘察（只读），r84 权威决策模型与 r83 工作台渲染器精读
- 结论日期：2026-08-11；性质：提炼设计（阶段 2 执行依据），执行前无需再次全量勘察
- 提炼语义（沿用清单第四节定义）：**通用逻辑进新仓对应包，脚本本体仍留旧仓**。提炼是"逻辑复制 + 泛化"，不是"抽走共享"——旧仓 27 个脚本一行不改，新仓内核不 import 旧仓。

## 0. 核心结论

27 个模块不是 27 套逻辑，而是**同一套「人工准入内核」的 27 次实例化**。全部模块共享同一条设计公理链：

1. **两界原则**：浏览器工作台只是"检视 + 可续传草稿"表面，输出永远带 `browser_demo: true` / hash 占位符，**结构上不可能**被当作权威输入；只有单独撰写的、经严格校验的人工决策 manifest 才能准入。
2. **自哈希模型**：每个决策/审计/候选/清单模型都携带 `*_sha256` 字段，值 = 模型去掉该字段后的 canonical JSON 哈希，pydantic `model_validator` 内自校验，篡改即拒。
3. **冻结链（chain of custody）**：每期先对上游 run 产物逐文件 SHA-256 冻结（`FrozenArtifact` + `build/verify_freeze`），校验不过不运行。
4. **四态处置守恒**：`reviewed_candidate / manual_review / blocked / not_applicable`，非 reviewed 状态永不静默升格；`reviewed_candidate` 必须五重确认 + 完整 typed contract + 显式生产授权。
5. **fail-closed 回归矩阵**：每期物化时内联执行一组"必须失败"的负向用例（`RegressionResult`），任何一条意外通过则门禁 FAIL。
6. **门禁 + 验收报告**：`GateCheck/OfflineGate/AdmissionReadiness` 汇总判定，JSON 机器报告 + Markdown 验收报告 + JUnit 回执一体持久化。

实例化差异全部集中在五类常量：上游冻结路径/哈希、队列来源、typed contract 种类、身份前缀/schema_version、run 目录与报告路径。**这些常量全部留旧仓，一律不进新仓。**

重复度实测（27 模块中定义了本机制自有副本的模块数；未定义者均从兄弟 phase import，重复度实际更高）：

| 机制 | 自有副本 | 说明 |
|---|---|---|
| 报告/持久化（Report 族） | 27/27 | 每期一套 |
| 冻结审计（FrozenArtifact + build/verify freeze） | 23/27 | 其余 4 个复用兄弟期 |
| 门禁模型（GateCheck/OfflineGate 族） | 23/27 | |
| 哈希/IO 工具（canonical_hash/file_hash/write_json 等） | 20/27 | 逐份复制粘贴 |
| 回归矩阵（_fail_closed/build_regression_matrix） | 16/27 | |
| reviewed fixture 族 | 13/27 | |
| 工作台 HTML 渲染器 | 8/27 | r73/r8/r81/r83/r85/r87/r10/r102 |
| 权威人工决策/授权 manifest 族 | 7/27 | r84 为规范源 |

## 1. 家族全景（27 模块 = 6 个家族）

### A. typed Evidence 证据准入链（11 个）：r71 → r73 → r8 → r81 → r82 → r83 → r84 ← r85 ← r86 ← r87 / r810

| 模块 | 角色 | 关键产物 |
|---|---|---|
| phase2r71_multi_subject_source_confirmed_evidence_admission | 队列源头：从 R7 普查建 provenance 绑定的准入队列，全部 manual_review/blocked | SourceRecordAdmission |
| phase2r73_generic_typed_evidence_review_queue | 通用评审队列 + 第一代工作台 + 可选准入路径 | ReviewQueueEntry、HumanReviewDecision |
| phase2r8_one_subject_typed_evidence_admission | 单科试点：人工 source-page 选择 → 首个 reviewed fixture | R8ReviewedFixture、R8FreezeAudit |
| phase2r81_multi_subject_typed_evidence_admission | 多科扩展：统一 fixture，R8 结果按引用嵌入 | UnifiedReviewedFixture |
| phase2r82_generic_review_decision_ingestion | 浏览器决策只作四态迁移审计，不可创建 fixture | QueueDisposition、DecisionAudit |
| phase2r83_generic_typed_evidence_review_workbench | 每科×每呈现 ≤1 代表；工作台含 typed contract 客户端结构预检 | SelectedReviewQueueItem、CandidateRecord |
| phase2r84_selected_batch_human_decision_ingestion | **准入内核规范源**：`phase2r84_human_decision_manifest_v1` 严格摄取（五确认 + typed contract + 逐元素 provenance） | HumanDecision(Manifest)、ReviewedFixture |
| phase2r85_production_human_source_page_decision_workbench | 双界拆分定型：browser_demo 审计 vs 显式 r84 manifest → r84 摄取 | WorkbenchCandidate、BrowserDemoManifest |
| phase2r86_r85_production_human_decision_explicit_export | 生产决策输入契约 → 转换为 r84 manifest → 内存中跑真 r84 校验器 | ProductionHumanDecision(Manifest) |
| phase2r87_production_source_page_human_decision_workbench | 生产工作台终态：可续传 raw 草稿 + 本地严格 normalizer → r86 manifest | RawProductionReviewDraftManifest、Progress |
| phase2r810_r89_reviewed_admission_authorization | 对冻结 R8.9 候选的显式授权 → r84 manifest → 内存严格摄取；处置守恒 | ProductionAuthorizationManifest、ReviewedTypedEvidenceFixture |

### B. Step 3 生产准入链（4 个）：r10 → r102 → r103 → r104

| 模块 | 角色 |
|---|---|
| phase2r10_generic_step3_human_review_admission | 从 r810 fixture + r98 确定性 bundle 建 Step3 评审队列；严格人工决策 manifest → ReviewedStep3Fixture |
| phase2r102_production_step3_human_decision_session | 生产授权会话：浏览器只产可续传 raw 草稿，必须经 r103 摄取 |
| phase2r103_step3_production_raw_decision_ingestion | raw 决策严格摄取：逐源绑定审计，拒绝从草稿制造生产授权 |
| phase2r104_step3_explicit_production_authorization | 显式生产授权逐条绑定冻结 raw 决策，全链重校验 → 标准 reviewed Step3 fixture |

### C. Step 4 图片人工目检准入（1 个）

| 模块 | 角色 |
|---|---|
| phase2r11101_step4_human_visual_review_strict_admission | R11.10 真图逐张人工目检：SourceImageBinding（含 PIL 尺寸校验）+ 授权 manifest + 追加审计日志（journal） → ReviewedImageFixture |

### D. Step 3 / 发布终审与合并（4 个）

| 模块 | 角色 |
|---|---|
| phase2r12_v25_golden_review | V25 golden 人工评审 + 语义 sidecar 闭合（与 continuation_review/diagram 核心深耦合） |
| phase2r16_final_release_review | 五个 V25/V25B run 的独立终审 → reviewed-release fixture（YAML） |
| phase2r224_step3_final_review | 真实 Step3 草稿人工终审 → Step3ReviewedReleaseFixture |
| phase2r2242_step3_reviewed_release_merge | 两个 reviewed 层级合并 → 统一 fixture + 确定性发布草稿 |

### E. Step 4 卡片 reviewed image fixture + pilot handoff（4 个，同构模板）

| 模块 | 卡片/呈现 |
|---|---|
| phase2r38_step4_card2_reviewed_image_fixture | Card2 / diagram（connector 拓扑绑定） |
| phase2r313_step4_card6_reviewed_image_fixture | Card6 / table（网格坐标/单元格绑定） |
| phase2r316_step4_card3_reviewed_image_fixture | Card3 / ordered_list（条目绑定） |
| phase2r319_step4_card4_reviewed_image_fixture | Card4 / constraint（块绑定） |

四者结构完全同构：`audit_frozen_history → make_final_review_audit → build_reviewed_fixture → build_pilot_handoff(+ _forbidden_handoff_fields) → assess_gate → build_regression_matrix → materialize`。

### F. 真实科目 onboarding 审计链（3 个）：r52 → r53 → r54

| 模块 | 角色 |
|---|---|
| phase2r52_fagui_reviewed_fixture_audit | 遗留结构化记录 vs 抽取页对账 → source-confirmed 候选（全部保持未评审）；legacy 锚点只记观察 |
| phase2r53_fagui_reviewed_v2_evidence | 显式本地源复核 → ReviewedV2EvidenceFixture（仅 Facts + 文本锚点） |
| phase2r54_fagui_typed_presentation_admission | typed 呈现准入审计：只审"显式 typed 证据是否存在"，legacy hint 永不升格 |

## 2. 目标包设计：`src/examcard/gates/review/`

> 落位与底座依据（2026-08-11 布局终裁 bc-888c6031，替代此前全部布局公告）：包路径承接工程骨架规范（`docs/engineering_skeleton.md` §3）——manifest 4.3 → `gates/review/`、4.4 → `gates/release/`；新仓顶层十包，底座两包并存：**`common/` = 冻结底座（hashing + gates 两件，签名按 providers 设计稿 §6.1/§9.1 冻结，变更报总指挥）**，**`utils/` = 非冻结杂项（io/freeze 等归此）**。依赖方向：common/utils 为叶包，本桶可 import common、utils、providers。

```
src/examcard/
  common/                    # ★冻结底座（终裁 bc-888c6031 + 总指挥补充裁决扩容为六件，落地即冻）
    hashing.py               # canonical_json / canonical_hash / hash_model（签名按 providers 稿 §6.1 冻结）
    gates.py                 # GateCheck + fail-closed 回归矩阵定式（签名按 providers 稿 §9.1 冻结）
    io.py                    # ★本桶供源（r84/r810 规范源，已落地 2026-08-11）：atomic_write_json（r16 形态+sort_keys）/ atomic_write_yaml（r224 形态）/ read_json / load_model / append_jsonl / relative / resolve（r810 形态）/ png_dimensions；write_json 为原子实现别名；哈希命名冻结为 sha256_* 族（sha256_text 等，签名按 providers §6.1）
    freeze.py                # ★本桶供源（r810/r102/r103 规范源，已落地）：FrozenArtifact(role 字段，桶词汇由调用方传入) / build_freeze / verify_freeze / diff_freeze / dedupe_paths（大小写不敏感、末者胜）——守护测试断言零 glob/零 git/零 phase 字面量
    junit.py                 # JUnit 回执解析助手（compilers 举证入列）
    schema.py                # strict_schema 规范化导出（compilers r92 血统）
  knowledge/
    contracts/
      admission_fixtures.py  # ★总指挥 A 案裁决（2026-08-11）：跨桶消费的纯数据契约模型唯一权威定义——ReviewedTypedEvidenceFixture（r810）、ReviewedStep3Fixture/ReviewedStep3Card（r103/r104）、r2242 合并三型、Step4PilotHandoffRefresh（r319 规范）、Step3CardContract（compilers 供源）及 r104 运行证物三型（AcceptanceReport/OfflineGate/PriorFreeze）；gates 与 compilers 均向下 import，零横向边。约束：只收纯数据模型（含其自哈希 model_validator），判定/校验编排/IO 一律不入；不碰 Step2 法定哈希冻结区
      admission_legacy_hash.py # ★法定哈希逐字节搬运件随契约下沉（铁律 bc-b8b9aca6 + A 案层级要求）：r8x 变体（canonical_json 带 normalize 预排序 + _hash_model 不排除 schema_version）与 r9/r10x 变体（_jsonable 不预排序 + _hash_model 排除 schema_version）两份并存；admission_fixtures 与 gates/review 桶内模型的自哈希校验、旧产物回放只走本模块，禁用 common/hashing 替代（合流须黄金重放逐字节平价，非阶段 2 范围）。r10 的 canonical_hash 系从 r9 编译器 import 的同一实现，故 r9/r10x 为同一变体，Step3CardContract 一并覆盖；r98 的 DeterministicReleaseBundle.release_sha256 经清单 v2 成员源码实证亦为 r9/r10x 变体（2026-08-11），两变体足以覆盖全部下沉模型、无第三变体
  gates/
    review/
      __init__.py
      dispositions.py        # 四态 Literal + DISPOSITIONS + admission_basis 枚举 + 处置守恒断言
      queue.py               # ReviewQueueItem/ReviewQueue 基类 + queue_sha256 计算与链绑定校验
      contracts.py           # TypedElementProvenance + TypedTextBase + 五种 typed contract（Diagram/Table/OrderedList/Constraint/Formula）
      decisions.py           # HumanDecision/HumanDecisionManifest 基类：自哈希、确认项集合、身份前缀、queue 哈希链、"非 reviewed 不得携带授权契约"
      authorization.py       # 显式生产授权层：AuthorizationBinding / ProductionAuthorizationManifest / 逐条绑定冻结 raw 决策
      browser.py             # 非权威浏览器界：RawDecision(Manifest) / BrowserDemoManifest / normalize_raw_draft / Progress（browser_demo 恒真、hash 占位符协议）
      audit.py               # DecisionAudit 基类 + admission_basis 推导 + "browser demo 不可授权"不变量
      fixtures.py            # reviewed fixture 基类族 + 桶内专有具体契约（ReviewedCandidate/ReviewedFixture、ReviewedImageFixture 等不跨桶消费者）+ cards_created=False 等禁授权不变量；跨桶消费的具体模型按 A 案引用 knowledge.contracts.admission_fixtures
      release_merge.py       # 双层 reviewed 合并**判定与生成逻辑**：合并审计 / 层级对账 / 确定性发布草稿生成（源：r2242）；SourceTier 与合并 fixture 数据模型在 admission_fixtures
      image_admission.py     # 图片目检准入：SourceImageBinding（尺寸/哈希校验）+ 逐张决策 + journal 追加审计
      handoff.py             # pilot handoff 构建与 forbidden_handoff_fields 校验逻辑（handoff manifest 数据模型在 admission_fixtures）
      regression.py          # 基于 common.gates 定式的准入回归矩阵组装（RegressionResult 桶内形态 + 负向用例族骨架）
      gate.py                # OfflineGate / StrictAdmissionGate / AdmissionReadiness 等准入聚合判定（GateCheck 基元来自 common.gates；聚合体属桶特有类型故留桶内）
      report.py              # MachineReport / AcceptanceReport / JUnitReceipt + persist / materialize 骨架
  webui/
    review_workbench.py      # 工作台 HTML 内核（原 §2 workbench.py）：队列 JSON 注入、过滤侧栏、源页图检视、决策表单、decisions JSON 导出。纯函数生成静态单文件 HTML、无 flask 依赖；按骨架"gates 只承载判定逻辑、webui 承载 Web 层"归入 webui，webui → gates 为合法向下依赖
```

各构件的**规范源**（同一机制取链路最晚、生产化终态的版本）：

| 新仓构件 | 规范源 | 次要参考 |
|---|---|---|
| knowledge/contracts/admission_legacy_hash（逐字节搬运，非提炼） | r84/r810（r8x 变体）+ r9→r10/r11101（r9/r10x 变体）**原样两份** | 勘察实证：两变体 `_hash_model` 排除语义不同（r10x 排除 schema_version、r8x 不排除），canonical_json 规范化路径不同 |
| knowledge/contracts/admission_fixtures（A 案下沉） | 各跨桶模型取其生产终态期（r810/r103/r104/r2242/r319/r9） | §4 清单为准 |
| common/hashing（冻结底座，本桶只引用且**仅限新产出**） | 签名按 providers 设计稿 §6.1 冻结 | 旧产物校验一律走 admission_legacy_hash（铁律 bc-b8b9aca6） |
| common/io（本桶供源，补充裁决入 common） | r84 / r810（最全） | r16/r224 的 atomic_write_yaml |
| common/freeze（本桶供源，补充裁决入 common） | r810（`_dedupe_paths` + 多来源合并） | r102/r103（kind 标注：run_artifact/code/schema）；通用形态不含 phase glob、不依赖 git |
| gates/review/queue | r83 SelectedReviewQueueItem + r10 Step3ReviewQueue | r73 第一代 |
| gates/review/contracts | **r84**（TypedElementProvenance + 五 contract） | r83 同构；r8/r81 payload 族标注为历史形态，不提炼 |
| gates/review/decisions | **r84 HumanDecision/HumanDecisionManifest** | r10 Step3HumanDecision |
| gates/review/authorization | r104 + r810 | r86 |
| gates/review/browser | r87（raw 草稿 + normalizer + Progress） | r102、r85 BrowserDemo |
| gates/review/audit | r84 DecisionAudit（admission_basis 全集） | r82 |
| gates/review/fixtures | r84 ReviewedFixture + r103/r104 ReviewedStep3Fixture + r11101 ReviewedImageFixture | r53 ReviewedV2EvidenceFixture |
| gates/review/release_merge | r2242（SourceTier + 统一 fixture + 确定性发布草稿） | r224 |
| gates/review/image_admission | r11101 | — |
| gates/review/handoff | r313/r316/r319（refresh 形态） | r38（初代） |
| gates/review/regression | r10x 家族 `_fail_closed`（定式基元引 common.gates） | r8x 家族 `_regression` |
| gates/review/gate | r84/r10 OfflineGate + AdmissionReadiness | r11101 StrictAdmissionGate |
| webui/review_workbench | **r83**（过滤 + contract 预检 + 导出最全） | r87（可续传草稿/进度） |
| gates/review/report | r10x 家族（MachineReport/JUnitReceipt/AcceptanceReport 最结构化） | r8x Report |

## 3. 逐模块处置表（27 个）

「提炼」= 该模块中值得进新仓的通用形态；「留守」= 该模块内不进新仓的 phase 专有物（除通例——RUN_DIR/REPORT_PATH/上游冻结哈希常量/验收报告路径——外仅列特有项）。

| # | 模块 | 提炼进新仓 | 特有留守物 |
|---|---|---|---|
| 1 | phase2r71 | 队列源头形态：provenance 绑定候选、页/锚证据模型 | R7 普查路径、科目 spec |
| 2 | phase2r73 | 第一代队列 + `validate_decision_against_entry`/`apply_decisions` 形态 | r72 冻结清单 |
| 3 | phase2r8 | 冻结审计 + 人工选择 spec → 候选构建流水线形态 | 单科选择 fixture、R8 身份前缀 |
| 4 | phase2r81 | 多科 choose/分组 + 统一 fixture（按引用继承）形态 | 九候选人工 fixture |
| 5 | phase2r82 | 浏览器决策分类 `classify_browser_decision` + 四态迁移审计 | r81 run 路径 |
| 6 | phase2r83 | **工作台内核规范源**；代表选择 `select_representatives`；typed contract 绑定校验 | BLOCKED_SUBJECTS、run 路径 |
| 7 | phase2r84 | **准入内核规范源**：决策/清单/审计/候选/fixture 全套 + 严格摄取校验器 + make_test_* 骨架 | r83 冻结清单、manifest 身份前缀常量 |
| 8 | phase2r85 | 双界拆分形态：WorkbenchCandidate + BrowserDemo 界 + 转交 r84 摄取 | r84 run 冻结 |
| 9 | phase2r86 | 生产决策输入契约 → 下游 manifest 转换 → 内存跑真校验器（`_write_temp_json` 模式） | r85 冻结清单 |
| 10 | phase2r87 | raw 草稿 normalizer + Progress + AuthorityBoundary 声明形态 | r86 冻结清单 |
| 11 | phase2r810 | 显式授权 manifest + 处置守恒投影 + `_dedupe_paths` 冻结合并 | r89 六候选绑定 |
| 12 | phase2r10 | Step3 队列 + Step3HumanDecision + strict_schema 导出 | r98/r810 冻结哈希、Step3CardContract 引用（见契约 2） |
| 13 | phase2r102 | 会话冻结（kind 标注）+ RawDecisionManifest + `validate_raw_manifest` | R101 run 路径 |
| 14 | phase2r103 | 逐源绑定审计 SourceBindingAudit + "拒绝从草稿造授权" | r102 run3 冻结 |
| 15 | phase2r104 | AuthorizationBinding：授权逐条绑定 raw 决策 + 全链重验 | RAW_DRAFT_EXPECTED_SHA256、Downloads 路径 |
| 16 | phase2r11101 | 图片目检准入全套：SourceImageBinding/journal/StrictAdmissionGate | R11.10 四个冻结哈希、1024×1536 尺寸常量 |
| 17 | phase2r52 | 对账审计形态：manifest 构建、锚点定位观察、`_record_source_confirmation` 处置推导 | fagui 数据路径、normalize 细节 |
| 18 | phase2r53 | 显式复核 → reviewed evidence fixture（仅 Fact+锚）形态 | r52 冻结 |
| 19 | phase2r54 | typed 呈现准入审计（存在性审计不升格）形态 | r53 冻结 |
| 20 | phase2r12 | 人工评审决策 + 评审基线（review_basis）形态；sidecar 闭合逻辑归核心包 | V25 golden 样本绑定 |
| 21 | phase2r16 | 终审固定形态：freeze before/after 快照 + Fact/Card 双层评审审计 + YAML fixture 物化 | 五 run 路径、V25 释放模型细节 |
| 22 | phase2r224 | 人工决策 fixture（Step3CardManualDecision）+ 逐卡评审审计 + 发布就绪对账 | r16/r22 引用、YAML 路径 |
| 23 | phase2r2242 | 双层 reviewed 合并 + 层级标注（SourceTier）+ 确定性发布草稿生成形态 | 两上游 fixture 路径 |
| 24 | phase2r38 | pilot handoff 初代形态 + `_forbidden_handoff_fields` | Card2 EXPECTED_* 常量 |
| 25 | phase2r313 | handoff refresh 形态（规范源之一） | Card6 EXPECTED_* 常量 |
| 26 | phase2r316 | 同上（ordered_list 绑定变体） | Card3 EXPECTED_* 常量 |
| 27 | phase2r319 | 同上（constraint 绑定变体） | Card4 EXPECTED_* 常量 |

## 4. 提炼规则

**参数化差异点**（旧仓的 phase 常量 → 新仓内核的构造参数/泛型参数）：

1. 身份前缀：`decision:r84:human:` / `manifest:r84:human:` / `decision:r83:ui:` → `identity_prefix` 参数，校验器按前缀断言"新鲜身份"。
2. `schema_version` Literal：每期一个字符串常量 → 版本参数；新仓基类用 `Literal` 泛型或 `ClassVar` 注入。
3. 确认项集合：证据准入是五确认（source_text/anchor/source_page/image_hash/typed_contract），图片目检是另一组 → `confirmations: tuple[str, ...]` 可配置，reviewed_candidate 必须全真。
4. 呈现集合：`("diagram","table","ordered_list","constraint","formula")` vs r11101 的 `("table","ordered_list","formula")` → 参数。
5. 队列哈希链：统一 `queue_sha256` 绑定协议（决策、manifest、审计、fixture 逐层携带并校验）。

**一律不进新仓**：`RUN_DIR`/`REPORT_PATH` 等 `data/semantic_llm_v2`、`docs/v2` 路径；上游冻结 SHA-256 字面量（如 r104 `RAW_DRAFT_EXPECTED_SHA256`、r11101 四个 SOURCE_*_SHA256）；`BLOCKED_SUBJECTS`、科目 spec；r38/r313/r316/r319 的 `EXPECTED_*` 卡片期望常量；各期 `make_test_*` 的具体实例（其骨架进 `regression.py`）。这些是历史台阶的证据，随脚本本体留旧仓。

**生产契约保真（schema_version 例外条款）**：`schema_version` 参数化只适用于内核**基类**。凡属"当前生产形态"的具体契约模型——已有持久化数据以其 schema_version 写盘、且被下游（compilers 等）消费——随内核**原样进新仓并保留历史 schema_version 字面量**（它们是数据格式标识，不是 run 路径常量；改掉会导致既有 reviewed 语料无法加载、回放验收失败）。

**法定哈希铁律（bc-b8b9aca6，凌驾于底座引用之上）**：上述具体契约模型的自哈希 `model_validator` 一律绑定 `knowledge/contracts/admission_legacy_hash.py` 中逐字节搬运的家族原实现——勘察实证本家族存在**两个不等价变体**（r8x：canonical_json 带 normalize 预排序、`_hash_model` 不排除 schema_version；r9/r10x：`_jsonable` 不预排序、`_hash_model` 排除 schema_version；r10 的实现系直接 import 自 r9 编译器，两者同源），必须按家族对号入座，禁止折叠进 `common/hashing` 或互换；`common/hashing` 仅供全新产出物使用。搬运件随 A 案契约下沉 knowledge/contracts（若留 gates/review 会迫使 knowledge→gates 向上引用，违反骨架层级）。合流仅允许在黄金重放逐字节平价通过后以别名方式进行，不属阶段 2 范围。

当前生产形态清单（按总指挥 A 案裁决落位）：
- `knowledge/contracts/admission_fixtures.py`（跨桶消费全集，gates 与 compilers 均向下 import）：`ReviewedTypedEvidenceFixture`（r810 形态，r9 编译器输入）；`ReviewedStep3Fixture`/`ReviewedStep3Card`（r10/r103/r104 形态，r11 编译器输入）；`Step3UnifiedReviewedReleaseFixture`/`Step3MergedReviewedReleaseCard`/`Step3MergedSourceOwnedDisplayText`（r2242 形态，r4 编译器输入）；`Step4PilotHandoffRefresh`（r319 形态为规范，r4 编译器输入）；`Step3CardContract`（r9 形态，compilers 供源、本桶 r10 队列消费）；`AcceptanceReport`/`OfflineGate`/`PriorFreeze`/`FrozenArtifact`（r104 运行证物形态，r11 编译器读取）
- `gates/review/fixtures.py`（桶内专有，不跨桶）：`ReviewedCandidate`/`ReviewedFixture`（r84 形态）、`ReviewedImageFixture`（r11101 形态）等
- `gates/review/report.py`：`MachineReport`/`JUnitReceipt`（r10x 形态，桶内运行报告基类；JUnit 解析助手引 `common/junit.py`）；`gates/review/gate.py`：`AdmissionReadiness`/`StrictAdmissionGate` 等聚合判定（`GateCheck` 基元来自已冻结的 `common/gates.py`）；新代码通用冻结机器引 `common/freeze.py`（本桶供源）

**不变量必须原样保真**（内核单测逐条覆盖）：

- browser 界输出（`browser_demo: true`、hash 占位符）在任何路径下都无法通过权威 manifest 校验；
- `reviewed_candidate` 缺任一确认/缺 typed contract/缺显式授权 → 拒；非 reviewed 处置携带授权契约 → 拒；
- 任何自哈希字段与内容不符 → 拒；跨 `queue_sha256` → 拒；
- 处置守恒：manual_review/blocked/not_applicable 在投影/合并中原样保留；
- handoff manifest 出现禁带字段 → 拒；
- 冻结校验不过 → 整期拒绝运行。

**依赖方向**：`gates` → {`common`, `utils`}；契约模型继承 `examcard.materials.schema.StrictModel`（清单第五节既定去向，骨架已冻结该路径）；common/utils 为叶包不 import 任何内部包。`gates` 不得依赖旧仓任何模块；旧仓 phase 脚本也不回接新仓。

## 5. 跨家族契约（对齐状态实时更新，2026-08-11）

1. **共享底座归属 — 终裁 bc-888c6031 + 补充裁决扩容（现行口径）**：新仓顶层十包，底座两包并存——`common/` = 冻结底座，补充裁决扩为**六件 {hashing, gates, io, freeze, junit, schema}**（compilers 举证 io/freeze/junit 重复 5/7/4 份获批；hashing/gates 签名按 providers 稿 §6.1/§9.1，其余落地即冻，变更报总指挥）；`utils/` = 非冻结杂项（本桶无供源项）。本桶为 io/freeze 的规范源供源方（r810/r102/r103 形态泛化；freeze 通用形态不含各 phase glob 集、不依赖 git）。common/utils 均为叶包；本桶可 import common、utils、providers、knowledge。
2. **gates ↔ compilers 类型依赖 — 总指挥已裁决采 A（终态）**：环的成因是 r10 消费 r9 的 `Step3CardContract`（gates→compilers）而 r9/r4/r11 编译器消费本桶 reviewed fixture 族（compilers→gates）。裁决：跨桶消费的纯数据契约模型下沉 `knowledge/contracts/admission_fixtures.py`（清单见 §4），两桶均向下 import，零横向边；理由是持久化数据契约必须有唯一具体类型权威定义，Protocol 鸭子类型会在最依赖严格校验/fail-closed 之处削弱静态保证。三条约束：只收纯数据模型（判定留 gates、编译留 compilers）；schema_version 字面量保真不变；不碰 Step2 法定哈希冻结区。此前与 compilers 临时合意的 B 单侧变体作废。
3. **gates ↔ release-gate — 随 A 案并入下沉集（已执行）**：r10 消费 r98 的 `DeterministicDraftCard` / `DeterministicReleaseBundle`（r98 形态、schema_version 原样，含 `DeterministicDraftCard` 内嵌持久化的 `FakeArtifact`）。该组类型同时被 compilers r11 跨桶消费，留在 `gates.release` 会残存 compilers→gates 横向边——经与 compilers/release-gate 设计者对齐一并下沉 `admission_fixtures`（其已直接同步执行者，含源文件行区间与保真要求），本桶 r10 队列改为向下 import；`DraftBundle` 属编译期容器、无跨桶持久化消费者，留驻 compilers；r2242 的"确定性发布草稿"输出形态与 release-gate 的就绪判定衔接不变。
4. **冻结审计归属 — 补充裁决入 common**：通用机器（FrozenArtifact/FreezeAudit/build/verify/dedupe）进 `common/freeze.py`（本桶 r810/r102/r103 为规范源供源，通用形态不含 phase glob、不依赖 git，落地即冻）；providers 桶自身不迁冻结机器（其 §4.2 决策不变）；r104 形态的 `PriorFreeze`/`FrozenArtifact` 运行证物模型因被 r11 编译器跨桶读取而随 A 案落 `admission_fixtures`；旧冻结产物自哈希重验走 `admission_legacy_hash`。
5. **跨桶导出面 — 按 A 案统一于 knowledge/contracts（已裁决）**：compilers 家族 4 个编译器的输入类型 + 本桶 r10 消费的 `Step3CardContract` 全部收敛为 `knowledge/contracts/admission_fixtures.py` 单一权威定义（清单见 §4），两桶直接 import 具体类型，无 Protocol。r16（phase2r16_final_release_review）在清单 4.3 归本家族，其完整提炼设计以本文档 D 家族为准；compilers/release-gate 侧只做边界说明，不重复设计。
6. **双向环解法 — 终态 = A 案（总指挥裁决，升级件闭环）**：跨桶纯数据契约下沉 admission_fixtures 后零横向边；随契约下沉的还有法定哈希搬运件 `admission_legacy_hash.py`（层级要求，见 §4）。执行侧影响：本桶与 compilers 均改为向下 import；清单 v2（extraction_manifest 修订）需在去向列补一行 admission_fixtures 注解——已按总指挥指派同步三方（compilers 设计者、清单 v2 成员、骨架侧）。

## 6. 迁移步骤与验收标准

分四步（可与其他家族并行；utils/ 底座已终裁，hashing/gates 签名冻结由先动工桶落地，io/freeze 由本桶按规范源供源）：

- **S1 契约下沉 + 法定哈希搬运 + 底座供源**：先落 `knowledge/contracts/admission_legacy_hash.py`（**逐字节搬运**两变体，配平价单测：同一载荷分别过 r8x/r9-r10x 变体断言与旧仓实现输出一致——用旧仓真实产物的已存哈希做黄金断言，不 import 旧仓）与 `knowledge/contracts/admission_fixtures.py`（跨桶纯数据契约，与 compilers 共建：本桶供 fixture 族、compilers 供 Step3CardContract）；向 `common/` 供源 `io.py` + `freeze.py`（补充裁决入列，落地即冻）；引用 `common/hashing`（仅新产出）、`common/gates`、`common/junit`、`common/schema`。
- **S2 schema 基类族**：dispositions/queue/contracts/decisions/authorization/browser/audit/fixtures/release_merge，逐条移植 §4 不变量单测（跨桶模型经 admission_fixtures 引用，桶内基类不阻塞）。
- **S3 执行内核**：regression/gate/report + `webui/review_workbench.py`。workbench 输出为参数化单文件 HTML（列、确认项、预检规则可配置），保持 UTF-8 中文文案。
- **S4 变体与回放**：image_admission/handoff + 三链回放验收（见下）。

**验收标准**：

1. **历史产物回放一致**：用新仓内核加载旧仓真实 run 产物并重跑校验——r84 链（queue + human manifest + reviewed fixture）、r104 链（raw 决策 + 授权 manifest + Step3 fixture）、r11101 链（图片绑定 + 授权 manifest + image fixture）——所有哈希、处置、判定与历史完全一致。
2. **fail-closed 负向套件全绿**：§4 不变量清单逐条有单测，且"必须失败"的用例意外通过时套件本身 FAIL。
3. **零旧仓耦合**：新仓 `gates`/`common`/`utils` 无任何 `knowledge_v2` import、无 `data/semantic_llm_v2`/`docs/v2` 路径、无上游冻结哈希字面量（`legacy_hash.py` 为逐字节搬运的代码副本，不构成 import 耦合；铁律 bc-b8b9aca6 要求项）。
4. **旧仓零改动**：27 个脚本与其 289 个伴生工具、93 个留守测试不动，旧仓测试仍绿。

## 7. 风险与开放问题

1. **typed contract 双版本**：r8/r81 的 payload 族与 r83/r84 的 contract 族同构不同名。取 contract 族为规范源；payload 族视为历史形态不提炼，回放验收只覆盖 contract 族链路。
2. **r12 耦合深**：其 sidecar 闭合逻辑属于核心包（continuation_review/diagram_* 已列核心迁移），gates 只提炼"人工评审决策 + review_basis"薄层；若执行时发现拆不干净，允许把 r12 整体重划为 archive（对 D 家族其余模块无影响）。
3. **r224/r2242 依赖 archive 期**（phase2r22 等）：提炼时只取形态、切断对 archive 类型的引用，用新仓 contracts 类型重表达；不得为此把 archive 期拖进新仓。
4. **工作台中文文案编码**：源文件为 UTF-8（勘察时 PowerShell 控制台显示乱码属终端编码问题，与文件无关）；新仓统一 UTF-8 + `ensure_ascii=False` 并加回归断言。
5. **体量预估**：内核约 4–5K 行（27 模块共 29.7K 行，约 85% 是留守的实例化常量与逐期重复）。
