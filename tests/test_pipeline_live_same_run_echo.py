"""Same-run Step3 V3 to Echo Review V2 checkpoint regressions."""
from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
from base64 import b64encode
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
from PIL import Image

from examcard.common.hashing import canonical_hash
from examcard.compilers.step4_echo_bridge_v2 import load_step4_bundle_source_v2
from examcard.pipeline.live_config import LiveRunConfig, build_run_resume_contract
from examcard.pipeline.live_checkpoint import (
    emit_echo_step3_review_checkpoint_v2,
    ingest_echo_step3_review_checkpoint_v2,
)
from examcard.pipeline.live_orchestrate import orchestrate
from migration import run_exam_pipeline
from tests.fixtures.step3_v3 import valid_v3_response_content
from tests.fixtures.step3_v3 import approve_sheet, compile_v3, seed_v3_echo_run
from tests.test_gates_review_step2b_admission_v2 import (
    _projection,
    _seed_orchestration_workspace,
    _source_case,
    _write_authority_inputs,
)


def _checkpoint_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    fixture_path, _fixture, _file_sha, compilation = compile_v3(tmp_path / "source")
    run_root = seed_v3_echo_run(tmp_path, compilation)
    run_fixture_path = run_root / "checkpoints" / "admission" / "reviewed-typed-evidence-fixture-v2.json"
    run_fixture_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture_path, run_fixture_path)
    ticket = emit_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        typed_fixture_path=run_fixture_path,
        expected_step3_count=len(compilation.contracts),
        source_echo_run_id="run:echo-v2-test",
    )
    return run_fixture_path, run_root, ticket


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run_root: next((run_root / "step3" / "cards").glob("*/attempt-1.response.raw.json")).write_bytes(
            b"tampered"
        ),
        lambda run_root: next((run_root / "step3" / "cards").glob("*/attempt-1.gate.json")).write_text(
            "{}", encoding="utf-8"
        ),
    ],
)
def test_same_run_echo_revalidates_step3_records_before_human_release(
    tmp_path: Path, mutate: Callable[[Path], object]
) -> None:
    _fixture_path, run_root, ticket = _checkpoint_fixture(tmp_path)
    mutate(run_root)

    result = ingest_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        ticket=ticket,
        expected_step3_count=1,
        source_echo_run_id="run:echo-v2-test",
    )

    assert result["status"] == "refused"
    assert result["stop_reason"] == "gate_refused_echo_review"
    assert "same-run Echo Step3" in result["detail"]


@pytest.mark.parametrize("artifact_name", ["typed", "sheet"])
def test_same_run_echo_rejects_emitted_source_or_sheet_tamper(tmp_path: Path, artifact_name: str) -> None:
    _fixture_path, run_root, ticket = _checkpoint_fixture(tmp_path)
    if artifact_name == "typed":
        Path(str(ticket["source_typed_fixture_path"])).write_text("{}", encoding="utf-8")
    else:
        sheet_path = Path(str(ticket["review_sheet_path"]))
        sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
        sheet["cards"][0]["source_candidate_id"] = "tampered-candidate"
        sheet_path.write_text(json.dumps(sheet), encoding="utf-8")

    result = ingest_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        ticket=ticket,
        expected_step3_count=1,
        source_echo_run_id="run:echo-v2-test",
    )

    assert result["status"] == "refused"


def test_same_run_echo_rejects_byte_only_release_and_handoff_tamper(tmp_path: Path) -> None:
    fixture_path, run_root, ticket = _checkpoint_fixture(tmp_path)
    approve_sheet(Path(str(ticket["review_sheet_path"])))

    ingested = ingest_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        ticket=ticket,
        expected_step3_count=1,
        source_echo_run_id="run:echo-v2-test",
    )

    assert ingested["status"] == "ingested"
    for key in ("ingest_receipt_path", "release_path", "handoff_path"):
        assert Path(str(ingested[key])).is_relative_to(run_root)
    persisted_ticket = {"status": "ingested", **ingested}
    revalidated = ingest_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        ticket=persisted_ticket,
        expected_step3_count=1,
        source_echo_run_id="run:echo-v2-test",
    )
    assert revalidated["status"] == "ingested"
    for artifact_key, identity_key in (
        ("release_path", "release_file_sha256"),
        ("handoff_path", "handoff_file_sha256"),
    ):
        artifact_path = Path(str(ingested[artifact_key]))
        original = artifact_path.read_bytes()
        artifact_path.write_bytes(original + b"\n")
        refused = ingest_echo_step3_review_checkpoint_v2(
            run_root=run_root,
            ticket=persisted_ticket,
            expected_step3_count=1,
            source_echo_run_id="run:echo-v2-test",
        )
        assert refused["status"] == "refused"
        assert f"frozen identity drifted: {identity_key}" in refused["detail"]
        artifact_path.write_bytes(original)
    assert json.loads(fixture_path.read_text(encoding="utf-8"))["schema_version"] == "phase2r810_reviewed_typed_evidence_fixture_v2"


