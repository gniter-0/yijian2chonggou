"""admission_fixtures 门面完整性封印（cm35 蓝图 §7）。

cm35 把单体 admission_fixtures.py（1249 行 / 113 个顶层符号：52 类 + 61 常量）机械拆成 7 个
扁平兄弟 + 原名门面。本封印钉住五件事：

1. 拆前 113 个顶层符号（含 6 个 `_` 私有）逐一仍可从原路径取到，且 `__all__` 与之精确相等；
2. `__all__` 里每个名字都解析得到；
3. 门面自己不定义实现：取到的每个函数/类都归属某个 `admission_fixtures_*` 兄弟；
4. 每个符号绑定到蓝图 §3 声明的归属兄弟里的**同一对象**（`is` 比对）；
5. 全部 pydantic 模型在新模块里仍然建完（`__pydantic_complete__`）——字段注解跨模块后必须
   在*定义模块*命名空间可解析，未建完的模型在这里当场断，而不是等首次校验才抛。
"""
from __future__ import annotations

import inspect
from importlib import import_module

import examcard.knowledge.contracts.admission_fixtures as facade

FAMILY_PREFIX = "examcard.knowledge.contracts.admission_fixtures_"
FACADE_MODULE = "examcard.knowledge.contracts.admission_fixtures"

# 拆前 admission_fixtures.py 的顶层符号表（AST 扫描，113 个）。
PRE_SPLIT_NAMES = (
    "AcceptanceReport",
    "CONSTRAINT_TOKEN_VERSION",
    "CONSTRAINT_VERSION",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "ConstraintContract",
    "ConstraintToken",
    "DECISION_VERSION",
    "DIAGRAM_VERSION",
    "DRAFT_CARD_VERSION",
    "DRAFT_VERSION",
    "DeterministicDraftCard",
    "DeterministicReleaseBundle",
    "DiagramContract",
    "DiagramEdge",
    "DiagramEdgeRef",
    "DiagramNode",
    "DiagramNodeRef",
    "DisplayElement",
    "DraftBundle",
    "EDGE_VERSION",
    "EXPECTED_CARD_COUNT",
    "EXPECTED_DISPLAY_TEXT_COUNT",
    "EXPECTED_FACT_COUNT",
    "EXPECTED_ORIGINAL_CARD_COUNT",
    "EXPECTED_REMEDIATION_CARD_COUNT",
    "EXPECTED_SEMANTIC_EDGE_COUNT",
    "ExtractionMode",
    "FORBIDDEN_TEXT",
    "FORMULA_TOKEN_VERSION",
    "FORMULA_VERSION",
    "FakeArtifact",
    "FormulaContract",
    "FormulaToken",
    "FrozenArtifact",
    "GateCheck",
    "GeometryConstraints",
    "HANDOFF_VERSION",
    "HASH",
    "HASH_PATTERN",
    "ID",
    "ID_PATTERN",
    "JUnitReceipt",
    "LIST_ITEM_VERSION",
    "LIST_VERSION",
    "LayoutContract",
    "ListItemRef",
    "NODE_VERSION",
    "NOISE_MARKERS",
    "OfflineGate",
    "OrderedListContract",
    "OrderedListItem",
    "PROVENANCE_VERSION",
    "PriorFreeze",
    "R103_FIXTURE_VERSION",
    "R104_ACCEPTANCE_VERSION",
    "R104_FREEZE_VERSION",
    "R104_GATE_CHECK_VERSION",
    "R104_GATE_VERSION",
    "R104_PHASE",
    "R10_FIXTURE_VERSION",
    "R2242_FIXTURE_VERSION",
    "R810_FIXTURE_VERSION",
    "RELEASE_VERSION",
    "REVIEWED_AT_UTC",
    "REVIEWED_CANDIDATE_VERSION",
    "REVIEWED_CARD_VERSION",
    "REVIEWER",
    "RemediationEvidenceBinding",
    "RemediationTableCell",
    "RemediationTableGrid",
    "ReviewedCandidate",
    "ReviewedStep3Card",
    "ReviewedStep3Fixture",
    "ReviewedStep3FixtureR103",
    "ReviewedTypedEvidenceFixture",
    "SOURCE_CANDIDATE",
    "SOURCE_PAGE_VERSION",
    "SOURCE_PHASE2241_FIXTURE",
    "SOURCE_PHASE224_FIXTURE",
    "SOURCE_R316_HANDOFF",
    "SOURCE_R316_HANDOFF_POSIX",
    "SOURCE_R316_RUN",
    "SourcePageBinding",
    "SourceProvenance",
    "Step3CardContract",
    "Step3HumanDecision",
    "Step3MergedReviewedReleaseCard",
    "Step3MergedSourceOwnedDisplayText",
    "Step3ReviewedSourceTier",
    "Step3SourceOwnedSemanticEdgeAudit",
    "Step3UnifiedReviewedReleaseFixture",
    "Step4PilotCardDispositionR319",
    "Step4PilotHandoffRefresh",
    "Step4ReviewedImageReferenceR319",
    "TABLE_CELL_VERSION",
    "TABLE_VERSION",
    "TYPED_TEXT_VERSION",
    "TableCell",
    "TableCellRef",
    "TableContract",
    "TokenRef",
    "TypedContract",
    "TypedElementProvenance",
    "TypedTextBase",
    "UNIFIED_FIXTURE",
    "UNIFIED_FIXTURE_POSIX",
    "_ANCHOR_ID_PATTERN",
    "_CARD",
    "_CARD_ID_PATTERN",
    "_FACT_ID_PATTERN",
    "_SHA",
    "_SHA256_PATTERN",
)

