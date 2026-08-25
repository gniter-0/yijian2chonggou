"""摘要格式、离线中继基址与两个纯字符串助手（拆分层 0，不依赖任何兄弟）。

``_v25_id`` 把载荷的规范化 sha256 截前 24 位拼成业务 id：**截断长度参与既有黄金件比对**，
不许「顺手」调整。``_SHA256_PATTERN`` 是各模型自证字段的形状校验，全家共用一份。
"""
from __future__ import annotations

from . import canonical_step2_sha256


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OFFLINE_RELAY_BASE_URL = "https://offline.invalid/v25-generic-contract"


def _v25_id(prefix: str, payload: object) -> str:
    return f"{prefix}:{canonical_step2_sha256(payload)[:24]}"


def _label_from_source(value: str) -> str:
    compact = " ".join(value.split())
    if len(compact) < 2:
        return "source rule"
    return compact[:96].rstrip(" ,;:，；：")
