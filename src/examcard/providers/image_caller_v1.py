"""Reusable injected image-provider transport boundary.

This module owns only the HTTP call shape used by the exam-project runner.  It
does not acquire credentials, interpret image envelopes, retry, or persist
provider state.  The HTTP client is deliberately injected so credential and
client construction stay at the process boundary.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Final, Protocol, TypeAlias
from urllib.parse import urlsplit


__all__ = [
    "ImageCallerErrorV1",
    "ImageCallerV1",
    "ImageHttpClientV1",
    "ImageHttpResponseV1",
    "OpenAICompatibleImageCallerV1",
]


ImageCallerV1: TypeAlias = Callable[[Mapping[str, object]], tuple[int, bytes]]


class ImageHttpResponseV1(Protocol):
    """Minimal response surface required from an injected HTTP client."""

    @property
    def status_code(self) -> int: ...

    @property
    def content(self) -> bytes: ...


class ImageHttpClientV1(Protocol):
    """Minimal POST surface required from an injected HTTP client."""

    def post(self, url: str, *, json: Mapping[str, object]) -> ImageHttpResponseV1: ...


class ImageCallerErrorV1(RuntimeError):
    """Fixed-text transport failure that cannot expose provider details."""

    _MESSAGE: Final = "image provider request failed"

    def __init__(self) -> None:
        super().__init__(self._MESSAGE)

    def __repr__(self) -> str:
        return "<image-caller-error-v1>"


@dataclass(frozen=True, slots=True, repr=False)
class OpenAICompatibleImageCallerV1:
    """Call an already configured OpenAI-compatible image HTTP endpoint.

    The client may contain credentials and is therefore treated as an opaque
    runtime object.  This wrapper never prints, serializes, or inspects it.
    Response validation and PNG decoding remain the runner's responsibility.
    """

    client: ImageHttpClientV1
    base_url: str
    endpoint_path: str = "/images/generations"

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str):
            raise ImageCallerErrorV1()
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ImageCallerErrorV1()
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ImageCallerErrorV1()
        if not isinstance(self.endpoint_path, str) or not self.endpoint_path.startswith("/"):
            raise ImageCallerErrorV1()
        if "?" in self.endpoint_path or "#" in self.endpoint_path:
            raise ImageCallerErrorV1()

    def __repr__(self) -> str:
        return "<openai-compatible-image-caller-v1>"

    def __str__(self) -> str:
        return "<openai-compatible-image-caller-v1>"

    def __getstate__(self) -> None:
        """Prevent accidental persistence of an opaque runtime client."""

        raise TypeError("image provider client is runtime-only")

    def __call__(self, payload: Mapping[str, object]) -> tuple[int, bytes]:
        """Perform exactly one provider POST and return its opaque response."""

        endpoint = f"{self.base_url.rstrip('/')}{self.endpoint_path}"
        try:
            response = self.client.post(endpoint, json=dict(payload))
            status = response.status_code
            body = response.content
        except Exception:
            raise ImageCallerErrorV1() from None
        if not isinstance(status, int) or not 100 <= status <= 599 or not isinstance(body, bytes):
            raise ImageCallerErrorV1()
        return status, body
