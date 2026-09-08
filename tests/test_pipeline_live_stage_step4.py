"""Offline vertical-slice safety net for the live Step4 stage glue."""
from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

import pytest
from PIL import Image

from examcard.common.hashing import sha256_bytes
from examcard.pipeline.live_budget import LiveRunBudget
from examcard.pipeline.live_paths import MissingUpstreamArtifact
from examcard.pipeline.live_stage_step4 import (
    Step4ImageProvenance,
    run_step4_stage,
    step4_image_provenance_path,
)
from examcard.providers import IMAGE_HTTP_PUBLIC_FIELDS, build_image_profile


Card = SimpleNamespace
Cards = tuple[Card, ...]
ImageCaller = Callable[[Mapping[str, Any]], tuple[int, bytes]]
Probe = Callable[[], Mapping[str, Any]]
DriftClassifier = Callable[[bytes, Any], bytes | None]


def _card(index: int, sample_id: str, presentation: str = "table") -> Card:
    return Card(
        card_index=index,
        source_card_id=f"source:card:{sample_id}",
        sample_id=sample_id,
        render_presentation=presentation,
    )


def _seed_upstream(root: Path) -> tuple[Path, Path]:
    release = root / "release-fixture.yaml"
    handoff = root / "handoff.json"
    release.write_text("synthetic: true\n", encoding="utf-8")
    handoff.write_text('{"synthetic": true}\n', encoding="utf-8")
    return release, handoff


def _bundle_loader(cards: Cards, fact_count: int) -> Callable[[Path, Path], tuple[Any, Any]]:
    def load(_release: Path, _handoff: Path) -> tuple[Any, Any]:
        return SimpleNamespace(cards=cards), SimpleNamespace(fact_count=fact_count)

    return load


def _budget(root: Path, *, call_cap: int = 8) -> LiveRunBudget:
    return LiveRunBudget(root / "budget.json", usd_cap=10.0, stage_call_caps={"step4": call_cap})


def _prompt(card: Any) -> str:
    return f"synthetic prompt for {card.sample_id}"


def _invoke(
    root: Path,
    *,
    cards: Cards,
    budget: LiveRunBudget,
    image_caller: ImageCaller,
    models_probe: Probe | None = None,
    drift_classifier: DriftClassifier | None = None,
    only_cards: set[int] | None = None,
    dry_run: bool = False,
    max_attempts: int = 2,
    price_per_call: float = 0.0,
    probe_price: float = 0.0,
    prompt_builder: Callable[[Any], str] = _prompt,
) -> dict[str, Any]:
    release, handoff = _seed_upstream(root)
    return run_step4_stage(
        release_fixture_path=release,
        handoff_path=handoff,
        prompt_builder=prompt_builder,
        image_caller=image_caller,
        models_probe=models_probe,
        image_model="gpt-image-2",
        storage=root / "step4",
        budget=budget,
        drift_classifier=drift_classifier,
        image_width=1024,
        image_height=1536,
        max_attempts=max_attempts,
        price_per_call=price_per_call,
        probe_price=probe_price,
        only_cards=only_cards,
        dry_run=dry_run,
        bundle_loader=_bundle_loader(cards, fact_count=3),
    )


