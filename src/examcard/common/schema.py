"""严格模型基座与 strict_schema(model) 严格模式 JSON Schema 导出。

`strict_schema` 语义取自旧仓 Step3 全链沿用的 phase2r91 归一化器（r92/r93/r98/r10
等十余个 phase 转发的唯一根实现）：递归地把每个对象节点收紧为
「全部属性必填 + additionalProperties=False + 默认值剥离」，未必填且不可空的属性
包一层 anyOf-null，携带 const 的属性保持原样。

`StrictModel`（本模块）只供 common 六件自身的冻结原语模型继承：common 是叶包，
不得 import examcard.materials；四桶业务契约模型仍按 providers 稿 §6.2 继承
`examcard.materials.schema.StrictModel`。两处 model_config 必须保持一致
（extra="forbid", frozen=True, str_strip_whitespace=True）。

签名落地即冻（终裁广播 bc-9d82a196）；变更须报总指挥新终裁。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _allows_null(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "null":
        return True
    if isinstance(schema.get("type"), list) and "null" in schema["type"]:
        return True
    return any(isinstance(item, dict) and item.get("type") == "null" for item in schema.get("anyOf", ()))


def _normalize_strict_node(value: object) -> object:
    if isinstance(value, list):
        return [_normalize_strict_node(item) for item in value]
    if not isinstance(value, dict):
        return value
    original_required = set(value.get("required", ()))
    normalized = {
        key: _normalize_strict_node(item)
        for key, item in value.items()
        if key not in {"required", "additionalProperties", "default"}
    }
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        for name, property_schema in tuple(properties.items()):
            if name in original_required or _allows_null(property_schema) or (
                isinstance(property_schema, dict) and "const" in property_schema
            ):
                continue
            properties[name] = {"anyOf": [property_schema, {"type": "null"}]}
        normalized["required"] = list(properties)
        normalized["additionalProperties"] = False
    return normalized


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = _normalize_strict_node(model.model_json_schema())
    if not isinstance(schema, dict):
        raise TypeError("strict schema root must be an object")
    return schema
