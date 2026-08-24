"""step4 出图阶段（拆分层 1；设计 §4）。

providers 五字段投影 + 具名信封门禁；prompt 构建器与图像客户端均由 CLI 注入，
故整条出图逻辑可在离线替身上跑全量断言。
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..common.io import atomic_write_json

from .live_budget import LiveRunBudget
from .live_paths import MissingUpstreamArtifact


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

    from ..providers import build_image_profile, inspect_image_envelope, project_image_request

    fixture_path = Path(release_fixture_path)
    handoff_file = Path(handoff_path)
    if not fixture_path.is_file():
        raise MissingUpstreamArtifact(f"step4 needs the reviewed release fixture: {fixture_path}")
    if not handoff_file.is_file():
        raise MissingUpstreamArtifact(f"step4 needs the handoff manifest: {handoff_file}")
    if bundle_loader is not None:
        bundle, model_input = bundle_loader(fixture_path, handoff_file)
    else:
        import yaml

        from ..compilers.step4_contract import derive_step4_bundle
        from ..knowledge.contracts.admission_fixtures import (
            Step3UnifiedReviewedReleaseFixture,
            Step4PilotHandoffRefresh,
        )

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
    profile = build_image_profile(model=image_model, image_width=image_width, image_height=image_height)
    storage.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, Any] = {
        "stage": "step4",
        "card_count": len(bundle.cards),
        "fact_count": model_input.fact_count,
        "probe": None,
        "cards": [],
    }
    projections = []
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
    atomic_write_json(
        storage / "requests-manifest.json",
        [
            {
                "card_index": entry["card"].card_index,
                "source_card_id": entry["card"].source_card_id,
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

    if models_probe is not None:
        budget.charge("step4", calls=1, usd_estimate=probe_price)
        probe_result = dict(models_probe())
        outcome["probe"] = probe_result
        if not probe_result.get("model_advertised"):
            outcome["status"] = "failed"
            outcome["stop_reason"] = "step4_probe_failed"
            return outcome

    images_dir = storage / "images"
    succeeded = 0
    for entry in projections:
        card = entry["card"]
        if only_cards is not None and card.card_index not in only_cards:
            continue
        image_path = images_dir / f"step4-card-{card.card_index:02d}-{card.source_card_id.rsplit(':', 1)[-1][:8]}.png"
        record: dict[str, Any] = {
            "card_index": card.card_index,
            "source_card_id": card.source_card_id,
            "render_presentation": card.render_presentation,
            "outcome": "failed",
            "attempts": [],
        }
        outcome["cards"].append(record)
        if image_path.exists():
            record["outcome"] = "success"
            record["image_path"] = image_path.relative_to(storage).as_posix()
            record["reused_existing"] = True
            succeeded += 1
            continue
        payload = entry["projection"].http_request.model_dump(mode="json")
        for attempt in range(1, max_attempts + 1):
            budget.charge("step4", calls=1, usd_estimate=price_per_call)
            status_code, body = image_caller(payload)
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
            record["attempts"].append(attempt_entry)
            if gate.valid:
                document = json.loads(body.decode("utf-8"))
                image_bytes = base64.b64decode(document["data"][0]["b64_json"], validate=True)
                images_dir.mkdir(parents=True, exist_ok=True)
                image_path.write_bytes(image_bytes)
                record["outcome"] = "success"
                record["image_path"] = image_path.relative_to(storage).as_posix()
                record["image_sha256"] = gate.image_sha256
                succeeded += 1
                break
    attempted = len(outcome["cards"])
    outcome["succeeded_cards"] = succeeded
    outcome["status"] = "completed" if succeeded == attempted and attempted else "completed_with_failures"
    return outcome
