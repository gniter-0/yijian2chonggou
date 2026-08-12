# exam-card-engine 文档总索引

更新：2026-08-12（P2·docs 重复件收拾 + 总索引；n11 总报告遗留项 5.4 已清理）。

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
| `engineering_skeleton.md` | 工程骨架规范：pyproject/十包布局与依赖方向、测试隔离 T1–T8、布局终裁登记（bc-888c6031 十包 + bc-9d82a196 common 六件扩容） |
| `extraction_manifest.md` | 迁移清单 v2：142 模块去向对账基准（含 8 条【A案】跨桶契约下沉注解） |
| `stage2_migration_report.md` | 阶段 2 总集成验收（n11）：pytest 462 全绿、依赖守护、26/26 对账、遗留风险与下一步 |
| `new_exam_onboarding_runbook.md` | 新考试（造价/消防）接入手册 |
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
| `P3_step4成品图评审底稿_20260812.md` | pilot_gonglu5 step4 16 张成品图人工级评审底稿：A 10 / B 2 / C 4（可接受 75%）；diagram 类 3C1B 为重灾区，另有 ＞550→≥550 符号级考点错误（#08）与表题漏项（#06）；含逐卡证据表、分层统计与缺陷 Top 清单 D1–D5 |
| `P3_webui勘察_20260812.md` | webui 审阅工作台现状勘察（喂 T4a 人工检查点选型）：能力/数据装载清单、已验证启动指引（渲染+回环静态服务，HTTP 200 实录）、对照 step2 准入评审与图像评审两检查点的差距（G-A1–A4 / G-B1–B3）、选型建议=文件侧车为契约 + webui 作检查点①可选检视器 |
| `P3_step3v2实测_20260812.md` | T3 验收实测：r810 六卡单卡回显 live（2chat+5.6-sol+SSE）**6/6 全过、零重发**（17-32s/卡，输出 0.6-1.5K tokens/卡）→ 新卡自动 step3 路径实测可用；含 v1 三卡整体回显证伪史对比、表达族覆盖注记（live 仅 table/ordered_list/formula 三族，diagram/constraint 由离线五族用例保障）、2chat 上游 502 瞬断运维注记 |

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
| `relay_preflight.py` | 中继能力预检（T1：体积阶梯/strict 遵从/首字节窗口/图像端点四合一 + 需求画像结论；吸收 probe_relay_body_limit.py） | 双中继首跑见 `prework/P3_中继预检实测_20260812.md` |

## 裁决登记速查（现行口径）

| 裁决 | 内容 | 登记处 |
|---|---|---|
| bc-888c6031（布局终裁） | 十包：materials/evidence/knowledge/compilers/gates/pipeline/webui/providers/common/utils；common=冻结底座、utils=非冻结杂项 | `engineering_skeleton.md` §3 |
| bc-9d82a196（扩容终裁） | common 扩为六件 {hashing, gates, io, freeze, junit, schema}，签名落地即冻 | `engineering_skeleton.md` §3 |
| bc-b8b9aca6（铁律×2） | 旧契约法定哈希逐字节随包搬运、禁入 common/hashing；yijian2 只读、产出路径纪律 | 各设计稿 §哈希条款 |
| A 案（跨桶契约下沉） | 跨桶持久化数据契约唯一权威定义于 `knowledge/contracts/admission_fixtures.py`（+法定哈希搬运件 admission_legacy_hash.py） | 清单 v2 8 条【A案】注解；gates-review 稿 §4/§5 |
| R1 | requires-python >= 3.11 | `engineering_skeleton.md` §2 |
