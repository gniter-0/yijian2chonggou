"""OpenAI-compatible, audit-first execution for V2 Step 2A/2B planning.

This module is deliberately separate from V1 Step 2. It persists every V2
request/response under an independent root, never mutates Evidence or review
sidecars, and treats a sent request without a durable response as
``unknown_outcome`` instead of retrying it automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import Field, ValidationError, model_validator

from ..evidence.layout import normalized_compare_text
from ..materials.schema import StrictModel

from .schema import FactType
from .contracts import (
    ContractValidationReport,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    FactSummaryInput,
    Step2AFailure,
    Step2AFactProposal,
    Step2ARequest,
    Step2AResponse,
    Step2AConditionClaimAudit,
    Step2ACoverageObligationForensic,
    Step2ANumericClaimAudit,
    Step2ATableNumericOccurrenceAudit,
    Step2ATableStructuralIndexOccurrenceAudit,
    Step2ACompleteTableStructuralProjectionAudit,
    Step2AAnchorSpanSelectionAudit,
    Step2ATableCellAnchorAudit,
    Step2BRequest,
    Step2BResponse,
    Step2BResultPayload,
    Step2FailureState,
    Step2RequestIdentity,
    Step2ResponseFormat,
    Step2ResultState,
    default_step2_prompt_template,
    derive_step2a_condition_claims,
    derive_step2a_coverage_obligation_forensics,
    derive_step2a_numeric_claims,
    derive_step2a_anchor_span_selection,
    derive_step2a_table_cell_anchors,
    fingerprint_step2_prompt,
    step2a_result_payload_model,
    validate_step2a_response,
    validate_step2b_response,
)
from .quality import (
    CardGranularityResult,
    HeadingOnlyFactResult,
    TeachingQualityGate,
    TeachingQualityEvaluationState,
    TitleQualityResult,
    assess_card_granularity,
    assess_heading_only_fact,
    assess_title_quality,
    build_teaching_quality_gate,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class Step2JournalOutcome(StrEnum):
    REQUEST_STARTED = "request_started"
    CACHE_HIT = "cache_hit"
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"
    RECONCILED_TERMINAL = "reconciled_terminal"


class SemanticReviewState(StrEnum):
    DRAFT = "draft"


class Step2JournalEvent(StrictModel):
    event_id: str = Field(pattern=r"^step2-event:[0-9a-f]{64}$")
    recorded_at_utc: str = Field(pattern=r"^.+Z$")
    outcome: Step2JournalOutcome
    request: Step2RequestIdentity
    provider_request_id: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    error_type: str | None = None
    error_message: str | None = None
    reconciled_terminal_outcome: Step2JournalOutcome | None = None

    @model_validator(mode="after")
    def validate_reconciliation(self) -> "Step2JournalEvent":
        terminal_outcomes = {
            Step2JournalOutcome.SUCCESS,
            Step2JournalOutcome.FAILED,
            Step2JournalOutcome.UNKNOWN_OUTCOME,
        }
        if self.outcome == Step2JournalOutcome.RECONCILED_TERMINAL:
            if self.reconciled_terminal_outcome not in terminal_outcomes:
                raise ValueError("reconciled terminal journal events must declare the recovered terminal outcome")
        elif self.reconciled_terminal_outcome is not None:
            raise ValueError("only reconciled terminal journal events may declare a recovered outcome")
        return self


def _make_event_id(*, request_id: str, outcome: Step2JournalOutcome, ordinal: int) -> str:
    return f"step2-event:{_sha256(_json_dumps({'request_id': request_id, 'outcome': outcome.value, 'ordinal': ordinal}))}"


class Step2TeachingReview(StrictModel):
    state: SemanticReviewState = SemanticReviewState.DRAFT
    gate: TeachingQualityGate
    title_results: tuple[TitleQualityResult, ...] = ()
    granularity_results: tuple[CardGranularityResult, ...] = ()
    heading_only_fact_results: tuple[HeadingOnlyFactResult, ...] = ()
    basis: str = Field(min_length=1)


class Step2SchemaErrorLocation(StrictModel):
    """Sanitized Pydantic location without any rejected input value."""

    path: tuple[str | int, ...] = Field(min_length=1)
    error_type: str = Field(min_length=1, max_length=96)


class Step2RejectedResponseForensics(StrictModel):
    """Safe diagnostics for a model response rejected after it reached this process.

    The original response body is deliberately never persisted. The request
    identity, content hash, and normalized local error class are enough to
    correlate a future review without making raw model text a runtime artifact.
    """

    request_id: str = Field(pattern=r"^step2[ab]-request:[0-9a-f]{64}$")
    raw_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    rejection_code: str = Field(pattern=r"^[a-z0-9_]+$")
    error_types: tuple[str, ...] = ()
    schema_error_locations: tuple[Step2SchemaErrorLocation, ...] = ()
    schema_error_count: int = Field(default=0, ge=0)
    missing_field_count: int = Field(default=0, ge=0)
    extra_field_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_forensics(self) -> "Step2RejectedResponseForensics":
        if len(self.error_types) != len(set(self.error_types)):
            raise ValueError("rejected-response forensic error types must be unique")
        locations = [(location.path, location.error_type) for location in self.schema_error_locations]
        if len(locations) != len(set(locations)):
            raise ValueError("rejected-response forensic schema locations must be unique")
        if self.schema_error_count != len(self.schema_error_locations):
            raise ValueError("rejected-response forensic schema error count must match locations")
        if self.missing_field_count != sum(location.error_type == "missing" for location in self.schema_error_locations):
            raise ValueError("rejected-response forensic missing count must match locations")
        if self.extra_field_count != sum(location.error_type == "extra_forbidden" for location in self.schema_error_locations):
            raise ValueError("rejected-response forensic extra-field count must match locations")
        return self


class Step2AExecutionRecord(StrictModel):
    request: Step2ARequest
    response: Step2AResponse
    contract_report: ContractValidationReport
    teaching_review: Step2TeachingReview
    table_cell_anchor_audit: tuple[Step2ATableCellAnchorAudit, ...] = ()
    anchor_span_selection_audit: tuple[Step2AAnchorSpanSelectionAudit, ...] = ()
    numeric_claim_derivation_audit: tuple[Step2ANumericClaimAudit, ...] = ()
    table_numeric_occurrence_audit: tuple[Step2ATableNumericOccurrenceAudit, ...] = ()
    table_structural_index_occurrence_audit: tuple[Step2ATableStructuralIndexOccurrenceAudit, ...] = ()
    table_structural_projection_audit: tuple[Step2ACompleteTableStructuralProjectionAudit, ...] = ()
    condition_claim_audit: tuple[Step2AConditionClaimAudit, ...] = ()
    coverage_obligation_forensics: tuple[Step2ACoverageObligationForensic, ...] = ()
    provider_request_id: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    raw_response_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    rejected_response_forensics: Step2RejectedResponseForensics | None = None
    completed_at_utc: str

    @model_validator(mode="after")
    def validate_record(self) -> "Step2AExecutionRecord":
        proposal_keys = tuple(proposal.proposal_key for proposal in self.response.proposals)
        audit_keys = tuple(audit.proposal_key for audit in self.table_cell_anchor_audit)
        selection_audit_keys = tuple(audit.proposal_key for audit in self.anchor_span_selection_audit)
        if self.response.state == Step2ResultState.SUCCEEDED and proposal_keys != audit_keys:
            raise ValueError("successful Step 2A records require table-cell audit for every proposal")
        if self.response.state == Step2ResultState.SUCCEEDED and proposal_keys != selection_audit_keys:
            raise ValueError("successful Step 2A records require anchor-span selection audit for every proposal")
        if self.response.state == Step2ResultState.FAILED and self.table_cell_anchor_audit:
            if self.response.failure is None or self.response.failure.state != Step2FailureState.GROUNDING_FAILED:
                raise ValueError("only post-derivation grounding failures may retain table-cell anchor audit")
            if len(audit_keys) != len(set(audit_keys)):
                raise ValueError("failed Step 2A table-cell audit proposal keys must remain unique")
        if self.response.state == Step2ResultState.FAILED and self.anchor_span_selection_audit:
            if self.response.failure is None or self.response.failure.state != Step2FailureState.GROUNDING_FAILED:
                raise ValueError("only post-derivation grounding failures may retain anchor-span selection audit")
            if len(selection_audit_keys) != len(set(selection_audit_keys)):
                raise ValueError("failed Step 2A anchor-span audit proposal keys must remain unique")
        if self.response.state == Step2ResultState.FAILED and self.numeric_claim_derivation_audit:
            if self.response.failure is None or self.response.failure.state != Step2FailureState.GROUNDING_FAILED:
                raise ValueError("only post-derivation grounding failures may retain numeric-claim derivation audit")
        if self.response.state == Step2ResultState.FAILED and self.table_numeric_occurrence_audit:
            if self.response.failure is None or self.response.failure.state != Step2FailureState.GROUNDING_FAILED:
                raise ValueError("only post-derivation grounding failures may retain table numeric occurrence audit")
        if self.response.state == Step2ResultState.FAILED and self.table_structural_index_occurrence_audit:
            if self.response.failure is None or self.response.failure.state != Step2FailureState.GROUNDING_FAILED:
                raise ValueError("only post-derivation grounding failures may retain table structural-index occurrence audit")
        if self.response.state == Step2ResultState.FAILED and self.table_structural_projection_audit:
            if self.response.failure is None or self.response.failure.state != Step2FailureState.GROUNDING_FAILED:
                raise ValueError("only post-derivation grounding failures may retain table structural projection audit")
        if self.response.state == Step2ResultState.FAILED and self.condition_claim_audit:
            if self.response.failure is None or self.response.failure.state != Step2FailureState.GROUNDING_FAILED:
                raise ValueError("only post-derivation grounding failures may retain condition-claim audit")
        if self.response.state == Step2ResultState.FAILED and self.coverage_obligation_forensics:
            if self.response.failure is None or self.response.failure.state != Step2FailureState.GROUNDING_FAILED:
                raise ValueError("only post-derivation grounding failures may retain coverage-obligation forensics")
        if self.response.state == Step2ResultState.SUCCEEDED and self.condition_claim_audit != self.contract_report.condition_claim_audit:
            raise ValueError("successful Step 2A condition audit must equal its contract report")
        if (
            self.response.state == Step2ResultState.SUCCEEDED
            and self.numeric_claim_derivation_audit != self.contract_report.numeric_claim_derivation_audit
        ):
            raise ValueError("successful Step 2A numeric derivation audit must equal its contract report")
        if (
            self.response.state == Step2ResultState.SUCCEEDED
            and self.table_numeric_occurrence_audit != self.contract_report.table_numeric_occurrence_audit
        ):
            raise ValueError("successful Step 2A table numeric occurrence audit must equal its contract report")
        if (
            self.response.state == Step2ResultState.SUCCEEDED
            and self.table_structural_index_occurrence_audit
            != self.contract_report.table_structural_index_occurrence_audit
        ):
            raise ValueError("successful Step 2A table structural-index audit must equal its contract report")
        if (
            self.response.state == Step2ResultState.SUCCEEDED
            and self.table_structural_projection_audit
            != self.contract_report.table_structural_projection_audit
        ):
            raise ValueError("successful Step 2A table structural projection audit must equal its contract report")
        if self.rejected_response_forensics is not None:
            if self.response.state != Step2ResultState.FAILED:
                raise ValueError("rejected-response forensics require a failed response")
            if self.rejected_response_forensics.request_id != self.request.identity.request_id:
                raise ValueError("rejected-response forensics must bind the execution request")
            if self.raw_response_sha256 != self.rejected_response_forensics.raw_response_sha256:
                raise ValueError("rejected-response forensics must bind the stored response hash")
        return self


class Step2BExecutionRecord(StrictModel):
    request: Step2BRequest
    response: Step2BResponse
    contract_report: ContractValidationReport
    teaching_review: Step2TeachingReview
    provider_request_id: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    raw_response_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    rejected_response_forensics: Step2RejectedResponseForensics | None = None
    completed_at_utc: str

    @model_validator(mode="after")
    def validate_record(self) -> "Step2BExecutionRecord":
        if self.rejected_response_forensics is not None:
            if self.response.state != Step2ResultState.FAILED:
                raise ValueError("rejected-response forensics require a failed response")
            if self.rejected_response_forensics.request_id != self.request.identity.request_id:
                raise ValueError("rejected-response forensics must bind the execution request")
            if self.raw_response_sha256 != self.rejected_response_forensics.raw_response_sha256:
                raise ValueError("rejected-response forensics must bind the stored response hash")
        return self


class TransportCompletion(StrictModel):
    content: str
    provider_request_id: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    headers: dict[str, str] = {}
    finish_reason: str | None = None


class Step2Transport(Protocol):
    def complete(
        self,
        *,
        request: Step2ARequest | Step2BRequest,
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion: ...


class OpenAICompatibleStep2Transport:
    """Small transport wrapper with SDK retries disabled for auditable outcomes.

    Production-recipe options (P3 T2, both default-off so the existing behavior
    stays byte-identical):

    - ``stream=True`` aggregates an SSE response into the same
      ``TransportCompletion`` shape (defeats gateway first-byte/idle cutoffs on
      long generations; semantics ported verbatim from the pilot streaming
      transport).
    - ``system_suffix`` is appended to the end of the system message at message
      construction time (transport-layer reinforcement injection; the frozen
      prompt template body is never modified).

    Identity discipline: neither option participates in request-identity
    derivation (identities are computed upstream from the unmodified prompt
    template). Drivers must journal both (``streaming_transport`` /
    ``reinforcement_id``) in their run reports for auditability.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float,
        default_headers: Mapping[str, str] | None = None,
        stream: bool = False,
        system_suffix: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.network_call_count = 0
        self.stream = stream
        self.system_suffix = system_suffix
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=timeout,
            default_headers=default_headers or None,
            max_retries=0,
        )

    @staticmethod
    def _provider_request_id(headers: Mapping[str, str]) -> str | None:
        return headers.get("x-request-id") or headers.get("request-id")

    @staticmethod
    def _normalized_headers(raw_headers: Any) -> dict[str, str]:
        return {str(key).lower(): str(value) for key, value in raw_headers.items()}

    @staticmethod
    def _raw_http_status(raw: Any) -> int | None:
        """Read the SDK raw-response status without inferring it from success."""

        for candidate in (
            getattr(raw, "status_code", None),
            getattr(getattr(raw, "http_response", None), "status_code", None),
            getattr(getattr(raw, "_response", None), "status_code", None),
        ):
            if isinstance(candidate, int) and 100 <= candidate <= 599:
                return candidate
        return None

    def _raw_create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        response_format: Mapping[str, Any],
        idempotency_key: str | None,
    ) -> TransportCompletion:
        self.network_call_count += 1
        if self.stream:
            return self._raw_create_streaming(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                idempotency_key=idempotency_key,
            )
        raw = self._client.chat.completions.with_raw_response.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=dict(response_format),
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        headers = self._normalized_headers(raw.headers)
        response = raw.parse()
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        finish_reason = getattr(choice, "finish_reason", None)
        return TransportCompletion(
            content=content,
            provider_request_id=self._provider_request_id(headers),
            http_status=self._raw_http_status(raw),
            headers=headers,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )

    def _raw_create_streaming(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        response_format: Mapping[str, Any],
        idempotency_key: str | None,
    ) -> TransportCompletion:
        """SSE aggregation: join deltas into a TransportCompletion equivalent to the non-streaming shape.

        Ported verbatim from the pilot ``StreamingStep2Transport`` (probes and
        business calls share this single ``_raw_create`` seam). Besides the wire
        transfer mode nothing about the request changes (model, decoder
        parameters and response_format stay identical). The last chunk's
        ``finish_reason`` is preserved so a mid-stream cutoff (aggregate without
        ``finish_reason="stop"``) remains distinguishable in audits.
        """

        raw = self._client.chat.completions.with_raw_response.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=dict(response_format),
            stream=True,
            extra_headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        headers = self._normalized_headers(raw.headers)
        stream = raw.parse()
        parts: list[str] = []
        finish_reason: str | None = None
        try:
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or ()
                if choices:
                    delta = getattr(choices[0].delta, "content", None)
                    if delta:
                        parts.append(delta)
                    chunk_finish = getattr(choices[0], "finish_reason", None)
                    if chunk_finish is not None:
                        finish_reason = str(chunk_finish)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        return TransportCompletion(
            content="".join(parts).strip(),
            provider_request_id=self._provider_request_id(headers),
            http_status=self._raw_http_status(raw),
            headers=headers,
            finish_reason=finish_reason,
        )

    def complete(
        self,
        *,
        request: Step2ARequest | Step2BRequest,
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion:
        return self.complete_payload(
            identity=request.identity,
            model_payload=self.model_payload(request),
            response_schema=response_schema,
            prompt=prompt,
            use_idempotency_key=use_idempotency_key,
        )

    def complete_payload(
        self,
        *,
        identity: Step2RequestIdentity,
        model_payload: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion:
        """Submit a versioned custom contract without widening the V1 path.

        V2 has a few deliberately isolated contracts whose model input is not a
        ``Step2ARequest`` or ``Step2BRequest``.  They still need the same
        no-retry OpenAI-compatible transport, status telemetry and idempotency
        behavior.  Callers remain responsible for their local response Gate.
        """

        decoder = identity.decoder
        if decoder.response_format == Step2ResponseFormat.JSON_SCHEMA_STRICT:
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{identity.stage.value}_result",
                    "strict": True,
                    "schema": dict(response_schema),
                },
            }
        else:
            response_format = {"type": "json_object"}
        messages = self._messages_for_model_payload(
            prompt=self._system_prompt_with_suffix(prompt), model_payload=model_payload
        )
        return self._raw_create(
            model=identity.model,
            messages=messages,
            max_tokens=decoder.max_tokens,
            temperature=decoder.temperature,
            response_format=response_format,
            idempotency_key=identity.idempotency_key if use_idempotency_key else None,
        )

    @staticmethod
    def model_payload(request: Step2ARequest | Step2BRequest) -> dict[str, Any]:
        """Project audit-rich requests into the smallest model-visible shape."""

        if isinstance(request, Step2BRequest):
            return request.model_dump(mode="json", exclude={"identity"})
        spans_by_parent: dict[str, list[dict[str, str]]] = {}
        for projection in request.source_projections:
            for span in projection.anchor_spans:
                spans_by_parent.setdefault(span.parent_evidence_id, []).append(
                    {"anchor_span_id": span.anchor_span_id, "text": span.canonical_text}
                )

        sections: list[dict[str, Any]] = []
        for section in request.sections:
            diagrams = []
            for diagram in section.controlled_diagrams:
                diagrams.append(
                    {
                        "review_id": diagram.review_id,
                        "title": diagram.title,
                        "diagram_type": diagram.diagram_type,
                        "nodes": [
                            {
                                "node_id": node.node_id,
                                "label": node.label,
                                "anchor_span_ids": [
                                    span["anchor_span_id"]
                                    for evidence_id in node.evidence_ids
                                    for span in spans_by_parent.get(evidence_id, ())
                                ],
                            }
                            for node in diagram.nodes
                        ],
                        "edges": [
                            {
                                "source_node_id": edge.source_node_id,
                                "target_node_id": edge.target_node_id,
                                "relation": edge.relation,
                                "basis": edge.basis,
                                "confidence": edge.confidence,
                                "anchor_span_ids": [
                                    span["anchor_span_id"]
                                    for evidence_id in edge.evidence_ids
                                    for span in spans_by_parent.get(evidence_id, ())
                                ],
                            }
                            for edge in diagram.edges
                        ],
                    }
                )
            sections.append(
                {
                    "section_id": section.section_id,
                    "page_num": section.page_num,
                    "text_evidence": [
                        {
                            "kind": item.kind.value,
                            "context_only": item.context_only,
                            "anchor_spans": spans_by_parent[item.evidence_id],
                        }
                        for item in section.text_evidence
                    ],
                    "tables": [
                        {
                            "table_evidence_id": table.table_evidence_id,
                            "row_count": table.row_count,
                            "column_count": table.column_count,
                            "requires_complete_composition": True,
                            "complete_cell_anchor_span_ids": [
                                span["anchor_span_id"]
                                for row in table.rows
                                for cell in row.cells
                                for span in spans_by_parent[cell.evidence_id]
                            ],
                            "atomic_subfact_requirements": [
                                {
                                    "requirement_id": requirement.requirement_id,
                                    "expected_fact_type": requirement.expected_fact_type.value,
                                    "required_anchor_span_id": requirement.anchor_span_id,
                                    "required_assertion": requirement.required_assertion,
                                }
                                for requirement in table.atomic_subfact_requirements
                            ],
                            "rows": [
                                {
                                    "row_index": row.row_index,
                                    "cells": [
                                        {
                                            "row_index": cell.row_index,
                                            "column_index": cell.column_index,
                                            "row_span": cell.row_span,
                                            "col_span": cell.col_span,
                                            "anchor_spans": spans_by_parent[cell.evidence_id],
                                        }
                                        for cell in row.cells
                                    ],
                                }
                                for row in table.rows
                            ],
                        }
                        for table in section.tables
                    ],
                    "controlled_diagrams": diagrams,
                }
            )
        payload = {
            "sample_id": request.sample_id,
            "material_profile_id": request.material_profile_id,
            "heading_only_allowed_anchor_span_ids": list(request.heading_only_allowed_anchor_span_ids),
            "continuation_records": [
                {
                    "continuation_id": record.continuation_id,
                    "state": record.state.value,
                    "anchor_span_ids": list(record.anchor_span_ids),
                    "required_adjacent_page_num": record.required_adjacent_page_num,
                }
                for projection in request.source_projections
                for record in projection.continuation_records
            ],
            "sections": sections,
        }
        if request.continuation_bindings:
            payload["continuation_bindings"] = [
                {
                    "binding_id": binding.binding_id,
                    "continuation_id": binding.continuation_id,
                    "state": binding.state.value,
                    "source_section_id": binding.source_section_id,
                    "source_anchor_span_ids": list(binding.source_anchor_span_ids),
                    "adjacent_page_num": binding.adjacent_page_num,
                    "adjacent_section_id": binding.adjacent_section_id,
                    "closure_anchor_span_ids": list(binding.closure_anchor_span_ids),
                    "closure_basis": binding.closure_basis,
                    "failure_code": binding.failure_code.value if binding.failure_code is not None else None,
                }
                for binding in request.continuation_bindings
            ]
        if request.continuation_review_sidecar is not None:
            payload["reviewed_continuation_sidecar"] = {
                "sidecar_id": request.continuation_review_sidecar.sidecar_id,
                "sidecar_sha256": request.continuation_review_sidecar_sha256,
                "marker_dispositions": [
                    {
                        "continuation_id": item.continuation_id,
                        "disposition": item.disposition.value,
                        "anchor_span_ids": list(item.anchor_span_ids),
                    }
                    for item in request.continuation_review_sidecar.marker_reviews
                ],
                "closure_review_ids": [item.review_id for item in request.continuation_review_sidecar.closure_reviews],
            }
        if request.coverage_obligation_context_input is not None:
            context_input = request.coverage_obligation_context_input
            payload["coverage_obligation_context_input"] = {
                "input_version": context_input.input_version,
                "input_id": context_input.input_id,
                "contexts": [
                    {
                        "context_id": context.context_id,
                        "obligation_id": context.obligation_id,
                        "kind": context.kind.value,
                        "expected_fact_type": context.expected_fact_type.value,
                        "required_primary_anchor_span_ids": list(context.required_primary_anchor_span_ids),
                        "requires_all_items": context.requires_all_items,
                        "table_evidence_id": context.table_evidence_id,
                        "table_row_count": context.table_row_count,
                        "table_column_count": context.table_column_count,
                        "required_source_assertion": context.required_source_assertion,
                        "anchor_contexts": [item.model_dump(mode="json") for item in context.anchor_contexts],
                    }
                    for context in context_input.contexts
                ],
            }
        elif request.coverage_obligation_input is not None:
            payload["coverage_obligation_input"] = {
                "input_version": request.coverage_obligation_input.input_version,
                "input_id": request.coverage_obligation_input.input_id,
                "obligations": [
                    {
                        "obligation_id": item.obligation_id,
                        "kind": item.kind.value,
                        "expected_fact_type": item.expected_fact_type.value,
                        "required_primary_anchor_span_ids": list(item.required_primary_anchor_span_ids),
                        "requires_all_items": item.requires_all_items,
                    }
                    for item in request.coverage_obligation_input.obligations
                ],
            }
        return payload

    def _system_prompt_with_suffix(self, prompt: str) -> str:
        """Append the optional reinforcement suffix at message-construction time.

        The suffix is applied after the identity/prompt fingerprint has been
        verified upstream, so request identities keep binding the unmodified
        template (pilot-calibrated discipline: reinforcement is injected at the
        transport layer and never enters identity derivation).
        """

        return prompt if self.system_suffix is None else prompt + self.system_suffix

    @staticmethod
    def _request_messages(
        *, request: Step2ARequest | Step2BRequest, prompt: str
    ) -> list[dict[str, Any]]:
        return OpenAICompatibleStep2Transport._messages_for_model_payload(
            prompt=prompt,
            model_payload=OpenAICompatibleStep2Transport.model_payload(request),
        )

    @staticmethod
    def _messages_for_model_payload(*, prompt: str, model_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "Return only the required JSON object for this immutable request data:\n"
                + json.dumps(dict(model_payload), ensure_ascii=False),
            },
        ]

    def probe_response_format(self, *, model: str) -> dict[str, Any]:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }
        try:
            completion = self._raw_create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {"role": "user", "content": "Return {\"ok\": true}."},
                ],
                max_tokens=64,
                temperature=0,
                response_format={"type": "json_schema", "json_schema": {"name": "step2_probe", "strict": True, "schema": schema}},
                idempotency_key=None,
            )
            parsed = json.loads(completion.content)
            return {
                "supported": parsed == {"ok": True},
                "provider_request_id": completion.provider_request_id,
                "raw_response_sha256": _sha256(completion.content),
                "error": None,
            }
        except Exception as exc:  # Capability probes must report incompatibility, not hide it.
            return {"supported": False, "provider_request_id": None, "raw_response_sha256": None, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}

    def probe_json_schema(
        self,
        *,
        model: str,
        schema_name: str,
        response_schema: Mapping[str, Any],
        user_content: str,
        max_tokens: int = 400,
    ) -> TransportCompletion:
        """Perform one no-retry strict-schema probe for a concrete V2 contract."""
        return self._raw_create(
            model=model,
            messages=[
                {"role": "system", "content": "Return only strict JSON conforming to the supplied response schema."},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": dict(response_schema)},
            },
            idempotency_key=None,
        )

    def probe_idempotency(self, *, model: str, idempotency_key: str) -> dict[str, Any]:
        response_format = {"type": "json_object"}
        messages = [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": "Return {\"probe\": true}."},
        ]
        records: list[dict[str, Any]] = []
        try:
            for _ in range(2):
                completion = self._raw_create(
                    model=model,
                    messages=messages,
                    max_tokens=64,
                    temperature=0,
                    response_format=response_format,
                    idempotency_key=idempotency_key,
                )
                replay_value = next(
                    (
                        completion.headers.get(name)
                        for name in ("idempotency-replayed", "idempotent-replayed", "x-idempotency-replayed")
                        if completion.headers.get(name) is not None
                    ),
                    None,
                )
                records.append(
                    {
                        "provider_request_id": completion.provider_request_id,
                        "idempotency_replayed": replay_value is not None and replay_value.lower() in {"1", "true", "yes", "replayed"},
                        "response_header_evidence": {
                            name: value
                            for name, value in completion.headers.items()
                            if name in {"idempotency-replayed", "idempotent-replayed", "x-idempotency-replayed", "x-request-id"}
                        },
                    }
                )
            return {"supported": records[1]["idempotency_replayed"] is True, "records": records, "error": None}
        except Exception as exc:
            return {"supported": False, "records": records, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


class CallableStep2Transport:
    """Test-only transport adapter; no network implementation is hidden here."""

    def __init__(self, callback: Callable[..., TransportCompletion]) -> None:
        self._callback = callback

    def complete(
        self,
        *,
        request: Step2ARequest | Step2BRequest,
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion:
        return self._callback(
            request=request,
            response_schema=response_schema,
            prompt=prompt,
            use_idempotency_key=use_idempotency_key,
        )


def _append_event(path: Path, event: Step2JournalEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(event.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class Step2OpenAIAdapter:
    """Persisted V2-only execution with cache, journal and unknown-outcome safety."""

    def __init__(
        self,
        *,
        storage_root: Path,
        transport: Step2Transport,
        idempotency_supported: bool,
    ) -> None:
        self.storage_root = Path(storage_root)
        self.transport = transport
        self.idempotency_supported = idempotency_supported

    @property
    def _journal_path(self) -> Path:
        return self.storage_root / "telemetry" / "journal.jsonl"

    def _record_path(self, request_id: str) -> Path:
        return self.storage_root / "responses" / f"{request_id.replace(':', '_')}.json"

    def _events_for_request(self, request_id: str) -> tuple[Step2JournalEvent, ...]:
        if not self._journal_path.is_file():
            return ()
        events: list[Step2JournalEvent] = []
        for line in self._journal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = Step2JournalEvent.model_validate_json(line)
            if event.request.request_id == request_id:
                events.append(event)
        return tuple(events)

    def _append(
        self,
        *,
        outcome: Step2JournalOutcome,
        request: Step2RequestIdentity,
        provider_request_id: str | None = None,
        http_status: int | None = None,
        error: Exception | None = None,
        reconciled_terminal_outcome: Step2JournalOutcome | None = None,
    ) -> None:
        ordinal = len(self._events_for_request(request.request_id)) + 1
        event = Step2JournalEvent(
            event_id=_make_event_id(request_id=request.request_id, outcome=outcome, ordinal=ordinal),
            recorded_at_utc=_utc_now(),
            outcome=outcome,
            request=request,
            provider_request_id=provider_request_id,
            http_status=http_status,
            error_type=type(error).__name__ if error else None,
            error_message=str(error)[:500] if error else None,
            reconciled_terminal_outcome=reconciled_terminal_outcome,
        )
        _append_event(self._journal_path, event)

    @staticmethod
    def _record_terminal_outcome(record: Step2AExecutionRecord | Step2BExecutionRecord) -> Step2JournalOutcome:
        if record.response.state == Step2ResultState.SUCCEEDED:
            return Step2JournalOutcome.SUCCESS
        if record.response.failure and record.response.failure.state == Step2FailureState.UNKNOWN_OUTCOME:
            return Step2JournalOutcome.UNKNOWN_OUTCOME
        return Step2JournalOutcome.FAILED

    def _reconcile_terminal_journal(
        self,
        *,
        request: Step2RequestIdentity,
        record: Step2AExecutionRecord | Step2BExecutionRecord,
        events: tuple[Step2JournalEvent, ...],
    ) -> None:
        expected = self._record_terminal_outcome(record)
        terminal_outcomes = {
            Step2JournalOutcome.SUCCESS,
            Step2JournalOutcome.FAILED,
            Step2JournalOutcome.UNKNOWN_OUTCOME,
        }
        terminal_events = [
            event
            for event in events
            if event.outcome in terminal_outcomes or event.outcome == Step2JournalOutcome.RECONCILED_TERMINAL
        ]
        if not terminal_events:
            self._append(
                outcome=Step2JournalOutcome.RECONCILED_TERMINAL,
                request=request,
                reconciled_terminal_outcome=expected,
            )
            return
        for event in terminal_events:
            observed = event.reconciled_terminal_outcome if event.outcome == Step2JournalOutcome.RECONCILED_TERMINAL else event.outcome
            if observed != expected:
                raise ValueError("cached Step 2 record terminal state conflicts with its journal")

    @staticmethod
    def _failed_a(request: Step2ARequest, state: Step2FailureState, message: str) -> Step2AResponse:
        return Step2AResponse(request=request.identity, state=Step2ResultState.FAILED, failure=Step2AFailure(state=state, message=message))

    @staticmethod
    def _failed_b(request: Step2BRequest, state: Step2FailureState, message: str) -> Step2BResponse:
        return Step2BResponse(request=request.identity, state=Step2ResultState.FAILED, failure=Step2AFailure(state=state, message=message))

    @staticmethod
    def _empty_review(*, basis: str) -> Step2TeachingReview:
        return Step2TeachingReview(
            gate=TeachingQualityGate(
                evaluation_state=TeachingQualityEvaluationState.NOT_EVALUATED,
                title_failures=0,
                table_structure_failures=0,
                diagram_semantic_failures=0,
                heading_only_fact_failures=0,
                manual_review_items=0,
                spatial_watermark_candidates=0,
            ),
            basis=basis,
        )

    @staticmethod
    def _step2a_review(request: Step2ARequest, response: Step2AResponse) -> Step2TeachingReview:
        source_text: dict[str, str] = {}
        context_only_span_ids: set[str] = set()
        for section in request.sections:
            source_text.update({span.anchor_span_id: span.canonical_text for span in section.anchor_spans})
            context_only_span_ids.update(
                span.anchor_span_id
                for item in section.text_evidence
                if item.context_only
                for span in item.anchor_spans
            )
        for controlled in request.table_continuation_inputs:
            source_text.update({span.anchor_span_id: span.canonical_text for span in controlled.anchor_spans})
        titles = tuple(
            assess_title_quality(
                title=proposal.label,
                evidence_texts=tuple(source_text.get(span_id, "") for span_id in proposal.primary_anchor_span_ids),
            )
            for proposal in response.proposals
        )
        heading_only = tuple(
            assess_heading_only_fact(
                proposal_key=proposal.proposal_key,
                selected_anchor_span_ids=(*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids),
                context_only_anchor_span_ids=context_only_span_ids,
                allowed_heading_only_anchor_span_ids=request.heading_only_allowed_anchor_span_ids,
            )
            for proposal in response.proposals
        )
        return Step2TeachingReview(
            gate=build_teaching_quality_gate(titles=titles, heading_only_facts=heading_only),
            title_results=titles,
            heading_only_fact_results=heading_only,
            basis="Step 2A proposal titles and heading-only anchors were checked against immutable Evidence; review state remains draft.",
        )

    @staticmethod
    def _step2b_review(request: Step2BRequest, response: Step2BResponse) -> Step2TeachingReview:
        facts = {fact.fact_id: fact for fact in request.facts}
        titles: list[TitleQualityResult] = []
        granularities: list[CardGranularityResult] = []
        for proposal in response.proposals:
            primary = tuple(facts[fact_id] for fact_id in proposal.primary_fact_ids if fact_id in facts)
            titles.append(assess_title_quality(title=proposal.title, evidence_texts=(fact.label for fact in primary), short_card_exception=proposal.short_card_exception))
            granularities.append(
                assess_card_granularity(
                    content="\n".join(fact.assertion_summary for fact in primary),
                    fact_types=(fact.fact_type for fact in primary),
                    short_card_exception=proposal.short_card_exception,
                )
            )
        return Step2TeachingReview(
            gate=build_teaching_quality_gate(titles=titles, granularities=granularities),
            title_results=tuple(titles),
            granularity_results=tuple(granularities),
            basis="Step 2B plan titles and typed short-card exceptions were checked; review state remains draft.",
        )

    @staticmethod
    def _json_payload(content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("model response must be a JSON object")
        return parsed

    def _is_unknown_outcome(self, error: Exception) -> bool:
        """Conservatively quarantine requests whose upstream execution is unknowable.

        A 5xx response is not proof that the relay did not forward the request.
        Without verified relay idempotency, it therefore cannot be retried or
        classified as a terminal model failure.
        """
        if isinstance(error, (APITimeoutError, APIConnectionError, TimeoutError, ConnectionError)):
            return True
        return (
            not self.idempotency_supported
            and isinstance(error, APIStatusError)
            and error.status_code >= 500
        )

    @staticmethod
    def _failure_state(error: Exception, *, unknown_outcome: bool) -> Step2FailureState:
        if unknown_outcome:
            return Step2FailureState.UNKNOWN_OUTCOME
        if isinstance(error, APIStatusError):
            return Step2FailureState.UPSTREAM_UNAVAILABLE
        return Step2FailureState.INVALID_RESPONSE

    @staticmethod
    def _error_http_status(error: Exception) -> int | None:
        status = getattr(error, "status_code", None)
        return status if isinstance(status, int) and 100 <= status <= 599 else None

    @staticmethod
    def _rejected_response_forensics(
        *,
        request: Step2ARequest | Step2BRequest,
        raw_response_sha256: str,
        rejection_code: str,
        error: Exception | None = None,
    ) -> Step2RejectedResponseForensics:
        error_types: tuple[str, ...] = ()
        locations: tuple[Step2SchemaErrorLocation, ...] = ()
        if isinstance(error, ValidationError):
            parsed_errors = error.errors()
            error_types = tuple(sorted({str(item.get("type", "validation_error")) for item in parsed_errors}))
            locations = tuple(
                Step2SchemaErrorLocation(
                    path=tuple(
                        Step2OpenAIAdapter._safe_schema_location_part(part)
                        for part in (item.get("loc") or ("__root__",))
                    ),
                    error_type=str(item.get("type", "validation_error")),
                )
                for item in parsed_errors
            )
        elif error is not None:
            error_types = (type(error).__name__,)
        return Step2RejectedResponseForensics(
            request_id=request.identity.request_id,
            raw_response_sha256=raw_response_sha256,
            rejection_code=rejection_code,
            error_types=error_types,
            schema_error_locations=locations,
            schema_error_count=len(locations),
            missing_field_count=sum(location.error_type == "missing" for location in locations),
            extra_field_count=sum(location.error_type == "extra_forbidden" for location in locations),
        )

    @staticmethod
    def _safe_schema_location_part(value: Any) -> str | int:
        if isinstance(value, int):
            return value
        text = str(value)
        if len(text) <= 80 and all(char.isascii() and (char.isalnum() or char in "_:-[]") for char in text):
            return text
        return f"field_sha256:{_sha256(text)[:16]}"

    @staticmethod
    def _safe_rejected_response_message(rejection_code: str) -> str:
        return f"model response rejected by local {rejection_code}; inspect safe forensic metadata by request identity"

    @staticmethod
    def _verified_prompt(
        request: Step2ARequest | Step2BRequest,
        prompt_template: str | None,
    ) -> str:
        prompt = prompt_template or default_step2_prompt_template(
            request.identity.stage,
            request_version=request.identity.request_version,
        )
        if fingerprint_step2_prompt(prompt) != request.identity.prompt_sha256:
            raise ValueError("Step 2 execution prompt does not match the request identity")
        return prompt

    def _resume_record_a(self, request: Step2ARequest) -> Step2AExecutionRecord | None:
        path = self._record_path(request.identity.request_id)
        events = self._events_for_request(request.identity.request_id)
        if path.is_file():
            record = Step2AExecutionRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if record.request.identity != request.identity:
                raise ValueError("cached Step 2A response identity mismatch")
            self._reconcile_terminal_journal(request=request.identity, record=record, events=events)
            self._append(outcome=Step2JournalOutcome.CACHE_HIT, request=request.identity)
            return record
        if any(event.outcome == Step2JournalOutcome.REQUEST_STARTED for event in events) and not any(
            event.outcome in {Step2JournalOutcome.SUCCESS, Step2JournalOutcome.FAILED, Step2JournalOutcome.UNKNOWN_OUTCOME} for event in events
        ):
            response = self._failed_a(request, Step2FailureState.UNKNOWN_OUTCOME, "prior request was journaled without a durable terminal response")
            record = Step2AExecutionRecord(
                request=request,
                response=response,
                contract_report=ContractValidationReport(stage=request.identity.stage, valid=False, errors=("unknown_outcome",)),
                teaching_review=self._empty_review(basis="request recovery stopped before any automatic retry"),
                completed_at_utc=_utc_now(),
            )
            _atomic_write_json(path, record.model_dump(mode="json", exclude_computed_fields=True))
            self._append(outcome=Step2JournalOutcome.UNKNOWN_OUTCOME, request=request.identity)
            return record
        return None

    def _resume_record_b(self, request: Step2BRequest) -> Step2BExecutionRecord | None:
        path = self._record_path(request.identity.request_id)
        events = self._events_for_request(request.identity.request_id)
        if path.is_file():
            record = Step2BExecutionRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if record.request.identity != request.identity:
                raise ValueError("cached Step 2B response identity mismatch")
            self._reconcile_terminal_journal(request=request.identity, record=record, events=events)
            self._append(outcome=Step2JournalOutcome.CACHE_HIT, request=request.identity)
            return record
        if any(event.outcome == Step2JournalOutcome.REQUEST_STARTED for event in events) and not any(
            event.outcome in {Step2JournalOutcome.SUCCESS, Step2JournalOutcome.FAILED, Step2JournalOutcome.UNKNOWN_OUTCOME} for event in events
        ):
            response = self._failed_b(request, Step2FailureState.UNKNOWN_OUTCOME, "prior request was journaled without a durable terminal response")
            record = Step2BExecutionRecord(
                request=request,
                response=response,
                contract_report=ContractValidationReport(stage=request.identity.stage, valid=False, errors=("unknown_outcome",)),
                teaching_review=self._empty_review(basis="request recovery stopped before any automatic retry"),
                completed_at_utc=_utc_now(),
            )
            _atomic_write_json(path, record.model_dump(mode="json", exclude_computed_fields=True))
            self._append(outcome=Step2JournalOutcome.UNKNOWN_OUTCOME, request=request.identity)
            return record
        return None

    def complete_step2a(
        self,
        request: Step2ARequest,
        *,
        prompt_template: str | None = None,
    ) -> Step2AExecutionRecord:
        resumed = self._resume_record_a(request)
        if resumed is not None:
            return resumed
        prompt = self._verified_prompt(request, prompt_template)
        self._append(outcome=Step2JournalOutcome.REQUEST_STARTED, request=request.identity)
        completion: TransportCompletion | None = None
        try:
            completion = self.transport.complete(
                request=request,
                response_schema=step2a_result_payload_model(request.identity.request_version).model_json_schema(),
                prompt=prompt,
                use_idempotency_key=self.idempotency_supported,
            )
            raw_response_sha256 = _sha256(completion.content)
            payload = step2a_result_payload_model(request.identity.request_version).model_validate(
                self._json_payload(completion.content)
            )
            selected = derive_step2a_anchor_span_selection(request=request, proposals=payload.proposals)
            canonical = derive_step2a_table_cell_anchors(request=request, proposals=selected.proposals)
            selection_errors = tuple(
                f"{audit.proposal_key}: anchor span selection {audit.state.value}: {audit.basis}"
                for audit in selected.audits
                if audit.state.value != "derived"
            )
            if selection_errors:
                conditions = None
                numeric_derivation = None
                coverage_obligation_forensics = ()
                response = self._failed_a(
                    request,
                    Step2FailureState.GROUNDING_FAILED,
                    self._safe_rejected_response_message("grounding_contract_failed"),
                )
                contract = ContractValidationReport(stage=request.identity.stage, valid=False, errors=selection_errors)
            else:
                numeric_derivation = (
                    derive_step2a_numeric_claims(request=request, proposals=canonical.proposals)
                    if request.identity.request_version
                    in {
                        DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
                        TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
                        TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
                        TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
                        TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                        STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
                    }
                    else None
                )
                proposals_after_numeric = numeric_derivation.proposals if numeric_derivation is not None else canonical.proposals
                conditions = derive_step2a_condition_claims(request=request, proposals=proposals_after_numeric)
                response = Step2AResponse(
                    request=request.identity,
                    state=Step2ResultState.SUCCEEDED,
                    proposals=conditions.proposals,
                )
                contract = validate_step2a_response(request=request, response=response)
                coverage_obligation_forensics = derive_step2a_coverage_obligation_forensics(
                    request=request, proposals=conditions.proposals
                )
            rejected_response_forensics: Step2RejectedResponseForensics | None = None
            if not contract.valid:
                rejected_response_forensics = self._rejected_response_forensics(
                    request=request,
                    raw_response_sha256=raw_response_sha256,
                    rejection_code="grounding_contract_failed",
                )
                response = self._failed_a(
                    request,
                    Step2FailureState.GROUNDING_FAILED,
                    self._safe_rejected_response_message("grounding_contract_failed"),
                )
            review = self._step2a_review(request, response) if response.state == Step2ResultState.SUCCEEDED else self._empty_review(basis="invalid model output was not eligible for teaching review")
            record = Step2AExecutionRecord(
                request=request,
                response=response,
                contract_report=contract,
                teaching_review=review,
                table_cell_anchor_audit=canonical.audits,
                anchor_span_selection_audit=selected.audits,
                numeric_claim_derivation_audit=numeric_derivation.audits if numeric_derivation is not None else (),
                table_numeric_occurrence_audit=(
                    numeric_derivation.table_occurrence_audits if numeric_derivation is not None else ()
                ),
                table_structural_index_occurrence_audit=(
                    numeric_derivation.table_structural_index_occurrence_audits
                    if numeric_derivation is not None
                    else ()
                ),
                table_structural_projection_audit=(
                    numeric_derivation.table_structural_projection_audits
                    if numeric_derivation is not None
                    else ()
                ),
                condition_claim_audit=conditions.audits if conditions is not None else (),
                coverage_obligation_forensics=coverage_obligation_forensics,
                provider_request_id=completion.provider_request_id,
                http_status=completion.http_status,
                raw_response_sha256=raw_response_sha256,
                rejected_response_forensics=rejected_response_forensics,
                completed_at_utc=_utc_now(),
            )
            _atomic_write_json(
                self._record_path(request.identity.request_id),
                record.model_dump(mode="json", exclude_computed_fields=True),
            )
            self._append(
                outcome=Step2JournalOutcome.SUCCESS if response.state == Step2ResultState.SUCCEEDED else Step2JournalOutcome.FAILED,
                request=request.identity,
                provider_request_id=completion.provider_request_id,
                http_status=completion.http_status,
                error=ValueError("model response failed local grounding contract") if rejected_response_forensics else None,
            )
            return record
        except Exception as exc:  # Unknown transport completion must never be retried automatically.
            response_received = completion is not None
            unknown_outcome = False if response_received else self._is_unknown_outcome(exc)
            outcome = Step2JournalOutcome.UNKNOWN_OUTCOME if unknown_outcome else Step2JournalOutcome.FAILED
            state = Step2FailureState.INVALID_RESPONSE if response_received else self._failure_state(exc, unknown_outcome=unknown_outcome)
            raw_response_sha256 = _sha256(completion.content) if completion is not None else None
            rejected_response_forensics = (
                self._rejected_response_forensics(
                    request=request,
                    raw_response_sha256=raw_response_sha256,
                    rejection_code="payload_schema_validation_failed",
                    error=exc,
                )
                if raw_response_sha256 is not None
                else None
            )
            response = self._failed_a(
                request,
                state,
                self._safe_rejected_response_message("payload_schema_validation_failed")
                if rejected_response_forensics is not None
                else f"{type(exc).__name__}: {str(exc)[:500]}",
            )
            record = Step2AExecutionRecord(
                request=request,
                response=response,
                contract_report=ContractValidationReport(stage=request.identity.stage, valid=False, errors=(response.failure.message if response.failure else "unknown error",)),
                teaching_review=self._empty_review(basis="failed responses remain draft and do not promote reviews"),
                provider_request_id=completion.provider_request_id if completion is not None else None,
                http_status=completion.http_status if completion is not None else self._error_http_status(exc),
                raw_response_sha256=raw_response_sha256,
                rejected_response_forensics=rejected_response_forensics,
                completed_at_utc=_utc_now(),
            )
            _atomic_write_json(
                self._record_path(request.identity.request_id),
                record.model_dump(mode="json", exclude_computed_fields=True),
            )
            self._append(
                outcome=outcome,
                request=request.identity,
                provider_request_id=completion.provider_request_id if completion is not None else None,
                http_status=completion.http_status if completion is not None else self._error_http_status(exc),
                error=ValueError("model payload failed local schema validation") if rejected_response_forensics else exc,
            )
            return record

    def complete_step2b(
        self,
        request: Step2BRequest,
        *,
        prompt_template: str | None = None,
    ) -> Step2BExecutionRecord:
        resumed = self._resume_record_b(request)
        if resumed is not None:
            return resumed
        prompt = self._verified_prompt(request, prompt_template)
        self._append(outcome=Step2JournalOutcome.REQUEST_STARTED, request=request.identity)
        try:
            completion = self.transport.complete(
                request=request,
                response_schema=Step2BResultPayload.model_json_schema(),
                prompt=prompt,
                use_idempotency_key=self.idempotency_supported,
            )
            payload = Step2BResultPayload.model_validate(self._json_payload(completion.content))
            response = Step2BResponse(request=request.identity, state=Step2ResultState.SUCCEEDED, proposals=payload.proposals)
            contract = validate_step2b_response(request=request, response=response)
            if not contract.valid:
                response = self._failed_b(request, Step2FailureState.GROUNDING_FAILED, "; ".join(contract.errors))
            review = self._step2b_review(request, response) if response.state == Step2ResultState.SUCCEEDED else self._empty_review(basis="invalid model output was not eligible for teaching review")
            record = Step2BExecutionRecord(
                request=request,
                response=response,
                contract_report=contract,
                teaching_review=review,
                provider_request_id=completion.provider_request_id,
                http_status=completion.http_status,
                raw_response_sha256=_sha256(completion.content),
                completed_at_utc=_utc_now(),
            )
            _atomic_write_json(
                self._record_path(request.identity.request_id),
                record.model_dump(mode="json", exclude_computed_fields=True),
            )
            self._append(
                outcome=Step2JournalOutcome.SUCCESS if response.state == Step2ResultState.SUCCEEDED else Step2JournalOutcome.FAILED,
                request=request.identity,
                provider_request_id=completion.provider_request_id,
                http_status=completion.http_status,
            )
            return record
        except Exception as exc:
            unknown_outcome = self._is_unknown_outcome(exc)
            outcome = Step2JournalOutcome.UNKNOWN_OUTCOME if unknown_outcome else Step2JournalOutcome.FAILED
            state = self._failure_state(exc, unknown_outcome=unknown_outcome)
            response = self._failed_b(request, state, f"{type(exc).__name__}: {str(exc)[:500]}")
            record = Step2BExecutionRecord(
                request=request,
                response=response,
                contract_report=ContractValidationReport(stage=request.identity.stage, valid=False, errors=(response.failure.message if response.failure else "unknown error",)),
                teaching_review=self._empty_review(basis="failed responses remain draft and do not promote reviews"),
                http_status=self._error_http_status(exc),
                completed_at_utc=_utc_now(),
            )
            _atomic_write_json(
                self._record_path(request.identity.request_id),
                record.model_dump(mode="json", exclude_computed_fields=True),
            )
            self._append(
                outcome=outcome,
                request=request.identity,
                http_status=self._error_http_status(exc),
                error=exc,
            )
            return record