@pytest.mark.parametrize(
    "identity_key",
    ["release_sha256", "handoff_sha256", "ingest_receipt_sha256", "released_count"],
)
def test_same_run_echo_rejects_frozen_ticket_identity_drift(tmp_path: Path, identity_key: str) -> None:
    _fixture_path, run_root, ticket = _checkpoint_fixture(tmp_path)
    approve_sheet(Path(str(ticket["review_sheet_path"])))
    ingested = ingest_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        ticket=ticket,
        expected_step3_count=1,
        source_echo_run_id="run:echo-v2-test",
    )
    assert ingested["status"] == "ingested"
    persisted_ticket = {"status": "ingested", **ingested}
    tampered_ticket = dict(persisted_ticket)
    tampered_ticket[identity_key] = (
        int(tampered_ticket[identity_key]) + 1
        if identity_key == "released_count"
        else "0" * 64
    )

    refused = ingest_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        ticket=tampered_ticket,
        expected_step3_count=1,
        source_echo_run_id="run:echo-v2-test",
    )

    assert refused["status"] == "refused"
    assert f"frozen identity drifted: {identity_key}" in refused["detail"]


def test_same_run_echo_rejects_post_ingest_review_sheet_byte_drift(tmp_path: Path) -> None:
    _fixture_path, run_root, ticket = _checkpoint_fixture(tmp_path)
    approve_sheet(Path(str(ticket["review_sheet_path"])))
    ingested = ingest_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        ticket=ticket,
        expected_step3_count=1,
        source_echo_run_id="run:echo-v2-test",
    )
    assert ingested["status"] == "ingested"
    persisted_ticket = {"status": "ingested", **ingested}
    sheet_path = Path(str(ingested["review_sheet_path"]))
    original = sheet_path.read_bytes()
    sheet_path.write_bytes(original + b"\n")

    refused = ingest_echo_step3_review_checkpoint_v2(
        run_root=run_root,
        ticket=persisted_ticket,
        expected_step3_count=1,
        source_echo_run_id="run:echo-v2-test",
    )

    assert refused["status"] == "refused"
    assert "review sheet file drifted" in refused["detail"]


