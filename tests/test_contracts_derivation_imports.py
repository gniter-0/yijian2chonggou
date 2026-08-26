"""derivation 门面完整性封印（cm36 蓝图 §7）。

cm36 把单体 derivation.py（1256 行 / 23 个顶层符号：11 函数 + 6 私有类 + 6 私有常量）机械拆成
4 个扁平兄弟 + 原名门面。本封印钉住四件事：

1. 拆前 23 个顶层符号（含大量 `_` 私有）逐一仍可从原路径取到，且 `__all__` 与之精确相等；
2. `__all__` 里每个名字都解析得到；
3. 门面自己不定义实现：取到的每个函数/类都归属某个 `derivation_*` 兄弟；
4. 每个符号绑定到蓝图 §3 声明的归属兄弟里的**同一对象**（`is` 比对）。

derivation 家族里没有 pydantic 模型（6 个私有类都是 dataclass/TypedDict），故不含
`__pydantic_complete__` 那条断言；改为顺手钉住「6 个私有类都在兄弟命名空间里、门面不自造类」。
"""
from __future__ import annotations

import inspect
from importlib import import_module

import examcard.knowledge.contracts.derivation as facade

FAMILY_PREFIX = "examcard.knowledge.contracts.derivation_"
FACADE_MODULE = "examcard.knowledge.contracts.derivation"

# 拆前 derivation.py 的顶层符号表（AST 扫描，23 个）。
PRE_SPLIT_NAMES = (
    "_CONDITION_SPAN_BOUNDARIES",
    "_ConditionSpanCandidate",
    "_DUAL_ROLE_ANCHOR_PROPOSAL_TYPES",
    "_MINIMAL_MODEL_PROPOSAL_TYPES",
    "_MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION",
    "_MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES",
    "_NumericSpanCandidate",
    "_STRUCTURAL_INDEX_COUNT_UNIT_PATTERN",
    "_TableNumericOccurrenceCandidate",
    "_TableNumericOccurrenceKwargs",
    "_TableStructuralCellOccurrenceKwargs",
    "_TableStructuralIndexOccurrenceCandidate",
    "_assertion_token_is_quantity_count",
    "_complete_condition_span",
    "_condition_span_reference",
    "_exact_common_condition_span",
    "_numeric_span_reference",
    "_table_grid_metadata_token_bindings",
    "_table_numeric_occurrence_sources",
    "derive_step2a_anchor_span_selection",
    "derive_step2a_condition_claims",
    "derive_step2a_numeric_claims",
    "derive_step2a_table_cell_anchors",
)

# 每个符号在蓝图 §3 里的归属兄弟（相对 derivation_ 前缀）。
SYMBOL_OWNERS = {
    "_MINIMAL_MODEL_PROPOSAL_TYPES": "anchor_span",
    "_DUAL_ROLE_ANCHOR_PROPOSAL_TYPES": "anchor_span",
    "_MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES": "anchor_span",
    "_MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION": "anchor_span",
    "derive_step2a_anchor_span_selection": "anchor_span",
    "derive_step2a_table_cell_anchors": "table_cell",
    "_CONDITION_SPAN_BOUNDARIES": "condition",
    "_ConditionSpanCandidate": "condition",
    "_condition_span_reference": "condition",
    "_complete_condition_span": "condition",
    "_exact_common_condition_span": "condition",
    "derive_step2a_condition_claims": "condition",
    "_NumericSpanCandidate": "numeric",
    "_TableNumericOccurrenceCandidate": "numeric",
    "_TableStructuralIndexOccurrenceCandidate": "numeric",
    "_TableStructuralCellOccurrenceKwargs": "numeric",
    "_TableNumericOccurrenceKwargs": "numeric",
    "_numeric_span_reference": "numeric",
    "_table_numeric_occurrence_sources": "numeric",
    "_table_grid_metadata_token_bindings": "numeric",
    "_STRUCTURAL_INDEX_COUNT_UNIT_PATTERN": "numeric",
    "_assertion_token_is_quantity_count": "numeric",
    "derive_step2a_numeric_claims": "numeric",
}


def test_pre_split_symbol_table_is_23_names() -> None:
    assert len(PRE_SPLIT_NAMES) == 23
    assert len(set(PRE_SPLIT_NAMES)) == 23
    assert set(SYMBOL_OWNERS) == set(PRE_SPLIT_NAMES)


def test_every_pre_split_name_is_importable_from_the_facade() -> None:
    missing = [name for name in PRE_SPLIT_NAMES if not hasattr(facade, name)]
    assert not missing, f"门面丢了拆分前的名字: {missing}"


def test_all_equals_the_pre_split_symbol_table() -> None:
    exported = set(facade.__all__)
    assert exported == set(PRE_SPLIT_NAMES), f"__all__ 漂移: {exported ^ set(PRE_SPLIT_NAMES)}"


def test_all_entries_resolve() -> None:
    broken = [name for name in facade.__all__ if not hasattr(facade, name)]
    assert not broken, f"__all__ 里有解析不了的名字: {broken}"


def test_facade_defines_no_implementation_of_its_own() -> None:
    """门面只转口：每个函数/类都必须归属某个 derivation_* 兄弟。

    只看函数与类：正则字面量、类元组这类值没有定义位置意义上的 __module__，据此判断会误伤。
    """
    stray = []
    for name in facade.__all__:
        obj = getattr(facade, name)
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        owner = obj.__module__
        if owner == FACADE_MODULE or not owner.startswith(FAMILY_PREFIX):
            stray.append(f"{name} <- {owner}")
    assert not stray, f"门面自己定义了实现或从家族外取名: {stray}"


def test_every_symbol_binds_its_declared_owner() -> None:
    """23 个符号逐一用 `is` 比对声明归属兄弟里的同一对象。

    归属模块取不到时走哨兵，让 23 条全部走完、一次给齐 mismatch 清单。
    """
    missing = object()
    mismatched = []
    for name, group in sorted(SYMBOL_OWNERS.items()):
        owner_module = import_module(f"{FAMILY_PREFIX}{group}")
        owned = getattr(owner_module, name, missing)
        if owned is missing:
            mismatched.append(f"{group} 里没有 {name}")
        elif getattr(facade, name) is not owned:
            mismatched.append(f"{name} != {group}.{name}")
        if name not in facade.__all__:
            mismatched.append(f"{name} 不在 __all__ 里")
    assert not mismatched, f"符号与声明归属不符: {mismatched}"


def test_six_private_classes_live_in_siblings() -> None:
    """6 个私有类（4 dataclass + 2 TypedDict）都在兄弟命名空间里，门面不自造类。

    derivation 家族无 pydantic 模型，故不查 __pydantic_complete__；这里改钉私有类数量，
    少一个说明有 dataclass/TypedDict 被漏搬或降级成别的东西。
    """
    classes = [name for name in facade.__all__ if inspect.isclass(getattr(facade, name))]
    assert len(classes) == 6, f"私有类数量变了: {sorted(classes)}"
    external = [
        name
        for name in classes
        if not getattr(facade, name).__module__.startswith(FAMILY_PREFIX)
    ]
    assert not external, f"这些类不在 derivation_* 兄弟里: {external}"
