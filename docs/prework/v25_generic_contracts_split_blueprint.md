# v25_generic_contracts.py 拆分蓝图（P3 · cm28 落地稿）

- 生成日期：2026-08-25
- 对象：`src/examcard/knowledge/contracts/v25_generic_contracts.py`（V25/V25B 现役声明层：step2a 取材契约 +
  step2b 归组契约，含版本号底座、义务模型族、载荷与提示词、请求/结果/门禁模型、构建/评定/离线替身）
- 方法：只读 AST 扫描（顶层符号表 + 顶层符号互引图，区分运行位与注解位）→ 分组 → 机械切片执行器整段搬运
- 先例：`docs/prework/fact_engine_split_blueprint.md`（cm21，12 兄弟）、`docs/prework/llm_split_blueprint.md`
  （cm25，7 兄弟）、`docs/prework/live_run_split_blueprint.md`（cm26，14 兄弟）；本稿沿用同一套节号，
  §8-2 = facade 完整性，§8-3 = 分层守卫，§8-6 = 咬合验证
- 执行器：`.test-tmp/cm28_split.py`（机制承自 `cm26_split.py`，三处必须不同，见 §2-6、§2-7、§2-8）

## 1. 现状盘点（依据，全部机器扫描）

| 项 | 实测值 |
|---|---|
| 拆前体量 | 1,948 行 / 104,439 字节（**纯 LF**、0 个 CRLF、无 BOM） |
| 顶层符号 | 79 个（常量 31：28 个版本号 + 摘要正则 + 离线中继基址 + 来源族呈现映射；函数 20，含 6 个私有助手；类 28：pydantic `StrictModel` 23 + `StrEnum` 5；异常/dataclass/普通类 0） |
| 作者分节 | **0 个横幅**：顶层一行注释都没有（执行器把这个前提写成断言，见 §2-7） |
| 外部使用面 | 8 个 importer（1 个 migration 脚本、5 个同族契约模块、2 个测试）、31 个名字共 66 处引用，全部是公开名，9 个私有名零外泄 |
| 整模块导入 | 拆前 0 处；新增的 §8-2 封印是家族里唯一 2 处（`import examcard.knowledge.contracts as contracts` 与 `... .v25_generic_contracts as v25`） |
| `import *` | 0 |
| `__all__` | 拆前没有；`contracts/__init__.py` 不再导出任何 v25 名字（已 grep 核实） |
| mypy 豁免 | 无 `examcard.knowledge.contracts.v25_generic_contracts` 活条目（`pyproject.toml` 第 149 行那句「v25_generic_contracts 58」只是历史普查记账），故拆分**不需要新键** |
| 反射导入 | 1 处：`phase2r14_v25_live.py` 第 334 行 `__import__(..., fromlist=["build_v25b_source_fake_response"])`；该文件另有同目标的顶层静态 import，这处反射是历史冗余。cm27 已把它登记进 `DYNAMIC_SIBLING_IMPORTS` |
| 运行期环 | 0 个 SCC；按 §3 分组后 110 条跨模块运行位边**全部顺层，回边 0 条** |
| 注解位边 | 跨模块 29 条，其中 15 条「仅注解位」；只有 **2 条指向层序下方**，必须走 `TYPE_CHECKING`（§4） |
| 既有封印的管辖 | 脱敏封印、遗留根解析封印都是 `rglob` 口径，拆完自动覆盖 15 个兄弟；contracts 分层守卫已由 cm27（`ae0d1f7`）升级成家族级扫描，本次只需把 15 个兄弟登记进 `LAYER_ORDER` |

## 2. 拆分原则

1. **只搬不改**：符号整段搬运，判定口径、字面量、报错文本一字不动；
2. **同级平铺 + 原名门面**：子模块落在 `src/examcard/knowledge/contracts/` 下作 `v25_*.py` 兄弟，
   `v25_generic_contracts.py` 原地改成只做再导出的门面，导入路径
   `examcard.knowledge.contracts.v25_generic_contracts` 一字不改；
3. **层序即 import 方向**：子模块只许运行位引用层序在其上方的兄弟，回边为零（§3、§8-3）；
4. **字节权威不受影响**：规范化 JSON 与 sha256 载荷从不写入 `__module__` / `__qualname__` / `__file__`，
   模块路径变化不进哈希；
