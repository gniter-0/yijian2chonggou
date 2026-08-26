# step4_contract.py 拆分蓝图（P1 · cm34 落地稿）

- 生成日期：2026-08-26
- 对象：`src/examcard/compilers/step4_contract.py`（phase2r4：统一发布 fixture → Step4 通用图卡契约、
  离线闸门与回归矩阵；全仓最大文件，且在 mypy 严格档 `gates + compilers` 内）
- 方法：只读 AST 扫描（`.test-tmp/splitscan.py --graph` 顶层符号表 + 运行位/注解位互引图；
  `.test-tmp/usescan.py` 外部使用面）→ 分组 → 机械切片执行器整段搬运
- 先例：`docs/prework/llm_split_blueprint.md`（cm25，7 兄弟 + 门面）、
  `docs/prework/v25_generic_contracts_split_blueprint.md`（cm28，15 兄弟 + 门面）；本稿沿用其口径与节号，
  §8-2 = facade 完整性，§8-3 = 分层守卫
- 执行器：`.test-tmp/cm34_split.py`（机制与 `cm21_split.py` 同一份，只换分组与 docstring）
- **状态**：落地稿。下表「行/字节」为执行器逐符号核对后的**实测**值（搬运无损 80/80 逐字节相等、
  门面 `__all__` 精确等于原符号集），生成物 7 文件共 74,880 字节、全部纯 CRLF 无 BOM。
- **族前缀取 `step4_contract_`（唯一），非 `step4_`**：`compilers/` 下另有与本次无关的
  `step4_echo_bridge.py` / `step4_image.py` 共用 `step4_` 前缀；分层守卫靠前缀清点家族成员，
  用 `step4_` 会把这两个无关模块误纳、逃逸口失守，故 6 个兄弟一律 `step4_contract_*`。

## 1. 现状盘点（依据，全部机器扫描）

| 项 | 实测值 |
|---|---|
| 拆前体量 | 1,259 行 / 66,174 字节（纯 CRLF、无 BOM） |
| 顶层符号 | 81 个（`__all__` 1、常量 32、hash 助手 3、pydantic 模型 24、dataclass 1、派生/校验/闸门函数 20） |
| 外部使用面 | 5 个 importer 文件、14 个名字（含私有助手 `_canonical_hash` `_extract_tokens` `_no_forbidden_text` `_walk_mappings`） |
| 整模块导入 | 0 |
| `import *` | 0 |
| `__all__` | 拆前已有（当前列全 80 名）；拆后归门面，`compilers/__init__.py` 从 `examcard.compilers.step4_contract` 再导出 `ExpectedCounts` / `assess_offline_gate` / `derive_step4_bundle` |
| mypy 豁免 | 无 `step4_contract` 模块级豁免（cm29 已清零），逐点 ignore 清单里也无本文件条目，故拆分**不需要新键**；但本文件在**严格档** `gates + compilers` 内，拆后严格档必须仍 `Success` |
| 反射导入 | 0（全仓无字符串模块名指向本文件，与 `v25_generic_contracts` 的 `__import__` 情形不同——无硬阻挡） |
| 运行期环 | 0 个 SCC（顶层互引图无环），每条分组边界都可切，无需 `TYPE_CHECKING` 断环 |

## 2. 拆分原则

1. **只搬不改**：符号连同紧贴其上的注释块整段搬运，判定口径、字面量、报错文本、版本串一字不动；
2. **同级平铺 + 原名门面**：子模块落在 `src/examcard/compilers/` 下作 `step4_contract_*.py` 兄弟，
   `step4_contract.py` 原地改成只做再导出的门面，导入路径 `examcard.compilers.step4_contract` 一字不改；
3. **层序即 import 方向**：子模块只许引用层序在其上方的兄弟，回边为零（§3、§8-3）；
4. **字节权威不受影响**：规范化 JSON 与 sha256 载荷（`_json_bytes` / `_sha256` / `_canonical_hash`）
   从不写入 `__module__` / `__qualname__` / `__file__`，模块路径变化不进哈希；本文件也没有仓根自锚点；
5. **pydantic 字段注解必须在定义模块可解析**：全家开 `from __future__ import annotations`，字段注解是字符串，
   模型建完靠*定义模块*的命名空间。执行器把所有 import 一律放运行期（不用 `TYPE_CHECKING`，本来也没有环要断），
   由 §8-2 的 `__pydantic_complete__` 断言兜住——**cm34 的头号隐蔽坑**：`Phase2R4ContractBundle` /
   `Phase2R4ModelInput` 的字段注解引用 `GenericCardContract`，一旦 models_phase 漏 import models_generic，
   模型会以「未建完」状态静静存在到首次实例化才炸。

