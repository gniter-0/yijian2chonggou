"""Production-safe composition of credential sources and provider clients.

This module is the non-authoritative security boundary between a caller-owned
credential source and the existing object-storage delivery seam.  It accepts
only secret-free backend configuration, resolves one opaque credential lease,
and materializes an already-configured provider client.  Delivery operations
remain outside this module.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn, SupportsIndex, TypeAlias

from ..config.delivery_backend_config_v1 import (
    DeliveryBackendConfigV1,
    DeliveryBackendType,
)
from ..delivery.object_storage_v1 import ObjectStorageProvider
from .credential_provider_v1 import CredentialProviderError
from .credential_source_v1 import (
    CredentialLeaseV1,
    CredentialSourceV1,
    materialize_credential_lease_v1,
)

_MISSING = object()

CredentialClientFactoryV1: TypeAlias = Callable[[CredentialLeaseV1], ObjectStorageProvider]


def _source_provider_mismatch(source: object, expected: str) -> bool:
    """Reject an explicitly declared source/provider mismatch safely."""

    try:
        declared = getattr(source, "provider_type", _MISSING)
    except Exception:
        return True
    if declared is _MISSING:
        return False
    return not isinstance(declared, str) or declared != expected


class ProductionCredentialProviderV1:
    """Resolve an opaque provider client from an injected credential source.

    ``provider_type`` is a non-secret binding supplied by the deployment
    composition.  The source remains caller-owned; this class does not cache a
    lease or provider client, allowing the source to handle rotation on a new
    resolution.  It has no delivery, retry, receipt, identity, checkpoint, or
    persistence responsibilities.
    """

    __slots__ = ("__source", "__provider_type", "__client_factory")

    def __init__(
        self,
        *,
        source: CredentialSourceV1,
        provider_type: str,
        client_factory: CredentialClientFactoryV1 | None = None,
    ) -> None:
        source_valid = False
        try:
            source_valid = callable(getattr(source, "resolve", None))
        except Exception:
            source_valid = False
        if not source_valid:
            raise TypeError("credential source must provide resolve()")
        if not isinstance(provider_type, str) or not provider_type.strip():
            raise ValueError("provider_type must be a non-empty string")
        if client_factory is not None and not callable(client_factory):
            raise TypeError("credential client factory must be callable")
        object.__setattr__(self, "_ProductionCredentialProviderV1__source", source)
        object.__setattr__(self, "_ProductionCredentialProviderV1__provider_type", provider_type.strip())
        object.__setattr__(
            self,
            "_ProductionCredentialProviderV1__client_factory",
            client_factory or materialize_credential_lease_v1,
        )

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise AttributeError("credential providers are immutable")

    @property
    def provider_type(self) -> str:
        """Return the non-secret provider binding used for fail-closed checks."""

        return object.__getattribute__(self, "_ProductionCredentialProviderV1__provider_type")

    def __repr__(self) -> str:
        return "<production-credential-provider-v1>"

    def __str__(self) -> str:
        return "<production-credential-provider-v1>"

    def __getstate__(self) -> NoReturn:
        raise TypeError("production credential providers cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("production credential providers cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("production credential providers cannot be serialized")

    def resolve(self, *, config: DeliveryBackendConfigV1) -> ObjectStorageProvider:
        """Return one configured provider client without invoking delivery."""

        if not isinstance(config, DeliveryBackendConfigV1):
            raise CredentialProviderError("resolution_failed")
        if config.backend_type is not DeliveryBackendType.OBJECT_STORAGE:
            raise CredentialProviderError("unsupported_backend")
        if config.provider_type != self.provider_type:
            raise CredentialProviderError("resolution_failed")

        source = object.__getattribute__(self, "_ProductionCredentialProviderV1__source")
        if _source_provider_mismatch(source, self.provider_type):
            raise CredentialProviderError("resolution_failed")
        failed = False
        lease: CredentialLeaseV1 | None = None
        try:
            resolved = source.resolve(config=config)
            if isinstance(resolved, CredentialLeaseV1):
                lease = resolved
            else:
                failed = True
        except Exception:
            failed = True
        if failed or lease is None:
            raise CredentialProviderError("resolution_failed")

        failed = False
        client: ObjectStorageProvider | None = None
        client_factory = object.__getattribute__(self, "_ProductionCredentialProviderV1__client_factory")
        try:
            client = client_factory(lease)
        except Exception:
            failed = True
        client_valid = False
        if not failed and client is not None:
            try:
                client_valid = callable(getattr(client, "upload", None))
            except Exception:
                client_valid = False
        if failed or client is None or not client_valid:
            raise CredentialProviderError("resolution_failed")
        return client


CredentialProviderProductionV1 = ProductionCredentialProviderV1


__all__ = (
    "CredentialClientFactoryV1",
    "CredentialProviderProductionV1",
    "ProductionCredentialProviderV1",
)
