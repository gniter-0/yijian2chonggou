# echo 新批 → step4 产品化衔接道设计稿（P8 补链·设计段，20260813）

- 使命：补齐 B1 实走定位的头号断头点 P8（`P3_B1_echo生产道实走报告_20260813.md` §3，e6c2379）——echo 生产道自 step3 v2 单卡回显后，**任意新批次在类型层面不可表达**，step4 出图结构性不可达。本稿只设计不施工（红线：零代码改动、零 live 花费；施工待用户审稿后另立卡）。
- 体例：仿 T4a 设计稿（`live_pipeline_driver_design.md`）——不变式先行、逐节契约、新增件清单、防误改清单、测试计划、对齐台账。

## 0. 设计不变式（违者返工）

1. **新增模块/新版本号，pilot 原件零改动**：r319 `Step4PilotHandoffRefresh`、r2242 `Step3UnifiedReviewedReleaseFixture`、`derive_step4_bundle` 及其全部 Literal 钉扎**一字节不动**（法定哈希/字节平价资产照旧可重放）；echo 批一律走新版本号家族。
2. **无自动晋升**：echo draft 卡 → reviewed 只能经人工评审决定文件发生（沿 §7 文件契约体例）；驱动没有任何自动晋升代码路径（T4a §7 既有红线的延伸）。
3. **检查点权威**：真人检查点归 B2 权威口径（总指挥 20260813 冻结令）——本稿新增的「step3 echo 批评审」检查点沿用同一签名纪律：评审签名以 **b2_review 取证块**为有效（B1 实走已建防伪造守卫先例），签字串不预公开。
4. **谱系四哈希贯穿**：echo 批 release 卡必须可回溯至 typed fixture → Step3CardContract → echo record → 评审签发四层哈希；驱动不得注入哈希（对齐 fe76237 转换器「四哈希在授权链内派生」原则）。
5. **写面纪律**：施工涉 `pipeline/live_run.py`（接缝）与 `knowledge/contracts/`（新模型）——派卡时须明确写锁 scope；`gates/review/`、`compilers/` 新增文件不与在飞任务重叠。

## 1. 断头点取证（P8 双缺，代码级）

| 缺口 | 证据 | 含义 |
|---|---|---|
| 驱动取材硬绑 | `live_run.py` `run_step4_stage(release_fixture_path, handoff_path, …)` L1038-1055：装载 `Step3UnifiedReviewedReleaseFixture` + `Step4PilotHandoffRefresh` 后直接 `derive_step4_bundle(fixture, handoff, …)` | step4 只吃 r2242 形状 |
| handoff 类型钉死 | `admission_fixtures.py` L1198-1217：`card_count: Literal[16]`、`reviewed_card_count: Literal[4]`、`source_step3_fixture_path: Literal[<r2242 字面量>]`、card2/3/4/6 逐卡布尔、三 presentation 泛化开关 `Literal[False]` | 新批（如 B1 的 3 卡 echo 批）**无法构造合法 handoff 实例** |
| derive 消费面 | `step4_contract.py` L980（`prior_reviewed` 来自 handoff.cards disposition）与 L1095（卡数+已评审下标互证） | 通用化需保留「先前已评审图跳过」与「卡数互证」两语义 |

## 2. 总体形态：五件一桥

```
echo live 产物（draft 卡 ×N，step3-response-draft-card/2.0，逐卡回显 record）
   │  ①〔评审物料〕emit_echo_step3_review_sheet → sheet（文件契约，§3.1）
   ▼
人工 step3 评审（B2 权威；b2_review 取证块签名；逐卡 approved/rejected/manual_review）
   │  ②〔摄取门禁〕ingest_echo_step3_review → released 集合（fail-closed）
   ▼
③ EchoBatchReviewedReleaseFixture（新版本号，N≥1 卡，§3.2）
   │
④ EchoBatchStep4Handoff（新版本号，授权语义显式，§3.3）
   │  ⑤〔装载接缝〕load_step4_bundle_source(source=echo-batch|pilot-release) → GenericCardContract ×N
   ▼
run_step4_stage 现有下游循环（投影/出图/信封门禁/良性漂移/预算/PNG 断点）一字不改复用
   ▼
step4-text-gate（1.1 分层）行级预筛 → 检查点②人工评审（§3.5）
```

