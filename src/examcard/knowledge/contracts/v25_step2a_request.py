"""step2a 请求/结果/门禁/离线日志四类模型（拆分层 3；全家最大的单个符号在此）。

``V25Step2ARequest`` 一个类 13.6KB：它按六个侧写各自校验版本号三元组、核对 identity 的
``payload_sha256`` 与规范化载荷相等。这个类不再往下切——切它就不是搬运而是改设计了。
``V25JournalRecord`` 被 step2a 与 step2b 两侧的离线日志共用，故留在 step2a 侧不另起模块。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from ..schema import FactType

from . import (
    Step2ACoverageObligation,
    Step2ASectionInput,
    Step2RequestIdentity,
    Step2Stage,
    canonical_step2_sha256,
)

from .v25_versions import (
    V25_ATOMIC_SOURCE_STEP2A_MODEL_INPUT_VERSION,
    V25_ATOMIC_SOURCE_STEP2A_PROMPT_VERSION,
    V25_ATOMIC_SOURCE_STEP2A_REQUEST_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_LIST_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_LIST_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_LIST_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_PROMPT_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_REQUEST_VERSION,
    V25_STEP2A_MODEL_INPUT_VERSION,
    V25_STEP2A_PROMPT_VERSION,
    V25_STEP2A_REQUEST_VERSION,
)
from .v25_support import _SHA256_PATTERN
from .v25_enums import V25Step2AContractProfile
from .v25_step2a_obligations import (
    V25AtomicSourceObligation,
    V25CoverageObligationContext,
    V25ListSourceObligation,
    V25ReviewedContinuationBinding,
    V25TextSourceObligation,
)
from .v25_step2a_prompt import _section_anchor_map, _table_by_id, v25_step2a_request_payload


class V25Step2AModelFactProposal(StrictModel):
    """The V25 model response is intentionally semantic and anchor-only."""

    fact_type: FactType
    label: str = Field(min_length=2, max_length=96)
    assertion: str = Field(min_length=2, max_length=4000)
    primary_anchor_span_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_proposal(self) -> "V25Step2AModelFactProposal":
        if self.label != self.label.strip() or self.assertion != self.assertion.strip():
            raise ValueError("V25 Step 2A model text must be stripped")
        if len(self.primary_anchor_span_ids) != len(set(self.primary_anchor_span_ids)):
            raise ValueError("V25 Step 2A primary anchor spans must be unique")
        return self


class V25Step2AResultPayload(StrictModel):
    """Strict V25 JSON shape. Unknown fields are rejected by StrictModel."""

    proposals: tuple[V25Step2AModelFactProposal, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_payload(self) -> "V25Step2AResultPayload":
        fingerprints = [
            (item.fact_type.value, item.assertion, item.primary_anchor_span_ids)
            for item in self.proposals
        ]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("V25 Step 2A response cannot duplicate a Fact proposal")
        return self


class V25Step2ARequest(StrictModel):
    """Versioned generic V25 request with only source-owned model input."""

    identity: Step2RequestIdentity
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    material_profile_id: str = Field(min_length=1)
    source_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_manifest_sha256s: tuple[str, ...] = Field(min_length=1)
    projection_sha256s: tuple[str, ...] = Field(min_length=1)
    content_policy_id: str = Field(min_length=1)
    content_policy_revision: int = Field(ge=1)
    content_policy_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_profile: V25Step2AContractProfile = V25Step2AContractProfile.GENERIC_SOURCE_DERIVED
    model_input_version: str = V25_STEP2A_MODEL_INPUT_VERSION
    prompt_version: str = V25_STEP2A_PROMPT_VERSION
    sections: tuple[Step2ASectionInput, ...] = Field(min_length=1)
    coverage_obligations: tuple[Step2ACoverageObligation, ...] = ()
    coverage_contexts: tuple[V25CoverageObligationContext, ...] = ()
    atomic_source_obligations: tuple[V25AtomicSourceObligation, ...] = ()
    text_source_obligations: tuple[V25TextSourceObligation, ...] = ()
    list_source_obligations: tuple[V25ListSourceObligation, ...] = ()
    source_contract_projection_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    reviewed_continuation_bindings: tuple[V25ReviewedContinuationBinding, ...] = ()
    reviewed_table_repair_ids: tuple[str, ...] = ()
    table_repair_sidecar_sha256s: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_request(self) -> "V25Step2ARequest":
        profile_versions = {
            V25Step2AContractProfile.GENERIC_SOURCE_DERIVED: (
                V25_STEP2A_REQUEST_VERSION,
                V25_STEP2A_MODEL_INPUT_VERSION,
                V25_STEP2A_PROMPT_VERSION,
            ),
            V25Step2AContractProfile.ATOMIC_SOURCE_CONTRACT: (
                V25_ATOMIC_SOURCE_STEP2A_REQUEST_VERSION,
                V25_ATOMIC_SOURCE_STEP2A_MODEL_INPUT_VERSION,
                V25_ATOMIC_SOURCE_STEP2A_PROMPT_VERSION,
            ),
            V25Step2AContractProfile.DIAGRAM_CANONICAL_PRECHECK: (
                V25_DIAGRAM_CANONICAL_STEP2A_REQUEST_VERSION,
                V25_DIAGRAM_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
                V25_DIAGRAM_CANONICAL_STEP2A_PROMPT_VERSION,
            ),
            V25Step2AContractProfile.LIST_CANONICAL_PRECHECK: (
                V25_LIST_CANONICAL_STEP2A_REQUEST_VERSION,
                V25_LIST_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
                V25_LIST_CANONICAL_STEP2A_PROMPT_VERSION,
            ),
            V25Step2AContractProfile.STANDARD_CANONICAL_PRECHECK: (
                V25_STANDARD_CANONICAL_STEP2A_REQUEST_VERSION,
                V25_STANDARD_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
                V25_STANDARD_CANONICAL_STEP2A_PROMPT_VERSION,
            ),
            V25Step2AContractProfile.COST_FLOW_CANONICAL_PRECHECK: (
                V25_COST_FLOW_CANONICAL_STEP2A_REQUEST_VERSION,
                V25_COST_FLOW_CANONICAL_STEP2A_MODEL_INPUT_VERSION,
                V25_COST_FLOW_CANONICAL_STEP2A_PROMPT_VERSION,
            ),
        }
        request_version, model_input_version, prompt_version = profile_versions[self.contract_profile]
        if any(section.controlled_diagram_anchor_spans for section in self.sections):
            # Fail-closed until a V25 version family explicitly declares the
            # quarantined controlled-diagram anchor lane (follow-up card).
            raise ValueError("V25 request families do not admit controlled-diagram node anchor spans yet")
        if self.identity.stage != Step2Stage.FACT_PLANNING or self.identity.request_version != request_version:
            raise ValueError("V25 Step 2A request identity does not match its contract profile")
        if self.model_input_version != model_input_version:
            raise ValueError("V25 Step 2A model-input version mismatch")
        if self.prompt_version != prompt_version:
            raise ValueError("V25 Step 2A prompt version mismatch")
        if self.contract_profile == V25Step2AContractProfile.GENERIC_SOURCE_DERIVED:
            if (
                self.atomic_source_obligations
                or self.text_source_obligations
                or self.list_source_obligations
                or self.source_contract_projection_sha256 is not None
            ):
                raise ValueError("generic V25 requests cannot carry atomic-source contract state")
        elif self.contract_profile == V25Step2AContractProfile.ATOMIC_SOURCE_CONTRACT:
            if (
                not self.atomic_source_obligations
                or self.text_source_obligations
                or self.list_source_obligations
                or self.source_contract_projection_sha256 is None
            ):
                raise ValueError("atomic-source V25 requests require only atomic obligations and projection provenance")
        elif self.contract_profile == V25Step2AContractProfile.DIAGRAM_CANONICAL_PRECHECK and (
            self.atomic_source_obligations
            or not self.text_source_obligations
            or self.list_source_obligations
            or self.source_contract_projection_sha256 is None
        ):
            raise ValueError("diagram canonical V25 requests require source-text obligations and projection provenance")
        elif self.contract_profile == V25Step2AContractProfile.LIST_CANONICAL_PRECHECK and (
            self.atomic_source_obligations
            or self.text_source_obligations
            or not self.list_source_obligations
            or self.source_contract_projection_sha256 is None
        ):
            raise ValueError("list canonical V25 requests require source-list obligations and projection provenance")
        elif self.contract_profile == V25Step2AContractProfile.STANDARD_CANONICAL_PRECHECK and (
            self.atomic_source_obligations
            or not self.text_source_obligations
            or self.list_source_obligations
            or self.source_contract_projection_sha256 is None
        ):
            raise ValueError("standard canonical V25 requests require source-text obligations and projection provenance")
        elif self.contract_profile == V25Step2AContractProfile.COST_FLOW_CANONICAL_PRECHECK and (
            self.atomic_source_obligations
            or not self.text_source_obligations
            or not self.list_source_obligations
            or self.source_contract_projection_sha256 is None
        ):
            raise ValueError("cost-flow canonical V25 requests require source-text and source-list obligations")
        if len(self.source_manifest_sha256s) != len(self.sections) or len(self.projection_sha256s) != len(self.sections):
            raise ValueError("V25 Step 2A source manifest, projection, and section counts must match")
        for name, values in (
            ("source manifest hashes", self.source_manifest_sha256s),
            ("projection hashes", self.projection_sha256s),
            ("section IDs", tuple(section.section_id for section in self.sections)),
            ("reviewed table repair IDs", self.reviewed_table_repair_ids),
            ("table repair sidecar hashes", self.table_repair_sidecar_sha256s),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"V25 Step 2A {name} must be unique")
        binding_ids = [item.binding_id for item in self.reviewed_continuation_bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("V25 Step 2A reviewed continuation bindings must be unique")
        if bool(self.reviewed_table_repair_ids) != bool(self.table_repair_sidecar_sha256s):
            raise ValueError("V25 reviewed table repairs require sidecar hash provenance")
        obligations = {item.obligation_id: item for item in self.coverage_obligations}
        if len(obligations) != len(self.coverage_obligations):
            raise ValueError("V25 Step 2A coverage obligations must be unique")
        contexts = {item.obligation_id: item for item in self.coverage_contexts}
        if set(obligations) != set(contexts):
            raise ValueError("V25 Step 2A coverage contexts must exactly cover its obligations")
        selectable = _section_anchor_map(self.sections)
        atomic_ids = [item.obligation_id for item in self.atomic_source_obligations]
        if len(atomic_ids) != len(set(atomic_ids)):
            raise ValueError("V25 Step 2A atomic-source obligations must be unique")
        text_ids = [item.obligation_id for item in self.text_source_obligations]
        if len(text_ids) != len(set(text_ids)):
            raise ValueError("V25 Step 2A text-source obligations must be unique")
        list_ids = [item.obligation_id for item in self.list_source_obligations]
        if len(list_ids) != len(set(list_ids)):
            raise ValueError("V25 Step 2A list-source obligations must be unique")
        for binding in self.reviewed_continuation_bindings:
            if not set(binding.source_manifest_sha256s) <= set(self.source_manifest_sha256s):
                raise ValueError("V25 reviewed continuation binding must use request-local manifests")
            if any(anchor_id not in selectable for anchor_id in binding.anchor_span_ids):
                raise ValueError("V25 reviewed continuation binding must use request-local anchors")
        for obligation_id, obligation in obligations.items():
            context = contexts[obligation_id]
            if (
                context.kind != obligation.kind
                or context.expected_fact_type != obligation.expected_fact_type
                or context.required_primary_anchor_span_ids != obligation.required_primary_anchor_span_ids
                or context.table_evidence_id != obligation.table_evidence_id
            ):
                raise ValueError("V25 coverage context does not match its source-derived obligation")
            if any(anchor_id not in selectable for anchor_id in obligation.required_primary_anchor_span_ids):
                raise ValueError("V25 coverage obligation cannot reference a non-selectable anchor")
        for atomic in self.atomic_source_obligations:
            if atomic.table_evidence_id not in _table_by_id(self.sections):
                raise ValueError("V25 atomic-source obligation must retain its containing source table")
            if any(anchor_id not in selectable for anchor_id in atomic.required_primary_anchor_span_ids):
                raise ValueError("V25 atomic-source obligation cannot reference a non-selectable anchor")
            if any(anchor.source_manifest_sha256 not in self.source_manifest_sha256s for anchor in atomic.anchors):
                raise ValueError("V25 atomic-source obligation must retain request-local source manifests")
        for text_obligation in self.text_source_obligations:
            if any(anchor_id not in selectable for anchor_id in text_obligation.required_primary_anchor_span_ids):
                raise ValueError("V25 text-source obligation cannot reference a non-selectable anchor")
            if any(
                anchor.source_manifest_sha256 not in self.source_manifest_sha256s for anchor in text_obligation.anchors
            ):
                raise ValueError("V25 text-source obligation must retain request-local source manifests")
        tables = _table_by_id(self.sections)
        for list_obligation in self.list_source_obligations:
            table = tables.get(list_obligation.source_table_id)
            if table is None:
                raise ValueError("V25 list-source obligation must retain its source table")
            cell = next(
                (
                    item
                    for row in table.rows
                    for item in row.cells
                    if item.evidence_id == list_obligation.source_cell_evidence_id
                ),
                None,
            )
            if (
                cell is None
                or cell.row_index != list_obligation.source_row
                or cell.column_index != list_obligation.source_column
            ):
                raise ValueError("V25 list-source obligation must retain its exact source table cell")
            cell_anchor_ids = {item.anchor_span_id for item in cell.anchor_spans}
            if not set(list_obligation.required_primary_anchor_span_ids) <= cell_anchor_ids:
                raise ValueError("V25 list-source obligation cannot reference anchors outside its source cell")
            if any(anchor_id not in selectable for anchor_id in list_obligation.required_primary_anchor_span_ids):
                raise ValueError("V25 list-source obligation cannot reference a non-selectable anchor")
            if any(
                anchor.source_manifest_sha256 not in self.source_manifest_sha256s for anchor in list_obligation.anchors
            ):
                raise ValueError("V25 list-source obligation must retain request-local source manifests")
            reviewed_binding_ids = {item.binding_id for item in self.reviewed_continuation_bindings}
            if not set(list_obligation.continuation_binding_ids) <= reviewed_binding_ids:
                raise ValueError("V25 list-source obligation cannot claim an unreviewed continuation binding")
        payload = v25_step2a_request_payload(self)
        if self.identity.payload_sha256 != canonical_step2_sha256(payload):
            raise ValueError("V25 Step 2A request payload no longer matches its cache identity")
        return self


class V25Step2AGate(StrictModel):
    request_id: str = Field(pattern=r"^step2a-request:[0-9a-f]{64}$")
    valid: bool
    failure_codes: tuple[str, ...] = ()
    proposal_count: int = Field(ge=0)
    obligation_count: int = Field(ge=0)
    complete_obligation_count: int = Field(ge=0)
    source_anchor_linked: bool
    heading_only_failures: int = Field(ge=0)
    unsupported_diagram_fact_count: int = Field(ge=0)
    basis: str = Field(min_length=24, max_length=1600)


class V25JournalRecord(StrictModel):
    """One offline-only journal event with a distinct V25/V25B contract version."""

    journal_version: str
    request_id: str = Field(pattern=r"^step2[ab]-request:[0-9a-f]{64}$")
    stage: Step2Stage
    event_cursor: int = Field(ge=0)
    event_type: str = Field(pattern=r"^offline_[a-z0-9_]+$")
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    network_calls: int = Field(ge=0)
    state: str = Field(pattern=r"^offline_[a-z0-9_]+$")

    @model_validator(mode="after")
    def validate_offline(self) -> "V25JournalRecord":
        if self.network_calls != 0:
            raise ValueError("V25 offline journal records cannot report network calls")
        return self
