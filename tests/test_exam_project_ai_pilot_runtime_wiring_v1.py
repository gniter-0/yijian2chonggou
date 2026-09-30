from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "migration"))

import run_exam_project_ai_pilot_v1 as pilot
from examcard.pipeline.exam_project_runner_v1 import ExamProjectDependenciesV1


SECRET = "runtime-wiring-test-secret"


def _env() -> dict[str, str]:
    return {
        "API_BASE_URL": "https://text.example/v1",
        "API_KEY": SECRET,
        "TEXT_MODEL": "text-model-v1",
        "IMAGE_API_BASE_URL": "https://image.example/v1",
        "IMAGE_API_KEY": "image-" + SECRET,
        "IMAGE_MODEL": "image-model-v1",
    }


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
        self.complete_calls = 0
        self.__class__.instances.append(self)


class _FakeHttpClient:
    instances: list["_FakeHttpClient"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.close_calls = 0
        self.post_calls = 0
        self.__class__.instances.append(self)

    def close(self) -> None:
        self.close_calls += 1

    def post(self, *_args: object, **_kwargs: object) -> Any:
        self.post_calls += 1
        raise AssertionError("runtime wiring must not call the image provider")


def test_default_factory_composes_existing_boundaries_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeTransport.instances.clear()
    _FakeHttpClient.instances.clear()
    dependencies = cast(ExamProjectDependenciesV1, object())
    monkeypatch.setattr(pilot, "load_run_env", lambda: _env())
    monkeypatch.setattr(pilot, "OpenAICompatibleStep2Transport", _FakeTransport)
    monkeypatch.setattr(pilot.httpx, "Client", _FakeHttpClient)
    monkeypatch.setattr(
        pilot, "build_exam_project_dependencies_v1", lambda **_kwargs: dependencies
    )

    runtime = pilot.build_default_exam_project_pilot_runtime_v1(
        pdf_path=tmp_path / "input.pdf",
        output_root=tmp_path / "run",
    )

    assert len(_FakeTransport.instances) == 1
    transport = _FakeTransport.instances[0]
    assert transport.kwargs["base_url"] == _env()["API_BASE_URL"]
    assert transport.kwargs["api_key"] == SECRET
    assert len(_FakeHttpClient.instances) == 1
    client = _FakeHttpClient.instances[0]
    headers = cast(dict[str, str], client.kwargs["headers"])
    assert headers["Authorization"] == "Bearer image-" + SECRET
    assert client.post_calls == 0

    assert not hasattr(runtime.dependencies, "upload")
    assert not hasattr(runtime.dependencies, "lookup")
    assert not hasattr(runtime.dependencies, "mutate_checkpoint")
    runtime.close()
    runtime.close()
    assert client.close_calls == 1
    assert transport._client.close_calls == 1


def test_missing_configuration_fails_closed_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pilot,
        "load_run_env",
        lambda: {
            "API_BASE_URL": "https://text.example/v1",
            "TEXT_MODEL": "text-model-v1",
            "IMAGE_MODEL": "image-model-v1",
        },
    )
    monkeypatch.setattr(
        pilot,
        "OpenAICompatibleStep2Transport",
        lambda **_kwargs: pytest.fail(
            "transport must not be created for missing credentials"
        ),
    )

    with pytest.raises(pilot.ExamProjectPilotConfigurationError) as raised:
        pilot.build_default_exam_project_pilot_runtime_v1(
            pdf_path=tmp_path / "input.pdf",
            output_root=tmp_path / "run",
        )

    assert raised.value.code == "pilot_configuration_missing"
    assert "API_KEY" in str(raised.value)


def test_configuration_failure_does_not_echo_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    output_root = tmp_path / "run"
    monkeypatch.setattr(pilot, "load_run_env", lambda: _env())
    monkeypatch.setattr(pilot, "OpenAICompatibleStep2Transport", _FakeTransport)
    monkeypatch.setattr(pilot.httpx, "Client", _FakeHttpClient)
    monkeypatch.setattr(
        pilot,
        "build_exam_project_dependencies_v1",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("provider leaked " + SECRET)
        ),
    )

    code = pilot.main(
        ("--pdf", str(pdf_path), "--output-root", str(output_root)),
        runtime_factory=pilot.build_default_exam_project_pilot_runtime_v1,
    )
    captured = capsys.readouterr()

    assert code == 2
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert not output_root.exists()


def test_explicit_runtime_factory_has_priority_and_closes_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    output_root = tmp_path / "run"
    profile = cast(Any, object())
    dependencies = cast(ExamProjectDependenciesV1, object())
    closed = []
    calls: list[dict[str, object]] = []

    def explicit_factory(**_kwargs: object) -> pilot.ExamProjectPilotRuntimeV1:
        return pilot.ExamProjectPilotRuntimeV1(
            profile=profile,
            dependencies=dependencies,
            close_callback=lambda: closed.append(True),
        )

    def fake_run(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(pilot, "run_exam_project_ai_pilot_v1", fake_run)
    monkeypatch.setattr(
        pilot,
        "build_default_exam_project_pilot_runtime_v1",
        lambda **_kwargs: pytest.fail(
            "default factory must not override explicit injection"
        ),
    )

    code = pilot.main(
        ("--pdf", str(pdf_path), "--output-root", str(output_root)),
        runtime_factory=explicit_factory,
    )

    assert code == 0
    assert len(calls) == 1
    assert closed == [True]
    assert not output_root.exists()
