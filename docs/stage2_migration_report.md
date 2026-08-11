# 阶段 2 迁移总报告（总集成验收 n11）

- 日期：2026-08-12；验收仓：`C:\Users\liushaocheng\Desktop\getinfo\exam-card-engine`；基准仓：yijian2（全程只读）
- 验收四项：① 全仓 pytest 全绿；② 依赖方向守护全绿；③ 清单 26/26 对账；④ 本总报告
- 复核方式：全部检查在本验收轮**实际重跑**（非引用他人历史结论）

## 总判定：✅ 四项全部通过

| 验收项 | 结果 |
|---|---|
| ① 全仓 pytest | **462 passed / 1 skipped / 0 failed**（skip=`test_v1_adapter_v2.py:71`，full_check 属 V1 生产工具留守旧仓，清单 §五-2 口径，合法） |
| ② 依赖方向守护 | **全绿**（守护清单见 §2） |
| ③ 清单 26/26 对账 | **26/26 RECONCILED**（逐模块存在性+非空核对，见 §3） |
| ④ 总报告 | 本文件 |

## 1. 全仓测试面

- 462 用例分布：骨架冒烟（10 包/嵌套包/T4 禁网守卫/T3 写盘收口）、common 六件（hashing/gates/io/freeze/junit/schema + 依赖守护）、contracts 拆分（facade 147 名完整性 + 分层 DAG 回边零 + admission）、compilers 四线（step3 card/response、step4 contract/image）、gates 十线（review 七 + release 三）、providers 四线（chat 信封/图像信封/请求投影/能力诊断，129 用例录制 fixture 驱动）、pipeline/webui、随核心迁移的 21 个旧仓测试（fact/semantic/learning/continuation/diagram/v25 系）。
- 测试隔离规范 T1–T8 全程生效（live 标记默认剔除、默认禁网、写盘收口 `.test-tmp/`）。

## 2. 依赖方向守护（布局终裁 bc-888c6031 + 扩容终裁 bc-9d82a196）

| 守护 | 文件 | 覆盖 |
|---|---|---|
| common 叶包纪律 | `tests/test_common_dependency_guard.py` | 模块集锁定六件；不得 import 任何 examcard 内部包；第三方仅 pydantic/yaml；freeze 无 git/subprocess/glob/phase 路径集；无网络模块 |
| providers 方向 | `tests/test_providers_dependency_guard.py`（本验收轮补齐） | providers 只可 import examcard.common / examcard.utils；相对导入不越包 |
| contracts 分层 | `tests/test_knowledge_contracts_layering.py` | 拆分 23 模块 + admission 对 + V25 七件严格 DAG、运行期回边零 |
| facade 完整性 | `tests/test_knowledge_contracts_imports.py` | 147 外部名 + 转口全部可达 |

业务包方向（可 import common/utils/providers）是许可而非禁令，无需守护。

## 3. 清单 26/26 对账（extraction_manifest v2 迁走集）

core 19 + V25 现役 2 + RC1 运行时闭包 5 = 26，逐模块核对新仓归宿全部存在且非空（复核命令：临时脚本存在性检查，本轮输出 `RECONCILED 26/26`）：

| # | 旧模块 | 新仓归宿 | 备注 |
|---|---|---|---|
| 1 | semantic_contracts | `knowledge/contracts/`（23 子模块 + facade） | 拆分蓝图执行；另 admission_fixtures/admission_legacy_hash 两件 |
| 2 | fact_engine | `knowledge/fact_engine.py` | ROOT 锚点已修 parents[3]（n5，n4 复核无副作用） |
| 3 | semantic_llm | `knowledge/llm.py` | |
| 4 | learning_fixture | `knowledge/learning_fixture.py` | 总指挥裁决：运行时闭包优先，改落包内 |
| 5 | table_continuation_integration | `knowledge/continuation/integration.py` | |
| 6 | continuation_review | `knowledge/continuation/review.py` | |
| 7 | table_continuation | `knowledge/continuation/core.py` | |
| 8 | semantic_projection | `knowledge/projection.py` | |
| 9 | teaching_quality | `knowledge/quality.py` | |
| 10 | table_continuation_request_fixture | `tests/fixtures/table_continuation_request_fixture.py` | |
| 11 | schema | `knowledge/schema.py` | |
| 12 | step2b_card_golden | `knowledge/step2b_card_golden.py` | 惰性导入闭包成员，改落包内 |
| 13 | table_repair_sidecar | `knowledge/continuation/repair_sidecar.py` | |
| 14 | v1_adapter | `knowledge/adapter_v1.py` | 逐字节一致（0 行改写） |
| 15 | diagram_sidecar | `knowledge/diagram/sidecar.py` | |
| 16 | diagram_disposition | `knowledge/diagram/disposition.py` | |
| 17 | step2b_bridge | `knowledge/planner/bridge.py` | |
| 18 | state_machine | `knowledge/state_machine.py` | |
| 19 | identity | `knowledge/identity.py` | 逐字节一致 |
| 20 | v25_generic_contracts | `knowledge/contracts/v25_generic_contracts.py` | V25 现役 |
| 21 | v25_teaching_gate_layers | `knowledge/contracts/v25_teaching_gate_layers.py` | V25 现役 |
| 22–26 | phase2r11/12/13/14/142 | `knowledge/contracts/phase2r1*.py` | RC1 运行时闭包整簇 |

