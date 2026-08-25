"""step2a 离线替身与离线日志（拆分层 4）。

``build_v25_source_fake_response`` 按请求里的义务合成一份「正确答案」，供离线回放与黄金件
复算；它是替身而不是兜底：真跑链路一处都不调它。
"""
from __future__ import annotations

from ...evidence.schema import EvidenceKind
from ..schema import FactType

from . import Step2Stage

from .v25_versions import (
    V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION,
    V25_COST_FLOW_CANONICAL_STEP2A_JOURNAL_VERSION,
    V25_DIAGRAM_CANONICAL_STEP2A_JOURNAL_VERSION,
    V25_LIST_CANONICAL_STEP2A_JOURNAL_VERSION,
    V25_STANDARD_CANONICAL_STEP2A_JOURNAL_VERSION,
    V25_STEP2A_JOURNAL_VERSION,
)
from .v25_support import _label_from_source
from .v25_enums import V25Step2AContractProfile
from .v25_step2a_prompt import _section_anchor_map
from .v25_step2a_request import (
    V25JournalRecord,
    V25Step2AModelFactProposal,
    V25Step2ARequest,
    V25Step2AResultPayload,
)


def build_v25_source_fake_response(*, request: V25Step2ARequest) -> V25Step2AResultPayload:
    """Build a deterministic source-only fake response for offline contract coverage."""

    selectable = _section_anchor_map(request.sections)
    proposals: list[V25Step2AModelFactProposal] = []
    for obligation in request.coverage_obligations:
        texts = [selectable[anchor_id].canonical_text for anchor_id in obligation.required_primary_anchor_span_ids]
        assertion = "\n".join(texts).strip()
        label = _label_from_source(assertion)
        proposals.append(
            V25Step2AModelFactProposal(
                fact_type=obligation.expected_fact_type,
                label=label,
                assertion=assertion,
                primary_anchor_span_ids=obligation.required_primary_anchor_span_ids,
            )
        )
    for atomic in request.atomic_source_obligations:
        proposals.append(
            V25Step2AModelFactProposal(
                fact_type=atomic.expected_fact_type,
                label=_label_from_source(atomic.required_source_assertion),
                assertion=atomic.required_source_assertion,
                primary_anchor_span_ids=atomic.required_primary_anchor_span_ids,
            )
        )
    for text_obligation in request.text_source_obligations:
        proposals.append(
            V25Step2AModelFactProposal(
                fact_type=text_obligation.expected_fact_type,
                label=_label_from_source(text_obligation.model_assertion),
                assertion=text_obligation.model_assertion,
                primary_anchor_span_ids=text_obligation.required_primary_anchor_span_ids,
            )
        )
    for list_obligation in request.list_source_obligations:
        proposals.append(
            V25Step2AModelFactProposal(
                fact_type=list_obligation.expected_fact_type,
                label=_label_from_source(list_obligation.model_assertion),
                assertion=list_obligation.model_assertion,
                primary_anchor_span_ids=list_obligation.required_primary_anchor_span_ids,
            )
        )
    direct_candidates = [
        span
        for section in request.sections
        for item in section.text_evidence
        if not item.context_only
        for span in item.anchor_spans
    ]
    used = {anchor_id for proposal in proposals for anchor_id in proposal.primary_anchor_span_ids}
    direct = next((span for span in direct_candidates if span.anchor_span_id not in used), None)
    if direct is not None:
        fact_type = (
            FactType.ENUMERATION
            if direct.parent_evidence_kind in {EvidenceKind.LIST_GROUP, EvidenceKind.LIST_ITEM}
            else FactType.FORMULA
            if direct.parent_evidence_kind == EvidenceKind.FORMULA
            else FactType.DEFINITION
        )
        proposals.append(
            V25Step2AModelFactProposal(
                fact_type=fact_type,
                label=_label_from_source(direct.canonical_text),
                assertion=direct.canonical_text,
                primary_anchor_span_ids=(direct.anchor_span_id,),
            )
        )
    if not proposals:
        raise ValueError("V25 source fake response requires at least one selectable source anchor")
    return V25Step2AResultPayload(proposals=tuple(proposals))


def make_v25_offline_journal(*, request: V25Step2ARequest) -> V25JournalRecord:
    return V25JournalRecord(
        journal_version=(
            V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION
            if request.contract_profile == V25Step2AContractProfile.ATOMIC_SOURCE_CONTRACT
            else V25_DIAGRAM_CANONICAL_STEP2A_JOURNAL_VERSION
            if request.contract_profile == V25Step2AContractProfile.DIAGRAM_CANONICAL_PRECHECK
            else V25_LIST_CANONICAL_STEP2A_JOURNAL_VERSION
            if request.contract_profile == V25Step2AContractProfile.LIST_CANONICAL_PRECHECK
            else V25_STANDARD_CANONICAL_STEP2A_JOURNAL_VERSION
            if request.contract_profile == V25Step2AContractProfile.STANDARD_CANONICAL_PRECHECK
            else V25_COST_FLOW_CANONICAL_STEP2A_JOURNAL_VERSION
            if request.contract_profile == V25Step2AContractProfile.COST_FLOW_CANONICAL_PRECHECK
            else V25_STEP2A_JOURNAL_VERSION
        ),
        request_id=request.identity.request_id,
        stage=Step2Stage.FACT_PLANNING,
        event_cursor=0,
        event_type="offline_contract_preflight",
        payload_sha256=request.identity.payload_sha256,
        response_schema_sha256=request.identity.response_schema_sha256,
        network_calls=0,
        state="offline_contract_preflight",
    )
