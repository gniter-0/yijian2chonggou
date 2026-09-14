"""Non-authoritative credential injection boundary for object storage.

Credential loading belongs to the caller-owned runtime environment.  This
module accepts a credential provider and a secret-free configuration, then
returns an opaque, already-configured provider client.  It does not load or
persist credentials itself and has no delivery, identity, receipt, retry, or
checkpoint responsibilities.
"""
from __future__ import annotations

from typing import Final, Literal, NoReturn, Protocol, SupportsIndex, TypeAlias, runtime_checkable

from ..config.delivery_backend_config_v1 import (
    DeliveryBackendConfigV1,
    DeliveryBackendType,
)
from ..delivery.object_storage_v1 import (
    ObjectStorageProvider,
    ObjectStorageProviderResponse,
)

CredentialProviderErrorCode: TypeAlias = Literal[
    "unsupported_backend",
    "resolution_failed",
    "invalid_provider_client",
    "provider_operation_failed",
]

_ERROR_MESSAGES: Final[dict[CredentialProviderErrorCode, str]] = {
    "unsupported_backend": "credential provider requires object-storage-v1 configuration",
    "resolution_failed": "credential provider could not resolve a provider client",
    "invalid_provider_client": "credential provider returned an invalid provider client",
    "provider_operation_failed": "opaque provider operation failed",
}


class CredentialProviderError(RuntimeError):
    """Safe, fixed-text error raised at the credential boundary.

    Provider exception text is intentionally never accepted as an argument.
    Unknown runtime values are mapped to ``resolution_failed`` so even a
    misbehaving caller cannot put arbitrary data into this exception.
    """

    code: CredentialProviderErrorCode

    def __init__(self, code: CredentialProviderErrorCode) -> None:
        safe_code: CredentialProviderErrorCode
        if isinstance(code, str) and code in _ERROR_MESSAGES:
            safe_code = code
        else:
            safe_code = "resolution_failed"
        self.code = safe_code
        super().__init__(_ERROR_MESSAGES[safe_code])

    def __repr__(self) -> str:
        return f"CredentialProviderError(code={self.code!r})"


@runtime_checkable
class CredentialProvider(Protocol):
    """Resolve an already-configured provider client from safe config.

    Implementations may obtain credentials from an environment, secret
    manager, or injected runtime context.  Raw credentials are not part of
    this protocol's input or output.
    """

    def resolve(self, *, config: DeliveryBackendConfigV1) -> ObjectStorageProvider:
        """Return a configured object-storage provider client."""


CredentialProviderV1 = CredentialProvider


class _OpaqueProviderClientBase:
    """Delegate only the provider operations needed by the delivery adapter.

    The wrapped client is kept in slots and is deliberately not exposed via
    attribute forwarding.  Fixed ``repr``/``str`` output and rejected pickle
    hooks prevent accidental logging or persistence of a client that may hold
    credential material.
    """

    __slots__ = ("__client",)

    def __init__(self, client: ObjectStorageProvider) -> None:
        object.__setattr__(self, "_OpaqueProviderClientBase__client", client)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("opaque provider clients are immutable")

    def _upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> ObjectStorageProviderResponse:
        failed = False
        response: ObjectStorageProviderResponse = None
        try:
            response = self._get_client().upload(
                package_sha256=package_sha256,
                target_sha256=target_sha256,
                idempotency_key=idempotency_key,
            )
        except Exception:
            failed = True
        if failed:
            raise CredentialProviderError("provider_operation_failed")
        return response

    def _get_client(self) -> ObjectStorageProvider:
        return object.__getattribute__(self, "_OpaqueProviderClientBase__client")

    def __repr__(self) -> str:
        return "<opaque-provider-client>"

    def __str__(self) -> str:
        return "<opaque-provider-client>"

    def __getstate__(self) -> object:
        raise TypeError("opaque provider clients cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("opaque provider clients cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("opaque provider clients cannot be serialized")


class _OpaqueProviderClientWithLookup(_OpaqueProviderClientBase):
    __slots__ = ()

    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> ObjectStorageProviderResponse:
        return self._upload(
            package_sha256=package_sha256,
            target_sha256=target_sha256,
            idempotency_key=idempotency_key,
        )

    def lookup(self, *, idempotency_key: str) -> ObjectStorageProviderResponse:
        failed = False
        response: ObjectStorageProviderResponse = None
        try:
            response = getattr(self._get_client(), "lookup")(idempotency_key=idempotency_key)
        except Exception:
            failed = True
        if failed:
            raise CredentialProviderError("provider_operation_failed")
        return response

class _OpaqueProviderClientWithoutLookup(_OpaqueProviderClientBase):
    __slots__ = ()

    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> ObjectStorageProviderResponse:
        return self._upload(
            package_sha256=package_sha256,
            target_sha256=target_sha256,
            idempotency_key=idempotency_key,
        )


def resolve_provider_client(
    *,
    credential_provider: CredentialProvider,
    config: DeliveryBackendConfigV1,
) -> ObjectStorageProvider:
    """Resolve and wrap one configured object-storage provider client.

    Resolution performs no provider operation.  Provider failures and
    malformed clients are converted to fixed-text errors without exception
    chaining.  Lookup is exposed only when the resolved client has a callable
    lookup method; otherwise the returned object has no lookup surface.
    """

    if not isinstance(config, DeliveryBackendConfigV1):
        raise CredentialProviderError("resolution_failed")
    if config.backend_type is not DeliveryBackendType.OBJECT_STORAGE:
        raise CredentialProviderError("unsupported_backend")

    failed = False
    client: ObjectStorageProvider | None = None
    try:
        resolver = getattr(credential_provider, "resolve", None)
        if not callable(resolver):
            failed = True
        else:
            client = resolver(config=config)
    except Exception:
        failed = True

    if failed:
        raise CredentialProviderError("resolution_failed")
    if client is None:
        raise CredentialProviderError("invalid_provider_client")

    try:
        upload = getattr(client, "upload", None)
    except Exception:
        upload = None
    if not callable(upload):
        raise CredentialProviderError("invalid_provider_client")

    try:
        lookup = getattr(client, "lookup", None)
    except Exception:
        lookup = None
    if callable(lookup):
        return _OpaqueProviderClientWithLookup(client)
    return _OpaqueProviderClientWithoutLookup(client)


__all__ = (
    "CredentialProvider",
    "CredentialProviderError",
    "CredentialProviderErrorCode",
    "CredentialProviderV1",
    "resolve_provider_client",
)
