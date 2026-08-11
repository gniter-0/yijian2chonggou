"""V18/V19 coverage obligations: deterministic mandatory Fact checklists."""
from __future__ import annotations
import hashlib
from collections.abc import Iterable
from enum import StrEnum

from pydantic import Field, model_validator

from ...evidence.schema import EvidenceKind
from ...materials.schema import StrictModel
from ..schema import FactType

from .continuation import ContinuationBinding, ContinuationBindingState
from .hashing import _canonical_sha256
from .request_identity import _SHA256_PATTERN
from .table_inputs import Step2ASectionInput, TableContinuationRequestInput
from .versions import (
    STEP2A_COVERAGE_OBLIGATION_CONTEXT_INPUT_VERSION,
    STEP2A_COVERAGE_OBLIGATION_INPUT_VERSION,
)


class Step2ACoverageObligationKind(StrEnum):
    COMPLETE_TABLE_COMPOSITION = "complete_table_composition"
    ATOMIC_TABLE_RULE = "atomic_table_rule"
    CLOSED_CONTINUATION = "closed_continuation"


def make_step2a_coverage_obligation_id(
    *,
    kind: Step2ACoverageObligationKind,
    source_binding_id: str,
    table_evidence_id: str | None,
    atomic_requirement_id: str | None,
    expected_fact_type: FactType,
    required_primary_anchor_span_ids: Iterable[str],
    requires_all_items: bool,
) -> str:
    """Derive an obligation identifier without embedding a title or assertion."""

    digest = _canonical_sha256(
        {
            "kind": kind.value,
            "source_binding_id": source_binding_id,
            "table_evidence_id": table_evidence_id,
            "atomic_requirement_id": atomic_requirement_id,
            "expected_fact_type": expected_fact_type.value,
            "required_primary_anchor_span_ids": list(required_primary_anchor_span_ids),
            "requires_all_items": requires_all_items,
        }
    )
    return f"coverage-obligation:{digest[:24]}"


class Step2ACoverageObligation(StrictModel):
    """One deterministic coverage requirement visible to V18 but not model-owned."""

    obligation_id: str = Field(pattern=r"^coverage-obligation:[0-9a-f]{24}$")
    kind: Step2ACoverageObligationKind
    source_binding_id: str = Field(min_length=1)
    table_evidence_id: str | None = None
    atomic_requirement_id: str | None = Field(default=None, pattern=r"^table-subfact:[0-9a-f]{24}$")
    expected_fact_type: FactType
    required_primary_anchor_span_ids: tuple[str, ...]
    requires_all_items: bool = False

    @model_validator(mode="after")
    def validate_obligation(self) -> "Step2ACoverageObligation":
        spans = self.required_primary_anchor_span_ids
        if not spans or len(spans) != len(set(spans)):
            raise ValueError("coverage obligations require unique primary anchor spans")
        if self.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION:
            if self.table_evidence_id is None or self.atomic_requirement_id is not None:
                raise ValueError("complete-table coverage obligations bind exactly one source table")
            if self.expected_fact_type != FactType.COMPOSITION or self.requires_all_items:
                raise ValueError("complete-table coverage obligations require a composition Fact without all_items")
        elif self.kind == Step2ACoverageObligationKind.ATOMIC_TABLE_RULE:
            if self.table_evidence_id is None or self.atomic_requirement_id is None:
                raise ValueError("atomic table coverage obligations bind one table and one atomic requirement")
            if self.expected_fact_type == FactType.COMPOSITION or self.requires_all_items:
                raise ValueError("atomic table coverage obligations require a non-composition atomic Fact")
        else:
            if self.table_evidence_id is not None or self.atomic_requirement_id is not None:
                raise ValueError("closed-continuation coverage obligations cannot bind a table rule")
            if self.expected_fact_type != FactType.ENUMERATION or not self.requires_all_items:
                raise ValueError("closed-continuation coverage obligations require an all-items enumeration Fact")
        expected_id = make_step2a_coverage_obligation_id(
            kind=self.kind,
            source_binding_id=self.source_binding_id,
            table_evidence_id=self.table_evidence_id,
            atomic_requirement_id=self.atomic_requirement_id,
            expected_fact_type=self.expected_fact_type,
            required_primary_anchor_span_ids=spans,
            requires_all_items=self.requires_all_items,
        )
        if self.obligation_id != expected_id:
            raise ValueError("coverage obligation ID must match its immutable source bindings and anchor spans")
        return self


def make_step2a_coverage_obligation_input_id(payload: object) -> str:
    return f"coverage-input:{_canonical_sha256(payload)[:24]}"


