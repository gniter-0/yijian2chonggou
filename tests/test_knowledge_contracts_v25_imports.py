"""Facade completeness for examcard.knowledge.contracts.v25_generic_contracts（蓝图 §8-2）。

cm28 把实现搬到同级 v25_* 兄弟（15 个）后，`v25_generic_contracts` 只剩再导出。本封印钉住六件事：

1. 拆前 79 个顶层符号（含 28 个版本常量与 11 个私有助手）逐一仍可从原路径取到，
   且 `__all__` 与之精确相等——多一个少一个都算门面漂移；
2. 机器扫出的 31 个真实外部名（8 个 importer：1 个 migration 脚本、5 个同族契约模块、2 个测试）
   绑定的就是新归属模块里的**同一对象**；
3. 门面自己不定义实现，取到的每个函数/类都归属某个 v25_* 兄弟；
4. **`_v25_id` 的 24 位截断没被搬歪**：这个数字参与既有黄金件的业务 id 比对，改一位就是改字节权威。
   同级搬迁本不该碰它，但「顺手改数字」是拆分里最典型的静默故障，故直接兑现；
5. **pydantic 模型在新模块里仍然建完**：全家都开了 `from __future__ import annotations`，字段注解是
   字符串，必须在*定义模块*的命名空间里可解析。本次拆分有 2 处前向注解走了 `if TYPE_CHECKING:`
   （载荷函数签名标注请求模型），一旦有人把模型→模型的边也塞进去，模型会以未建完状态存在、
   直到第一次校验才抛 `PydanticUserError`——这里直接断言 23 个模型的 `__pydantic_complete__`；
6. `phase2r14_v25_live` 那处 `__import__(..., fromlist=[...])` 取到的是**门面**模块对象，
   属性必须仍从门面解析得到（层序侧的登记见 test_knowledge_contracts_layering.py）。
"""
from __future__ import annotations

import inspect
import re
from importlib import import_module

import examcard.knowledge.contracts as contracts
import examcard.knowledge.contracts.v25_generic_contracts as v25

# 拆前 v25_generic_contracts.py 的顶层符号表（AST 扫描，79 个）。
PRE_SPLIT_NAMES = (
    "V25AnchorContext",
    "V25AtomicConditionOperator",
    "V25AtomicNumericExpression",
    "V25AtomicSourceObligation",
    "V25BBlockedPreflight",
    "V25BGroupingGate",
    "V25BGroupingKind",
    "V25BGroupingObligation",
    "V25BModelCardProposal",
    "V25BSourceFamilyBinding",
    "V25BSourceFamilyKind",
    "V25BStep2BRequest",
    "V25BStep2BResultPayload",
    "V25B_STEP2B_JOURNAL_VERSION",
    "V25B_STEP2B_MODEL_INPUT_VERSION",
    "V25B_STEP2B_PROMPT_VERSION",
    "V25B_STEP2B_REQUEST_VERSION",
    "V25CoverageObligationContext",
    "V25FactReviewState",
    "V25JournalRecord",
    "V25ListSourceObligation",
    "V25ReviewedContinuationBinding",
    "V25SemanticDiagramBridge",
    "V25SemanticDiagramEdge",
    "V25SemanticDiagramNode",
    "V25Step2AContractProfile",
    "V25Step2AGate",
    "V25Step2AModelFactProposal",
    "V25Step2ARequest",
    "V25Step2AResultPayload",
    "V25Step2BFactSummary",
    "V25TextSourceObligation",
    "V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION",
    "V25_ATOMIC_SOURCE_STEP2A_MODEL_INPUT_VERSION",
    "V25_ATOMIC_SOURCE_STEP2A_PROMPT_VERSION",
    "V25_ATOMIC_SOURCE_STEP2A_REQUEST_VERSION",
    "V25_COST_FLOW_CANONICAL_STEP2A_JOURNAL_VERSION",
    "V25_COST_FLOW_CANONICAL_STEP2A_MODEL_INPUT_VERSION",
    "V25_COST_FLOW_CANONICAL_STEP2A_PROMPT_VERSION",
    "V25_COST_FLOW_CANONICAL_STEP2A_REQUEST_VERSION",
    "V25_DIAGRAM_CANONICAL_STEP2A_JOURNAL_VERSION",
    "V25_DIAGRAM_CANONICAL_STEP2A_MODEL_INPUT_VERSION",
    "V25_DIAGRAM_CANONICAL_STEP2A_PROMPT_VERSION",
    "V25_DIAGRAM_CANONICAL_STEP2A_REQUEST_VERSION",
    "V25_LIST_CANONICAL_STEP2A_JOURNAL_VERSION",
    "V25_LIST_CANONICAL_STEP2A_MODEL_INPUT_VERSION",
    "V25_LIST_CANONICAL_STEP2A_PROMPT_VERSION",
    "V25_LIST_CANONICAL_STEP2A_REQUEST_VERSION",
    "V25_STANDARD_CANONICAL_STEP2A_JOURNAL_VERSION",
    "V25_STANDARD_CANONICAL_STEP2A_MODEL_INPUT_VERSION",
    "V25_STANDARD_CANONICAL_STEP2A_PROMPT_VERSION",
    "V25_STANDARD_CANONICAL_STEP2A_REQUEST_VERSION",
    "V25_STEP2A_JOURNAL_VERSION",
    "V25_STEP2A_MODEL_INPUT_VERSION",
    "V25_STEP2A_PROMPT_VERSION",
    "V25_STEP2A_REQUEST_VERSION",
    "_OFFLINE_RELAY_BASE_URL",
    "_SHA256_PATTERN",
    "_SOURCE_FAMILY_PRESENTATION",
    "_label_from_source",
    "_presentation_for_fact_type",
    "_section_anchor_map",
    "_table_by_id",
    "_v25_contexts",
    "_v25_id",
    "assess_v25_step2a_response",
    "assess_v25b_step2b_response",
    "build_v25_source_fake_response",
    "build_v25_step2a_request",
    "build_v25b_source_fake_response",
    "build_v25b_step2b_request",
    "derive_v25b_grouping_obligations",
    "make_v25_offline_journal",
    "make_v25b_offline_journal",
    "normalize_v25_source_assertion",
    "v25_step2a_prompt_template",
    "v25_step2a_request_payload",
    "v25b_step2b_prompt_template",
    "v25b_step2b_request_payload",
)

