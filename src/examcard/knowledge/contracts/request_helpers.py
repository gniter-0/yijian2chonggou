"""Request-scoped helpers shared by audits/derivation/validation.

Annotation-only references to Step2ARequest stay behind TYPE_CHECKING (PEP 563)
to keep the runtime import DAG acyclic (blueprint section 4).
"""
from __future__ import annotations
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from ...evidence.schema import EvidenceKind
from ..schema import CompletenessMode, FactType

from .anchors import EvidenceAnchorSpan
from .continuation import ContinuationBindingState
from .obligations import (
    Step2ACoverageObligationAnchorContext,
    Step2ACoverageObligationAnchorRole,
    Step2ACoverageObligationContext,
    Step2ACoverageObligationContextInput,
    Step2ACoverageObligationKind,
    make_step2a_coverage_obligation_context_id,
    make_step2a_coverage_obligation_context_input_id,
)
from .table_inputs import StructuredTableInput
from .versions import STEP2A_COVERAGE_OBLIGATION_CONTEXT_INPUT_VERSION

if TYPE_CHECKING:
    from .step2a_request import Step2ARequest


def _step2a_anchor_sources(
    request: Step2ARequest,
) -> tuple[dict[str, EvidenceAnchorSpan], set[str], set[str], dict[str, set[str]]]:
    """Return only projection-selected source fragments, never whole parent text."""

    spans_by_id: dict[str, EvidenceAnchorSpan] = {}
    table_cell_span_ids: set[str] = set()
    section_ids: set[str] = set()
    span_sections: dict[str, set[str]] = {}
    for section in request.sections:
        section_ids.add(section.section_id)
        for span in section.anchor_spans:
            if span.anchor_span_id in spans_by_id and spans_by_id[span.anchor_span_id] != span:
                raise ValueError("one anchor span ID cannot bind different source content")
            spans_by_id[span.anchor_span_id] = span
            span_sections.setdefault(span.anchor_span_id, set()).add(section.section_id)
            if span.parent_evidence_kind == EvidenceKind.TABLE_CELL:
                table_cell_span_ids.add(span.anchor_span_id)
    for controlled in request.table_continuation_inputs:
        section_by_manifest = {
            item.source_manifest_sha256: item.controlled_section_id
            for item in controlled.source_pages
        }
        section_ids.update(section_by_manifest.values())
        for span in controlled.anchor_spans:
            if span.anchor_span_id in spans_by_id:
                raise ValueError("controlled table continuation span collides with an ordinary projection span")
            controlled_section_id = section_by_manifest.get(span.source_manifest_sha256)
            if controlled_section_id is None:
                raise ValueError("controlled table continuation span has no declared source section")
            spans_by_id[span.anchor_span_id] = span
            span_sections.setdefault(span.anchor_span_id, set()).add(controlled_section_id)
            if span.parent_evidence_kind != EvidenceKind.TABLE_CELL:
                raise ValueError("controlled table continuation inputs may expose only table-cell anchor spans")
            table_cell_span_ids.add(span.anchor_span_id)
    return spans_by_id, table_cell_span_ids, section_ids, span_sections


def _ordered_parent_evidence_ids(span_ids: Iterable[str], spans_by_id: Mapping[str, EvidenceAnchorSpan]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(spans_by_id[span_id].parent_evidence_id for span_id in span_ids))


def _derive_v13_source_section_ids(request: Step2ARequest, selected_span_ids: Iterable[str]) -> tuple[str, ...]:
    selected = set(selected_span_ids)
    _, _, _, span_sections = _step2a_anchor_sources(request)
    ordered_section_ids = [section.section_id for section in request.sections]
    ordered_section_ids.extend(
        source_page.controlled_section_id
        for controlled in request.table_continuation_inputs
        for source_page in controlled.source_pages
    )
    return tuple(
        section_id
        for section_id in dict.fromkeys(ordered_section_ids)
        if any(section_id in span_sections.get(span_id, set()) for span_id in selected)
    )


def _request_structured_tables(request: Step2ARequest) -> tuple[StructuredTableInput, ...]:
    """Return ordinary and V17 controlled tables in deterministic request order."""

    tables = [table for section in request.sections for table in section.tables]
    tables.extend(controlled.table for controlled in request.table_continuation_inputs)
    table_ids = [table.table_evidence_id for table in tables]
    if len(table_ids) != len(set(table_ids)):
        raise ValueError("Step 2A request structured table identifiers must be unique")
    return tuple(tables)


