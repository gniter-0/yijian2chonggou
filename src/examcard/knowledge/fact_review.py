"""人工评审冲突与覆盖组的解读（拆分层 1）。"""
from __future__ import annotations

from typing import Iterable

from ..evidence.annotations import ConflictClassification, GoldenReviewSidecar
from ..evidence.schema import BBox, EvidenceUnit, PageEvidence, SourceQuality
from ..materials.schema import PolicyDisposition

from .fact_types import EvidenceContext, ReviewedConflict
from .fact_support import _bbox_overlaps


def _conflict_evidence_ids(*, pages: tuple[PageEvidence, ...], page_num: int, bbox: BBox | None) -> tuple[str, ...]:
    page = next((item for item in pages if item.page_num == page_num), None)
    if page is None:
        raise ValueError(f"reviewed conflict page is missing from Evidence: {page_num}")
    return tuple(
        sorted(
            unit.evidence_id
            for unit in page.evidence_units
            if unit.source_quality == SourceQuality.CONFLICT
            and _bbox_overlaps(unit.locator.bbox, bbox)
        )
    )


def _reviewed_conflicts(
    *, sidecar: GoldenReviewSidecar, sample_id: str, pages: tuple[PageEvidence, ...]
) -> tuple[ReviewedConflict, ...]:
    sample = next((item for item in sidecar.samples if item.sample_id == sample_id), None)
    if sample is None:
        raise ValueError(f"review sidecar is missing {sample_id}")
    conflicts: list[ReviewedConflict] = []
    for page in sample.pages:
        for conflict in page.conflicts:
            if conflict.classification == ConflictClassification.MANUAL_REVIEW or conflict.requires_human_confirmation:
                raise ValueError(f"{sample_id}: unresolved review conflict {conflict.conflict_id}")
            if conflict.proposed_canonical_text is None:
                raise ValueError(f"{sample_id}: reviewed conflict lacks canonical text {conflict.conflict_id}")
            evidence_ids = _conflict_evidence_ids(
                pages=pages,
                page_num=page.page_num,
                bbox=conflict.locator.bbox,
            )
            if not evidence_ids:
                raise ValueError(f"{sample_id}: reviewed conflict has no exact conflict Evidence anchor {conflict.conflict_id}")
            conflicts.append(
                ReviewedConflict(
                    conflict_id=conflict.conflict_id,
                    page_num=page.page_num,
                    bbox=conflict.locator.bbox,
                    evidence_ids=evidence_ids,
                    native_text=conflict.native_text,
                    canonical_text=conflict.proposed_canonical_text,
                )
            )
    return tuple(conflicts)


def _conflict_is_resolved(unit: EvidenceUnit, conflicts: Iterable[ReviewedConflict]) -> bool:
    return any(unit.evidence_id in conflict.evidence_ids for conflict in conflicts)


def _reviewed_override_groups(
    *, contexts: tuple[EvidenceContext, ...], conflicts: tuple[ReviewedConflict, ...]
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """Apply sidecar corrections as fact text without ever mutating evidence."""

    groups: list[tuple[tuple[str, ...], str]] = []
    already_used: set[str] = set()
    for conflict in conflicts:
        if conflict.native_text == conflict.canonical_text:
            continue
        matches = [
            context.unit
            for context in contexts
            if context.decision.disposition == PolicyDisposition.INCLUDED
            and context.unit.evidence_id in conflict.evidence_ids
        ]
        matches.sort(key=lambda item: item.order)
        if not matches:
            raise ValueError(f"reviewed conflict cannot be anchored to included evidence: {conflict.conflict_id}")
        joined = "".join(item.canonical_text for item in matches)
        if conflict.native_text not in joined:
            raise ValueError(f"reviewed conflict cannot replace its native text in evidence: {conflict.conflict_id}")
        ids = tuple(item.evidence_id for item in matches)
        if already_used & set(ids):
            raise ValueError(f"overlapping reviewed corrections require explicit adjudication: {conflict.conflict_id}")
        already_used.update(ids)
        groups.append((ids, joined.replace(conflict.native_text, conflict.canonical_text, 1)))
    return tuple(groups)
