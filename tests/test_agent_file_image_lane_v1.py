"""Offline tests for the agent image lane (file-backed image caller + typesetter + CLI)."""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from examcard.common.hashing import sha256_bytes
from examcard.compilers.step4_knowledge_card_bridge_v1 import (
    build_knowledge_card_image_request_v1,
    project_knowledge_card_image_request_v1,
)
from examcard.knowledge.agent_file_transport import AgentFileStep2Transport
from examcard.knowledge.extraction_v1 import run_document_fact_pipeline
from examcard.knowledge.llm import Step2OpenAIAdapter
from examcard.pipeline.exam_project_runner_v1 import ExamProjectRunError, _validated_image_bytes
from examcard.providers.agent_file_image_caller_v1 import (
    AgentFileImageCallerV1,
    AgentImageRequestInvalid,
    AgentImageResponseMissing,
    agent_image_envelope_body,
    agent_image_prompt_sha256,
    agent_image_response_path,
)
from examcard.providers.envelope_profiles import build_image_profile, inspect_image_envelope

from .test_agent_file_step2_transport_v1 import MODEL, _manifest, _policy, _request, _write_envelope

ROOT = Path(__file__).resolve().parents[1]
PROFILE = build_image_profile()
SIZE = (PROFILE.image_width, PROFILE.image_height)
_FONTS = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _font() -> Path:
    for path in _FONTS:
        if path.is_file():
            return path
    pytest.skip("no TrueType font available for the typesetter")


