"""Isolated single-PDF pilot entry point for the exam-project runtime.

The pilot owns only process-level composition and safe reporting.  It accepts
already constructed runtime dependencies so credential acquisition and client
construction remain outside this module.  It does not use delivery, P6/P7
state, checkpoints, receipts, or the existing migration CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, Protocol, TypeAlias

import httpx

# Keep the pilot directly executable from the repository root.  This is only
# import-path setup; runtime configuration and provider clients remain at the
# explicit process boundary below.
NEW_ROOT = Path(__file__).resolve().parents[1]
if str(NEW_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.materials.catalog import load_content_policy, load_material_profile  # noqa: E402
from examcard.knowledge.contracts import Step2DecoderConfig, Step2ResponseFormat  # noqa: E402
from examcard.knowledge.llm_transport import OpenAICompatibleStep2Transport  # noqa: E402
from examcard.pipeline.exam_project_ai_runtime_v1 import (  # noqa: E402
    build_exam_project_dependencies_v1,
)  # noqa: E402
from examcard.pipeline.exam_project_runner_v1 import (  # noqa: E402
    ExamProjectDependenciesV1,
    ExamProjectRunError,
    ExamProjectRunDiagnostic,
    ExamProjectRunResultV1,
    run_exam_project_v1,
)  # noqa: E402
from examcard.pipeline.live_run import (  # noqa: E402
    image_api_key,
    image_base_url,
    load_run_env,
    text_api_key,
    text_base_url,
)  # noqa: E402
from examcard.projects.exam_subject_profile_v1 import ExamSubjectProfileV1  # noqa: E402
from examcard.providers.envelope_profiles import build_image_profile  # noqa: E402
from examcard.providers.image_caller_v1 import OpenAICompatibleImageCallerV1  # noqa: E402


__all__ = [
    "EXAM_PROJECT_PILOT_METADATA_VERSION",
    "ExamProjectPilotConfigurationError",
    "ExamProjectPilotRuntimeV1",
    "ExamProjectPilotRuntimeFactoryV1",
    "build_default_exam_project_pilot_runtime_v1",
    "run_exam_project_ai_pilot_v1",
    "main",
]


EXAM_PROJECT_PILOT_METADATA_VERSION: Final = (
    "examcard_exam_project_ai_pilot_metadata_v1"
)
PilotFailureCode: TypeAlias = Literal[
    "pilot_pdf_required",
    "pilot_output_root_required",
    "pilot_runtime_required",
    "pilot_configuration_missing",
    "pilot_configuration_invalid",
    "pilot_execution_failed",
]

PilotRuntimeCloserV1: TypeAlias = Callable[[], None]


class ExamProjectPilotConfigurationError(RuntimeError):
    """Fixed-text configuration failure safe for CLI output."""

    code: PilotFailureCode

    _MESSAGES: Final[dict[PilotFailureCode, str]] = {
        "pilot_pdf_required": "pilot PDF path is required and must be a file",
        "pilot_output_root_required": "pilot output root must be an absolute directory path",
        "pilot_runtime_required": "pilot runtime dependencies must be injected",
        "pilot_configuration_missing": "pilot runtime configuration is incomplete",
        "pilot_configuration_invalid": "pilot runtime configuration is invalid",
        "pilot_execution_failed": "exam project AI pilot failed",
    }

    def __init__(self, code: PilotFailureCode, *, missing: Sequence[str] = ()) -> None:
        self.code = code
        self.missing = tuple(missing)
        suffix = f"; missing: {', '.join(self.missing)}" if self.missing else ""
        super().__init__(self._MESSAGES[code] + suffix)

    def __repr__(self) -> str:
        return f"ExamProjectPilotConfigurationError(code={self.code!r})"


class ExamProjectPilotRuntimeFactoryV1(Protocol):
    """Process-edge seam for supplying real, already configured dependencies."""

    def __call__(
        self,
        *,
        pdf_path: Path,
        output_root: Path,
    ) -> "ExamProjectPilotRuntimeV1": ...


class ExamProjectPilotRuntimeV1:
    """Non-secret profile and dependency bundle supplied to the pilot."""

    __slots__ = (
        "profile",
        "dependencies",
        "step2_decoder_config",
        "source_relative_path",
        "sample_id",
        "_close_callback",
    )

    def __init__(
        self,
        *,
        profile: ExamSubjectProfileV1,
        dependencies: ExamProjectDependenciesV1,
        step2_decoder_config: Step2DecoderConfig | None = None,
        source_relative_path: str | None = None,
        sample_id: str | None = None,
        close_callback: PilotRuntimeCloserV1 | None = None,
    ) -> None:
        self.profile = profile
        self.dependencies = dependencies
        self.step2_decoder_config = step2_decoder_config
        self.source_relative_path = source_relative_path
        self.sample_id = sample_id
        self._close_callback = close_callback

    def __repr__(self) -> str:
        return "<exam-project-pilot-runtime-v1>"

    def __str__(self) -> str:
        return "<exam-project-pilot-runtime-v1>"

    def __getstate__(self) -> None:
        raise TypeError("exam project pilot runtime is runtime-only")

    def close(self) -> None:
        """Release injected HTTP clients without exposing close errors."""

        callback = self._close_callback
        self._close_callback = None
        if callback is not None:
            try:
                callback()
            except Exception:
                return


def _close_quietly(value: object | None) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return


def _missing_runtime_configuration(env: Mapping[str, str]) -> tuple[str, ...]:
    missing: list[str] = []
    required = (
        ("API_BASE_URL", env.get("API_BASE_URL")),
        ("API_KEY", env.get("API_KEY")),
        ("TEXT_MODEL", env.get("TEXT_MODEL")),
        ("IMAGE_MODEL", env.get("IMAGE_MODEL")),
    )
    for name, value in required:
        if not (value or "").strip():
            missing.append(name)
    if not ((env.get("IMAGE_API_BASE_URL") or env.get("API_BASE_URL") or "").strip()):
        missing.append("IMAGE_API_BASE_URL/API_BASE_URL")
    if not ((env.get("IMAGE_API_KEY") or env.get("API_KEY") or "").strip()):
        missing.append("IMAGE_API_KEY/API_KEY")
    return tuple(dict.fromkeys(missing))


def _step2_decoder_config_from_env(env: Mapping[str, str]) -> Step2DecoderConfig:
    """Parse the non-secret Step 2 response mode at the process edge."""

    raw_mode = (env.get("STEP2_RESPONSE_MODE") or "").strip().upper()
    if not raw_mode or raw_mode == "STRICT_SCHEMA":
        response_format = Step2ResponseFormat.JSON_SCHEMA_STRICT
    elif raw_mode == "JSON_OBJECT":
        response_format = Step2ResponseFormat.JSON_OBJECT
    else:
        raise ExamProjectPilotConfigurationError("pilot_configuration_invalid")
    return Step2DecoderConfig(response_format=response_format)


def build_default_exam_project_pilot_runtime_v1(
    *,
    pdf_path: Path,
    output_root: Path,
) -> ExamProjectPilotRuntimeV1:
    """Build the direct-CLI runtime from the existing injected boundaries.

    ``load_run_env`` is the repository's existing process-edge configuration
    loader.  This function keeps its values in memory, constructs clients only,
    and leaves execution to ``run_exam_project_v1``.
    """

    del pdf_path
    image_client: httpx.Client | None = None
    transport: OpenAICompatibleStep2Transport | None = None
    try:
        env = load_run_env()
        missing = _missing_runtime_configuration(env)
        if missing:
            raise ExamProjectPilotConfigurationError(
                "pilot_configuration_missing", missing=missing
            )
        step2_decoder_config = _step2_decoder_config_from_env(env)

        material_profile = load_material_profile(
            NEW_ROOT
            / "configs"
            / "v2"
            / "material_profiles"
            / "yijian_gonglu_2026.yaml"
        )
        content_policy = load_content_policy(
            NEW_ROOT / "configs" / "v2" / "content_policies" / "core_only_v1.yaml"
        )
        profile = ExamSubjectProfileV1(
            material_profile=material_profile,
            content_policy=content_policy,
            step2_model=env["TEXT_MODEL"].strip(),
            image_profile=build_image_profile(
                model=env["IMAGE_MODEL"].strip(),
                image_width=1024,
                image_height=1536,
            ),
        )

        user_agent = (env.get("OPENAI_USER_AGENT") or "").strip()
        default_headers = {"User-Agent": user_agent} if user_agent else None
        transport = OpenAICompatibleStep2Transport(
            base_url=text_base_url(env),
            api_key=text_api_key(env),
            timeout=600.0,
            default_headers=default_headers,
            stream=True,
        )

        image_key = image_api_key(env)
        image_headers = {
            "Authorization": f"Bearer {image_key}",
            "Content-Type": "application/json",
        }
        if user_agent:
            image_headers["User-Agent"] = user_agent
        image_client = httpx.Client(
            timeout=600.0,
            follow_redirects=False,
            headers=image_headers,
        )
        image_caller = OpenAICompatibleImageCallerV1(
            client=image_client,
            base_url=image_base_url(env),
            endpoint_path="/images/generations",
        )
        dependencies = build_exam_project_dependencies_v1(
            storage_root=Path(output_root) / "step2",
            transport=transport,
            idempotency_supported=False,
            image_caller=image_caller,
        )

        def close_clients() -> None:
            _close_quietly(image_client)
            text_client = getattr(transport, "_client", None)
            _close_quietly(text_client)

        return ExamProjectPilotRuntimeV1(
            profile=profile,
            dependencies=dependencies,
            step2_decoder_config=step2_decoder_config,
            close_callback=close_clients,
        )
    except ExamProjectPilotConfigurationError:
        _close_quietly(image_client)
        _close_quietly(getattr(transport, "_client", None))
        raise
    except Exception:
        _close_quietly(image_client)
        _close_quietly(getattr(transport, "_client", None))
        raise ExamProjectPilotConfigurationError(
            "pilot_configuration_invalid"
        ) from None


def _validate_paths(*, pdf_path: Path, output_root: Path) -> tuple[Path, Path]:
    pdf = Path(pdf_path)
    root = Path(output_root)
    if not pdf.is_file():
        raise ExamProjectPilotConfigurationError("pilot_pdf_required")
    if not root.is_absolute():
        raise ExamProjectPilotConfigurationError("pilot_output_root_required")
    return pdf.resolve(), root.resolve()


def _safe_metadata(
    *,
    result: ExamProjectRunResultV1,
    profile: ExamSubjectProfileV1,
    pdf_path: Path,
    output_root: Path,
) -> dict[str, object]:
    """Build metadata from hashes, counts, and paths only."""

    evidence_unit_count = sum(
        len(page.evidence_units) for page in result.manifest.pages
    )
    images: list[dict[str, object]] = []
    for image in result.images:
        output_path: str | None = None
        if image.output_path is not None:
            try:
                output_path = image.output_path.relative_to(output_root).as_posix()
            except ValueError:
                output_path = image.output_path.name
        images.append(
            {
                "card_id": image.card_id,
                "raw_id": image.raw_id,
                "request_id": image.request_id,
                "request_sha256": image.request_sha256,
                "provenance_sha256": image.provenance_sha256,
                "source_pages": list(image.source_pages),
                "output_path": output_path,
                "image_sha256": image.image_sha256,
            }
        )

    return {
        "schema_version": EXAM_PROJECT_PILOT_METADATA_VERSION,
        "status": "succeeded",
        "input": {
            "name": pdf_path.name,
            "sha256": result.document_sha256,
            "byte_size": result.manifest.source.byte_size,
            "page_count": result.manifest.source.page_count,
        },
        "profile": {
            "profile_id": profile.profile_id,
            "profile_revision": profile.profile_revision,
            "profile_sha256": result.profile_sha256,
        },
        "stages": {
            "evidence_units": evidence_unit_count,
            "fact_count": len(result.fact_pipeline.facts),
            "card_count": len(result.cards),
            "image_request_count": len(result.requests),
            "image_count": len(result.images),
        },
        "images": images,
        "dry_run": result.dry_run,
    }


def _write_metadata(*, output_root: Path, metadata: dict[str, object]) -> Path:
    path = output_root / "run_metadata.json"
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        raise ExamProjectPilotConfigurationError("pilot_execution_failed") from None
    return path


def _write_failure_metadata(
    *,
    output_root: Path,
    pdf_path: Path,
    code: str,
    diagnostic: ExamProjectRunDiagnostic | None = None,
) -> None:
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        metadata: dict[str, object] = {
            "schema_version": EXAM_PROJECT_PILOT_METADATA_VERSION,
            "status": "failed",
            "input": {"name": pdf_path.name},
            "failure_code": code,
        }
        if code == "knowledge_extraction_failed" and diagnostic is not None:
            safe_diagnostic = dict(diagnostic)
            parse = safe_diagnostic.pop("parse", None)
            metadata["diagnostic"] = safe_diagnostic
            if isinstance(parse, Mapping):
                parse_result = parse.get("parse_result")
                response_length = parse.get("response_length")
                finish_reason = parse.get("finish_reason")
                if (
                    isinstance(parse_result, str)
                    and parse_result in {"success", "json_decode_error"}
                    and isinstance(response_length, int)
                    and not isinstance(response_length, bool)
                    and response_length >= 0
                    and (
                        finish_reason is None
                        or finish_reason
                        in {"stop", "length", "tool_calls", "content_filter", "function_call"}
                    )
                ):
                    safe_diagnostic["parse"] = {
                        "parse_result": parse_result,
                        "response_length": response_length,
                        "finish_reason": finish_reason,
                    }
        (output_root / "run_metadata.json").write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def run_exam_project_ai_pilot_v1(
    *,
    pdf_path: Path,
    output_root: Path,
    profile: ExamSubjectProfileV1,
    dependencies: ExamProjectDependenciesV1,
    step2_decoder_config: Step2DecoderConfig | None = None,
    source_relative_path: str | None = None,
    sample_id: str | None = None,
) -> ExamProjectRunResultV1:
    """Run one explicit PDF using injected AI/image dependencies."""

    pdf, root = _validate_paths(pdf_path=pdf_path, output_root=output_root)
    if not isinstance(profile, ExamSubjectProfileV1) or not isinstance(
        dependencies, ExamProjectDependenciesV1
    ):
        raise ExamProjectPilotConfigurationError("pilot_runtime_required")

    try:
        result = run_exam_project_v1(
            pdf_path=pdf,
            profile=profile,
            output_root=root,
            cache_root=root / "evidence-cache",
            dependencies=dependencies,
            dry_run=False,
            source_relative_path=source_relative_path,
            sample_id=sample_id,
            step2_decoder_config=step2_decoder_config,
        )
    except ExamProjectRunError as error:
        _write_failure_metadata(
            output_root=root,
            pdf_path=pdf,
            code=error.code,
            diagnostic=error.diagnostic,
        )
        raise
    except Exception:
        _write_failure_metadata(
            output_root=root, pdf_path=pdf, code="pilot_execution_failed"
        )
        raise ExamProjectPilotConfigurationError("pilot_execution_failed") from None

    _write_metadata(
        output_root=root,
        metadata=_safe_metadata(
            result=result, profile=profile, pdf_path=pdf, output_root=root
        ),
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one PDF through the isolated exam-card AI pilot."
    )
    parser.add_argument(
        "--pdf", required=True, type=Path, help="explicit PDF input path"
    )
    parser.add_argument(
        "--output-root", required=True, type=Path, help="isolated output directory"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: ExamProjectPilotRuntimeFactoryV1 | None = None,
) -> int:
    """Parse safe operator paths and execute with an explicitly injected factory."""

    args = _parser().parse_args(argv)
    if runtime_factory is None:
        print(
            ExamProjectPilotConfigurationError("pilot_runtime_required"),
            file=sys.stderr,
        )
        return 2
    try:
        pdf, root = _validate_paths(pdf_path=args.pdf, output_root=args.output_root)
    except ExamProjectPilotConfigurationError as error:
        print(error, file=sys.stderr)
        return 2
    try:
        runtime = runtime_factory(pdf_path=pdf, output_root=root)
        try:
            run_exam_project_ai_pilot_v1(
                pdf_path=pdf,
                output_root=root,
                profile=runtime.profile,
                dependencies=runtime.dependencies,
                step2_decoder_config=runtime.step2_decoder_config,
                source_relative_path=runtime.source_relative_path,
                sample_id=runtime.sample_id,
            )
        finally:
            runtime.close()
    except ExamProjectRunError as error:
        print(f"exam project pilot failed: {error.code}", file=sys.stderr)
        return 1
    except ExamProjectPilotConfigurationError as error:
        print(error, file=sys.stderr)
        return 2
    print("exam project pilot completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(runtime_factory=build_default_exam_project_pilot_runtime_v1))
