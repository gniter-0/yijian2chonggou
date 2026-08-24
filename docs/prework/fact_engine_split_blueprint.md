# fact_engine.py 拆分蓝图（P2 · cm21 落地稿）

- 生成日期：2026-08-24
- 对象：`src/examcard/knowledge/fact_engine.py`（阶段 2 确定性事实/卡片/覆盖运行器）
- 方法：只读 AST 扫描（顶层符号表 + 顶层符号互引图，区分运行位与注解位）→ 分组 → 机械切片执行器整段搬运
- 先例：`docs/prework/semantic_contracts_split_blueprint.md`（23 子模块 + facade）；本稿沿用其口径与节号，
  §8-2 = facade 完整性，§8-3 = 分层守卫

## 1. 现状盘点（依据，全部机器扫描）

| 项 | 实测值 |
|---|---|
| 拆前体量 | 1483 行 / 69,649 字节（纯 LF、无 BOM） |
| 顶层符号 | 52 个（常量 7、dataclass 6、函数 39） |
| 外部使用面 | 8 个 importer 文件、3 个名字：`run_golden_fact_pipeline`（6 处）、`decide_policy`（2 处，均在 src 内）、`_dedup_audit`（1 处，测试） |
| 整模块导入 / `import *` | 0（外部只按名字取） |
| `__all__` | 拆前没有；`knowledge/__init__.py` 是纯 docstring，不做再导出 |
| mypy 豁免 | 无 `examcard.knowledge.fact_engine` 条目（cm13 已真修清零），故拆分不触碰 cm16 的棘轮守卫 |
| 反射导入 | 0（全仓无字符串模块名指向本文件，与 `v25_generic_contracts` 的 `__import__` 情形不同） |

## 2. 拆分原则

1. **只搬不改**：符号连同紧贴其上的注释块整段搬运，判定口径、字面量、报错文本一字不动；
2. **同级平铺 + 原名门面**：子模块落在 `src/examcard/knowledge/` 下作 `fact_*.py` 兄弟，`fact_engine.py`
   原地改成只做再导出的门面，导入路径 `examcard.knowledge.fact_engine` 一字不改；
3. **层序即 import 方向**：子模块只许引用层序在其上方的兄弟，回边为零（§3、§8-3）；
4. **字节权威不受影响**：规范化 JSON 与 sha256 载荷从不写入 `__module__` / `__qualname__` / `__file__`，
   模块路径变化不进哈希；`ROOT` 锚点仍是 `parents[3]`，因为子模块与门面同目录、锚点深度不变。

## 3. 目标布局（层序即 import 允许方向，只许引用上方）

| 层 | 模块 | 行/字节 | 符号数 | 运行期引用的兄弟 |
|---|---|---|---|---|
| 0 | `fact_lexicon.py` | 21 / 1,008 | 7 | — |
| 0 | `fact_types.py` | 66 / 1,678 | 6 | — |
| 0 | `fact_support.py` | 50 / 1,804 | 6 | — |
| 1 | `fact_policy.py` | 69 / 3,064 | 3 | — |
| 1 | `fact_sidecars.py` | 233 / 14,473 | 3 | types, support |
| 1 | `fact_review.py` | 92 / 4,236 | 4 | types, support |
| 1 | `fact_render.py` | 112 / 4,519 | 5 | lexicon, types, support |
| 2 | `fact_classify.py` | 58 / 2,463 | 4 | lexicon, types, render |
| 2 | `fact_diagram.py` | 111 / 4,535 | 5 | lexicon, types, render |
| 3 | `fact_build.py` | 314 / 13,774 | 4 | lexicon, types, support, render, classify, diagram |
| 4 | `fact_quality.py` | 210 / 9,027 | 3 | lexicon, types, review |
| 5 | `fact_runner.py` | 311 / 15,007 | 2 | types, support, policy, sidecars, review, render, diagram, build, quality |
| — | `fact_engine.py`（门面） | 111 / 3,394 | 0（52 名转出） | 全部 12 个 |

合计 13 个文件 / 78,982 字节。较拆前增 9,333 字节，全部来自 12 份模块 docstring 与被复制的 import 头，
无一行实现代码新增或删除（执行器逐符号核对 52/52 已分配、符号区间外无非空行）。

## 4. 环的事实

顶层互引图按 §3 分组后**回边 0 条**：不存在需要 `TYPE_CHECKING` 断环或 `model_rebuild` 兜底的情形
（与 `semantic_contracts.py` 的唯一 4 元环形成对照）。本文件的 6 个 dataclass 只在 `fact_*` 家族内部传参，
不作 pydantic 字段注解，因此不受「字段注解必须在定义模块可解析」那条约束。