class Step2ACoverageObligationInput(StrictModel):
    """Versioned, auditable V18 checklist derived only from request-local sources."""

    input_version: str = STEP2A_COVERAGE_OBLIGATION_INPUT_VERSION
    input_id: str = Field(pattern=r"^coverage-input:[0-9a-f]{24}$")
    source_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_policy_id: str = Field(min_length=1)
    content_policy_revision: int = Field(ge=1)
    content_policy_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    obligations: tuple[Step2ACoverageObligation, ...]

    @model_validator(mode="after")
    def validate_input(self) -> "Step2ACoverageObligationInput":
        if self.input_version != STEP2A_COVERAGE_OBLIGATION_INPUT_VERSION:
            raise ValueError("unsupported Step 2A coverage-obligation input version")
        obligation_ids = [item.obligation_id for item in self.obligations]
        if not obligation_ids or len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("coverage-obligation inputs require one or more unique obligations")
        expected_id = make_step2a_coverage_obligation_input_id(
            self.model_dump(mode="json", exclude={"input_id"})
        )
        if self.input_id != expected_id:
            raise ValueError("coverage-obligation input ID does not match immutable content")
        return self


class Step2ACoverageObligationAnchorRole(StrEnum):
    DIRECT_EVIDENCE = "direct_evidence"
    TABLE_CELL = "table_cell"
    CONTINUATION_SOURCE = "continuation_source"
    CONTINUATION_CLOSURE = "continuation_closure"


class Step2ACoverageObligationAnchorContext(StrictModel):
    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    parent_evidence_id: str = Field(min_length=1)
    parent_evidence_kind: EvidenceKind
    role: Step2ACoverageObligationAnchorRole
    source_text: str = Field(min_length=1)
    source_text_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_anchor_context(self) -> "Step2ACoverageObligationAnchorContext":
        if self.source_text_sha256 != hashlib.sha256(self.source_text.encode("utf-8")).hexdigest():
            raise ValueError("coverage-obligation anchor context text hash must match source text")
        return self


def make_step2a_coverage_obligation_context_id(payload: object) -> str:
    return f"coverage-context:{_canonical_sha256(payload)[:24]}"


class Step2ACoverageObligationContext(StrictModel):
    context_id: str = Field(pattern=r"^coverage-context:[0-9a-f]{24}$")
    obligation_id: str = Field(pattern=r"^coverage-obligation:[0-9a-f]{24}$")
    kind: Step2ACoverageObligationKind
    expected_fact_type: FactType
    required_primary_anchor_span_ids: tuple[str, ...]
    requires_all_items: bool
    anchor_contexts: tuple[Step2ACoverageObligationAnchorContext, ...]
    table_evidence_id: str | None = None
    table_row_count: int | None = Field(default=None, ge=1)
    table_column_count: int | None = Field(default=None, ge=1)
    required_source_assertion: str | None = None
    required_source_assertion_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_context(self) -> "Step2ACoverageObligationContext":
        anchor_ids = tuple(item.anchor_span_id for item in self.anchor_contexts)
        if (
            not self.required_primary_anchor_span_ids
            or len(self.required_primary_anchor_span_ids) != len(set(self.required_primary_anchor_span_ids))
            or anchor_ids != self.required_primary_anchor_span_ids
        ):
            raise ValueError("coverage-obligation contexts require one ordered source context for every required anchor")
        if (self.required_source_assertion is None) != (self.required_source_assertion_sha256 is None):
            raise ValueError("coverage-obligation source assertion text and hash must be present together")
        if self.required_source_assertion is not None:
            if not self.required_source_assertion.strip() or self.required_source_assertion_sha256 != hashlib.sha256(
                self.required_source_assertion.encode("utf-8")
            ).hexdigest():
                raise ValueError("coverage-obligation source assertion hash must match its exact source text")
        if self.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION:
            if self.table_evidence_id is None or self.table_row_count is None or self.table_column_count is None:
                raise ValueError("complete-table contexts require immutable table identity and grid shape")
        elif self.kind == Step2ACoverageObligationKind.ATOMIC_TABLE_RULE:
            if self.table_evidence_id is None or self.table_row_count is not None or self.table_column_count is not None:
                raise ValueError("atomic-table contexts require source table identity but no complete-table grid")
        elif self.table_evidence_id is not None or self.table_row_count is not None or self.table_column_count is not None:
            raise ValueError("closed-continuation contexts cannot claim a table")
        payload = self.model_dump(mode="json", exclude={"context_id"})
        if self.context_id != make_step2a_coverage_obligation_context_id(payload):
            raise ValueError("coverage-obligation context ID must be deterministic")
        return self


def make_step2a_coverage_obligation_context_input_id(payload: object) -> str:
    return f"coverage-context-input:{_canonical_sha256(payload)[:24]}"


class Step2ACoverageObligationContextInput(StrictModel):
    input_version: str = STEP2A_COVERAGE_OBLIGATION_CONTEXT_INPUT_VERSION
    input_id: str = Field(pattern=r"^coverage-context-input:[0-9a-f]{24}$")
    coverage_obligation_input_id: str = Field(pattern=r"^coverage-input:[0-9a-f]{24}$")
    source_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_policy_id: str = Field(min_length=1)
    content_policy_revision: int = Field(ge=1)
    content_policy_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    contexts: tuple[Step2ACoverageObligationContext, ...]

    @model_validator(mode="after")
    def validate_input(self) -> "Step2ACoverageObligationContextInput":
        context_ids = [item.context_id for item in self.contexts]
        obligation_ids = [item.obligation_id for item in self.contexts]
        if not self.contexts or len(context_ids) != len(set(context_ids)) or len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("coverage-obligation context input requires unique contexts and obligations")
        payload = self.model_dump(mode="json", exclude={"input_id"})
        if self.input_id != make_step2a_coverage_obligation_context_input_id(payload):
            raise ValueError("coverage-obligation context input ID must be deterministic")
        return self


