# T4a 通用全链 live 驱动设计稿（`migration/run_exam_pipeline.py` + `pipeline/live_run.py`）

- 日期：2026-08-13 凌晨；作者：yijian2-1（T4a 节点）；状态：**设计稿待总指挥终验**（评审意见 R1/R2 已应用：step3 双取材模式 + 转换器已知开口 + T3/T1 对齐台账更新），批准后为 T4b 施工依据。
- 任务书：`P3_优化施工方案_T1-T5_20260812.md` §5（含 §8 v1.1 修订）；输入报告：`P3_webui勘察_20260812.md`（人工检查点选型）、`new_exam_onboarding_runbook.md`（目录契约与调用形态）、`P2_pilot_step2a质量深评_20260812.md`（生产配方）、`P2_pilot_黄金五章全链实测_20260812.md`（中继能力边界）。
- 一句话：把 runbook 第 4–8 步的"专家陪跑"手工片段，收敛为**一条命令**的可断点续跑、预算受控、人工检查点在环的全链驱动；编排逻辑进库（可测），CLI 只做薄壳。

## 0. 设计不变式（继承全仓纪律，违者返工）

1. **冻结面零改动**：法定哈希实现、v1 冻结契约（含 step3 三卡回显、`ProductionAuthorizationManifest` 三决策钉扎形态）、`docs/v2/` 只读——新能力一律新增模块/新版本号。
2. **yijian2 零接触**：新考试资产只进本仓 `exams/`；运行配置读本仓 `.env`（T2 迁移后），`--env-file` 仅作显式覆盖。
3. **身份纪律**：请求 identity 内容寻址；已存在的 identity 决不复用（`_execute_stage` 现行拒绝语义保留）；重发 = 新 reason 后缀的新身份（ResendPlan）；禁自动同身份重试；`unknown_outcome` 是一等公民结局，不得吞并为 failed。
4. **审计优先**：每次外呼落 requests/records/cache/journal 四件套（现行 `_execute_stage` 布局）；密钥仅驻内存，不打印不落盘不入报告。
5. **人工门禁不绕过**：黄金样本 `reviewed` 状态机（`run_golden_fact_pipeline` 第一道校验）无参数可绕；浏览器导出的 `reviewed_candidate` 一律降格 `manual_review`（r82 定式）；全章模式产物一律 draft 待人审。
6. **fail-closed**：任何门禁/预算/预检失败即停在失败点，其后 stage 记 skipped；决不静默部分成功（`pipeline/run.py` 现行语义推广到 live）。

## 1. 总体形态：库/CLI 分层

```
migration/run_exam_pipeline.py          # CLI 薄壳：argparse → LiveRunConfig → orchestrate() → 退出码
src/examcard/pipeline/live_run.py       # 编排库（T2 已开桩：ResendPlan / load_run_env）：全部可测逻辑
```

- **薄壳只做四件事**：解析参数、装配 `LiveRunConfig`、调 `orchestrate()`、把 `LiveRunReport` 映射为退出码与控制台摘要。零业务逻辑，零直接外呼。
- **库承担全部编排**：目录契约解析、预检、预算、断点续跑、stage 执行、检查点物料生成与摄取、报告物化。全部函数可用伪 transport 离线测试（T4b 测试计划 §10）。
- 与既有 `pipeline/run.py`（离线四步骨架）的关系：**并列不替代**。`run.py` 的 provider 注入式离线骨架继续服务离线场景；`live_run.py` 是 live 编排（有外呼、有人工检查点、有预算），复用 `common.hashing/io/freeze` 同一底座。step 结果/报告模型形态对齐 `PipelineStepResult/PipelineRunReport` 的谱系（新版本号 `examcard_live_run_report_v1`，不复用 `network_calls: Literal[0]` 的离线钉扎）。

## 2. CLI 契约

```
uv run python migration/run_exam_pipeline.py \
  --exam <exam_id> \
  --stages step1,step2a,step2b,step3,step4 \
  [--budget-usd N] [--dry-run | --allow-live] \
  [--run-label <ascii-audit-id>] [--only-samples <substr,substr>] \
  [--channel first|last] [--env-file PATH] \
  [--step3-contract v2|v1] [--step3-mode echo|release-bypass] [--skip-preflight] [--resume <run_id>]
```

