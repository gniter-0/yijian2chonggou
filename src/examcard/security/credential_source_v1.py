"""Production-safe credential source seams for object-storage providers.

Sources may obtain credential material, but the material is kept inside an
opaque, runtime-only lease.  Callers receive neither raw values nor a public
accessor for them.  Provider-client construction is deliberately represented
by a callback held by the lease and is performed only inside the credential
boundary.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final, Literal, NoReturn, Protocol, SupportsIndex, TypeAlias, runtime_checkable

from ..config.delivery_backend_config_v1 import (
    DeliveryBackendConfigV1,
    DeliveryBackendType,
)
from ..delivery.object_storage_v1 import ObjectStorageProvider

CredentialSourceErrorCode: TypeAlias = Literal[
    "unsupported_backend",
    "source_unavailable",
    "credentials_missing",
    "credentials_invalid",
    "invalid_lease",
]

_ERROR_MESSAGES: Final[dict[CredentialSourceErrorCode, str]] = {
    "unsupported_backend": "credential source requires object-storage-v1 configuration",
    "source_unavailable": "credential source is unavailable",
    "credentials_missing": "required object-storage credentials are unavailable",
    "credentials_invalid": "object-storage credentials are invalid",
    "invalid_lease": "credential source returned an invalid lease",
}


class CredentialSourceError(RuntimeError):
    """Fixed-text source error that cannot render secret material."""

    code: CredentialSourceErrorCode

    def __init__(self, code: CredentialSourceErrorCode) -> None:
        safe_code: CredentialSourceErrorCode
        if isinstance(code, str) and code in _ERROR_MESSAGES:
            safe_code = code
        else:
            safe_code = "source_unavailable"
        self.code = safe_code
        super().__init__(_ERROR_MESSAGES[safe_code])

    def __repr__(self) -> str:
        return f"CredentialSourceError(code={self.code!r})"


CredentialClientBuilderV1: TypeAlias = Callable[..., ObjectStorageProvider]


class CredentialLeaseV1:
    """Opaque, runtime-only lease for provider-client construction.

    The lease stores only a private materializer callback.  A caller can hand
    the lease to the credential-layer client factory, but cannot inspect or
    serialize the values captured by that callback through this object.
    """

    __slots__ = ("__materializer", "__source_type", "__expires_at")

    def __init__(
        self,
        *,
        source_type: str,
        materializer: Callable[[], ObjectStorageProvider],
        expires_at: str | None = None,
    ) -> None:
        if not isinstance(source_type, str) or not source_type.strip():
            raise CredentialSourceError("invalid_lease")
        if not callable(materializer):
            raise CredentialSourceError("invalid_lease")
        if expires_at is not None and (not isinstance(expires_at, str) or not expires_at.strip()):
            raise CredentialSourceError("invalid_lease")
        object.__setattr__(self, "_CredentialLeaseV1__source_type", source_type.strip())
        object.__setattr__(self, "_CredentialLeaseV1__materializer", materializer)
        object.__setattr__(self, "_CredentialLeaseV1__expires_at", expires_at.strip() if expires_at else None)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("credential leases are immutable")

    @property
    def source_type(self) -> str:
        """Return safe source metadata, never credential material."""

        return object.__getattribute__(self, "_CredentialLeaseV1__source_type")

    @property
    def expires_at(self) -> str | None:
        """Return optional non-secret lease metadata."""

        return object.__getattribute__(self, "_CredentialLeaseV1__expires_at")

    def __repr__(self) -> str:
        return "<opaque-credential-lease>"

    def __str__(self) -> str:
        return "<opaque-credential-lease>"

    def __getstate__(self) -> NoReturn:
        raise TypeError("opaque credential leases cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("opaque credential leases cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("opaque credential leases cannot be serialized")

    def _materialize(self) -> ObjectStorageProvider:
        """Materialize once through the credential-layer factory seam."""

        materializer = object.__getattribute__(self, "_CredentialLeaseV1__materializer")
        if materializer is None:
            raise CredentialSourceError("invalid_lease")
        object.__setattr__(self, "_CredentialLeaseV1__materializer", None)
        return materializer()


@runtime_checkable
class CredentialSourceV1(Protocol):
    """Resolve an opaque lease without exposing raw credentials."""

    source_type: str

    def resolve(self, *, config: DeliveryBackendConfigV1) -> CredentialLeaseV1:
        """Return a runtime-only lease for the validated configuration."""


def _require_object_storage_config(config: DeliveryBackendConfigV1) -> None:
    if not isinstance(config, DeliveryBackendConfigV1):
        raise CredentialSourceError("unsupported_backend")
    if config.backend_type is not DeliveryBackendType.OBJECT_STORAGE:
        raise CredentialSourceError("unsupported_backend")


def _has_upload_surface(client: object) -> bool:
    try:
        return callable(getattr(client, "upload", None))
    except Exception:
        return False


class _OpaqueCredentialSourceBase:
    """Shared safe representation and serialization behavior for sources."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<credential-source>"

    def __str__(self) -> str:
        return "<credential-source>"

    def __getstate__(self) -> NoReturn:
        raise TypeError("credential sources cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("credential sources cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        del protocol
        raise TypeError("credential sources cannot be serialized")


class InjectedDeploymentCredentialSourceV1(_OpaqueCredentialSourceBase):
    """Source backed by a deployment-injected client builder."""

    __slots__ = ("__client_builder",)
    source_type = "injected-deployment"

    def __init__(self, *, client_builder: CredentialClientBuilderV1) -> None:
        if not callable(client_builder):
            raise TypeError("injected credential client builder must be callable")
        self.__client_builder = client_builder

    def resolve(self, *, config: DeliveryBackendConfigV1) -> CredentialLeaseV1:
        _require_object_storage_config(config)
        client_builder = self.__client_builder
        return CredentialLeaseV1(
            source_type=self.source_type,
            materializer=lambda: client_builder(config=config),
        )


class EnvironmentCredentialSourceV1(_OpaqueCredentialSourceBase):
    """Environment-backed source with an injected provider-client builder.

    The environment mapping is always injected by the caller.  Environment
    values are captured only by the private lease materializer.  They are
    never returned by ``resolve`` or included in source/lease output.
    """

    __slots__ = ("__client_builder", "__variable_names", "__environ")
    source_type = "environment"

    def __init__(
        self,
        *,
        client_builder: CredentialClientBuilderV1,
        variable_names: Mapping[str, str],
        environ: Mapping[str, str],
    ) -> None:
        if not callable(client_builder):
            raise TypeError("environment credential client builder must be callable")
        normalized_names: dict[str, str] = {}
        for logical_name, variable_name in variable_names.items():
            if not isinstance(logical_name, str) or not logical_name.strip():
                raise ValueError("credential logical names must be non-empty strings")
            if not isinstance(variable_name, str) or not variable_name.strip():
                raise ValueError("credential environment variable names must be non-empty strings")
            normalized_names[logical_name.strip()] = variable_name.strip()
        if not normalized_names:
            raise ValueError("at least one credential environment variable is required")
        self.__client_builder = client_builder
        self.__variable_names = normalized_names
        self.__environ = environ

    def resolve(self, *, config: DeliveryBackendConfigV1) -> CredentialLeaseV1:
        _require_object_storage_config(config)
        values: dict[str, str] = {}
        missing = False
        failed = False
        try:
            for logical_name, variable_name in self.__variable_names.items():
                value = self.__environ.get(variable_name)
                if not isinstance(value, str) or not value:
                    missing = True
                else:
                    values[logical_name] = value
        except Exception:
            failed = True
        if failed:
            raise CredentialSourceError("source_unavailable")
        if missing:
            raise CredentialSourceError("credentials_missing")
        client_builder = self.__client_builder
        return CredentialLeaseV1(
            source_type=self.source_type,
            materializer=lambda: client_builder(config=config, credential_values=values),
        )


@runtime_checkable
class SecretManagerReaderV1(Protocol):
    """SDK-neutral seam for a caller-injected secret manager reader."""

    def read(
        self,
        *,
        secret_reference: str,
        provider_type: str,
    ) -> Mapping[str, str]:
        """Return secret values to the source boundary only."""


class ExternalSecretManagerCredentialSourceV1(_OpaqueCredentialSourceBase):
    """External secret-manager adapter without a vendor SDK dependency."""

    __slots__ = ("__reader", "__secret_reference", "__client_builder")
    source_type = "external-secret-manager"

    def __init__(
        self,
        *,
        reader: SecretManagerReaderV1,
        secret_reference: str,
        client_builder: CredentialClientBuilderV1,
    ) -> None:
        if not callable(getattr(reader, "read", None)):
            raise TypeError("secret manager reader must provide read()")
        if not isinstance(secret_reference, str) or not secret_reference.strip():
            raise ValueError("secret reference must be a non-empty string")
        if not callable(client_builder):
            raise TypeError("secret manager credential client builder must be callable")
        self.__reader = reader
        self.__secret_reference = secret_reference.strip()
        self.__client_builder = client_builder

    def resolve(self, *, config: DeliveryBackendConfigV1) -> CredentialLeaseV1:
        _require_object_storage_config(config)
        failed = False
        values: Mapping[str, str] | None = None
        try:
            values = self.__reader.read(
                secret_reference=self.__secret_reference,
                provider_type=str(config.provider_type),
            )
        except Exception:
            failed = True
        if failed:
            raise CredentialSourceError("source_unavailable")
        if values is None or not isinstance(values, Mapping):
            raise CredentialSourceError("credentials_invalid")
        invalid = False
        try:
            invalid = not values or any(
                not isinstance(key, str) or not isinstance(value, str) or not value
                for key, value in values.items()
            )
        except Exception:
            invalid = True
        if invalid:
            raise CredentialSourceError("credentials_invalid")
        client_builder = self.__client_builder
        secret_values = dict(values)
        return CredentialLeaseV1(
            source_type=self.source_type,
            materializer=lambda: client_builder(config=config, credential_values=secret_values),
        )


def materialize_credential_lease_v1(lease: CredentialLeaseV1) -> ObjectStorageProvider:
    """Materialize a lease inside the security boundary.

    This is intentionally the only exported operation that consumes a lease;
    it returns a provider client and never returns the captured credential
    values.
    """

    if not isinstance(lease, CredentialLeaseV1):
        raise CredentialSourceError("invalid_lease")
    failed = False
    client: ObjectStorageProvider | None = None
    try:
        client = lease._materialize()
    except Exception:
        failed = True
    if failed or client is None or not _has_upload_surface(client):
        raise CredentialSourceError("credentials_invalid")
    return client


__all__ = (
    "CredentialClientBuilderV1",
    "CredentialLeaseV1",
    "CredentialSourceError",
    "CredentialSourceErrorCode",
    "CredentialSourceV1",
    "EnvironmentCredentialSourceV1",
    "ExternalSecretManagerCredentialSourceV1",
    "InjectedDeploymentCredentialSourceV1",
    "SecretManagerReaderV1",
    "materialize_credential_lease_v1",
)