## 5. facade 与外部改写策略

- `fact_engine.py` 保留原模块 docstring（阶段 2 运行器的语义声明）+ 12 行 `from .fact_* import ...` + `__all__`；
- `__all__` 列全 52 名，**含私有助手**：随迁测试确实从本模块 import `_dedup_audit`，
  门面的职责是把原有名字面 100% 兜住，不是重新定义什么算公开；
- 8 个 importer（3 个 migration 脚本、2 个 src 模块、3 个测试）一行不改。

## 6. 外部 3 名 → 新子模块归属（验收对照表）

| 外部名 | 新归属 | importer |
|---|---|---|
| `run_golden_fact_pipeline` | `fact_runner` | `migration/golden_five_replay.py`、`migration/prelay_gonglu5_rehearsal_workspace.py`、`migration/run_exam_pipeline.py`、`tests/test_diagram_disposition_v2.py`、`tests/test_fact_v2_engine.py`、`tests/test_phase25f_structural_reviews_v2.py` |
| `decide_policy` | `fact_policy` | `src/examcard/knowledge/continuation/core.py`、`src/examcard/knowledge/projection.py` |
| `_dedup_audit` | `fact_quality` | `tests/test_fact_v2_engine.py` |

## 7. 迁移步骤（一次提交完成，每步可独立验证）

1. 机械切片：执行器按 §3 分组切片、自动推导各子模块 import 面、写 LF 子模块并把 `fact_engine.py` 改成门面
   （先 `--dry-run` 核对文件数/字节/符号覆盖，再落盘）；
2. `ruff check src tests migration` + 全仓 `mypy`（守住豁免清单不加宽）；
3. 补两条封印测试（§8-2 / §8-3）；
4. 全量 `pytest --basetemp .test-tmp/<name> --cov --cov-fail-under=75`；
5. EOL/BOM 平权审计（新文件必须纯 LF、无 BOM，与拆前同风格）。

## 8. 不变量验收（拆分算成的标准）

1. **行为平价**（一票否决）：`tests/test_fact_v2_engine.py` / `test_diagram_disposition_v2.py` /
   `test_phase25f_structural_reviews_v2.py` 全量通过，覆盖率不低于门槛；黄金重放
   `migration/golden_five_replay.py` 的入口签名不变（旧仓语料在位时可实跑）。
2. **facade 完整性**：`tests/test_knowledge_fact_engine_imports.py` 断言 52 名逐一可从
   `examcard.knowledge.fact_engine` 导入且在 `__all__` 内，另单列 §6 的 3 个外部名。
3. **分层守卫**：`tests/test_knowledge_fact_engine_layering.py` AST 扫描 `fact_*` 兄弟边，断言层序全覆盖、
   回边 0 条；同时把「子模块被改成子包」的逃逸口一起封住（目录与文件一起清点）。
4. **旧仓不动性**：本次拆分对 `yijian2` 零写入。

## 9. 风险清单

| # | 风险 | 处置 |
|---|---|---|
| R1 | 后续有人往 `knowledge/` 里新增 `fact_*.py` 而不进层序 | §8-3 的层序全覆盖断言会响亮失败 |
| R2 | 有人把某个子模块改成子包（`fact_x/__init__.py`），使非递归 `glob("*.py")` 静默失去管辖 | §8-3 同时清点同名目录，改包即失败 |
| R3 | 门面被「清理未使用导入」误删 | 52 名全在 `__all__` 内，pyflakes 视为已用；ruff 实测 0 告警 |
| R4 | `ROOT` 锚点深度被后续移动子模块改坏 | 锚点集中在 `fact_support.py` 一处，注释写明「与门面同目录」 |
| R5 | 5 层里 `fact_sidecars` / `fact_runner` 仍偏大（14.5KB / 15.0KB） | 可接受（职责单一）；若再切，`fact_runner` 的样本级与注册表级是天然二次边界 |

## 附：本蓝图分析口径

- 顶层定义图：`ast` 解析 52 个顶层 def/class/常量，收集互相引用（区分运行位与注解位，PEP 563 下注解不求值）；
- 分区校验：52 符号全部有归属（0 未分配、0 重复），符号区间之外无非空行，生成物无 CR、无 BOM；
- 外部使用面：全仓（src / tests / migration）AST 扫描 `from ...fact_engine import` 共 3 名，无整模块导入。