def _png_bytes(color: tuple[int, int, int] = (20, 80, 140)) -> bytes:
    image = Image.new("RGB", (1024, 1536), color=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image.close()
    return buffer.getvalue()


def _image_envelope(png: bytes, prompt: str) -> bytes:
    document = {
        "created": 1_700_000_000,
        "data": [{"b64_json": base64.b64encode(png).decode("ascii"), "revised_prompt": prompt}],
        "usage": {
            "input_tokens": 1,
            "output_tokens": 2,
            "total_tokens": 3,
            "input_tokens_details": {"text_tokens": 1, "image_tokens": 0, "cached_tokens": 0},
            "output_tokens_details": {"text_tokens": 0, "image_tokens": 2, "reasoning_tokens": 0},
        },
    }
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize("missing", ["release", "handoff"])
def test_missing_upstream_artifact_fails_closed(repo_tmp_path: Path, missing: str) -> None:
    release, handoff = _seed_upstream(repo_tmp_path)
    if missing == "release":
        release.unlink()
    else:
        handoff.unlink()
    loader_calls: list[tuple[Path, Path]] = []
    caller_calls: list[Mapping[str, Any]] = []

    def loader(release_path: Path, handoff_path: Path) -> tuple[Any, Any]:
        loader_calls.append((release_path, handoff_path))
        return SimpleNamespace(cards=(_card(1, "one"),)), SimpleNamespace(fact_count=1)

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        caller_calls.append(payload)
        return 200, b"{}"

    with pytest.raises(MissingUpstreamArtifact):
        run_step4_stage(
            release_fixture_path=release,
            handoff_path=handoff,
            prompt_builder=_prompt,
            image_caller=caller,
            models_probe=None,
            image_model="gpt-image-2",
            storage=repo_tmp_path / "step4",
            budget=_budget(repo_tmp_path),
            bundle_loader=loader,
        )

    assert loader_calls == []
    assert caller_calls == []


def test_dry_run_assembles_manifest_without_probe_or_image_calls(repo_tmp_path: Path) -> None:
    cards = (_card(1, "one", "table"), _card(2, "two", "diagram"))

    def no_probe() -> Mapping[str, Any]:
        pytest.fail("dry-run must not probe models")

    def no_caller(_payload: Mapping[str, Any]) -> tuple[int, bytes]:
        pytest.fail("dry-run must not call the image provider")

    budget = _budget(repo_tmp_path)
    result = _invoke(
        repo_tmp_path,
        cards=cards,
        budget=budget,
        image_caller=no_caller,
        models_probe=no_probe,
        dry_run=True,
    )

    assert result == {
        "stage": "step4",
        "card_count": 2,
        "fact_count": 3,
        "probe": None,
        "cards": [],
        "status": "completed",
        "detail": "dry_run_assembled",
    }
    manifest = json.loads((repo_tmp_path / "step4" / "requests-manifest.json").read_text(encoding="utf-8"))
    assert [row["card_index"] for row in manifest] == [1, 2]
    assert [row["source_card_id"] for row in manifest] == ["source:card:one", "source:card:two"]
    assert [row["sample_id"] for row in manifest] == ["one", "two"]
    assert [row["render_presentation"] for row in manifest] == ["table", "diagram"]
    assert [row["prompt_utf8_bytes"] for row in manifest] == [len(_prompt(card).encode("utf-8")) for card in cards]
    assert all(len(row["http_payload_sha256"]) == 64 for row in manifest)
    assert budget.snapshot()["total_calls"] == 0
    assert budget.snapshot()["spent_usd_estimate"] == 0.0
    assert not list((repo_tmp_path / "step4" / "images").glob("*.png"))


def test_stage_passes_only_public_image_fields_to_caller(repo_tmp_path: Path) -> None:
    calls: list[Mapping[str, Any]] = []
    png = _png_bytes()

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        calls.append(dict(payload))
        return 200, _image_envelope(png, str(payload["prompt"]))

    _invoke(repo_tmp_path, cards=(_card(1, "one"),), budget=_budget(repo_tmp_path), image_caller=caller)

    assert len(calls) == 1
    assert set(calls[0]) == set(IMAGE_HTTP_PUBLIC_FIELDS)
    assert "card_index" not in calls[0]
    assert "source_card_id" not in calls[0]
    assert "sample_id" not in calls[0]


def test_successful_image_vertical_slice_persists_exact_png(repo_tmp_path: Path) -> None:
    png = _png_bytes((80, 20, 160))
    calls: list[Mapping[str, Any]] = []

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        calls.append(payload)
        return 200, _image_envelope(png, str(payload["prompt"]))

    budget = _budget(repo_tmp_path)
    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=budget,
        image_caller=caller,
        price_per_call=0.25,
    )

    image_path = repo_tmp_path / "step4" / "images" / "step4-card-01-one.png"
    assert result["status"] == "completed"
    assert result["succeeded_cards"] == 1
    assert len(result["cards"]) == 1
    assert result["cards"][0]["attempts"] == [
        {
            "attempt": 1,
            "http_status": 200,
            "gate_valid": True,
            "gate_failures": [],
            "envelope_drift": None,
        }
    ]
    assert image_path.read_bytes() == png
    assert result["cards"][0]["image_sha256"] == sha256_bytes(png)
    assert len(calls) == 1
    assert budget.snapshot()["per_stage"]["step4"]["calls"] == 1
    assert budget.snapshot()["spent_usd_estimate"] == 0.25
    assert (repo_tmp_path / "step4" / "requests-manifest.json").is_file()
    provenance_path = step4_image_provenance_path(repo_tmp_path / "step4", 1, "source:card:one")
    provenance = Step4ImageProvenance.model_validate(json.loads(provenance_path.read_text(encoding="utf-8")))
    assert provenance.image_sha256 == sha256_bytes(png)
    assert provenance.http_payload_sha256 == json.loads(
        (repo_tmp_path / "step4" / "requests-manifest.json").read_text(encoding="utf-8")
    )[0]["http_payload_sha256"]
    assert result["cards"][0]["image_provenance_sha256"] == provenance.provenance_sha256


