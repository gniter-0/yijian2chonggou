"""Stable ID and cache-key contract for V2 evidence artifacts."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from .schema import BBox, SourceLocator


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


def _digest(prefix: str, namespace: str, payload: Any) -> str:
    serialized = json.dumps(
        {"namespace": namespace, "payload": _canonical(payload)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(serialized).hexdigest()[:24]}"


def normalize_evidence_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return " ".join(normalized.split())


def _validate_sha256(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("pdf_sha256 must be 64 lowercase hexadecimal characters")
    return normalized


def make_document_id(pdf_sha256: str) -> str:
    return _digest("doc", "document-v2", {"pdf_sha256": _validate_sha256(pdf_sha256)})


def make_source_locator_id(locator: SourceLocator) -> str:
    return _digest("loc", "source-locator-v2", locator)


def make_evidence_fingerprint(
    *,
    kind: str,
    canonical_text: str,
    semantic_context: Mapping[str, Any] | None = None,
) -> str:
    return _digest(
        "evfp",
        "evidence-fingerprint-v2",
        {
            "kind": kind,
            "canonical_text": normalize_evidence_text(canonical_text),
            "semantic_context": semantic_context or {},
        },
    )


def make_evidence_revision_id(
    *,
    fingerprint: str,
    raw_text: str,
    source_channels: Sequence[str],
    engine_versions: Mapping[str, str],
    config_hash: str,
) -> str:
    return _digest(
        "evrev",
        "evidence-revision-v2",
        {
            "fingerprint": fingerprint,
            "raw_text": raw_text,
            "source_channels": sorted(set(source_channels)),
            "engine_versions": engine_versions,
            "config_hash": config_hash,
        },
    )


def make_evidence_id(*, locator_id: str, fingerprint: str) -> str:
    return _digest("ev", "evidence-instance-v2", {"locator_id": locator_id, "fingerprint": fingerprint})


def make_page_id(*, document_id: str, page_num: int) -> str:
    if page_num < 1:
        raise ValueError("page_num must be >= 1")
    return _digest("page", "page-evidence-v2", {"document_id": document_id, "page_num": page_num})


def make_manifest_id(
    *,
    document_id: str,
    material_profile_id: str,
    material_profile_revision: int,
    config_hash: str,
) -> str:
    return _digest(
        "manifest",
        "document-evidence-manifest-v2",
        {
            "document_id": document_id,
            "material_profile_id": material_profile_id,
            "material_profile_revision": material_profile_revision,
            "config_hash": config_hash,
        },
    )


def make_conflict_id(*, locator: SourceLocator, native_text: str, visual_text: str) -> str:
    return _digest(
        "conflict",
        "evidence-conflict-v2",
        {
            "locator": locator,
            "native_text": normalize_evidence_text(native_text),
            "visual_text": normalize_evidence_text(visual_text),
        },
    )


def make_cache_key(
    *,
    pdf_sha256: str,
    page_num: int,
    region_bbox: BBox | None,
    engine_name: str,
    engine_version: str,
    config_hash: str,
    artifact_schema_version: str = "2.0",
) -> str:
    if page_num < 1:
        raise ValueError("page_num must be >= 1")
    return _digest(
        "cache",
        "evidence-cache-v2",
        {
            "pdf_sha256": _validate_sha256(pdf_sha256),
            "page_num": page_num,
            "region_bbox": region_bbox,
            "engine_name": engine_name,
            "engine_version": engine_version,
            "config_hash": config_hash,
            "artifact_schema_version": artifact_schema_version,
        },
    )
