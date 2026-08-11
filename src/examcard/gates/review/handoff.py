"""pilot handoff 判定：禁带字段校验（规范源 r313/r316/r319 refresh 形态，初代 r38）。

handoff manifest 数据模型（Step4PilotHandoffRefresh 等，类名已按 gates-review
终裁去 R319 后缀）按 A 案在 knowledge/contracts/admission_fixtures；本模块承载
「handoff 不得夹带上游评审语义字段」的 fail-closed 判定。EXPECTED_* 卡片期望
常量按提炼规则留旧仓。
"""
from __future__ import annotations

from typing import Any, Iterator, Mapping

from pydantic import BaseModel

from ...knowledge.contracts.admission_fixtures import (
    Step4PilotCardDispositionR319,
    Step4PilotHandoffRefresh,
    Step4ReviewedImageReferenceR319,
)

__all__ = (
    "FORBIDDEN_HANDOFF_FIELDS",
    "Step4PilotHandoffRefresh",
    "Step4PilotCardDispositionR319",
    "Step4ReviewedImageReferenceR319",
    "forbidden_handoff_fields_absent",
    "validate_handoff_fields",
)

FORBIDDEN_HANDOFF_FIELDS = frozenset(
    {
        "golden_title",
        "canonical_title",
        "review_state",
        "review_basis",
        "expected_texts",
        "image_prompt",
        "layout_description",
        "unknown_field",
        "watermark",
        "evidence_only",
    }
)


def _keys(item: Any) -> Iterator[str]:
    if isinstance(item, dict):
        yield from item
        for child in item.values():
            yield from _keys(child)
    elif isinstance(item, list):
        for child in item:
            yield from _keys(child)


def forbidden_handoff_fields_absent(manifest: BaseModel | Mapping[str, Any]) -> bool:
    """r319 `_forbidden_handoff_fields` 定式：递归键扫描（大小写折叠），命中即失败。"""
    value = manifest.model_dump(mode="json") if isinstance(manifest, BaseModel) else dict(manifest)
    return not {key for key in _keys(value) if key.casefold() in FORBIDDEN_HANDOFF_FIELDS}


def validate_handoff_fields(manifest: BaseModel | Mapping[str, Any]) -> None:
    if not forbidden_handoff_fields_absent(manifest):
        raise ValueError("pilot handoff manifest carries forbidden review-semantic fields")