def test_probe_refusal_prevents_image_generation(repo_tmp_path: Path) -> None:
    image_calls: list[Mapping[str, Any]] = []

    def probe() -> Mapping[str, Any]:
        return {"model_advertised": False, "reason": "not available"}

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        image_calls.append(payload)
        return 200, b"{}"

    budget = _budget(repo_tmp_path)
    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=budget,
        image_caller=caller,
        models_probe=probe,
        probe_price=0.1,
    )

    assert result["status"] == "failed"
    assert result["stop_reason"] == "step4_probe_failed"
    assert result["probe"] == {"model_advertised": False, "reason": "not available"}
    assert image_calls == []
    assert budget.snapshot()["per_stage"]["step4"]["calls"] == 1
    assert budget.snapshot()["spent_usd_estimate"] == 0.1
    assert not list((repo_tmp_path / "step4" / "images").glob("*.png"))


def test_invalid_envelope_retries_to_cap_and_fails_closed(repo_tmp_path: Path) -> None:
    calls: list[Mapping[str, Any]] = []

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        calls.append(payload)
        return 502, b'{"error":"opaque failure"}'

    budget = _budget(repo_tmp_path, call_cap=2)
    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=budget,
        image_caller=caller,
        max_attempts=2,
        price_per_call=0.2,
    )

    record = result["cards"][0]
    assert len(calls) == 2
    assert budget.snapshot()["per_stage"]["step4"]["calls"] == 2
    assert len(record["attempts"]) == 2
    assert all(not attempt["gate_valid"] and attempt["gate_failures"] for attempt in record["attempts"])
    assert record["outcome"] == "failed"
    assert result["status"] == "completed_with_failures"
    assert result["succeeded_cards"] == 0
    assert not list((repo_tmp_path / "step4" / "images").glob("*.png"))


def test_non_2xx_image_attempt_persists_opaque_provider_failure(repo_tmp_path: Path) -> None:
    body = json.dumps(
        {
            "error": {
                "message": "Invalid size. Authorization: Bearer sk-supersecret123456 Cookie: session=abcdef",
                "type": "invalid_request_error",
                "code": "invalid_size",
                "param": "size",
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")

    def caller(_payload: Mapping[str, Any]) -> tuple[int, bytes]:
        return 400, body

    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path, call_cap=1),
        image_caller=caller,
        max_attempts=1,
    )

    record = result["cards"][0]
    attempt = record["attempts"][0]
    assert result["status"] == "completed_with_failures"
    assert result["succeeded_cards"] == 0
    assert record["outcome"] == "failed"
    assert len(record["attempts"]) == 1
    assert attempt["http_status"] == 400
    assert attempt["gate_valid"] is False
    provider_failure = attempt["provider_failure"]
    assert provider_failure["schema_version"] == "examcard_provider_opaque_http_failure_v1"
    assert provider_failure["http_status"] == 400
    assert provider_failure["response_sha256"] == sha256_bytes(body)
    assert provider_failure["response_bytes"] == len(body)
    assert provider_failure["sanitized_detail"] == (
        "body=json; envelope=error; class=unsupported_size; "
        "type=invalid_request_error; code=invalid_size; param=size"
    )
    assert provider_failure["body_persisted"] is False
    assert provider_failure["body_reconstruction_allowed"] is False
    assert len(provider_failure["failure_sha256"]) == 64
    serialized = json.dumps(result, ensure_ascii=False)
    assert body.decode("utf-8") not in serialized
    for secret in ("Authorization", "Bearer", "sk-supersecret123456", "Cookie", "session=abcdef"):
        assert secret not in serialized
    assert not list((repo_tmp_path / "step4" / "images").glob("*.png"))
    assert not list((repo_tmp_path / "step4" / "image-provenance").glob("*.json"))


def test_http_200_invalid_envelope_does_not_create_opaque_provider_failure(repo_tmp_path: Path) -> None:
    def caller(_payload: Mapping[str, Any]) -> tuple[int, bytes]:
        return 200, b"{}"

    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path, call_cap=1),
        image_caller=caller,
        max_attempts=1,
    )

    attempt = result["cards"][0]["attempts"][0]
    assert result["status"] == "completed_with_failures"
    assert result["succeeded_cards"] == 0
    assert attempt["http_status"] == 200
    assert attempt["gate_valid"] is False
    assert "provider_failure" not in attempt
    assert not list((repo_tmp_path / "step4" / "images").glob("*.png"))
    assert not list((repo_tmp_path / "step4" / "image-provenance").glob("*.json"))


