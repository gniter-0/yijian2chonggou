"""五个 StrEnum 与来源族的呈现映射（拆分层 0，不依赖任何兄弟）。

枚举**取值即契约**：它们进请求载荷、也进黄金件，改字面量等于改字节权威。
``_SOURCE_FAMILY_PRESENTATION`` 只吃本模块的两个枚举，故与它们同住。
"""
from __future__ import annotations

from enum import StrEnum

from . import CardPresentation


class V25Step2AContractProfile(StrEnum):
    """Versioned V25 source-contract profiles; old runs remain immutable."""

    GENERIC_SOURCE_DERIVED = "v25_generic_source_derived_v1"
    ATOMIC_SOURCE_CONTRACT = "v25_atomic_source_contract_v2"
    DIAGRAM_CANONICAL_PRECHECK = "v25_diagram_canonical_precheck_v1"
    LIST_CANONICAL_PRECHECK = "v25_list_canonical_precheck_v1"
    STANDARD_CANONICAL_PRECHECK = "v25_standard_canonical_precheck_v1"
    COST_FLOW_CANONICAL_PRECHECK = "v25_cost_flow_canonical_precheck_v1"


class V25AtomicConditionOperator(StrEnum):
    """V25 atomic-only operators; legacy contracts remain unchanged."""

    STRICTLY_FORBIDDEN = "严禁"
    PERMITTED_ONLY_IF = "方可"


class V25FactReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class V25BGroupingKind(StrEnum):
    STANDALONE = "standalone"
    TABLE_COMPOSITION = "table_composition"
    CLASSIFICATION_FAMILY = "classification_family"
    CONSTRAINT_FAMILY = "constraint_family"
    ORDERED_FAMILY = "ordered_family"
    THRESHOLD_FAMILY = "threshold_family"


class V25BSourceFamilyKind(StrEnum):
    """Reviewed source relationship only; no Learning Golden Card data."""

    TABLE_CLASSIFICATION = "table_classification"
    RULE_FAMILY = "rule_family"
    TIMED_ENUMERATION = "timed_enumeration"
    FORMULA_FAMILY = "formula_family"


_SOURCE_FAMILY_PRESENTATION = {
    V25BSourceFamilyKind.TABLE_CLASSIFICATION: (V25BGroupingKind.CLASSIFICATION_FAMILY, CardPresentation.TABLE),
    V25BSourceFamilyKind.RULE_FAMILY: (V25BGroupingKind.CONSTRAINT_FAMILY, CardPresentation.CONSTRAINT),
    V25BSourceFamilyKind.TIMED_ENUMERATION: (V25BGroupingKind.ORDERED_FAMILY, CardPresentation.ORDERED_LIST),
    V25BSourceFamilyKind.FORMULA_FAMILY: (V25BGroupingKind.STANDALONE, CardPresentation.FORMULA),
}