| 参数 | 语义 | 缺省 |
|---|---|---|
| `--exam` | `exams/<exam_id>/` 目录契约的考试 id | 必填 |
| `--stages` | 逗号分隔的 stage 子集，顺序无关（按固定 DAG 排序执行）；支持只跑一段（如 `step3,step4`） | 必填 |
| `--budget-usd` | 本次运行美元硬上限（§6 预算护栏），live 必填 | dry-run 可省 |
| `--dry-run` / `--allow-live` | 二选一互斥（沿用 pilot 驱动语义）；dry-run 零外呼 | 必选其一 |
| `--run-label` | 审计标识（ASCII `[a-z0-9][a-z0-9._-]*` ≤64），live 必填；识别身份 reason 由驱动派生为 `<exam>-<stage>-<label>`（≤96 约束自动校验） | dry-run 自动 `dry-run` |
| `--only-samples` | 样本 id 子串过滤（重发窄道，沿用 pilot 语义） | 全样本 |
| `--channel` | 文本通道选择（.env 多对键约定，同 T1 preflight：first=chatgpt2api-new，last=2chat） | `last` |
| `--env-file` | 显式指定 env 文件（回归旧仓路径用） | 新仓 `.env` |
| `--step3-contract` | step3 回显契约版本：`v2` 单卡（T3）或 `v1` 三卡 | `v2` |
| `--step3-mode` | step3 取材模式（方案 v1.2 勘误 5a9f238）：`echo` = 检查点①放行产物经 typed-evidence fixture 走 v2 回显（通用生产道，N 卡）；`release-bypass` = 既有 reviewed 发布 fixture 直进 step4 derive 路径、跳过回显（pilot 同款，gonglu 彩排即此道——r2242 16 发布卡无 typed layout 不可喂 v2） | `echo` |
| `--skip-preflight` | 跳过开跑预检（仅当当日已有该通道预检报告时允许） | 不跳 |
| `--resume` | 续跑既有 run（§5）；缺省新开 run_id | 新 run |

**退出码**：`0` = 请求的 stages 全部完成；`2` = 正常暂停在人工检查点（打印待办与恢复命令，**不是错误**）；`1` = 失败（预检不达标/预算耗尽/门禁失败/样本硬失败，见 §9 stop_reason 表）。

## 3. 目录契约与运行产物布局

### 3.1 exams/ 目录契约（runbook §1 原样，此处为规范性引用）

```
exam-card-engine/
├── src/…                          # 引擎（不因考试而改）
├── configs/v2/
│   ├── material_profiles/<exam_id>.yaml      # ★唯一必须新增的"接入点"
│   ├── content_policies/                     # 复用 core_only_v1 / question_bank_v1，或新增
│   └── *_sidecars/                           # 人工评审侧车（按 registry 命名空间新增文件）
├── exams/
│   └── zaojia_2026/               # 例：一级造价 2026（xiaofang_2026 同构）
│       ├── input_pdf/             # 源 PDF（gitignore）
│       ├── data/evidence_v2/      # Step1 产物+缓存（gitignore）
│       ├── data/facts_v2/         # Step2 产物（gitignore）
│       └── fixtures/
│           ├── golden_samples/<exam_id>_phase0.yaml       # 黄金注册表（入库）
│           └── golden_annotations/<exam_id>_phase0_v1.yaml # 人工标注侧车（入库）
└── .gitignore                     # 追加 /exams/*/input_pdf/ 与 /exams/*/data/
```

约束照抄 runbook：一切持久化引用路径为**仓根相对路径**；注册表/侧车（人工签字物）入库，PDF 与运行产物不入库。

### 3.2 .gitignore 增补（T4b 落地件）

```gitignore
# 新考试工作区（runbook §1：代码与数据分仓）
/exams/*/input_pdf/
/exams/*/data/
```

### 3.3 live 运行产物（新增约定，在 `data/` 之下故天然 gitignore）

```
exams/<exam_id>/data/live_runs/<run_id>/
├── run_state.json                 # 断点续跑状态机（§5）
├── budget.json                    # 预算账本（§6，VisionCallBudget 同型持久化）
├── relay_preflight/               # 本次运行的预检报告快照（§8）
├── step2a/ step2b/                # 各自 requests/ records/ cache/ telemetry/（现行 _execute_stage 四件套布局）
├── step3/                         # 同上 + 编译工件
├── step4/                         # 图片 + 信封审计件 + 出图报告
├── checkpoints/
│   ├── admission/                 # 检查点①物料：queue.json / workbench.html / decisions 摄取审计（§7.1）
│   └── image_review/              # 检查点②物料：image_review_sheet.json / 授权清单 / 准入审计（§7.2）
├── live_run_report.json           # 运行总报告（§9）
└── live_run_freeze.json           # common.freeze 冻结快照（报告+账本+journal 清单）
```

`run_id` = `<UTC日期>-<run-label>-<8位随机>`；`--resume <run_id>` 指回该目录。

## 4. 阶段流水：每 stage 输入 / 输出 / 审计件 / 网络面