关键判断：`GenericCardContract` 之后的一切（`project_image_request` 五字段投影、`inspect_image_envelope` 具名门禁、良性漂移剥离、预算护栏、PNG 断点续跑）已被 b2 彩排（16/16）与修复轨实弹验证——桥只需把 echo 批**送到 GenericCardContract 为止**，下游零新造。

## 3. 逐节契约

### 3.1 echo 批 step3 人工评审（文件契约，§7 体例）

- **物料生成**（新函数 `emit_echo_step3_review_sheet`，落 `gates/review/echo_step3_review.py`）：输入 = echo run 的逐卡 record（含 draft 卡回显、gate 结果、request_id）+ 源 `Step3CardContract`；产出 `echo_step3_review_sheet.json`：逐卡 {card_seq, contract_id, presentation, 源 typed fixture sha, echo record sha, 关键可见文本摘要, disposition:null, reviewer:null, basis:null}。
- **人工回填**：disposition ∈ approved / rejected / manual_review + reviewer + basis + **b2_review 取证块**（防伪造守卫复用 B1 实走件的校验手法；无有效取证块 = 摄取拒绝）。
- **摄取门禁**（`ingest_echo_step3_review`）：全卡有 disposition 才放行（undecided → waiting_human 语义）；approved 集合 = released；rejected → 回 echo 重发道（新身份重发，v2 契约既有四不变式）；manual_review → 终审记录（#02/#08 先例：可带瑕疵接受记档）。
- 与检查点①（准入）的关系：正交——准入门的是「step2 产物能否进 step3」，本检查点门的是「echo draft 卡能否晋升 release 进 step4」；两道都不绕过。

### 3.2 `EchoBatchReviewedReleaseFixture`（新模型，`knowledge/contracts/echo_release.py`）

- `schema_version: Literal["echo-batch-release/1.0"]`（新版本号家族，与 r2242 `R2242_FIXTURE_VERSION` 平行不相扰）。
- 顶层：`batch_id`（`echo-batch:<24hex>`）、`source_typed_fixture_path/sha256`（r810 形 typed fixture，转换器三件套产物）、`source_echo_run_id`、`review_sheet_sha256`（3.1 签发件）、`cards: tuple[EchoBatchReleaseCard, ...]`（**N≥1，无上限 Literal**）、计数互证 validator、`canonical_hash` 自证哈希（common.hashing 新产出域）。
- `EchoBatchReleaseCard` 字段映射（Step3CardContract → step4 所需投影）：

| GenericCardContract 需要 | echo 侧来源 | 备注 |
|---|---|---|
| source_card_id / card_index | contract_id / 批内序 | 谱系保留 contract_id 原串 |
| sample_id | contract.source_binding（typed fixture 样本） | |
| source/render_presentation | contract.presentation（五族 Literal 同集） | 双 presentation 初版同值 |
| visible_texts | contract.visible_elements（element_id+text 保序） | 词级门禁真值同源于此 |
| 结构枚举（NODES/EDGES/TABLE_GRID） | contract.layout（ModelLayout，presentation 特化） | prompt v2 硬性要求的输入（2335c4c 谱系） |
| fact_ids | contract.fact_ids | |
| 评审谱系 | review_sheet 行（reviewer/basis/取证块 sha） | 四哈希之第四层 |

- **四哈希谱系字段**（不变式 4）：`source_typed_fixture_sha256` / `source_contract_sha256` / `echo_record_sha256` / `review_signoff_sha256`——全部由 3.1→3.2 构建器在链内派生，构造函数不收外部哈希实参（fe76237 同原则）。

### 3.3 `EchoBatchStep4Handoff`（新模型，同文件）

- `schema_version: Literal["echo-batch-step4-handoff/1.0"]`；`batch_id` 与 release 互证；`source_release_sha256`。
- `card_count: int = Field(ge=1)`（**去 Literal 化的核心**）；`cards: tuple[EchoBatchCardDisposition, ...]` 计数互证；disposition ∈ pending / reviewed（承接 derive 的 `prior_reviewed` 跳过语义——**新批缺省全 pending、增量重渲批可携带旧图 reviewed 引用**，保留断点/增量道）。
- `reviewed_image_references: tuple[...] = ()`（缺省空；字段形状对齐 r319 的引用三元组：card_index/image_sha256/fixture 路径——供增量批跳过已评审图）。
- **授权语义显式化**：`batch_generation_authorized: Literal[True]`——实例**只能**由 3.1 摄取门禁在 released 非空时签发（生成函数私有于摄取模块）；`authorized_by`（reviewer）+ `authorization_evidence_sha256`（取证块）。r319 的 `batch_generation_authorized=False` 钉扎本就是「试点不授权批产」的表达，通用家族把授权变成**人工签发的正向事实**而非常量。

