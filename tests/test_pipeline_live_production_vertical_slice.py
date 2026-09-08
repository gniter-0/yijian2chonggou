"""Narrow offline vertical slices through the live orchestrator and Step3 lane."""
from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from examcard.compilers.step3_card import Step3Compilation, compile_reviewed_fixture
from examcard.compilers.step3_response_v2 import (
    build_fake_single_card_result,
    build_single_card_model_input,
    encode_single_card_response,
)
from examcard.pipeline.live_config import LiveRunConfig
from examcard.pipeline.live_orchestrate import orchestrate
from tests.fixtures.compilers_synthetic import make_reviewed_typed_evidence_fixture


_REAL_REPO = Path(__file__).resolve().parents[1]
_EXAM_ID = "gonglu_golden5_rehearsal"


def _seed_exam_workspace(root: Path) -> None:
    """Seed only committed metadata required by ExamPaths for a Step3-only run."""
    for relative in (
        Path("exams") / _EXAM_ID / "fixtures" / "golden_samples" / f"{_EXAM_ID}_phase0.yaml",
        Path("configs/v2/material_profiles/yijian_gonglu_2026.yaml"),
        Path("configs/v2/content_policies/core_only_v1.yaml"),
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REAL_REPO / relative, target)


def _seed_typed_fixture(root: Path) -> tuple[Path, Step3Compilation]:
    fixture = make_reviewed_typed_evidence_fixture(("table",))
    path = root / "reviewed-typed-evidence-fixture.json"
    path.write_text(json.dumps(fixture.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    source_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, compile_reviewed_fixture(fixture, source_fixture_sha256=source_sha)


def _valid_content(compilation: Step3Compilation) -> str:
    model_input = build_single_card_model_input(compilation.contracts[0], compilation.model_input.cards[0])
    encoded = encode_single_card_response(build_fake_single_card_result(model_input))
    return json.loads(encoded.decode("utf-8"))["choices"][0]["message"]["content"]


def _config(root: Path, fixture_path: Path, *, stages: tuple[str, ...], max_attempts: int) -> LiveRunConfig:
    return LiveRunConfig(
        exam_id=_EXAM_ID,
        stages=stages,
        mode="live",
        run_label=f"step3-vertical-{max_attempts}",
        budget_usd=1.0,
        step3_contract="v2",
        step3_mode="echo",
        step3_typed_fixture=fixture_path,
        skip_preflight=True,
        max_attempts=max_attempts,
        repo_root=root,
    )


def test_orchestrate_live_propagates_max_attempts_to_production_step3(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    fixture_path, compilation = _seed_typed_fixture(repo_tmp_path)
    responses = ["not-json", "not-json", _valid_content(compilation)]
    calls: list[Mapping[str, Any]] = []

    def chat_strict(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"content": responses.pop(0), "http_status": 200}

    report, exit_code = orchestrate(
        _config(repo_tmp_path, fixture_path, stages=("step3",), max_attempts=3),
        {
            "pricing": {"step3_card_call": 0.1},
            "step3_card_count": 1,
            "chat_strict": chat_strict,
        },
    )

    assert exit_code == 0
    assert report.mode == "live"
    assert report.stop_reason == "completed"
    assert report.network_calls == 3
    assert report.usd_estimate == pytest.approx(0.3)
    stage = next(item for item in report.stages if item.stage == "step3")
    assert stage.status == "completed"
    assert stage.samples_total == 1
    assert stage.samples_succeeded == 1
    assert len(calls) == 3
    run_root = repo_tmp_path / "exams" / _EXAM_ID / "data" / "live_runs" / report.run_id
    persisted = json.loads((run_root / "live_run_report.json").read_text(encoding="utf-8"))
    assert persisted["extras"]["budget"]["per_stage"]["step3"]["calls"] == 3


def test_orchestrate_live_step3_failure_skips_downstream_step4(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    fixture_path, _compilation = _seed_typed_fixture(repo_tmp_path)
    calls = 0

    def chat_strict(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"content": "not-json", "http_status": 200}

    report, exit_code = orchestrate(
        _config(repo_tmp_path, fixture_path, stages=("step3", "step4"), max_attempts=1),
        {
            "pricing": {"step3_card_call": 0.1},
            "step3_card_count": 1,
            "chat_strict": chat_strict,
        },
    )

    by_stage = {item.stage: item for item in report.stages}
    assert exit_code == 1
    assert report.stop_reason == "completed_with_failures"
    assert by_stage["step3"].status == "completed_with_failures"
    assert by_stage["step4"].status == "skipped"
    assert calls == 1
    assert report.network_calls == 1