## 3. 目标布局（层序即 import 允许方向，只许引用上方）

| 层 | 模块 | 符号数 | 行/字节（实测） | 运行期引用的兄弟 |
|---|---|---|---|---|
| 0 | `step4_contract_constants.py` | 32 | 78 / 3,410 | — |
| 0 | `step4_contract_hashing.py` | 3 | 26 / 856 | — |
| 1 | `step4_contract_models_generic.py` | 9 | 225 / 10,892 | constants, hashing |
| 2 | `step4_contract_models_phase.py` | 15 | 343 / 15,883 | constants, models_generic |
| 3 | `step4_contract_derive.py` | 15 | 408 / 18,214 | constants, hashing, models_generic, models_phase |
| 4 | `step4_contract_gate.py` | 6 | 278 / 19,374 | constants, models_phase, derive |
| — | `step4_contract.py`（门面） | 0（80 名转出） | 201 / 6,251 | 全部 6 个 |

**分文件的依据**：`Generic*`（9 个通用图卡契约模型）与 `Phase2R4*`（15 个流水线工件模型）之间是
**单向依赖**——`Generic*` 从不引用 `Phase2R4*`，`Phase2R4*` 仅通过 `GenericCardContract` 触及通用层
（`Phase2R4ContractBundle` / `Phase2R4ModelInput` 两处）。这是「通用图卡契约 ⇄ Phase2R4 发布工件」的
天然语义缝，不是任意切割；分两文件让层序自解释，与 v25（15 兄弟）、llm（7 兄弟）的细粒度口径一致。

## 4. 环与注解的事实

顶层互引图按 §3 分组后**回边 0 条**，拆前本来就没有 SCC。关键注解边（PEP 563 下均为字符串，
pydantic 建类时用定义模块命名空间求值）：

- `Phase2R4ContractBundle.contract: GenericCardContract`、`Phase2R4ModelInput` 同 → models_phase 必须运行期 import models_generic；
- `Phase2R4Request` 引 `Phase2R4ModelInput` / `Phase2R4PromptContract`、`Phase2R4Report` 引 `Phase2R4FreezeAudit` /
  `Phase2R4OfflineGate` / `Phase2R4RegressionResult`、`Phase2R4BatchManifest` 引 `Phase2R4BatchEntry`、
  `Phase2R4FakeArtifactManifest` 引 `Phase2R4FakeArtifact`——**均为 models_phase 文件内部边**，同文件天然可解析；
- 9 个 `Generic*` 之间的注解边（`GenericCardContract` 引其余 8 个、`GenericTableContract` 引 `GenericTableCell`、
  `GenericOrderedListContract` 引 `GenericOrderedListItem`）——**均为 models_generic 文件内部边**。

执行器按 AST 出现的名字自动推导各子模块运行期 import 面，§8-2 再用 `__pydantic_complete__` 做兜底断言。

## 5. facade 与外部改写策略

- `step4_contract.py` 保留原模块 docstring + 6 行 `from examcard.compilers.step4_contract_* import ...` + `__all__`；
- `__all__` 列全 80 名，**含私有助手**：`tests/test_compilers_step4_contract.py` 确实从本模块 import
  `_canonical_hash` / `_extract_tokens` / `_no_forbidden_text` / `_walk_mappings`，门面职责是把原有名字面 100% 兜住；
- 5 个 importer（1 个 migration 脚本、`compilers/__init__.py`、`step4_echo_bridge.py`、
  `pipeline/live_stage_step4.py`、1 个测试）一行不改。

## 6. 外部 14 名 → 新子模块归属（验收对照表）

| 外部名 | 新归属 | importer |
|---|---|---|
| `derive_step4_bundle` | `step4_contract_derive` | 4（migration / `__init__` / echo_bridge / live_stage） |
| `assess_offline_gate` | `step4_contract_gate` | 1（`__init__`） |
| `ExpectedCounts` | `step4_contract_gate` | 2（`__init__` / 测试） |
| `_walk_mappings` `_no_forbidden_text` | `step4_contract_gate` | 1（测试） |
| `_canonical_hash` | `step4_contract_hashing` | 1（测试） |
| `_extract_tokens` | `step4_contract_derive` | 1（测试） |
| `GenericVisibleText` `GenericTableContract` `GenericOrderedListContract` `GenericOrderedListItem` `GenericTokenBinding` | `step4_contract_models_generic` | 1（测试） |
| `MARKERS` `DEFAULT_FAKE_FILENAME_PREFIX` | `step4_contract_constants` | 1（测试） |