# 每个符号在蓝图 §3 里的归属兄弟（相对 admission_fixtures_ 前缀）。
SYMBOL_OWNERS = {
    "AcceptanceReport": "offline",
    "CONSTRAINT_TOKEN_VERSION": "constants",
    "CONSTRAINT_VERSION": "constants",
    "CONTRACT_ID": "constants",
    "CONTRACT_VERSION": "constants",
    "ConstraintContract": "typed",
    "ConstraintToken": "typed",
    "DECISION_VERSION": "constants",
    "DIAGRAM_VERSION": "constants",
    "DRAFT_CARD_VERSION": "constants",
    "DRAFT_VERSION": "constants",
    "DeterministicDraftCard": "release",
    "DeterministicReleaseBundle": "release",
    "DiagramContract": "typed",
    "DiagramEdge": "typed",
    "DiagramEdgeRef": "step3card",
    "DiagramNode": "typed",
    "DiagramNodeRef": "step3card",
    "DisplayElement": "step3card",
    "DraftBundle": "release",
    "EDGE_VERSION": "constants",
    "EXPECTED_CARD_COUNT": "constants",
    "EXPECTED_DISPLAY_TEXT_COUNT": "constants",
    "EXPECTED_FACT_COUNT": "constants",
    "EXPECTED_ORIGINAL_CARD_COUNT": "constants",
    "EXPECTED_REMEDIATION_CARD_COUNT": "constants",
    "EXPECTED_SEMANTIC_EDGE_COUNT": "constants",
    "ExtractionMode": "constants",
    "FORBIDDEN_TEXT": "constants",
    "FORMULA_TOKEN_VERSION": "constants",
    "FORMULA_VERSION": "constants",
    "FakeArtifact": "release",
    "FormulaContract": "typed",
    "FormulaToken": "typed",
    "FrozenArtifact": "offline",
    "GateCheck": "offline",
    "GeometryConstraints": "step3card",
    "HANDOFF_VERSION": "constants",
    "HASH": "constants",
    "HASH_PATTERN": "constants",
    "ID": "constants",
    "ID_PATTERN": "constants",
    "JUnitReceipt": "offline",
    "LIST_ITEM_VERSION": "constants",
    "LIST_VERSION": "constants",
    "LayoutContract": "step3card",
    "ListItemRef": "step3card",
    "NODE_VERSION": "constants",
    "NOISE_MARKERS": "constants",
    "OfflineGate": "offline",
    "OrderedListContract": "typed",
    "OrderedListItem": "typed",
    "PROVENANCE_VERSION": "constants",
    "PriorFreeze": "offline",
    "R103_FIXTURE_VERSION": "constants",
    "R104_ACCEPTANCE_VERSION": "constants",
    "R104_FREEZE_VERSION": "constants",
    "R104_GATE_CHECK_VERSION": "constants",
    "R104_GATE_VERSION": "constants",
    "R104_PHASE": "constants",
    "R10_FIXTURE_VERSION": "constants",
    "R2242_FIXTURE_VERSION": "constants",
    "R810_FIXTURE_VERSION": "constants",
    "RELEASE_VERSION": "constants",
    "REVIEWED_AT_UTC": "constants",
    "REVIEWED_CANDIDATE_VERSION": "constants",
    "REVIEWED_CARD_VERSION": "constants",
    "REVIEWER": "constants",
    "RemediationEvidenceBinding": "merged",
    "RemediationTableCell": "merged",
    "RemediationTableGrid": "merged",
    "ReviewedCandidate": "typed",
    "ReviewedStep3Card": "release",
    "ReviewedStep3Fixture": "release",
    "ReviewedStep3FixtureR103": "release",
    "ReviewedTypedEvidenceFixture": "typed",
    "SOURCE_CANDIDATE": "constants",
    "SOURCE_PAGE_VERSION": "constants",
    "SOURCE_PHASE2241_FIXTURE": "constants",
    "SOURCE_PHASE224_FIXTURE": "constants",
    "SOURCE_R316_HANDOFF": "constants",
    "SOURCE_R316_HANDOFF_POSIX": "constants",
    "SOURCE_R316_RUN": "constants",
    "SourcePageBinding": "typed",
    "SourceProvenance": "step3card",
    "Step3CardContract": "step3card",
    "Step3HumanDecision": "release",
    "Step3MergedReviewedReleaseCard": "merged",
    "Step3MergedSourceOwnedDisplayText": "merged",
    "Step3ReviewedSourceTier": "constants",
    "Step3SourceOwnedSemanticEdgeAudit": "merged",
    "Step3UnifiedReviewedReleaseFixture": "merged",
    "Step4PilotCardDispositionR319": "pilot",
    "Step4PilotHandoffRefresh": "pilot",
    "Step4ReviewedImageReferenceR319": "pilot",
    "TABLE_CELL_VERSION": "constants",
    "TABLE_VERSION": "constants",
    "TYPED_TEXT_VERSION": "constants",
    "TableCell": "typed",
    "TableCellRef": "step3card",
    "TableContract": "typed",
    "TokenRef": "step3card",
    "TypedContract": "typed",
    "TypedElementProvenance": "typed",
    "TypedTextBase": "typed",
    "UNIFIED_FIXTURE": "constants",
    "UNIFIED_FIXTURE_POSIX": "constants",
    "_ANCHOR_ID_PATTERN": "constants",
    "_CARD": "constants",
    "_CARD_ID_PATTERN": "constants",
    "_FACT_ID_PATTERN": "constants",
    "_SHA": "constants",
    "_SHA256_PATTERN": "constants",
}


