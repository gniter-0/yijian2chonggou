"""Typed-contract binding gates for the v2 admission authority path."""
from __future__ import annotations

from typing import Any

from ...knowledge.contracts.admission_fixtures_typed import (
    ConstraintContract,
    DiagramContract,
    DiagramEdge,
    FormulaContract,
    OrderedListContract,
    TableContract,
    TypedElementProvenance,
)
from ...knowledge.contracts.admission_v2 import ThresholdContract
from .queue_v2 import QueueItemLikeV2


def contract_elements_v2(contract: object) -> tuple[object, ...]:
    """Return source-bearing elements for v1 contracts plus threshold tokens."""

    if isinstance(contract, DiagramContract):
        return tuple(contract.nodes) + tuple(contract.edges)
    if isinstance(contract, TableContract):
        return tuple(contract.cells)
    if isinstance(contract, OrderedListContract):
        return tuple(contract.items)
    if isinstance(contract, (ConstraintContract, ThresholdContract, FormulaContract)):
        return tuple(contract.tokens)
    raise TypeError(f"unsupported typed contract: {type(contract)!r}")


def validate_typed_contract_binding_v2(decision: Any, queue_item: QueueItemLikeV2) -> None:
    """Validate exact source/provenance binding for the v2 authority path."""

    contract = decision.typed_contract
    if contract is None:
        raise ValueError("typed contract is missing")
    if contract.presentation != decision.presentation:
        raise ValueError("typed contract presentation mismatch")
    source_pages = {page.page_ref_id: page for page in decision.pages}
    if not source_pages:
        raise ValueError("typed contract has no source page")
    for element in contract_elements_v2(contract):
        provenance = getattr(element, "provenance", None)
        if not isinstance(provenance, TypedElementProvenance):
            raise ValueError("typed element provenance is missing")
        if (
            provenance.subject_id != decision.subject_id
            or provenance.record_id != decision.record_id
            or provenance.fact_id != decision.fact_id
            or provenance.evidence_id != decision.evidence_id
        ):
            raise ValueError("typed element crossed Fact/Evidence binding")
        if provenance.anchor_ids != decision.anchor_ids or provenance.source_manifest_sha256 != decision.source_manifest_sha256:
            raise ValueError("typed element crossed anchor or source manifest")
        page = source_pages.get(provenance.page_ref_id)
        if page is None or page.page != provenance.page or page.image_sha256 != provenance.image_sha256:
            raise ValueError("typed element crossed source page or image hash")
        if isinstance(element, DiagramEdge):
            if not element.layout_only:
                raise ValueError("diagram edge must remain layout-only")
        elif (
            not getattr(element, "human_confirmed", True)
            or getattr(element, "ocr_derived", True)
            or getattr(element, "assertion_derived", True)
            or getattr(element, "filename_derived", True)
            or getattr(element, "legacy_hint_derived", True)
            or getattr(element, "watermark", True)
        ):
            raise ValueError("typed element has an unauthorized derivation or noise flag")
    if (
        queue_item.presentation != decision.presentation
        or queue_item.subject_id != decision.subject_id
        or queue_item.record_id != decision.record_id
        or queue_item.fact_id != decision.fact_id
        or queue_item.evidence_id != decision.evidence_id
    ):
        raise ValueError("decision crossed queue presentation or Fact/Evidence")
    if not queue_item.anchor_ids or tuple(queue_item.anchor_ids) != tuple(decision.anchor_ids):
        raise ValueError("decision crossed queue anchor binding")


__all__ = ("contract_elements_v2", "validate_typed_contract_binding_v2")
