# cm35 落地稿：拆 `knowledge/contracts/admission_fixtures.py`

## 一、背景与目标

`src/examcard/knowledge/contracts/admission_fixtures.py` 是「A 案」跨桶纯数据契约的落脚点，
当前 **1249 行 / 62,491 字节 / 纯 LF / 无 BOM**，顶层 **0 函数、52 个 pydantic 契约类、
61 个模块级常量**（含 6 个 `_` 私有），当前**无 `__all__`**。外部依赖仅两处：
`...materials.schema.StrictModel` 与同目录法定哈希搬运件
`.admission_legacy_hash`（`canonical_hash` / `canonical_hash_r10x` / `text_hash`）。

目标：按既定「拆巨石」套路，机械切成 **7 个 `admission_fixtures_*` 扁平兄弟 + 原名门面**，
逐符号字节相等，外部导入面全部经门面兜住，两条**既有家族级封印**同步维护，验证链全绿、
`eolaudit VIOLATIONS=0`，落一次临时身份本地提交（不推送）。

## 二、EOL 与 mypy tier 归属（与 cm34 的关键差异）

1. **纯 LF 家族**：`knowledge/contracts/` 及其新测试均为纯 LF。故本次兄弟模块、门面、
   新测试**可直接用 Write/Edit**（落盘即 LF，正合家族风格），无需 CRLF 保守脚本；
   但**本落地稿身处 `docs/`，`docs/*.md` 是纯 CRLF**，须走 emit 脚本写 CRLF。
2. **tier 归属**：`admission_fixtures` 属 `knowledge/contracts`，只在 **全项目 mypy 档**，
   **不在严格档**（严格档仅 `gates` + `compilers`）。

## 三、分片清单与层序（7 兄弟 + 门面）

依赖方向严格自上而下，兄弟只引用排在自己上方的层；注解位跨引用（PEP 563 字符串）
不构成运行期边。落点按运行位依赖（基类 / 装饰器 / 默认值 / 校验器体内调用）判定。

- **L0 `admission_fixtures_constants`（62 项）**：全部 60 个非 `TypedContract` 常量
  + 2 个 `StrEnum`（`ExtractionMode`、`Step3ReviewedSourceTier`）。零兄弟依赖。
- **L1 `admission_fixtures_typed`（17 项）**：r84 typed-evidence 家族——`SourcePageBinding`、
  `TypedElementProvenance`、`TypedTextBase`、`DiagramNode/Edge/Contract`、`TableCell/Contract`、
  `OrderedListItem/Contract`、`ConstraintToken/Contract`、`FormulaToken/Contract`、
  `TypedContract`（5 契约的 Union 别名常量，必须排在 5 类之后）、`ReviewedCandidate`、
  `ReviewedTypedEvidenceFixture`。依赖 constants + admission_legacy_hash。
  **注意：全文件仅有的 5 处 `# type: ignore[assignment]` 全落在本模块**
  （`DiagramNode`/`TableCell`/`OrderedListItem`/`ConstraintToken`/`FormulaToken` 的
  `schema_version` Literal 默认值覆盖）。
- **L2 `admission_fixtures_step3card`（10 项）**：`SourceProvenance`、`DisplayElement`、
  五个 `*Ref`（`TableCellRef`/`ListItemRef`/`TokenRef`/`DiagramNodeRef`/`DiagramEdgeRef`）、
  `LayoutContract`、`GeometryConstraints`、`Step3CardContract`。依赖 constants。
- **L3 `admission_fixtures_release`（8 项）**：`FakeArtifact`、`DraftBundle`、
  `DeterministicDraftCard`、`DeterministicReleaseBundle`、`Step3HumanDecision`、
  `ReviewedStep3Card`、`ReviewedStep3Fixture`、`ReviewedStep3FixtureR103`。
  依赖 step3card（`Step3CardContract`）+ constants，故排在 step3card 之后。
- **L4 `admission_fixtures_merged`（7 项）**：`Step3SourceOwnedSemanticEdgeAudit`、
  `RemediationEvidenceBinding`、`RemediationTableCell`、`RemediationTableGrid`、
  `Step3MergedSourceOwnedDisplayText`、`Step3MergedReviewedReleaseCard`、
  `Step3UnifiedReviewedReleaseFixture`。依赖 constants（含两个 StrEnum、`EXPECTED_*`）。
- **L5 `admission_fixtures_offline`（6 项）**：`FrozenArtifact`、`PriorFreeze`、`GateCheck`、
  `OfflineGate`、`JUnitReceipt`、`AcceptanceReport`（r104 离线闸家族）。依赖 constants。
