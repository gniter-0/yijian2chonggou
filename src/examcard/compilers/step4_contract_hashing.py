"""r4 法定哈希三件套（拆分层 0）。

_sha256/_json_bytes/_canonical_hash 依铁律 bc-b8b9aca6 随模块逐字节原样，
载荷从不写 __module__/__qualname__/__file__，故换模块不触碰缓存键与金标。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from examcard.materials.schema import StrictModel


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    # r4 法定哈希实现，逐字节原样（铁律 bc-b8b9aca6）
    if isinstance(value, StrictModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_hash(value: Any) -> str:
    return _sha256(_json_bytes(value))