### 3.4 `run_step4_stage` 集成点（echo-mode）

- **不改函数本体循环**：新增装载适配器 `load_step4_bundle_source(*, source: Literal["pilot-release","echo-batch"], release_fixture_path, handoff_path) -> tuple[GenericCardContract, ...]`（新函数，建议落 `compilers/step4_echo_bridge.py`）：
  - `pilot-release`：调用现 `derive_step4_bundle`（零改动路径，r2242 彩排/回归照旧可跑）；
  - `echo-batch`：装载 3.2/3.3 新模型 → `derive_step4_bundle_echo(release, handoff)`（新函数）→ `GenericCardContract ×N`（复用 `GenericCardContract` 模型本身——它无 16 卡钉扎，card_index `le=EXPECTED_CARD_COUNT` 需核实：若 le=16 为 pilot 钉扎，则新增 `GenericCardContractV2` 或提高上限属禁区，**采用新模型平行件**，投影函数签名与五字段投影兼容即可【施工首项核实点】）。
  - `run_step4_stage` 增一个可选参 `bundle_loader:`（缺省=现行为）或在 CLI 装配层选择 loader 后传 cards 进来——**二选一在施工卡定夺，倾向后者**（live_run.py 改动面最小：把「装载段」提为参数注入，循环体零改动；与 T4a「providers 注入」风格一致）。
- CLI（`run_exam_pipeline.py`）：`--step4-source pilot-release|echo-batch`（缺省 pilot-release 向后兼容）+ `--echo-release` / `--echo-handoff` 路径参；`--step3-mode echo` 跑完自动衔接 3.1 物料生成（waiting_human 暂停语义与检查点②同构，含 e60fbe8 已修的 resume 对账语义——**resume 盲区补丁的对账模式在新检查点必须复刻**，勿重蹈 P8 前身覆辙）。

### 3.5 与词级门禁（step4-text-gate/1.1）的行级衔接

- 位置：step4 出图完成后、检查点②人工评审前，作**机器预筛**（A2 校准 R5 分层，零漏报双道实证）。
- 输入互证：text-gate 真值 = 3.2 release 卡 `visible_texts`（与 prompt 同源，天然一致）；`--release-fixture` 参数在 echo 模式下指向 EchoBatchReviewedReleaseFixture（text-gate 的真值抽取器需加 echo 家族适配分支【新增件清单 #6】）。
- 分层路由（1.1 disposition 语义原样）：`pass → auto_accept`（进检查点②正常评审）；`warn → auto_archive_with_note`（punct 级差异随 sheet 附注给评审人）；`hard_fail → human_adjudication`（路由重发道/人工终审，**不自动废图**）。
- sheet 侧注字段：`text_gate: {verdict, disposition, findings_digest, report_sha256}` 逐卡并入 image_review_sheet（评审人打开即见机器预筛结论；門禁不替代人评审——不变式 2）。

## 4. 新增件清单（施工卡的验收面）

| # | 件 | 位置 | 说明 |
|---|---|---|---|
| 1 | `EchoBatchReviewedReleaseFixture` + `EchoBatchReleaseCard` | `knowledge/contracts/echo_release.py`（新） | echo-batch-release/1.0；四哈希谱系；N≥1 |
| 2 | `EchoBatchStep4Handoff` + disposition/引用子模型 | 同上 | echo-batch-step4-handoff/1.0；授权显式化 |
| 3 | `emit_echo_step3_review_sheet` / `ingest_echo_step3_review` | `gates/review/echo_step3_review.py`（新） | §3.1 文件契约；取证块校验复用 B1 件 |
| 4 | `derive_step4_bundle_echo` + `load_step4_bundle_source` | `compilers/step4_echo_bridge.py`（新） | §3.4；pilot 路径零触碰 |
| 5 | `run_step4_stage` 装载注入 + CLI `--step4-source` 族 | `pipeline/live_run.py` / `migration/run_exam_pipeline.py` | 最小接缝（装载段参数化）；写锁派卡 |
| 6 | text-gate 真值抽取 echo 适配 | `gates/review/step4_text_gate.py`（增函数不改判定核） | `extract_truths_from_echo_release` |
| 7 | 离线测试 ×~15 + 彩排脚本 | `tests/test_*_echo_bridge.py` 等 | §6 |

