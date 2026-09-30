"""Regression coverage for OpenAI construction with an ambient SOCKS proxy."""
from __future__ import annotations

import pytest

from examcard.knowledge.llm_transport import OpenAICompatibleStep2Transport
from examcard.knowledge.source_owned_live_transport import (
    OpenAICompatibleSourceOwnedLiveTransport,
)


def test_text_transports_construct_with_ambient_socks_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = "socks5://127.0.0.1:9"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, proxy)
    monkeypatch.setenv("NO_PROXY", "")

    legacy = OpenAICompatibleStep2Transport(
        base_url="https://provider.invalid/v1",
        api_key="offline-construction-test-key",
        timeout=1,
    )
    source_owned = OpenAICompatibleSourceOwnedLiveTransport(
        base_url="https://provider.invalid/v1",
        api_key="offline-construction-test-key",
        timeout=1,
    )
    try:
        assert legacy.network_call_count == 0
        assert source_owned.network_call_count == 0
    finally:
        legacy._client.close()
        source_owned._client.close()
