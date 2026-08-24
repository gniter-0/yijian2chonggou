"""Step 2A/2B 的执行记录模型（拆分层 2）。

记录体自带强校验：成功态必须逐 proposal 配齐锚点与选段审计、且审计须与契约报告逐项相等；
失败态只有 grounding 失败才许保留审计残留；取证块必须同时绑定请求 id 与响应哈希。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from ..materials.schema import StrictModel

from .contracts import (
    ContractValidationReport,
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
    Step2FailureState,
    Step2ResultState,
)

from .llm_support import _SHA256_PATTERN
from .llm_review import Step2TeachingReview
from .llm_forensics import Step2RejectedResponseForensics


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
