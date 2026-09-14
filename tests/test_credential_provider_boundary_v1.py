"""Offline tests for the non-authoritative credential provider boundary."""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from examcard.config.delivery_backend_config_v1 import DeliveryBackendConfigV1
from examcard.security.credential_provider_v1 import (
    CredentialProviderError,
    resolve_provider_client,
)


SECRET = "synthetic-credential-value-must-not-escape"


@dataclass
class _ConfiguredClient:
    credential: str = SECRET
    upload_calls: list[tuple[str, str, str]] = field(default_factory=list)
    lookup_calls: list[str] = field(default_factory=list)

    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        self.upload_calls.append((package_sha256, target_sha256, idempotency_key))
        return {"status": "success", "remote_reference": "object-1"}

    def lookup(self, *, idempotency_key: str) -> dict[str, str]:
        self.lookup_calls.append(idempotency_key)
        return {"status": "success", "remote_reference": "object-1"}


@dataclass
class _CredentialProvider:
    client: object | None = field(default_factory=_ConfiguredClient)
    error: BaseException | None = None
    configs: list[DeliveryBackendConfigV1] = field(default_factory=list)

    def resolve(self, *, config: DeliveryBackendConfigV1) -> object:
        self.configs.append(config)
        if self.error is not None:
            raise self.error
        return self.client


class _UploadOnlyClient:
    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        del package_sha256, target_sha256, idempotency_key
        return {"status": "success"}


def _config() -> DeliveryBackendConfigV1:
    return DeliveryBackendConfigV1.object_storage(
        provider_type="fake-object-store",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
        namespace="test",
        region="local",
    )


def test_fake_provider_injection_returns_configured_client_surface() -> None:
    provider = _CredentialProvider()

    resolved = resolve_provider_client(credential_provider=provider, config=_config())

    assert callable(getattr(resolved, "upload", None))
    assert callable(getattr(resolved, "lookup", None))
    assert provider.configs == [_config()]


def test_resolution_does_not_perform_delivery() -> None:
    client = _ConfiguredClient()
    resolve_provider_client(credential_provider=_CredentialProvider(client=client), config=_config())

    assert client.upload_calls == []
    assert client.lookup_calls == []


def test_resolved_client_preserves_provider_operations_without_authority_methods() -> None:
    client = _ConfiguredClient()
    resolved = resolve_provider_client(credential_provider=_CredentialProvider(client=client), config=_config())
    key = "a" * 64

    assert resolved.upload(package_sha256="b" * 64, target_sha256="c" * 64, idempotency_key=key) == {
        "status": "success",
        "remote_reference": "object-1",
    }
    assert resolved.lookup(idempotency_key=key) == {  # type: ignore[attr-defined]
        "status": "success",
        "remote_reference": "object-1",
    }
    assert client.upload_calls == [("b" * 64, "c" * 64, key)]
    assert client.lookup_calls == [key]
    for method in (
        "create_receipt",
        "create_identity",
        "retry",
        "set_checkpoint",
        "decide_delivery",
    ):
        assert not hasattr(resolved, method)


def test_missing_credentials_raise_a_redacted_fixed_error() -> None:
    provider = _CredentialProvider(error=RuntimeError(SECRET))

    with pytest.raises(CredentialProviderError) as raised:
        resolve_provider_client(credential_provider=provider, config=_config())

    assert raised.value.code == "resolution_failed"
    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_invalid_provider_client_is_rejected_without_rendering_its_value() -> None:
    class _InvalidClient:
        def __repr__(self) -> str:
            return f"invalid({SECRET})"

    with pytest.raises(CredentialProviderError) as raised:
        resolve_provider_client(
            credential_provider=_CredentialProvider(client=_InvalidClient()),
            config=_config(),
        )

    assert raised.value.code == "invalid_provider_client"
    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)


def test_lookupless_client_preserves_unsupported_lookup_semantics() -> None:
    resolved = resolve_provider_client(
        credential_provider=_CredentialProvider(client=_UploadOnlyClient()),
        config=_config(),
    )

    assert callable(getattr(resolved, "upload", None))
    assert not callable(getattr(resolved, "lookup", None))


def test_opaque_client_repr_and_str_never_include_client_secrets() -> None:
    client = _ConfiguredClient()
    resolved = resolve_provider_client(credential_provider=_CredentialProvider(client=client), config=_config())

    assert SECRET not in repr(resolved)
    assert SECRET not in str(resolved)
    assert repr(resolved) == "<opaque-provider-client>"
    assert str(resolved) == "<opaque-provider-client>"


def test_opaque_client_rejects_serialization_and_has_no_serialization_helper() -> None:
    resolved = resolve_provider_client(
        credential_provider=_CredentialProvider(client=_ConfiguredClient()),
        config=_config(),
    )

    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(resolved)
    for helper in ("to_dict", "serialize", "model_dump", "model_dump_json"):
        assert not hasattr(resolved, helper)


def test_non_object_storage_config_is_rejected_before_provider_resolution() -> None:
    provider = _CredentialProvider()

    with pytest.raises(CredentialProviderError) as raised:
        resolve_provider_client(
            credential_provider=provider,
            config=DeliveryBackendConfigV1.filesystem("destination"),
        )

    assert raised.value.code == "unsupported_backend"
    assert provider.configs == []


def test_boundary_does_not_create_persistent_state() -> None:
    before = sorted(path.name for path in Path.cwd().iterdir())
    resolve_provider_client(credential_provider=_CredentialProvider(), config=_config())

    assert sorted(path.name for path in Path.cwd().iterdir()) == before
