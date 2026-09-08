"""Offline safety net for the production Step3 v2 live stage."""
from __future__ import annotations

import json
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
from examcard.pipeline.live_budget import LiveBudgetExceeded, LiveRunBudget
from examcard.pipeline.live_stage_step3 import run_step3_stage
from tests.fixtures.compilers_synthetic import make_reviewed_typed_evidence_fixture


SOURCE_SHA = "2" * 64


@pytest.fixture
def compilation() -> Step3Compilation:
    return compile_reviewed_fixture(
        make_reviewed_typed_evidence_fixture(("table",)),
        source_fixture_sha256=SOURCE_SHA,
    )


def _valid_content(compilation: Step3Compilation) -> str:
    model_input = build_single_card_model_input(compilation.contracts[0], compilation.model_input.cards[0])
    encoded = encode_single_card_response(build_fake_single_card_result(model_input))
    return json.loads(encoded.decode("utf-8"))["choices"][0]["message"]["content"]


def _budget(root: Path, *, call_cap: int = 8) -> LiveRunBudget:
    return LiveRunBudget(root / "budget.json", usd_cap=10.0, stage_call_caps={"step3": call_cap})


def _run(
    root: Path,
    compilation: Step3Compilation,
    chat_strict,
    *,
    budget: LiveRunBudget | None = None,
    max_attempts: int = 2,
    probe=None,
) -> dict[str, Any]:
    return run_step3_stage(
        compilation=compilation,
        contract_version="v2",
        chat_strict=chat_strict,
        storage=root / "step3",
        budget=budget or _budget(root),
        base_reason="step3-live-test",
        max_attempts=max_attempts,
        price_per_call=0.2,
        probe_price=0.1,
        probe=probe,
    )


def test_v2_happy_path_persists_response_gate_and_budget(repo_tmp_path: Path, compilation: Step3Compilation) -> None:
    calls: list[Mapping[str, Any]] = []
    valid = _valid_content(compilation)

    def chat_strict(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"content": valid, "http_status": 200, "latency_ms": 1.0}

    result = _run(repo_tmp_path, compilation, chat_strict)

    card = result["cards"][0]
    card_dir = repo_tmp_path / "step3" / "cards" / f"01_{compilation.contracts[0].contract_id.rsplit(':', 1)[-1]}"
    assert result["status"] == "completed"
    assert result["card_count"] == 1
    assert result["passed_cards"] == 1
    assert card["outcome"] == "success"
    assert len(calls) == 1
    assert (card_dir / "model-input.json").is_file()
    assert (card_dir / "attempt-1.response.raw.json").is_file()
    assert (card_dir / "attempt-1.gate.json").is_file()
    assert json.loads((card_dir / "attempt-1.gate.json").read_text(encoding="utf-8"))["valid"] is True
    assert card["attempts"][0]["gate_valid"] is True


def test_v2_invalid_then_valid_retries_once_with_distinct_attempt_ids(
    repo_tmp_path: Path, compilation: Step3Compilation
) -> None:
    calls: list[Mapping[str, Any]] = []
    responses = ["not-json", _valid_content(compilation)]

    def chat_strict(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"content": responses.pop(0), "http_status": 200}

    result = _run(repo_tmp_path, compilation, chat_strict, max_attempts=2)

    attempts = result["cards"][0]["attempts"]
    assert result["status"] == "completed"
    assert result["passed_cards"] == 1
    assert len(calls) == 2
    assert len(attempts) == 2
    assert attempts[0]["request_id"] != attempts[1]["request_id"]
    assert attempts[0]["gate_valid"] is False
    assert attempts[1]["gate_valid"] is True


def test_v2_retry_exhaustion_is_strictly_capped(repo_tmp_path: Path, compilation: Step3Compilation) -> None:
    calls = 0

    def chat_strict(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"content": "not-json", "http_status": 200}

    result = _run(repo_tmp_path, compilation, chat_strict, max_attempts=3)

    assert result["status"] == "completed_with_failures"
    assert result["card_count"] == 1
    assert result["passed_cards"] == 0
    assert calls == 3
    assert len(result["cards"][0]["attempts"]) == 3


def test_v2_probe_refusal_prevents_card_chat(repo_tmp_path: Path, compilation: Step3Compilation) -> None:
    chat_calls = 0
    probe_calls = 0

    def chat_strict(**_kwargs: Any) -> dict[str, Any]:
        nonlocal chat_calls
        chat_calls += 1
        raise AssertionError("card chat must not run after probe refusal")

    def probe() -> Mapping[str, Any]:
        nonlocal probe_calls
        probe_calls += 1
        return {"outcome": "invalid_response"}

    result = _run(repo_tmp_path, compilation, chat_strict, probe=probe)

    assert result["status"] == "failed"
    assert result["stop_reason"] == "step3_probe_failed"
    assert probe_calls == 1
    assert chat_calls == 0


def test_v2_budget_refusal_happens_before_excess_provider_call(
    repo_tmp_path: Path, compilation: Step3Compilation
) -> None:
    calls = 0

    def chat_strict(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"content": "not-json", "http_status": 200}

    budget = _budget(repo_tmp_path, call_cap=1)
    with pytest.raises(LiveBudgetExceeded, match="stage step3 call budget exhausted"):
        _run(repo_tmp_path, compilation, chat_strict, budget=budget, max_attempts=2)

    assert calls == 1
    assert budget.snapshot()["per_stage"]["step3"]["calls"] == 1
