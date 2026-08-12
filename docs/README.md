# exam-card-engine 文档总索引

更新：2026-08-13（T5 文档沉淀：runbook §8 生产配方/中继矩阵/预检/.env 双通道成文 + 漂移核查 N1–N6 并入；engineering_skeleton §5 增 pytest 环境修复附注；新增 P3 引擎件速查）。上一轮：2026-08-12（P2·docs 重复件收拾 + 总索引；n11 总报告遗留项 5.4 已清理）。

## 目录分层约定

| 位置 | 职责 | 约束 |
|---|---|---|
| `docs/`（顶层） | 全仓级基准文档：工程骨架、迁移清单、阶段总报告、本索引 | 每份唯一，无副本 |
| `docs/design/` | 施工设计契约（阶段 2 各 distill 桶的执行依据） | 正本唯一位置；旧顶层/migration 指路占位件已清理 |
| `docs/prework/` | 勘察、清点、映射、验证报告类产物 | 文件名带日期后缀 |
| `docs/v2/` | 自 yijian2 随迁的历史资产（RC1 运行时闭包与黄金基线的冻结输入） | **只读基准**，受冻结哈希铁律 bc-b8b9aca6 保护，不得改写 |
| `migration/` | 一次性迁移脚本与平价/审计验证工具 | 非包代码，不参与测试发现（骨架 T1）；对 yijian2 只读 |

## 顶层基准文档

| 文件 | 内容 |
|---|---|
| `engineering_skeleton.md` | 工程骨架规范：pyproject/十包布局与依赖方向、测试隔离 T1–T8、布局终裁登记（bc-888c6031 十包 + bc-9d82a196 common 六件扩容）；§5 附本机 pytest ACL 环境修复指引（管理员一行命令 + `--basetemp` 替代口径） |
| `extraction_manifest.md` | 迁移清单 v2：142 模块去向对账基准（含 8 条【A案】跨桶契约下沉注解） |
| `stage2_migration_report.md` | 阶段 2 总集成验收（n11）：pytest 462 全绿、依赖守护、26/26 对账、遗留风险与下一步 |
| `new_exam_onboarding_runbook.md` | 新考试（造价/消防）接入手册；§8 已成文 live 口径：生产配方四件套（5.6-sol/补强/流式/ResendPlan 重发）、两中继能力边界矩阵、`relay_preflight` 前置检查、`.env` 双通道键约定（配置在新仓）；已并入漂移核查 N1–N6 修订 |
| `README.md` | 本索引 |

## docs/design/（施工设计契约，正本）

| 文件 | 桶 | 要点 |
|---|---|---|
| `providers_distillation_design.md` | distill:providers（4.2，18 模块） | 12 条传输不变式；common 冻结签名依据（§6.1/§9.1） |
| `p1_compilers_release_gate_design.md` | distill:compilers（4.1）+ release-gate（4.4） | 复制改造手法、逐符号去向表、§6 哈希稳定性红线、§8.1 A 案契约下沉 |
| `distill_gates_review_design.md` | distill:gates-review（4.3，27 模块） | 人工准入内核六公理、法定哈希双变体勘察、生产契约保真条款 |
| `live_pipeline_driver_design.md` | T4a 通用全链 live 驱动（待批，T4b 施工依据） | 一条命令全链 CLI 契约、exams/ 目录契约与运行产物布局、断点续跑（cache-state 跳过 + ResendPlan 重发）、双账本预算熔断、两处人工检查点（文件为契约 + webui 可选检视器）、双通道路由与 preflight 放行矩阵、fail-closed stop_reason 总表、T4b 签名清单与测试计划 |

> 迁移备注：`distill_gates_review_design.md` 曾在 `docs/` 顶层、`p1_compilers_release_gate_design.md` 曾在 `migration/`，均已按产出路径纪律（bc-b8b9aca6 ②）归位 design/，旧路径指路占位件已于本次清理删除。历史消息/任务书中的旧路径一律以本表为准。

## docs/prework/（勘察与验证报告）

