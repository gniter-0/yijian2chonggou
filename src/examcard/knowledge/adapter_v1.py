"""Read-only adapter from a V2 Fact/Card run to the legacy KP review shape."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .schema import (
    AtomicFact,
    ChapterQualityGate,
    CoverageDisposition,
    CoverageMap,
    KnowledgeCard,
)


ADAPTER_VERSION = "v2-to-v1-review-v1"


class V2AdapterError(ValueError):
    """Raised when a V2 run cannot be represented without losing lineage."""


def discover_fact_runs(root: Path) -> tuple[Path, ...]:
    """Return only direct ``<sample>/fact_run.json`` children of a V2 run root."""

    root = Path(root)
    if not root.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(root.glob("*/fact_run.json"))
        if path.parent.parent == root and not path.parent.name.startswith("_")
    )


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V2AdapterError(f"cannot read V2 fact run {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise V2AdapterError(f"V2 fact run must be a JSON object: {path}")
    return payload


def _anchor_from_ref(ref: Any) -> dict[str, Any]:
    anchor: dict[str, Any] = {
        "anchor_type": "v2_evidence_ref",
        "evidence_id": ref.evidence_id,
        "page": ref.page_num,
        "visual_only": ref.visual_only,
    }
    if ref.bbox is not None:
        anchor["bbox"] = ref.bbox.model_dump(mode="json")
    return anchor


def adapt_fact_run(
    payload: dict[str, Any],
    *,
    chapter_title: str | None = None,
    source_file: str | None = None,
) -> dict[str, Any]:
    """Validate and project one V2 run without mutating or writing its source.

    The result resembles a V1 structured chapter for existing read-only review
    components. V2 lineage remains under ``_v2`` and ``content_anchors``. Text
    ``start/end`` anchors are never invented for evidence-ID grounding.
    """

    sample_id = str(payload.get("sample_id", "") or "").strip()
    if not sample_id:
        raise V2AdapterError("V2 fact run is missing sample_id")

    gate_payload = dict(payload.get("quality_gate", {}) or {})
    gate_payload.pop("blockers", None)
    gate_payload.pop("publishable", None)
    try:
        facts = tuple(AtomicFact.model_validate(item) for item in payload.get("facts", ()))
        cards = tuple(KnowledgeCard.model_validate(item) for item in payload.get("cards", ()))
        coverage = CoverageMap.model_validate(payload.get("coverage", {}))
        gate = ChapterQualityGate.model_validate(gate_payload)
    except (TypeError, ValueError) as exc:
        raise V2AdapterError(f"{sample_id}: invalid V2 contract: {exc}") from exc

    if not cards:
        raise V2AdapterError(f"{sample_id}: V2 fact run has no cards")
    fact_by_id = {fact.fact_id: fact for fact in facts}
    if len(fact_by_id) != len(facts):
        raise V2AdapterError(f"{sample_id}: duplicate fact IDs")
    card_by_raw_id = {card.raw_id: card for card in cards}
    if len(card_by_raw_id) != len(cards):
        raise V2AdapterError(f"{sample_id}: duplicate card raw IDs")

    coverage_by_evidence = {record.evidence_id: record for record in coverage.records}
    unknown_owners = {
        record.coverage_owner
        for record in coverage.records
        if record.coverage_owner and record.coverage_owner not in card_by_raw_id
    }
    if unknown_owners:
        raise V2AdapterError(f"{sample_id}: coverage references unknown card owners {sorted(unknown_owners)}")

    knowledge_points: list[dict[str, Any]] = []
    for index, card in enumerate(cards, start=1):
        segment_fact_ids = tuple(
            dict.fromkeys(fact_id for segment in card.content_segments for fact_id in segment.fact_ids)
        )
        missing_facts = [fact_id for fact_id in segment_fact_ids if fact_id not in fact_by_id]
        if missing_facts:
            raise V2AdapterError(f"{sample_id}: card {card.raw_id} references unknown facts {missing_facts}")

        expected_evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for fact_id in segment_fact_ids
                for evidence_id in fact_by_id[fact_id].primary_evidence_ids
            )
        )
        ref_ids = {ref.evidence_id for ref in card.evidence_refs}
        missing_refs = [evidence_id for evidence_id in expected_evidence_ids if evidence_id not in ref_ids]
        missing_coverage = [evidence_id for evidence_id in ref_ids if evidence_id not in coverage_by_evidence]
        if missing_refs or missing_coverage:
            raise V2AdapterError(
                f"{sample_id}: card {card.raw_id} loses lineage; "
                f"missing_refs={missing_refs}, missing_coverage={missing_coverage}"
            )

        records = [coverage_by_evidence[ref.evidence_id] for ref in card.evidence_refs]
        owner_mismatches = [
            evidence_id
            for evidence_id in expected_evidence_ids
            for record in (coverage_by_evidence[evidence_id],)
            if record.disposition != CoverageDisposition.PRIMARY_ASSIGNED
            or record.coverage_owner != card.raw_id
        ]
        dispositions = Counter(record.disposition.value for record in records)
        coverage_complete = not owner_mismatches and set(expected_evidence_ids) <= ref_ids
        anchors = [_anchor_from_ref(ref) for ref in card.evidence_refs]

        knowledge_points.append(
            {
                "id": f"{index:03d}",
                "raw_id": card.raw_id,
                "title": card.title,
                "content": card.content,
                "content_anchors": anchors,
                "source_pages": list(card.source_pages),
                "key_words": list(card.key_words),
                "diagram_type": card.diagram_type,
                "diagram_elements": list(card.diagram_elements),
                "annotations": list(card.annotations),
                "kp_type": card.kp_type,
                "is_overview": card.is_overview,
                "_anchor_status": f"v2:{card.grounding_status.value}",
                "_v2": {
                    "schema_version": card.schema_version,
                    "card_id": card.id,
                    "grounding_status": card.grounding_status.value,
                    "renderability": card.renderability.value,
                    "quality_flags": [flag.value for flag in card.quality_flags],
                    "diagram_review_id": card.diagram_review_id,
                    "diagram_disposition_id": card.diagram_disposition_id,
                    "diagram_disposition": (
                        card.diagram_disposition_trace.model_dump(mode="json")
                        if card.diagram_disposition_trace is not None
                        else None
                    ),
                    "fact_ids": list(segment_fact_ids),
                    "evidence_ids": [ref.evidence_id for ref in card.evidence_refs],
                    "visual_only_evidence_ids": [
                        ref.evidence_id for ref in card.evidence_refs if ref.visual_only
                    ],
                    "coverage": {
                        "complete": coverage_complete,
                        "expected_primary": len(expected_evidence_ids),
                        "primary_assigned": dispositions[CoverageDisposition.PRIMARY_ASSIGNED.value],
                        "owner_mismatch_evidence_ids": owner_mismatches,
                        "dispositions": dict(dispositions),
                    },
                },
            }
        )

    coverage_summary = Counter(record.disposition.value for record in coverage.records)
    return {
        "source_file": source_file or f"{sample_id}/fact_run.json",
        "chapter": chapter_title or sample_id,
        "total_points": len(knowledge_points),
        "knowledge_points": knowledge_points,
        "_v2_adapter": {
            "adapter_version": ADAPTER_VERSION,
            "read_only": True,
            "sample_id": sample_id,
            "pdf_sha256": payload.get("pdf_sha256", ""),
            "selected_pages": list(payload.get("selected_pages", [])),
            "material_profile_id": coverage.material_profile_id,
            "content_policy_id": coverage.content_policy_id,
            "content_policy_revision": coverage.content_policy_revision,
            "quality_gate": {
                **gate.model_dump(mode="json"),
                "blockers": list(gate.blockers),
                "publishable": gate.publishable,
            },
            "coverage_summary": dict(coverage_summary),
        },
    }


def load_fact_run_as_v1(path: Path, *, chapter_title: str | None = None) -> dict[str, Any]:
    """Read and adapt one V2 run; no source file is ever written."""

    path = Path(path)
    return adapt_fact_run(
        _read_object(path),
        chapter_title=chapter_title,
        source_file=f"{path.parent.name}/{path.name}",
    )
