"""Offline tests for the agent file-exchange Step 2 transport."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from examcard.evidence.identity import (
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_id,
    make_evidence_revision_id,
    make_manifest_id,
    make_page_id,
    make_source_locator_id,
)
from examcard.evidence.schema import (
    DocumentEvidenceManifest,
    EvidenceKind,
    EvidenceLineage,
    EvidenceProvenance,
    EvidenceUnit,
    PageEvidence,
    PageQuality,
    SourceChannel,
    SourceDocument,
    SourceLocator,
    SourceQuality,
)
from examcard.knowledge.agent_file_transport import (
    AGENT_STEP2_TRANSPORT_ID,
    AgentFileStep2Transport,
    AgentStep2ResponseBindingError,
    AgentStep2ResponseEnvelopeV1,
    AgentStep2ResponseMissing,
    agent_step2_response_path,
    load_agent_step2_response,
)
from examcard.knowledge.contracts import Step2ARequest
from examcard.knowledge.extraction_v1 import iter_document_step2a_requests, run_document_fact_pipeline
from examcard.knowledge.llm import Step2OpenAIAdapter, TransportCompletion
from examcard.materials.schema import ContentPolicy, SemanticRole

MODEL = "agent-file:test-review"
SOURCE = "钢材抗拉强度不得低于65 MPa。"


def _policy() -> ContentPolicy:
    return ContentPolicy(
        policy_id="test_policy",
        revision=1,
        display_name="test",
        included_roles=frozenset({SemanticRole.CORE}),
    )


def _manifest(text: str = SOURCE) -> DocumentEvidenceManifest:
    pdf_sha256 = "a" * 64
    document_id = make_document_id(pdf_sha256)
    locator = SourceLocator(document_sha256=pdf_sha256, page_num=1, native_block_index=0)
    fingerprint = make_evidence_fingerprint(kind=EvidenceKind.PARAGRAPH.value, canonical_text=text)
    evidence_id = make_evidence_id(locator_id=make_source_locator_id(locator), fingerprint=fingerprint)
    unit = EvidenceUnit(
        evidence_id=evidence_id,
        fingerprint=fingerprint,
        revision_id=make_evidence_revision_id(
            fingerprint=fingerprint,
            raw_text=text,
            source_channels=(SourceChannel.NATIVE_TEXT.value,),
            engine_versions={"test": "1"},
            config_hash="test-config",
        ),
        document_id=document_id,
        kind=EvidenceKind.PARAGRAPH,
        locator=locator,
        raw_text=text,
        canonical_text=text,
        semantic_role=SemanticRole.CORE,
        source_quality=SourceQuality.NATIVE_ONLY,
        confidence=1,
        order=0,
        provenance=EvidenceProvenance(
            source_channels=frozenset({SourceChannel.NATIVE_TEXT}),
            engine_versions={"test": "1"},
            config_hash="test-config",
            generated_at_utc="2026-01-01T00:00:00Z",
        ),
    )
    page = PageEvidence(
        page_id=make_page_id(document_id=document_id, page_num=1),
        page_num=1,
        width=100,
        height=100,
        evidence_units=(unit,),
        quality=PageQuality(native_text_coverage=1, ocr_alignment_rate=0, unresolved_regions=0, conflict_count=0),
    )
    return DocumentEvidenceManifest(
        manifest_id=make_manifest_id(
            document_id=document_id,
            material_profile_id="test_profile",
            material_profile_revision=1,
            config_hash="test-config",
        ),
        document_id=document_id,
        material_profile_id="test_profile",
        material_profile_revision=1,
        source=SourceDocument(relative_path="input.pdf", sha256=pdf_sha256, byte_size=1, page_count=1),
        pages=(page,),
        lineage=EvidenceLineage(
            run_id="run:test",
            profile_id="test_profile",
            profile_revision=1,
            config_hash="test-config",
            generated_at_utc="2026-01-01T00:00:00Z",
        ),
    )


def _request(model: str = MODEL) -> Step2ARequest:
    return next(iter_document_step2a_requests(manifest=_manifest(), content_policy=_policy(), model=model))


def _payload(request: Step2ARequest) -> dict[str, Any]:
    span = request.projection.anchor_spans[0]
    return {
        "proposals": [
            {
                "proposal_key": "proposal:strength",
                "source_section_ids": [request.sections[0].section_id],
                "fact_type": "exception",
                "label": "抗拉强度",
                "assertion": SOURCE,
                "primary_anchor_span_ids": [span.anchor_span_id],
                "numeric_claims": [{"value": "65 MPa", "anchor_span_id": span.anchor_span_id}],
            }
        ]
    }


def _write_envelope(root: Path, request: Step2ARequest, **overrides: Any) -> Path:
    identity = request.identity
    envelope: dict[str, Any] = {
        "schema_version": "examcard_agent_step2_response_v1",
        "request_id": identity.request_id,
        "model": identity.model,
        "prompt_sha256": identity.prompt_sha256,
        "response_schema_sha256": identity.response_schema_sha256,
        "projection_sha256": identity.projection_sha256,
        "author": {"agent_label": "test agent", "page_image_reviewed": True, "notes": ""},
        "payload": _payload(request),
    }
    envelope.update(overrides)
    path = agent_step2_response_path(root, identity.request_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    return path


class _Recorder:
    """Wraps the agent transport to capture the exact adapter call arguments."""

    def __init__(self, inner: AgentFileStep2Transport) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []
        self.completions: list[TransportCompletion] = []

    def complete(self, **kwargs: Any) -> TransportCompletion:
        self.calls.append(kwargs)
        completion = self.inner.complete(**kwargs)
        self.completions.append(completion)
        return completion


def _adapter_call(tmp_path: Path, request: Step2ARequest) -> tuple[_Recorder, Any]:
    recorder = _Recorder(AgentFileStep2Transport(response_root=tmp_path / "responses"))
    adapter = Step2OpenAIAdapter(storage_root=tmp_path / "step2", transport=recorder, idempotency_supported=False)
    return recorder, adapter.complete_step2a(request)


def test_agent_envelope_flows_through_the_unchanged_adapter_to_a_grounded_card(tmp_path: Path) -> None:
    request = _request()
    _write_envelope(tmp_path / "responses", request)
    adapter = Step2OpenAIAdapter(
        storage_root=tmp_path / "step2",
        transport=AgentFileStep2Transport(response_root=tmp_path / "responses"),
        idempotency_supported=False,
    )

    result = run_document_fact_pipeline(
        manifest=_manifest(),
        content_policy=_policy(),
        adapter=adapter,
        model=MODEL,
    )

    assert [proposal.proposal_key for proposal in result.extraction.proposals] == ["proposal:strength"]
    assert result.facts[0].assertion == SOURCE
    assert result.cards[0].content == SOURCE
    assert result.facts[0].numbers[0].normalized == "65MPa"


def test_completion_is_the_payload_tagged_as_agent_output_without_network(tmp_path: Path) -> None:
    request = _request()
    path = _write_envelope(tmp_path / "responses", request)
    recorder, record = _adapter_call(tmp_path, request)

    assert record.response.failure is None
    completion = recorder.completions[0]
    assert json.loads(completion.content) == _payload(request)
    _, envelope_sha256 = load_agent_step2_response(path)
    assert completion.provider_request_id == "agent-file:" + envelope_sha256[:32]
    assert completion.headers == {"x-examcard-transport": AGENT_STEP2_TRANSPORT_ID}
    assert completion.http_status is None
    assert AgentFileStep2Transport.network_call_count == 0


def test_missing_response_file_raises_instead_of_waiting(tmp_path: Path) -> None:
    request = _request()
    _write_envelope(tmp_path / "responses", request)
    recorder, _ = _adapter_call(tmp_path, request)
    call = recorder.calls[0]
    empty = AgentFileStep2Transport(response_root=tmp_path / "empty")

    with pytest.raises(AgentStep2ResponseMissing):
        empty.complete(**call)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "step2a-request:" + "0" * 64),
        ("model", "agent-file:other-review"),
        ("prompt_sha256", "1" * 64),
        ("response_schema_sha256", "2" * 64),
        ("projection_sha256", "3" * 64),
    ],
)
def test_envelope_written_for_another_request_is_refused(tmp_path: Path, field: str, value: str) -> None:
    request = _request()
    _write_envelope(tmp_path / "responses", request)
    recorder, _ = _adapter_call(tmp_path, request)
    call = recorder.calls[0]
    _write_envelope(tmp_path / "tampered", request, **{field: value})
    transport = AgentFileStep2Transport(response_root=tmp_path / "tampered")

    with pytest.raises(AgentStep2ResponseBindingError, match=field):
        transport.complete(**call)


def test_prompt_changed_after_identity_is_refused(tmp_path: Path) -> None:
    request = _request()
    _write_envelope(tmp_path / "responses", request)
    recorder, _ = _adapter_call(tmp_path, request)
    call = {**recorder.calls[0], "prompt": recorder.calls[0]["prompt"] + "\nextra instruction"}

    with pytest.raises(AgentStep2ResponseBindingError, match="prompt_sha256"):
        recorder.inner.complete(**call)


def test_provider_model_label_is_refused_even_with_a_matching_envelope(tmp_path: Path) -> None:
    request = _request(model="fake-step2-v1")
    _write_envelope(tmp_path / "responses", request)
    transport = AgentFileStep2Transport(response_root=tmp_path / "responses")

    with pytest.raises(AgentStep2ResponseBindingError, match="agent-file:"):
        transport.complete(request=request, response_schema={}, prompt="", use_idempotency_key=False)


def test_agent_and_provider_requests_never_share_a_request_id() -> None:
    assert _request().identity.request_id != _request(model="fake-step2-v1").identity.request_id
    assert _request().identity.request_id == _request().identity.request_id


def test_envelope_rejects_unknown_fields_and_non_agent_shapes(tmp_path: Path) -> None:
    request = _request()
    path = _write_envelope(tmp_path / "responses", request, approved_by="someone")

    with pytest.raises(ValidationError):
        load_agent_step2_response(path)
    with pytest.raises(ValidationError):
        AgentStep2ResponseEnvelopeV1.model_validate(
            {
                "request_id": request.identity.request_id,
                "model": request.identity.model,
                "prompt_sha256": request.identity.prompt_sha256,
                "response_schema_sha256": request.identity.response_schema_sha256,
                "author": {"agent_label": "", "page_image_reviewed": True},
                "payload": {},
            }
        )
