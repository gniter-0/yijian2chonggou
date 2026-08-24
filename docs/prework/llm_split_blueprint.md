# llm.py 拆分蓝图（P2 · cm25 落地稿）

- 生成日期：2026-08-24
- 对象：`src/examcard/knowledge/llm.py`（V2 Step 2A/2B 的审计优先执行链，唯一会真联网的业务模块）
- 方法：只读 AST 扫描（顶层符号表 + 顶层符号互引图，区分运行位与注解位）→ 分组 → 机械切片执行器整段搬运
- 先例：`docs/prework/fact_engine_split_blueprint.md`（cm21，12 兄弟 + 门面）；本稿沿用其口径与节号，
  §8-2 = facade 完整性，§8-3 = 分层守卫
- 执行器：`.test-tmp/cm25_split.py`（机制与 `cm21_split.py` 同一份，只换分组与 docstring）

## 1. 现状盘点（依据，全部机器扫描）

| 项 | 实测值 |
|---|---|
| 拆前体量 | 1,491 行 / 72,962 字节（纯 LF、无 BOM） |
| 顶层符号 | 22 个（常量/类型别名 2、函数 7、枚举 2、pydantic 模型 7、Protocol 1、普通类 3） |
| 外部使用面 | 19 个 importer 文件、9 个名字（含私有助手 `_sanitized_error_message`） |
| 整模块导入 | 1 处：`tests/test_llm_error_redaction.py` 的 `import examcard.knowledge.llm as llm_module`——cm24 的脱敏封印拿它当扫描面锚点 |
| `import *` | 0 |
| `__all__` | 拆前没有；`knowledge/__init__.py` 是纯 docstring，不做再导出 |
| mypy 豁免 | 无 `examcard.knowledge.llm` 条目（cm23 用 openai TypedDict 真修 `call-overload` 后已退掉），故拆分**不需要新键**，cm16 棘轮天然满足 |
| 反射导入 | 0（全仓无字符串模块名指向本文件，与 `v25_generic_contracts` 的 `__import__` 情形不同） |
| 运行期环 | 0 个 SCC（顶层互引图无环），每条分组边界都可切，无需 `TYPE_CHECKING` 断环 |

## 2. 拆分原则

1. **只搬不改**：符号连同紧贴其上的注释块整段搬运，判定口径、字面量、报错文本一字不动
   （`_ChatResponseFormatParam` 头上那 5 行「为什么钉 SDK TypedDict」的由来注释跟着走）；
2. **同级平铺 + 原名门面**：子模块落在 `src/examcard/knowledge/` 下作 `llm_*.py` 兄弟，`llm.py`
   原地改成只做再导出的门面，导入路径 `examcard.knowledge.llm` 一字不改；
3. **层序即 import 方向**：子模块只许引用层序在其上方的兄弟，回边为零（§3、§8-3）；
4. **字节权威不受影响**：规范化 JSON 与 sha256 载荷从不写入 `__module__` / `__qualname__` / `__file__`，
   模块路径变化不进哈希；本模块也没有仓根自锚点（`ROOT` 在 `fact_support.py`，与本次无关）；
5. **pydantic 字段注解必须在定义模块可解析**：全家开 `from __future__ import annotations`，
   字段注解是字符串，模型建完靠的是*定义模块*的命名空间。因此执行器把所有 import 一律放运行期，
   不用 `TYPE_CHECKING`（本来也没有环要断），并由 §8-2 的 `__pydantic_complete__` 断言兜住。

## 3. 目标布局（层序即 import 允许方向，只许引用上方）

