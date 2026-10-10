"""File-exchange image caller: an interactive agent supplies the PNG instead of an API.

The exam-project runner calls an ``ImageCallerV1`` with the public image request
(``projection.http_request``) and validates whatever comes back through the
unchanged ``inspect_image_envelope`` gate.  This caller answers that request
from a PNG file the agent wrote beforehand, so every image check (exact
envelope keys, strict base64, verifiable PNG, exact profile dimensions,
``revised_prompt`` equal to the prompt, consistent usage sums) and the runner's
own sha256 re-check still run exactly as they do for a provider response.

Honesty and binding rules:

- A PNG is bound to the exact prompt it was produced for: the file name is the
  sha256 of the request prompt, so editing a card (and therefore its prompt)
  can never silently reuse an older image.
- ``created`` and ``usage`` are transport-only metadata in the image envelope
  profile.  No provider was called and no token was billed, so ``created`` is
  ``0`` and every usage counter is ``0``; nothing is invented.
- The PNG bytes are passed through untouched; this module never resizes,
  re-encodes or repairs an image.  A wrong size fails the envelope gate.
- A missing file raises :class:`AgentImageResponseMissing`; callers should
  verify completeness *before* running (see ``migration/run_agent_step2_v1.py``).
- No network I/O happens here.
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from .envelope_profiles import ImageEnvelopeProfile

__all__ = [
    "AGENT_IMAGE_TRANSPORT_ID",
    "AgentFileImageCallerV1",
    "AgentImageRequestInvalid",
    "AgentImageResponseMissing",
    "agent_image_envelope_body",
    "agent_image_response_path",
    "agent_image_prompt_sha256",
]

AGENT_IMAGE_TRANSPORT_ID: Final = "agent_file_image_v1"


class AgentImageResponseMissing(FileNotFoundError):
    """No agent-supplied PNG exists for the requested prompt."""


class AgentImageRequestInvalid(ValueError):
    """The runner payload carries no usable prompt."""


def agent_image_prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def agent_image_response_path(response_root: Path, prompt_sha256: str) -> Path:
    """Where the agent must put the PNG for the prompt with this sha256."""

    if len(prompt_sha256) != 64 or any(char not in "0123456789abcdef" for char in prompt_sha256):
        raise AgentImageRequestInvalid("prompt sha256 must be 64 lowercase hex characters")
    return response_root / f"prompt-{prompt_sha256}.png"


def agent_image_envelope_body(png: bytes, prompt: str, profile: ImageEnvelopeProfile) -> bytes:
    """Wrap untouched PNG bytes in the profiled success envelope (zero usage)."""

    usage: dict[str, object] = {key: 0 for key in profile.usage_keys}
    usage["input_tokens_details"] = {key: 0 for key in profile.input_detail_keys}
    usage["output_tokens_details"] = {key: 0 for key in profile.output_detail_keys}
    item = {"b64_json": base64.b64encode(png).decode("ascii"), "revised_prompt": prompt}
    document = {"created": 0, "data": [item], "usage": usage}
    # Shape follows the profile key sets; refuse to answer if the profile ever changes.
    if sorted(document) != sorted(profile.top_level_keys) or sorted(item) != sorted(profile.data_item_keys):
        raise AgentImageRequestInvalid("image envelope profile keys changed; agent caller must be reviewed")
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class AgentFileImageCallerV1:
    """``ImageCallerV1`` that reads ``prompt-<sha256>.png`` from ``response_root``."""

    def __init__(self, *, response_root: Path, profile: ImageEnvelopeProfile) -> None:
        if not isinstance(response_root, Path):
            raise TypeError("agent image response root must be a Path")
        self._response_root = response_root
        self._profile = profile

    @property
    def response_root(self) -> Path:
        return self._response_root

    def __call__(self, payload: Mapping[str, object]) -> tuple[int, bytes]:
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise AgentImageRequestInvalid("image request payload has no prompt")
        path = agent_image_response_path(self._response_root, agent_image_prompt_sha256(prompt))
        if not path.is_file():
            raise AgentImageResponseMissing(f"agent image response is missing: {path.name}")
        return 200, agent_image_envelope_body(path.read_bytes(), prompt, self._profile)
