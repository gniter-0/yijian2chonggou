"""Stable IDs for facts and cards, independent from display ordering."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _digest(prefix: str, namespace: str, payload: Any) -> str:
    raw = json.dumps(
        {"namespace": namespace, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(raw).hexdigest()[:24]}"


def make_fact_id(
    *,
    fact_type: str,
    primary_evidence_fingerprints: Sequence[str],
    assertion_contract: Mapping[str, Any],
) -> str:
    if not primary_evidence_fingerprints:
        raise ValueError("fact IDs require primary evidence fingerprints")
    return _digest(
        "fact",
        "atomic-fact-v2",
        {
            "fact_type": fact_type,
            "primary_evidence_fingerprints": sorted(set(primary_evidence_fingerprints)),
            "assertion_contract": assertion_contract,
        },
    )


def make_card_raw_id(
    *,
    material_profile_id: str,
    content_policy_id: str,
    primary_fact_ids: Sequence[str],
    child_kp_ids: Sequence[str] = (),
    is_overview: bool = False,
) -> str:
    if not primary_fact_ids and not (is_overview and child_kp_ids):
        raise ValueError("card IDs require primary facts")
    return _digest(
        "kp",
        "learning-card-v2",
        {
            "material_profile_id": material_profile_id,
            "content_policy_id": content_policy_id,
            "primary_fact_ids": sorted(set(primary_fact_ids)),
            "child_kp_ids": sorted(set(child_kp_ids)),
            "is_overview": is_overview,
        },
    )