## 7. 迁移步骤（一次提交完成，每步可独立验证）

1. **前置——先升级 layering 封印**：本文件所在的 `compilers/` 目前有无家族级 layering 封印待确认；
   若无则照 `test_knowledge_llm_layering.py` 建 `tests/test_compilers_step4_layering.py`，否则拆完门面一变
   薄壳、回边探针会静默空转（同 v25 的前置教训）；
2. 机械切片：`.test-tmp/cm34_split.py` 按 §3 分组切片、自动推导各子模块 import 面、写纯 CRLF 兄弟模块
   并把 `step4_contract.py` 改成门面（先 `--dry-run` 核对文件数/字节/符号覆盖，再落盘）；
3. 搬运无损核对：拿 `git show HEAD:...step4_contract.py` 与新家族逐符号比对源码文本（80/80 逐字节相等）；
4. `ruff check src tests migration tools` + 全仓 `mypy`（176 files）+ **严格档 `gates + compilers`（32 files）**
   ——后者是本次特有的一票否决项；
5. 补两条封印（§8-2 facade 完整性 / §8-3 分层守卫），各自做咬合验证（人为造回边/漂移/门面自带实现，确认响亮失败）；
6. 全量 `pytest --basetemp .test-tmp/r34cov --ignore=.pytest_cache --cov --cov-fail-under=75`（两档：语料缺席 / 在位严格）；
7. 还原 7 个黄金件 → `tools/eolaudit.py`（新文件必须纯 CRLF、无 BOM）。

## 8. 不变量验收（拆分算成的标准）

1. **行为平价**（一票否决）：`tests/test_compilers_step4_contract.py` 与 phase2r4 一族全量通过，两档覆盖率不低于门槛；
2. **facade 完整性**：新 `tests/test_compilers_step4_imports.py` 断言 80 名逐一可从
   `examcard.compilers.step4_contract` 取到且 `__all__` 精确相等，§6 的 14 个外部名逐个绑定新归属模块里的同一对象，
   门面自身不定义任何实现，并断言全部 24 个 pydantic 模型 `__pydantic_complete__`；
3. **分层守卫**：`tests/test_compilers_step4_layering.py` AST 扫 `step4_contract_*` 兄弟边，层序全覆盖、回边 0 条、
   层序条目仍是单文件模块（子包逃逸口封住），门面 6 个兄弟一个不漏；
4. **严格档不加宽**：拆后 `mypy gates + compilers --follow-imports=silent` 仍 32 files `Success`，逐点 ignore 清单不新增；
5. **字节权威**：canonical JSON / sha256 载荷不含模块路径，phase2r4 金标与缓存键零漂移；
6. **旧仓不动性**：本次拆分对 `yijian2` 零写入。

## 9. 风险清单

| # | 风险 | 处置 |
|---|---|---|
| R1 | models_phase 漏 import models_generic，`Phase2R4ContractBundle` / `Phase2R4ModelInput` 未建完 | §8-2 `__pydantic_complete__` 断言 + 咬合验证（人为删 import 即失败）——**本次头号坑** |
| R2 | 后续有人往 `compilers/` 新增 `step4_contract_*.py` 不进层序 | §8-3 层序全覆盖断言响亮失败 |
| R3 | 有人把某子模块改成子包，使非递归 `glob` 静默失去管辖 | §8-3 同时清点同名目录 |
| R4 | 门面被「清理未使用导入」误删 | 80 名全在 `__all__` 内，ruff 实测 0 告警 |
| R5 | 严格档因跨文件 import 面变化冒出新报错 | §7 步骤 4 把严格档设为一票否决，逐点 ignore 清单不许新增 |
| R6 | `step4_contract_derive.py` 偏大（15 个派生函数，约 330 def-行） | 可接受：单一职责（fixture → 契约的确定性派生链）；真要再切须动设计，本次不做 |

## 附：本蓝图分析口径

- 顶层定义图：`ast` 解析 81 个顶层 def/class/常量，收集互相引用（区分运行位与注解位，PEP 563 下注解不求值）；
- 分区校验：80 符号全部有归属（0 未分配、0 重复，`__all__` 归门面），符号区间之外无非空行，生成物无 CR、无 BOM；
- 搬运无损：逐符号源码文本与 HEAD 相等（80/80）；
- 外部使用面：全仓 AST 扫描 `from ...step4_contract import` 共 14 名 / 5 个文件，0 处整模块导入。

