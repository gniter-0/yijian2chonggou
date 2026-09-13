"""Explicit registry assembly for the provider-neutral object-storage adapter.

This module is deliberately below the P6 authority boundary.  It only binds
an already-configured provider client to the process-local backend registry.
P6 still owns delivery identity, idempotency, retries, receipts, and recovery.
"""
from __future__ import annotations

from typing import Final

from ..gates.release.delivery_v2 import DeliveryAdapter, DeliveryAdapterResult
from .backend_registry_v1 import (
    DeliveryBackendCapabilities,
    DeliveryBackendRegistration,
    DeliveryBackendRegistryV1,
)
from .object_storage_v1 import ObjectStorageDeliveryAdapter, ObjectStorageProvider

OBJECT_STORAGE_BACKEND_TYPE: Final = "object-storage-v1"


class _LookuplessObjectStorageAdapter:
    """Expose only ``deliver`` when the provider cannot perform lookup.

    P6 detects lookup support from the adapter surface.  Keeping ``lookup``
    absent here preserves the frozen P6 behavior for providers without a
    lookup operation while reusing the existing object-storage adapter.
    """

    def __init__(self, adapter: ObjectStorageDeliveryAdapter) -> None:
        self._adapter = adapter

    def deliver(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> DeliveryAdapterResult:
        return self._adapter.deliver(
            package_sha256=package_sha256,
            target_sha256=target_sha256,
            idempotency_key=idempotency_key,
        )


def register_object_storage_v1(
    registry: DeliveryBackendRegistryV1,
    *,
    provider: ObjectStorageProvider,
) -> DeliveryBackendRegistration:
    """Register one in-memory object-storage adapter factory.

    The provider is resolved and configured by the caller.  The registry
    retains only the factory closure for this process; it does not serialize
    provider configuration or credentials.  The same injected provider is
    used for every adapter created from this registration.
    """

    supports_lookup = callable(getattr(provider, "lookup", None))

    def factory() -> DeliveryAdapter:
        adapter = ObjectStorageDeliveryAdapter(provider)
        if supports_lookup:
            return adapter
        return _LookuplessObjectStorageAdapter(adapter)

    return registry.register(
        OBJECT_STORAGE_BACKEND_TYPE,
        factory,
        capabilities=DeliveryBackendCapabilities(supports_lookup=supports_lookup),
    )


__all__ = ("OBJECT_STORAGE_BACKEND_TYPE", "register_object_storage_v1")
