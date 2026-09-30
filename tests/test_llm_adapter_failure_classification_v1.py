"""Focused offline tests for Step 2 provider-failure diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from openai import APIError, APIStatusError

from examcard.evidence.identity import make_page_id
from examcard.evidence.schema import (
    EvidenceKind,
    PageEvidence,
    PageQuality,
    SourceChannel,
    SourceQuality,
)
from examcard.evidence.units import build_evidence_unit
from examcard.knowledge.contracts import (
    Step2DecoderConfig,
    Step2ResponseFormat,
    build_step2a_request,
)
from examcard.knowledge.llm import CallableStep2Transport, Step2OpenAIAdapter, TransportCompletion
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.materials.schema import ContentPolicy, SemanticRole


def _request(tmp_path: Path):
    document_sha256 = "a" * 64
    unit = build_evidence_unit(
        document_sha256=document_sha256,
        page_num=1,
        kind=EvidenceKind.PARAGRAPH,
        bbox=(0, 0, 10, 10),
        raw_text="Synthetic provider classification evidence.",
        canonical_text="Synthetic provider classification evidence.",
        semantic_role=SemanticRole.CORE,
        source_quality=SourceQuality.NATIVE_ONLY,
        confidence=1.0,
        order=0,
        source_channels={SourceChannel.NATIVE_TEXT},
        engine_versions={"test": "1"},
        config_hash="test-config",
        native_block_index=0,
        generated_at_utc="2026-01-01T00:00:00Z",
    )
    page = PageEvidence(
        page_id=make_page_id(document_id=unit.document_id, page_num=1),
        page_num=1,
        width=100,
        height=100,
        evidence_units=(unit,),
        quality=PageQuality(
            native_text_coverage=1,
            ocr_alignment_rate=0,
            unresolved_regions=0,
            conflict_count=0,
        ),
    )
    page_path = tmp_path / "page_0001.json"
    page_path.write_text(page.model_dump_json(), encoding="utf-8")
    policy = ContentPolicy(
        policy_id="test_policy",
        revision=1,
        display_name="test",
        included_roles=frozenset({SemanticRole.CORE}),
    )
    built = build_step2a_semantic_projection(
        sample_id="provider-classification",
        page=page,
        source_manifest_path=page_path,
        source_manifest_reference="tests/provider-classification/page_0001.json",
        content_policy=policy,
    )
    return build_step2a_request(
        sample_id="provider-classification",
        material_profile_id="test-profile",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        decoder=Step2DecoderConfig(
            max_tokens=256,
            response_format=Step2ResponseFormat.JSON_SCHEMA_STRICT,
        ),
    )


def _adapter(tmp_path: Path, callback):
    return Step2OpenAIAdapter(
        storage_root=tmp_path / "step2",
        transport=CallableStep2Transport(callback),
        idempotency_supported=False,
    )


def _saved_record(tmp_path: Path) -> tuple[dict, str]:
    response_path = next((tmp_path / "step2" / "responses").glob("*.json"))
    journal_path = tmp_path / "step2" / "telemetry" / "journal.jsonl"
    return json.loads(response_path.read_text(encoding="utf-8")), journal_path.read_text(encoding="utf-8")


def test_api_error_before_completion_is_upstream_unavailable_with_fixed_provider_diagnostic(tmp_path: Path) -> None:
    secret = "sk-provider-secret-api-error"
    request = _request(tmp_path)

    def callback(**_kwargs: object) -> TransportCompletion:
        raise APIError(
            f"provider failure {secret}",
            httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
            body={"secret": secret},
        )

    adapter = _adapter(tmp_path, callback)
    first = adapter.complete_step2a(request)
    resumed = adapter.complete_step2a(request)

    assert first.response.failure is not None
    assert first.response.failure.state.value == "upstream_unavailable"
    assert first.response.failure.message.endswith("provider_unavailable")
    assert resumed.response == first.response
    record, journal = _saved_record(tmp_path)
    serialized = json.dumps(record, ensure_ascii=False) + journal
    assert secret not in serialized
    assert "provider_unavailable" in serialized


def test_rate_limit_is_upstream_unavailable_with_rate_limit_diagnostic(tmp_path: Path) -> None:
    request = _request(tmp_path)
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
    )

    def callback(**_kwargs: object) -> TransportCompletion:
        raise APIStatusError("rate limited provider secret", response=response, body=None)

    record = _adapter(tmp_path, callback).complete_step2a(request)

    assert record.response.failure is not None
    assert record.response.failure.state.value == "upstream_unavailable"
    assert record.response.failure.message.endswith("provider_rate_limited")


def test_timeout_preserves_unknown_outcome_with_timeout_diagnostic(tmp_path: Path) -> None:
    request = _request(tmp_path)

    def callback(**_kwargs: object) -> TransportCompletion:
        raise TimeoutError("provider timeout secret")

    record = _adapter(tmp_path, callback).complete_step2a(request)

    assert record.response.failure is not None
    assert record.response.failure.state.value == "unknown_outcome"
    assert record.response.failure.message.endswith("provider_timeout")


def test_invalid_json_after_completion_remains_invalid_response(tmp_path: Path) -> None:
    request = _request(tmp_path)
    record = _adapter(
        tmp_path,
        lambda **_kwargs: TransportCompletion(content="not-json"),
    ).complete_step2a(request)

    assert record.response.failure is not None
    assert record.response.failure.state.value == "invalid_response"
    assert "payload_schema_validation_failed" in record.response.failure.message


def test_schema_mismatch_after_completion_remains_invalid_response(tmp_path: Path) -> None:
    request = _request(tmp_path)
    record = _adapter(
        tmp_path,
        lambda **_kwargs: TransportCompletion(content='{"proposals": [{"proposal_key": "proposal:bad"}]}'),
    ).complete_step2a(request)

    assert record.response.failure is not None
    assert record.response.failure.state.value == "invalid_response"
    assert record.rejected_response_forensics is not None