固定 DAG：`step1 → [事实底座] → step2a → step2b → ◆检查点① → step3 → step4 → ◆检查点②`。`--stages` 取子集时按此序执行，前置产物缺失即 fail-closed 报错（指出缺哪件、该跑哪段）。

| stage | 输入（仓根相对） | 引擎入口 | 输出 | 审计件 | 网络 |
|---|---|---|---|---|---|
| **step1 证据** | `exams/<id>/input_pdf/*` + 黄金注册表 + material profile | `evidence.golden_runner.run_golden_registry`（黄金样本面；可选 `sample_ids: set[str]` 支撑 `--only-samples` 单样本重跑——漂移核查实测签名）；全章模式走 `EvidenceEngine.ingest_document` 逐章（shadow_wiring 形态） | `exams/<id>/data/evidence_v2/golden/<registry.registry_id>/<sample_id>/pages/page_%04d.json` + `golden_run_report.json` | 逐样本 evidence 报告 + 引擎缓存；vision 复核启用时 `vision-budget.json` | 离线（vision 复核可选外呼，走预算） |
| **事实底座**（step2a 的自动前置，不占 stage 名） | 注册表（**全 reviewed**）+ 标注侧车 + 内容策略 + step1 产物 + diagram/table 侧车（**disposition 侧车必须与 review 侧车成对传**——fact_engine 强校验，漂移核查 N 条） | `knowledge.fact_engine.run_golden_fact_pipeline` | `exams/<id>/data/facts_v2/golden/<…>/<sample_id>/fact_run.json` + `golden_fact_run_report.json` | 同左（内容寻址 fact:/card:/ev: ID，字节可复现）；summary 顶层键 `quality_gates_passed`/`machine_assertions_passed`（复数）为 step2a 放行条件 | 纯离线 |
| **step2a 提案** | 事实底座产物 + reviewed 学习 fixture + 语义侧车（`build_live_sample_contexts` 的泛化版，§11-E1） | `contracts.phase2r14_v25_live._execute_stage`（经 live_run 编排）+ 生产配方 transport（补强+SSE 流式，T2 参数） | 逐样本 step2a 提案（draft） | `step2a/{requests,records,cache}/` + `telemetry/journal.jsonl`；门禁评估结果（source_contract/teaching/learning_golden，登记不熔断——pilot 口径） | live，2 探针先行 |
| **step2b 编组** | step2a 同源上下文 + reviewed Fact 桥 | 同上（stage=step2b） | 逐样本 step2b 编组提案（draft） | 同布局；编组/卡门禁登记 | live |
| **◆检查点① 准入** | step2 产物 + 源页图 manifest | §7.1（人工在环） | 权威 `HumanDecisionManifest`；放行集合 | `checkpoints/admission/` 全件 | 零外呼 |
| **step3 编译+回显** | `echo` 模式：检查点①放行产物转成的 reviewed typed-evidence fixture（**N 卡通用，勿写死卡数**——v1.2 勘误：r810 typed 链实编 6 卡，「16 卡」属 r2242 step4 发布语料且无 typed layout 不可喂 v2）；`release-bypass` 模式：既有 reviewed 发布 fixture 直进 step4，跳过本 stage 回显 | 离线编译 `compilers.step3_card.compile_reviewed_fixture` → v2 单卡回显 `compilers.step3_response_v2.*`（T3 已落地 282fd69，缺省）或 v1 三卡 `step3_response.*` | 逐卡确认后的 draft 卡工件 | providers `inspect_chat_envelope` 信封审计（`make_single_card_inspector(model_input)` 直接作 inner_inspector）+ 逐卡 strict 回显 record + journal；版本号族 input=`step3-response/2.0` / result=`step3-response-result/2.0` / draft 卡=`step3-response-draft-card/2.0`（input/result 刻意不同，回显错版即判负） | live，1 探针先行 |
| **step4 出图** | step3 产物派生 `derive_step4_bundle` → `GenericCardContract` ×N | `providers.project_image_request` 五字段投影 → POST images/generations → `inspect_image_envelope` 具名门禁 | PNG ×N（尺寸/哈希校验后落盘） | 逐图信封审计 + 出图报告（成功率/时延/单价） | live，1 `/models` 探针先行 |
| **◆检查点② 图像评审** | step4 图片 + 绑定摘要 | §7.2（人工在环） | N 图授权清单（新版本号模型）→ `ReviewedImageCard`/`PostReviewItem` + `AdmissionAudit` | `checkpoints/image_review/` 全件 | 零外呼 |

## 5. 断点续跑与失败重发

### 5.1 断点续跑（同 run 内幂等恢复）

