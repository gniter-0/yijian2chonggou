"""生产配方 transport 可选参数测试（P3 T2）：SSE 流式聚合 + system_suffix 注入。

全离线（规范 T4 默认禁网）：以假 SDK client 对象替换 `_client`，不触网络。
"""
from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace


from examcard.knowledge.contracts import (
    STEP2A_REQUEST_VERSION,
    Step2Stage,
    fingerprint_step2_prompt,
    make_step2_request_identity,
)
from examcard.knowledge.llm import OpenAICompatibleStep2Transport, TransportCompletion
from examcard.knowledge.reinforcement import (
    STEP2A_REINFORCEMENT_V1,
    STEP2A_REINFORCEMENT_V1_ID,
    reinforcement_id,
)


class _FakeStream:
    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self) -> None:
        self.closed = True


class _FakeRaw:
    def __init__(self, *, parsed, headers, status_code=200) -> None:
        self._parsed = parsed
        self.headers = headers
        self.status_code = status_code

    def parse(self):
        return self._parsed


class _FakeCompletionsAPI:
    """记录 create(**kwargs) 并返回预置 raw 响应的假 SDK 端点。"""

    def __init__(self, raw) -> None:
        self._raw = raw
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._raw


def _install_fake_client(transport: OpenAICompatibleStep2Transport, raw) -> _FakeCompletionsAPI:
    api = _FakeCompletionsAPI(raw)
    transport._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=api))
    )
    return api


def _transport(**kwargs) -> OpenAICompatibleStep2Transport:
    return OpenAICompatibleStep2Transport(
        base_url="http://127.0.0.1:9/v1", api_key="offline-test-key", timeout=1, **kwargs
    )


def _sse_chunk(content=None, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)]
    )


def _non_streaming_response(content: str, finish_reason: str | None = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)]
    )


class TestStreamingAggregation:
    def test_stream_true_aggregates_sse_deltas_into_equivalent_completion(self) -> None:
        transport = _transport(stream=True)
        stream = _FakeStream(
            [
                _sse_chunk(" {\"proposals\""),
                SimpleNamespace(choices=[]),  # keep-alive/心跳块：无 choices 必须安全跳过
                _sse_chunk(": ["),
                _sse_chunk(None),  # 空 delta 块
                _sse_chunk("]} "),
                _sse_chunk(None, finish_reason="stop"),
            ]
        )
        raw = _FakeRaw(parsed=stream, headers={"X-Request-Id": "req-stream-1"}, status_code=200)
        api = _install_fake_client(transport, raw)

        completion = transport._raw_create(
            model="gpt-5.6-sol",
            messages=[{"role": "system", "content": "s"}],
            max_tokens=64,
            temperature=0,
            response_format={"type": "json_object"},
            idempotency_key=None,
        )

        assert completion.content == "{\"proposals\": []}"  # 拼 delta 后 strip，与非流式同构
        assert completion.finish_reason == "stop"
        assert completion.provider_request_id == "req-stream-1"
        assert completion.http_status == 200
        assert completion.headers == {"x-request-id": "req-stream-1"}
        assert api.calls[0]["stream"] is True
        assert transport.network_call_count == 1
        assert stream.closed is True

    def test_stream_cutoff_without_stop_is_distinguishable(self) -> None:
        transport = _transport(stream=True)
        stream = _FakeStream([_sse_chunk("{\"partial\""), _sse_chunk(": true")])
        api = _install_fake_client(transport, _FakeRaw(parsed=stream, headers={}))

        completion = transport._raw_create(
            model="gpt-5.6-sol",
            messages=[],
            max_tokens=64,
            temperature=0,
            response_format={"type": "json_object"},
            idempotency_key=None,
        )

        assert completion.content == "{\"partial\": true"
        assert completion.finish_reason is None  # 中途掐断：无 finish_reason="stop" 可审计
        assert stream.closed is True
        assert api.calls[0]["stream"] is True

    def test_default_stream_false_keeps_non_streaming_create(self) -> None:
        transport = _transport()
        assert transport.stream is False
        raw = _FakeRaw(
            parsed=_non_streaming_response("  {\"ok\": true}  "),
            headers={"Request-Id": "req-plain-1"},
            status_code=200,
        )
        api = _install_fake_client(transport, raw)

        completion = transport._raw_create(
            model="gpt-5.6-sol",
            messages=[],
            max_tokens=64,
            temperature=0,
            response_format={"type": "json_object"},
            idempotency_key="step2-abc",
        )

        assert "stream" not in api.calls[0]  # 默认路径不得引入 stream 参数（逐字节不变）
        assert api.calls[0]["extra_headers"] == {"Idempotency-Key": "step2-abc"}
        assert completion.content == "{\"ok\": true}"
        assert completion.finish_reason == "stop"
        assert completion.provider_request_id == "req-plain-1"
        assert transport.network_call_count == 1

    def test_transport_completion_finish_reason_defaults_to_none(self) -> None:
        assert TransportCompletion(content="x").finish_reason is None