# 机器扫描的真实外部使用面：8 个 importer 只取这 31 个名字。值 = 拆分后的新归属模块。
# v25_support 一个外部名都没有：那 4 个符号（摘要形状、离线中继基址、两个字符串助手）全是家族内部件。
EXTERNAL_OWNERS = {
    "V25_STEP2A_JOURNAL_VERSION": "v25_versions",
    "V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION": "v25_versions",
    "V25B_STEP2B_JOURNAL_VERSION": "v25_versions",
    "V25Step2AContractProfile": "v25_enums",
    "V25FactReviewState": "v25_enums",
    "V25BSourceFamilyKind": "v25_enums",
    "V25ReviewedContinuationBinding": "v25_step2a_obligations",
    "v25_step2a_prompt_template": "v25_step2a_prompt",
    "v25_step2a_request_payload": "v25_step2a_prompt",
    "V25Step2AModelFactProposal": "v25_step2a_request",
    "V25Step2AResultPayload": "v25_step2a_request",
    "V25Step2ARequest": "v25_step2a_request",
    "V25Step2AGate": "v25_step2a_request",
    "V25JournalRecord": "v25_step2a_request",
    "build_v25_step2a_request": "v25_step2a_build",
    "assess_v25_step2a_response": "v25_step2a_assess",
    "build_v25_source_fake_response": "v25_step2a_offline",
    "make_v25_offline_journal": "v25_step2a_offline",
    "V25SemanticDiagramNode": "v25_step2b_diagram",
    "V25SemanticDiagramEdge": "v25_step2b_diagram",
    "V25SemanticDiagramBridge": "v25_step2b_diagram",
    "v25b_step2b_prompt_template": "v25_step2b_prompt",
    "v25b_step2b_request_payload": "v25_step2b_prompt",
    "V25Step2BFactSummary": "v25_step2b_models",
    "V25BSourceFamilyBinding": "v25_step2b_models",
    "V25BStep2BResultPayload": "v25_step2b_models",
    "V25BGroupingGate": "v25_step2b_models",
    "V25BBlockedPreflight": "v25_step2b_models",
    "build_v25b_step2b_request": "v25_step2b_build",
    "assess_v25b_step2b_response": "v25_step2b_assess",
    "build_v25b_source_fake_response": "v25_step2b_offline",
}

FAMILY_PREFIX = "examcard.knowledge.contracts.v25_"
FACADE_MODULE = "examcard.knowledge.contracts.v25_generic_contracts"


def test_pre_split_symbol_table_is_79_names() -> None:
    assert len(PRE_SPLIT_NAMES) == 79
    assert len(set(PRE_SPLIT_NAMES)) == 79


def test_every_pre_split_name_is_importable_from_the_facade() -> None:
    missing = [name for name in PRE_SPLIT_NAMES if not hasattr(v25, name)]
    assert not missing, f"门面丢了拆分前的名字: {missing}"


def test_all_equals_the_pre_split_symbol_table() -> None:
    exported = set(v25.__all__)
    assert exported == set(PRE_SPLIT_NAMES), f"__all__ 漂移: {exported ^ set(PRE_SPLIT_NAMES)}"


