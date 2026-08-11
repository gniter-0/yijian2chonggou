"""common/schema：StrictModel 配置与 strict_schema 归一化语义（r91 定式）。"""
from __future__ import annotations

from typing import Literal, Optional

import pytest
from pydantic import Field, ValidationError

from examcard.common.schema import StrictModel, strict_schema


class Inner(StrictModel):
    name: str


class Sample(StrictModel):
    schema_version: Literal["sample_v1"] = "sample_v1"
    required_text: str
    optional_text: str = "fallback"
    nullable_text: Optional[str] = None
    inner: Inner
    count: int = Field(default=0, ge=0)


def test_strict_model_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Inner.model_validate({"name": "ok", "unknown": True})


def test_strict_model_is_frozen() -> None:
    instance = Inner(name="ok")
    with pytest.raises(ValidationError):
        instance.name = "changed"  # type: ignore[misc]


def test_strict_model_strips_whitespace() -> None:
    assert Inner(name="  ok  ").name == "ok"


def test_strict_schema_marks_every_property_required() -> None:
    schema = strict_schema(Sample)
    assert set(schema["required"]) == set(schema["properties"])


def test_strict_schema_closes_objects_recursively() -> None:
    schema = strict_schema(Sample)
    assert schema["additionalProperties"] is False
    inner = schema["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert inner["required"] == ["name"]


def test_strict_schema_wraps_optional_non_nullable_with_null() -> None:
    schema = strict_schema(Sample)
    wrapped = schema["properties"]["optional_text"]
    assert {"type": "null"} in wrapped["anyOf"]


def test_strict_schema_keeps_nullable_property_unwrapped() -> None:
    schema = strict_schema(Sample)
    variants = schema["properties"]["nullable_text"]["anyOf"]
    assert sum(1 for item in variants if item.get("type") == "null") == 1


def test_strict_schema_keeps_const_property_untouched() -> None:
    schema = strict_schema(Sample)
    version = schema["properties"]["schema_version"]
    assert version.get("const") == "sample_v1"
    assert "anyOf" not in version


def test_strict_schema_strips_defaults() -> None:
    schema = strict_schema(Sample)
    assert "default" not in str(schema)