def derive_step2a_coverage_obligation_context_input(
    *, request: Step2ARequest
) -> Step2ACoverageObligationContextInput:
    """Bind every V19 coverage obligation to source text without using Golden data.

    The context is intentionally derived from only request-local anchor spans,
    structured tables, and reviewed continuation bindings.  It provides the
    model a direct, auditable association between each mandatory Fact owner and
    the immutable source wording it must preserve; it does not introduce a
    title, card grouping, or Learning Golden assertion.
    """

    obligations = request.coverage_obligation_input
    if obligations is None:
        raise ValueError("coverage-obligation context requires the deterministic obligation input")
    spans_by_id, table_cell_span_ids, _, _ = _step2a_anchor_sources(request)
    tables = {table.table_evidence_id: table for table in _request_structured_tables(request)}
    requirements = {
        requirement.requirement_id: requirement
        for table in tables.values()
        for requirement in table.atomic_subfact_requirements
    }
    continuation_roles: dict[str, Step2ACoverageObligationAnchorRole] = {}
    for binding in request.continuation_bindings:
        if binding.state != ContinuationBindingState.CLOSED:
            continue
        continuation_roles.update(
            {span_id: Step2ACoverageObligationAnchorRole.CONTINUATION_SOURCE for span_id in binding.source_anchor_span_ids}
        )
        continuation_roles.update(
            {span_id: Step2ACoverageObligationAnchorRole.CONTINUATION_CLOSURE for span_id in binding.closure_anchor_span_ids}
        )
    for controlled in request.table_continuation_inputs:
        continuation_roles[controlled.item_three_source_anchor_span_id] = Step2ACoverageObligationAnchorRole.CONTINUATION_SOURCE
        continuation_roles[controlled.item_three_closure_anchor_span_id] = Step2ACoverageObligationAnchorRole.CONTINUATION_CLOSURE

    contexts: list[Step2ACoverageObligationContext] = []
    for obligation in obligations.obligations:
        table = tables.get(obligation.table_evidence_id or "")
        if obligation.kind != Step2ACoverageObligationKind.CLOSED_CONTINUATION and table is None:
            raise ValueError("table coverage obligations must bind a request-local structured table")
        requirement = requirements.get(obligation.atomic_requirement_id or "")
        if obligation.kind == Step2ACoverageObligationKind.ATOMIC_TABLE_RULE and requirement is None:
            raise ValueError("atomic coverage obligations must bind a request-local atomic requirement")
        anchor_contexts: list[Step2ACoverageObligationAnchorContext] = []
        for span_id in obligation.required_primary_anchor_span_ids:
            span = spans_by_id.get(span_id)
            if span is None:
                raise ValueError("coverage-obligation context cannot include an unknown anchor span")
            role = continuation_roles.get(span_id)
            if role is None:
                role = (
                    Step2ACoverageObligationAnchorRole.TABLE_CELL
                    if span_id in table_cell_span_ids
                    else Step2ACoverageObligationAnchorRole.DIRECT_EVIDENCE
                )
            anchor_contexts.append(
                Step2ACoverageObligationAnchorContext(
                    anchor_span_id=span.anchor_span_id,
                    parent_evidence_id=span.parent_evidence_id,
                    parent_evidence_kind=span.parent_evidence_kind,
                    role=role,
                    source_text=span.canonical_text,
                    source_text_sha256=span.exact_text_sha256,
                )
            )
        context_payload = {
            "obligation_id": obligation.obligation_id,
            "kind": obligation.kind.value,
            "expected_fact_type": obligation.expected_fact_type.value,
            "required_primary_anchor_span_ids": list(obligation.required_primary_anchor_span_ids),
            "requires_all_items": obligation.requires_all_items,
            "anchor_contexts": [item.model_dump(mode="json") for item in anchor_contexts],
            "table_evidence_id": obligation.table_evidence_id,
            "table_row_count": table.row_count if obligation.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION else None,
            "table_column_count": table.column_count if obligation.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION else None,
            "required_source_assertion": requirement.required_assertion if requirement is not None else None,
            "required_source_assertion_sha256": requirement.required_assertion_sha256 if requirement is not None else None,
        }
        contexts.append(
            Step2ACoverageObligationContext.model_validate(
                {
                    "context_id": make_step2a_coverage_obligation_context_id(context_payload),
                    **context_payload,
                }
            )
        )
    payload = {
        "input_version": STEP2A_COVERAGE_OBLIGATION_CONTEXT_INPUT_VERSION,
        "coverage_obligation_input_id": obligations.input_id,
        "source_document_sha256": obligations.source_document_sha256,
        "content_policy_id": obligations.content_policy_id,
        "content_policy_revision": obligations.content_policy_revision,
        "content_policy_fingerprint_sha256": obligations.content_policy_fingerprint_sha256,
        "contexts": [item.model_dump(mode="json") for item in contexts],
    }
    return Step2ACoverageObligationContextInput.model_validate(
        {
            "input_id": make_step2a_coverage_obligation_context_input_id(payload),
            **payload,
        }
    )


def _derive_v13_completeness_mode(
    *, request: Step2ARequest, fact_type: FactType, selected_span_ids: Iterable[str]
) -> tuple[CompletenessMode | None, str]:
    selected = set(selected_span_ids)
    covered_bindings = tuple(
        binding
        for binding in request.continuation_bindings
        if binding.state == ContinuationBindingState.CLOSED
        and set((*binding.source_anchor_span_ids, *binding.closure_anchor_span_ids)) <= selected
    )
    if fact_type != FactType.ENUMERATION or not covered_bindings:
        return CompletenessMode.ATOMIC, "no unique closed continuation enumeration is fully selected; derived atomic"
    if len(covered_bindings) > 1:
        return None, "selected spans fully cover multiple closed continuation bindings; completeness is ambiguous"
    return CompletenessMode.ALL_ITEMS, "one closed continuation binding is fully selected by an enumeration; derived all_items"