def test_benign_drift_recovery_requires_strict_regate(repo_tmp_path: Path) -> None:
    png = _png_bytes((160, 60, 20))
    corrected = _image_envelope(png, _prompt(_card(1, "one")))
    calls: list[Mapping[str, Any]] = []
    classifier_inputs: list[bytes] = []

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        calls.append(payload)
        return 200, b"malformed-before-drift"

    def classify(body: bytes, _profile: Any) -> bytes | None:
        classifier_inputs.append(body)
        return corrected

    budget = _budget(repo_tmp_path)
    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=budget,
        image_caller=caller,
        drift_classifier=classify,
        price_per_call=0.3,
    )

    record = result["cards"][0]
    assert len(calls) == 1
    assert classifier_inputs == [b"malformed-before-drift"]
    assert budget.snapshot()["per_stage"]["step4"]["calls"] == 1
    assert record["attempts"][0]["gate_valid"] is True
    assert record["attempts"][0]["envelope_drift"] == "data0_extra_width_height_stripped"
    assert record["outcome"] == "success"
    assert (repo_tmp_path / "step4" / "images" / "step4-card-01-one.png").read_bytes() == png
    assert result["status"] == "completed"


def test_only_cards_prevents_unselected_generation(repo_tmp_path: Path) -> None:
    cards = (_card(1, "one"), _card(2, "two"))
    calls: list[Mapping[str, Any]] = []
    png = _png_bytes()

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        calls.append(payload)
        return 200, _image_envelope(png, str(payload["prompt"]))

    budget = _budget(repo_tmp_path)
    result = _invoke(
        repo_tmp_path,
        cards=cards,
        budget=budget,
        image_caller=caller,
        only_cards={2},
    )

    assert [record["card_index"] for record in result["cards"]] == [2]
    assert len(calls) == 1
    assert budget.snapshot()["per_stage"]["step4"]["calls"] == 1
    assert not (repo_tmp_path / "step4" / "images" / "step4-card-01-one.png").exists()
    assert (repo_tmp_path / "step4" / "images" / "step4-card-02-two.png").is_file()
    manifest = json.loads((repo_tmp_path / "step4" / "requests-manifest.json").read_text(encoding="utf-8"))
    assert [row["card_index"] for row in manifest] == [1, 2]


def test_existing_image_resume_skips_caller_and_budget(repo_tmp_path: Path) -> None:
    png = _png_bytes((40, 120, 200))

    def first_caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        return 200, _image_envelope(png, str(payload["prompt"]))

    first_budget = _budget(repo_tmp_path)
    first = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=first_budget,
        image_caller=first_caller,
        only_cards={1},
    )
    assert first["cards"][0]["outcome"] == "success"
    storage = repo_tmp_path / "step4"
    image_path = storage / "images" / "step4-card-01-one.png"
    caller_calls: list[Mapping[str, Any]] = []

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        caller_calls.append(payload)
        return 200, b"should not be called"

    budget = _budget(repo_tmp_path)
    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=budget,
        image_caller=caller,
        only_cards={1},
    )

    record = result["cards"][0]
    assert result["status"] == "completed"
    assert record["outcome"] == "success"
    assert record["reused_existing"] is True
    assert caller_calls == []
    assert budget.snapshot()["total_calls"] == 1
    assert image_path.read_bytes() == png
    assert record["image_sha256"] == sha256_bytes(png)


def test_orphan_png_is_refused_without_provider_or_budget_charge(repo_tmp_path: Path) -> None:
    storage = repo_tmp_path / "step4"
    image_path = storage / "images" / "step4-card-01-one.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(_png_bytes())
    calls: list[Mapping[str, Any]] = []

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        calls.append(payload)
        return 200, b"{}"

    budget = _budget(repo_tmp_path)
    result = _invoke(repo_tmp_path, cards=(_card(1, "one"),), budget=budget, image_caller=caller, only_cards={1})

    assert result["status"] == "failed"
    assert result["stop_reason"] == "step4_image_integrity_refused"
    assert result["cards"][0]["outcome"] == "failed"
    assert calls == []
    assert budget.snapshot()["total_calls"] == 0
    assert image_path.is_file()