| 文件 | 内容 |
|---|---|
| `semantic_contracts_split_blueprint.md` | semantic_contracts（380KB）拆分蓝图（knowledge/contracts/ 23 子模块 + facade） |
| `P2_evidence_materials_api_inventory_import_map_20260811.md` | evidence_v2/materials_v2 API 面清点与 import 改写映射表 |
| `P2_import_scan_result_20260811.json` | 全仓 import 扫描结果（机器可读） |
| `P2_黄金样本与字节级验收资产清点_20260811.md` | 黄金样本与字节级验收资产清点 |
| `P2_阶段2引擎抽包搬运_汇总验收_20260812.md` | 阶段 2 引擎抽包搬运汇总验收 |
| `P2_工程决策日志_20260811.md` | 阶段 2 全部工程裁决登记 |
| `P2_公路65章shadow与API_pilot费用估算_20260812.md` | shadow ≈$3 / pilot ≈$5 / 全量 ≈$65-72 费用估算 |
| `P2_交接文档_shadow65实跑中_20260812.md` | shadow65 实跑中断点交接（checkpoint，已被终版报告接续） |
| `P2_s3_人工冲突清单_20260812.md`（+`.json`） | shadow65 终版 s3 人工冲突清单：Tier-1 161 / Tier-1b 0 / Tier-2 884 / Tier-3 67 |
| `P2_s3_Tier1裁决建议_20260812.md` | Tier-1 161 条逐条预审：机判排版级 25 + 读图排除 132，4 条真疑点已人工拍板 4/4 |
| `P2_pilot_黄金五章全链实测_20260812.md` | 黄金五章真实 API pilot 实测：step2b 双中继 5/5、step4 16/16 出图；两个中继能力边界摸清（chatgpt2api-new 体积 40-70KB 上限 / 2chat 首字节 ~125s 窗口） |
| `P2_pilot_step2a质量深评_20260812.md` | step2a 质量攻坚全记录：五轮模型对比（5.5=C → 5.6-sol+补强=A-/B+，噪声归零、diagram 全命中）+ SSE 流式改造；**生产配方定稿：2chat+5.6-sol+补强+流式+自动重发**；step3 三路径证伪、v2 单卡契约为出路 |
| `P2_s4_V1V2差异总报告_20260812.md` | shadow65 终版 s4 V1/V2 差异总报告：65/65 章、vision 610 调用 0 失败、482/643（75%）确认原文无误 |
| `P3_golden_five_replay_report_20260812.md` | 黄金五章 Step2 全链重放验收（A 字节对拍 6/6、B 谱系登记 7 例、C config_hash、D 学习黄金） |
| `P3_未来就绪审计与优化路线图_20260812.md` | 优化期开局审计：pytest 477 全绿基线（本机 84 ERROR 系环境 ACL 损伤，附修复命令）、G1–G5 未来就绪差距、T1–T5 优化路线图（中继预检/配方产品化/step3 v2 单卡契约/通用全链驱动/文档沉淀） |
| `P3_优化施工方案_T1-T5_20260812.md` | T1–T5 详细施工方案（待批未开工）：改动范围铁律（仅新仓，yijian2 零接触）、逐轨文件级改动清单与验收口径、执行序与工时（单干 2-4 天 / 组队 1-1.5 天）、花钱四点合计 ≈$3 逐笔报批 |
| `P3_中继预检实测_20260812.md` | T1 交付验收：`relay_preflight` 双中继首跑矩阵（与昨日实测一致性对照全过）；新信号：2chat strict 强制+流式可续 → step3 v2 前置条件成立；漂移发现：3100 图像信封新增 width/height 键致严格门禁 FAIL，step4 跑前必须预检 |
| `P3_runbook漂移核查_20260812.md` | 新考试接入 runbook §2–§8 与真实代码 API 漂移核查：41 项逐步核查全 PASS、0 硬漂移（签名/关键字参数/枚举/路径/门禁逐一实测吻合），6 条建议级备注 N1–N6（注册表三字段互锁、N+1 产物口径、validate_learning_fixture 收对象、17 守护口径、phase2 五模块记法、模板 revision 陈旧）供 T5 落修 |
| `P3_step4成品图评审底稿_20260812.md` | pilot_gonglu5 step4 16 张成品图人工级评审底稿：A 10 / B 2 / C 4（可接受 75%）；diagram 类 3C1B 为重灾区，另有 ＞550→≥550 符号级考点错误（#08）与表题漏项（#06）；含逐卡证据表、分层统计与缺陷 Top 清单 D1–D5。**修复轨（20260813 追加）**：prompt v2（结构枚举+字形保真硬性要求）重渲 6 缺陷卡，3/4 C 转 A、合成 A14/B1/C1（93.75% 可接受，双评审独立复判 6/6 一致），归因证实 diagram 失真主因系 v1 结构信息缺投；**建议 T4b 默认 `--prompt-variant v2`**；插曲：图像中继信封漂移（data[0] 新增 width/height）致首轮整废，已落良性剥离道并喂给 T1 预检；残留 #12 指标边锚点错挂与 #02 悬空线画像入 D1 结构 gate |
| `P3_webui勘察_20260812.md` | webui 审阅工作台现状勘察（喂 T4a 人工检查点选型）：能力/数据装载清单、已验证启动指引（渲染+回环静态服务，HTTP 200 实录）、对照 step2 准入评审与图像评审两检查点的差距（G-A1–A4 / G-B1–B3）、选型建议=文件侧车为契约 + webui 作检查点①可选检视器 |
| `P3_step3v2实测_20260812.md` | T3 验收实测：r810 六卡单卡回显 live（2chat+5.6-sol+SSE）**6/6 全过、零重发**（17-32s/卡，输出 0.6-1.5K tokens/卡）→ 新卡自动 step3 路径实测可用；含 v1 三卡整体回显证伪史对比、表达族覆盖注记（live 仅 table/ordered_list/formula 三族，diagram/constraint 由离线五族用例保障）、2chat 上游 502 瞬断运维注记 |
| `P3_r810授权装配独立验证_20260813.md` | 转换器第二段（r89/r810 授权装配，实现=`1d85fea`）独立验证记录：双层验证（内核内联参照链 + 实现方 API）金标四级逐字节对拍全过（含 `r84-ingestion-closure` 哈希链）、合成端到端全不变式、相关回归 100 passed；关键发现=语料候选序源头是 r89 `admission-candidates`（权威队列序）而非 r810 manifest 的 candidate_id 排序序；配套 `tests/test_gates_review_authorization_replay.py`（16 用例，`_replay` 独立面） |
| `P3_检查点①追溯审计_20260813.md` | T4b 彩排检查点①追溯审计（只读）：**结论=release-bypass 豁免道闭环（设计 v1.2 预期道）**——四 run 全程无 `checkpoints/admission/` 物料、a/a2 停点系所请求段自然终止（未到达 ◆①）、b2 step3 零外呼消费 r2242 既有 reviewed 发布 fixture（sha 独立复算一致）且 step4 16/16 全部 v25-release-card 身份、a 段 draft 合规滞留未被下游消费——黄金样本 reviewed 门禁未被绕过；旁证=run b 被 preflight fail-closed 熔断；观察：echo 道随转换器落库已解锁、图像通道预检建议、重发/隔离纪律实证 |
| `P3_T2字节级回归守卫_20260812.md` | T2 落地回归守卫：黄金五章重放 A/B 对拍（A 腿 282fd69 前置控制点 / B 腿 cb6c1a2 含 ff3728e），四验收线全过且两腿产物与基线报告**三方字节全等**（六产物 SHA256 逐一相同）；全量 pytest 558 passed/1 skipped（唯一 skip 为 v1_adapter 留守既定项）；双独立验证一致（yijian2-1 执行记录交叉印证）；**结论：T2 无需返工** |
| `P3_T4b前置_伪新考试工作区预铺_20260813.md` | T4b 前置预铺：`exams/gonglu_golden5_rehearsal/` 伪新考试工作区（设计稿 §3 目录契约落地 + 接入点物料 + 命名空间侧车副本哈希互锁链）；关键决策：material_profile_id 保留 `yijian_gonglu_2026`（config_hash 把 profile 身份编进 ev: 锚，改名=黄金五章评审资产整体失锚）；全链事实底座验证跑通（quality/machine gates 双真）；彩排双道语料核实（r2242 16 卡新仓件与老仓字节一致 / r810 六卡编译+门禁绿）；§7.1 转换器核查结论=r84 半程在库、r89/r810 授权半程缺失（彩排不受阻，新考试 step3 正路暂走 release-bypass） |
| `P3_彩排b2_step4十六图人工评审_20260813.md` | T4b 彩排 b2 检查点②人工评审（逐张真实读图 × r2242 真值）：首渲 **A13/B1/C2（87.5%）**；C 卡=#01 术语单字符错（滑坡地段→地质）+ #08『14≤B<18』整条漏渲染，#12 上轮残留（指标边错锚）本轮未复现。**r2 重发道微复审追加**：两卡驳点全修复，#01→A、#08→B（Ⅳ行一处『，→；』标点级新缺陷），**合成 A14/B2/C0 = 100% 可接受**（14 approved/2 manual_review）；三轮 C 卡不重合证明缺陷系逐次渲染随机性：每批必须全量评审，且建议把词级/标点级比对与 visible_texts 覆盖率纳入 step4 自动门禁 |
| `P3_改进A1_step4词级标点覆盖率门禁_20260813.md` | 改进 A1 落地（b2-D1/D2 建议 → standalone 后审计件）：`gates/review/step4_text_gate.py` 三档判定契约（hard=语义 token/覆盖率/空转写；warn=标点级；pass=全对）与匹配口径（行单元/行序无关/行内严格/多物理行真值拆行）；15 离线测试全绿 + 真语料双向 smoke（真值转写 16/16 pass；注入 D1/D2/标点 → 精确 2 hard+1 warn，误伤零）；A2 金标集接口双键兼容；live 视觉道留待 A2 校准 |
| `P3_A2_step4词级门禁live校准_20260813.md` | 改进 A2 交付：18 条标注金标集（`tests/fixtures/step4_word_gate/b2_review_gold.json`，评审结论形式化，档位 2 hard/1 warn/15 pass）+ 门禁双道校准——**两道 hard_fail 召回 2/2 零漏报**（分歧全为误报向安全偏差）；误报归因 G1 等值字形（∑/Σ、＜/<、Ⅰ/I）/G2 reflow 拼接窗过小/G3 行首标签合并，修正建议 R1–R5；A1 当夜按建议落 e300b39+a75e95b 后复测（§3.1）：**离线 18/18（A1 内置消费回归钉住）、live 真 vision 15/18**，残留=∑/Σ 显式折叠+真转写形态标签容忍+转写行约定 prompt；**通道新实证：2chat+gpt-5.6-sol 可 vision 读图**（18/18 成功，≈6.3K prompt tokens/卡，全程 ≈$0.15-0.2）；真 vision 转写实录入库可复放（`b2_live_vision_transcripts_20260813.json`） |
| `P3_T4b彩排_伪新考试全链实测报告_20260813.md` | **T4b 彩排总报告（T4 轨终局）**：一条命令链在 `exams/gonglu_golden5_rehearsal/` 全通至 `completed`——run 谱系（5 dry-run + a/a2/b/b2，护栏两次实弹：b 预检熔断 + 收口期 call-cap 拒付并发双跑）、终版账本 ≈$2.57、检查点①=release-bypass 豁免道（引 f62fd7b 审计）、检查点② 评审→重渲→微复审→两笔终审→二次摄取闭环（终盘 **A14/B2/C0=100% 可接受**，清单 14 approved/2 manual_review 携终审记录）；**彩排正向战果：拓出并修复驱动两处 resume 盲区**（检查点②摄取不可达 e60fbe8 + CLI skip-preflight 图像通道合并重做，均带回归测试与 live 验证），4 项改进项登记（终报归档制/预检先账后线/rejected 重渲道自动化/word-level+覆盖率门禁）；归属三段记（实现+执行=yijian2-5，监护+收口=yijian2-8，评审=yijian2-1，终审=yijian2-ready） |
| `P3_总指挥交接台账_20260813.md` | 协同工作组「一建2重构」总指挥班次台账（终盘定稿）：十轨全景台账（承接/提交号/终态）、事件簿（前任总指挥僵尸窗口证据链、T4b 叠写裁决、两起租约漂移裁决准则、密钥回显披露、hub 版本坑位）、终盘记录（评分 8.9→9.5、下一轮改进清单五项、用户侧遗留关注项三条）——总指挥接任者第一读物 |
| `P3_协同机制经验教训_20260813.md` | 协同机制经验教训总结（总指挥班后复盘）：八类失效模式实录 F1-F8（僵尸会话/卡死误报/租约漂移/截止重置/撞名/hub 能力缺失/本机 ACL 病/消息交错，各含实例与根因）、零产物损失的七道防线复盘、可复用打法 P1-P11（实际写手优先/双独立验证默认制/continue-not-redo/哨兵条款/台账入仓/评审人预定/诚实反压等）、hub 平台改进建议 H1-H7 |