| 层 | 模块 | 行/字节 | 符号数 | 运行期引用的兄弟 |
|---|---|---|---|---|
| 0 | `llm_support.py` | 59 / 2,600 | 6 | — |
| 1 | `llm_journal.py` | 64 / 2,499 | 4 | support |
| 1 | `llm_review.py` | 22 / 735 | 2 | — |
| 1 | `llm_forensics.py` | 52 / 2,609 | 2 | support |
| 1 | `llm_transport.py` | 609 / 26,857 | 5 | support |
| 2 | `llm_records.py` | 145 / 9,302 | 2 | support, review, forensics |
| 3 | `llm_adapter.py` | 636 / 31,870 | 1 | support, journal, review, forensics, transport, records |
| — | `llm.py`（门面） | 61 / 2,059 | 0（22 名转出） | 全部 7 个 |

合计 8 个文件 / 78,531 字节。较拆前增 5,569 字节，全部来自 7 份模块 docstring 与被复制的 import 头：
执行器逐符号核对 22/22 已分配、符号区间外无非空行，且**符号正文与 HEAD 逐字节相等**（两侧同为 70,219 字节）。

两个大模块（`llm_transport` 609 行、`llm_adapter` 636 行）各自只装**一个类**，是本文件的天然下限：
再切就得把私有方法提成模块级函数，那是改设计而不是搬运，本次不做（见 §9 R5）。

## 4. 环与注解的事实

顶层互引图按 §3 分组后**回边 0 条**，且拆前本来就没有 SCC——不存在需要断环的情形。
但与 `fact_engine` 那次有一处关键差别：本文件的 7 个 pydantic 模型**互相做字段注解**
（`Step2AExecutionRecord.teaching_review: Step2TeachingReview`、
`rejected_response_forensics: Step2RejectedResponseForensics | None`），
执行记录还引用 `contracts` 的十来个审计类型。PEP 563 下这些注解是字符串，
pydantic 在类定义时用**定义模块**的命名空间求值：一旦某个被引用的名字没跟着搬进新模块的运行期 import 面，
模型会以「未建完」状态静静存在，直到第一次实例化才炸。执行器按 AST 出现的名字自动推导 import 面正是为此，
§8-2 再用 `__pydantic_complete__` 做一道兜底断言。

## 5. facade 与外部改写策略

- `llm.py` 保留原模块 docstring（V2 Step 2 的语义声明：独立落盘根、不改 Evidence、
  `unknown_outcome` 绝不自动重试）+ 7 行 `from .llm_* import ...` + `__all__`；
- `__all__` 列全 22 名，**含私有助手**：`tests/test_llm_error_redaction.py` 确实从本模块 import
  `_sanitized_error_message`，门面的职责是把原有名字面 100% 兜住，不是重新定义什么算公开；
- 19 个 importer（4 个 migration 脚本、2 个 src 模块、13 个测试/夹具）一行不改。

## 6. 外部 9 名 → 新子模块归属（验收对照表）

| 外部名 | 新归属 | importer 数 |
|---|---|---|
| `OpenAICompatibleStep2Transport` | `llm_transport` | 13 |
| `TransportCompletion` | `llm_transport` | 6 |
| `Step2OpenAIAdapter` | `llm_adapter` | 5 |
| `CallableStep2Transport` | `llm_transport` | 4 |
| `Step2AExecutionRecord` | `llm_records` | 4 |
| `Step2JournalOutcome` | `llm_journal` | 2 |
| `Step2BExecutionRecord` | `llm_records` | 1 |
| `Step2JournalEvent` | `llm_journal` | 1 |
| `_sanitized_error_message` | `llm_support` | 1 |

## 7. 迁移步骤（一次提交完成，每步可独立验证）

1. **前置（cm24 已落，`8d09dc9`）**：把 `tests/test_llm_error_redaction.py` 的两条 AST 封印从单文件
   升级成家族级扫描，并加一条「扫描面自锁」断言——否则 llm.py 一变门面，禁切片那条会静默空转；
2. 机械切片：`.test-tmp/cm25_split.py` 按 §3 分组切片、自动推导各子模块 import 面、写纯 LF 兄弟模块
   并把 `llm.py` 改成门面（先 `--dry-run` 核对文件数/字节/符号覆盖，再落盘）；
