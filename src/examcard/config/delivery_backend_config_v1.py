"""Typed, secret-free operator configuration for delivery backend assembly.

This module validates non-secret input only.  It does not load credentials,
construct delivery identities, create receipts, persist state, or decide a
delivery outcome.  Runtime provider clients and their credentials belong to a
later assembly boundary and are intentionally not representable here.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator, model_validator

from ..common.hashing import canonical_json as _canonical_json
from ..common.hashing import sha256_bytes


class DeliveryBackendType(str, Enum):
    """Closed set of backend identifiers understood by the v1 boundary."""

    FILESYSTEM = "filesystem-directory-v1"
    OBJECT_STORAGE = "object-storage-v1"


BackendType = DeliveryBackendType
BACKEND_TYPES: Final[tuple[str, ...]] = tuple(item.value for item in DeliveryBackendType)

# These names are documented as forbidden so callers can fail closed with a
# useful message before attempting to place a secret in an operator config.
# Unknown fields are rejected by ``extra='forbid'`` as well.
FORBIDDEN_SECRET_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "access_key",
        "access_key_id",
        "api_key",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "secret",
        "secret_key",
        "token",
    }
)

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_QUERY_PATTERN = re.compile(
    r"(?:access[_-]?key|api[_-]?key|authorization|credential|password|secret|signature|token)\s*=",
    re.IGNORECASE,
)


class DeliveryBackendConfigV1(BaseModel):
    """Immutable non-secret configuration consumed by a future P8 assembly.

    ``extra='forbid'`` is deliberate: credentials, authority fields, and
    future fields must not be silently accepted and accidentally persisted.
    Backend-specific fields are validated as an explicit closed shape.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    backend_type: DeliveryBackendType
    destination_root: Path | None = None
    provider_type: StrictStr | None = None
    endpoint: StrictStr | None = None
    bucket: StrictStr | None = None
    namespace: StrictStr | None = None
    region: StrictStr | None = None

    _CANONICAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "backend_type",
        "destination_root",
        "provider_type",
        "endpoint",
        "bucket",
        "namespace",
        "region",
    )

    @field_validator("destination_root", mode="before")
    @classmethod
    def _validate_destination_root(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, (str, Path)):
            raise TypeError("destination_root must be a path or string")
        if isinstance(value, str) and not value.strip():
            raise ValueError("destination_root must not be blank")
        return value

    @field_validator("provider_type", "endpoint", "bucket", "namespace", "region", mode="before")
    @classmethod
    def _validate_non_secret_text(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("non-secret backend configuration fields must be strings")
        normalized = value.strip()
        if not normalized:
            raise ValueError("non-secret backend configuration fields must not be blank")
        if _CONTROL_CHARACTER_PATTERN.search(normalized):
            raise ValueError("non-secret backend configuration fields must not contain control characters")
        return normalized

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Endpoint is an opaque provider/destination reference, not a URL
        # carrying credentials or signed query material.
        if any(character in value for character in ("?", "#", "@")) or _SECRET_QUERY_PATTERN.search(value):
            raise ValueError("endpoint must be a non-secret reference without credential material")
        return value

    @model_validator(mode="after")
    def _validate_backend_shape(self) -> "DeliveryBackendConfigV1":
        object_storage_fields = {
            "provider_type": self.provider_type,
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "namespace": self.namespace,
            "region": self.region,
        }
        if self.backend_type is DeliveryBackendType.FILESYSTEM:
            if self.destination_root is None:
                raise ValueError("filesystem-directory-v1 requires destination_root")
            present = tuple(name for name, value in object_storage_fields.items() if value is not None)
            if present:
                raise ValueError(
                    "filesystem-directory-v1 does not accept object-storage fields: " + ", ".join(present)
                )
            return self

        if self.destination_root is not None:
            raise ValueError("object-storage-v1 does not accept destination_root")
        missing = tuple(name for name in ("provider_type", "endpoint", "bucket") if object_storage_fields[name] is None)
        if missing:
            raise ValueError("object-storage-v1 requires: " + ", ".join(missing))
        return self

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeliveryBackendConfigV1":
        """Validate a caller-owned mapping without modifying it."""

        if not isinstance(value, Mapping):
            raise TypeError("delivery backend configuration must be a mapping")
        return cls.model_validate(dict(value))

    @classmethod
    def filesystem(cls, destination_root: str | Path) -> "DeliveryBackendConfigV1":
        """Build a validated filesystem configuration."""

        return cls(backend_type=DeliveryBackendType.FILESYSTEM, destination_root=destination_root)

    @classmethod
    def object_storage(
        cls,
        *,
        provider_type: str,
        endpoint: str,
        bucket: str,
        namespace: str | None = None,
        region: str | None = None,
    ) -> "DeliveryBackendConfigV1":
        """Build a validated object-storage configuration without credentials."""

        return cls(
            backend_type=DeliveryBackendType.OBJECT_STORAGE,
            provider_type=provider_type,
            endpoint=endpoint,
            bucket=bucket,
            namespace=namespace,
            region=region,
        )

    def non_secret_dict(self) -> dict[str, object]:
        """Return a new persisted-safe projection containing no runtime data."""

        dumped = self.model_dump(mode="json", exclude_none=False)
        return {field: dumped[field] for field in self._CANONICAL_FIELDS}

    def canonical_non_secret(self) -> dict[str, object]:
        """Return the deterministic canonical non-secret projection."""

        return self.non_secret_dict()

    def canonical_json(self) -> bytes:
        """Serialize the non-secret projection with the repository JSON rules."""

        return _canonical_json(self.canonical_non_secret())

    def canonical_sha256(self) -> str:
        """Return the digest of canonical non-secret configuration bytes."""

        return sha256_bytes(self.canonical_json())


BackendConfigurationV1 = DeliveryBackendConfigV1


__all__ = (
    "BACKEND_TYPES",
    "FORBIDDEN_SECRET_FIELDS",
    "BackendConfigurationV1",
    "BackendType",
    "DeliveryBackendConfigV1",
    "DeliveryBackendType",
)