- **完成标记 = 产物本身**（shadow 章报告模式推广）：每个 stage 的样本粒度以 `cache/<cache_key>.json` 的 `state` 为准——`--resume` 时驱动先枚举本 stage 全部待跑样本的 identity，`state=success` 的**跳过**（内容寻址保证同输入同 identity，跳过即复用）；无 cache 的执行；`state∈{failed, unknown_outcome}` 的进入重发道（§5.2）。
- `run_state.json` 记录 stage 粒度状态机（`pending/running/completed/completed_with_failures/waiting_human/skipped/failed`）+ 各样本 identity 清单 + 检查点票据；崩溃后 `--resume` 从中恢复，与 cache 层双保险（state 文件损坏时可从 cache/records 重建，重建逻辑进库并测试）。
- **identity 拒绝复用语义保留**：恢复执行前先查三件（request/record/cache）存在性——存在即跳过而不是重放，从结构上杜绝"同身份重放"（现行 `_execute_stage` 的 ValueError 是最后一道保险，不是常规路径）。

### 5.2 失败重发（ResendPlan，签名以 T2 落地为准）

- 重发一律**新身份**：`ResendPlan` 基于原 reason 生成后缀身份（形如 `<exam>-<stage>-<label>-r2/-r3`），禁复用失败身份，轮数上限缺省 3（62 章实证口径：首发 ~60%、重发累计 >95%）。
- 触发面：`state∈{failed, unknown_outcome}` 的样本；探针失败整 stage 停（fail-closed），不进样本重发道。
- 节流：同一样本两次重发之间强制间隔（缺省 300s，可配）——昨晚 62 章三连挂的教训：热窗口内立即重发只是浪费预算。
- 单样本失败**不熔断整场**（pilot 纪律）：登记后继续下一样本，stage 结果 `completed_with_failures`，重发道收尾；重发轮数耗尽仍失败 → stage `failed`，下游 skipped。
- **对齐说明**：`ResendPlan`/`load_run_env()` 的最终签名以 t2-recipe 落地的 `src/examcard/pipeline/live_run.py` 为准（已 send_to_session 对齐，等回复后本节校正字段名）；本稿按 P3 §2 计划口径书写。
- **step3 v2 重发四不变式**（T3 落地口径 282fd69；T4b 用 ResendPlan 的 reason 生成器替换其驱动内规则时必须保持等价）：(a) 首发 attempt=1、reason=None；(b) 重发 reason=`manual_reissue_step3v2_r{attempt}`（attempt≥2），每（卡,轮）唯一、禁复用失败身份；(c) 轮上限 2（stage 预算=探针+2N）；(d) 补强 system 与 stream 是传输层参数**不进 identity**，`reinforcement_id` 单独入 journal。

## 6. 预算护栏（仿 shadow vision-budget，双账本）

沿用 `shadow_wiring.VisionCallBudget` 的全部已验证性质，泛化为 `LiveRunBudget`（新类，进 live_run.py）：

1. **持久化账本** `budget.json`：`{budget_usd_cap, spent_usd_estimate, per_stage: {<stage>: {call_cap, calls, usd_estimate}}}`；每次外呼**前** `charge(stage, est_usd)`，超限抛 `LiveBudgetExceeded`（fail-closed，决不降级）；文件随写随刷，跨进程续跑不重置；`--resume` 时 cap 不一致即拒（同 VisionCallBudget 的 hard_cap mismatch 语义）。
2. **双护栏取严**：调用次数上限（每 stage 静态推导：样本数×2+探针+重发余量，pilot 的 NETWORK_CALL_BUDGET 谱系）与 `--budget-usd` 美元上限并行判定，任一触顶即熔断，stop_reason 区分 `stage_call_budget_reached` / `usd_budget_reached`。
3. **单价表** `configs/v2/live_pricing_v1.yaml`（新增，非冻结面）：逐调用类型估价（step2a/step2b/step3 单卡/step4 单图/探针），初值以 pilot 实测均值标定（`P2_公路65章shadow与API_pilot费用估算_20260812.md` 口径），字段含 `calibrated_from` 审计出处；估价仅用于护栏与报告，不进请求 identity。
4. dry-run 输出**预算预演**：按选定 stages/样本数打印预计调用数与美元区间，帮助用户定 `--budget-usd`。

## 7. 人工检查点（文件为契约，webui 为可选检视器——webui 勘察 §7 选型采纳）

两处检查点都遵守同一形态：**驱动产出待审物料 → 以退出码 2 暂停并打印人工操作指引 → 人工产出决定文件 → `--resume` 时驱动严格摄取 → 门禁放行**。决定文件是唯一权威输入；webui 只是"看图、录契约、导 JSON"的壳。

### 7.1 检查点①：step2 产物 → 人工准入 → step3

