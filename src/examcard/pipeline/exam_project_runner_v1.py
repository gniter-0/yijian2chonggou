"""Single-PDF exam project runtime.

The runner composes the existing evidence, knowledge, and image-provider
contracts.  It deliberately owns no delivery identity, retry policy, receipt,
checkpoint, credential, or provider-client construction behavior.
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Literal, NoReturn, Protocol, TypeAlias, cast

from examcard.common.hashing import sha256_bytes
from examcard.compilers.step4_knowledge_card_bridge_v1 import (
    KnowledgeCardImageRequestV1,
    build_knowledge_card_image_request_v1,
    project_knowledge_card_image_request_v1,
)
from examcard.evidence.ingest import EvidenceEngine
from examcard.evidence.ocr import OCRProvider
from examcard.evidence.schema import DocumentEvidenceManifest
from examcard.evidence.vision import OpenAIVisionVerifier
from examcard.knowledge.extraction_v1 import (
    DocumentFactPipelineResult,
    run_document_fact_pipeline,
)
from examcard.knowledge.contracts import Step2DecoderConfig
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from examcard.knowledge.schema import KnowledgeCard
from examcard.projects.exam_subject_profile_v1 import ExamSubjectProfileV1
from examcard.providers.envelope_profiles import (
    ImageEnvelopeGate,
    ImageEnvelopeProfile,
    ImageRequestProjection,
    inspect_image_envelope,
)

__all__ = [
    "ExamProjectDependenciesV1",
    "ExamProjectImageResultV1",
    "ExamProjectRunError",
    "ExamProjectRunErrorCode",
    "ExamProjectRunDiagnostic",
    "ExamProjectRunResultV1",
    "run_exam_project_v1",
]


ImageCallerV1: TypeAlias = Callable[[Mapping[str, object]], tuple[int, bytes]]
ExamProjectRunErrorCode: TypeAlias = Literal[
    "invalid_profile",
    "evidence_ingestion_failed",
    "knowledge_extraction_failed",
    "card_compilation_failed",
    "image_caller_required",
    "image_generation_failed",
    "image_response_invalid",
    "image_output_failed",
]
ExamProjectRunDiagnosticStage: TypeAlias = Literal[
    "page_source_paths",
    "projection",
    "request_build",
    "adapter_resume",
    "prompt_verify",
    "request_started",
    "provider_call",
    "grounding",
    "knowledge_extraction",
]


ExamProjectRunDiagnostic: TypeAlias = dict[str, object]


_SAFE_DIAGNOSTIC_MESSAGE: Final = (
    "knowledge extraction failed before durable Step2 completion"
)
_SAFE_DIAGNOSTIC_STAGES: Final[frozenset[str]] = frozenset(
    {
        "page_source_paths",
        "projection",
        "request_build",
        "adapter_resume",
        "prompt_verify",
        "request_started",
        "provider_call",
        "grounding",
        "knowledge_extraction",
    }
)
_SAFE_EXCEPTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "PermissionError",
        "FileNotFoundError",
        "OSError",
        "ValueError",
        "TypeError",
        "RuntimeError",
    }
)
_SAFE_PARSE_RESULTS: Final[frozenset[str]] = frozenset(
    {"success", "json_decode_error"}
)
_SAFE_FINISH_REASONS: Final[frozenset[str]] = frozenset(
    {"stop", "length", "tool_calls", "content_filter", "function_call"}
)
_STAGE_BY_FUNCTION: Final[dict[str, ExamProjectRunDiagnosticStage]] = {
    "_page_source_paths": "page_source_paths",
    "page_source_paths": "page_source_paths",
    "build_step2a_semantic_projection": "projection",
    "build_projection": "projection",
    "projection": "projection",
    "build_step2a_request": "request_build",
    "build_request": "request_build",
    "request_build": "request_build",
    "_resume_record_a": "adapter_resume",
    "_resume_record_b": "adapter_resume",
    "adapter_resume": "adapter_resume",
    "_verified_prompt": "prompt_verify",
    "verified_prompt": "prompt_verify",
    "prompt_verify": "prompt_verify",
    "request_started": "request_started",
    "_raw_create": "provider_call",
    "_raw_create_streaming": "provider_call",
    "provider_call": "provider_call",
    "ground_step2a_proposals": "grounding",
    "validate_grounding": "grounding",
    "grounding": "grounding",
}


def _safe_exception_type(error: BaseException) -> str:
    name = type(error).__name__
    return name if name in _SAFE_EXCEPTION_TYPES else "Exception"


def _traceback_frames(traceback: TracebackType | None) -> tuple[TracebackType, ...]:
    frames: list[TracebackType] = []
    current = traceback
    while current is not None:
        frames.append(current)
        current = current.tb_next
    return tuple(frames)


def _stage_from_frame(frame: TracebackType) -> ExamProjectRunDiagnosticStage | None:
    function_name = frame.tb_frame.f_code.co_name
    direct = _STAGE_BY_FUNCTION.get(function_name)
    if direct is not None:
        return direct

    # Journal failures do not have a dedicated function for the event.  The
    # enum value is a fixed internal label, not source/provider content.
    if function_name in {"_append", "_append_event"}:
        candidate = frame.tb_frame.f_locals.get("outcome")
        if candidate is None:
            try:
                candidate = getattr(frame.tb_frame.f_locals.get("event"), "outcome", None)
            except Exception:
                candidate = None
        try:
            candidate_value = getattr(candidate, "value", None)
        except Exception:
            candidate_value = None
        if candidate_value == "request_started":
            return "request_started"
    return None


def _stage_from_traceback(
    traceback: TracebackType | None,
) -> ExamProjectRunDiagnosticStage:
    # The innermost recognized frame is the strongest local evidence for the
    # failing seam.  Unrecognized frames never become a guessed stage.
    for frame in reversed(_traceback_frames(traceback)):
        stage = _stage_from_frame(frame)
        if stage is not None:
            return stage
    return "knowledge_extraction"


def _safe_source_location(traceback: TracebackType | None) -> str:
    source_root = Path(__file__).resolve().parents[2]
    for frame in reversed(_traceback_frames(traceback)):
        filename = Path(frame.tb_frame.f_code.co_filename)
        try:
            relative = filename.resolve().relative_to(source_root)
        except (OSError, RuntimeError, ValueError):
            continue
        if (
            not relative.parts
            or relative.parts[0] != "examcard"
            or relative.suffix != ".py"
        ):
            continue
        function_name = frame.tb_frame.f_code.co_name
        if not function_name.isidentifier() or not function_name.isascii():
            continue
        line_number = frame.tb_lineno
        if line_number < 1:
            continue
        return f"{relative.as_posix()}:{line_number}:{function_name}"
    return "external"


def _sanitize_diagnostic_mapping(
    diagnostic: Mapping[str, object] | None,
) -> ExamProjectRunDiagnostic | None:
    if diagnostic is None:
        return None
    stage_value = diagnostic.get("stage")
    stage = (
        cast(ExamProjectRunDiagnosticStage, stage_value)
        if isinstance(stage_value, str) and stage_value in _SAFE_DIAGNOSTIC_STAGES
        else "knowledge_extraction"
    )
    exception_type = diagnostic.get("exception_type")
    safe_exception_type = (
        exception_type
        if isinstance(exception_type, str) and exception_type in _SAFE_EXCEPTION_TYPES
        else "Exception"
    )
    source_location = diagnostic.get("source_location")
    safe_source_location = (
        source_location
        if isinstance(source_location, str)
        and source_location == "external"
        else "external"
    )
    if isinstance(source_location, str) and source_location != "external":
        location_parts = source_location.split(":")
        if len(location_parts) == 3:
            path, line, function = location_parts
            path_parts = path.split("/")
            if (
                path.startswith("examcard/")
                and path.endswith(".py")
                and all(part not in {"", ".", ".."} for part in path_parts)
                and line.isdigit()
                and int(line) > 0
                and function.isidentifier()
                and function.isascii()
            ):
                safe_source_location = source_location
    sanitized: ExamProjectRunDiagnostic = {
        "stage": stage,
        "exception_type": safe_exception_type,
        "source_location": safe_source_location,
        "message": _SAFE_DIAGNOSTIC_MESSAGE,
    }
    parse = _sanitize_parse_diagnostic(diagnostic.get("parse"))
    if parse is not None:
        sanitized["parse"] = parse
    return sanitized


def _sanitize_parse_diagnostic(
    diagnostic: object,
) -> dict[str, object] | None:
    if not isinstance(diagnostic, Mapping):
        return None
    parse_result = diagnostic.get("parse_result")
    response_length = diagnostic.get("response_length")
    finish_reason = diagnostic.get("finish_reason")
    completion_received = diagnostic.get("completion_received")
    if (
        not isinstance(parse_result, str)
        or parse_result not in _SAFE_PARSE_RESULTS
        or not isinstance(response_length, int)
        or isinstance(response_length, bool)
        or response_length < 0
        or not isinstance(completion_received, bool)
        or (
            finish_reason is not None
            and (
                not isinstance(finish_reason, str)
                or finish_reason not in _SAFE_FINISH_REASONS
            )
        )
    ):
        return None
    return {
        "parse_result": parse_result,
        "response_length": response_length,
        "finish_reason": finish_reason,
        "completion_received": completion_received,
    }


def _safe_knowledge_extraction_diagnostic(
    error: BaseException,
    *,
    parse_diagnostic: Mapping[str, object] | None = None,
) -> ExamProjectRunDiagnostic:
    """Build fixed/whitelisted diagnostics without reading exception text."""

    diagnostic: ExamProjectRunDiagnostic = {
        "stage": _stage_from_traceback(error.__traceback__),
        "exception_type": _safe_exception_type(error),
        "source_location": _safe_source_location(error.__traceback__),
        "message": _SAFE_DIAGNOSTIC_MESSAGE,
    }
    parse = _sanitize_parse_diagnostic(parse_diagnostic)
    if parse is not None:
        diagnostic["parse"] = parse
    return diagnostic

_ERROR_MESSAGES: Final[dict[ExamProjectRunErrorCode, str]] = {
    "invalid_profile": "exam project profile is invalid",
    "evidence_ingestion_failed": "exam project could not ingest the PDF",
    "knowledge_extraction_failed": "exam project could not extract grounded knowledge",
    "card_compilation_failed": "exam project could not compile a knowledge card image request",
    "image_caller_required": "exam project image caller is required for execution",
    "image_generation_failed": "exam project image generation failed",
    "image_response_invalid": "exam project image response failed validation",
    "image_output_failed": "exam project could not write image output",
}


class ExamProjectRunError(RuntimeError):
    """Fixed-text failure that cannot expose provider or source internals."""

    code: ExamProjectRunErrorCode
    _diagnostic: ExamProjectRunDiagnostic | None

    def __init__(
        self,
        code: ExamProjectRunErrorCode,
        *,
        diagnostic: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code if code in _ERROR_MESSAGES else "image_output_failed"
        self._diagnostic = (
            _sanitize_diagnostic_mapping(diagnostic)
            if self.code == "knowledge_extraction_failed"
            else None
        )
        super().__init__(_ERROR_MESSAGES[self.code])

    @property
    def diagnostic(self) -> ExamProjectRunDiagnostic | None:
        """Return a copy so callers cannot mutate the sanitized representation."""

        if self._diagnostic is None:
            return None
        diagnostic = dict(self._diagnostic)
        parse = diagnostic.get("parse")
        if isinstance(parse, dict):
            diagnostic["parse"] = dict(parse)
        return diagnostic

    def __repr__(self) -> str:
        return f"ExamProjectRunError(code={self.code!r})"


class EvidenceIngestorV1(Protocol):
    """Minimal evidence seam used by the runner and deterministic tests."""

    def ingest_document(
        self,
        pdf_path: Path,
        *,
        source_relative_path: str | None = None,
        output_dir: Path | None = None,
    ) -> tuple[DocumentEvidenceManifest, dict[str, object]]: ...


@dataclass(frozen=True, slots=True, repr=False)
class ExamProjectDependenciesV1:
    """Injected runtime dependencies; no dependency is persisted by the runner."""

    step2_adapter: Step2OpenAIAdapter
    image_caller: ImageCallerV1 | None = None
    evidence_engine: EvidenceIngestorV1 | None = None
    ocr_provider: OCRProvider | None = None
    vision_verifier: OpenAIVisionVerifier | None = None

    def __repr__(self) -> str:
        return "<exam-project-dependencies-v1>"

    def __str__(self) -> str:
        return "<exam-project-dependencies-v1>"


@dataclass(frozen=True, slots=True)
class ExamProjectImageResultV1:
    """Safe in-memory result for one card image."""

    card_id: str
    raw_id: str
    request_id: str
    request_sha256: str
    provenance_sha256: str
    source_pages: tuple[int, ...]
    output_path: Path | None
    image_sha256: str | None


@dataclass(frozen=True, slots=True)
class ExamProjectRunResultV1:
    """Complete result of one PDF composition/execution run."""

    profile_sha256: str
    manifest: DocumentEvidenceManifest
    evidence_report: dict[str, object]
    fact_pipeline: DocumentFactPipelineResult
    cards: tuple[KnowledgeCard, ...]
    requests: tuple[KnowledgeCardImageRequestV1, ...]
    projections: tuple[ImageRequestProjection, ...]
    images: tuple[ExamProjectImageResultV1, ...]
    dry_run: bool

    @property
    def document_sha256(self) -> str:
        return self.manifest.source.sha256


@dataclass(frozen=True, slots=True)
class _PendingImageV1:
    result: ExamProjectImageResultV1
    image_bytes: bytes


def _new_evidence_engine(
    *,
    profile: ExamSubjectProfileV1,
    cache_root: Path,
    ocr_provider: OCRProvider | None,
    vision_verifier: OpenAIVisionVerifier | None,
) -> EvidenceEngine:
    return EvidenceEngine(
        profile=profile.material_profile,
        cache_root=cache_root,
        config=profile.evidence_config,
        ocr_provider=ocr_provider,
        vision_verifier=vision_verifier,
    )


def _raise_without_provider_context(
    code: ExamProjectRunErrorCode,
    *,
    diagnostic: Mapping[str, object] | None = None,
) -> NoReturn:
    """Raise after an exception handler has ended, avoiding exception chaining."""

    raise ExamProjectRunError(code, diagnostic=diagnostic)


def _validated_image_bytes(
    *,
    caller: ImageCallerV1,
    payload: Mapping[str, object],
    prompt: str,
    profile: ImageEnvelopeProfile,
) -> tuple[bytes, ImageEnvelopeGate]:
    call_failed = False
    status = 0
    body: bytes = b""
    try:
        status, body = caller(payload)
    except Exception:
        call_failed = True
    if call_failed:
        _raise_without_provider_context("image_generation_failed")

    gate: ImageEnvelopeGate | None = None
    gate_failed = False
    try:
        gate = inspect_image_envelope(status, body, prompt, profile)
    except Exception:
        gate_failed = True
    if gate_failed or gate is None or not gate.valid or gate.image_sha256 is None:
        _raise_without_provider_context("image_response_invalid")

    decode_failed = False
    image_bytes = b""
    try:
        document = json.loads(body.decode("utf-8"))
        encoded = document["data"][0]["b64_json"]
        image_bytes = base64.b64decode(encoded, validate=True)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        decode_failed = True
    if decode_failed or sha256_bytes(image_bytes) != gate.image_sha256:
        _raise_without_provider_context("image_response_invalid")
    return image_bytes, gate


def _image_output_path(output_root: Path, raw_id: str) -> Path:
    """Map the validated card identity to a stable, Windows-safe local path."""

    return output_root / "images" / f"card-{raw_id.removeprefix('kp:')}.png"


def run_exam_project_v1(
    *,
    pdf_path: Path,
    profile: ExamSubjectProfileV1,
    output_root: Path,
    cache_root: Path,
    dependencies: ExamProjectDependenciesV1,
    dry_run: bool = False,
    source_relative_path: str | None = None,
    sample_id: str | None = None,
    step2_decoder_config: Step2DecoderConfig | None = None,
) -> ExamProjectRunResultV1:
    """Run one PDF through evidence, knowledge, card bridge, and image caller.

    The function is intentionally a composition seam.  It does not infer an
    exam subject, create provider clients, retry calls, or persist pipeline
    authority state.  In dry-run mode it builds and returns all public image
    requests without invoking the injected image caller or writing PNGs.
    """

    if not isinstance(profile, ExamSubjectProfileV1):
        _raise_without_provider_context("invalid_profile")
    if not isinstance(dependencies, ExamProjectDependenciesV1):
        _raise_without_provider_context("invalid_profile")
    if not dry_run and dependencies.image_caller is None:
        _raise_without_provider_context("image_caller_required")

    evidence_engine = dependencies.evidence_engine
    if evidence_engine is None:
        construct_failed = False
        try:
            evidence_engine = _new_evidence_engine(
                profile=profile,
                cache_root=Path(cache_root),
                ocr_provider=dependencies.ocr_provider,
                vision_verifier=dependencies.vision_verifier,
            )
        except Exception:
            construct_failed = True
        if construct_failed or evidence_engine is None:
            _raise_without_provider_context("evidence_ingestion_failed")

    ingest_failed = False
    manifest: DocumentEvidenceManifest | None = None
    evidence_report: dict[str, object] = {}
    try:
        manifest, evidence_report = evidence_engine.ingest_document(
            Path(pdf_path),
            source_relative_path=source_relative_path,
            output_dir=None if dry_run else Path(output_root) / "evidence",
        )
    except Exception:
        ingest_failed = True
    if ingest_failed or manifest is None:
        _raise_without_provider_context("evidence_ingestion_failed")

    extraction_failed = False
    fact_pipeline: DocumentFactPipelineResult | None = None
    extraction_diagnostic: ExamProjectRunDiagnostic | None = None
    try:
        fact_pipeline = run_document_fact_pipeline(
            manifest=manifest,
            content_policy=profile.content_policy,
            adapter=dependencies.step2_adapter,
            model=profile.step2_model,
            material_profile_id=profile.material_profile.profile_id,
            sample_id=sample_id,
            decoder=step2_decoder_config,
        )
    except Exception as error:
        extraction_failed = True
        adapter_parse_diagnostic = getattr(
            dependencies.step2_adapter,
            "last_parse_diagnostic",
            None,
        )
        extraction_diagnostic = _safe_knowledge_extraction_diagnostic(
            error,
            parse_diagnostic=adapter_parse_diagnostic,
        )
    if extraction_failed or fact_pipeline is None:
        _raise_without_provider_context(
            "knowledge_extraction_failed",
            diagnostic=extraction_diagnostic,
        )

    cards = fact_pipeline.cards
    requests: list[KnowledgeCardImageRequestV1] = []
    projections: list[ImageRequestProjection] = []
    compile_failed = False
    try:
        for card in cards:
            request = build_knowledge_card_image_request_v1(
                card,
                profile=profile.image_profile,
            )
            projection, _sidecar = project_knowledge_card_image_request_v1(
                request,
                profile=profile.image_profile,
            )
            requests.append(request)
            projections.append(projection)
    except Exception:
        compile_failed = True
    if compile_failed:
        _raise_without_provider_context("card_compilation_failed")

    if dry_run:
        images = tuple(
            ExamProjectImageResultV1(
                card_id=request.card_id,
                raw_id=request.raw_id,
                request_id=request.request_id,
                request_sha256=request.request_sha256,
                provenance_sha256=request.provenance_sha256,
                source_pages=request.source_pages,
                output_path=None,
                image_sha256=None,
            )
            for request in requests
        )
        return ExamProjectRunResultV1(
            profile_sha256=profile.canonical_sha256(),
            manifest=manifest,
            evidence_report=evidence_report,
            fact_pipeline=fact_pipeline,
            cards=cards,
            requests=tuple(requests),
            projections=tuple(projections),
            images=images,
            dry_run=True,
        )

    caller = cast(ImageCallerV1, dependencies.image_caller)
    pending: list[_PendingImageV1] = []
    seen_paths: set[Path] = set()
    for request, projection in zip(requests, projections, strict=True):
        output_path = _image_output_path(Path(output_root), request.raw_id)
        if output_path in seen_paths:
            _raise_without_provider_context("image_output_failed")
        seen_paths.add(output_path)
        image_bytes, gate = _validated_image_bytes(
            caller=caller,
            payload=projection.http_request.model_dump(mode="json"),
            prompt=request.prompt_text,
            profile=profile.image_profile,
        )
        pending.append(
            _PendingImageV1(
                result=ExamProjectImageResultV1(
                    card_id=request.card_id,
                    raw_id=request.raw_id,
                    request_id=request.request_id,
                    request_sha256=request.request_sha256,
                    provenance_sha256=request.provenance_sha256,
                    source_pages=request.source_pages,
                    output_path=output_path,
                    image_sha256=gate.image_sha256,
                ),
                image_bytes=image_bytes,
            )
        )

    write_failed = False
    try:
        for item in pending:
            assert item.result.output_path is not None
            item.result.output_path.parent.mkdir(parents=True, exist_ok=True)
            item.result.output_path.write_bytes(item.image_bytes)
    except Exception:
        write_failed = True
    if write_failed:
        _raise_without_provider_context("image_output_failed")

    return ExamProjectRunResultV1(
        profile_sha256=profile.canonical_sha256(),
        manifest=manifest,
        evidence_report=evidence_report,
        fact_pipeline=fact_pipeline,
        cards=cards,
        requests=tuple(requests),
        projections=tuple(projections),
        images=tuple(item.result for item in pending),
        dry_run=False,
    )
