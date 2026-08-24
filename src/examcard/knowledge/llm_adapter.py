"""Step 2A/2B 的审计优先执行器（拆分层 3）。

「发出去但没拿到持久响应」记为 ``unknown_outcome``，绝不自动重试；每次执行都落 journal
事件与记录文件，且从不改写 Evidence 或评审侧车。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError

from ..providers.base import sanitize_error_text

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
    Step2AFailure,
    Step2ARequest,
    Step2AResponse,
    Step2ACoverageObligationForensic,
    Step2BRequest,
    Step2BResponse,
    Step2BResultPayload,
    Step2FailureState,
    Step2RequestIdentity,
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
    TeachingQualityGate,
    TeachingQualityEvaluationState,
    TitleQualityResult,
    assess_card_granularity,
    assess_heading_only_fact,
    assess_title_quality,
    build_teaching_quality_gate,
)

from .llm_support import _atomic_write_json, _sanitized_error_message, _sha256, _utc_now
from .llm_journal import Step2JournalEvent, Step2JournalOutcome, _append_event, _make_event_id
from .llm_review import Step2TeachingReview
from .llm_forensics import Step2RejectedResponseForensics, Step2SchemaErrorLocation
from .llm_transport import Step2Transport, TransportCompletion
from .llm_records import Step2AExecutionRecord, Step2BExecutionRecord


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
            error_message=sanitize_error_text(str(error)) if error else None,
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
                # 形状 B：空元组会被推成 tuple[()]，另一支路赋真值就报 assignment。
                # 元素类型取自 Step2AExecutionRecord 同名字段的声明。
                coverage_obligation_forensics: tuple[Step2ACoverageObligationForensic, ...] = ()
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
            # 失败道另起一个可空的名字：try 道里同名量的首次绑定是 str（一定有 completion
            # 才走到那里），这里的取值是 str | None，沿用同名就报 assignment。改名后收窄由
            # 下面那句 is not None 承担，取值与控制流不变。
            failure_raw_response_sha256 = _sha256(completion.content) if completion is not None else None
            rejected_response_forensics = (
                self._rejected_response_forensics(
                    request=request,
                    raw_response_sha256=failure_raw_response_sha256,
                    rejection_code="payload_schema_validation_failed",
                    error=exc,
                )
                if failure_raw_response_sha256 is not None
                else None
            )
            response = self._failed_a(
                request,
                state,
                self._safe_rejected_response_message("payload_schema_validation_failed")
                if rejected_response_forensics is not None
                else _sanitized_error_message(exc),
            )
            record = Step2AExecutionRecord(
                request=request,
                response=response,
                contract_report=ContractValidationReport(stage=request.identity.stage, valid=False, errors=(response.failure.message if response.failure else "unknown error",)),
                teaching_review=self._empty_review(basis="failed responses remain draft and do not promote reviews"),
                provider_request_id=completion.provider_request_id if completion is not None else None,
                http_status=completion.http_status if completion is not None else self._error_http_status(exc),
                raw_response_sha256=failure_raw_response_sha256,
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
            response = self._failed_b(request, state, _sanitized_error_message(exc))
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
