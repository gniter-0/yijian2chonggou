"""Offline tests for pilot response-mode configuration and runner wiring."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "migration"))

import run_exam_project_ai_pilot_v1 as pilot
from examcard.evidence.ingest import EvidenceEngineConfig
from examcard.knowledge.contracts import Step2DecoderConfig, Step2ResponseFormat
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from examcard.materials.schema import ContentPolicy, MaterialProfile, SemanticRole, SourceKind
from examcard.pipeline.exam_project_runner_v1 import (
    ExamProjectDependenciesV1,
    run_exam_project_v1,
)
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
        image_profile=build_image_profile(
            model="fake-image-v1",
            image_width=128,
            image_height=128,
        ),
    )


def _env(mode: str | None = None) -> dict[str, str]:
    env = {
        "API_BASE_URL": "https://text.example/v1",
        "API_KEY": "offline-text-secret",
        "TEXT_MODEL": "text-model-v1",
        "IMAGE_API_BASE_URL": "https://image.example/v1",
        "IMAGE_API_KEY": "offline-image-secret",
        "IMAGE_MODEL": "image-model-v1",
    }
    if mode is not None:
        env["STEP2_RESPONSE_MODE"] = mode
    return env


class _Closable:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _FakeTransport:
    instances: list["_FakeTransport"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self._client = _Closable()
        self.__class__.instances.append(self)


class _FakeHttpClient:
    instances: list["_FakeHttpClient"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.post_calls = 0
        self.close_calls = 0
        self.__class__.instances.append(self)

    def post(self, *_args: object, **_kwargs: object) -> Any:
        self.post_calls += 1
        raise AssertionError("response-mode runtime construction must not call image provider")

    def close(self) -> None:
        self.close_calls += 1


def _build_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str | None = None,
) -> pilot.ExamProjectPilotRuntimeV1:
    dependencies = cast(ExamProjectDependenciesV1, object())
    monkeypatch.setattr(pilot, "load_run_env", lambda: _env(mode))
    monkeypatch.setattr(pilot, "OpenAICompatibleStep2Transport", _FakeTransport)
    monkeypatch.setattr(pilot.httpx, "Client", _FakeHttpClient)
    monkeypatch.setattr(
        pilot,
        "build_exam_project_dependencies_v1",
        lambda **_kwargs: dependencies,
    )
    monkeypatch.setattr(pilot, "load_material_profile", lambda _path: _profile().material_profile)
    monkeypatch.setattr(pilot, "load_content_policy", lambda _path: _profile().content_policy)
    return pilot.build_default_exam_project_pilot_runtime_v1(
        pdf_path=tmp_path / "input.pdf",
        output_root=tmp_path / "run",
    )


def test_default_mode_is_strict_and_runtime_construction_is_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeTransport.instances.clear()
    _FakeHttpClient.instances.clear()
    runtime = _build_runtime(tmp_path, monkeypatch)

    assert runtime.step2_decoder_config is not None
    assert runtime.step2_decoder_config.response_format == Step2ResponseFormat.JSON_SCHEMA_STRICT
    assert len(_FakeTransport.instances) == 1
    assert len(_FakeHttpClient.instances) == 1
    assert _FakeHttpClient.instances[0].post_calls == 0

    runtime.close()
    assert _FakeTransport.instances[0]._client.close_calls == 1
    assert _FakeHttpClient.instances[0].close_calls == 1


def test_json_object_mode_is_explicitly_carried_by_pilot_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _build_runtime(tmp_path, monkeypatch, mode="JSON_OBJECT")

    assert runtime.step2_decoder_config is not None
    assert runtime.step2_decoder_config.response_format == Step2ResponseFormat.JSON_OBJECT
    runtime.close()


def test_invalid_response_mode_fails_closed_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeTransport.instances.clear()
    monkeypatch.setattr(pilot, "load_run_env", lambda: _env("UNSUPPORTED"))
    monkeypatch.setattr(
        pilot,
        "OpenAICompatibleStep2Transport",
        lambda **_kwargs: pytest.fail("invalid response mode must stop before client creation"),
    )

    with pytest.raises(pilot.ExamProjectPilotConfigurationError) as raised:
        pilot.build_default_exam_project_pilot_runtime_v1(
            pdf_path=tmp_path / "input.pdf",
            output_root=tmp_path / "run",
        )

    assert raised.value.code == "pilot_configuration_invalid"


def test_explicit_runtime_factory_forwards_decoder_without_touching_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    decoder = Step2DecoderConfig(response_format=Step2ResponseFormat.JSON_OBJECT)
    captured: list[Step2DecoderConfig | None] = []

    runtime = pilot.ExamProjectPilotRuntimeV1(
        profile=cast(Any, object()),
        dependencies=cast(ExamProjectDependenciesV1, object()),
        step2_decoder_config=decoder,
    )

    def fake_pilot(**kwargs: object) -> None:
        captured.append(cast(Step2DecoderConfig | None, kwargs["step2_decoder_config"]))

    monkeypatch.setattr(pilot, "run_exam_project_ai_pilot_v1", fake_pilot)
    code = pilot.main(
        ("--pdf", str(pdf_path), "--output-root", str(tmp_path / "run")),
        runtime_factory=lambda **_kwargs: runtime,
    )

    assert code == 0
    assert captured == [decoder]


def test_runner_passes_decoder_to_existing_document_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoder = Step2DecoderConfig(response_format=Step2ResponseFormat.JSON_OBJECT)
    captured: list[object] = []

    class _Evidence:
        def ingest_document(self, *_args: object, **_kwargs: object):
            return cast(Any, object()), {}

    def fake_fact_pipeline(**kwargs: object) -> Any:
        captured.append(kwargs["decoder"])
        return SimpleNamespace(cards=())

    dependencies = ExamProjectDependenciesV1(
        step2_adapter=cast(Step2OpenAIAdapter, object()),
        image_caller=lambda _payload: (200, b""),
        evidence_engine=cast(Any, _Evidence()),
    )
    monkeypatch.setattr(
        "examcard.pipeline.exam_project_runner_v1.run_document_fact_pipeline",
        fake_fact_pipeline,
    )

    result = run_exam_project_v1(
        pdf_path=tmp_path / "input.pdf",
        profile=_profile(),
        output_root=tmp_path / "output",
        cache_root=tmp_path / "cache",
        dependencies=dependencies,
        step2_decoder_config=decoder,
    )

    assert captured == [decoder]
    assert result.cards == ()
