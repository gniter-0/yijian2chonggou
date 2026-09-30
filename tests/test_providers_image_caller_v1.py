"""Offline tests for the reusable injected image caller."""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from typing import Mapping

import pytest

from examcard.providers.image_caller_v1 import (
    ImageCallerErrorV1,
    OpenAICompatibleImageCallerV1,
)


@dataclass(frozen=True)
class _Response:
    status_code: int
    content: bytes


class _FakeClient:
    def __init__(self, response: _Response | None = None, error: Exception | None = None) -> None:
        self.response = response or _Response(200, b"opaque")
        self.error = error
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def post(self, url: str, *, json: Mapping[str, object]) -> _Response:
        self.calls.append((url, json))
        if self.error is not None:
            raise self.error
        return self.response


def test_caller_posts_once_to_injected_client_without_network() -> None:
    client = _FakeClient(_Response(200, b"provider-response"))
    caller = OpenAICompatibleImageCallerV1(client=client, base_url="https://images.example/v1/")

    result = caller({"model": "image-model", "prompt": "draw a card"})

    assert result == (200, b"provider-response")
    assert client.calls == [("https://images.example/v1/images/generations", {"model": "image-model", "prompt": "draw a card"})]


def test_caller_redacts_transport_errors_and_has_no_authority_methods() -> None:
    secret = "Bearer synthetic-secret-value"
    caller = OpenAICompatibleImageCallerV1(
        client=_FakeClient(error=RuntimeError(secret)),
        base_url="https://images.example/v1",
    )

    with pytest.raises(ImageCallerErrorV1) as raised:
        caller({"prompt": "draw"})

    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)
    assert not hasattr(caller, "upload")
    assert not hasattr(caller, "lookup")
    assert not hasattr(caller, "retry")
    assert not hasattr(caller, "create_receipt")


def test_caller_repr_str_and_pickle_do_not_expose_opaque_client() -> None:
    secret = "opaque-provider-secret"
    caller = OpenAICompatibleImageCallerV1(
        client=_FakeClient(error=RuntimeError(secret)),
        base_url="https://images.example/v1",
    )

    assert secret not in repr(caller)
    assert secret not in str(caller)
    with pytest.raises(TypeError, match="runtime-only"):
        pickle.dumps(caller)


@pytest.mark.parametrize(
    "base_url",
    (
        "",
        "images.example/v1",
        "https://user:secret@images.example/v1",
        "https://images.example/v1?token=secret",
        "https://images.example/v1#secret",
    ),
)
def test_caller_rejects_non_public_endpoint_forms_without_echoing_values(base_url: str) -> None:
    with pytest.raises(ImageCallerErrorV1) as raised:
        OpenAICompatibleImageCallerV1(client=_FakeClient(), base_url=base_url)
    if base_url:
        assert base_url not in str(raised.value)
        assert base_url not in repr(raised.value)


def test_caller_rejects_invalid_provider_response_shape() -> None:
    caller = OpenAICompatibleImageCallerV1(
        client=_FakeClient(_Response(700, "not-bytes")),
        base_url="https://images.example/v1",
    )

    with pytest.raises(ImageCallerErrorV1):
        caller({"prompt": "draw"})