@pytest.mark.parametrize("tamper", ["image_sha256", "source_card_id", "http_payload_sha256"])
def test_tampered_provenance_is_refused(repo_tmp_path: Path, tamper: str) -> None:
    png = _png_bytes((100, 30, 70))

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        return 200, _image_envelope(png, str(payload["prompt"]))

    _invoke(repo_tmp_path, cards=(_card(1, "one"),), budget=_budget(repo_tmp_path), image_caller=caller, only_cards={1})
    provenance_path = step4_image_provenance_path(repo_tmp_path / "step4", 1, "source:card:one")
    payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    payload[tamper] = "0" * 64 if tamper != "source_card_id" else "source:card:other"
    provenance_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path),
        image_caller=lambda _payload: pytest.fail("tampered image must not call provider"),
        only_cards={1},
    )
    assert result["status"] == "failed"
    assert result["stop_reason"] == "step4_image_integrity_refused"


def test_image_bytes_drift_is_refused_without_regeneration(repo_tmp_path: Path) -> None:
    png = _png_bytes((5, 55, 155))

    def caller(payload: Mapping[str, Any]) -> tuple[int, bytes]:
        return 200, _image_envelope(png, str(payload["prompt"]))

    _invoke(repo_tmp_path, cards=(_card(1, "one"),), budget=_budget(repo_tmp_path), image_caller=caller, only_cards={1})
    image_path = repo_tmp_path / "step4" / "images" / "step4-card-01-one.png"
    image_path.write_bytes(_png_bytes((155, 55, 5)))
    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path),
        image_caller=lambda _payload: pytest.fail("drifted image must not call provider"),
        only_cards={1},
    )
    assert result["status"] == "failed"
    assert result["stop_reason"] == "step4_image_integrity_refused"


def test_orphan_provenance_is_refused(repo_tmp_path: Path) -> None:
    png = _png_bytes((15, 145, 95))

    _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path),
        image_caller=lambda payload: (200, _image_envelope(png, str(payload["prompt"]))),
        only_cards={1},
    )
    image_path = repo_tmp_path / "step4" / "images" / "step4-card-01-one.png"
    image_path.unlink()
    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path),
        image_caller=lambda _payload: pytest.fail("orphan provenance must not call provider"),
        only_cards={1},
    )
    assert result["status"] == "failed"
    assert result["stop_reason"] == "step4_image_integrity_refused"


def test_request_projection_drift_is_refused(repo_tmp_path: Path) -> None:
    png = _png_bytes((45, 100, 180))
    _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path),
        image_caller=lambda payload: (200, _image_envelope(png, str(payload["prompt"]))),
        only_cards={1},
    )
    manifest_path = repo_tmp_path / "step4" / "requests-manifest.json"
    manifest_before = manifest_path.read_bytes()
    result = _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path),
        image_caller=lambda _payload: pytest.fail("request drift must not call provider"),
        only_cards={1},
        prompt_builder=lambda card: f"changed prompt for {card.sample_id}",
    )
    assert result["status"] == "failed"
    assert result["stop_reason"] == "step4_image_integrity_refused"
    assert manifest_path.read_bytes() == manifest_before


def test_successful_generation_uses_atomic_image_writer(repo_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import examcard.pipeline.live_stage_step4 as live_step4

    calls: list[Path] = []
    original = live_step4.atomic_write_bytes

    def spy(path: Path, content: bytes) -> None:
        calls.append(path)
        original(path, content)

    monkeypatch.setattr(live_step4, "atomic_write_bytes", spy)
    png = _png_bytes()
    _invoke(
        repo_tmp_path,
        cards=(_card(1, "one"),),
        budget=_budget(repo_tmp_path),
        image_caller=lambda payload: (200, _image_envelope(png, str(payload["prompt"]))),
        only_cards={1},
    )
    assert repo_tmp_path / "step4" / "images" / "step4-card-01-one.png" in calls


def test_profile_used_by_fixture_matches_portrait_stage_contract() -> None:
    profile = build_image_profile(model="gpt-image-2", image_width=1024, image_height=1536)
    assert (profile.model, profile.image_width, profile.image_height) == ("gpt-image-2", 1024, 1536)