## 5. 防误改清单

- `admission_fixtures.py`：**只增不改**——r319/r2242/r810 全部既有模型与 Literal 钉扎原字节；新模型独立新文件（清单 #1/#2），避免任何「放宽 Literal」的诱惑（那会毁掉字节平价重放资产）。
- `derive_step4_bundle` / `step4_contract.py` 既有函数零改动（`prior_reviewed`/互证语义在新 derive 中重述而非改造）。
- `step3_response_v2.py`（T3 契约）、v1 冻结件、法定哈希族：零接触。
- `run_step4_stage` 下游循环（投影/门禁/漂移/预算/断点）零改动——只动装载段接缝。
- 检查点评审执行权：设计内所有人工评审动作归 B2 权威口径，工具件只产物料与摄取。

## 6. 测试计划

**离线（零外呼，伪件注入）**：① 新模型往返/计数互证/四哈希防注入（构造函数拒收外部哈希）；② 评审 sheet 往返：undecided→waiting / 全回填→released / 无效取证块→拒（防伪造用 B1 同型负例）；③ `derive_step4_bundle_echo`：N=1/3/17 批（跨 16 上限证伪 Literal 已除）、prior_reviewed 跳过、与 pilot 路径互不感染（r2242 全套现测继续绿）；④ 装载接缝：`--step4-source` 两分支快照 + 缺件 fail-closed 口径；⑤ text-gate echo 真值适配：B1 三卡真语料（typed fixture 在盘）拉通 gate 判定；⑥ resume 对账：新检查点 waiting_human→回填→resume 摄取（e60fbe8 回归模式复刻三态）。

**彩排验证（施工卡尾段，预算 ≈$0.2-0.4，报批后执行）**：B1 的 3 卡 echo 批（typed fixture/echo record 已在盘，零重跑）→ 人工 step3 评审（B2）→ release/handoff 签发 → `--step4-source echo-batch` 出图 3 图（≈$0.12）→ text-gate（复用缓存转写道零成本或 live ≈$0.06）→ 检查点② 3 图微评审 → 全链 completed。验收线：3/3 出图过具名门禁、text-gate 无误报、审计件齐全（run 报告 + 四哈希谱系可回溯）。

## 7. 实施估算

| 项 | 估算 |
|---|---|
| 工时 | 模型+构建器 ~2h；评审文件契约 ~1.5h；derive/接缝 ~1.5h；测试 ~2h；彩排+报告 ~1h → **单人 ~8h（一夜量）** |
| 写面 | 新文件 ×4 + `live_run.py` 装载段 + CLI + text-gate 增函数（写锁：live_run.py 单独申请） |
| 花费 | 离线 $0；彩排 ≈$0.2-0.4（上表） |
| 风险 | GenericCardContract 的 `card_index le=16` 钉扎属性【施工首项核实】；echo layout→结构枚举映射的 diagram 族未经 live 出图验证（B1 批仅 table/constraint/formula——彩排若无 diagram 卡，记「diagram 族 echo 出图待后续语料」与 T3 实测同款注记） |

## 8. 对齐台账

| 依赖 | 口径来源 | 状态 |
|---|---|---|
| P8 断头点定位 | B1 报告 §3（e6c2379），本稿 §1 补代码级行号取证 | 已对齐 |
| 转换器三件套接口（typed fixture 上游） | fe76237（`export_reviewed_typed_evidence_fixture`，四哈希链内派生原则） | 已对齐（不变式 4 同源） |
| resume 检查点对账语义 | e60fbe8（检查点② resume 盲区修复 + 三态回归） | 设计已复刻进 §3.4/§6-⑥ |
| 词级门禁 1.1 分层 | 542deab（disposition 三路由）+ A2 双道校准（d90afa9/b0c7650，零漏报） | 已对齐（§3.5） |
| 检查点权威冻结令 | 总指挥 20260813 广播（bc-5392d26f） | 已并入不变式 3 |
| 评审签名取证块组规 | B1 防伪造守卫（e6c2379） | 复用（§3.1/§3.3） |
| GenericCardContract 上限属性 | `step4_contract.py` L337 `le=EXPECTED_CARD_COUNT` | **施工首项核实点**（§7 风险） |