def _png(size: tuple[int, int] = SIZE, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _write_png(root: Path, prompt: str, data: bytes) -> Path:
    path = agent_image_response_path(root, agent_image_prompt_sha256(prompt))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------- caller


def test_agent_png_passes_the_unchanged_image_envelope_gate_with_zero_usage(tmp_path: Path) -> None:
    prompt = "Render the supplied card exactly."
    data = _png()
    _write_png(tmp_path, prompt, data)
    caller = AgentFileImageCallerV1(response_root=tmp_path, profile=PROFILE)

    status, body = caller({"model": PROFILE.model, "prompt": prompt, "n": 1, "size": "1536x1024"})
    gate = inspect_image_envelope(status, body, prompt, PROFILE)

    assert status == 200
    assert gate.valid, gate.failure_codes
    assert gate.image_sha256 == sha256_bytes(data)
    document = json.loads(body)
    assert document["created"] == 0
    assert document["data"][0]["revised_prompt"] == prompt
    usage = document["usage"]
    assert usage["input_tokens"] == usage["output_tokens"] == usage["total_tokens"] == 0
    assert set(usage["input_tokens_details"].values()) == {0}
    assert set(usage["output_tokens_details"].values()) == {0}
    assert base64.b64decode(document["data"][0]["b64_json"]) == data  # bytes passed through untouched


def test_png_is_bound_to_the_exact_prompt(tmp_path: Path) -> None:
    _write_png(tmp_path, "card A", _png())
    caller = AgentFileImageCallerV1(response_root=tmp_path, profile=PROFILE)

    with pytest.raises(AgentImageResponseMissing):
        caller({"prompt": "card A (edited)"})


@pytest.mark.parametrize("payload", [{}, {"prompt": ""}, {"prompt": 3}])
def test_payload_without_prompt_is_refused(tmp_path: Path, payload: dict[str, Any]) -> None:
    caller = AgentFileImageCallerV1(response_root=tmp_path, profile=PROFILE)
    with pytest.raises(AgentImageRequestInvalid):
        caller(payload)


def test_wrong_size_png_is_not_repaired_and_fails_the_gate(tmp_path: Path) -> None:
    prompt = "p"
    _write_png(tmp_path, prompt, _png((1024, 1024)))
    status, body = AgentFileImageCallerV1(response_root=tmp_path, profile=PROFILE)({"prompt": prompt})

    gate = inspect_image_envelope(status, body, prompt, PROFILE)
    assert not gate.valid
    assert gate.png_dimensions_exact is False


def test_response_path_rejects_non_sha256_names(tmp_path: Path) -> None:
    with pytest.raises(AgentImageRequestInvalid):
        agent_image_response_path(tmp_path, "../escape")
    assert agent_image_response_path(tmp_path, "a" * 64).name == f"prompt-{'a' * 64}.png"


def test_envelope_refuses_a_changed_profile_shape() -> None:
    changed = PROFILE.model_copy(update={"top_level_keys": ("created", "data", "usage", "extra")})
    with pytest.raises(AgentImageRequestInvalid):
        agent_image_envelope_body(_png(), "p", changed)


# --------------------------------------------------------------------------- card -> image, runner validation


def _card_request(tmp_path: Path) -> tuple[Any, Any]:
    _write_envelope(tmp_path / "responses", _request())
    adapter = Step2OpenAIAdapter(
        storage_root=tmp_path / "step2",
        transport=AgentFileStep2Transport(response_root=tmp_path / "responses"),
        idempotency_supported=False,
    )
    result = run_document_fact_pipeline(manifest=_manifest(), content_policy=_policy(), adapter=adapter, model=MODEL)
    request = build_knowledge_card_image_request_v1(result.cards[0], profile=PROFILE)
    projection, _sidecar = project_knowledge_card_image_request_v1(request, profile=PROFILE)
    return request, projection


def test_typeset_card_flows_through_the_runner_image_validation(tmp_path: Path) -> None:
    render = _load("render_agent_card_images_v1_test", "migration/render_agent_card_images_v1.py")
    request, projection = _card_request(tmp_path)
    fonts = render._Fonts(regular=_font(), bold=_font())
    image = render.render_card_image(request.model_dump(mode="json"), size=SIZE, fonts=fonts)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    _write_png(tmp_path / "images", request.prompt_text, buffer.getvalue())

    caller = AgentFileImageCallerV1(response_root=tmp_path / "images", profile=PROFILE)
    image_bytes, gate = _validated_image_bytes(
        caller=caller,
        payload=projection.http_request.model_dump(mode="json"),
        prompt=request.prompt_text,
        profile=PROFILE,
    )

    assert gate.valid and (gate.image_width, gate.image_height) == SIZE
    assert image_bytes == buffer.getvalue()


def test_runner_reports_a_missing_agent_image_as_generation_failure(tmp_path: Path) -> None:
    request, projection = _card_request(tmp_path)
    caller = AgentFileImageCallerV1(response_root=tmp_path / "images", profile=PROFILE)

    with pytest.raises(ExamProjectRunError) as error:
        _validated_image_bytes(
            caller=caller,
            payload=projection.http_request.model_dump(mode="json"),
            prompt=request.prompt_text,
            profile=PROFILE,
        )
    assert error.value.code == "image_generation_failed"


# --------------------------------------------------------------------------- typesetter


def test_wrap_text_keeps_source_line_breaks_and_every_character() -> None:
    render = _load("render_agent_card_images_v1_wrap", "migration/render_agent_card_images_v1.py")
    from PIL import ImageFont

    font = ImageFont.truetype(str(_font()), 20)
    lines = render.wrap_text("abc def ghi\njkl", font, 60)
    assert "".join(lines) == "abc def ghijkl"
    assert lines[-1] == "jkl"


def test_table_cells_are_placed_by_source_bbox(tmp_path: Path) -> None:
    render = _load("render_agent_card_images_v1_table", "migration/render_agent_card_images_v1.py")
    fonts = render._Fonts(regular=_font(), bold=_font())
    request = {
        "title": "T",
        "presentation": "table",
        "evidence_refs": [
            {"evidence_id": "ev:a", "bbox": {"x0": 0, "y0": 0, "x1": 50, "y1": 10}},
            {"evidence_id": "ev:b", "bbox": {"x0": 50, "y0": 0, "x1": 100, "y1": 10}},
        ],
        "source_elements": [
            {"text": "A", "evidence_ids": ["ev:a"], "source_renderer": "table"},
            {"text": "B", "evidence_ids": ["ev:b"], "source_renderer": "table"},
        ],
    }
    image = render.render_card_image(request, size=SIZE, fonts=fonts)
    assert image.size == SIZE
    # Left and right halves both received ink (one cell each), the canvas is not blank.
    gray = image.convert("L")
    left = gray.crop((0, 300, SIZE[0] // 2, SIZE[1] - 100)).getextrema()
    right = gray.crop((SIZE[0] // 2, 300, SIZE[0], SIZE[1] - 100)).getextrema()
    assert left[0] < 128 and right[0] < 128


def test_render_main_writes_every_missing_png_once(tmp_path: Path) -> None:
    render = _load("render_agent_card_images_v1_main", "migration/render_agent_card_images_v1.py")
    image_root = tmp_path / "agent_images"
    (image_root / "requests").mkdir(parents=True)
    request = {
        "title": "标题",
        "presentation": "text",
        "evidence_refs": [],
        "source_elements": [{"text": "正文", "evidence_ids": [], "source_renderer": "text"}],
    }
    (image_root / "requests" / "card-x.json").write_text(json.dumps({"request": request}), encoding="utf-8")
    queue = {"size": "1536x1024", "requests": [{"raw_id": "kp:x", "title": "标题", "packet": "requests/card-x.json", "response_file": "prompt-x.png"}]}
    (image_root / "queue.json").write_text(json.dumps(queue), encoding="utf-8")

    assert render.main(["--run-root", str(tmp_path), "--font", str(_font())]) == 0
    target = image_root / "responses" / "prompt-x.png"
    first = target.read_bytes()
    with Image.open(target) as png:
        assert png.format == "PNG" and png.size == SIZE
    target.write_bytes(b"kept")
    assert render.main(["--run-root", str(tmp_path), "--font", str(_font())]) == 0
    assert target.read_bytes() == b"kept"  # existing PNGs are never overwritten without --force
    assert first


# --------------------------------------------------------------------------- CLI


def test_cli_exports_one_prompt_bound_packet_per_card(tmp_path: Path) -> None:
    cli = _load("run_agent_step2_v1_images", "migration/run_agent_step2_v1.py")
    request, projection = _card_request(tmp_path)
    workspace = cli.AgentStep2Workspace(tmp_path / "run")
    result = SimpleNamespace(requests=(request,), projections=(projection,))

    queue = cli.export_image_packets(result, workspace)

    entry = queue["requests"][0]
    assert queue["size"] == "1536x1024" and queue["transport"] == "agent_file_image_v1"
    assert entry["prompt_sha256"] == agent_image_prompt_sha256(request.prompt_text)
    assert entry["response_file"] == f"prompt-{entry['prompt_sha256']}.png"
    assert entry["present"] is False
    packet = json.loads((workspace.image_root / entry["packet"]).read_text(encoding="utf-8"))
    assert packet["http_request"]["prompt"] == request.prompt_text
    assert packet["request"]["raw_id"] == request.raw_id


def test_cli_images_stops_before_the_runner_when_a_png_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _load("run_agent_step2_v1_images_missing", "migration/run_agent_step2_v1.py")
    workspace = cli.AgentStep2Workspace(tmp_path / "run")
    workspace.image_root.mkdir(parents=True)
    queue = {"requests": [{"raw_id": "kp:x", "title": "T", "response_file": "prompt-x.png", "present": False}]}
    workspace.image_queue_path.write_text(json.dumps(queue), encoding="utf-8")
    monkeypatch.setattr(cli, "run_agent_document", lambda *args, **kwargs: {"status": "completed", "response_set_sha256": "0" * 64})

    def _never(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("runner must not execute while an image is missing")

    monkeypatch.setattr(cli, "_dry_run", _never)
    summary = cli.run_agent_images(SimpleNamespace(), workspace)

    assert summary["status"] == "images_missing"
    assert summary["missing_images"] == [{"raw_id": "kp:x", "title": "T", "response_file": "prompt-x.png"}]