def _identity(prompt_template: str):
    return make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=STEP2A_REQUEST_VERSION,
        model="deterministic-fake-v1",
        payload_sha256="0" * 64,
        prompt_template=prompt_template,
        response_schema={"type": "object"},
    )


class TestSystemSuffixInjection:
    PROMPT = "V25 冻结模板本体（测试替身）"

    def _messages_sent(self, transport: OpenAICompatibleStep2Transport) -> list[dict]:
        api = _install_fake_client(
            transport, _FakeRaw(parsed=_non_streaming_response("{}"), headers={})
        )
        transport.complete_payload(
            identity=_identity(self.PROMPT),
            model_payload={"sample_id": "t2-snapshot"},
            response_schema={"type": "object"},
            prompt=self.PROMPT,
            use_idempotency_key=False,
        )
        return api.calls[0]["messages"]

    def test_suffix_appends_to_system_tail_message_snapshot(self) -> None:
        messages = self._messages_sent(_transport(system_suffix=STEP2A_REINFORCEMENT_V1))
        assert messages == [
            {"role": "system", "content": self.PROMPT + STEP2A_REINFORCEMENT_V1},
            {
                "role": "user",
                "content": "Return only the required JSON object for this immutable request data:\n"
                + json.dumps({"sample_id": "t2-snapshot"}, ensure_ascii=False),
            },
        ]

    def test_default_none_keeps_messages_byte_identical(self) -> None:
        messages = self._messages_sent(_transport())
        assert messages == OpenAICompatibleStep2Transport._messages_for_model_payload(
            prompt=self.PROMPT, model_payload={"sample_id": "t2-snapshot"}
        )
        assert messages[0]["content"] == self.PROMPT

    def test_suffix_and_stream_never_enter_identity_derivation(self) -> None:
        identity = _identity(self.PROMPT)
        # identity 指纹绑定的是未加后缀的模板本体；后缀仅存在于消息构造层。
        assert identity.prompt_sha256 == fingerprint_step2_prompt(self.PROMPT)
        assert identity == _identity(self.PROMPT)  # 构造参数不同的 transport 不影响 identity


class TestReinforcementModule:
    def test_reinforcement_id_is_content_sha256_short_address(self) -> None:
        digest = hashlib.sha256(STEP2A_REINFORCEMENT_V1.encode("utf-8")).hexdigest()
        assert STEP2A_REINFORCEMENT_V1_ID == digest[:16]
        assert STEP2A_REINFORCEMENT_V1_ID == reinforcement_id(STEP2A_REINFORCEMENT_V1)
        assert re.fullmatch(r"[0-9a-f]{16}", STEP2A_REINFORCEMENT_V1_ID)

    def test_reinforcement_text_pinned_verbatim(self) -> None:
        # pilot 驱动 REINFORCEMENT_NOTE 一字不改搬运（P3 T2）；改动本文必须换新版本常量。
        assert STEP2A_REINFORCEMENT_V1_ID == "7a33f88fdb676c72"
        assert STEP2A_REINFORCEMENT_V1.startswith("\n\n【附加硬性要求（优先级最高）】\n")
        for marker in ("考试元数据不是知识点", "严禁按行拆分", "水印散字", "diagram_relation", "宁缺毋滥"):
            assert marker in STEP2A_REINFORCEMENT_V1
        assert [f"{n}." in STEP2A_REINFORCEMENT_V1 for n in range(1, 6)] == [True] * 5