5. **24 位截断与摘要正则本身就是字节权威**：`_v25_id` 里的 `[:24]` 参与既有黄金件的业务 id 比对，
   `_SHA256_PATTERN` 的 `{64}` 进全部模型的 `Field(pattern=...)`。同级搬迁本不该碰这两个字面量，
   但「顺手改数字」是拆分里最典型的静默故障，故 §8-2 直接兑现两条断言；
6. **EOL：全家纯 LF**（本次与 cm26 的第一处不同）。cm26 的 `live_run.py` 是纯 CRLF，本文件是纯 LF。
   执行器读入即验「无 BOM + 无 CRLF」，落盘也一律 LF，并复验生成物没有 CRLF/BOM 漏出去；
   新增的 imports 封印测试同样是纯 LF（`tests/test_knowledge_llm_imports.py` 已有先例）。
   反过来，`tests/test_knowledge_contracts_layering.py` 是**纯 CRLF**，改它必须走锚点脚本（§7-5）；
7. **没有横幅可搬**（第二处不同）。cm26 那套「跨空行并入分节横幅」的启发式在本文件永不触发：
   顶层一个注释都没有。故退回 cm25 的「只并紧贴符号的注释」，并另加一条断言把这个前提钉住——
   将来谁往原文件加了顶层横幅再来重跑，执行器会先说话而不是默默丢注释；
8. **pydantic 字段注解按运行位计**（第三处不同，也是 cm28 唯一的新机制）。PEP 563 下注解不求值，
   函数签名的前向引用可以进 `if TYPE_CHECKING:`；但 pydantic v2 建类时要解析**字段注解**，
   解析不到并不当场炸，而是让模型停在 `__pydantic_complete__ = False`，直到第一次校验才抛
   `PydanticUserError`。所以模型→模型的边必须是**真 import**，`splitscan.collect_refs(..., pydantic_body=True)`
   把模型体里的 `AnnAssign` 注解计入运行位，执行器据此只把该走的 2 条放进 `TYPE_CHECKING`（§4）。

## 3. 目标布局（层序即 import 允许方向，只许引用上方）

| 层 | 模块 | 行/字节 | 符号 | 模型 | 运行期引用的兄弟 |
|---|---|---|---|---|---|
| 0 | `v25_versions.py` | 38 / 3,142 | 28 | 0 | — |
| 0 | `v25_support.py` | 23 / 826 | 4 | 0 | — |
| 0 | `v25_enums.py` | 59 / 2,195 | 6 | 0 | — |
| 1 | `v25_step2a_obligations.py` | 285 / 17,177 | 8 | 7 | support, enums |
| 1 | `v25_step2b_diagram.py` | 75 / 3,707 | 3 | 3 | support |
| 2 | `v25_step2a_prompt.py` | 244 / 12,905 | 5 | 0 | versions, support, enums, step2a_obligations（+`TYPE_CHECKING`: step2a_request） |
| 2 | `v25_step2b_prompt.py` | 88 / 4,019 | 3 | 0 | versions（+`TYPE_CHECKING`: step2b_models） |
| 3 | `v25_step2a_request.py` | 332 / 18,329 | 5 | 5 | versions, support, enums, step2a_obligations, step2a_prompt |
| 3 | `v25_step2b_models.py` | 238 / 12,102 | 8 | 8 | versions, support, enums, step2b_diagram, step2b_prompt |
| 4 | `v25_step2a_build.py` | 248 / 12,915 | 1 | 0 | 上述 step2a 侧 6 个 |
| 4 | `v25_step2a_assess.py` | 198 / 9,949 | 1 | 0 | step2a_prompt, step2a_request |
| 4 | `v25_step2a_offline.py` | 129 / 5,654 | 2 | 0 | versions, support, enums, step2a_prompt, step2a_request |
| 4 | `v25_step2b_build.py` | 188 / 8,996 | 2 | 0 | versions, support, enums, step2b_prompt, step2b_models |
| 4 | `v25_step2b_assess.py` | 63 / 2,846 | 1 | 0 | step2b_models |
| 4 | `v25_step2b_offline.py` | 47 / 2,213 | 2 | 0 | versions, support, step2a_request, step2b_models |
| — | `v25_generic_contracts.py`（门面） | 182 / 6,854 | 0（79 名转出） | — | 全部 15 个 |

