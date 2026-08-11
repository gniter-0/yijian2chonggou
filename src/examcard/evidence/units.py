"""Evidence-unit construction centralizes IDs, lineage and immutable provenance."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from examcard.materials.schema import SemanticRole

from .identity import (
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_id,
    make_evidence_revision_id,
    make_source_locator_id,
)
from .schema import BBox, EvidenceKind, EvidenceProvenance, EvidenceUnit, SourceChannel, SourceLocator, SourceQuality


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bbox(value: tuple[float, float, float, float] | None) -> BBox | None:
    if value is None:
        return None
    x0, y0, x1, y1 = value
    if x1 <= x0:
        x1 = x0 + 0.1
    if y1 <= y0:
        y1 = y0 + 0.1
    return BBox(x0=max(0.0, x0), y0=max(0.0, y0), x1=max(0.1, x1), y1=max(0.1, y1))


def build_evidence_unit(
    *,
    document_sha256: str,
    page_num: int,
    kind: EvidenceKind,
    bbox: tuple[float, float, float, float] | None,
    raw_text: str,
    canonical_text: str | None,
    semantic_role: SemanticRole,
    source_quality: SourceQuality,
    confidence: float,
    order: int,
    source_channels: set[SourceChannel] | frozenset[SourceChannel],
    engine_versions: dict[str, str],
    config_hash: str,
    parent_id: str | None = None,
    risk_tags: set[str] | frozenset[str] = frozenset(),
    must_cover_by_source: bool = False,
    native_block_index: int | None = None,
    native_line_index: int | None = None,
    native_span_index: int | None = None,
    region_id: str | None = None,
    crop_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    generated_at_utc: str | None = None,
) -> EvidenceUnit:
    locator = SourceLocator(
        document_sha256=document_sha256,
        page_num=page_num,
        bbox=_bbox(bbox),
        native_block_index=native_block_index,
        native_line_index=native_line_index,
        native_span_index=native_span_index,
        region_id=region_id,
    )
    text = canonical_text if canonical_text is not None else raw_text
    fingerprint = make_evidence_fingerprint(
        kind=kind.value,
        canonical_text=text,
        semantic_context={"region_id": region_id or "", "parent_kind": kind.value if parent_id else ""},
    )
    locator_id = make_source_locator_id(locator)
    evidence_id = make_evidence_id(locator_id=locator_id, fingerprint=fingerprint)
    revision_id = make_evidence_revision_id(
        fingerprint=fingerprint,
        raw_text=raw_text,
        source_channels=sorted(channel.value for channel in source_channels),
        engine_versions=engine_versions,
        config_hash=config_hash,
    )
    return EvidenceUnit(
        evidence_id=evidence_id,
        fingerprint=fingerprint,
        revision_id=revision_id,
        document_id=make_document_id(document_sha256),
        kind=kind,
        locator=locator,
        raw_text=raw_text,
        canonical_text=text,
        semantic_role=semantic_role,
        source_quality=source_quality,
        confidence=max(0.0, min(1.0, confidence)),
        parent_id=parent_id,
        order=order,
        risk_tags=frozenset(risk_tags),
        must_cover_by_source=must_cover_by_source,
        provenance=EvidenceProvenance(
            crop_path=crop_path,
            source_channels=frozenset(source_channels),
            engine_versions=engine_versions,
            config_hash=config_hash,
            generated_at_utc=generated_at_utc or utc_now(),
        ),
        metadata=metadata or {},
    )