## docs/v2/（随迁历史资产，只读）

| 文件 | 作用 |
|---|---|
| `Phase2R.1.3_V25源锚定完整表格图示桥接对账报告_20260729.md` | RC1 闭包（phase2r13）验收报告冻结输入 |
| `artifacts/phase2r13_v25_reconciliation_offline_report_20260729.json` | 同上机器报告 |
| `baselines/gonglu_v1_baseline_20260724.json` | gonglu V1 黄金基线（黄金重放比对基准） |

## migration/（一次性工具）

| 文件 | 作用 | n11 本轮结果 |
|---|---|---|
| `gen_extraction_manifest.py` | 清单 v2 生成器（注解写入生成器，重生不丢） | — |
| `bring_upstream_knowledge_core.py` | core 19+2 模块搬运 | — |
| `split_semantic_contracts.py` | semantic_contracts 拆分执行 | — |
| `build_admission_fixtures.py` | admission_fixtures/admission_legacy_hash 构建 | — |
| `migrate_core_tests.py` | 随核心迁移测试搬运 | — |
| `verify_manifest_audit.py` / `verify_manifest_audit2.py` | 迁走↔留守依赖对账 | PASS（migrate=21, stay=152） |
| `verify_contracts_hash_parity.py` | 拆分前后法定哈希/ID/prompt/golden 全等 | HASH PARITY OK |
| `verify_contracts_schema_parity.py` | 28 模型 JSON Schema 字节平价 | 28/28 OK |
| `verify_admission_fixtures.py` | 准入 fixture 族真实冻结工件重放 | PARITY OK |
| `verify_compilers_parity.py` | compilers/gates-release 双跑对照 | 37/37 byte-identical |
| `golden_five_replay.py` | 黄金五章 Step2 全链重放 | 四验收线全过 |
| `relay_preflight.py` | 中继能力预检（T1：体积阶梯/strict 遵从/首字节窗口/图像端点四合一 + 需求画像结论；吸收 probe_relay_body_limit.py；库入口 `run_preflight() -> report`，`verdict_code` 四值枚举） | 双中继首跑见 `prework/P3_中继预检实测_20260812.md` |
| `run_pilot_gonglu5_step3_v2.py` | T3 v2 单卡回显独立 live 驱动（SSE 流式 + 逐卡重发四不变式） | r810 六卡 6/6 首发全过（`prework/P3_step3v2实测_20260812.md`） |
| `prelay_gonglu5_rehearsal_workspace.py` | T4b 前置：伪新考试工作区预铺执行器（幂等：PDF 复制/命名空间侧车副本/哈希链重算/事实底座全链自验证/彩排双道语料核实） | accepted=true（`prework/P3_T4b前置_伪新考试工作区预铺_20260813.md`） |
| `run_step4_text_gate.py` | 改进 A1：step4 词级/标点/覆盖率门禁 CLI 薄壳（--transcripts-json 零网络道 / --allow-live 视觉道；核心在 `gates/review/step4_text_gate.py`） | 真语料双向 smoke 通过（`prework/P3_改进A1_step4词级标点覆盖率门禁_20260813.md`） |