合计 16 个文件 / 123,829 字节。较拆前增 19,390 字节，全部来自 15 份模块 docstring、门面 docstring 与被复制的
import 头：执行器逐符号核对 79/79 已分配、符号区间外无非空行，且**符号正文与 HEAD 逐字节相等**
（79 个符号两侧同为 103,554 字节，sha256 双侧 `ed570e2960c7d3b6…`；核对脚本 `.test-tmp/cm28_verify.py`）。

分组不是重新设计，而是认领这份声明层本来就有的二维结构：**两条流水线**（step2a 取材 / step2b 归组）
× **五个角色**（无依赖底座 / 义务与语义图模型 / 载荷与提示词 / 请求与门禁模型 / 构建·评定·离线）。
层 0 的三个底座是两条流水线共用的：版本号 28 个、摘要形状与 id 助手、五个 `StrEnum` 与来源族呈现映射。
表里的「层」是概念层；执行器实际用的是一条 15 元拓扑序（step2a 侧 9 个在前、step2b 侧 6 个在后），
两者不冲突——概念同层的模块之间没有任何边。

两处值得单独记一笔：`v25_step2b_offline` 运行位用到 step2a 侧的 `V25JournalRecord`（离线日志两侧共用
一个记录类型），这是**跨侧复用**而不是回边，它在拓扑序里排最后；`v25_support` 是全家唯一没有外部
使用者的兄弟（§6），四个符号全是家族内部件——这条事实也被写成断言，见 §8-2。

## 4. 环与注解的事实

拆前顶层互引图**没有 SCC**；按 §3 分组后，110 条跨模块运行位边全部指向层序上方，回边 0 条。
真正需要处理的是注解位：29 条跨模块注解位边里，15 条「仅注解位」（同一名字没有对应的运行位引用），
其中 **13 条本来就指向层序上方**，执行器直接给真 import；只有 2 条指向下方，走 `TYPE_CHECKING`：

| 所在模块 | 前向引用 | 归属 | 为什么反向 |
|---|---|---|---|
| `v25_step2a_prompt` | `V25Step2ARequest` | `v25_step2a_request` | 载荷函数在**签名**上标注请求模型 |
| `v25_step2b_prompt` | `V25BStep2BRequest` | `v25_step2b_models` | 同上 |

方向是这样定下来的：`V25Step2ARequest` 的校验器在**运行位**调用 `v25_step2a_request_payload` /
`_section_anchor_map` / `_table_by_id`，`V25BStep2BRequest` 的校验器在运行位调用
`v25b_step2b_request_payload`，所以载荷与提示词模块必须排在请求模型模块之前；反过来那 2 条只是
函数签名注解，PEP 563 下不求值，可以前向引用。

与 cm25/cm26 的差别在 pydantic 面积：本文件有 **23 个 StrictModel**（cm26 的 `live_run` 只有 2 个）。
面积一大，「把模型→模型的边也塞进 `TYPE_CHECKING`」就从理论风险变成会真发生的事，而它的坏法是
**静默的**——import 过、ruff 过、mypy 过，模型停在未建完状态，直到第一次校验才炸。所以这次不靠人眼
判断哪条边该走哪个通道，而是把「模型体里的字段注解按运行位计」写进执行器判据，再由 §8-2 的
`__pydantic_complete__` 断言兜底。§8-6 的探针 ④ 专门兑现这一条。

## 5. facade 与外部改写策略

- `v25_generic_contracts.py` 保留原模块 docstring（由执行器从源码切下，不手抄）+ 6 行拆分说明 +
  15 行 `from .v25_* import ...` + `__all__`；
- `__all__` 列全 79 名，**含 9 个私有名**（`_v25_id`、`_section_anchor_map`、`_table_by_id`、`_v25_contexts`、
  `_presentation_for_fact_type`、`_label_from_source`、`_SHA256_PATTERN`、`_OFFLINE_RELAY_BASE_URL`、
  `_SOURCE_FAMILY_PRESENTATION`）：门面的职责是把拆前的名字面 100% 兜住，不是趁搬家重新定义什么算公开；
- 8 个 importer（1 个 migration 脚本、5 个同族契约模块、2 个测试）**一行不改**；
- `phase2r14_v25_live` 那处带 `fromlist` 的 `__import__` 取到的是**门面模块对象**，属性由再导出兜住；
  §8-2 按它的实际调用形态再兑现一次，§8-3 则管这条边的层序登记。

## 6. 外部 31 名 → 新子模块归属（验收对照表）