附：evidence_v2（17 文件）→ `evidence/`、materials_v2（3 文件）→ `materials/`（清单 §五；identity/cache 法定哈希文件逐字节一致，SHA256 验证）。

## 4. 各节点 diff / 平价结果索引（本轮全部重跑复核）

| 平价/审计工具（migration/） | 本轮结果 |
|---|---|
| `verify_manifest_audit.py`（迁走→留仓依赖零 + 迁走测试闭包自洽） | PASS（migrate=21, stay=152） |
| `verify_manifest_audit2.py`（留守测试卡点明细） | exit 0 |
| `verify_contracts_hash_parity.py`（拆分前后法定哈希/ID/prompt/golden 重放/step2b 全等） | HASH PARITY OK |
| `verify_contracts_schema_parity.py`（28 模型 JSON Schema 字节平价 + git 基线） | 28/28 OK（202837 字节/侧） |
| `verify_admission_fixtures.py`（准入 fixture 族真实冻结工件重放） | PARITY OK |
| `verify_compilers_parity.py`（compilers/gates-release 双桶 37 对象） | 37/37 byte-identical |
| `golden_five_replay.py`（黄金 5 章 Step2 全链重放，报告 `docs/prework/P3_golden_five_replay_report_20260812.md`） | A 产物字节对拍 6/6；B 内容寻址 ID 迁移漂移 0（7 例旧数据谱系登记）；C config_hash 1499/1501（2 例同源谱系）+ make_cache_key 新旧 40/40；D 录制学习黄金全过 |

设计契约（docs/）：`engineering_skeleton.md`（骨架 + T1–T8 + 终裁登记）、`extraction_manifest.md`（v2）、`design/providers_distillation_design.md`（含 12 不变式与冻结签名依据）、`design/p1_compilers_release_gate_design.md`、`design/distill_gates_review_design.md`、`prework/` 四件（黄金清点/import 映射/拆分蓝图/扫描结果）。

包结构（10 包终裁）：common 7 文件、compilers 5、evidence 17、gates 20、knowledge 54、materials 3、pipeline 3、providers 3、utils 1、webui 2；资产树 data/（evidence_v2/facts_v2/diagram_review_v2/semantic_llm_v2/shadow_v2）、configs/、schemas/、docs/v2/baselines 已随迁。

## 5. 遗留风险

1. **旧数据谱系异常 7 例**（P3 重放报告 §B/§C 登记）：fusion 后处理单元的 revision_id/config_hash 与构建期输入不可由持久化面重导——已证明非迁移漂移（新旧实现同输入同输出、A 线字节全等），但后续若有人对黄金页做"全量 ID 重铸"会撞上，需引用该登记。
2. **旧仓侧切换批次未执行**：yijian2 内 186 消费文件/294 行 import 改写 + `scripts/evidence_v2`、`scripts/materials_v2` 目录移除（P2 映射表 §3.2/§3.4 的遮蔽风险在切换时生效）。当前旧仓只读铁律下属后续专门批次。
3. **providers 后续批次**：transport 有界传输/新鲜重发恢复/remediation 整改循环骨架（设计稿 §4/§8 的 5–6 步）未实施；当前首批 base+envelope_profiles 已交付并测试。
4. **文档重复件**：`docs/distill_gates_review_design.md` 与 `docs/design/distill_gates_review_design.md` 两份同名（以 design/ 下为准），建议清理其一。
5. **git 未初始化提交**：全仓处于 untracked 状态，schema parity 的 git 基线比对依赖既有 blob——建议尽快初始化提交并打 stage2 基线 tag（属仓库管理动作，未在本验收内擅自执行）。
6. **manifest v3 补注**在总指挥待办池（learning_fixture/step2b_card_golden 包内落点、r33 归档说明、谱系登记引用）。
7. 合法 skip 1 例（full_check V1 工具留守），预期长期存在直至 V1 退役。

## 6. 下一步建议

1. git 初始化提交 + tag `stage2-baseline`（先决：无）；随后把黄金重放四验收线固化为带 `golden` 标记的 pytest 套件供回归复跑。
2. 执行旧仓切换批次（P3：消费方改写 + 目录移除 + 全量旧仓 pytest 复验），需总指挥解除 yijian2 只读并排期。
3. providers transport/recovery/remediation 批次实施（设计稿 §8 步骤 5–6）。
4. 阶段 3：pipeline/webui 功能面充实、V25 闭包与黄金脚手架解耦（manifest §二-2 的 ⚠ 项）、common 六件按需扩容走终裁流程。
