"""Offline tests for the isolated exam-project AI pilot entry point."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "migration"))

import run_exam_project_ai_pilot_v1 as pilot
import examcard.pipeline.exam_project_runner_v1 as runner_module
from examcard.evidence.ingest import EvidenceEngineConfig
from examcard.knowledge.llm import CallableStep2Transport, TransportCompletion
from examcard.materials.schema import ContentPolicy, MaterialProfile, SemanticRole, SourceKind
from examcard.pipeline.exam_project_runner_v1 import ExamProjectDependenciesV1, ExamProjectRunError
from examcard.projects.exam_subject_profile_v1 import ExamSubjectProfileV1
from examcard.providers.envelope_profiles import build_image_profile


def _profile() -> ExamSubjectProfileV1:
    policy = ContentPolicy(
        policy_id="core_only_v1",
        revision=1,
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
        image_profile=build_image_profile(model="fake-image-v1", image_width=128, image_height=128),
    )


def _dependencies(tmp_path: Path) -> ExamProjectDependenciesV1:
    def callback(**_kwargs: object) -> TransportCompletion:
        return TransportCompletion(content='{"proposals": []}')

    from examcard.knowledge.llm import Step2OpenAIAdapter

    return ExamProjectDependenciesV1(
        step2_adapter=Step2OpenAIAdapter(
            storage_root=tmp_path / "step2",
            transport=CallableStep2Transport(callback),
            idempotency_supported=False,
        )
    )


def _result(output_root: Path) -> Any:
    image_path = output_root / "images" / "card-demo.png"
    source = SimpleNamespace(
        relative_path="input.pdf",
        sha256="a" * 64,
        byte_size=10,
        page_count=1,
    )
    manifest = SimpleNamespace(source=source, pages=(SimpleNamespace(evidence_units=(1, 2)),))
    fact_pipeline = SimpleNamespace(facts=(object(),))
    image = SimpleNamespace(
        card_id="card:demo",
        raw_id="kp:demo",
        request_id="request:demo",
        request_sha256="b" * 64,
        provenance_sha256="c" * 64,
        source_pages=(1,),
        output_path=image_path,
        image_sha256="d" * 64,
    )
    return SimpleNamespace(
        document_sha256="a" * 64,
        manifest=manifest,
        profile_sha256="e" * 64,
        fact_pipeline=fact_pipeline,
        cards=(object(),),
        requests=(object(),),
        images=(image,),
        dry_run=False,
    )


def test_pilot_runs_with_injected_runtime_and_writes_safe_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    output_root = tmp_path / "pilot-output"
    result = _result(output_root)
    calls: list[dict[str, object]] = []

    def fake_runner(**kwargs: object) -> Any:
        calls.append(dict(kwargs))
        return result

    monkeypatch.setattr(pilot, "run_exam_project_v1", fake_runner)
    profile = _profile()

    returned = pilot.run_exam_project_ai_pilot_v1(
        pdf_path=pdf_path,
        output_root=output_root,
        profile=profile,
        dependencies=_dependencies(tmp_path),
        source_relative_path="materials/source.pdf",
        sample_id="sample-1",
    )

    assert returned is result
    assert calls[0]["dry_run"] is False
    assert calls[0]["cache_root"] == output_root / "evidence-cache"
    metadata = json.loads((output_root / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "succeeded"
    assert metadata["input"] == {"name": "source.pdf", "sha256": "a" * 64, "byte_size": 10, "page_count": 1}
    assert metadata["stages"] == {
        "evidence_units": 2,
        "fact_count": 1,
        "card_count": 1,
        "image_request_count": 1,
        "image_count": 1,
    }
    assert metadata["images"][0]["output_path"] == "images/card-demo.png"


def test_pilot_failure_metadata_is_fixed_text_and_does_not_leak_provider_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    output_root = tmp_path / "pilot-output"
    secret = "Bearer synthetic-pilot-secret"

    def fake_runner(**_kwargs: object) -> Any:
        raise RuntimeError(secret)

    monkeypatch.setattr(pilot, "run_exam_project_v1", fake_runner)
    profile = _profile()

    with pytest.raises(pilot.ExamProjectPilotConfigurationError) as raised:
        pilot.run_exam_project_ai_pilot_v1(
            pdf_path=pdf_path,
            output_root=output_root,
            profile=profile,
            dependencies=_dependencies(tmp_path),
        )

    assert secret not in str(raised.value)
    metadata = (output_root / "run_metadata.json").read_text(encoding="utf-8")
    assert secret not in metadata
    payload = json.loads(metadata)
    assert payload["failure_code"] == "pilot_execution_failed"
    assert "diagnostic" not in payload


def test_pilot_persists_only_safe_knowledge_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    output_root = tmp_path / "pilot-output"
    machine_path = "C:" + chr(92) + "Users" + chr(92) + "private"
    secret = f"provider secret https://provider.invalid/v1 {machine_path}"

    def fake_runner(**_kwargs: object) -> Any:
        try:
            raise PermissionError(secret)
        except PermissionError as caught:
            diagnostic = runner_module._safe_knowledge_extraction_diagnostic(caught)
        raise ExamProjectRunError(
            "knowledge_extraction_failed",
            diagnostic=diagnostic,
        )

    monkeypatch.setattr(pilot, "run_exam_project_v1", fake_runner)

    with pytest.raises(ExamProjectRunError) as raised:
        pilot.run_exam_project_ai_pilot_v1(
            pdf_path=pdf_path,
            output_root=output_root,
            profile=_profile(),
            dependencies=_dependencies(tmp_path),
        )

    assert str(raised.value) == "exam project could not extract grounded knowledge"
    metadata_text = (output_root / "run_metadata.json").read_text(encoding="utf-8")
    assert secret not in metadata_text
    payload = json.loads(metadata_text)
    assert payload["failure_code"] == "knowledge_extraction_failed"
    assert payload["diagnostic"] == {
        "stage": "knowledge_extraction",
        "exception_type": "PermissionError",
        "source_location": "external",
        "message": "knowledge extraction failed before durable Step2 completion",
    }
    assert "Authorization" not in metadata_text
    assert "Bearer" not in metadata_text
    assert "https://" not in metadata_text
    assert "C:\\" not in metadata_text


def test_pilot_persists_safe_parse_diagnostic_without_extra_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    output_root = tmp_path / "pilot-output"
    secret = "Bearer parse-secret https://provider.invalid/v1"

    def fake_runner(**_kwargs: object) -> Any:
        try:
            raise ValueError(secret)
        except ValueError as caught:
            diagnostic = runner_module._safe_knowledge_extraction_diagnostic(
                caught,
                parse_diagnostic={
                    "parse_result": "json_decode_error",
                    "response_length": 42,
                    "finish_reason": "stop",
                    "completion_received": True,
                },
            )
        raise ExamProjectRunError(
            "knowledge_extraction_failed",
            diagnostic=diagnostic,
        )

    monkeypatch.setattr(pilot, "run_exam_project_v1", fake_runner)

    with pytest.raises(ExamProjectRunError):
        pilot.run_exam_project_ai_pilot_v1(
            pdf_path=pdf_path,
            output_root=output_root,
            profile=_profile(),
            dependencies=_dependencies(tmp_path),
        )

    metadata_text = (output_root / "run_metadata.json").read_text(encoding="utf-8")
    payload = json.loads(metadata_text)
    assert payload["failure_code"] == "knowledge_extraction_failed"
    assert payload["diagnostic"]["parse"] == {
        "parse_result": "json_decode_error",
        "response_length": 42,
        "finish_reason": "stop",
    }
    assert "completion_received" not in payload["diagnostic"]["parse"]
    assert secret not in metadata_text
    assert "Authorization" not in metadata_text
    assert "Bearer" not in metadata_text
    assert "https://" not in metadata_text


def test_main_requires_explicit_runtime_factory_without_accepting_secrets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    code = pilot.main(("--pdf", str(pdf_path), "--output-root", str(tmp_path / "out")))
    captured = capsys.readouterr()
    assert code == 2
    assert "runtime dependencies must be injected" in captured.err
    assert "secret" not in captured.err.casefold()


def test_main_accepts_explicit_paths_and_injected_runtime_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    output_root = tmp_path / "out"
    profile = _profile()
    dependencies = _dependencies(tmp_path)
    result = _result(output_root)

    def fake_runner(**_kwargs: object) -> Any:
        return result

    monkeypatch.setattr(pilot, "run_exam_project_v1", fake_runner)

    code = pilot.main(
        ("--pdf", str(pdf_path), "--output-root", str(output_root)),
        runtime_factory=lambda **_kwargs: pilot.ExamProjectPilotRuntimeV1(
            profile=profile,
            dependencies=dependencies,
        ),
    )

    assert code == 0


def test_pilot_rejects_relative_output_root_without_running(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    with pytest.raises(pilot.ExamProjectPilotConfigurationError) as raised:
        pilot.run_exam_project_ai_pilot_v1(
            pdf_path=pdf_path,
            output_root=Path("relative-output"),
            profile=_profile(),
            dependencies=_dependencies(tmp_path),
        )
    assert raised.value.code == "pilot_output_root_required"
