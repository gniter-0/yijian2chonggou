"""File-exchange Step 2 transport: an interactive agent answers instead of an API.

The agent reads the exact model-visible request exported for one Step 2A page,
writes one JSON response envelope per request, and this transport hands the
envelope payload to the unchanged ``Step2OpenAIAdapter``.  Every local gate
(payload schema, anchor selection, numeric/condition derivation, grounding
contract, cache and journal) therefore runs exactly as it does for a provider
completion.

Honesty and binding rules:

- The request identity model must carry the ``agent-file:`` prefix, so agent
  answers never share a ``request_id``/cache entry with a real provider model
  and journals never claim a provider produced them.
- An envelope is accepted only for the request it was written against: the
  request id, model label, prompt fingerprint, response-schema fingerprint and
  projection fingerprint must all match the live request.
- A missing or mismatched file raises.  The adapter persists that as a failed
  terminal record, so callers must verify completeness *before* running (see
  ``migration/run_agent_step2_v1.py``); this transport never waits or retries.
- No network I/O happens here.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import Field

from ..materials.schema import StrictModel
from .contracts import (
    Step2ARequest,
    Step2BRequest,
    canonical_step2_sha256,
    fingerprint_step2_prompt,
)
from .llm_transport import OpenAICompatibleStep2Transport, TransportCompletion

__all__ = [
    "AGENT_STEP2_MODEL_PREFIX",
    "AGENT_STEP2_RESPONSE_SCHEMA_VERSION",
    "AGENT_STEP2_TRANSPORT_ID",
    "AgentFileStep2Transport",
    "AgentStep2ResponseAuthor",
    "AgentStep2ResponseBindingError",
    "AgentStep2ResponseEnvelopeV1",
    "AgentStep2ResponseMissing",
    "agent_step2_model_payload",
    "agent_step2_response_path",
    "load_agent_step2_response",
    "verify_agent_step2_response_binding",
]

AGENT_STEP2_MODEL_PREFIX: Final = "agent-file:"
AGENT_STEP2_RESPONSE_SCHEMA_VERSION: Final = "examcard_agent_step2_response_v1"
AGENT_STEP2_TRANSPORT_ID: Final = "agent_file_v1"

_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"


class AgentStep2ResponseMissing(ValueError):
    """No agent response file exists for the live request."""


class AgentStep2ResponseBindingError(ValueError):
    """The agent response file was written for a different request."""


class AgentStep2ResponseAuthor(StrictModel):
    """Self-reported authorship; never an approval or a review receipt."""

    agent_label: str = Field(min_length=1, max_length=120)
    page_image_reviewed: bool
    notes: str = Field(default="", max_length=4000)


class AgentStep2ResponseEnvelopeV1(StrictModel):
    schema_version: Literal["examcard_agent_step2_response_v1"] = AGENT_STEP2_RESPONSE_SCHEMA_VERSION
    request_id: str = Field(pattern=r"^step2[ab]-request:[0-9a-f]{64}$")
    model: str = Field(min_length=len(AGENT_STEP2_MODEL_PREFIX) + 1)
    prompt_sha256: str = Field(pattern=_SHA256_PATTERN)
    response_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    projection_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    author: AgentStep2ResponseAuthor
    payload: dict[str, Any]


def agent_step2_response_path(response_root: Path, request_id: str) -> Path:
    """Return the single response location for one request id."""

    return Path(response_root) / f"{request_id.replace(':', '_')}.response.json"


def agent_step2_model_payload(request: Step2ARequest | Step2BRequest) -> dict[str, Any]:
    """The exact model-visible payload a provider would receive for ``request``."""

    return OpenAICompatibleStep2Transport.model_payload(request)


def load_agent_step2_response(path: Path) -> tuple[AgentStep2ResponseEnvelopeV1, str]:
    """Load one envelope and return it with the sha256 of its exact bytes."""

    path = Path(path)
    if not path.is_file():
        raise AgentStep2ResponseMissing("agent Step 2 response file is missing for this request")
    raw = path.read_bytes()
    envelope = AgentStep2ResponseEnvelopeV1.model_validate_json(raw)
    return envelope, hashlib.sha256(raw).hexdigest()


def verify_agent_step2_response_binding(
    envelope: AgentStep2ResponseEnvelopeV1,
    *,
    request: Step2ARequest | Step2BRequest,
    response_schema: Mapping[str, Any],
    prompt: str,
) -> None:
    """Refuse an envelope unless it is bound to exactly this live request."""

    identity = request.identity
    if not identity.model.startswith(AGENT_STEP2_MODEL_PREFIX):
        raise AgentStep2ResponseBindingError(
            "agent file transport requires an agent-file: model label in the request identity"
        )
    mismatched: list[str] = []
    if envelope.request_id != identity.request_id:
        mismatched.append("request_id")
    if envelope.model != identity.model:
        mismatched.append("model")
    if envelope.prompt_sha256 != identity.prompt_sha256 or fingerprint_step2_prompt(prompt) != identity.prompt_sha256:
        mismatched.append("prompt_sha256")
    if (
        envelope.response_schema_sha256 != identity.response_schema_sha256
        or canonical_step2_sha256(dict(response_schema)) != identity.response_schema_sha256
    ):
        mismatched.append("response_schema_sha256")
    if envelope.projection_sha256 != identity.projection_sha256:
        mismatched.append("projection_sha256")
    if mismatched:
        raise AgentStep2ResponseBindingError(
            "agent Step 2 response is not bound to this request: " + ", ".join(mismatched)
        )


class AgentFileStep2Transport:
    """``Step2Transport`` implementation backed by agent-written response files."""

    network_call_count: Final = 0

    def __init__(self, *, response_root: Path) -> None:
        self.response_root = Path(response_root)

    def complete(
        self,
        *,
        request: Step2ARequest | Step2BRequest,
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion:
        del use_idempotency_key  # no provider: idempotency is the request id itself
        path = agent_step2_response_path(self.response_root, request.identity.request_id)
        envelope, envelope_sha256 = load_agent_step2_response(path)
        verify_agent_step2_response_binding(
            envelope,
            request=request,
            response_schema=response_schema,
            prompt=prompt,
        )
        return TransportCompletion(
            content=json.dumps(envelope.payload, ensure_ascii=False, sort_keys=True),
            provider_request_id=f"{AGENT_STEP2_MODEL_PREFIX}{envelope_sha256[:32]}",
            http_status=None,
            headers={"x-examcard-transport": AGENT_STEP2_TRANSPORT_ID},
            finish_reason="stop",
        )