def test_pre_split_symbol_table_is_113_names() -> None:
    assert len(PRE_SPLIT_NAMES) == 113
    assert len(set(PRE_SPLIT_NAMES)) == 113
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
    """门面只转口：每个函数/类都必须归属某个 admission_fixtures_* 兄弟。

    只看函数与类：版本常量、正则字面量这类值没有定义位置意义上的 __module__，据此判断会误伤。
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
    """113 个符号逐一用 `is` 比对声明归属兄弟里的同一对象。

    归属模块取不到时走哨兵，让 113 条全部走完、一次给齐 mismatch 清单。
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


def test_every_model_finished_building_in_its_new_module() -> None:
    """字段注解跨模块后仍必须可解析：未建完的模型在这里当场断。

    50 个 pydantic 模型的数量一并钉住（52 个类里 2 个是 StrEnum，不计）——少一个说明有模型被漏搬或降级。
    """
    models = []
    incomplete = []
    for name in facade.__all__:
        obj = getattr(facade, name)
        if not inspect.isclass(obj) or not hasattr(obj, "__pydantic_complete__"):
            continue
        models.append(name)
        if not obj.__pydantic_complete__:
            incomplete.append(f"{name} ({obj.__module__})")
    assert not incomplete, f"模型未建完，字段注解在新模块里解析不了: {incomplete}"
    assert len(models) == 50, f"pydantic 模型数量变了: {len(models)}"

