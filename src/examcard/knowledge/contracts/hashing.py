"""Legal legacy Step 2 canonical hash / fingerprint implementations.

Frozen byte-for-byte per iron rule bc-b8b9aca6 and blueprint R7: never fold
into examcard.common.hashing and never re-normalize; one byte of drift
invalidates every persisted request_id / cache_key / golden fixture.
"""
from __future__ import annotations
import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import Any


_OFFLINE_RELAY_BASE_URL = "offline://deterministic-fake"


def _canonicalize(value: Any) -> Any:
    """Normalize unordered contract values before they participate in an ID."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        normalized = [_canonicalize(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return value


def canonical_step2_sha256(value: Any) -> str:
    raw = json.dumps(_canonicalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return canonical_step2_sha256(value)


def fingerprint_step2_relay(relay_base_url: str) -> str:
    """Return the canonical relay identity used by every Step 2 cache key."""
    return _canonical_sha256(relay_base_url.strip().rstrip("/"))


def fingerprint_step2_prompt(prompt_template: str) -> str:
    """Return the canonical prompt identity used by every Step 2 cache key."""
    return _canonical_sha256(prompt_template)
