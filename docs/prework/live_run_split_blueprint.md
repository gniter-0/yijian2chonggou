# live_run.py 拆分蓝图（P3 · cm26 落地稿）

- 生成日期：2026-08-24
- 对象：`src/examcard/pipeline/live_run.py`（live 生产道驱动：预算护栏、四个 stage 执行器、两个人工检查点、总编排）
- 方法：只读 AST 扫描（顶层符号表 + 顶层符号互引图，区分运行位与注解位）→ 分组 → 机械切片执行器整段搬运
- 先例：`docs/prework/fact_engine_split_blueprint.md`（cm21，12 兄弟）、`docs/prework/llm_split_blueprint.md`
  （cm25，7 兄弟）；本稿沿用同一套节号，§8-2 = facade 完整性，§8-3 = 分层守卫
- 执行器：`.test-tmp/cm26_split.py`（机制承自 `cm25_split.py`，两处必须不同，见 §2-6、§2-7）

## 1. 现状盘点（依据，全部机器扫描）

| 项 | 实测值 |
|---|---|
| 拆前体量 | 1,775 行 / 86,399 字节（**纯 CRLF**、1,774 个 CRLF、0 个孤立 LF、无 BOM） |
| 顶层符号 | 50 个（常量 11、函数 29、类 10：pydantic StrictModel 2 + 异常 3 + frozen dataclass 2 + 普通类 3） |
| 作者分节 | 14 个横幅块（13 个 `# ---` + 1 个 `# ===` 的 T4b 架构说明），横幅与被引领符号之间隔空行 |
| 外部使用面 | 6 个 importer 文件、28 个名字（全部是公开名，无私有助手外泄） |
| 整模块导入 | 拆前 0 处；新增的 §8-2 封印是家族里唯一一处（`import examcard.pipeline.live_run as live_run`） |
| `import *` | 0 |
| `__all__` | 拆前没有；`pipeline/__init__.py` 不做再导出 |
| mypy 豁免 | 无 `examcard.pipeline.live_run` 活条目（早前已按正解修掉 5 条并退出清单，只剩历史注释里的记账），故拆分**不需要新键** |
| 反射导入 | 0（全仓无字符串模块名指向本文件，与 `v25_generic_contracts` 的 `__import__` 情形不同） |
| 运行期环 | 0 个 SCC（顶层互引图无环），每条分组边界都可切，无需 `TYPE_CHECKING` 断环 |
| 既有封印的管辖 | `tests/test_error_sink_redaction_seal.py`、`tests/test_legacy_root_resolution_seal.py` 都已是 `rglob` 口径，拆完自动扫到 14 个兄弟，**不需要前置提交**（cm25 那次需要） |

## 2. 拆分原则

1. **只搬不改**：符号连同其上的注释整段搬运，判定口径、字面量、报错文本一字不动；
2. **同级平铺 + 原名门面**：子模块落在 `src/examcard/pipeline/` 下作 `live_*.py` 兄弟，`live_run.py`
   原地改成只做再导出的门面，导入路径 `examcard.pipeline.live_run` 一字不改；
3. **层序即 import 方向**：子模块只许引用层序在其上方的兄弟，回边为零（§3、§8-3）；
4. **字节权威不受影响**：规范化 JSON 与 sha256 载荷从不写入 `__module__` / `__qualname__` / `__file__`，
   模块路径变化不进哈希；报告里的 freeze 与自证哈希都只吃业务字段；
5. **仓根自锚点原样保留**：`_REPO_ROOT = Path(__file__).resolve().parents[3]` 锚的是
   src/examcard/&lt;pkg&gt;/&lt;mod&gt;.py 这一深度。**同级搬迁不改变深度**，所以这个字面量不许跟着「顺手」调整；
   §8-2 直接断言它等于仓根，且仓根下 `pyproject.toml` 与缺省单价表都在；
6. **EOL：全家纯 CRLF**（本次与 cm25 的第一处不同）。cm25 的 `llm.py` 是纯 LF，本文件是纯 CRLF，
   而编辑工具一律写 LF。执行器读入即验「无 BOM + 无孤立 LF + 确有 CRLF」，内部按 LF 处理，
   落盘前整体转回 CRLF 并复验生成物无孤立 LF；两个封印测试也走同一条落盘器
   （`.test-tmp/cm26_tests_emit.py`），因为 `tests/test_pipeline_live_run{,_orchestration}.py` 同样是纯 CRLF。
   在字节权威仓里降一次 EOL 等于一次全文件改动，这条不是洁癖；