- **L6 `admission_fixtures_pilot`（3 项）**：`Step4ReviewedImageReferenceR319`、
  `Step4PilotCardDispositionR319`、`Step4PilotHandoffRefresh`（r319 试点家族）。依赖 constants。
- **门面 `admission_fixtures`**：`from .admission_fixtures_X import *`（或具名）重导出全部
  113 个符号，`__all__` 全量列出（含 6 个 `_` 私有）。门面 import 兄弟属运行期边，
  故门面 rank 必须大于所有兄弟。

L2/L4/L5/L6 只依赖 constants，彼此并列；线性层序取
constants → typed → step3card → release → merged → offline → pilot → 门面。

## 四、逐模块 import（由切片实际用到的名反推，避免 F401）

统一头：`from __future__ import annotations` + `from ...materials.schema import StrictModel`。
按需追加：`re`（仅 constants 用 1 处）、`from enum import StrEnum`（constants）、
`from pathlib import Path`（constants，用于 `*_POSIX` 派生）、
`from typing import Annotated, Final, Literal`、`from pydantic import Field, model_validator`、
`from .admission_legacy_hash import canonical_hash, canonical_hash_r10x, text_hash`（凡校验器
体内调用哈希的模型模块）。执行器按每个切片的标识符使用集自动裁剪 import，落盘前用 ruff 兜底。

## 五、两条既有家族级封印的同步（本次不新写封印，只维护）

1. **`tests/test_knowledge_contracts_layering.py` 的 `LAYER_ORDER`**：`family_members()` 用
   **非递归 glob** 自动清点 `*.py`，新兄弟不入 `LAYER_ORDER` 即 `test_layer_order_covers_every_module_file`
   当场失败。把 7 个新兄弟按拓扑序**插在 `admission_legacy_hash` 与 `admission_fixtures` 之间**
   （先例：cm28 在门面名前插了 15 条 v25_*）。门面名 `admission_fixtures` 保持在兄弟之后。
2. **`tests/test_mypy_waiver_ratchet.py` 的 `_IGNORE_CEILING`**：现有一条路径记名的
   `"knowledge/contracts/admission_fixtures.py": {"assignment": 5}`。5 处 ignore 全部随代码搬进
   `admission_fixtures_typed`，故把该键**整条改名**为
   `"knowledge/contracts/admission_fixtures_typed.py": {"assignment": 5}`（计数不变、无需拆分）。
   门面里不得残留任何 `# type: ignore`。

## 六、一次性 builder 的作废说明

`migration/build_admission_fixtures.py` 会把 `admission_fixtures.py` **整文件重生**
（`(CONTRACTS_DIR/"admission_fixtures.py").write_bytes(...)`）。拆分后该 builder 与现状不符，
属一次性迁移件，cm35 **不修复其可运行性**；收口时在 handoff 点明「builder 已随 cm35 拆分作废，
误运行会把门面覆盖回单体，如需重建须改写为多兄弟产出」。

## 七、门面 `__all__`（113 项，含私有）

52 类 + 61 常量全量导出，私有 6 个务必列入：`_SHA256_PATTERN`、`_FACT_ID_PATTERN`、
`_CARD_ID_PATTERN`、`_ANCHOR_ID_PATTERN`、`_SHA`、`_CARD`。由执行器排序生成，
`tests/test_contracts_admission_imports.py` 断言门面 `__all__` == 兄弟并集且各符号可从门面取到，
并对全部 pydantic 类断言 `__pydantic_complete__ is True`（防跨模块模型注解未在运行期解析）。

## 八、执行与验证口径

执行器 `.test-tmp/cm35_split.py`：按 AST 逐符号切片（含各符号自身源跨度，逐符号字节相等校验）→
先 dry-run 打印落点与逐模块 import → 全部渲染成新字节、全通过再逐个落盘 → 门面重导出。
新增 `tests/test_contracts_admission_imports.py`（门面完整性 + `__pydantic_complete__`）。
layering 由既有 `test_knowledge_contracts_layering.py` 兜住（本次只改 `LAYER_ORDER`）。

固定验证链（各命令单独跑、看末行）：ruff（src tests migration tools）→ 全项目 mypy（裸 `mypy`）→
严格档 mypy（`gates`+`compilers`，本次预期不变）→ 定向 pytest（新旧契约测试 + 两条封印）→
全量 pytest + 覆盖（两档：语料在位 REQUIRE=1 / 语料缺席 CI 口径）→ 还原 7 个 golden →
`tools/eolaudit.py` 要求 `VIOLATIONS=0`（并单独确认 8 个新文件 crlf=0）。
提交：逐路径 `git add`，`tools/msgnorm.py` 规整 `.test-tmp/cm35.txt` 后临时身份 `commit -F`，**不推送**。