3. 搬运无损核对：拿 `git show HEAD:...llm.py` 与新家族逐符号比对源码文本（22/22 逐字节相等）；
4. `ruff check src tests migration` + 全仓 `mypy`（豁免清单不加宽，`warn_unused_configs` 顺带证明没有死条目）；
5. 补两条封印测试（§8-2 / §8-3），并各自做咬合验证（人为造回边/漂移，确认响亮失败）；
6. 全量 `pytest --basetemp .test-tmp/<name> --cov --cov-fail-under=75`；
7. EOL/BOM 平权审计（新文件必须纯 LF、无 BOM，与拆前同风格）。

## 8. 不变量验收（拆分算成的标准）

1. **行为平价**（一票否决）：`tests/test_semantic_llm_v2.py`、`test_knowledge_llm_recipe.py`、
   `test_v2_contracts.py`、`test_llm_error_redaction.py` 与 phase2r0x 一族全量通过，覆盖率不低于门槛。
2. **facade 完整性**：`tests/test_knowledge_llm_imports.py` 断言 22 名逐一可从 `examcard.knowledge.llm`
   取到且 `__all__` 精确相等，§6 的 9 个外部名逐个绑定新归属模块里的同一对象，
   门面自身不定义任何实现，并断言全部 pydantic 模型 `__pydantic_complete__`。
3. **分层守卫**：`tests/test_knowledge_llm_layering.py` AST 扫描 `llm_*` 兄弟边，断言层序全覆盖、回边 0 条、
   层序条目仍是单文件模块（子包逃逸口一并封住），门面 7 个兄弟一个不漏。
4. **脱敏封印仍有管辖**：cm24 的家族级扫描现在真的扫到 7 个兄弟；
   `error_message=` 与 `{"error": ...}` 的落纸点全部落在 `llm_journal` / `llm_adapter` 里，取值仍只有 None 或脱敏调用。
5. **旧仓不动性**：本次拆分对 `yijian2` 零写入。

## 9. 风险清单

| # | 风险 | 处置 |
|---|---|---|
| R1 | 后续有人往 `knowledge/` 里新增 `llm_*.py` 而不进层序 | §8-3 的层序全覆盖断言会响亮失败 |
| R2 | 有人把某个子模块改成子包（`llm_x/__init__.py`），使非递归 `glob("*.py")` 静默失去管辖 | §8-3 同时清点同名目录；cm24 的脱敏封印也按同口径清点 |
| R3 | 门面被「清理未使用导入」误删 | 22 名全在 `__all__` 内，pyflakes 视为已用；ruff 实测 0 告警 |
| R4 | 模型字段注解在新模块里解析不了（最隐蔽的一种坏法） | §8-2 的 `__pydantic_complete__` 断言；已做咬合验证（人为置 False 即失败） |
| R5 | `llm_transport` / `llm_adapter` 仍偏大（26.9KB / 31.9KB） | 可接受：各自只含一个类，职责单一。真要再切须动设计——transport 的 `model_payload`（193 行）与 adapter 的 `complete_step2a`（172 行）是天然的二次边界 |
| R6 | 新增落纸点又照抄 `str(exc)[:N]` | cm24 的两条家族级 AST 封印覆盖全部 `llm_*` 成员 |

## 附：本蓝图分析口径

- 顶层定义图：`ast` 解析 22 个顶层 def/class/常量，收集互相引用（区分运行位与注解位，PEP 563 下注解不求值）；
- 分区校验：22 符号全部有归属（0 未分配、0 重复），符号区间之外无非空行，生成物无 CR、无 BOM；
- 搬运无损：逐符号源码文本与 HEAD 相等（22/22，合计 70,219 字节两侧一致）；
- 外部使用面：全仓（src / tests / migration）AST 扫描 `from ...llm import` 共 9 名 / 19 个文件，另 1 处整模块导入。
