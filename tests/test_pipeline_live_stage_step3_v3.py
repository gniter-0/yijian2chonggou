"""Offline live-stage safety net for Step 3 V3."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from examcard.pipeline.live_budget import LiveBudgetExceeded, LiveRunBudget
from examcard.pipeline.live_stage_step3 import load_typed_fixture_compilation, run_step3_stage
from tests.fixtures.step3_v3 import compile_v3, valid_v3_response_content, write_v2_fixture


def _budget(root: Path, *, cap: int = 4) -> LiveRunBudget:
    return LiveRunBudget(root / "budget.json", usd_cap=10.0, stage_call_caps={"step3": cap})


def _run(root: Path, compilation: Any, chat_strict, *, max_attempts: int = 2, probe=None):
    return run_step3_stage(
        compilation=compilation,
        contract_version="v3",
        chat_strict=chat_strict,
        storage=root / "step3",
        budget=_budget(root),
        base_reason="step3-v3-test",
        max_attempts=max_attempts,
        price_per_call=0.2,
        probe_price=0.1,
        probe=probe,
    )


def test_v3_live_stage_echoes_threshold_with_no_v2_fallback(tmp_path: Path) -> None:
    _path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    calls: list[Mapping[str, Any]] = []
    valid = valid_v3_response_content(compilation)

    def chat_strict(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        assert kwargs["schema_name"] == "step3_v3_single_card_result"
        return {"content": valid, "http_status": 200, "latency_ms": 1.0}

    result = _run(tmp_path, compilation, chat_strict, max_attempts=1)
    assert result["status"] == "completed"
    assert result["passed_cards"] == 1
    assert result["cards"][0]["presentation"] == "threshold"
    assert result["cards"][0]["attempts"][0]["gate_valid"] is True
    assert len(calls) == 1
    assert calls[0]["response_schema"]["properties"]["draft_cards"]


def test_v3_live_stage_retries_and_caps_calls(tmp_path: Path) -> None:
    _path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    valid = valid_v3_response_content(compilation)
    responses = ["not-json", valid]
    calls = 0

    def chat_strict(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"content": responses.pop(0), "http_status": 200}

    result = _run(tmp_path, compilation, chat_strict, max_attempts=2)
    assert result["status"] == "completed"
    assert calls == 2
    assert len(result["cards"][0]["attempts"]) == 2
    assert result["cards"][0]["attempts"][0]["gate_valid"] is False
    assert result["cards"][0]["attempts"][1]["gate_valid"] is True


def test_v3_probe_and_budget_refuse_before_provider_call(tmp_path: Path) -> None:
    _path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    calls = 0

    def chat_strict(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not run after probe refusal")

    refused = _run(tmp_path, compilation, chat_strict, probe=lambda: {"outcome": "invalid_response"})
    assert refused["status"] == "failed"
    assert refused["stop_reason"] == "step3_probe_failed"
    assert calls == 0

    typed_path, _fixture, _file_sha = write_v2_fixture(tmp_path / "loader")
    loaded = load_typed_fixture_compilation(typed_path, contract_version="v3")
    assert loaded.contracts[0].presentation == "threshold"
    with pytest.raises(LiveBudgetExceeded, match="stage step3 call budget exhausted"):
        run_step3_stage(
            compilation=loaded,
            contract_version="v3",
            chat_strict=lambda **_kwargs: {"content": "not-json", "http_status": 200},
            storage=tmp_path / "budget-step3",
            budget=LiveRunBudget(tmp_path / "budget-step3.json", usd_cap=10.0, stage_call_caps={"step3": 1}),
            base_reason="budget",
            max_attempts=2,
        )


def test_v3_loader_rejects_v1_fixture_before_provider_call(tmp_path: Path) -> None:
    typed_path, _fixture, _file_sha = write_v2_fixture(tmp_path / "mismatch")
    payload = json.loads(typed_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "phase2r810_reviewed_typed_evidence_fixture_v1"
    typed_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="requires the V1|requires the V2"):
        load_typed_fixture_compilation(typed_path, contract_version="v3")


def test_v1_v2_loader_lanes_reject_v2_fixture_before_provider_call(tmp_path: Path) -> None:
    typed_path, _fixture, _file_sha = write_v2_fixture(tmp_path / "mismatch")
    for version in ("v1", "v2"):
        with pytest.raises(ValueError, match="requires the V1"):
            load_typed_fixture_compilation(typed_path, contract_version=version)
