"""Provider-neutral object-storage adapter for the frozen P6 delivery seam.

The adapter accepts an already-configured provider client.  It performs one
provider upload or lookup call and normalizes the observation to the existing
``DeliveryAdapterResult`` contract.  P6 remains responsible for identity,
idempotency, retries, receipts, and recovery; this module has none of those
authority responsibilities.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol, TypeAlias

from ..gates.release.delivery_v2 import (
    DeliveryAdapterResult,
    DeliveryAdapterStatus,
)

ObjectStorageProviderResponse: TypeAlias = DeliveryAdapterResult | Mapping[str, object] | None

_REMOTE_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}\Z")
_FORBIDDEN_REFERENCE_PARTS = re.compile(
    r"(?:access[_-]?key|api[_-]?key|authorization|credential|password|secret|signature|token)"
    r"|[?&#@]",
    re.IGNORECASE,
)


class ObjectStorageProvider(Protocol):
    """Minimal injected provider surface; lookup is intentionally optional."""

    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> ObjectStorageProviderResponse:
        """Upload one package using the caller-supplied frozen identities."""


def sanitize_remote_reference(value: object) -> str | None:
    """Return only a bounded opaque reference with no credential-like data."""

    if not isinstance(value, str):
        return None
    reference = value.strip()
    if not _REMOTE_REFERENCE_PATTERN.fullmatch(reference):
        return None
    if _FORBIDDEN_REFERENCE_PARTS.search(reference):
        return None
    return reference


def normalize_provider_status(value: object) -> DeliveryAdapterStatus:
    """Map provider status vocabulary to the frozen P6 result vocabulary."""

    raw_status: object
    if isinstance(value, DeliveryAdapterResult):
        raw_status = value.status
    elif isinstance(value, Mapping):
        raw_status = value.get("status")
    elif isinstance(value, str):
        raw_status = value
    else:
        raw_status = None

    if not isinstance(raw_status, str):
        return "unknown"
    status = raw_status.strip().lower().replace("-", "_").replace(" ", "_")
    if status in {"success", "succeeded", "completed", "complete", "uploaded", "ok"}:
        return "success"
    if status in {"failure", "failed", "rejected", "denied", "invalid"}:
        return "failure"
    return "unknown"


def normalize_provider_response(value: object) -> DeliveryAdapterResult:
    """Normalize a provider response without exposing provider internals."""

    remote_reference: object = None
    if isinstance(value, DeliveryAdapterResult):
        remote_reference = value.remote_reference
    elif isinstance(value, Mapping):
        remote_reference = value.get("remote_reference")
    return DeliveryAdapterResult(
        status=normalize_provider_status(value),
        remote_reference=sanitize_remote_reference(remote_reference),
    )


def normalize_provider_exception(error: BaseException) -> DeliveryAdapterResult:
    """Fail closed on provider exceptions without returning their messages."""

    del error
    return DeliveryAdapterResult(status="unknown")


class ObjectStorageDeliveryAdapter:
    """One-call object-storage adapter below the P6 authority boundary."""

    def __init__(self, provider: ObjectStorageProvider) -> None:
        if not callable(getattr(provider, "upload", None)):
            raise TypeError("object-storage provider must provide upload()")
        self._provider = provider

    @property
    def supports_lookup(self) -> bool:
        """Whether the injected provider exposes same-key lookup."""

        return callable(getattr(self._provider, "lookup", None))

    def deliver(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> DeliveryAdapterResult:
        """Perform exactly one provider upload with unchanged P6 bindings."""

        try:
            response = self._provider.upload(
                package_sha256=package_sha256,
                target_sha256=target_sha256,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            return normalize_provider_exception(error)
        return normalize_provider_response(response)

    def lookup(self, *, idempotency_key: str) -> DeliveryAdapterResult | None:
        """Resolve the original key when the provider supports lookup."""

        method = getattr(self._provider, "lookup", None)
        if not callable(method):
            return None
        try:
            response = method(idempotency_key=idempotency_key)
        except Exception as error:
            return normalize_provider_exception(error)
        return normalize_provider_response(response)


__all__ = (
    "ObjectStorageProvider",
    "ObjectStorageProviderResponse",
    "ObjectStorageDeliveryAdapter",
    "normalize_provider_exception",
    "normalize_provider_response",
    "normalize_provider_status",
    "sanitize_remote_reference",
)
