"""Persisted V2 Step 2A adapter tests for the anchor-span contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
from openai import InternalServerError

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.schema import EvidenceKind, PageEvidence
from examcard.knowledge.schema import FactType
from examcard.knowledge.contracts import (
    ModelNumericClaim,
    NumericTokenClass,
    Step2AModelFactProposal,
    Step2DecoderConfig,
    Step2ResponseFormat,
    Step2ResultState,
    build_step2a_request,
    classify_numeric_tokens,
    condition_operators_in_text,
)
from examcard.knowledge.llm import (
    CallableStep2Transport,
    OpenAICompatibleStep2Transport,
    Step2JournalOutcome,
    Step2OpenAIAdapter,
    TransportCompletion,
)
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.materials.catalog import load_content_policy


PAGE_PATH = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"


def _request_and_proposal():
    page = PageEvidence.model_validate_json(PAGE_PATH.read_text(encoding="utf-8"))
    built = build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=page,
        source_manifest_path=PAGE_PATH,
        source_manifest_reference="data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json",
        content_policy=load_content_policy(ROOT / "configs/v2/content_policies/core_only_v1.yaml"),
    )
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        decoder=Step2DecoderConfig(max_tokens=1000, response_format=Step2ResponseFormat.JSON_SCHEMA_STRICT),
    )
    span = next(
        span
        for span in built.projection.anchor_spans
        if span.parent_evidence_kind == EvidenceKind.TABLE_CELL and condition_operators_in_text(span.canonical_text)
    )
    proposal = Step2AModelFactProposal(
        proposal_key="proposal:adapter-span",
        source_section_ids=(request.sections[0].section_id,),
        fact_type=FactType.CONDITION,
        label="Adapter span rule",
        assertion=span.canonical_text,
        primary_anchor_span_ids=(span.anchor_span_id,),
    )
    return request, proposal


def _payload(proposal: Step2AModelFactProposal) -> dict:
    return proposal.model_dump(mode="json")


def _complete_table_proposals(request, *, skip_anchor_span_ids=()):
    skipped = set(skip_anchor_span_ids)
    compositions = tuple(
        Step2AModelFactProposal(
            proposal_key=f"proposal:complete-table-{section_index}-{table_index}",
            source_section_ids=(section.section_id,),
            fact_type=FactType.COMPOSITION,
            label="模板",
            assertion="模板",
            primary_anchor_span_ids=tuple(
                span.anchor_span_id
                for row in table.rows
                for cell in row.cells
                for span in cell.anchor_spans
            ),
        )
        for section_index, section in enumerate(request.sections)
        for table_index, table in enumerate(section.tables)
    )
    atomic = []
    for section in request.sections:
        for table in section.tables:
            for requirement in table.atomic_subfact_requirements:
                if requirement.anchor_span_id in skipped:
                    continue
                numeric_claims = ()
                if requirement.expected_fact_type == FactType.THRESHOLD:
                    numeric_claims = tuple(
                        ModelNumericClaim(value=token.value, anchor_span_id=requirement.anchor_span_id)
                        for token in classify_numeric_tokens(requirement.required_assertion)
                        if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
                    )
                atomic.append(
                    Step2AModelFactProposal(
                        proposal_key=f"proposal:atomic-{requirement.requirement_id.split(':', 1)[1]}",
                        source_section_ids=(section.section_id,),
                        fact_type=requirement.expected_fact_type,
                        label=requirement.required_assertion,
                        assertion=requirement.required_assertion,
                        primary_anchor_span_ids=(requirement.anchor_span_id,),
                        numeric_claims=numeric_claims,
                    )
                )
    return (*compositions, *atomic)


def test_adapter_persists_canonical_parent_and_span_audits_then_resumes(tmp_path):
    request, proposal = _request_and_proposal()
    calls: list[str] = []

    def callback(**kwargs):
        calls.append(kwargs["request"].identity.request_id)
        proposals = (
            proposal,
            *_complete_table_proposals(
                request,
                skip_anchor_span_ids=proposal.primary_anchor_span_ids,
            ),
        )
        return TransportCompletion(content=json.dumps({"proposals": [_payload(item) for item in proposals]}, ensure_ascii=False))

    adapter = Step2OpenAIAdapter(storage_root=tmp_path / "semantic", transport=CallableStep2Transport(callback), idempotency_supported=False)
    first = adapter.complete_step2a(request)
    resumed = adapter.complete_step2a(request)

    fact = first.response.proposals[0]
    assert first.response.state == Step2ResultState.SUCCEEDED
    assert first.contract_report.valid is True
    assert fact.primary_evidence_ids
    assert fact.primary_anchor_span_ids == proposal.primary_anchor_span_ids
    assert first.anchor_span_selection_audit[0].state.value == "derived"
    assert resumed.response == first.response
    assert calls == [request.identity.request_id]
    journal = (tmp_path / "semantic/telemetry/journal.jsonl").read_text(encoding="utf-8")
    assert Step2JournalOutcome.REQUEST_STARTED.value in journal
    assert Step2JournalOutcome.SUCCESS.value in journal
    assert Step2JournalOutcome.CACHE_HIT.value in journal


def test_adapter_fails_closed_for_unknown_span_and_keeps_selection_audit(tmp_path):
    request, proposal = _request_and_proposal()
    unknown = proposal.model_copy(update={"primary_anchor_span_ids": ("span:" + "f" * 24,)})

    adapter = Step2OpenAIAdapter(
        storage_root=tmp_path / "semantic",
        transport=CallableStep2Transport(lambda **kwargs: TransportCompletion(content=json.dumps({"proposals": [_payload(unknown)]}))),
        idempotency_supported=False,
    )
    record = adapter.complete_step2a(request)

    assert record.response.state == Step2ResultState.FAILED
    assert record.response.failure is not None
    assert record.response.failure.state.value == "grounding_failed"
    assert record.anchor_span_selection_audit[0].state.value == "unknown_span"
    assert record.rejected_response_forensics is not None


def test_adapter_rejects_legacy_parent_evidence_response_without_persisting_raw_text(tmp_path):
    request, proposal = _request_and_proposal()
    raw_content = json.dumps(
        {
            "proposals": [
                {
                    "proposal_key": "proposal:legacy",
                    "source_section_ids": [request.sections[0].section_id],
                    "fact_type": "condition",
                    "label": "UNPERSISTED legacy text",
                    "assertion": "UNPERSISTED legacy assertion",
                    "primary_evidence_ids": ["ev:UNPERSISTED"],
                }
            ]
        },
        ensure_ascii=False,
    )
    adapter = Step2OpenAIAdapter(
        storage_root=tmp_path / "semantic",
        transport=CallableStep2Transport(lambda **kwargs: TransportCompletion(content=raw_content)),
        idempotency_supported=False,
    )
    record = adapter.complete_step2a(request)

    assert record.response.failure is not None
    assert record.response.failure.state.value == "invalid_response"
    assert record.rejected_response_forensics is not None
    assert record.rejected_response_forensics.missing_field_count == 1
    assert record.rejected_response_forensics.extra_field_count == 1
    saved = (tmp_path / "semantic/responses" / f"{request.identity.request_id.replace(':', '_')}.json").read_text(encoding="utf-8")
    assert "UNPERSISTED" not in saved


def test_transport_sends_anchor_spans_not_projection_audit_or_parent_selection_fields():
    request, _ = _request_and_proposal()
    messages = OpenAICompatibleStep2Transport._request_messages(request=request, prompt="reviewed prompt")
    payload = json.loads(messages[1]["content"].split("\n", 1)[1])
    encoded = json.dumps(payload, ensure_ascii=False)

    assert "projection" not in payload
    assert "primary_evidence_ids" not in encoded
    assert "source_manifest_sha256" not in encoded
    assert "content_policy_fingerprint_sha256" not in encoded
    assert "policy_decision" not in encoded
    sent_spans = [
        span["anchor_span_id"]
        for section in payload["sections"]
        for item in section["text_evidence"]
        for span in item["anchor_spans"]
    ]
    assert sent_spans
    assert set(sent_spans) <= set(request.sections[0].anchor_span_ids)


def test_adapter_never_retries_unknown_transport_outcome(tmp_path):
    request, _ = _request_and_proposal()
    calls = 0

    def callback(**kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("simulated response loss")

    adapter = Step2OpenAIAdapter(storage_root=tmp_path / "semantic", transport=CallableStep2Transport(callback), idempotency_supported=False)
    first = adapter.complete_step2a(request)
    resumed = adapter.complete_step2a(request)

    assert first.response.failure is not None
    assert first.response.failure.state.value == "unknown_outcome"
    assert resumed.response.failure is not None
    assert calls == 1


def test_adapter_quarantines_non_idempotent_5xx_as_unknown_outcome(tmp_path):
    request, _ = _request_and_proposal()
    response = httpx.Response(502, request=httpx.Request("POST", "https://relay.test/v1/chat/completions"))

    def callback(**kwargs):
        raise InternalServerError("upstream 502", response=response, body=None)

    record = Step2OpenAIAdapter(
        storage_root=tmp_path / "semantic", transport=CallableStep2Transport(callback), idempotency_supported=False
    ).complete_step2a(request)
    assert record.response.failure is not None
    assert record.response.failure.state.value == "unknown_outcome"