7. **横幅随节搬迁**（第二处不同）。cm25 的 `leading_comment_start` 只并「紧贴」符号的注释，
   在本文件会把 47 行横幅判成「符号区间外的非空行」而中止。cm26 版允许**跨空行并入一次**：
   每个横幅跟着它引领的那一节进入新模块头部，一个字都不丢；
8. **pydantic 字段注解必须在定义模块可解析**：全家开 `from __future__ import annotations`，
   字段注解是字符串，模型建完靠的是*定义模块*的命名空间。两个 StrictModel 都落在 `live_reports`，
   执行器按 AST 出现的名字自动推导 import 面，§8-2 再用 `__pydantic_complete__` 兜底。

## 3. 目标布局（层序即 import 允许方向，只许引用上方）

| 层 | 模块 | 行/字节 | 符号数 | 运行期引用的兄弟 |
|---|---|---|---|---|
| 0 | `live_support.py` | 59 / 2,739 | 12 | — |
| 0 | `live_budget.py` | 124 / 5,803 | 3 | — |
| 0 | `live_paths.py` | 111 / 5,565 | 2 | — |
| 1 | `live_env.py` | 76 / 3,068 | 8 | support |
| 1 | `live_resend.py` | 79 / 3,825 | 2 | support |
| 1 | `live_preflight.py` | 37 / 1,579 | 1 | support |
| 1 | `live_reports.py` | 95 / 4,019 | 4 | support |
| 1 | `live_stage_step3.py` | 262 / 12,362 | 3 | budget, paths |
| 1 | `live_stage_step4.py` | 192 / 8,268 | 1 | budget, paths |
| 1 | `live_checkpoint.py` | 188 / 8,831 | 4 | support |
| 2 | `live_config.py` | 128 / 5,705 | 3 | resend, support |
| 2 | `live_stage_samples.py` | 126 / 6,069 | 2 | budget, resend |
| 2 | `live_rehearsal.py` | 96 / 3,345 | 3 | reports |
| 3 | `live_orchestrate.py` | 366 / 21,102 | 2 | 除 env 外的 12 个 |
| — | `live_run.py`（门面） | 127 / 5,184 | 0（50 名转出） | 全部 14 个 |

合计 15 个文件 / 97,603 字节。较拆前增 11,204 字节，全部来自 14 份模块 docstring 与被复制的 import 头：
执行器逐符号核对 50/50 已分配、符号区间外无非空行，且**符号正文与 HEAD 逐字节相等**
（两侧同为 81,923 字节，LF 计法；核对脚本 `.test-tmp/cm26_verify.py`）。

分组不是重新设计，而是**认领作者自己的分节**：50 个符号恰好构成 14 段连续的横幅区间，
唯一的合并是文件头 3 个常量并进 `live_support`（它们与 T4b 横幅下的 stage 常量、时间助手同属无依赖底座）。
`_STAGE_ORDER` 从 `live_support` 移到 `live_config`，正是为了让「运行配置与运行状态机（§2 / §5.1）」
这块横幅跟对模块走。

`live_env` 是家族内的孤岛：**没有任何兄弟引用它**。`.env` 取值由 CLI 侧读出后作为参数传进来，
家族内部不自己去读运行配置——这条纪律拆分后反而更显眼了。

## 4. 环与注解的事实

顶层互引图按 §3 分组后**回边 0 条**（运行位与注解位一并检查），拆前本来也没有 SCC。
与 cm25 的差别在于 pydantic 面积很小：本文件只有 `LiveStageReport` / `LiveRunReport` 两个 StrictModel，
且互不做字段注解、同落 `live_reports`，其余 8 个类是 dataclass 或异常。因此 PEP 563 的风险面比 llm 那次窄，
但仍按同样口径断言 `__pydantic_complete__`——「模型以未建完状态存在、直到首次实例化才炸」这种坏法
不该靠面积小来赌。

## 5. facade 与外部改写策略

- `live_run.py` 保留原模块 docstring（由执行器从源码切下，不手抄）+ 一段拆分说明 + 14 行
  `from .live_* import ...` + `__all__`；
- `__all__` 列全 50 名，**含 11 个私有名**（`_REPO_ROOT`、`_STAGE_ORDER`、`_REASON_PATTERN` 等）：
  门面的职责是把拆前的名字面 100% 兜住，不是趁搬家重新定义什么算公开；
- 6 个 importer（4 个 migration 脚本、2 个测试）**一行不改**。