- **物料生成**（G-A1 补齐，进 live_run.py）：从 step2 产物 + 源页图 manifest 构造 `AdmissionQueueItem` 队列 → `queue.json`（含 `queue_sha256`，r8x 法定哈希谱系）+ `compact_queue_entry` 投影 → `render_review_workbench()` 渲染 `workbench.html`。
- **启动指引**（G-A4）：驱动打印一条命令——`\.venv\Scripts\python.exe -m http.server 8777 --bind 127.0.0.1 --directory <服务根>`；**服务根钉死为 `exams/<exam_id>/` 运行根**（勘察 §7 硬输入：`image_uri` 根绝对路径必须可解析到源页图）。
- **摄取**（G-A2 补齐）：人工在工作台导出 `review-browser-decisions.json`（或直接手写同信封文件）放入 `checkpoints/admission/inbox/`；`--resume` 时走严格链：`RawReviewDraftManifest → classify_browser_decision()`（浏览器 `reviewed_candidate` 一律降格 `manual_review`，语义照搬 r84 定式不新造）`→ normalize_raw_draft()`（重绑定冻结候选，`decision_builder` 由驱动注入并重算权威哈希）`→ HumanDecisionManifest` 落盘。
- **放行门禁**：仅 `reviewed_candidate`（CLI 侧权威决策）集合进入 step3；`manual_review/blocked/not_applicable` 留在准入队列并入报告。queue_sha256 贯穿决定/清单/审计（链绑定）。
- **已知开口（T4b 首项验证点，不得默认存在）**：「准入放行的 step2 产物 → reviewed typed-evidence fixture」的转换器（旧仓 r71→r8x 链的新仓等价物）**存在性未验证**——T4b 开工第一件事即核实；若缺失，need_input 上报总指挥裁决（补搬运该链 or 该考试暂走 `--step3-mode release-bypass`），不得自行发明新的转换语义。
- **进度可续**（G-A3 补齐）：`build_progress()` 快照随摄取落盘 `checkpoints/admission/progress.json`，分批评审不丢。
- **黄金样本注册表 `reviewed` 状态机独立于此**：它门禁的是"黄金样本能不能进事实底座"（`run_golden_fact_pipeline` 第一道校验），本检查点门禁的是"step2 产物能不能进 step3"——两道门都不绕过。

### 7.2 检查点②：step4 出图 → 人工目检 → reviewed fixture

- **v1 纯文件侧车，webui 不加图像页**（勘察 G-B1：现状零界面，补 UI 属新开发；图像页列 v2 增强）。
- **评审底稿**（G-B3）：step4 收尾自动产出 `image_review_sheet.json`——逐图 `{card_id, presentation, image_path(仓根相对), image_sha256, width×height, source_binding 摘要, prompt_sha256}`；字段口径对齐 16 图人工评审底稿实践（`P3_step4成品图评审底稿_20260812.md`）。
- **人工回填**：`disposition: approved | rejected | manual_review` + reviewer + 依据，写回 sheet（或旁置 `dispositions.json`）。
- **摄取与授权**（G-B2）：`image_binding_decision()/build_image_manifest()` 的映射手法保留，但落到**新版本号清单模型**——`ProductionAuthorizationManifest` v1 冻结为"恰 3 决策、三呈现各恰一次"（phase2r11101 钉扎），通用 N 图必须新增 `BatchImageAuthorizationManifest`（`schema_version: "image-admission-manifest/2.0"`，N ≥1 决策，呈现分布自由，`decision_source: "explicit_user_message"` 语义保留），新模块放 `gates/review/image_admission_batch.py`，v1 文件零改动。
- **收口**：`admit_images()` 等价的 v2 准入（approved→`ReviewedImageCard`；其余→`PostReviewItem`；全量 `AdmissionAudit`）→ reviewed fixture 供发布/回归。
- **全章模式**（黄金样本之外的批量生产）：step2/step3/step4 全部产物**恒为 draft**，`review_promotion: disabled` 报告字段保留（pilot 口径）；任何 reviewed 晋升只能经上述两个检查点的人工决定文件发生，驱动没有任何"自动晋升"代码路径。

## 8. 双通道路由与开跑预检

### 8.1 .env 双通道（T2 落地，driver 消费）

