"""Step 2 stage/presentation/failure enums, condition operators, decoder config."""
from __future__ import annotations
from enum import StrEnum

from pydantic import Field

from ...materials.schema import StrictModel


class Step2Stage(StrEnum):
    FACT_PLANNING = "step2a"
    CARD_PLANNING = "step2b"


class CardPresentation(StrEnum):
    TEXT = "text"
    ORDERED_LIST = "ordered_list"
    TABLE = "table"
    DIAGRAM = "diagram"
    FORMULA = "formula"
    CONSTRAINT = "constraint"
    THRESHOLD = "threshold"
    VISUAL_REFERENCE = "visual_reference"


class NumericTokenClass(StrEnum):
    QUANTITATIVE_VALUE = "quantitative_value"
    SECTION_NUMBER = "section_number"
    LIST_INDEX = "list_index"
    MATERIAL_GRADE = "material_grade"
    IDENTIFIER = "identifier"


class ConditionOperator(StrEnum):
    FORBIDDEN = "不得"
    REQUIRED = "必须"
    SHOULD_NOT = "不应"
    MINIMUM = "不小于"
    MAXIMUM = "不大于"
    PERMITTED_ONLY_IF = "方可"
    AT_LEAST_RANGE = "及以上"
    BELOW_RANGE = "以下"
    WITHIN_RANGE = "以内"
    NOT_EXCEEDING = "不超过"
    AT_LEAST = "至少"
    LESS_OR_EQUAL = "≤"
    GREATER_OR_EQUAL = "≥"
    LESS_THAN = "<"
    GREATER_THAN = ">"


def _condition_operator_occurrences(text: str) -> tuple[tuple[int, int, ConditionOperator], ...]:
    """Return non-overlapping condition-operator offsets in source order."""

    located: list[tuple[int, int, ConditionOperator]] = []
    occupied: list[tuple[int, int]] = []
    for operator in sorted(ConditionOperator, key=lambda item: len(item.value), reverse=True):
        start = text.find(operator.value)
        while start >= 0:
            end = start + len(operator.value)
            if not any(start < observed_end and end > observed_start for observed_start, observed_end in occupied):
                occupied.append((start, end))
                located.append((start, end, operator))
            start = text.find(operator.value, start + 1)
    return tuple(sorted(located, key=lambda item: (item[0], item[1])))


def condition_operators_in_text(text: str) -> tuple[ConditionOperator, ...]:
    """Return non-overlapping condition operators in source order."""

    return tuple(item[2] for item in _condition_operator_occurrences(text))


class Step2ResponseFormat(StrEnum):
    JSON_SCHEMA_STRICT = "json_schema_strict"
    JSON_OBJECT = "json_object"


class Step2DecoderConfig(StrictModel):
    """All output-affecting generation controls must participate in cache identity."""

    temperature: float = Field(default=0, ge=0, le=2)
    max_tokens: int = Field(default=1400, ge=64, le=4000)
    response_format: Step2ResponseFormat = Step2ResponseFormat.JSON_SCHEMA_STRICT


def default_step2_decoder(stage: Step2Stage) -> Step2DecoderConfig:
    return Step2DecoderConfig(max_tokens=1400 if stage == Step2Stage.FACT_PLANNING else 1000)


class Step2FailureState(StrEnum):
    VALIDATION_FAILED = "validation_failed"
    SOURCE_MISSING = "source_missing"
    GROUNDING_FAILED = "grounding_failed"
    INVALID_RESPONSE = "invalid_response"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UNKNOWN_OUTCOME = "unknown_outcome"


class Step2ResultState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
