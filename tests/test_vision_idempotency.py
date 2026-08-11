"""Contracts for relay idempotency headers used by Phase 3B.1.3."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


import examcard.evidence.vision as vision_module
from examcard.evidence.vision import OpenAIVisionVerifier


def _raw_response(*, replayed: str | None, request_id: str):
    headers = {"x-request-id": request_id}
    if replayed is not None:
        headers["idempotency-replayed"] = replayed
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"visible_text":"确认文本","uncertain":false}'))]
    )
    return SimpleNamespace(headers=headers, parse=lambda: response)


def test_verify_passes_stable_idempotency_header_and_reads_replay_signal(monkeypatch):
    calls: list[dict] = []

    class FakeRawCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _raw_response(replayed="true", request_id="req-1")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=FakeRawCompletions())))
    monkeypatch.setattr(vision_module, "OpenAI", lambda **kwargs: fake_client)
    verifier = OpenAIVisionVerifier(base_url="https://relay.example/v1", api_key="test", model="gpt-test")

    result = verifier.verify(b"\x89PNGfixture", idempotency_key="vision-stable-key")

    assert calls[0]["extra_headers"] == {"Idempotency-Key": "vision-stable-key"}
    assert result.provider_request_id == "req-1"
    assert result.idempotency_replayed is True


def test_probe_requires_explicit_second_replay_header(monkeypatch):
    raw_calls = [
        _raw_response(replayed="false", request_id="req-1"),
        _raw_response(replayed="true", request_id="req-1"),
    ]

    class FakeRawCompletions:
        def create(self, **kwargs):
            return raw_calls.pop(0)

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=FakeRawCompletions())))
    monkeypatch.setattr(vision_module, "OpenAI", lambda **kwargs: fake_client)
    verifier = OpenAIVisionVerifier(base_url="https://relay.example/v1", api_key="test", model="gpt-test")

    result = verifier.probe_idempotency(b"\x89PNGfixture", idempotency_key="vision-probe-key")

    assert result.supported is True
    assert result.second_replayed is True
    assert result.second_provider_request_id == "req-1"
