# cm36 拆分蓝图：knowledge/contracts/derivation.py

## 一、背景与目标

`src/examcard/knowledge/contracts/derivation.py`（1256 行 / 61,798 字节 / **纯 LF** / 无 BOM / 当前无 `__all__`）
是 Step 2A 的四条本地确定性派生管线。cm36 按既定六步套路把它机械拆成 **4 个同级 `derivation_*` 兄弟 + 原名门面**，
只搬不改、逐符号逐字节相等，门面 `__all__` 精确等于拆分前 23 个顶层名。

顶层 23 符号 = 11 函数（4 公开 `derive_step2a_*` + 7 私有助手）+ 6 私有类（dataclass / TypedDict）+ 6 私有常量。

## 二、EOL / 档位归属

- derivation.py 属 `knowledge/contracts/` **纯 LF 家族**：4 兄弟 / 门面 / 新封印测试一律直接 Write（落盘即 LF）。
- `tests/test_knowledge_contracts_derivation_shape_seal.py` 实测 **纯 LF**（crlf=0）→ 可用 Edit 直接改。
- `tests/test_knowledge_contracts_layering.py` 实测 **纯 CRLF**（crlf=235）→ `LAYER_ORDER` 插入只能走锚点唯一命中的字节脚本，禁 Write/Edit。
- 仅在全项目 mypy 档；严格档（gates+compilers）不含本文件。
- 收口前 `tools/eolaudit.py` 必须 VIOLATIONS=0；新兄弟为纯 LF（家族风格）。

## 三、分片清单 + 层序（互引图零交叉 → 4 兄弟同层）

互引图显示四条管线彼此**零顶层交叉引用**，故 4 个兄弟无兄弟间边、同处一层，均只依赖更低秩的既有模块：

1. **`derivation_anchor_span.py`** —— `derive_step2a_anchor_span_selection`（L150-329）+ 4 张 `Final` 形状表
   `_MINIMAL_MODEL_PROPOSAL_TYPES` / `_DUAL_ROLE_ANCHOR_PROPOSAL_TYPES` / `_MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES` /
   `_MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION`。**shape_seal 检查的全部代码都在此模块**（见 §五）。
2. **`derivation_table_cell.py`** —— `derive_step2a_table_cell_anchors`（L332-401，独立，无顶层引用）。
3. **`derivation_condition.py`** —— `derive_step2a_condition_claims`（L505-686）+ `_condition_span_reference` /
   `_complete_condition_span` / `_exact_common_condition_span` / `_ConditionSpanCandidate` / `_CONDITION_SPAN_BOUNDARIES`。
4. **`derivation_numeric.py`** —— `derive_step2a_numeric_claims`（L973-1256）+ `_numeric_span_reference` /
   `_table_numeric_occurrence_sources` / `_table_grid_metadata_token_bindings` / `_assertion_token_is_quantity_count` /
   `_NumericSpanCandidate` / `_TableNumericOccurrenceCandidate` / `_TableStructuralIndexOccurrenceCandidate` /
   `_TableStructuralCellOccurrenceKwargs` / `_TableNumericOccurrenceKwargs` / `_STRUCTURAL_INDEX_COUNT_UNIT_PATTERN`。

门面 `derivation.py` 运行期 import 这 4 个兄弟，故其 rank 必须高于全部兄弟。

## 四、逐模块 import 口径

- 4 个兄弟**都要** `if TYPE_CHECKING: from .step2a_request import Step2ARequest`——4 个公开函数签名均以它标注 `request`。
- 其余外部 import（stdlib：hashlib/re/collections.abc/dataclasses/typing；pydantic.BaseModel；`...evidence.layout`；
  `..schema.FactType`；同族 audits/claims/enums/numeric_tokens/occurrences/proposals/request_helpers/table_inputs/versions）
  由切片脚本按各兄弟切片文本里实际出现的名字**逐模块最小化**导出，避免未用 import 触发 ruff F401。
- 无 pydantic 模型定义（类均为 dataclass/TypedDict）→ 无 `__pydantic_complete__` 顾虑，无需跨模块前向注解处置。

## 五、既有封印同步（本次的地雷所在）

### 5.1 `tests/test_knowledge_contracts_derivation_shape_seal.py`（专属形状封印，纯 LF）

该封印 `_SOURCE = Path(derivation.__file__)` 后**直接 AST 解析该源文件**，并以 `derivation.<私有>` /
`getattr(derivation, "Step2AVxx…")` 取符号。拆分后 `derive_step2a_anchor_span_selection` + 4 张 `Final` 表 +
isinstance 窄化逻辑**全部落在 `derivation_anchor_span.py`**，门面里已无这些定义，故必须让封印**跟着代码搬家**：

