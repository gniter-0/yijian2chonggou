"""Offline tests for the explicit Step 2 response-format wire boundary."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from examcard.knowledge.contracts import (
    STEP2A_REQUEST_VERSION,
    Step2DecoderConfig,
    Step2ResponseFormat,
    Step2Stage,
    make_step2_request_identity,
)
from examcard.knowledge.llm_transport import OpenAICompatibleStep2Transport


class _FakeStream:
    def __init__(self) -> None:
        self.closed = False

    def __iter__(self):
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="{}"),
                    finish_reason="stop",
                )
            ]
        )

    def close(self) -> None:
        self.closed = True


class _FakeRaw:
    def __init__(self, parsed: object) -> None:
        self._parsed = parsed
        self.headers = {"X-Request-Id": "offline-response-mode-test"}
        self.status_code = 200

    def parse(self) -> object:
        return self._parsed


class _FakeCompletions:
    def __init__(self, parsed: object) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raw = _FakeRaw(parsed)

    def create(self, **kwargs: Any) -> _FakeRaw:
        self.calls.append(kwargs)
        return self._raw


def _transport(*, mode: Step2ResponseFormat, stream: bool) -> tuple[OpenAICompatibleStep2Transport, _FakeCompletions]:
    parsed = _FakeStream() if stream else SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="{}"),
                finish_reason="stop",
            )
        ]
    )
    transport = OpenAICompatibleStep2Transport(
        base_url="http://127.0.0.1:9/v1",
        api_key="offline-test-key",
        timeout=1,
        stream=stream,
    )
    api = _FakeCompletions(parsed)
    transport._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=api),
        )
    )
    return transport, api


def _identity(mode: Step2ResponseFormat):
    return make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=STEP2A_REQUEST_VERSION,
        model="offline-step2-v1",
        payload_sha256="0" * 64,
        prompt_template="offline response-mode test",
        response_schema={"type": "object"},
        decoder=Step2DecoderConfig(response_format=mode),
    )


@pytest.mark.parametrize("stream", (False, True))
@pytest.mark.parametrize(
    "mode",
    (Step2ResponseFormat.JSON_SCHEMA_STRICT, Step2ResponseFormat.JSON_OBJECT),
)
def test_response_mode_mapping_is_identical_for_streaming_and_non_streaming(
    stream: bool,
    mode: Step2ResponseFormat,
) -> None:
    transport, api = _transport(mode=mode, stream=stream)

    completion = transport.complete_payload(
        identity=_identity(mode),
        model_payload={"sample_id": "offline-sample"},
        response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        prompt="offline response-mode test",
        use_idempotency_key=False,
    )

    assert completion.content == "{}"
    assert len(api.calls) == 1
    wire_format = api.calls[0]["response_format"]
    if mode == Step2ResponseFormat.JSON_SCHEMA_STRICT:
        assert wire_format["type"] == "json_schema"
        assert wire_format["json_schema"]["strict"] is True
        assert wire_format["json_schema"]["name"] == "step2a_result"
        assert wire_format["json_schema"]["schema"]["type"] == "object"
    else:
        assert wire_format == {"type": "json_object"}
        assert "json_schema" not in wire_format


def test_response_mode_failure_does_not_fallback_or_retry() -> None:
    transport = OpenAICompatibleStep2Transport(
        base_url="http://127.0.0.1:9/v1",
        api_key="offline-test-key",
        timeout=1,
    )

    class _RaisingCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_kwargs: Any) -> None:
            self.calls += 1
            raise RuntimeError("offline provider failure")

    api = _RaisingCompletions()
    transport._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=api),
        )
    )

    with pytest.raises(RuntimeError, match="offline provider failure"):
        transport.complete_payload(
            identity=_identity(Step2ResponseFormat.JSON_OBJECT),
            model_payload={"sample_id": "offline-sample"},
            response_schema={"type": "object"},
            prompt="offline response-mode test",
            use_idempotency_key=False,
        )

    assert api.calls == 1