def test_all_entries_resolve() -> None:
    broken = [name for name in v25.__all__ if not hasattr(v25, name)]
    assert not broken, f"__all__ 里有解析不了的名字: {broken}"


def test_facade_defines_no_implementation_of_its_own() -> None:
    """门面只转口：每个函数/类都必须归属某个 v25_* 兄弟。

    只看函数与类：28 个版本常量、`_SHA256_PATTERN`、`_SOURCE_FAMILY_PRESENTATION` 这类值
    没有定义位置意义上的 `__module__`，据此判断会误伤。
    """
    stray = []
    for name in v25.__all__:
        obj = getattr(v25, name)
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        owner = obj.__module__
        if owner == FACADE_MODULE or not owner.startswith(FAMILY_PREFIX):
            stray.append(f"{name} <- {owner}")
    assert not stray, f"门面自己定义了实现或从家族外取名: {stray}"


def test_external_names_bind_their_new_owners() -> None:
    """31 个外部名逐一用 `is` 比对新归属模块里的同一对象。

    归属模块里取不到名字时走哨兵而不是让 `getattr` 抛 AttributeError：归属表写错一格就中断，
    只会报出第一条坏项；哨兵让 31 条全部走完，一次给齐 mismatch 清单。
    """
    assert len(EXTERNAL_OWNERS) == 31
    missing = object()
    mismatched = []
    for name, module_name in sorted(EXTERNAL_OWNERS.items()):
        owner_module = import_module(f"examcard.knowledge.contracts.{module_name}")
        owned = getattr(owner_module, name, missing)
        if owned is missing:
            mismatched.append(f"{module_name} 里没有 {name}")
        elif getattr(v25, name) is not owned:
            mismatched.append(f"{name} != {module_name}.{name}")
        if name not in v25.__all__:
            mismatched.append(f"{name} 不在 __all__ 里")
    assert not mismatched, f"外部名与新归属不符: {mismatched}"


def test_external_face_touches_fourteen_of_the_fifteen_siblings() -> None:
    """外部使用面覆盖 14 个兄弟；缺的那个是 v25_support（家族内部件，无外部使用者）。"""
    touched = set(EXTERNAL_OWNERS.values())
    assert len(touched) == 14
    assert "v25_support" not in touched


def test_v25_id_truncation_is_still_24_chars() -> None:
    """业务 id 的 24 位截断参与既有黄金件比对，搬家不许碰这个数字。"""
    payload = {"kind": "probe", "items": [1, 2, 3]}
    digest = contracts.canonical_step2_sha256(payload)
    produced = v25._v25_id("probe", payload)
    assert produced == f"probe:{digest[:24]}"
    assert len(produced.split(":", 1)[1]) == 24


def test_sha256_pattern_still_demands_64_hex() -> None:
    """摘要形状是全家共用的一份正则字面量（进 `Field(pattern=...)`）；放宽它等于放宽所有模型的自证。"""
    assert re.fullmatch(v25._SHA256_PATTERN, "a" * 64) is not None
    assert re.fullmatch(v25._SHA256_PATTERN, "a" * 63) is None
    assert re.fullmatch(v25._SHA256_PATTERN, "A" * 64) is None


def test_every_model_finished_building_in_its_new_module() -> None:
    """字段注解跨模块后仍必须可解析：未建完的模型要在这里当场断，而不是等到首次校验。

    23 个模型的数量也一并钉住——少一个说明有模型被漏搬或降级成普通类。
    """
    models = []
    incomplete = []
    for name in v25.__all__:
        obj = getattr(v25, name)
        if not inspect.isclass(obj) or not hasattr(obj, "__pydantic_complete__"):
            continue
        models.append(name)
        if not obj.__pydantic_complete__:
            incomplete.append(f"{name} ({obj.__module__})")
    assert not incomplete, f"模型未建完，字段注解在新模块里解析不了: {incomplete}"
    assert len(models) == 23, f"pydantic 模型数量变了: {len(models)}"


def test_reflected_name_resolves_through_the_facade() -> None:
    """`phase2r14_v25_live` 那处 `__import__(..., fromlist=[...])` 取到的是门面模块对象。

    层序封印登记了这条边并断言目标模块上属性可解析；这里按它的**实际调用形态**再兑现一次：
    带 fromlist 的 `__import__` 返回的是叶子模块（即门面本身），属性由再导出兜住。
    """
    module = __import__(FACADE_MODULE, fromlist=["build_v25b_source_fake_response"])
    assert module is v25
    reflected = getattr(module, "build_v25b_source_fake_response")
    assert reflected is import_module(f"{FAMILY_PREFIX}step2b_offline").build_v25b_source_fake_response
