"""Offline tests for the first real PDF-to-knowledge-card runtime."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import examcard.pipeline.exam_project_runner_v1 as runner_module
from examcard.common.hashing import sha256_bytes
from examcard.evidence.identity import (
    make_document_id,
    make_evidence_fingerprint,
    make_evidence_id,
    make_evidence_revision_id,
    make_manifest_id,
    make_page_id,
    make_source_locator_id,
)
from examcard.evidence.ingest import EvidenceEngineConfig
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
from examcard.knowledge.llm import CallableStep2Transport, Step2OpenAIAdapter, TransportCompletion
from examcard.materials.schema import ContentPolicy, MaterialProfile, SemanticRole, SourceKind
from examcard.pipeline.exam_project_runner_v1 import (
    ExamProjectDependenciesV1,
    ExamProjectRunError,
    run_exam_project_v1,
)
from examcard.projects.exam_subject_profile_v1 import ExamSubjectProfileV1
from examcard.providers.envelope_profiles import build_image_profile


PDF_BYTES = b"%PDF-1.7 synthetic input"
PDF_SHA256 = "a" * 64


def _profile(*, policy_revision: int = 1) -> ExamSubjectProfileV1:
    policy = ContentPolicy(
        policy_id="core_only_v1",
        revision=policy_revision,
        display_name="core content",
        included_roles=frozenset({SemanticRole.CORE}),
    )
    material = MaterialProfile(
        profile_id="physics_exam_v1",
        revision=1,
        display_name="Physics exam",
        collection_id="exam.physics",
        exam_family="senior_exam",
        exam_level="high_school",
        subject_id="physics",
        subject_name="Physics",
        source_kind=SourceKind.TEXTBOOK,
        content_policy_id=policy.policy_id,
        content_policy_revision=policy.revision,
        layout_profile_id="textbook_cn_v1",
        taxonomy_profile_id="physics_v1",
        validation_profile_id="physics_exam_v1",
        card_profile_id="knowledge_diagram_v1",
    )
    return ExamSubjectProfileV1(
        material_profile=material,
        content_policy=policy,
        evidence_config=EvidenceEngineConfig(enable_visual_review=False),
        step2_model="fake-step2-v1",
        image_profile=build_image_profile(
            model="fake-image-v1",
            image_width=128,
            image_height=128,
        ),
    )


def _manifest(text: str) -> DocumentEvidenceManifest:
    document_id = make_document_id(PDF_SHA256)
    locator = SourceLocator(document_sha256=PDF_SHA256, page_num=1, native_block_index=0)
    fingerprint = make_evidence_fingerprint(kind=EvidenceKind.PARAGRAPH.value, canonical_text=text)
    evidence_id = make_evidence_id(
        locator_id=make_source_locator_id(locator),
        fingerprint=fingerprint,
    )
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
        quality=PageQuality(
            native_text_coverage=1,
            ocr_alignment_rate=0,
            unresolved_regions=0,
            conflict_count=0,
        ),
    )
    return DocumentEvidenceManifest(
        manifest_id=make_manifest_id(
            document_id=document_id,
            material_profile_id="physics_exam_v1",
            material_profile_revision=1,
            config_hash="test-config",
        ),
        document_id=document_id,
        material_profile_id="physics_exam_v1",
        material_profile_revision=1,
        source=SourceDocument(
            relative_path="input.pdf",
            sha256=PDF_SHA256,
            byte_size=len(PDF_BYTES),
            page_count=1,
        ),
        pages=(page,),
        lineage=EvidenceLineage(
            run_id="run:test",
            profile_id="physics_exam_v1",
            profile_revision=1,
            config_hash="test-config",
            generated_at_utc="2026-01-01T00:00:00Z",
        ),
    )


class _FakeEvidenceEngine:
    def __init__(self, manifest: DocumentEvidenceManifest) -> None:
        self.manifest = manifest
        self.calls: list[tuple[Path, str | None, Path | None]] = []

    def ingest_document(
        self,
        pdf_path: Path,
        *,
        source_relative_path: str | None = None,
        output_dir: Path | None = None,
    ) -> tuple[DocumentEvidenceManifest, dict[str, object]]:
        self.calls.append((pdf_path, source_relative_path, output_dir))
        return self.manifest, {"pages": 1, "source": "fake"}


def _step2_adapter(
    tmp_path: Path,
    source: str,
    *,
    label: str = "抗拉强度",
) -> Step2OpenAIAdapter:
    def callback(**kwargs: Any) -> TransportCompletion:
        request = kwargs["request"]
        span = request.projection.anchor_spans[0]
        return TransportCompletion(
            content=json.dumps(
                {
                    "proposals": [
                        {
                            "proposal_key": "proposal:strength",
                            "source_section_ids": [request.sections[0].section_id],
                            "fact_type": "exception",
                            "label": label,
                            "assertion": source,
                            "primary_anchor_span_ids": [span.anchor_span_id],
                            "numeric_claims": [
                                {"value": "65 MPa", "anchor_span_id": span.anchor_span_id}
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )

    return Step2OpenAIAdapter(
        storage_root=tmp_path / "step2",
        transport=CallableStep2Transport(callback),
        idempotency_supported=False,
    )


def _image_body(prompt: str, width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")
    return json.dumps(
        {
            "created": 1,
            "data": [{"b64_json": encoded, "revised_prompt": prompt}],
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "image_tokens": 0,
                    "text_tokens": 1,
                },
                "output_tokens": 1,
                "output_tokens_details": {
                    "image_tokens": 1,
                    "reasoning_tokens": 0,
                    "text_tokens": 0,
                },
                "total_tokens": 2,
            },
        }
    ).encode("utf-8")


def _dependencies(
    tmp_path: Path,
    *,
    manifest: DocumentEvidenceManifest,
    source: str,
    image_caller: Any,
) -> tuple[ExamProjectDependenciesV1, _FakeEvidenceEngine]:
    evidence_engine = _FakeEvidenceEngine(manifest)
    dependencies = ExamProjectDependenciesV1(
        step2_adapter=_step2_adapter(tmp_path, source),
        image_caller=image_caller,
        evidence_engine=evidence_engine,
    )
    return dependencies, evidence_engine


def test_single_pdf_runner_connects_evidence_knowledge_bridge_and_png(tmp_path: Path) -> None:
    source = "钢材抗拉强度不得低于65 MPa。"
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(PDF_BYTES)
    profile = _profile()
    manifest = _manifest(source)
    calls: list[dict[str, object]] = []

    def image_caller(payload: Any) -> tuple[int, bytes]:
        calls.append(dict(payload))
        return 200, _image_body(
            str(payload["prompt"]),
            profile.image_profile.image_width,
            profile.image_profile.image_height,
        )

    dependencies, evidence_engine = _dependencies(
        tmp_path,
        manifest=manifest,
        source=source,
        image_caller=image_caller,
    )
    result = run_exam_project_v1(
        pdf_path=pdf_path,
        profile=profile,
        output_root=tmp_path / "output",
        cache_root=tmp_path / "cache",
        dependencies=dependencies,
        source_relative_path="materials/source.pdf",
    )

    assert evidence_engine.calls == [
        (pdf_path, "materials/source.pdf", tmp_path / "output" / "evidence")
    ]
    assert result.document_sha256 == PDF_SHA256
    assert len(result.cards) == len(result.requests) == len(result.projections) == 1
    assert result.requests[0].evidence_refs[0].evidence_id == result.cards[0].evidence_refs[0].evidence_id
    assert len(calls) == 1
    assert set(calls[0]) == {"model", "n", "prompt", "response_format", "size"}
    assert "evidence_refs" not in calls[0]
    assert "65 MPa" in str(calls[0]["prompt"])
    image_result = result.images[0]
    assert image_result.output_path is not None
    assert image_result.output_path.is_file()
    assert image_result.image_sha256 is not None
    assert sha256_bytes(image_result.output_path.read_bytes()) == image_result.image_sha256
    assert result.cards[0].evidence_refs[0].evidence_id == result.requests[0].evidence_refs[0].evidence_id


def test_dry_run_builds_requests_without_image_calls_or_pngs(tmp_path: Path) -> None:
    source = "钢材抗拉强度不得低于65 MPa。"
    manifest = _manifest(source)
    dependencies, _evidence_engine = _dependencies(
        tmp_path,
        manifest=manifest,
        source=source,
        image_caller=None,
    )

    result = run_exam_project_v1(
        pdf_path=tmp_path / "source.pdf",
        profile=_profile(),
        output_root=tmp_path / "output",
        cache_root=tmp_path / "cache",
        dependencies=dependencies,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.requests
    assert all(item.output_path is None for item in result.images)
    assert not (tmp_path / "output").exists()


def test_missing_image_caller_fails_closed_before_execution(tmp_path: Path) -> None:
    dependencies, _evidence_engine = _dependencies(
        tmp_path,
        manifest=_manifest("仅允许来源证据。"),
        source="仅允许来源证据。",
        image_caller=None,
    )

    with pytest.raises(ExamProjectRunError) as raised:
        run_exam_project_v1(
            pdf_path=tmp_path / "source.pdf",
            profile=_profile(),
            output_root=tmp_path / "output",
            cache_root=tmp_path / "cache",
            dependencies=dependencies,
        )
    assert raised.value.code == "image_caller_required"


def test_invalid_image_response_is_redacted_and_writes_no_png(tmp_path: Path) -> None:
    secret = "synthetic-image-provider-secret"

    def image_caller(_payload: Any) -> tuple[int, bytes]:
        return 502, secret.encode("utf-8")

    dependencies, _evidence_engine = _dependencies(
        tmp_path,
        manifest=_manifest("钢材抗拉强度不得低于65 MPa。"),
        source="钢材抗拉强度不得低于65 MPa。",
        image_caller=image_caller,
    )

    with pytest.raises(ExamProjectRunError) as raised:
        run_exam_project_v1(
            pdf_path=tmp_path / "source.pdf",
            profile=_profile(),
            output_root=tmp_path / "output",
            cache_root=tmp_path / "cache",
            dependencies=dependencies,
        )
    assert raised.value.code == "image_response_invalid"
    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)
    assert not (tmp_path / "output" / "images").exists()


def test_knowledge_extraction_failure_has_safe_diagnostic_without_chaining(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine_path = "C:" + chr(92) + "Users" + chr(92) + "private"
    secret = f"Bearer extraction-secret https://provider.invalid/v1 {machine_path}"

    def fake_pipeline(**_kwargs: object) -> Any:
        raise PermissionError(secret)

    monkeypatch.setattr(runner_module, "run_document_fact_pipeline", fake_pipeline)
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(PDF_BYTES)
    dependencies, _evidence_engine = _dependencies(
        tmp_path,
        manifest=_manifest("仅允许来源证据。"),
        source="仅允许来源证据。",
        image_caller=lambda _payload: (500, b""),
    )

    with pytest.raises(ExamProjectRunError) as raised:
        run_exam_project_v1(
            pdf_path=pdf_path,
            profile=_profile(),
            output_root=tmp_path / "output",
            cache_root=tmp_path / "cache",
            dependencies=dependencies,
        )

    error = raised.value
    diagnostic = error.diagnostic
    assert error.code == "knowledge_extraction_failed"
    assert str(error) == "exam project could not extract grounded knowledge"
    assert repr(error) == "ExamProjectRunError(code='knowledge_extraction_failed')"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert diagnostic is not None
    assert diagnostic["stage"] == "knowledge_extraction"
    assert diagnostic["exception_type"] == "PermissionError"
    assert diagnostic["source_location"].startswith("examcard/")
    assert ":run_exam_project_v1" in diagnostic["source_location"]
    assert diagnostic["message"] == "knowledge extraction failed before durable Step2 completion"
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in json.dumps(diagnostic)
    assert "C:\\" not in diagnostic["source_location"]


def test_knowledge_diagnostic_whitelists_exception_and_stage_values() -> None:
    machine_path = "C:" + chr(92) + "Users" + chr(92) + "private"
    secret = f"provider body secret https://provider.invalid/v1 {machine_path}"

    def projection() -> None:
        raise RuntimeError(secret)

    try:
        projection()
    except RuntimeError as error:
        diagnostic = runner_module._safe_knowledge_extraction_diagnostic(error)
    else:
        pytest.fail("projection probe did not fail")

    assert diagnostic["stage"] == "projection"
    assert diagnostic["exception_type"] == "RuntimeError"
    assert diagnostic["source_location"] == "external"
    assert secret not in json.dumps(diagnostic)

    class CustomFailure(Exception):
        pass

    try:
        raise CustomFailure(secret)
    except CustomFailure as error:
        custom_diagnostic = runner_module._safe_knowledge_extraction_diagnostic(error)

    assert custom_diagnostic["stage"] == "knowledge_extraction"
    assert custom_diagnostic["exception_type"] == "Exception"
    assert custom_diagnostic["source_location"] == "external"
    assert secret not in json.dumps(custom_diagnostic)


def test_knowledge_extraction_failure_carries_safe_parse_diagnostic(
    tmp_path: Path,
) -> None:
    secret = "Bearer parse-secret https://provider.invalid/v1"
    source = "仅允许来源证据。"
    adapter = Step2OpenAIAdapter(
        storage_root=tmp_path / "step2",
        transport=CallableStep2Transport(
            lambda **_kwargs: TransportCompletion(
                content=f"not-json {secret}",
                http_status=200,
                finish_reason="stop",
            )
        ),
        idempotency_supported=False,
    )
    dependencies = ExamProjectDependenciesV1(
        step2_adapter=adapter,
        image_caller=lambda _payload: (500, b""),
        evidence_engine=_FakeEvidenceEngine(_manifest(source)),
    )
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(PDF_BYTES)

    with pytest.raises(ExamProjectRunError) as raised:
        run_exam_project_v1(
            pdf_path=pdf_path,
            profile=_profile(),
            output_root=tmp_path / "output",
            cache_root=tmp_path / "cache",
            dependencies=dependencies,
        )

    error = raised.value
    assert error.code == "knowledge_extraction_failed"
    assert str(error) == "exam project could not extract grounded knowledge"
    assert repr(error) == "ExamProjectRunError(code='knowledge_extraction_failed')"
    assert error.diagnostic is not None
    assert error.diagnostic["parse"] == {
        "parse_result": "json_decode_error",
        "response_length": len(f"not-json {secret}"),
        "finish_reason": "stop",
        "completion_received": True,
    }
    assert secret not in json.dumps(error.diagnostic)


def test_profile_binding_is_deterministic_and_rejects_policy_drift() -> None:
    first = _profile()
    second = _profile()
    assert first.canonical_sha256() == second.canonical_sha256()
    mismatched_policy = ContentPolicy(
        policy_id="other_policy",
        revision=1,
        display_name="other",
        included_roles=frozenset({SemanticRole.CORE}),
    )
    with pytest.raises(ValueError, match="does not match"):
        ExamSubjectProfileV1(
            material_profile=first.material_profile,
            content_policy=mismatched_policy,
            evidence_config=first.evidence_config,
            step2_model=first.step2_model,
            image_profile=first.image_profile,
        )


def test_runner_has_no_delivery_or_persistence_authority() -> None:
    assert not hasattr(run_exam_project_v1, "upload")
    assert not hasattr(run_exam_project_v1, "lookup")
    assert not hasattr(run_exam_project_v1, "retry")
    assert not hasattr(run_exam_project_v1, "create_identity")
    assert not hasattr(run_exam_project_v1, "create_receipt")
    assert not hasattr(run_exam_project_v1, "mutate_checkpoint")
