"""Contracts for relay idempotency headers used by Phase 3B.1.3."""
from __future__ import annotations

from types import SimpleNamespace


import examcard.evidence.vision as vision_module
from examcard.evidence.vision import PROBE_IDEMPOTENCY_CALL_COUNT, OpenAIVisionVerifier


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


def test_client_pins_zero_automatic_retries(monkeypatch):
    """封印：视觉通道必须显式钉 max_retries=0，不许继承 SDK 默认的 2 次盲重试。

    这条不是风格约束。openai SDK 默认 max_retries=2，而「服务端未回响应就断开」这类
    未知完成无法判断对方是否已处理，重发不安全；且 BudgetedVisionVerifier 每次 verify
    只扣 1 次预算，一次调用真发三次请求会让硬顶被击穿到三倍而断路器毫无察觉。
    """
    constructed: list[dict] = []

    class FakeRawCompletions:
        def create(self, **kwargs):
            return _raw_response(replayed=None, request_id="req-1")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=FakeRawCompletions())))

    def _capture(**kwargs):
        constructed.append(kwargs)
        return fake_client

    monkeypatch.setattr(vision_module, "OpenAI", _capture)
    OpenAIVisionVerifier(base_url="https://relay.example/v1", api_key="test", model="gpt-test")

    assert constructed[0]["max_retries"] == 0


def test_probe_issues_exactly_the_number_of_calls_the_budget_charges(monkeypatch):
    """封印：探测的真实请求数必须等于 PROBE_IDEMPOTENCY_CALL_COUNT。

    这个常量是 pipeline.shadow_wiring 预算包装器扣额度的依据。两者一旦漂开，
    硬顶记账就会重新短记——与 cm15 修掉的「一次调用真发三次请求」是同一形状。
    """
    issued: list[dict] = []

    class FakeRawCompletions:
        def create(self, **kwargs):
            issued.append(kwargs)
            return _raw_response(replayed="true" if len(issued) > 1 else "false", request_id="req-1")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=FakeRawCompletions())))
    monkeypatch.setattr(vision_module, "OpenAI", lambda **kwargs: fake_client)
    verifier = OpenAIVisionVerifier(base_url="https://relay.example/v1", api_key="test", model="gpt-test")

    verifier.probe_idempotency(b"\x89PNGfixture", idempotency_key="vision-count-key")

    assert len(issued) == PROBE_IDEMPOTENCY_CALL_COUNT
    assert {call["extra_headers"]["Idempotency-Key"] for call in issued} == {"vision-count-key"}