def derive_step2a_coverage_obligation_input(
    *,
    source_document_sha256: str,
    content_policy_id: str,
    content_policy_revision: int,
    content_policy_fingerprint_sha256: str,
    sections: Iterable[Step2ASectionInput],
    continuation_bindings: Iterable[ContinuationBinding],
    table_continuation_inputs: Iterable[TableContinuationRequestInput],
) -> Step2ACoverageObligationInput:
    """Derive V18 obligations from structured request sources, never Learning Golden data."""

    obligations: list[Step2ACoverageObligation] = []
    normal_tables = tuple(table for section in sections for table in section.tables)
    controlled_inputs = tuple(table_continuation_inputs)
    controlled_tables = {item.table.table_evidence_id: item for item in controlled_inputs}
    tables = (*normal_tables, *(item.table for item in controlled_inputs))
    table_ids = [table.table_evidence_id for table in tables]
    if not tables or len(table_ids) != len(set(table_ids)):
        raise ValueError("coverage obligations require unique structured tables")

    for table in tables:
        controlled = controlled_tables.get(table.table_evidence_id)
        source_binding_id = controlled.binding_id if controlled is not None else table.table_evidence_id
        source_kind = (
            Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION
        )
        complete_spans = tuple(
            span.anchor_span_id
            for row in table.rows
            for cell in row.cells
            for span in cell.anchor_spans
        )
        obligations.append(
            Step2ACoverageObligation(
                obligation_id=make_step2a_coverage_obligation_id(
                    kind=source_kind,
                    source_binding_id=source_binding_id,
                    table_evidence_id=table.table_evidence_id,
                    atomic_requirement_id=None,
                    expected_fact_type=FactType.COMPOSITION,
                    required_primary_anchor_span_ids=complete_spans,
                    requires_all_items=False,
                ),
                kind=source_kind,
                source_binding_id=source_binding_id,
                table_evidence_id=table.table_evidence_id,
                expected_fact_type=FactType.COMPOSITION,
                required_primary_anchor_span_ids=complete_spans,
            )
        )
        for requirement in table.atomic_subfact_requirements:
            required_spans = [requirement.anchor_span_id]
            if controlled is not None and requirement.anchor_span_id == controlled.item_three_source_anchor_span_id:
                required_spans.append(controlled.item_three_closure_anchor_span_id)
            obligations.append(
                Step2ACoverageObligation(
                    obligation_id=make_step2a_coverage_obligation_id(
                        kind=Step2ACoverageObligationKind.ATOMIC_TABLE_RULE,
                        source_binding_id=source_binding_id,
                        table_evidence_id=table.table_evidence_id,
                        atomic_requirement_id=requirement.requirement_id,
                        expected_fact_type=requirement.expected_fact_type,
                        required_primary_anchor_span_ids=required_spans,
                        requires_all_items=False,
                    ),
                    kind=Step2ACoverageObligationKind.ATOMIC_TABLE_RULE,
                    source_binding_id=source_binding_id,
                    table_evidence_id=table.table_evidence_id,
                    atomic_requirement_id=requirement.requirement_id,
                    expected_fact_type=requirement.expected_fact_type,
                    required_primary_anchor_span_ids=tuple(required_spans),
                )
            )

    for binding in continuation_bindings:
        if binding.state != ContinuationBindingState.CLOSED:
            continue
        required_spans = (*binding.source_anchor_span_ids, *binding.closure_anchor_span_ids)
        obligations.append(
            Step2ACoverageObligation(
                obligation_id=make_step2a_coverage_obligation_id(
                    kind=Step2ACoverageObligationKind.CLOSED_CONTINUATION,
                    source_binding_id=binding.binding_id,
                    table_evidence_id=None,
                    atomic_requirement_id=None,
                    expected_fact_type=FactType.ENUMERATION,
                    required_primary_anchor_span_ids=required_spans,
                    requires_all_items=True,
                ),
                kind=Step2ACoverageObligationKind.CLOSED_CONTINUATION,
                source_binding_id=binding.binding_id,
                expected_fact_type=FactType.ENUMERATION,
                required_primary_anchor_span_ids=required_spans,
                requires_all_items=True,
            )
        )

    ordered = tuple(sorted(obligations, key=lambda item: item.obligation_id))
    payload = {
        "input_version": STEP2A_COVERAGE_OBLIGATION_INPUT_VERSION,
        "source_document_sha256": source_document_sha256,
        "content_policy_id": content_policy_id,
        "content_policy_revision": content_policy_revision,
        "content_policy_fingerprint_sha256": content_policy_fingerprint_sha256,
        "obligations": [item.model_dump(mode="json") for item in ordered],
    }
    return Step2ACoverageObligationInput.model_validate(
        {
            "input_id": make_step2a_coverage_obligation_input_id(payload),
            **payload,
        }
    )
