"""step4 出图阶段（拆分层 1；设计 §4）。

providers 五字段投影 + 具名信封门禁；prompt 构建器与图像客户端均由 CLI 注入，
故整条出图逻辑可在离线替身上跑全量断言。
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ..common.hashing import SHA256_HEX, canonical_hash, file_hash, hash_model, sha256_bytes
from ..common.io import atomic_write_bytes, atomic_write_json, read_json
from ..materials.schema import StrictModel

from .live_budget import LiveRunBudget
from .live_paths import MissingUpstreamArtifact


STEP4_IMAGE_PROVENANCE_VERSION: Literal["step4-image-provenance/1.0"] = "step4-image-provenance/1.0"
STEP4_IMAGE_REVIEW_REFUSAL = "step4_image_integrity_refused"


class Step4ImageIntegrityError(ValueError):
    """A persisted Step4 image is not bound to its immutable provenance."""


class Step4ImageProvenance(StrictModel):
    """The persisted, self-hashed binding between one image and one Step4 request."""

    schema_version: Literal["step4-image-provenance/1.0"] = STEP4_IMAGE_PROVENANCE_VERSION
    card_index: int = Field(ge=1)
    source_card_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    render_presentation: str = Field(min_length=2)
    image_path: str = Field(min_length=1)
    image_sha256: str = Field(pattern=SHA256_HEX)
    http_payload_sha256: str = Field(pattern=SHA256_HEX)
    provenance_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def _validate_self_hash(self) -> "Step4ImageProvenance":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "provenance_sha256"}))
        if self.provenance_sha256 != expected:
            raise ValueError("step4 image provenance hash mismatch")
        return self


def step4_image_path(storage: Path, card_index: int, source_card_id: str) -> Path:
    """Derive the only production image path accepted for a card."""

    return Path(storage) / "images" / f"step4-card-{card_index:02d}-{source_card_id.rsplit(':', 1)[-1][:8]}.png"


def step4_image_provenance_path(storage: Path, card_index: int, source_card_id: str) -> Path:
    image_path = step4_image_path(storage, card_index, source_card_id)
    return Path(storage) / "image-provenance" / f"{image_path.stem}.json"


def build_step4_image_provenance(
    *,
    card_index: int,
    source_card_id: str,
    sample_id: str,
    render_presentation: str,
    image_path: str,
    image_sha256: str,
    http_payload_sha256: str,
) -> Step4ImageProvenance:
    return hash_model(
        Step4ImageProvenance,
        {
            "card_index": card_index,
            "source_card_id": source_card_id,
            "sample_id": sample_id,
            "render_presentation": render_presentation,
            "image_path": image_path,
            "image_sha256": image_sha256,
            "http_payload_sha256": http_payload_sha256,
        },
        "provenance_sha256",
    )


def _confined_expected_image_path(storage: Path, image_path_value: str, expected: Path) -> Path:
    """Accept only a relative POSIX path equal to the derived card path."""

    value = str(image_path_value)
    if not value or "\\" in value:
        raise Step4ImageIntegrityError("Step4 image path is not a relative POSIX path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise Step4ImageIntegrityError("Step4 image path escapes the image root")
    expected_relative = expected.relative_to(Path(storage)).as_posix()
    if value != expected_relative:
        raise Step4ImageIntegrityError("Step4 image path does not match the expected card path")
    candidate = Path(storage, *pure.parts).resolve()
    image_root = (Path(storage) / "images").resolve()
    try:
        candidate.relative_to(image_root)
    except ValueError as error:
        raise Step4ImageIntegrityError("Step4 image path is outside the image root") from error
    if candidate != expected.resolve():
        raise Step4ImageIntegrityError("Step4 image path does not match the expected card path")
    return candidate


def validate_step4_image_artifact(
    *,
    storage: Path,
    card_index: int,
    source_card_id: str,
    sample_id: str,
    render_presentation: str,
    http_payload_sha256: str,
    image_path_value: str | None = None,
    image_sha256: str | None = None,
    provenance_sha256: str | None = None,
) -> Step4ImageProvenance:
    """Validate an existing image/provenance pair before reuse or authorization."""

    storage = Path(storage)
    expected_image = step4_image_path(storage, card_index, source_card_id)
    image_path = _confined_expected_image_path(
        storage,
        expected_image.relative_to(storage).as_posix() if image_path_value is None else image_path_value,
        expected_image,
    )
    provenance_path = step4_image_provenance_path(storage, card_index, source_card_id)
    if image_path.is_symlink() or not image_path.is_file():
        raise Step4ImageIntegrityError("Step4 image is missing or is not a regular file")
    if provenance_path.is_symlink() or not provenance_path.is_file():
        raise Step4ImageIntegrityError("Step4 image provenance is missing or is not a regular file")
    try:
        provenance = Step4ImageProvenance.model_validate(read_json(provenance_path))
    except Exception as error:
        raise Step4ImageIntegrityError("Step4 image provenance is invalid") from error
    expected_path = expected_image.relative_to(storage).as_posix()
    if (
        provenance.card_index != card_index
        or provenance.source_card_id != source_card_id
        or provenance.sample_id != sample_id
        or provenance.render_presentation != render_presentation
        or provenance.image_path != expected_path
        or provenance.http_payload_sha256 != http_payload_sha256
    ):
        raise Step4ImageIntegrityError("Step4 image provenance identity does not match the current request")
    if image_sha256 is not None and provenance.image_sha256 != image_sha256:
        raise Step4ImageIntegrityError("Step4 image hash disagrees with the persisted provenance")
    if provenance_sha256 is not None and provenance.provenance_sha256 != provenance_sha256:
        raise Step4ImageIntegrityError("Step4 image provenance hash disagrees with the review record")
    actual_sha256 = file_hash(image_path)
    if actual_sha256 != provenance.image_sha256:
        raise Step4ImageIntegrityError("Step4 image bytes drifted from the persisted provenance")
    return provenance


def _load_step4_bundle(
    fixture_path: Path,
    handoff_file: Path,
    bundle_loader: Callable[[Path, Path], tuple[Any, Any]] | None,
) -> tuple[Any, Any]:
    if not fixture_path.is_file():
        raise MissingUpstreamArtifact(f"step4 needs the reviewed release fixture: {fixture_path}")
    if not handoff_file.is_file():
        raise MissingUpstreamArtifact(f"step4 needs the handoff manifest: {handoff_file}")
    if bundle_loader is not None:
        return bundle_loader(fixture_path, handoff_file)
    import yaml

    from ..compilers.step4_contract import derive_step4_bundle
    from ..knowledge.contracts.admission_fixtures import Step3UnifiedReviewedReleaseFixture, Step4PilotHandoffRefresh

    fixture_bytes = fixture_path.read_bytes()
    handoff_bytes = handoff_file.read_bytes()
    import hashlib as _hashlib

    fixture = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(fixture_bytes.decode("utf-8")))
    handoff = Step4PilotHandoffRefresh.model_validate_json(handoff_bytes.decode("utf-8"))
    bundle, model_input, _request, _artifacts, _batch = derive_step4_bundle(
        fixture,
        handoff,
        unified_fixture_sha256=_hashlib.sha256(fixture_bytes).hexdigest(),
        handoff_sha256=_hashlib.sha256(handoff_bytes).hexdigest(),
    )
    return bundle, model_input


def _prepare_step4_context(
    *,
    release_fixture_path: Path,
    handoff_path: Path,
    prompt_builder: Callable[[Any], str],
    image_model: str,
    image_width: int,
    image_height: int,
    bundle_loader: Callable[[Path, Path], tuple[Any, Any]] | None,
) -> tuple[Any, Any, Any, list[dict[str, Any]]]:
    from ..providers import build_image_profile, project_image_request

    bundle, model_input = _load_step4_bundle(Path(release_fixture_path), Path(handoff_path), bundle_loader)
    profile = build_image_profile(model=image_model, image_width=image_width, image_height=image_height)
    projections: list[dict[str, Any]] = []
    for card in bundle.cards:
        prompt = prompt_builder(card)
        payload = {
            "model": image_model,
            "n": 1,
            "prompt": prompt,
            "response_format": "b64_json",
            "size": f"{image_width}x{image_height}",
            "source_card_id": card.source_card_id,
            "card_index": card.card_index,
            "sample_id": card.sample_id,
        }
        projection, _sidecar = project_image_request(payload, profile)
        projections.append({"card": card, "prompt": prompt, "projection": projection})
    return bundle, model_input, profile, projections


def prepare_step4_request_bindings(
    *,
    release_fixture_path: Path,
    handoff_path: Path,
    prompt_builder: Callable[[Any], str],
    image_model: str,
    image_width: int = 1024,
    image_height: int = 1536,
    bundle_loader: Callable[[Path, Path], tuple[Any, Any]] | None = None,
) -> dict[int, dict[str, Any]]:
    """Rebuild current Step4 request/card bindings without network or writes."""

    _bundle, _model_input, _profile, projections = _prepare_step4_context(
        release_fixture_path=release_fixture_path,
        handoff_path=handoff_path,
        prompt_builder=prompt_builder,
        image_model=image_model,
        image_width=image_width,
        image_height=image_height,
        bundle_loader=bundle_loader,
    )
    return {
        int(entry["card"].card_index): {
            "card_index": entry["card"].card_index,
            "source_card_id": entry["card"].source_card_id,
            "sample_id": entry["card"].sample_id,
            "render_presentation": entry["card"].render_presentation,
            "http_payload_sha256": entry["projection"].http_payload_sha256,
        }
        for entry in projections
    }


# ---------------------------------------------------------------------------
# step4 出图（§4；providers 五字段投影 + 具名信封门禁；prompt 构建器注入）
# ---------------------------------------------------------------------------


def run_step4_stage(
    *,
    release_fixture_path: Path,
    handoff_path: Path,
    prompt_builder: Callable[[Any], str],
    image_caller: Callable[[Mapping[str, Any]], tuple[int, bytes]],
    models_probe: Callable[[], Mapping[str, Any]] | None,
    image_model: str,
    storage: Path,
    budget: LiveRunBudget,
    drift_classifier: Callable[[bytes, Any], bytes | None] | None = None,
    image_width: int = 1024,
    image_height: int = 1536,
    max_attempts: int = 2,
    price_per_call: float = 0.0,
    probe_price: float = 0.0,
    only_cards: set[int] | None = None,
    dry_run: bool = False,
    bundle_loader: Callable[[Path, Path], tuple[Any, Any]] | None = None,
) -> dict[str, Any]:
    """step4 出图编排（pilot 驱动结构吸收进库；PNG 存在即跳过 = 断点续跑）。

    ``bundle_loader``：装载接缝（P8 补链，echo_step4_bridge_design.md §3.4）——注入时以
    ``(release_fixture_path, handoff_path) -> (bundle_view, input_view)`` 取代缺省的
    r2242/r319 装载路径（echo 批走 `compilers.step4_echo_bridge.load_step4_bundle_source`）；
    缺省行为逐字节不变，下游出图循环两路共用。
    """

    import base64

    from ..providers import inspect_image_envelope
    from ..providers.base import build_opaque_failure, summarize_opaque_http_error

    fixture_path = Path(release_fixture_path)
    handoff_file = Path(handoff_path)
    bundle, model_input, profile, projections = _prepare_step4_context(
        release_fixture_path=fixture_path,
        handoff_path=handoff_file,
        prompt_builder=prompt_builder,
        image_model=image_model,
        image_width=image_width,
        image_height=image_height,
        bundle_loader=bundle_loader,
    )
    storage.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {
        "stage": "step4",
        "card_count": len(bundle.cards),
        "fact_count": model_input.fact_count,
        "probe": None,
        "cards": [],
    }
    selected_entries = [
        entry for entry in projections if only_cards is None or entry["card"].card_index in only_cards
    ]
    reusable: dict[int, Step4ImageProvenance] = {}
    for entry in selected_entries:
        card = entry["card"]
        image_path = step4_image_path(storage, card.card_index, card.source_card_id)
        provenance_path = step4_image_provenance_path(storage, card.card_index, card.source_card_id)
        if not image_path.exists() and not provenance_path.exists():
            continue
        try:
            provenance = validate_step4_image_artifact(
                storage=storage,
                card_index=card.card_index,
                source_card_id=card.source_card_id,
                sample_id=card.sample_id,
                render_presentation=card.render_presentation,
                http_payload_sha256=entry["projection"].http_payload_sha256,
            )
        except Step4ImageIntegrityError as error:
            refusal_record = {
                "card_index": card.card_index,
                "source_card_id": card.source_card_id,
                "sample_id": card.sample_id,
                "render_presentation": card.render_presentation,
                "outcome": "failed",
                "attempts": [],
                "stop_reason": STEP4_IMAGE_REVIEW_REFUSAL,
                "detail": str(error),
            }
            outcome["cards"].append(refusal_record)
            outcome["status"] = "failed"
            outcome["stop_reason"] = STEP4_IMAGE_REVIEW_REFUSAL
            outcome["succeeded_cards"] = 0
            return outcome
        reusable[card.card_index] = provenance

    atomic_write_json(
        storage / "requests-manifest.json",
        [
            {
                "card_index": entry["card"].card_index,
                "source_card_id": entry["card"].source_card_id,
                "sample_id": entry["card"].sample_id,
                "render_presentation": entry["card"].render_presentation,
                "prompt_utf8_bytes": len(entry["prompt"].encode("utf-8")),
                "http_payload_sha256": entry["projection"].http_payload_sha256,
            }
            for entry in projections
        ],
    )
    if dry_run:
        outcome["status"] = "completed"
        outcome["detail"] = "dry_run_assembled"
        return outcome

    if models_probe is not None and len(reusable) != len(selected_entries):
        budget.charge("step4", calls=1, usd_estimate=probe_price)
        probe_result = dict(models_probe())
        outcome["probe"] = probe_result
        if not probe_result.get("model_advertised"):
            outcome["status"] = "failed"
            outcome["stop_reason"] = "step4_probe_failed"
            return outcome

    succeeded = 0
    for entry in selected_entries:
        card = entry["card"]
        image_path = step4_image_path(storage, card.card_index, card.source_card_id)
        record: dict[str, Any] = {
            "card_index": card.card_index,
            "source_card_id": card.source_card_id,
            "sample_id": card.sample_id,
            "render_presentation": card.render_presentation,
            "outcome": "failed",
            "attempts": [],
        }
        outcome["cards"].append(record)
        if card.card_index in reusable:
            provenance = reusable[card.card_index]
            record["outcome"] = "success"
            record["image_path"] = provenance.image_path
            record["image_sha256"] = provenance.image_sha256
            record["http_payload_sha256"] = provenance.http_payload_sha256
            record["image_provenance_sha256"] = provenance.provenance_sha256
            record["reused_existing"] = True
            succeeded += 1
            continue
        payload = entry["projection"].http_request.model_dump(mode="json")
        for attempt in range(1, max_attempts + 1):
            budget.charge("step4", calls=1, usd_estimate=price_per_call)
            status_code, body = image_caller(payload)
            provider_failure = None
            if 100 <= status_code <= 599 and not (200 <= status_code < 300):
                provider_failure = build_opaque_failure(
                    status_code,
                    body,
                    detail=summarize_opaque_http_error(body),
                )
            gate = inspect_image_envelope(status_code, body, entry["prompt"], profile)
            drift = None
            if not gate.valid and drift_classifier is not None:
                stripped = drift_classifier(body, profile)
                if stripped is not None:
                    regate = inspect_image_envelope(status_code, stripped, entry["prompt"], profile)
                    if regate.valid:
                        drift = "data0_extra_width_height_stripped"
                        gate = regate
                        body = stripped
            attempt_entry = {
                "attempt": attempt,
                "http_status": status_code,
                "gate_valid": gate.valid,
                "gate_failures": list(gate.failure_codes),
                "envelope_drift": drift,
            }
            if provider_failure is not None:
                attempt_entry["provider_failure"] = provider_failure.model_dump(mode="json")
            record["attempts"].append(attempt_entry)
            if gate.valid:
                try:
                    document = json.loads(body.decode("utf-8"))
                    image_bytes = base64.b64decode(document["data"][0]["b64_json"], validate=True)
                    if gate.image_sha256 is None or sha256_bytes(image_bytes) != gate.image_sha256:
                        raise Step4ImageIntegrityError("generated image bytes disagree with the image gate")
                    atomic_write_bytes(image_path, image_bytes)
                    if image_path.is_symlink() or not image_path.is_file() or file_hash(image_path) != gate.image_sha256:
                        raise Step4ImageIntegrityError("persisted Step4 image bytes failed post-write verification")
                    image_relative = image_path.relative_to(storage).as_posix()
                    provenance = build_step4_image_provenance(
                        card_index=card.card_index,
                        source_card_id=card.source_card_id,
                        sample_id=card.sample_id,
                        render_presentation=card.render_presentation,
                        image_path=image_relative,
                        image_sha256=gate.image_sha256,
                        http_payload_sha256=entry["projection"].http_payload_sha256,
                    )
                    provenance_path = step4_image_provenance_path(storage, card.card_index, card.source_card_id)
                    atomic_write_json(provenance_path, provenance.model_dump(mode="json"))
                    record["outcome"] = "success"
                    record["image_path"] = image_relative
                    record["image_sha256"] = gate.image_sha256
                    record["http_payload_sha256"] = provenance.http_payload_sha256
                    record["image_provenance_sha256"] = provenance.provenance_sha256
                    succeeded += 1
                except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
                    record["stop_reason"] = STEP4_IMAGE_REVIEW_REFUSAL
                    record["detail"] = str(error)
                break
    attempted = len(selected_entries)
    outcome["succeeded_cards"] = succeeded
    outcome["status"] = "completed" if succeeded == attempted and attempted else "completed_with_failures"
    return outcome
