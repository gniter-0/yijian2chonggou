"""Offline safety net for the production V25 live Step 2 executor."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from examcard.evidence.schema import EvidenceKind
from examcard.knowledge.contracts.v25_generic_contracts import (
    V25BStep2BResultPayload,
    V25FactReviewState,
    V25Step2AResultPayload,
    V25Step2BFactSummary,
    build_v25_source_fake_response,
    build_v25b_source_fake_response,
    build_v25_step2a_request,
    build_v25b_step2b_request,
    v25b_step2b_prompt_template,
    v25b_step2b_request_payload,
    v25_step2a_prompt_template,
    v25_step2a_request_payload,
)
from examcard.knowledge.contracts.anchors import EvidenceAnchorSpan, make_evidence_anchor_span_id
from examcard.knowledge.contracts.phase2r14_v25_live import _execute_stage, _strict_probe
from examcard.knowledge.contracts.table_inputs import SectionTextInput, Step2ASectionInput
from examcard.knowledge.llm_transport import TransportCompletion
from examcard.knowledge.schema import FactType
from examcard.materials.schema import (
    CoverageRequirement,
    PolicyDecision,
    PolicyDisposition,
    PolicyDecisionState,
    SemanticRole,
)
from examcard.pipeline.live_budget import LiveRunBudget
from examcard.pipeline.live_resend import ResendPlan
from examcard.pipeline.live_stage_samples import execute_stage_samples


_RELAY = "https://offline.invalid/v1"
_MODEL = "deterministic-v25-test"
_POLICY_SHA = "b" * 64
_MANIFEST_SHA = "c" * 64
_PROJECTION_SHA = "d" * 64
_SOURCE_SHA = "e" * 64


def _step2a_request(*, sample_id: str = "sample", reason: str = "step2-live-test"):
    evidence_id = f"evidence:test:{sample_id}"
    source_text = "来源事实"
    policy_decision = PolicyDecision(
        evidence_id=evidence_id,
        semantic_role=SemanticRole.CORE,
        disposition=PolicyDisposition.INCLUDED,
        coverage_requirement=CoverageRequirement.REQUIRED,
        decision_state=PolicyDecisionState.AUTO_CLASSIFIED,
        policy_id="core_only_v1",
        policy_revision=1,
        reason="synthetic included source",
    )
    exact_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    span_id = make_evidence_anchor_span_id(
        parent_evidence_id=evidence_id,
        start=0,
        end=len(source_text),
        exact_text_sha256=exact_sha,
        content_policy_fingerprint_sha256=_POLICY_SHA,
        source_manifest_sha256=_MANIFEST_SHA,
    )
    span = EvidenceAnchorSpan(
        anchor_span_id=span_id,
        parent_evidence_id=evidence_id,
        parent_evidence_kind=EvidenceKind.PARAGRAPH,
        start=0,
        end=len(source_text),
        canonical_text=source_text,
        exact_text_sha256=exact_sha,
        content_policy_id="core_only_v1",
        content_policy_revision=1,
        content_policy_fingerprint_sha256=_POLICY_SHA,
        policy_decision=policy_decision,
        source_manifest_sha256=_MANIFEST_SHA,
    )
    section = Step2ASectionInput(
        section_id="section:" + "a" * 24,
        page_num=1,
        heading_evidence_ids=(),
        context_only_evidence_ids=(),
        text_evidence=(
            SectionTextInput(
                evidence_id=evidence_id,
                page_num=1,
                kind=EvidenceKind.PARAGRAPH,
                canonical_text=source_text,
                anchor_spans=(span,),
                context_only=False,
            ),
        ),
        tables=(),
        controlled_diagrams=(),
    )
    return build_v25_step2a_request(
        sample_id=sample_id,
        material_profile_id="yijian_gonglu_2026",
        source_document_sha256=_SOURCE_SHA,
        source_manifest_sha256s=(_MANIFEST_SHA,),
        projection_sha256s=(_PROJECTION_SHA,),
        content_policy_id="core_only_v1",
        content_policy_revision=1,
        content_policy_fingerprint_sha256=_POLICY_SHA,
        sections=(section,),
        model=_MODEL,
        relay_base_url=_RELAY,
        manual_reissue_reason=reason,
    )


def _step2b_request(*, sample_id: str = "sample"):
    fact = V25Step2BFactSummary(
        fact_id="fact:" + "f" * 24,
        fact_type=FactType.DEFINITION,
        canonical_title="Step2B 定义事实",
        assertion_summary="用于验证 Step2B active-live production executor 的定义事实。",
        primary_evidence_ids=("evidence:step2b:fact",),
        primary_anchor_span_ids=("span:" + "0" * 24,),
        source_topic_key="source-topic:" + "1" * 24,
        review_state=V25FactReviewState.REVIEWED,
    )
    return build_v25b_step2b_request(
        sample_id=sample_id,
        material_profile_id="yijian_gonglu_2026",
        facts=(fact,),
        model=_MODEL,
        relay_base_url=_RELAY,
    )


def _completion_for(request: Any, *, content: str | None = None) -> TransportCompletion:
    if content is None:
        content = json.dumps(
            build_v25_source_fake_response(request=request).model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return TransportCompletion(content=content, provider_request_id="provider-v25-test", http_status=200)


CompletionAction = Callable[..., TransportCompletion]


class RecordingV25Transport:
    """Deterministic V25 seam; it never constructs an HTTP client."""

    def __init__(self, *, completions: list[CompletionAction | BaseException] | None = None, probes: list[Any] | None = None) -> None:
        self.completions = list(completions or [])
        self.probes = list(probes or [])
        self.network_call_count = 0
        self.complete_calls: list[dict[str, Any]] = []
        self.probe_calls: list[dict[str, Any]] = []

    def complete_payload(
        self,
        *,
        identity: Any,
        model_payload: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion:
        self.network_call_count += 1
        self.complete_calls.append(
            {
                "identity": identity,
                "model_payload": model_payload,
                "response_schema": response_schema,
                "prompt": prompt,
                "use_idempotency_key": use_idempotency_key,
            }
        )
        action = self.completions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action(
            identity=identity,
            model_payload=model_payload,
            response_schema=response_schema,
            prompt=prompt,
            use_idempotency_key=use_idempotency_key,
        )

    def probe_json_schema(
        self,
        *,
        model: str,
        schema_name: str,
        response_schema: Mapping[str, Any],
        user_content: str,
        max_tokens: int = 400,
    ) -> TransportCompletion:
        self.network_call_count += 1
        self.probe_calls.append(
            {
                "model": model,
                "schema_name": schema_name,
                "response_schema": response_schema,
                "user_content": user_content,
                "max_tokens": max_tokens,
            }
        )
        action = self.probes.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


def _artifact_paths(root: Path, request: Any) -> tuple[Path, Path, Path, Path]:
    request_name = request.identity.request_id.replace(":", "_") + ".json"
    cache_name = request.identity.cache_key.replace(":", "_") + ".json"
    return (
        root / "requests" / request_name,
        root / "records" / request_name,
        root / "cache" / cache_name,
        root / "telemetry" / "journal.jsonl",
    )


def test_execute_stage_success_persists_auditable_chain(repo_tmp_path: Path) -> None:
    request = _step2a_request()
    request_path, record_path, cache_path, journal_path = _artifact_paths(repo_tmp_path, request)
    raw_content = json.dumps(
        build_v25_source_fake_response(request=request).model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    def complete(**kwargs: Any) -> TransportCompletion:
        assert request_path.is_file(), "request must be persisted before the provider seam"
        events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        assert [event["event_type"] for event in events] == ["request_started"]
        assert kwargs["model_payload"] == v25_step2a_request_payload(request)
        assert kwargs["prompt"] == v25_step2a_prompt_template(contract_profile=request.contract_profile)
        assert kwargs["response_schema"] == V25Step2AResultPayload.model_json_schema()
        return TransportCompletion(content=raw_content, provider_request_id="provider-success", http_status=200)

    transport = RecordingV25Transport(completions=[complete])
    result = _execute_stage(storage_root=repo_tmp_path, transport=transport, request=request, stage="step2a")

    assert result["state"] == "success"
    assert transport.network_call_count == 1
    assert transport.complete_calls[0]["use_idempotency_key"] is False
    assert result["idempotency_header_sent"] is False
    assert result["automatic_retry_enabled"] is False
    assert request_path.is_file() and record_path.is_file() and cache_path.is_file()
    request_artifact = json.loads(request_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert request_artifact["request"]["identity"]["request_id"] == request.identity.request_id
    assert record["request_id"] == request.identity.request_id
    assert cache["request_id"] == request.identity.request_id
    assert cache["record_sha256"] == hashlib.sha256(record_path.read_bytes()).hexdigest()
    assert record["response_sha256"] == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    assert [event["event_type"] for event in events] == ["request_started", "terminal"]
    assert events[1]["outcome"] == "success"
    assert events[1]["request_id"] == request.identity.request_id


def test_execute_stage_step2b_success_uses_production_payload_prompt_schema_and_persists_chain(
    repo_tmp_path: Path,
) -> None:
    request = _step2b_request()
    request_path, record_path, cache_path, journal_path = _artifact_paths(repo_tmp_path, request)
    raw_content = json.dumps(
        build_v25b_source_fake_response(request=request).model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    observed: dict[str, Any] = {}

    def complete(**kwargs: Any) -> TransportCompletion:
        observed["request_exists_before_provider"] = request_path.is_file()
        events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        observed["event_types_before_provider"] = [event["event_type"] for event in events]
        observed["model_payload"] = kwargs["model_payload"]
        observed["prompt"] = kwargs["prompt"]
        observed["response_schema"] = kwargs["response_schema"]
        observed["use_idempotency_key"] = kwargs["use_idempotency_key"]
        return TransportCompletion(content=raw_content, provider_request_id="provider-step2b-success", http_status=200)

    transport = RecordingV25Transport(completions=[complete])
    result = _execute_stage(storage_root=repo_tmp_path, transport=transport, request=request, stage="step2b")

    assert result["state"] == "success"
    assert transport.network_call_count == 1
    assert observed["request_exists_before_provider"] is True
    assert observed["event_types_before_provider"] == ["request_started"]
    assert observed["model_payload"] == v25b_step2b_request_payload(request)
    assert observed["prompt"] == v25b_step2b_prompt_template()
    assert observed["response_schema"] == V25BStep2BResultPayload.model_json_schema()
    assert observed["use_idempotency_key"] is False
    assert request_path.is_file() and record_path.is_file() and cache_path.is_file()
    request_artifact = json.loads(request_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert request_artifact["request"]["identity"]["request_id"] == request.identity.request_id
    assert record["request_id"] == request.identity.request_id
    assert cache["request_id"] == request.identity.request_id
    assert cache["record_sha256"] == hashlib.sha256(record_path.read_bytes()).hexdigest()
    assert record["response_sha256"] == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    assert [event["event_type"] for event in events] == ["request_started", "terminal"]
    assert events[1]["outcome"] == "success"


def test_execute_stage_transport_exception_is_unknown_and_sanitized(repo_tmp_path: Path) -> None:
    request = _step2a_request()
    transport = RecordingV25Transport(
        completions=[RuntimeError("secret api_key=TOP-SECRET bearer=sk-test-body")]
    )

    result = _execute_stage(storage_root=repo_tmp_path, transport=transport, request=request, stage="step2a")

    assert result["state"] == "unknown_outcome"
    assert transport.network_call_count == 1
    request_path, record_path, cache_path, journal_path = _artifact_paths(repo_tmp_path, request)
    assert request_path.is_file() and record_path.is_file() and cache_path.is_file() and journal_path.is_file()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    journal = journal_path.read_text(encoding="utf-8")
    assert record["forensics"] == {"error_type": "RuntimeError", "http_status": None}
    assert cache["state"] == "unknown_outcome"
    assert "TOP-SECRET" not in record_path.read_text(encoding="utf-8")
    assert "TOP-SECRET" not in journal
    assert "sk-test-body" not in journal


def test_execute_stage_malformed_completion_is_failed_with_response_hash(repo_tmp_path: Path) -> None:
    request = _step2a_request()
    raw_content = '{"proposals": []}'
    transport = RecordingV25Transport(
        completions=[lambda **_kwargs: TransportCompletion(content=raw_content, http_status=200)]
    )

    result = _execute_stage(storage_root=repo_tmp_path, transport=transport, request=request, stage="step2a")

    assert result["state"] == "failed"
    assert result["state"] != "success"
    _, record_path, cache_path, journal_path = _artifact_paths(repo_tmp_path, request)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert record["response"] is None
    assert record["schema_valid"] is False
    assert record["response_sha256"] == hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    assert cache["state"] == "failed"
    terminal = json.loads(journal_path.read_text(encoding="utf-8").splitlines()[-1])
    assert terminal["event_type"] == "terminal"
    assert terminal["outcome"] == "failed"


@pytest.mark.parametrize("artifact", ["request", "record", "cache"])
def test_execute_stage_refuses_every_existing_identity_artifact(repo_tmp_path: Path, artifact: str) -> None:
    request = _step2a_request()
    request_path, record_path, cache_path, _ = _artifact_paths(repo_tmp_path, request)
    paths = {"request": request_path, "record": record_path, "cache": cache_path}
    path = paths[artifact]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"interrupted-attempt-sentinel")
    before = path.read_bytes()
    transport = RecordingV25Transport(completions=[lambda **_kwargs: _completion_for(request)])

    with pytest.raises(ValueError, match="refuses to reuse"):
        _execute_stage(storage_root=repo_tmp_path, transport=transport, request=request, stage="step2a")

    assert transport.network_call_count == 0
    assert path.read_bytes() == before
    if artifact == "request":
        assert not cache_path.exists(), "request-only interrupted state must still refuse replay"


def _valid_probe_completion() -> TransportCompletion:
    payload = {
        "proposals": [
            {
                "fact_type": "definition",
                "label": "V25形状探测",
                "assertion": "仅用于严格响应形状探测。",
                "primary_anchor_span_ids": ["span:" + "0" * 24],
            }
        ]
    }
    return TransportCompletion(content=json.dumps(payload, ensure_ascii=False), http_status=200)


def test_strict_probe_uses_production_schema_prompt_and_succeeds() -> None:
    transport = RecordingV25Transport(probes=[_valid_probe_completion()])

    result = _strict_probe(transport=transport, model=_MODEL, relay_base_url=_RELAY, stage="step2a")

    assert result["outcome"] == "success"
    assert transport.network_call_count == 1
    call = transport.probe_calls[0]
    assert call["model"] == _MODEL
    assert call["schema_name"] == "v25_step2a_strict_probe"
    assert call["response_schema"] == V25Step2AResultPayload.model_json_schema()
    assert "Return exactly this JSON object" in call["user_content"]


def test_strict_probe_invalid_response_is_classified_without_retry() -> None:
    transport = RecordingV25Transport(
        probes=[TransportCompletion(content="not-json", http_status=200)]
    )

    result = _strict_probe(transport=transport, model=_MODEL, relay_base_url=_RELAY, stage="step2a")

    assert result["outcome"] == "invalid_response"
    assert result["schema_valid"] is False
    assert transport.network_call_count == 1


def test_execute_stage_samples_resends_with_real_executor_and_fresh_identity(repo_tmp_path: Path) -> None:
    requests: dict[str, Any] = {}

    def context_builder(*, sample_ids: tuple[str, ...], reason: str):
        sample_id = sample_ids[0]
        request = _step2a_request(sample_id=sample_id, reason=reason)
        requests[request.identity.request_id] = request
        return (SimpleNamespace(sample_id=sample_id, step2a_request=request, step2b_request=request),)

    def second_completion(*, identity: Any, **_kwargs: Any) -> TransportCompletion:
        return _completion_for(requests[identity.request_id])

    transport = RecordingV25Transport(
        completions=[TimeoutError("response lost before completion"), second_completion]
    )
    budget = LiveRunBudget(repo_tmp_path / "budget.json", usd_cap=10.0, stage_call_caps={"step2a": 2})

    outcome = execute_stage_samples(
        stage="step2a",
        sample_ids=("sample",),
        context_builder=context_builder,
        transport=transport,
        storage=repo_tmp_path / "step2a",
        budget=budget,
        resend=ResendPlan(base_reason="sample-step2a-live", max_attempts=2),
        price_per_call=0.25,
    )

    attempts = outcome["samples"][0]["attempts"]
    request_ids = [attempt["request_id"] for attempt in attempts]
    assert outcome["status"] == "completed"
    assert [attempt["state"] for attempt in attempts] == ["unknown_outcome", "success"]
    assert request_ids[0] != request_ids[1]
    assert [call["identity"].request_id for call in transport.complete_calls] == request_ids
    assert transport.network_call_count == 2
    assert budget.total_calls == 2
    assert budget.snapshot()["per_stage"]["step2a"]["calls"] == 2
