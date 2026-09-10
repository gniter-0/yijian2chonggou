"""Deterministic Step 2B to Admission V2 projection.

The live model response is not an authority source.  This module only
projects a semantically validated, source-bound Step 2B response into the
frozen V2 admission queue and typed-contract drafts.  Anything that cannot be
represented without choosing, parsing, or inventing source information is
rejected as a whole batch.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...evidence.schema import EvidenceKind, PageEvidence
from ...knowledge.contracts.table_inputs import Step2ASectionInput
from ...knowledge.contracts.v25_generic_contracts import (
    V25BModelCardProposal,
    V25BStep2BRequest,
    V25BStep2BResultPayload,
    V25Step2BFactSummary,
)
from ...knowledge.contracts.admission_fixtures_typed import (
    ConstraintContract,
    ConstraintToken,
    FormulaContract,
    FormulaToken,
    OrderedListContract,
    OrderedListItem,
    SourcePageBinding,
    TableCell,
    TableContract,
    TypedElementProvenance,
)
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x, text_hash
from ...knowledge.contracts.admission_v2 import ThresholdContract, ThresholdToken
from .queue_v2 import AdmissionQueueItemV2


class AdmissionProjectionError(ValueError):
    """A Step 2B batch is not losslessly representable by Admission V2."""


@dataclass(frozen=True)
class AdmissionProjectionV2:
    """The immutable projection produced before human authority is supplied."""

    request_id: str
    sample_id: str
    queue_items: tuple[AdmissionQueueItemV2, ...]
    contract_drafts_by_candidate: Mapping[str, Mapping[str, Any]]

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.queue_items)


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def make_source_page_binding(
    *,
    page_ref_id: str,
    page: int,
    source_file: str,
    image_path: str,
    image_bytes: bytes,
    page_bytes: bytes,
    image_width: int,
    image_height: int,
    source_manifest_sha256: str,
) -> SourcePageBinding:
    """Seal one rendered source page from caller-supplied immutable bytes."""

    payload = {
        "page_ref_id": page_ref_id,
        "page": page,
        "source_file": source_file,
        "image_path": image_path,
        "image_sha256": _digest_bytes(image_bytes),
        "image_width": image_width,
        "image_height": image_height,
        "page_sha256": _digest_bytes(page_bytes),
        "source_manifest_sha256": source_manifest_sha256,
    }
    return hash_model_r8x(SourcePageBinding, payload, "page_binding_sha256")


def _page_map(
    page_bindings: Sequence[SourcePageBinding] | Mapping[int, SourcePageBinding]
) -> dict[tuple[str, int], SourcePageBinding]:
    """Index pages by their immutable source manifest and page number.

    A single sample may intentionally include adjacent pages from different
    source manifests.  Indexing by page number alone would make that valid
    situation look like a duplicate or, worse, silently bind an anchor to the
    wrong source document.
    """

    values = tuple(page_bindings.values()) if isinstance(page_bindings, Mapping) else tuple(page_bindings)
    result: dict[tuple[str, int], SourcePageBinding] = {}
    for value in values:
        if not isinstance(value, SourcePageBinding):
            raise AdmissionProjectionError("source page bindings must be V2 SourcePageBinding objects")
        key = (value.source_manifest_sha256, value.page)
        if key in result:
            raise AdmissionProjectionError(
                f"duplicate source page binding: {value.source_manifest_sha256}:{value.page}"
            )
        result[key] = value
    return result


def _section_inputs(
    request: V25BStep2BRequest, sections: Sequence[Step2ASectionInput] | None
) -> tuple[Step2ASectionInput, ...]:
    if sections is not None:
        values = tuple(sections)
        if values:
            return values
        raise AdmissionProjectionError("Step 2B source sections are empty")
    source = getattr(request, "source_step2a_request", None)
    sections = getattr(source, "sections", None)
    if sections:
        return tuple(sections)
    sections = getattr(request, "sections", None)
    if sections:
        return tuple(sections)
    raise AdmissionProjectionError("Step 2B request does not retain authoritative Step 2A sections")


def _source_indexes(
    request: V25BStep2BRequest, sections: Sequence[Step2ASectionInput] | None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    spans: dict[str, Any] = {}
    cells: dict[str, Any] = {}
    tables: dict[str, Any] = {}
    for section in _section_inputs(request, sections):
        for item in section.text_evidence:
            spans.update({span.anchor_span_id: span for span in item.anchor_spans})
        for table in section.tables:
            tables[table.table_evidence_id] = table
            for row in table.rows:
                for cell in row.cells:
                    cells[cell.evidence_id] = cell
                    spans.update({span.anchor_span_id: span for span in cell.anchor_spans})
    if not spans:
        raise AdmissionProjectionError("Step 2B request has no selectable authoritative source anchors")
    return spans, cells, tables


def _evidence_page_indexes(
    request: V25BStep2BRequest, pages: Sequence[PageEvidence]
) -> tuple[dict[str, int], dict[str, Any]]:
    evidence_pages: dict[str, int] = {}
    evidence_units: dict[str, Any] = {}
    for page in pages:
        for unit in page.evidence_units:
            if unit.evidence_id in evidence_pages and evidence_pages[unit.evidence_id] != page.page_num:
                raise AdmissionProjectionError(f"Evidence appears on multiple source pages: {unit.evidence_id}")
            evidence_pages[unit.evidence_id] = page.page_num
            evidence_units[unit.evidence_id] = unit
    return evidence_pages, evidence_units


def _source_page_for_anchor(
    anchor: Any,
    *,
    evidence_pages: Mapping[str, int],
    page_bindings: Mapping[tuple[str, int], SourcePageBinding],
) -> SourcePageBinding:
    page_num = evidence_pages.get(anchor.parent_evidence_id)
    if page_num is None:
        raise AdmissionProjectionError(f"unknown source Evidence for anchor: {anchor.anchor_span_id}")
    page = page_bindings.get((anchor.source_manifest_sha256, page_num))
    if page is None:
        raise AdmissionProjectionError(f"missing rendered source page binding: page {page_num}")
    if page.source_manifest_sha256 != anchor.source_manifest_sha256:
        raise AdmissionProjectionError("source page and anchor crossed source manifest")
    return page


def _provenance(
    *,
    subject_id: str,
    record_id: str,
    fact_id: str,
    evidence_id: str,
    anchor_ids: Sequence[str],
    page: SourcePageBinding,
) -> TypedElementProvenance:
    return hash_model_r8x(
        TypedElementProvenance,
        {
            "subject_id": subject_id,
            "record_id": record_id,
            "fact_id": fact_id,
            "evidence_id": evidence_id,
            "anchor_ids": tuple(anchor_ids),
            "page_ref_id": page.page_ref_id,
            "page": page.page,
            "image_sha256": page.image_sha256,
            "source_manifest_sha256": page.source_manifest_sha256,
        },
        "provenance_sha256",
    )


def _typed_text(
    model_type: type[Any],
    *,
    element_id: str,
    text: str,
    provenance: TypedElementProvenance,
    order_field: str,
    order: int,
) -> Any:
    return hash_model_r8x(
        model_type,
        {
            "element_id": element_id,
            "text": text,
            "provenance": provenance,
            "text_sha256": text_hash(text),
            order_field: order,
        },
        "element_sha256",
    )


def _contract_for_token_family(
    presentation: str,
    *,
    fact: V25Step2BFactSummary,
    anchors: tuple[Any, ...],
    pages: tuple[SourcePageBinding, ...],
    subject_id: str,
    record_id: str,
) -> Any:
    parent_ids = tuple(dict.fromkeys(anchor.parent_evidence_id for anchor in anchors))
    if len(parent_ids) != 1 or tuple(fact.primary_evidence_ids) != parent_ids:
        raise AdmissionProjectionError("token proposal does not have one unambiguous controlling Evidence")
    token_type: type[Any]
    order_field = "token_order"
    if presentation == "threshold":
        token_type = ThresholdToken
    elif presentation == "constraint":
        token_type = ConstraintToken
    elif presentation == "formula":
        token_type = FormulaToken
    else:
        raise AdmissionProjectionError(f"unsupported token presentation: {presentation}")
    all_anchor_ids = tuple(anchor.anchor_span_id for anchor in anchors)
    tokens = tuple(
        _typed_text(
            token_type,
            element_id=f"element:{canonical_hash({'record_id': record_id, 'anchor_id': anchor.anchor_span_id})[:24]}",
            text=anchor.canonical_text,
            provenance=_provenance(
                subject_id=subject_id,
                record_id=record_id,
                fact_id=fact.fact_id,
                evidence_id=parent_ids[0],
                anchor_ids=all_anchor_ids,
                page=page,
            ),
            order_field=order_field,
            order=index,
        )
        for index, (anchor, page) in enumerate(zip(anchors, pages), start=1)
    )
    contract_type: type[Any] = {
        "threshold": ThresholdContract,
        "constraint": ConstraintContract,
        "formula": FormulaContract,
    }[presentation]
    return hash_model_r8x(contract_type, {"tokens": tokens}, "contract_sha256")


def _ordered_list_contract(
    *,
    fact: V25Step2BFactSummary,
    anchors: tuple[Any, ...],
    pages: tuple[SourcePageBinding, ...],
    subject_id: str,
    record_id: str,
    evidence_units: Mapping[str, Any],
    source_list_markers: Mapping[str, str] | None,
) -> OrderedListContract:
    items: list[OrderedListItem] = []
    all_anchor_ids = tuple(anchor.anchor_span_id for anchor in anchors)
    for order, (anchor, page) in enumerate(zip(anchors, pages), start=1):
        unit = evidence_units.get(anchor.parent_evidence_id)
        marker = (source_list_markers or {}).get(anchor.parent_evidence_id)
        if marker is None and unit is not None:
            metadata = getattr(unit, "metadata", {})
            if isinstance(metadata, Mapping):
                candidate = metadata.get("marker")
                marker = str(candidate) if candidate is not None else None
        if not marker or not str(marker).strip():
            raise AdmissionProjectionError("ordered_list requires an authoritative non-empty source marker")
        if anchor.parent_evidence_kind != EvidenceKind.LIST_ITEM:
            raise AdmissionProjectionError("ordered_list anchors must bind source list-item Evidence")
        provenance = _provenance(
            subject_id=subject_id,
            record_id=record_id,
            fact_id=fact.fact_id,
            evidence_id=anchor.parent_evidence_id,
            anchor_ids=all_anchor_ids,
            page=page,
        )
        items.append(
            hash_model_r8x(
                OrderedListItem,
                {
                    "element_id": f"element:{canonical_hash({'record_id': record_id, 'anchor_id': anchor.anchor_span_id})[:24]}",
                    "text": anchor.canonical_text,
                    "provenance": provenance,
                    "text_sha256": text_hash(anchor.canonical_text),
                    "order": order,
                    "marker": str(marker),
                },
                "element_sha256",
            )
        )
    return hash_model_r8x(OrderedListContract, {"items": tuple(items)}, "contract_sha256")


def _table_contract(
    *,
    fact: V25Step2BFactSummary,
    table: Any,
    cell_anchors: Mapping[str, tuple[Any, ...]],
    page_by_anchor: Mapping[str, SourcePageBinding],
    subject_id: str,
    record_id: str,
) -> tuple[TableContract, tuple[str, ...], tuple[SourcePageBinding, ...]]:
    cells: list[TableCell] = []
    anchor_ids: list[str] = []
    page_values: list[SourcePageBinding] = []
    all_anchor_ids = tuple(
        anchor.anchor_span_id
        for row in table.rows
        for cell in row.cells
        for anchor in cell.anchor_spans
    )
    if len(all_anchor_ids) != len(set(all_anchor_ids)):
        raise AdmissionProjectionError("table source anchors are duplicated")
    if not all_anchor_ids:
        raise AdmissionProjectionError("table proposal has no authoritative source anchors")
    controlling_evidence_id = fact.primary_evidence_ids[0]
    for row in table.rows:
        for cell in row.cells:
            if not cell.canonical_text.strip():
                raise AdmissionProjectionError("table proposal contains an empty source cell")
            anchors = cell_anchors.get(cell.evidence_id, ())
            if len(anchors) != 1:
                raise AdmissionProjectionError(
                    "every non-empty table cell must have exactly one authoritative anchor"
                )
            anchor = anchors[0]
            anchor_ids.append(anchor.anchor_span_id)
            page_values.append(page_by_anchor[anchor.anchor_span_id])
            page = page_by_anchor[anchor.anchor_span_id]
            provenance = _provenance(
                subject_id=subject_id,
                record_id=record_id,
                fact_id=fact.fact_id,
                # The V2 queue has one controlling Evidence identity.  A
                # complete table still preserves each cell's exact text and
                # geometry, while every element carries that controlling
                # identity and the full table anchor set as required by the
                # frozen contract-binding gate.
                evidence_id=controlling_evidence_id,
                anchor_ids=all_anchor_ids,
                page=page,
            )
            cells.append(
                hash_model_r8x(
                    TableCell,
                    {
                        "element_id": f"element:{canonical_hash({'record_id': record_id, 'cell_id': cell.evidence_id})[:24]}",
                        "text": cell.canonical_text,
                        "provenance": provenance,
                        "text_sha256": text_hash(cell.canonical_text),
                        "row": cell.row_index + 1,
                        "column": cell.column_index + 1,
                        "row_span": cell.row_span,
                        "column_span": cell.col_span,
                        "reading_order": len(cells) + 1,
                    },
                    "element_sha256",
                )
            )
    if not cells:
        raise AdmissionProjectionError("table proposal has no non-empty source cells")
    pages = tuple(dict.fromkeys(page_values))
    manifests = {page.source_manifest_sha256 for page in pages}
    if len(manifests) != 1:
        raise AdmissionProjectionError("table proposal crossed source manifests")
    contract = hash_model_r8x(
        TableContract,
        {"rows": table.row_count, "columns": table.column_count, "cells": tuple(cells)},
        "contract_sha256",
    )
    return contract, tuple(anchor_ids), pages


def _build_contract(
    *,
    proposal: V25BModelCardProposal,
    fact: V25Step2BFactSummary,
    request: V25BStep2BRequest,
    sections: Sequence[Step2ASectionInput] | None,
    spans: Mapping[str, Any],
    cells: Mapping[str, Any],
    tables: Mapping[str, Any],
    evidence_pages: Mapping[str, int],
    evidence_units: Mapping[str, Any],
    page_bindings: Mapping[tuple[str, int], SourcePageBinding],
    subject_id: str,
    record_id: str,
    source_list_markers: Mapping[str, str] | None,
) -> tuple[Any, tuple[Any, ...], tuple[SourcePageBinding, ...], tuple[str, ...], str]:
    anchor_ids = tuple(fact.primary_anchor_span_ids)
    if not anchor_ids or len(anchor_ids) != len(set(anchor_ids)):
        raise AdmissionProjectionError("candidate has empty or duplicate authoritative anchors")
    try:
        anchors = tuple(spans[anchor_id] for anchor_id in anchor_ids)
    except KeyError as error:
        raise AdmissionProjectionError("candidate references an unknown source anchor") from error
    page_by_anchor = {
        anchor.anchor_span_id: _source_page_for_anchor(
            anchor, evidence_pages=evidence_pages, page_bindings=page_bindings
        )
        for anchor in anchors
    }
    pages = tuple(dict.fromkeys(page_by_anchor.values()))
    manifests = {anchor.source_manifest_sha256 for anchor in anchors}
    if len(manifests) != 1 or any(page.source_manifest_sha256 not in manifests for page in pages):
        raise AdmissionProjectionError("candidate crossed source manifests")
    presentation = proposal.presentation.value
    if presentation == "diagram":
        raise AdmissionProjectionError("diagram proposal has no legal Admission V2 anchor representation")
    if presentation == "ordered_list":
        # Ordered-list marker semantics are not yet represented by a frozen
        # source contract.  A caller-provided marker (or metadata copied from
        # an EvidenceUnit) is therefore never an authority substitute.
        raise AdmissionProjectionError(
            "ordered_list is not admitted until an authoritative source marker contract exists"
        )
    if presentation == "table":
        table_ids = tuple(fact.source_table_ids)
        if len(table_ids) != 1 or table_ids[0] not in tables:
            raise AdmissionProjectionError("table proposal must bind one explicit source table")
        table = tables[table_ids[0]]
        cell_anchors = {
            cell_id: tuple(span for span in spans.values() if span.parent_evidence_id == cell_id)
            for cell_id in (cell.evidence_id for row in table.rows for cell in row.cells)
        }
        table_contract, full_anchor_ids, full_pages = _table_contract(
            fact=fact,
            table=table,
            cell_anchors=cell_anchors,
            page_by_anchor={
                anchor_id: page_by_anchor.get(anchor_id)
                or _source_page_for_anchor(
                    spans[anchor_id], evidence_pages=evidence_pages, page_bindings=page_bindings
                )
                for anchor_id in spans
            },
            subject_id=subject_id,
            record_id=record_id,
        )
        if tuple(fact.primary_evidence_ids) != (fact.primary_evidence_ids[0],):
            raise AdmissionProjectionError("table candidate must have one controlling Evidence")
        return (
            table_contract,
            tuple(spans[item] for item in full_anchor_ids),
            full_pages,
            (fact.primary_evidence_ids[0],),
            next(iter({page.source_manifest_sha256 for page in full_pages})),
        )
    if presentation not in {"constraint", "threshold", "formula"}:
        raise AdmissionProjectionError(f"unsupported Step 2B presentation: {presentation}")
    contract = _contract_for_token_family(
        presentation,
        fact=fact,
        anchors=anchors,
        pages=tuple(page_by_anchor[item.anchor_span_id] for item in anchors),
        subject_id=subject_id,
        record_id=record_id,
    )
    return contract, anchors, pages, (anchors[0].parent_evidence_id,), next(iter(manifests))


def project_step2b_to_admission_v2(
    *,
    request: V25BStep2BRequest,
    response: V25BStep2BResultPayload,
    pages: Sequence[PageEvidence],
    page_bindings: Sequence[SourcePageBinding] | Mapping[int, SourcePageBinding],
    sections: Sequence[Step2ASectionInput] | None = None,
    source_list_markers: Mapping[str, str] | None = None,
    request_id: str | None = None,
) -> AdmissionProjectionV2:
    """Project one complete Step 2B response; never return a partial queue."""

    request = V25BStep2BRequest.model_validate(request)
    response = V25BStep2BResultPayload.model_validate(response)
    if request_id is not None and request.identity.request_id != request_id:
        raise AdmissionProjectionError("Step 2B record request identity does not match the rebuilt request")
    page_map = _page_map(page_bindings)
    spans, cells, tables = _source_indexes(request, sections)
    evidence_pages, evidence_units = _evidence_page_indexes(request, pages)
    obligations = {tuple(item.member_fact_ids): item for item in request.grouping_obligations}
    facts = {fact.fact_id: fact for fact in request.facts}
    if len(obligations) != len(request.grouping_obligations):
        raise AdmissionProjectionError("Step 2B request contains duplicate grouping obligations")
    if any(len(proposal.primary_fact_ids) != 1 for proposal in response.proposals):
        raise AdmissionProjectionError("multi-Fact Step 2B proposals are not representable by Step3 V3")
    if any(tuple(proposal.primary_fact_ids) not in obligations for proposal in response.proposals):
        raise AdmissionProjectionError("Step 2B response does not exactly bind source grouping obligations")
    if len(response.proposals) != len(obligations) or set(tuple(item.primary_fact_ids) for item in response.proposals) != set(obligations):
        raise AdmissionProjectionError("Step 2B response has incomplete or partial grouping coverage")
    if any(len(item.member_fact_ids) != 1 for item in obligations.values()):
        raise AdmissionProjectionError("multi-Fact source grouping obligations are not representable by Step3 V3")
    for proposal in response.proposals:
        obligation = obligations[tuple(proposal.primary_fact_ids)]
        if proposal.presentation != obligation.required_presentation:
            raise AdmissionProjectionError("Step 2B proposal presentation does not match source obligation")

    queue_items: list[AdmissionQueueItemV2] = []
    drafts: dict[str, Mapping[str, Any]] = {}
    subject_id = f"subject:material:{canonical_hash(request.material_profile_id)[:24]}"
    for index, proposal in enumerate(response.proposals):
        fact = facts.get(proposal.primary_fact_ids[0])
        if fact is None:
            raise AdmissionProjectionError("Step 2B proposal references an unknown Fact")
        binding = {
            "sample_id": request.sample_id,
            "request_id": request.identity.request_id,
            "proposal_index": index,
            "proposal": proposal.model_dump(mode="json"),
        }
        candidate_id = f"candidate:{canonical_hash(binding)}"
        record_id = f"record:step2b:{canonical_hash(binding)[:48]}"
        queue_item_id = f"queue:step2b:{canonical_hash({**binding, 'kind': 'queue'})[:48]}"
        contract, anchors, source_pages, evidence_ids, manifest = _build_contract(
            proposal=proposal,
            fact=fact,
            request=request,
            sections=sections,
            spans=spans,
            cells=cells,
            tables=tables,
            evidence_pages=evidence_pages,
            evidence_units=evidence_units,
            page_bindings=page_map,
            subject_id=subject_id,
            record_id=record_id,
            source_list_markers=source_list_markers,
        )
        if len(evidence_ids) != 1:
            raise AdmissionProjectionError("candidate does not have one controlling Evidence")
        queue_payload = {
            "candidate_id": candidate_id,
            "queue_item_id": queue_item_id,
            "subject_id": subject_id,
            "record_id": record_id,
            "fact_id": fact.fact_id,
            "evidence_id": evidence_ids[0],
            "presentation": proposal.presentation.value,
            "source_manifest_sha256": manifest,
            "anchor_ids": tuple(anchor.anchor_span_id for anchor in anchors),
            "pages": source_pages,
            "disposition": "manual_review",
            "source_decision_id": None,
            "decision_audit_id": f"audit:r84:v2:{canonical_hash(binding)[:24]}",
            "typed_contract_authorized": False,
            "reason": "semantically validated live Step2b output awaiting explicit V2 source-page review",
        }
        queue_item = hash_model_r8x(AdmissionQueueItemV2, queue_payload, "entry_sha256")
        queue_items.append(queue_item)
        drafts[candidate_id] = {
            "candidate_id": candidate_id,
            "queue_item_id": queue_item_id,
            "presentation": proposal.presentation.value,
            "typed_contract": contract.model_dump(mode="json"),
            "typed_contract_sha256": canonical_hash(contract.model_dump(mode="json")),
            "source_anchor_ids": [anchor.anchor_span_id for anchor in anchors],
            "source_evidence_ids": list(evidence_ids),
            "source_manifest_sha256": manifest,
        }
    return AdmissionProjectionV2(
        request_id=request.identity.request_id,
        sample_id=request.sample_id,
        queue_items=tuple(queue_items),
        contract_drafts_by_candidate=drafts,
    )


def build_admission_queue_items_v2(**kwargs: Any) -> tuple[AdmissionQueueItemV2, ...]:
    """Compatibility helper returning only the sealed queue item batch."""

    return project_step2b_to_admission_v2(**kwargs).queue_items


__all__ = (
    "AdmissionProjectionError",
    "AdmissionProjectionV2",
    "build_admission_queue_items_v2",
    "make_source_page_binding",
    "project_step2b_to_admission_v2",
)
