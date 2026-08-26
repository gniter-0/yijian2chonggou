"""Facade completeness for examcard.compilers.step4_contract（蓝图 §8-2）。

cm34 把实现搬到同级 step4_contract_* 兄弟后，`step4_contract` 只剩再导出。本封印钉住四件事：

1. 拆前 80 个顶层符号（含私有助手）逐一仍可从 `examcard.compilers.step4_contract` 取到，
   且 `__all__` 与之精确相等——多一个少一个都算门面漂移；
2. 机器扫出的 14 个真实外部名（5 个 importer 文件）绑定的就是新归属模块里的同一对象；
3. 门面自己不定义实现，取到的每个函数/类都归属某个 step4_contract_* 兄弟；
4. **pydantic 模型在新模块里仍然建完**：`Phase2R4ContractBundle` / `Phase2R4ModelInput` 的
   字段注解引用 `GenericCardContract`，而全家都开了 `from __future__ import annotations`，
   字段注解是字符串，必须在 *定义模块* 的命名空间里可解析。搬迁最可能悄悄坏在这一点上——
   模型会以未建完状态存在，直到第一次实例化才炸。这里直接断言 24 个模型 `__pydantic_complete__`。
"""
from __future__ import annotations

import inspect

import examcard.compilers.step4_contract as facade
from examcard.compilers.step4_contract_constants import (
    DEFAULT_FAKE_FILENAME_PREFIX as constants_prefix,
)
from examcard.compilers.step4_contract_derive import _extract_tokens as derive_extract_tokens
from examcard.compilers.step4_contract_derive import derive_step4_bundle as derive_bundle
from examcard.compilers.step4_contract_gate import ExpectedCounts as gate_expected_counts
from examcard.compilers.step4_contract_gate import assess_offline_gate as gate_assess
from examcard.compilers.step4_contract_hashing import _canonical_hash as hashing_canonical
from examcard.compilers.step4_contract_models_generic import (
    GenericVisibleText as generic_visible_text,
)

# 拆前 step4_contract.py 的顶层符号表（AST 扫描，80 个：常量 32、hash 助手 3、
# 通用图卡模型 9、Phase2R4 工件模型 15、dataclass 1、派生/校验/闸门函数 20；不含 __all__）。
PRE_SPLIT_NAMES = (
    "BATCH_VERSION",
    "CACHE_VERSION",
    "CONTRACT_BUNDLE_VERSION",
    "DEFAULT_FAKE_FILENAME_PREFIX",
    "ExpectedCounts",
    "FAKE_ARTIFACT_VERSION",
    "FORBIDDEN_MODEL_KEYS",
    "FORBIDDEN_TEXT",
    "FROZEN_ARTIFACT_COUNT",
    "GATE_VERSION",
    "GenericCardContract",
    "GenericGeometry",
    "GenericOrderedListContract",
    "GenericOrderedListItem",
    "GenericSemanticEdge",
    "GenericTableCell",
    "GenericTableContract",
    "GenericTokenBinding",
    "GenericVisibleText",
    "HISTORICAL_IDENTITY_COUNT",
    "JOURNAL_VERSION",
    "MARKERS",
    "MODEL_INPUT_VERSION",
    "OUTPUT_HEIGHT",
    "OUTPUT_WIDTH",
    "PHASE",
    "PROMPT_VERSION",
    "Phase2R4BatchEntry",
    "Phase2R4BatchManifest",
    "Phase2R4CacheRecord",
    "Phase2R4ContractBundle",
    "Phase2R4FakeArtifact",
    "Phase2R4FakeArtifactManifest",
    "Phase2R4FreezeAudit",
    "Phase2R4FrozenArtifact",
    "Phase2R4JournalRecord",
    "Phase2R4ModelInput",
    "Phase2R4OfflineGate",
    "Phase2R4PromptContract",
    "Phase2R4RegressionResult",
    "Phase2R4Report",
    "Phase2R4Request",
    "REGRESSION_VERSION",
    "RENDER_PRESENTATIONS",
    "REPORT_VERSION",
    "REQUEST_VERSION",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "SOURCE_PRESENTATIONS",
    "TOKEN_PATTERN",
    "_CARD",
    "_FACT",
    "_FAKE_FILENAME",
    "_JOB",
    "_MODEL_INPUT",
    "_REQUEST",
    "_SHA",
    "_canonical_hash",
    "_derive_batch_manifest",
    "_derive_card",
    "_derive_edge",
    "_derive_fake_artifact",
    "_derive_fake_manifest",
    "_derive_list",
    "_derive_model_input",
    "_derive_request",
    "_derive_table",
    "_derive_visible_text",
    "_extract_tokens",
    "_json_bytes",
    "_no_forbidden_model_keys",
    "_no_forbidden_text",
    "_sha256",
    "_source_card_payload",
    "_source_text_payload",
    "_walk_mappings",
    "assess_offline_gate",
    "build_regression_matrix",
    "derive_contracts",
    "derive_step4_bundle",
)