- 顶部加 `from examcard.knowledge.contracts import derivation_anchor_span`（保留 `derivation`、`versions`）；
- `_SOURCE` 改指 `Path(derivation_anchor_span.__file__)`；
- 所有**结构性**取名改指兄弟命名空间：`_union_members` 里的 `getattr(derivation, name)` → `getattr(derivation_anchor_span, name)`；
  `derivation._MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION`、三张类元组、`derivation.Step2AModelFactProposal`
  一律改成 `derivation_anchor_span.<X>`。原因：那 12 个 union 成员类（`Step2AModelFactProposal` / `Step2AVxx…`）
  是 **import 进来的名**，门面默认只再导出 derivation 自身定义的 23 个顶层符号、不含这些外来名，
  故只能从 anchor_span 兄弟的命名空间取。
- `test_derivation_carries_no_type_ignore` 由「扫单文件」扩成「扫门面 + 4 兄弟整个家族」，
  保证 cm30 拆掉的那条 arg-type 盖子在任何一个新落点都长不回来。

注：waiver 棘轮 `tests/test_mypy_waiver_ratchet.py` 的 `_IGNORE_CEILING` **无 derivation 条目**（cm30 已降掉），
故本次**不动 ceiling**；且任何兄弟里冒出 `type: ignore` 都会被棘轮的 `test_pinpoint_ignores_match_ceiling_exactly` 全局逮到。

### 5.2 `tests/test_knowledge_contracts_layering.py`（家族层序封印，纯 CRLF）

`derivation` 已在 `LAYER_ORDER`（`step2a_builders` 之后、`validation` 之前）。用锚点唯一命中脚本把 4 个兄弟
按任意序插在门面名 `derivation` **之前**（同层、无兄弟间边）。锚点取 `    "step2a_builders",<CRLF>    "derivation",`，
count==1、无 BOM、lone_lf==0 三重护栏。`family_members()` 走非递归 glob 自动清点，漏登记兄弟会被
`test_layer_order_covers_every_module_file` 当场逮到。

## 六、门面 `__all__`

门面 `derivation.py` 对 4 个兄弟逐一 `from .derivation_X import (...)`，随后 `__all__ = [...]` **精确等于拆分前 23 名**
（4 公开函数 + 7 私有函数 + 6 私有类 + 6 私有常量）。保留原模块 docstring 首行。
外部真实使用面仅 4 个公开函数（`contracts/__init__.py` 全取、`validation.py` 取 condition/numeric 两个），
由门面全量再导出兜住，`contracts/__init__.py` 与 `validation.py` 无需改动。

## 七、新封印 `tests/test_contracts_derivation_imports.py`（纯 LF，门面完整性）

仿 cm35 的 `test_contracts_admission_imports.py`，钉住：
1. `PRE_SPLIT_NAMES`（23 元）数量与去重；
2. 23 名逐一可从门面 `derivation` 取到；
3. `__all__` 精确等于 `PRE_SPLIT_NAMES`；`__all__` 每项可解析；
4. 门面自身不定义实现——每个函数/类的 `__module__` 都归属某个 `derivation_*` 兄弟（常量无 `__module__`，跳过）；
5. `SYMBOL_OWNERS`（23 名 → 兄弟）逐一 `is` 比对：门面取到的对象与其声明兄弟里的**同一对象**。

## 八、执行与验证口径

1. 切片脚本 `.test-tmp/cm36_split.py`：dry-run 先行，`ast.get_source_segment` 逐符号比对，确认 4 兄弟拼回与原文**逐字节相等**后再 `--write`；`write_text(..., newline="\n")`。
2. 一次性 builder 排查：`migration/split_semantic_contracts.py` L983 仅字符串字面量（codegen 模板），非真 import——但拆分后该模板与现状不符，收口时在 handoff 点明其已过时（与 cm35 的 build_admission_fixtures 作废同理）。
3. 三处封印各注入 1 条漂移（漏登记兄弟 / shape_seal 少改指一处 / 门面漏再导出一名）验证咬合，再复原。
4. 固定验证链（逐条独立、看末行）：ruff（src tests migration tools）→ 全项目 `mypy` → 严格档 `mypy src/examcard/gates src/examcard/compilers --follow-imports=silent --no-incremental` → 针对性 pytest（shape_seal + layering + waiver + 新 imports + contracts imports）→ 两档全量 pytest+coverage → 复原 7 金标 → `tools/eolaudit.py` VIOLATIONS=0。
5. 逐路径 `git add` + 临时身份 `-F .test-tmp/cm36.txt` 本地提交，**不推送**。
