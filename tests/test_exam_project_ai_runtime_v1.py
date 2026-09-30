"""Offline tests for exam-project AI dependency composition."""
from __future__ import annotations

from pathlib import Path

import pytest

from examcard.knowledge.llm import CallableStep2Transport, Step2OpenAIAdapter, TransportCompletion
from examcard.pipeline.exam_project_ai_runtime_v1 import build_exam_project_dependencies_v1
from examcard.pipeline.exam_project_runner_v1 import ExamProjectDependenciesV1
from examcard.providers.image_caller_v1 import OpenAICompatibleImageCallerV1


def _transport() -> CallableStep2Transport:
    def callback(**_kwargs: object) -> TransportCompletion:
        return TransportCompletion(content='{"proposals": []}')

    return CallableStep2Transport(callback)


def test_composition_wraps_existing_step2_adapter_and_preserves_injected_caller(tmp_path: Path) -> None:
    image_caller = OpenAICompatibleImageCallerV1(
        client=object(),  # type: ignore[arg-type]
        base_url="https://images.example/v1",
    )
    transport = _transport()

    dependencies = build_exam_project_dependencies_v1(
        storage_root=tmp_path / "step2",
        transport=transport,
        idempotency_supported=True,
        image_caller=image_caller,
    )

    assert isinstance(dependencies, ExamProjectDependenciesV1)
    assert isinstance(dependencies.step2_adapter, Step2OpenAIAdapter)
    assert dependencies.step2_adapter.transport is transport
    assert dependencies.step2_adapter.idempotency_supported is True
    assert dependencies.image_caller is image_caller
    assert not (tmp_path / "step2").exists()


def test_composition_allows_no_image_caller_for_runner_dry_run(tmp_path: Path) -> None:
    dependencies = build_exam_project_dependencies_v1(
        storage_root=tmp_path / "step2",
        transport=_transport(),
        idempotency_supported=False,
    )

    assert dependencies.image_caller is None
    assert not (tmp_path / "step2").exists()


@pytest.mark.parametrize(
    "transport",
    (object(), None),
)
def test_composition_rejects_invalid_transport_without_network(tmp_path: Path, transport: object) -> None:
    with pytest.raises(TypeError, match="transport is invalid"):
        build_exam_project_dependencies_v1(
            storage_root=tmp_path / "step2",
            transport=transport,  # type: ignore[arg-type]
            idempotency_supported=False,
        )


def test_composition_does_not_define_delivery_authority() -> None:
    assert not hasattr(build_exam_project_dependencies_v1, "upload")
    assert not hasattr(build_exam_project_dependencies_v1, "lookup")
    assert not hasattr(build_exam_project_dependencies_v1, "retry")
    assert not hasattr(build_exam_project_dependencies_v1, "create_identity")
    assert not hasattr(build_exam_project_dependencies_v1, "create_receipt")
    assert not hasattr(build_exam_project_dependencies_v1, "mutate_checkpoint")