def _image_response(prompt: str) -> bytes:
    image = Image.new("RGB", (1024, 1536), color=(30, 90, 170))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image.close()
    return json.dumps(
        {
            "created": 1_700_000_000,
            "data": [{"b64_json": b64encode(buffer.getvalue()).decode("ascii"), "revised_prompt": prompt}],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 3,
                "input_tokens_details": {"text_tokens": 1, "image_tokens": 0, "cached_tokens": 0},
                "output_tokens_details": {"text_tokens": 0, "image_tokens": 2, "reasoning_tokens": 0},
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.parametrize("review_mode", ["undecided", "rejected", "approved"])
def test_same_run_orchestrator_pauses_then_reaches_step4_from_internal_v2_chain(
    request: pytest.FixtureRequest, review_mode: str
) -> None:
    tmp_path = Path(tempfile.mkdtemp(prefix="ec2c-", dir=tempfile.gettempdir()))
    request.addfinalizer(lambda: shutil.rmtree(tmp_path, ignore_errors=True))
    _seed_orchestration_workspace(tmp_path)
    case = _source_case()
    projection = _projection(case)
    external_fixture = tmp_path / "external-typed-fixture.json"
    external_fixture.write_text("must not be read", encoding="utf-8")
    external_handoff = tmp_path / "external-handoff.json"
    external_handoff.write_text("must not be read", encoding="utf-8")
    run_root: Path | None = None
    image_calls: list[dict[str, object]] = []

    def context_builder(*, sample_ids: tuple[str, ...], reason: str) -> tuple[object, ...]:
        assert sample_ids == ("threshold-sample",)
        return (
            type(
                "Context",
                (),
                {"sample_id": "threshold-sample", "step2a_request": case["request"], "step2b_request": case["request"]},
            )(),
        )

    def stage_executor(*, storage_root: Path, request: object, stage: str, **_kwargs: object) -> dict[str, object]:
        response = case["response"].model_dump(mode="json") if stage == "step2b" else {"proposals": []}
        return {
            "state": "success",
            "request_id": request.identity.request_id,  # type: ignore[attr-defined]
            "stage": stage,
            "response": response,
            "http_status": 200,
            "latency_ms": 1.0,
        }

    def chat_strict(**_kwargs: object) -> dict[str, object]:
        assert run_root is not None
        fixture_path = run_root / "checkpoints" / "admission" / "reviewed-typed-evidence-fixture-v2.json"
        fixture_bytes = fixture_path.read_bytes()
        from examcard.compilers.step3_card_v3 import compile_reviewed_fixture_v3
        from examcard.knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2

        compilation = compile_reviewed_fixture_v3(
            ReviewedTypedEvidenceFixtureV2.model_validate_json(fixture_bytes),
            source_typed_fixture_file_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
        )
        return {"content": valid_v3_response_content(compilation), "http_status": 200, "latency_ms": 1.0}

    def prompt_builder(card: object) -> str:
        return f"same-run prompt for {card.source_card_id}"  # type: ignore[attr-defined]

    def image_caller(payload: dict[str, object]) -> tuple[int, bytes]:
        image_calls.append(payload)
        return 200, _image_response(str(payload["prompt"]))

    def wrong_loader(_release: Path, _handoff: Path) -> tuple[object, object]:
        raise AssertionError("pilot loader must not be used by the same-run lane")

    config = LiveRunConfig(
        exam_id="gonglu_golden5_rehearsal",
        stages=("step2a", "step2b", "step3", "step4"),
        mode="live",
        run_label="echo-v2",
        budget_usd=10.0,
        max_attempts=1,
        step3_contract="v3",
        step3_typed_fixture=external_fixture,
        step4_handoff=external_handoff,
        skip_preflight=True,
        repo_root=tmp_path,
    )
    providers: dict[str, object] = {
        "pricing": {"step2a_call": 0.1, "step2b_call": 0.1, "step3_card_call": 0.2, "step4_image_call": 0.3},
        "sample_ids": ("threshold-sample",),
        "step3_card_count": 1,
        "step4_card_count": 99,
        "step4_release_fixture": external_fixture,
        "context_builder": context_builder,
        "stage_executor": stage_executor,
        "gate_evaluator": lambda _stage, _context, _record: {"valid": True},
        "admission_projection_builder": lambda **_kwargs: (projection,),
        "admission_page_artifacts": {case["binding"].page_ref_id: (case["image_bytes"], case["page_bytes"])},
        "admission_source_validator": lambda **_kwargs: None,
        "chat_strict": chat_strict,
        "prompt_builder": prompt_builder,
        "image_caller": image_caller,
        "image_model": "gpt-image-2",
        "step4_bundle_loader": wrong_loader,
        "step4_bundle_loader_v2": lambda release, handoff: load_step4_bundle_source_v2(
            release_fixture_path=release, handoff_path=handoff
        ),
    }

    first_report, first_exit = orchestrate(config, providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_admission"), "; ".join(
        f"{entry.stage}:{entry.status}:{entry.detail}" for entry in first_report.stages
    )
    run_root = tmp_path / "exams" / "gonglu_golden5_rehearsal" / "data" / "live_runs" / first_report.run_id
    _write_authority_inputs(run_root, case)

    second_report, second_exit = orchestrate(replace(config, resume_run_id=first_report.run_id), providers)
    assert (second_exit, second_report.stop_reason) == (2, "waiting_human_echo_review")
    assert not image_calls
    echo_sheet = run_root / "checkpoints" / "echo_review" / "echo_step3_review_sheet_v2.json"
    if review_mode == "undecided":
        return
    if review_mode == "rejected":
        sheet = json.loads(echo_sheet.read_text(encoding="utf-8"))
        for row in sheet["cards"]:
            row["disposition"] = "rejected"
        echo_sheet.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
        third_report, third_exit = orchestrate(replace(config, resume_run_id=first_report.run_id), providers)
        assert (third_exit, third_report.stop_reason) == (1, "gate_refused_echo_review")
        assert not image_calls
        return
    approve_sheet(echo_sheet)

    third_report, third_exit = orchestrate(replace(config, resume_run_id=first_report.run_id), providers)
    report_doc = json.loads((run_root / "live_run_report.json").read_text(encoding="utf-8"))
    assert (third_exit, third_report.stop_reason) == (2, "waiting_human_image_review"), report_doc["extras"].get("step4_cards")[0]["attempts"]
    assert len(image_calls) == 1
    assert (run_root / "checkpoints" / "echo_review" / "echo-batch-release-v2.json").is_file()
    assert (run_root / "checkpoints" / "echo_review" / "echo-batch-handoff-v2.json").is_file()

    providers.pop("step4_bundle_loader_v2")
    fourth_report, fourth_exit = orchestrate(replace(config, resume_run_id=first_report.run_id), providers)
    assert (fourth_exit, fourth_report.stop_reason) == (1, "gate_refused_echo_review")
    assert len(image_calls) == 1


def _cli_args(
    *, step3_mode: str, typed_fixture: Path, release_fixture: Path, handoff: Path
) -> SimpleNamespace:
    return SimpleNamespace(
        exam="gonglu_golden5_rehearsal",
        stages="step2a,step2b,step3,step4",
        allow_live=True,
        dry_run=False,
        budget_usd=10.0,
        run_label="cli-same-run",
        only_samples=None,
        channel="last",
        env_file=None,
        step3_contract="v3",
        step3_mode=step3_mode,
        step3_typed_fixture=typed_fixture,
        step3_release_fixture=release_fixture,
        step4_handoff=handoff,
        prompt_variant="v2",
        skip_preflight=True,
        resume=None,
        max_attempts=1,
        step4_source="echo-batch",
        echo_release=release_fixture,
        echo_handoff=handoff,
        diagram_review_sidecar=None,
        diagram_disposition_sidecar=None,
        seed_evidence_from=None,
    )


@pytest.mark.parametrize("step3_mode", ["echo", "release-bypass"])
def test_production_cli_separates_same_run_echo_from_external_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step3_mode: str
) -> None:
    typed_fixture = tmp_path / "external-typed-fixture.json"
    typed_fixture.write_text("must not be selected", encoding="utf-8")
    release_fixture = tmp_path / "external-release.json"
    release_fixture.write_text(
        json.dumps({"schema_version": "echo-batch-release/2.0", "cards": []}), encoding="utf-8"
    )
    handoff = tmp_path / "external-handoff.json"
    handoff.write_text("{}", encoding="utf-8")
    args = _cli_args(
        step3_mode=step3_mode,
        typed_fixture=typed_fixture,
        release_fixture=release_fixture,
        handoff=handoff,
    )
    context = SimpleNamespace(
        sample_id="sample-1",
        step2a_request=SimpleNamespace(sections=()),
        step2b_request=SimpleNamespace(grouping_obligations=(object(),)),
    )
    registry = SimpleNamespace(samples=(SimpleNamespace(sample_id="sample-1"),))
    captured: dict[str, object] = {}

    monkeypatch.setattr(run_exam_pipeline, "_parse_args", lambda: args)
    monkeypatch.setattr(
        run_exam_pipeline,
        "ExamPaths",
        SimpleNamespace(resolve=lambda _root, _exam_id: SimpleNamespace(registry_path=tmp_path / "registry.yaml")),
    )
    monkeypatch.setattr(run_exam_pipeline, "load_run_env", lambda _path: {
        "TEXT_MODEL": "text-model",
        "IMAGE_MODEL": "gpt-image-2",
        "OPENAI_USER_AGENT": "",
    })
    monkeypatch.setattr(run_exam_pipeline, "load_live_pricing", lambda _path: {})
    monkeypatch.setattr(run_exam_pipeline, "_resolve_channel", lambda _env, _channel: {
        "base_url": "http://offline",
        "api_key": "test-key",
    })
    monkeypatch.setattr(run_exam_pipeline, "_load_preflight", lambda _args, _env, _stages: {})
    from examcard.evidence import golden as golden_module
    from examcard.knowledge.contracts import phase2r14_v25_live

    monkeypatch.setattr(golden_module, "load_golden_registry", lambda _path: registry)
    monkeypatch.setattr(phase2r14_v25_live, "build_live_sample_contexts", lambda **_kwargs: (context,))

    def fake_orchestrate(config: LiveRunConfig, providers: dict[str, object]) -> tuple[object, int]:
        captured["config"] = config
        captured["providers"] = providers
        return (
            SimpleNamespace(
                run_id="stub-run",
                mode="live",
                stages=(),
                stop_reason="stub",
                network_calls=0,
                usd_estimate=0.0,
            ),
            0,
        )

    monkeypatch.setattr(run_exam_pipeline, "orchestrate", fake_orchestrate)
    assert run_exam_pipeline.main() == 0

    providers = captured["providers"]
    assert isinstance(providers, dict)
    if step3_mode == "echo":
        assert callable(providers.get("step4_bundle_loader_v2"))
        assert "step4_bundle_loader" not in providers
        assert "step4_release_fixture" not in providers
        assert "step4_handoff" not in providers
    else:
        assert "step4_bundle_loader_v2" not in providers
        assert callable(providers.get("step4_bundle_loader"))
        assert providers["step4_release_fixture"] == release_fixture
        assert providers["step4_handoff"] == handoff


def test_resume_contract_binds_release_bypass_inputs_but_not_same_run_echo_inputs(tmp_path: Path) -> None:
    typed_fixture = tmp_path / "typed-fixture.json"
    typed_fixture.write_text("external typed fixture", encoding="utf-8")
    release_fixture = tmp_path / "release.json"
    release_fixture.write_bytes(b"release-v1")
    handoff = tmp_path / "handoff.json"
    handoff.write_bytes(b"handoff-v1")

    def build(mode: str) -> dict[str, object]:
        config = LiveRunConfig(
            exam_id="gonglu_golden5_rehearsal",
            stages=("step2b", "step3", "step4"),
            mode="live",
            run_label=f"resume-{mode}",
            budget_usd=10.0,
            step3_contract="v3",
            step3_mode=mode,
            step3_typed_fixture=typed_fixture,
            step3_release_fixture=release_fixture,
            step4_handoff=handoff,
            repo_root=tmp_path,
        )
        return build_run_resume_contract(
            config,
            resolved_sample_ids=("sample-1",),
            step3_card_count=1,
            step4_card_count=1,
            stage_call_caps={"step3": 1, "step4": 1},
            image_base_url="http://offline",
            image_model="gpt-image-2",
            step4_handoff=handoff,
            admission_contract="v2",
            step3_typed_fixture_origin="admission_checkpoint",
        )

    release_bypass = build("release-bypass")
    release_inputs = release_bypass["file_inputs"]
    assert isinstance(release_inputs, dict)
    assert release_inputs["step3_typed_fixture"] == {"configured": False, "origin": "admission_checkpoint"}
    assert release_inputs["step3_release_fixture"] == {
        "configured": True,
        "sha256": hashlib.sha256(b"release-v1").hexdigest(),
    }
    assert release_inputs["step4_handoff"] == {
        "configured": True,
        "sha256": hashlib.sha256(b"handoff-v1").hexdigest(),
    }

    release_fixture.write_bytes(b"release-v2")
    assert canonical_hash(build("release-bypass")) != canonical_hash(release_bypass)
    release_fixture.write_bytes(b"release-v1")
    handoff.write_bytes(b"handoff-v2")
    assert canonical_hash(build("release-bypass")) != canonical_hash(release_bypass)
    handoff.write_bytes(b"handoff-v1")

    same_run_echo = build("echo")
    echo_inputs = same_run_echo["file_inputs"]
    assert isinstance(echo_inputs, dict)
    assert echo_inputs["step3_release_fixture"] == {"configured": False}
    assert echo_inputs["step4_handoff"] == {"configured": False}
    assert echo_inputs["step4_release_fixture"] == {"configured": False}
