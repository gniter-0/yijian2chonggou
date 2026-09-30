"""Composition helper for injected AI dependencies of the exam-project runner."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeAlias

from examcard.evidence.ocr import OCRProvider
from examcard.evidence.vision import OpenAIVisionVerifier
from examcard.knowledge.llm import Step2OpenAIAdapter, Step2Transport

from .exam_project_runner_v1 import ExamProjectDependenciesV1, EvidenceIngestorV1


__all__ = ["ImageCallerV1", "build_exam_project_dependencies_v1"]


ImageCallerV1: TypeAlias = Callable[[Mapping[str, object]], tuple[int, bytes]]


def build_exam_project_dependencies_v1(
    *,
    storage_root: Path,
    transport: Step2Transport,
    idempotency_supported: bool,
    image_caller: ImageCallerV1 | None = None,
    evidence_engine: EvidenceIngestorV1 | None = None,
    ocr_provider: OCRProvider | None = None,
    vision_verifier: OpenAIVisionVerifier | None = None,
) -> ExamProjectDependenciesV1:
    """Compose runner dependencies from explicitly injected runtime objects.

    This function constructs the existing Step2 adapter only.  It does not
    acquire credentials, construct a provider client, perform network I/O, or
    persist anything itself.  ``storage_root`` is the adapter's existing
    response/journal root and must be supplied by the caller as an isolated
    runtime directory.
    """

    if not isinstance(storage_root, Path):
        raise TypeError("exam project storage root must be a Path")
    if not isinstance(idempotency_supported, bool):
        raise TypeError("Step 2 idempotency support must be a bool")
    if not callable(getattr(transport, "complete", None)):
        raise TypeError("exam project Step 2 transport is invalid")
    if image_caller is not None and not callable(image_caller):
        raise TypeError("exam project image caller is invalid")

    adapter = Step2OpenAIAdapter(
        storage_root=storage_root,
        transport=transport,
        idempotency_supported=idempotency_supported,
    )
    return ExamProjectDependenciesV1(
        step2_adapter=adapter,
        image_caller=image_caller,
        evidence_engine=evidence_engine,
        ocr_provider=ocr_provider,
        vision_verifier=vision_verifier,
    )