- 读取：`load_run_env()`（T2）——缺省新仓 `exam-card-engine/.env`，`--env-file` 显式覆盖；键仅驻内存。
- **文本通道**：`API_BASE_URL/API_KEY`（.env 中成对多次出现，`--channel first|last` 选对——与 T1 preflight 同一约定）+ `TEXT_MODEL`。
- **图像通道**：**`IMAGE_API_BASE_URL / IMAGE_API_KEY / IMAGE_MODEL` 显式键优先**；显式键缺席时回落文本通道同源（并在报告记 `image_channel_fallback: true`）。`OPENAI_USER_AGENT` 存在则入 default_headers（现行 pilot 口径）。
- step4 的传输客户端与 step2/step3 分实例：各自 base_url/key/timeout；providers 层门禁不变（chat 走 `inspect_chat_envelope`，图像走五字段投影 + `inspect_image_envelope`）。

### 8.2 relay_preflight 自动预检（T1 落地，driver 消费）

- live 模式开跑前对选定通道自动跑一次预检——**库函数直调已落地**（T1 回执 1b4bfbb）：`relay_preflight.run_preflight() -> report dict`，含机器可读 `verdict_code`（`pass/likely_pass/fail/unknown` 四值枚举）与 `report_path`；图像通道预检走 `--channel image`（新仓 .env 显式 `IMAGE_API_*` 键）。报告快照进 `<run>/relay_preflight/`。
- **需求画像 → stage 放行矩阵**（阈值以 T1 的 `REQUIREMENT_PROFILE` 为准）：

| 请求的 stage | 预检判定 | 不满足时 |
|---|---|---|
| step2a/step2b | 体积上限 ≥300KB（或流式可用 + 体积达样本实测最大 payload） | 拒开（`preflight_step2_unmet`） |
| step3(v2) | strict 强制解码 + （首字节窗口 ≥150s 或流式可续） | 拒开（`preflight_step3_unmet`） |
| step4 | 图像端点可用（IMAGE_MODEL 在列） | 拒开（`preflight_step4_unmet`） |

- `--skip-preflight` 仅在当日同通道已有报告时允许（复用其结论）；无报告硬拒——"2 分钟预检"换"数小时排障"是 T1 的立项理由，不给绕。
- 预检自身调用计入预算账本（`preflight` 类目）。

## 9. fail-closed 失败语义（stop_reason 总表）

| stop_reason | 触发 | 处置 |
|---|---|---|
| `completed` | 请求的 stages 全部成功 | 退出 0 |
| `waiting_human_admission` / `waiting_human_image_review` | 到达检查点①/② | 退出 2，打印待办与恢复命令 |
| `preflight_step{2,3,4}_unmet` | §8.2 矩阵不满足 | 退出 1，拒开任何 live 调用 |
| `<stage>_probe_failed` | stage 前置探针连败（探针重发 1 次的现行口径保留） | 退出 1，该 stage failed、下游 skipped |
| `stage_call_budget_reached` / `usd_budget_reached` | §6 双护栏任一触顶 | 退出 1，账本与报告留取证 |
| `completed_with_failures` | 样本级失败且重发轮数耗尽 | 退出 1，失败样本清单入报告（可 `--only-samples` 窄道补跑） |
| `missing_upstream_artifact` | `--stages` 子集缺前置产物 | 退出 1，指明缺件与应跑 stage |
| `gate_refused_admission` | 检查点摄取发现决定文件非法/哈希不链 | 退出 1，摄取审计留痕 |
| `frozen_input_drift` | 运行前后冻结输入指纹不等（pilot 的 freeze 对拍推广） | 退出 1，最高优先级告警 |

原则：**门禁失败登记不熔断（质量测量）、传输/预算/契约失败熔断（工程安全）**——与 pilot 一致；两类在报告中分字段，决不混淆。

## 10. 新增模块与函数签名清单（T4b 施工面）

### 10.1 `src/examcard/pipeline/live_run.py`（T2 已开桩，T4b 扩展点）