## P3 优化期新增引擎件（src/，速查）

| 件 | 作用 | 出处 |
|---|---|---|
| `compilers/step3_response_v2.py` | step3 **v2 单卡回显契约**：摸平 `SingleCardModelInput`（无嵌套卡、geometry 不进回显）、三层版本号族（input=`step3-response/2.0` / result=`step3-response-result/2.0` / draft 卡=`step3-response-draft-card/2.0`，回显错版即判负）、`make_single_card_inspector` 可直作 `inspect_chat_envelope` 的 inner_inspector | T3（282fd69）；live 实测 `prework/P3_step3v2实测_20260812.md` |
| `knowledge/reinforcement.py` | step2a 补强常量 `STEP2A_REINFORCEMENT_V1` + `reinforcement_id`（生产配方件；transport 注入、不进 identity） | T2（ff3728e；含 23 个离线测试 + gonglu_01 live 冒烟 4/4 绿） |
| `gates/review/extraction.py` | typed contract **提取面**（r71/r8/r81 蒸馏补搬）：`ExtractionSource`（step2/facts 准入产物投影）+ `ManualSelectionSpec`（人工圈选，五确认背书）→ r84 五族契约（逐元素 provenance、r8x 谱系封印、显式元素身份回放通道）→ 决策/队列/r84 reviewed fixture 装配 | 提取面补搬节点（P3 方案 §8.2 v1.3）；r810 六候选金标回放逐字节 6/6 全过 + 34 例离线测试 |
| `gates/review/authorization.py`（装配半程扩充） | **r89/r810 授权装配半程**（全仓原无 `ReviewedTypedEvidenceFixture` 构造调用）：`authorize_extraction_batch` 提取批一键授权（授权决策构造 → export_r84 → 严格摄取按**队列序**重建 → r810 fixture 构造）；冻结槽位引擎期以提取面工件绑定 | 同节点（P3 方案 §8.2 v1.3 范围细化）；语料回放 `r84_fixture_sha256` 与 r810 fixture 整体逐字节对拍全过 + 8 例测试；新考试 step3 正路引擎侧闭环 |
| `gates/review/typed_evidence_export.py` | **T4b 驱动权威入口**（签名由 T4b 方定稿）：`export_reviewed_typed_evidence_fixture(released, queue_items_by_candidate, queue_sha256, authorization)` → r810 fixture；四哈希链内推导、候选序=released 序、`ExplicitProductionAuthorization` 显式人工授权输入 | 同节点收口件；返回对象直通 `compile_reviewed_fixture`（三族放行集实测）+ 10 例入口测试（fail-closed 边界全覆盖） |
| `pipeline/live_run.py` | live 编排原语：`ResendPlan`（重发=新身份/禁复用/轮上限）、`load_run_env` 与双通道取值（`IMAGE_*` 显式键优先） | T2（ff3728e）；T4b 驱动在此扩展 |

## 裁决登记速查（现行口径）

| 裁决 | 内容 | 登记处 |
|---|---|---|
| bc-888c6031（布局终裁） | 十包：materials/evidence/knowledge/compilers/gates/pipeline/webui/providers/common/utils；common=冻结底座、utils=非冻结杂项 | `engineering_skeleton.md` §3 |
| bc-9d82a196（扩容终裁） | common 扩为六件 {hashing, gates, io, freeze, junit, schema}，签名落地即冻 | `engineering_skeleton.md` §3 |
| bc-b8b9aca6（铁律×2） | 旧契约法定哈希逐字节随包搬运、禁入 common/hashing；yijian2 只读、产出路径纪律 | 各设计稿 §哈希条款 |
| A 案（跨桶契约下沉） | 跨桶持久化数据契约唯一权威定义于 `knowledge/contracts/admission_fixtures.py`（+法定哈希搬运件 admission_legacy_hash.py） | 清单 v2 8 条【A案】注解；gates-review 稿 §4/§5 |
| R1 | requires-python >= 3.11 | `engineering_skeleton.md` §2 |
