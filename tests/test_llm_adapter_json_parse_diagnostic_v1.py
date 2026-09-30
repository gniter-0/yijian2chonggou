"""Offline tests for safe Step 2 JSON-parse diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

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
from examcard.knowledge.llm import (
    CallableStep2Transport,
    Step2OpenAIAdapter,
    TransportCompletion,
)
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.materials.schema import ContentPolicy, SemanticRole


def _request(tmp_path: Path):
    document_sha256 = "a" * 64
    unit = build_evidence_unit(
        document_sha256=document_sha256,
        page_num=1,
        kind=EvidenceKind.PARAGRAPH,
        bbox=(0, 0, 10, 10),
        raw_text="Synthetic JSON diagnostic evidence.",
        canonical_text="Synthetic JSON diagnostic evidence.",
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
        sample_id="json-diagnostic",
        page=page,
        source_manifest_path=page_path,
        source_manifest_reference="tests/json-diagnostic/page_0001.json",
        content_policy=policy,
    )
    return build_step2a_request(
        sample_id="json-diagnostic",
        material_profile_id="test-profile",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        decoder=Step2DecoderConfig(
            max_tokens=256,
            response_format=Step2ResponseFormat.JSON_SCHEMA_STRICT,
        ),
    )


def _adapter(tmp_path: Path, content: str, *, finish_reason: str | None = None) -> Step2OpenAIAdapter:
    return Step2OpenAIAdapter(
        storage_root=tmp_path / "step2",
        transport=CallableStep2Transport(
            lambda **_kwargs: TransportCompletion(
                content=content,
                http_status=200,
                finish_reason=finish_reason,
            )
        ),
        idempotency_supported=False,
    )


def _saved_artifacts(tmp_path: Path) -> str:
    response_path = next((tmp_path / "step2" / "responses").glob("*.json"))
    journal_path = tmp_path / "step2" / "telemetry" / "journal.jsonl"
    return response_path.read_text(encoding="utf-8") + journal_path.read_text(encoding="utf-8")


def test_valid_json_records_only_safe_parse_success_metadata(tmp_path: Path) -> None:
    request = _request(tmp_path)
    adapter = _adapter(tmp_path, '{"proposals": []}', finish_reason="stop")

    record = adapter.complete_step2a(request)

    # The empty proposal array is intentionally not a successful Step 2
    # response; this assertion isolates JSON parsing from the later contract.
    assert record.response.failure is not None
    assert record.response.failure.state.value == "invalid_response"
    assert adapter.last_parse_diagnostic == {
        "parse_result": "success",
        "response_length": len('{"proposals": []}'),
        "finish_reason": "stop",
        "completion_received": True,
    }


def test_invalid_json_keeps_failure_state_and_redacts_body_and_exception_text(
    tmp_path: Path,
) -> None:
    secret = "Bearer synthetic-secret https://provider.invalid/v1"
    body = f"not-json {secret}"
    request = _request(tmp_path)
    adapter = _adapter(tmp_path, body, finish_reason=secret)

    record = adapter.complete_step2a(request)

    assert record.response.failure is not None
    assert record.response.failure.state.value == "invalid_response"
    assert adapter.last_parse_diagnostic == {
        "parse_result": "json_decode_error",
        "response_length": len(body),
        "finish_reason": None,
        "completion_received": True,
    }
    diagnostic_text = json.dumps(adapter.last_parse_diagnostic, ensure_ascii=False)
    assert secret not in diagnostic_text
    artifacts = _saved_artifacts(tmp_path)
    assert secret not in artifacts
    assert body not in artifacts