## 6. 外部 28 名 → 新子模块归属（验收对照表）

| 外部名 | 新归属 | importer 数 |
|---|---|---|
| `build_admission_checkpoint` | `live_checkpoint` | 3 |
| `load_run_env` | `live_env` | 3 |
| `ExamPaths` | `live_paths` | 2 |
| `LiveRunConfig` | `live_config` | 2 |
| `ResendPlan` | `live_resend` | 2 |
| `orchestrate` | `live_orchestrate` | 2 |
| `DEFAULT_PRICING_RELATIVE` | `live_support` | 1 |
| `LiveBudgetExceeded` | `live_budget` | 1 |
| `LiveRunBudget` | `live_budget` | 1 |
| `load_live_pricing` | `live_budget` | 1 |
| `MissingUpstreamArtifact` | `live_paths` | 1 |
| `ResendRoundsExhausted` | `live_resend` | 1 |
| `default_env_path` | `live_env` | 1 |
| `text_base_url` | `live_env` | 1 |
| `text_api_key` | `live_env` | 1 |
| `image_base_url` | `live_env` | 1 |
| `image_api_key` | `live_env` | 1 |
| `image_channel_fallback` | `live_env` | 1 |
| `evaluate_preflight_gate` | `live_preflight` | 1 |
| `build_stage_report` | `live_reports` | 1 |
| `build_live_run_report` | `live_reports` | 1 |
| `load_typed_fixture_compilation` | `live_stage_step3` | 1 |
| `ingest_admission_decisions` | `live_checkpoint` | 1 |
| `emit_image_review_sheet` | `live_checkpoint` | 1 |
| `ingest_image_review_sheet` | `live_checkpoint` | 1 |
| `execute_stage_samples` | `live_stage_samples` | 1 |
| `budget_rehearsal_estimate` | `live_rehearsal` | 1 |
| `derive_stage_call_caps` | `live_rehearsal` | 1 |

外部面覆盖 13 个兄弟；唯一没有外部名的是 `live_stage_step4`——出图执行器只经 `orchestrate` 到达，
这也是它拆出来后唯一的调用路径。§8-2 把这条事实写成断言，将来谁绕开总编排直接调它，测试会先说话。

## 7. 迁移步骤（一次提交完成，每步可独立验证）

1. 预研：AST 扫顶层符号表、互引图、外部使用面、EOL 指纹；确认无反射导入、无 SCC、两个既有封印已是 rglob 口径；
2. 机械切片：`.test-tmp/cm26_split.py` 按 §3 分组切片、自动推导各子模块 import 面、写**纯 CRLF** 兄弟模块
   并把 `live_run.py` 改成门面（先 `--dry-run` 核对文件数/字节/符号覆盖，再落盘）；
3. 搬运无损核对：`.test-tmp/cm26_verify.py` 拿 `git show HEAD:...live_run.py` 逐符号比对（50/50 逐字节相等）；
4. `ruff check src tests migration` + 全仓 `mypy` + 严格档（gates/compilers）+ 无豁免档对账
   （无豁免档条数必须与拆前逐条相同，`live_*` 家族贡献 0 条）；
5. 补两条封印测试（§8-2 / §8-3），经 `.test-tmp/cm26_tests_emit.py` 落成纯 CRLF，并做咬合验证（§8-6）；
6. 全量 `pytest --basetemp .test-tmp/<name> --cov --cov-fail-under=75`，跑完还原 7 个被脏的黄金件；
7. EOL/BOM 平权审计（`.test-tmp/eolaudit.py`，要求 `VIOLATIONS=0`）；
8. 同步活文档：`docs/engineering_skeleton.md` 的 `pipeline/` 树补 `live_*.py` 家族、
   `docs/prework/handoff_20260824.md`（backlog 第 2 项收口、验证基线换 cm26 数字、纯 CRLF 名单补家族、
   巨石榜摘掉 live_run 并补家族对照）。**`docs/extraction_manifest.md` 与
   `migration/gen_extraction_manifest.py` 本次不动**：两者只登记从 yijian2 抽包的 knowledge 核心模块，
   全文没有一处 `pipeline/`（已 grep 核实），`live_run.py` 是新仓自产模块，不在清单口径内。

## 8. 不变量验收（拆分算成的标准）

1. **行为平价**（一票否决）：`tests/test_pipeline_live_run.py`、`test_pipeline_live_run_orchestration.py`
   与两条新封印全量通过，全量套件与覆盖率门槛（75）不退。