| 外部名 | 新归属 | importer 数 |
|---|---|---|
| `assess_v25_step2a_response` | `v25_step2a_assess` | 6 |
| `assess_v25b_step2b_response` | `v25_step2b_assess` | 5 |
| `build_v25_step2a_request` | `v25_step2a_build` | 5 |
| `V25Step2AGate` | `v25_step2a_request` | 4 |
| `V25Step2AResultPayload` | `v25_step2a_request` | 4 |
| `build_v25_source_fake_response` | `v25_step2a_offline` | 4 |
| `build_v25b_step2b_request` | `v25_step2b_build` | 4 |
| `V25Step2BFactSummary` | `v25_step2b_models` | 3 |
| `build_v25b_source_fake_response` | `v25_step2b_offline` | 3 |
| `V25BGroupingGate` | `v25_step2b_models` | 2 |
| `V25BSourceFamilyBinding` | `v25_step2b_models` | 2 |
| `V25BStep2BResultPayload` | `v25_step2b_models` | 2 |
| `V25ReviewedContinuationBinding` | `v25_step2a_obligations` | 2 |
| `V25Step2ARequest` | `v25_step2a_request` | 2 |
| `v25_step2a_request_payload` | `v25_step2a_prompt` | 2 |
| `V25BBlockedPreflight` | `v25_step2b_models` | 1 |
| `V25BSourceFamilyKind` | `v25_enums` | 1 |
| `V25B_STEP2B_JOURNAL_VERSION` | `v25_versions` | 1 |
| `V25FactReviewState` | `v25_enums` | 1 |
| `V25JournalRecord` | `v25_step2a_request` | 1 |
| `V25SemanticDiagramBridge` | `v25_step2b_diagram` | 1 |
| `V25SemanticDiagramEdge` | `v25_step2b_diagram` | 1 |
| `V25SemanticDiagramNode` | `v25_step2b_diagram` | 1 |
| `V25Step2AContractProfile` | `v25_enums` | 1 |
| `V25Step2AModelFactProposal` | `v25_step2a_request` | 1 |
| `V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION` | `v25_versions` | 1 |
| `V25_STEP2A_JOURNAL_VERSION` | `v25_versions` | 1 |
| `make_v25_offline_journal` | `v25_step2a_offline` | 1 |
| `v25_step2a_prompt_template` | `v25_step2a_prompt` | 1 |
| `v25b_step2b_prompt_template` | `v25_step2b_prompt` | 1 |
| `v25b_step2b_request_payload` | `v25_step2b_prompt` | 1 |

外部面覆盖 14 个兄弟；唯一没有外部名的是 `v25_support`——摘要形状、离线中继基址与两个字符串助手
全是家族内部件。§8-2 把这条事实写成断言：将来谁把内部助手外泄成外部依赖，对照表先说话。

值得注意的是外部面的形状：31 个名字里只有 3 个版本常量、2 个枚举被直接取用，其余全是模型类与
四个入口函数（构建 / 评定 / 离线替身 / 提示词）。这说明拆分边界与真实使用面是对齐的——
外部消费者拿的就是「请求模型 + 结果模型 + 四个动作」，而不是散装内部件。

## 7. 迁移步骤（一次提交完成，每步可独立验证）

1. 预研：AST 扫顶层符号表（79）、互引图（运行位 / 注解位分开）、外部使用面（8 importer / 31 名）、
   EOL 指纹（纯 LF）；确认 0 SCC、1 处反射导入已被 cm27 登记、mypy 豁免清单无活条目；
2. 机械切片：`.test-tmp/cm28_split.py` 按 §3 分组切片、自动推导各子模块 import 面与 `TYPE_CHECKING` 通道、
   写**纯 LF** 兄弟模块并把 `v25_generic_contracts.py` 改成门面（先 `--dry-run` 核对文件数 / 字节 /
   符号覆盖 / 通道条数，再落盘）。执行器自带三道拒绝：顶层出现注释行、docstring 末行不是独立三引号、
   目标已是门面（防重跑把门面当原文切）；
3. 搬运无损核对：`.test-tmp/cm28_verify.py` 拿 `git show HEAD:...v25_generic_contracts.py` 逐符号比对
   （79/79 逐字节相等，两侧同为 103,554 字节）；
