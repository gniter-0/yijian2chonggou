"""P8 runtime assembly for an injected object-storage credential boundary.

This module composes already-approved, non-authoritative pieces.  It validates
the non-secret backend configuration, resolves an opaque provider client, and
constructs the existing object-storage adapter through the existing registry.
It performs no provider operation and owns no delivery identity, receipt,
retry, recovery, checkpoint, or persistence behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, NoReturn, Protocol, SupportsIndex, TypeAlias

from ..config.delivery_backend_config_v1 import (
    DeliveryBackendConfigV1,
    DeliveryBackendType,
)
from ..delivery.backend_registry_v1 import DeliveryBackendRegistryV1
from ..delivery.object_storage_registry_v1 import (
    OBJECT_STORAGE_BACKEND_TYPE,
    register_object_storage_v1,
)
from ..gates.release.delivery_v2 import DeliveryAdapter
from ..security.credential_provider_v1 import (
    CredentialProvider,
    resolve_provider_client,
)

DeliveryRuntimeAssemblyErrorCode: TypeAlias = Literal[
    "invalid_configuration",
    "unsupported_backend",
    "provider_mismatch",
    "credential_resolution_failed",
    "adapter_assembly_failed",
]

_ERROR_MESSAGES: Final[dict[DeliveryRuntimeAssemblyErrorCode, str]] = {
    "invalid_configuration": "delivery runtime configuration is invalid",
    "unsupported_backend": "delivery runtime supports object-storage-v1 only",
    "provider_mismatch": "credential provider does not match object-storage configuration",
    "credential_resolution_failed": "delivery runtime could not resolve provider credentials",
    "adapter_assembly_failed": "delivery runtime could not assemble the adapter",
}
_MISSING = object()


class DeliveryRuntimeAssemblyError(RuntimeError):
    """Fixed-text assembly error that cannot echo provider internals."""

    code: DeliveryRuntimeAssemblyErrorCode

    def __init__(self, code: DeliveryRuntimeAssemblyErrorCode) -> None:
        if isinstance(code, str) and code in _ERROR_MESSAGES:
            safe_code = code
        else:
            safe_code = "adapter_assembly_failed"
        self.code = safe_code
        super().__init__(_ERROR_MESSAGES[safe_code])

    def __repr__(self) -> str:
        return f"DeliveryRuntimeAssemblyError(code={self.code!r})"


@dataclass(frozen=True, slots=True)
class DeliveryRuntimeMetadataV1:
    """Deterministic, non-secret metadata for an in-memory assembly."""

    backend_type: str
    provider_type: str
    configuration_sha256: str
    supports_lookup: bool

    def as_dict(self) -> dict[str, object]:
        """Return only safe metadata; the provider client is not included."""

        return {
            "backend_type": self.backend_type,
            "provider_type": self.provider_type,
            "configuration_sha256": self.configuration_sha256,
            "supports_lookup": self.supports_lookup,
        }


@dataclass(frozen=True, slots=True, repr=False)
class DeliveryRuntimeAssemblyV1:
    """An in-memory P8 composition result for the P6 adapter seam."""

    adapter: DeliveryAdapter
    metadata: DeliveryRuntimeMetadataV1

    @property
    def backend_type(self) -> str:
        return self.metadata.backend_type

    @property
    def supports_lookup(self) -> bool:
        return self.metadata.supports_lookup

    def __repr__(self) -> str:
        return "<delivery-runtime-assembly-v1>"

    def __str__(self) -> str:
        return "<delivery-runtime-assembly-v1>"

    def __getstate__(self) -> NoReturn:
        raise TypeError("delivery runtime assemblies cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("delivery runtime assemblies cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("delivery runtime assemblies cannot be serialized")


class _ProviderTypeMetadata(Protocol):
    provider_type: str


def _declared_provider_mismatch(provider: object, expected: str) -> bool:
    """Validate optional provider metadata without rendering provider values."""

    try:
        declared = getattr(provider, "provider_type", _MISSING)
    except Exception:
        return True
    if declared is _MISSING:
        return False
    return not isinstance(declared, str) or declared != expected


def assemble_object_storage_delivery_v1(
    *,
    config: DeliveryBackendConfigV1,
    credential_provider: CredentialProvider,
) -> DeliveryRuntimeAssemblyV1:
    """Assemble an object-storage adapter without invoking the provider.

    The caller supplies the credential provider explicitly.  Its optional
    ``provider_type`` metadata is checked when present; absence is supported
    for compatibility with the existing minimal ``CredentialProvider``
    protocol, whose binding is established by the caller's explicit
    injection.  No provider client or configuration is persisted.
    """

    if not isinstance(config, DeliveryBackendConfigV1):
        raise DeliveryRuntimeAssemblyError("invalid_configuration")
    if config.backend_type is not DeliveryBackendType.OBJECT_STORAGE:
        raise DeliveryRuntimeAssemblyError("unsupported_backend")
    if config.provider_type is None:
        raise DeliveryRuntimeAssemblyError("invalid_configuration")
    if _declared_provider_mismatch(credential_provider, config.provider_type):
        raise DeliveryRuntimeAssemblyError("provider_mismatch")

    credential_failed = False
    provider_client = None
    try:
        provider_client = resolve_provider_client(
            credential_provider=credential_provider,
            config=config,
        )
    except Exception:
        credential_failed = True
    if credential_failed or provider_client is None:
        raise DeliveryRuntimeAssemblyError("credential_resolution_failed")

    assembly_failed = False
    adapter: DeliveryAdapter | None = None
    supports_lookup = False
    try:
        registry = DeliveryBackendRegistryV1()
        registration = register_object_storage_v1(registry, provider=provider_client)
        adapter = registry.create(OBJECT_STORAGE_BACKEND_TYPE)
        supports_lookup = registration.capabilities.supports_lookup
    except Exception:
        assembly_failed = True
    if assembly_failed or adapter is None:
        raise DeliveryRuntimeAssemblyError("adapter_assembly_failed")

    metadata = DeliveryRuntimeMetadataV1(
        backend_type=OBJECT_STORAGE_BACKEND_TYPE,
        provider_type=config.provider_type,
        configuration_sha256=config.canonical_sha256(),
        supports_lookup=supports_lookup,
    )
    return DeliveryRuntimeAssemblyV1(adapter=adapter, metadata=metadata)


def assemble_object_storage_adapter_v1(
    *,
    config: DeliveryBackendConfigV1,
    credential_provider: CredentialProvider,
) -> DeliveryAdapter:
    """Return only the P6 adapter from the explicit runtime assembly."""

    return assemble_object_storage_delivery_v1(
        config=config,
        credential_provider=credential_provider,
    ).adapter


__all__ = (
    "DeliveryRuntimeAssemblyError",
    "DeliveryRuntimeAssemblyErrorCode",
    "DeliveryRuntimeAssemblyV1",
    "DeliveryRuntimeMetadataV1",
    "assemble_object_storage_adapter_v1",
    "assemble_object_storage_delivery_v1",
)
