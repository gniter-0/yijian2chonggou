"""Fixture-only deterministic fake LLM (no network path)."""
from __future__ import annotations
from collections.abc import Mapping

from .enums import Step2FailureState, Step2ResultState, Step2Stage
from .request_identity import Step2RequestIdentity
from .responses import Step2AFailure, Step2AResponse
from .step2a_request import Step2ARequest
from .step2b import Step2BRequest, Step2BResponse


class DeterministicFakeLLM:
    """Fixture-only client with no network path and a cache-keyed response map."""

    def __init__(self, responses: Mapping[str, Step2AResponse | Step2BResponse]) -> None:
        self._responses = dict(responses)
        self.calls: list[str] = []

    def complete_step2a(self, request: Step2ARequest) -> Step2AResponse:
        response = self._complete(request.identity)
        if not isinstance(response, Step2AResponse):
            raise TypeError("deterministic fake response type does not match Step 2A")
        return response

    def complete_step2b(self, request: Step2BRequest) -> Step2BResponse:
        response = self._complete(request.identity)
        if not isinstance(response, Step2BResponse):
            raise TypeError("deterministic fake response type does not match Step 2B")
        return response

    def _complete(self, identity: Step2RequestIdentity) -> Step2AResponse | Step2BResponse:
        self.calls.append(identity.cache_key)
        response = self._responses.get(identity.cache_key)
        if response is None:
            failure = Step2AFailure(state=Step2FailureState.SOURCE_MISSING, message="fixture response is not configured")
            if identity.stage == Step2Stage.FACT_PLANNING:
                return Step2AResponse(request=identity, state=Step2ResultState.FAILED, failure=failure)
            return Step2BResponse(request=identity, state=Step2ResultState.FAILED, failure=failure)
        return response