4. `ruff check src tests migration` + 全仓 `mypy` + 严格档（gates/compilers）+ 无豁免档对账
   （无豁免档条数必须与拆前逐条相同，`v25_*` 家族贡献 0 条）；
5. 补两条封印：`tests/test_knowledge_contracts_v25_imports.py`（§8-2，新建，纯 LF）与
   `tests/test_knowledge_contracts_layering.py` 的 `LAYER_ORDER` 登记（§8-3）。**后者是纯 CRLF**，
   故不用编辑工具，走 `.test-tmp/cm28_seal_layering.py` 的锚点唯一命中字节替换（每条锚点先断言
   `count == 1`，改完复验无孤立 LF、无 BOM、行数按预期增长 23 行：说明段 5 + 注释 2 + 条目 15 + 空行 1）；
6. 咬合验证：`.test-tmp/cm28_bite.py` 造 7 种漂移、9 条断言，逐条确认封印响亮失败，随后按字节还原并用
   sha256 自证 19 个文件逐字节复原（§8-6）；
7. 全量 `pytest --basetemp .test-tmp/r28 --cov --cov-fail-under=75`，跑完还原 7 个被脏的黄金件；
   EOL/BOM 平权审计（`.test-tmp/eolaudit.py`，要求 `VIOLATIONS=0`）；
8. 同步活文档：`docs/engineering_skeleton.md` 的 `contracts/` 树补 `v25_*.py` 家族、
   `docs/prework/handoff_20260824.md`（巨石榜摘掉本模块并补家族对照、验证基线换 cm28 数字）。
   **本次与 cm26 相反，`docs/extraction_manifest.md` 与 `migration/gen_extraction_manifest.py` 要动**：
   清单第 49 行登记的正是这个模块（旧仓 101KB → `contracts/`），而 cm21 / cm25 两次拆分都在生成器的
   去向列留了「门面 + N 兄弟」注记（`CORE_TARGETS` 第 62 / 64 行）。v25 走的是 `core-current` 分支
   （生成器第 126 行硬编码去向串），故需补一条按模块名生效的注记后重生成；注意生成器 `write_text`
   出的是 LF，而仓里的清单是 450 行**纯 CRLF**，落盘必须过 CRLF 落盘器。

## 8. 不变量验收（拆分算成的标准）

1. **行为平价**（一票否决）：5 个同族契约模块与 2 个测试的既有用例、加两条新封印全量通过，
   全量套件与覆盖率门槛（75）不退。
2. **facade 完整性**（`tests/test_knowledge_contracts_v25_imports.py`，11 条）：79 名逐一可从
   `examcard.knowledge.contracts.v25_generic_contracts` 取到且 `__all__` 精确相等；§6 的 31 个外部名逐个
   `is` 绑定新归属模块里的同一对象（用哨兵取值而不是 `getattr`，让 31 条一次走完给齐清单）；
   门面自身不定义实现（只看函数与类，常量没有定义位置意义上的 `__module__`）；外部面覆盖 14 个兄弟且
   `v25_support` 不在其中；`_v25_id` 的截断仍是 24 位；`_SHA256_PATTERN` 仍要求 64 位小写十六进制；
   **23 个模型的 `__pydantic_complete__` 全为真且数量恰好 23**；带 `fromlist` 的 `__import__` 仍取到门面
   且属性与 `v25_step2b_offline` 里的同一对象相等。
3. **分层守卫**（`tests/test_knowledge_contracts_layering.py`，cm27 已是家族级扫描）：`LAYER_ORDER` 全覆盖
   contracts 下每个模块文件、每个层序条目仍是单文件模块而非同名子包、运行位边 0 回边、
   `DYNAMIC_SIBLING_IMPORTS` 与实际字符串导入一致且被取属性在目标模块上可解析。
4. **两条封印互补而非重复**：层序守卫按既有口径豁免 `TYPE_CHECKING` 里的节点，所以「把模型→模型的边
   塞进 `TYPE_CHECKING`」它**会放行**；抓住这种漂移的是 §8-2 的 `__pydantic_complete__` 断言。
   反过来，新增一个不登记的兄弟或造一条运行位回边，只有层序守卫会响。§8-6 的探针 ④ 同时验证了这两面。
