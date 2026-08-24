"""Facade completeness for examcard.knowledge.llm (blueprint section 8-2).

cm25 把实现搬到同级 llm_* 兄弟后，`llm` 只剩再导出。本封印钉住四件事：

1. 拆前 22 个顶层符号（含私有助手）逐一仍可从 `examcard.knowledge.llm` 取到，
   且 `__all__` 与之精确相等——多一个少一个都算门面漂移；
2. 机器扫出的 9 个真实外部名（19 个 importer 文件）绑定的就是新归属模块里的同一对象；
3. 门面自己不定义实现，取到的每个函数/类都归属某个 llm_* 兄弟；
4. **pydantic 模型在新模块里仍然建完**：本家族的模型字段互相注解（执行记录引用评审载荷与
   取证载荷），而全家都开了 `from __future__ import annotations`，字段注解是字符串，必须在
   *定义模块* 的命名空间里可解析。搬迁最可能悄悄坏在这一点上——模型会以未完成状态存在，
   直到第一次实例化才炸。这里直接断言 `__pydantic_complete__`。
"""
from __future__ import annotations

import inspect

import examcard.knowledge.llm as llm
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter as adapter_adapter
from examcard.knowledge.llm_journal import Step2JournalEvent as journal_event
from examcard.knowledge.llm_records import Step2AExecutionRecord as records_step2a
from examcard.knowledge.llm_support import _sanitized_error_message as support_sanitizer
from examcard.knowledge.llm_transport import (
    OpenAICompatibleStep2Transport as transport_openai_compatible,
)

# 拆前 llm.py 的顶层符号表（AST 扫描，22 个，含常量、类型别名与私有助手）。
PRE_SPLIT_NAMES = (
    "CallableStep2Transport",
    "OpenAICompatibleStep2Transport",
    "SemanticReviewState",
    "Step2AExecutionRecord",
    "Step2BExecutionRecord",
    "Step2JournalEvent",
    "Step2JournalOutcome",
    "Step2OpenAIAdapter",
    "Step2RejectedResponseForensics",
    "Step2SchemaErrorLocation",
    "Step2TeachingReview",
    "Step2Transport",
    "TransportCompletion",
    "_ChatResponseFormatParam",
    "_SHA256_PATTERN",
    "_append_event",
    "_atomic_write_json",
    "_json_dumps",
    "_make_event_id",
    "_sanitized_error_message",
    "_sha256",
    "_utc_now",
)

# 机器扫描的真实外部使用面：19 个 importer 文件（4 个 migration 脚本、2 个 src 模块、
# 13 个测试/夹具）只取这 9 个名字，其中 `_sanitized_error_message` 是私有助手。
EXTERNAL_NAMES = (
    "CallableStep2Transport",
    "OpenAICompatibleStep2Transport",
    "Step2AExecutionRecord",
    "Step2BExecutionRecord",
    "Step2JournalEvent",
    "Step2JournalOutcome",
    "Step2OpenAIAdapter",
    "TransportCompletion",
    "_sanitized_error_message",
)


def test_pre_split_symbol_table_is_22_names() -> None:
    assert len(PRE_SPLIT_NAMES) == 22
    assert len(set(PRE_SPLIT_NAMES)) == 22


def test_every_pre_split_name_is_importable_from_the_facade() -> None:
    missing = [name for name in PRE_SPLIT_NAMES if not hasattr(llm, name)]
    assert not missing, f"facade lost pre-split names: {missing}"


def test_all_equals_the_pre_split_symbol_table() -> None:
    exported = set(llm.__all__)
    assert exported == set(PRE_SPLIT_NAMES), f"__all__ drifted: {exported ^ set(PRE_SPLIT_NAMES)}"


def test_all_entries_resolve() -> None:
    broken = [name for name in llm.__all__ if not hasattr(llm, name)]
    assert not broken, f"__all__ declares unresolvable names: {broken}"


def test_facade_defines_no_implementation_of_its_own() -> None:
    """门面只转口：每个函数/类都必须归属某个 llm_* 兄弟。

    只看函数与类：`_SHA256_PATTERN`（str）与 `_ChatResponseFormatParam`（TypedDict 联合）
    没有定义位置意义上的 `__module__`，据此判断会误伤。
    """
    stray = []
    for name in llm.__all__:
        obj = getattr(llm, name)
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        owner = obj.__module__
        if owner == "examcard.knowledge.llm" or not owner.startswith("examcard.knowledge.llm_"):
            stray.append(f"{name} <- {owner}")
    assert not stray, f"facade 自己定义了实现或从家族外取名: {stray}"


def test_external_names_bind_their_new_owners() -> None:
    assert llm.Step2OpenAIAdapter is adapter_adapter
    assert llm.OpenAICompatibleStep2Transport is transport_openai_compatible
    assert llm.Step2AExecutionRecord is records_step2a
    assert llm.Step2JournalEvent is journal_event
    assert llm._sanitized_error_message is support_sanitizer
    for name in EXTERNAL_NAMES:
        assert name in llm.__all__


def test_every_model_finished_building_in_its_new_module() -> None:
    """字段注解跨模块后仍必须可解析：未建完的模型要在这里当场断，而不是等到首次实例化。"""
    incomplete = []
    for name in llm.__all__:
        obj = getattr(llm, name)
        if not inspect.isclass(obj) or not hasattr(obj, "__pydantic_complete__"):
            continue
        if not obj.__pydantic_complete__:
            incomplete.append(f"{name} ({obj.__module__})")
    assert not incomplete, f"模型未建完，字段注解在新模块里解析不了: {incomplete}"