```python
# —— T2 已落/将落（签名以 T2 落地为准，本稿引用）——
def load_run_env(env_file: Path | None = None) -> Mapping[str, str]: ...
class ResendPlan: ...          # 新 reason 后缀身份、禁复用失败身份、轮数上限

# —— T4b 新增 ——
@dataclass(frozen=True)
class ExamPaths:               # 目录契约解析器（§3；一切路径仓根相对）
    # 互锁纪律（漂移核查 N1）：evidence/facts 产物路径一律从 registry.registry_id 派生
    # （命名 <exam_id>_phase0_v1），决不硬编码拼串；material_profile_id 必须等于
    # profile.profile_id（golden_runner 与 fact_engine 两处强校验，resolve 时先行断言）。
    @classmethod
    def resolve(cls, repo_root: Path, exam_id: str) -> "ExamPaths": ...

@dataclass(frozen=True)
class LiveRunConfig:           # CLI 参数的全量映射（exam_id/stages/budget_usd/channel/…）
    ...

class LiveRunBudget:           # §6：VisionCallBudget 同型（persist/charge/resume 校验）
    def __init__(self, state_path: Path, *, usd_cap: float, stage_call_caps: Mapping[str, int]) -> None: ...
    def charge(self, stage: str, *, calls: int = 1, usd_estimate: float) -> None: ...   # 超限抛 LiveBudgetExceeded

class LiveBudgetExceeded(RuntimeError): ...

def run_preflight_gate(*, channel: ChannelConfig, stages: tuple[str, ...], storage: Path,
                       budget: LiveRunBudget, skip_with_cached: bool) -> PreflightVerdict: ...  # §8.2

def build_exam_sample_contexts(*, paths: ExamPaths, env: Mapping[str, str],
                               reason: str, sample_ids: tuple[str, ...] | None) -> tuple[Any, ...]: ...
    # E1：build_live_sample_contexts 的 exams/ 目录契约泛化（资产路径从 ExamPaths 来，不再钉死 gonglu 常量）

def execute_stage_samples(*, stage: str, contexts: tuple[Any, ...], transport: Any,
                          storage: Path, budget: LiveRunBudget, resend: ResendPlan,
                          state: RunState) -> StageReport: ...
    # §5：cache-state 跳过 + 失败重发 + 门禁登记；step2a/step2b 共用

def run_step3_stage(*, cards: tuple[Any, ...], contract_version: Literal["v1", "v2"], ...) -> StageReport: ...
    # v2 逐卡循环吸收 T3 独立驱动结构（v1.1 修订1：集成在 T4b 做）

def run_step4_stage(*, bundle: Any, image_channel: ChannelConfig, ...) -> StageReport: ...

def build_admission_checkpoint(*, run_root: Path, paths: ExamPaths, step2_outputs: ...) -> CheckpointTicket: ...   # §7.1 物料
def ingest_admission_decisions(*, run_root: Path, decision_builder: Callable[..., Any]) -> AdmissionOutcome: ...   # §7.1 摄取
def emit_image_review_sheet(*, run_root: Path, images: ...) -> Path: ...                                           # §7.2 底稿
def ingest_image_review_sheet(*, run_root: Path) -> ImageAdmissionOutcome: ...                                     # §7.2 摄取

class RunState: ...            # run_state.json 状态机（load/save/rebuild-from-cache）

def orchestrate(config: LiveRunConfig) -> LiveRunReport: ...   # 总编排：DAG 排序 → 逐 stage → 报告/freeze 物化
```

### 10.2 其他新增件

| 件 | 位置 | 说明 |
|---|---|---|
| CLI 薄壳 | `migration/run_exam_pipeline.py` | argparse → `orchestrate()`；≤150 行 |
| N 图授权清单 v2 模型 | `src/examcard/gates/review/image_admission_batch.py` | §7.2；v1 `image_admission.py` 零改动 |
| 单价表 | `configs/v2/live_pricing_v1.yaml` | §6；非冻结面，带标定出处 |
| 报告模型 | live_run.py 内 `LiveRunReport/StageReport`（StrictModel + 自证哈希，谱系对齐 `PipelineRunReport`） | 新版本号 `examcard_live_run_report_v1` |
| .gitignore 增补 | 仓根 `.gitignore` | §3.2 两行 |

### 10.3 上游签名依赖登记（落地后校正本稿）

| 依赖 | 承接人 | 本稿引用口径 | 对齐状态 |
|---|---|---|---|
| `ResendPlan` / `load_run_env` / transport 可选参（stream、system_suffix）与 journal 审计字段 | T2（yijian2-4） | P3 §2 计划口径 | 已 send_to_session，待回 |
| `relay_preflight` 库调用面 + 报告结构 + REQUIREMENT_PROFILE | T1（yijian2-7） | 落地文件实读（四探针/通道约定/报告路径） | **已回并采纳**（工具 5af201b + 库入口回执 1b4bfbb）：`run_preflight() -> report dict`（`verdict_code` 四值枚举 + `report_path`）、`--channel image` 走显式 `IMAGE_API_*`、dry-run 不再覆写 live 报告；双中继实测 2chat strict 强制 + 流式穿越掐断 → step3v2 亮绿灯，3100 图像信封漂移带 width+height 键（§8.2 已按此定稿） |
| step3 v2 模块面（`SingleCardModelInput` 等四件）+ 独立驱动重发规则 | T3（yijian2-5） | P3 §4 计划口径 | **已回并采纳**（落地 282fd69，四名字全保留）：摊平模型（geometry 不进回显、source_fixture_sha256 顶层、五族 presentation Literal）；`build_single_card_model_input(contract, projection, *, source_fixture_sha256=None)` 三重配对守卫；版本号族三层刻意不同（§4 step3 行）；inspector 满足 InnerGate 最小面；判负码 empty-draft-cards-escape 等四枚；重发四不变式入 §5.2；语料勘误（r810=6 卡）入 §4/§11。**live 实证**（912b883）：r810 六卡 2chat+5.6-sol+SSE 单卡回显 **6/6 首发全过**（17-32s/卡，零重发）——echo 生产道可行性已被真实验证（live 语料仅覆盖 table/ordered_list/formula 三族，diagram/constraint 待后续语料） |
| runbook 与代码 API 漂移核查结论 | yijian2-3 | runbook §1/§4/§6 原文 + 本人对代码直读交叉验证 | **已回并采纳**（`P3_runbook漂移核查_20260812.md`，41 项 0 硬漂移）：①`run_golden_registry` 可选 `sample_ids` 已入 §4；②registry_id 互锁纪律已入 §10.1 ExamPaths；③disposition/review 侧车成对强校验已入 §4；④`validate_learning_fixture` 收已加载对象（配 `load_learning_golden_fixture(path)` 加载器），driver 不得直传路径；⑤contracts phase2 为 r11/r12/r13/r14/r142 五模块（端点记法勿写成连续闭包）；⑥`inspect_chat_envelope(body: bytes, *, inner_inspector, profile=None, allowlist=None)` 实测签名 |