5. **既有封印仍有管辖**：脱敏封印与遗留根解析封印按 `rglob` 清点，拆完自动覆盖 15 个兄弟。
6. **咬合验证**（`.test-tmp/cm28_bite.py`，7 种漂移 / 9 条断言，全部命中后按字节还原并 sha256 自证
   19 个文件逐字节复原）：① 新增兄弟却不登记进 `LAYER_ORDER`；② 造一条真正的运行位回边；
   ③ 把既有 `TYPE_CHECKING` 前向注解降级成运行位 import；④ 把「模型→模型」的运行位边塞进
   `TYPE_CHECKING`——**层序放行（退出码 0）而模型自证响亮失败**，这是本轮的重点探针；
   ⑤ 门面漏一个再导出（`__all__` 解析失败 + 动态反射边取不到属性，两条同时响）；
   ⑥ 把 24 位截断改成 23；⑦ 门面自己定义实现。
7. **旧仓不动性**：本次拆分对 `yijian2` 零写入。

## 9. 风险清单

| # | 风险 | 处置 |
|---|---|---|
| R1 | 后续有人往 `contracts/` 里新增模块而不进层序 | §8-3 的全覆盖断言会响亮失败（探针 ① 已兑现） |
| R2 | 有人把某个子模块改成子包（`v25_x/__init__.py`），使非递归 `glob("*.py")` 静默失去管辖 | §8-3 同时清点同名目录；这条教训本来就来自 contracts 家族 |
| R3 | 门面被「清理未使用导入」误删 | 79 名全在 `__all__` 内，pyflakes 视为已用；ruff 实测 0 告警 |
| R4 | **模型字段注解在新模块里解析不了**（本次面积最大、最隐蔽的坏法，23 个模型） | §8-2 的 `__pydantic_complete__` 断言 + 执行器把字段注解按运行位计（探针 ③④ 已兑现） |
| R5 | `_v25_id` 的 `[:24]` 或 `_SHA256_PATTERN` 的 `{64}` 被「顺手」调整 | §8-2 两条字面量断言（探针 ⑥ 已兑现）；前者还会连带撞黄金件 |
| R6 | 反射取名的那条隐形边在门面上取不到属性 | §8-3 断言属性可解析 + §8-2 按实际 `fromlist` 形态复现（探针 ⑤ 已兑现两侧同时响） |
| R7 | 新文件混进 CRLF，触发日后一次全文件 EOL 抖动 | 执行器双向验 EOL；改纯 CRLF 的层序测试走锚点脚本；提交前 `eolaudit.py` 要求 `VIOLATIONS=0` |
| R8 | `v25_step2a_request` 仍偏大（332 行 / 18.3KB，其中 `V25Step2ARequest` 一个类 13.6KB） | 可接受：那个类按六个侧写各自校验版本号三元组并核对 identity 的 `payload_sha256`，再切就不是搬运而是改设计。真要动须单独一步，不与搬运混做 |
| R9 | 门面与兄弟之间将来出现「门面→兄弟→门面」的环 | **层序守卫抓不到**：门面在 `LAYER_ORDER` 里排在 15 个兄弟**之后**（它后面还有 5 个运行时闭包模块 + `v25_teaching_gate_layers`），兄弟引用门面算前向边而非回边。真正的兜底是 Python 自己：门面顶层就在 import 兄弟，兄弟再回头 `from .v25_generic_contracts import X` 会撞上部分初始化模块、当场 `ImportError`。cm26 给 live_run 家族单加过「兄弟不许回头 import 门面」断言，contracts 家族要不要照做是后续一步，不与本次搬运混做 |

## 附：本蓝图分析口径

- 顶层定义图：`ast` 解析 79 个顶层 def / class / 常量，收集互相引用并区分运行位与注解位；
  **模型体内的字段注解按运行位计**（pydantic v2 建类要解析，PEP 563 不救它）；
- 分区校验：79 符号全部有归属（0 未分配、0 重复），符号区间之外无非空行，生成物无 CRLF、无 BOM；
- 搬运无损：逐符号源码文本与 HEAD 相等（79/79，合计 103,554 字节两侧一致，sha256 `ed570e2960c7d3b6…`）；
- 外部使用面：全仓（src / tests / migration / scripts）AST 扫描 `from ...v25_generic_contracts import`
  共 31 名 / 8 个文件 / 66 处，拆前 0 处整模块导入、0 处 `import *`；
- 环与通道：跨模块运行位边 110 条（回边 0）、注解位边 29 条（仅注解位 15 条，其中 2 条走 `TYPE_CHECKING`）。