# 机器扫描的真实外部使用面：5 个 importer 文件只取这 14 个名字，
# 其中 4 个是私有助手（_canonical_hash / _extract_tokens / _no_forbidden_text / _walk_mappings）。
EXTERNAL_NAMES = (
    "derive_step4_bundle",
    "assess_offline_gate",
    "ExpectedCounts",
    "_walk_mappings",
    "_no_forbidden_text",
    "_canonical_hash",
    "_extract_tokens",
    "GenericVisibleText",
    "GenericTableContract",
    "GenericOrderedListContract",
    "GenericOrderedListItem",
    "GenericTokenBinding",
    "MARKERS",
    "DEFAULT_FAKE_FILENAME_PREFIX",
)


def test_pre_split_symbol_table_is_80_names() -> None:
    assert len(PRE_SPLIT_NAMES) == 80
    assert len(set(PRE_SPLIT_NAMES)) == 80


def test_every_pre_split_name_is_importable_from_the_facade() -> None:
    missing = [name for name in PRE_SPLIT_NAMES if not hasattr(facade, name)]
    assert not missing, f"facade lost pre-split names: {missing}"


def test_all_equals_the_pre_split_symbol_table() -> None:
    exported = set(facade.__all__)
    assert exported == set(PRE_SPLIT_NAMES), f"__all__ drifted: {exported ^ set(PRE_SPLIT_NAMES)}"


def test_all_entries_resolve() -> None:
    broken = [name for name in facade.__all__ if not hasattr(facade, name)]
    assert not broken, f"__all__ declares unresolvable names: {broken}"


def test_facade_defines_no_implementation_of_its_own() -> None:
    """门面只转口：每个函数/类都必须归属某个 step4_contract_* 兄弟。

    只看函数与类：常量、正则、typing 别名没有定义位置意义上的 `__module__`，据此判断会误伤。
    """
    stray = []
    prefix = "examcard.compilers.step4_contract_"
    for name in facade.__all__:
        obj = getattr(facade, name)
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        owner = obj.__module__
        if owner == "examcard.compilers.step4_contract" or not owner.startswith(prefix):
            stray.append(f"{name} <- {owner}")
    assert not stray, f"facade 自己定义了实现或从家族外取名: {stray}"


def test_external_names_bind_their_new_owners() -> None:
    assert facade.derive_step4_bundle is derive_bundle
    assert facade._extract_tokens is derive_extract_tokens
    assert facade.assess_offline_gate is gate_assess
    assert facade.ExpectedCounts is gate_expected_counts
    assert facade._canonical_hash is hashing_canonical
    assert facade.GenericVisibleText is generic_visible_text
    assert facade.DEFAULT_FAKE_FILENAME_PREFIX is constants_prefix
    for name in EXTERNAL_NAMES:
        assert name in facade.__all__


def test_every_model_finished_building_in_its_new_module() -> None:
    """字段注解跨模块后仍必须可解析：未建完的模型要在这里当场断，而不是等到首次实例化。

    头号坑是 Phase2R4ContractBundle / Phase2R4ModelInput 的字段引用 GenericCardContract，
    一旦 models_phase 漏 import models_generic，模型会以未建完状态存在到首次实例化。
    """
    incomplete = []
    completed = 0
    for name in facade.__all__:
        obj = getattr(facade, name)
        if not inspect.isclass(obj) or not hasattr(obj, "__pydantic_complete__"):
            continue
        completed += 1
        if not obj.__pydantic_complete__:
            incomplete.append(f"{name} ({obj.__module__})")
    assert not incomplete, f"模型未建完，字段注解在新模块里解析不了: {incomplete}"
    assert completed == 24, f"pydantic 模型数应为 24，实测 {completed}"