## 11. T4b 测试计划（全离线为主，live 彩排单列）

**离线（进 `tests/`，遵守 T4 禁网守卫；伪 transport 用现成 `CallableStep2Transport`）**：

1. `ExamPaths.resolve`：目录契约解析 + 缺件报错口径（逐件断言 `missing_upstream_artifact` 消息指名道姓）。
2. `LiveRunBudget`：charge/超限抛异常/持久化-恢复/cap mismatch 拒绝（对拍 VisionCallBudget 行为等价）+ 双护栏取严次序。
3. `ResendPlan` 集成：失败样本获得新后缀身份、身份决不重复、轮上限触发 stage failed（T2 单测之上的编排层用例）。
4. 断点续跑：预置 cache（success/failed/unknown_outcome 三态）→ 断言跳过/重发/继续的分派正确；`run_state.json` 损坏时 rebuild-from-cache 等价。
5. 检查点①往返：用 `tests/fixtures/admission_kernel.py` 造队列 → 渲染 → 伪浏览器导出（占位哈希 + browser_demo=true）→ 摄取 → 断言 reviewed_candidate 降格、权威哈希重算、queue_sha256 链绑定、非法决定文件拒绝（`gate_refused_admission`）。
6. 检查点②往返：sheet 产出字段全 + dispositions 回填摄取 → v2 清单模型校验（N 决策、schema_version、v1 模型零触碰的 import 守卫）。
7. 预检门禁：喂三份判定报告（全过/step3 不满足/图像缺席）→ 断言放行矩阵与 stop_reason；`--skip-preflight` 无缓存硬拒。
8. CLI 薄壳：argparse 快照（互斥组/必填组合/run-label 正则）+ 退出码映射（0/1/2）。
9. dry-run 零外呼保证：伪 transport 计数恒 0 + 预算预演字段在场。
10. 报告/freeze：`LiveRunReport` 自证哈希、stage 序、failed 后 skipped 不变式（对拍 `PipelineRunReport` 语义）。

**live 彩排（T4b 验收，预算 ≈$1-2，报总指挥审计）**：gonglu 黄金五章伪装新考试落 `exams/gonglu_golden5_rehearsal/`（复用既有 reviewed 资产），依次 `--stages step1,step2a,step2b`（→人工准入）`step3,step4`（→图像评审），一条命令链全通、审计件齐全、`uv run pytest` 全绿。step3 取材按 v1.2 勘误双道走：主彩排 `--step3-mode release-bypass`（r2242 16 发布卡直进 step4 derive，pilot 同款）；`echo` 生产道以 r810 六卡 typed 语料作演示车道（N 卡通用，验证 v2 回显编排）。

## 12. 与既有件的边界（防误改清单）

- `pipeline/run.py`（离线骨架）、`pipeline/shadow*.py`（shadow 轨）零改动；live_run.py 只新增。
- `phase2r14_v25_live.py` 的 `_execute_stage/_strict_probe` 按现签名消费；若泛化需要参数化 gonglu 常量（`_TARGET_*` 路径族），走**新函数**（§10.1 E1）不改旧签名——旧 pilot 驱动继续可跑。
- v1 冻结件（step3_response.py、image_admission.py、法定哈希、admission_fixtures.py）零改动。
- webui 包只消费不改（渲染函数现签名够用）；G-A1~A4 的胶水全部落 live_run.py / CLI 侧。