2. **facade 完整性**（`tests/test_pipeline_live_run_imports.py`）：50 名逐一可从 `examcard.pipeline.live_run`
   取到且 `__all__` 精确相等；§6 的 28 个外部名逐个 `is` 绑定新归属模块里的同一对象；门面自身不定义实现；
   `_REPO_ROOT` 仍等于仓根；两个 StrictModel `__pydantic_complete__` 为真。
3. **分层守卫**（`tests/test_pipeline_live_run_layering.py`）：AST 扫 `live_*` 兄弟边，断言层序全覆盖、
   回边 0 条、层序条目仍是单文件模块、**兄弟不许回头 import 门面**（cm26 新增，堵住
   门面→兄弟→门面 的环）、门面 14 个兄弟一个不漏。
4. **既有封印仍有管辖**：脱敏封印与遗留根解析封印都按 `rglob` 清点，拆完自动覆盖 14 个兄弟；
   密钥取值仍只在 `live_env` 内驻留，不进任何报错串。
5. **非本家族模块不受影响**：`pipeline/` 下的 `run.py` / `shadow.py` / `shadow_wiring.py` 按 `live_` 前缀
   天然在守卫管辖外，而将来任何新的 `live_*.py` 都会被层序全覆盖断言抓住。
6. **咬合验证**（`.test-tmp/cm26_bite.py`，7 条探针全部响亮失败后按字节还原，并用 sha256 指纹自证）：
   ① 造回边；② 多放一个不在层序里的 `live_probe.py`；③ 兄弟回头 import 门面；
   ④ 把某个兄弟改成子包；⑤ 把 `__pydantic_complete__` 置 False；⑥ 把 `parents[3]` 改成 `parents[4]`；
   ⑦ 把归属表写错一格。探针 ⑦ 首轮暴露封印自身的一个缺陷：`getattr` 会先抛 `AttributeError`，
   只报出第一条坏项——已改为哨兵取值，28 条走完一次给齐清单。
7. **旧仓不动性**：本次拆分对 `yijian2` 零写入。

## 9. 风险清单

| # | 风险 | 处置 |
|---|---|---|
| R1 | 后续有人往 `pipeline/` 里新增 `live_*.py` 而不进层序 | §8-3 的层序全覆盖断言会响亮失败（咬合探针 ② 已兑现） |
| R2 | 有人把某个子模块改成子包（`live_x/__init__.py`），使非递归 `glob("*.py")` 静默失去管辖 | §8-3 同时清点同名目录（探针 ④ 已兑现）；这条教训来自 contracts 家族 |
| R3 | 门面被「清理未使用导入」误删 | 50 名全在 `__all__` 内，pyflakes 视为已用；ruff 实测 0 告警 |
| R4 | 模型字段注解在新模块里解析不了（最隐蔽的一种坏法） | §8-2 的 `__pydantic_complete__` 断言（探针 ⑤ 已兑现） |
| R5 | `_REPO_ROOT` 的 `parents[3]` 被「顺手」改深浅 | §8-2 直接断言它等于仓根且仓根下两个锚文件在（探针 ⑥ 已兑现） |
| R6 | 新文件混进 LF，触发日后一次全文件 EOL 抖动 | 执行器与测试落盘器双向验 EOL；提交前 `eolaudit.py` 要求 `VIOLATIONS=0` |
| R7 | T4b 的 `# ===` 横幅（家族级架构说明）随 `LIVE_STAGES` 落在 `live_support`，位置偏低 | 有意为之：保住「只搬不改 / 50-of-50 逐字节相等」。把它上提到门面 docstring 是后续单独一步，不与搬运混做 |
| R8 | `live_orchestrate` 仍偏大（366 行 / 21.1KB） | 可接受：它是唯一的汇聚点，逻辑就是那条 DAG。真要再切须动设计——`orchestrate` 内部的 stage 分派与检查点暂停是天然的二次边界 |

## 附：本蓝图分析口径

- 顶层定义图：`ast` 解析 50 个顶层 def/class/常量，收集互相引用（区分运行位与注解位，PEP 563 下注解不求值）；
- 分区校验：50 符号全部有归属（0 未分配、0 重复），符号区间之外无非空行，生成物无孤立 LF、无 BOM；
- 搬运无损：逐符号源码文本与 HEAD 相等（50/50，合计 81,923 字节两侧一致）；
- 外部使用面：全仓（src / tests / migration / scripts）AST 扫描 `from ...live_run import` 共 28 名 / 6 个文件，
  拆前 0 处整模块导入。
