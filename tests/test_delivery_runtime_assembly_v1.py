"""Offline tests for the isolated P8 object-storage runtime assembly."""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from examcard.config.delivery_backend_config_v1 import DeliveryBackendConfigV1
from examcard.delivery.object_storage_v1 import ObjectStorageProvider
from examcard.delivery.object_storage_v1 import ObjectStorageDeliveryAdapter
from examcard.pipeline.delivery_runtime_assembly_v1 import (
    DeliveryRuntimeAssemblyError,
    DeliveryRuntimeAssemblyV1,
    assemble_object_storage_adapter_v1,
    assemble_object_storage_delivery_v1,
)


SECRET = "synthetic-runtime-credential-must-not-escape"


@dataclass
class _FakeObjectStorageProvider:
    provider_type: str = "fake-object-store"
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


class _FakeUploadOnlyProvider:
    provider_type = "fake-object-store"

    def __init__(self) -> None:
        self.upload_calls: list[tuple[str, str, str]] = []

    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        self.upload_calls.append((package_sha256, target_sha256, idempotency_key))
        return {"status": "success"}


@dataclass
class _FakeCredentialProvider:
    client: ObjectStorageProvider = field(default_factory=_FakeObjectStorageProvider)
    provider_type: str = "fake-object-store"
    error: BaseException | None = None
    resolve_calls: list[DeliveryBackendConfigV1] = field(default_factory=list)

    def resolve(self, *, config: DeliveryBackendConfigV1) -> ObjectStorageProvider:
        self.resolve_calls.append(config)
        if self.error is not None:
            raise self.error
        return self.client


def _config(*, provider_type: str = "fake-object-store") -> DeliveryBackendConfigV1:
    return DeliveryBackendConfigV1.object_storage(
        provider_type=provider_type,
        endpoint="objects.example.test",
        bucket="exam-artifacts",
        namespace="tests",
        region="local",
    )


def test_successful_assembly_uses_injected_provider_and_registry() -> None:
    provider = _FakeCredentialProvider()

    assembly = assemble_object_storage_delivery_v1(config=_config(), credential_provider=provider)

    assert isinstance(assembly, DeliveryRuntimeAssemblyV1)
    assert isinstance(assembly.adapter, ObjectStorageDeliveryAdapter)
    assert assembly.metadata.backend_type == "object-storage-v1"
    assert assembly.metadata.provider_type == "fake-object-store"
    assert assembly.metadata.supports_lookup is True
    assert provider.resolve_calls == [_config()]


def test_assembly_metadata_is_deterministic_and_secret_free() -> None:
    first = assemble_object_storage_delivery_v1(
        config=_config(), credential_provider=_FakeCredentialProvider()
    )
    second = assemble_object_storage_delivery_v1(
        config=_config(), credential_provider=_FakeCredentialProvider()
    )

    assert first.metadata.as_dict() == second.metadata.as_dict()
    assert SECRET not in repr(first.metadata)
    assert SECRET not in str(first.metadata)
    assert SECRET not in repr(first)
    assert SECRET not in str(first)


def test_assembly_does_not_call_upload_or_lookup() -> None:
    client = _FakeObjectStorageProvider()

    assemble_object_storage_delivery_v1(
        config=_config(), credential_provider=_FakeCredentialProvider(client=client)
    )

    assert client.upload_calls == []
    assert client.lookup_calls == []


def test_assembled_adapter_preserves_p6_bindings_and_lookup_capability() -> None:
    client = _FakeObjectStorageProvider()
    assembly = assemble_object_storage_delivery_v1(
        config=_config(), credential_provider=_FakeCredentialProvider(client=client)
    )
    key = "a" * 64

    result = assembly.adapter.deliver(
        package_sha256="b" * 64,
        target_sha256="c" * 64,
        idempotency_key=key,
    )
    lookup = assembly.adapter.lookup(idempotency_key=key)  # type: ignore[attr-defined]

    assert result.status == "success"
    assert lookup is not None
    assert lookup.status == "success"
    assert client.upload_calls == [("b" * 64, "c" * 64, key)]
    assert client.lookup_calls == [key]


def test_lookupless_provider_keeps_lookup_unsupported() -> None:
    client = _FakeUploadOnlyProvider()
    assembly = assemble_object_storage_delivery_v1(
        config=_config(), credential_provider=_FakeCredentialProvider(client=client)
    )

    assert assembly.metadata.supports_lookup is False
    assert not callable(getattr(assembly.adapter, "lookup", None))
    assert assembly.adapter.deliver(
        package_sha256="d" * 64,
        target_sha256="e" * 64,
        idempotency_key="f" * 64,
    ).status == "success"


def test_missing_credentials_fail_closed_without_leaking_provider_error() -> None:
    provider = _FakeCredentialProvider(error=RuntimeError(SECRET))

    with pytest.raises(DeliveryRuntimeAssemblyError) as raised:
        assemble_object_storage_delivery_v1(config=_config(), credential_provider=provider)

    assert raised.value.code == "credential_resolution_failed"
    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_provider_type_mismatch_fails_before_resolution() -> None:
    provider = _FakeCredentialProvider(provider_type="different-provider")

    with pytest.raises(DeliveryRuntimeAssemblyError) as raised:
        assemble_object_storage_delivery_v1(config=_config(), credential_provider=provider)

    assert raised.value.code == "provider_mismatch"
    assert provider.resolve_calls == []


def test_non_object_storage_config_is_rejected_before_resolution() -> None:
    provider = _FakeCredentialProvider()

    with pytest.raises(DeliveryRuntimeAssemblyError) as raised:
        assemble_object_storage_delivery_v1(
            config=DeliveryBackendConfigV1.filesystem("destination"),
            credential_provider=provider,
        )

    assert raised.value.code == "unsupported_backend"
    assert provider.resolve_calls == []


def test_invalid_provider_type_is_rejected_without_provider_call() -> None:
    provider = _FakeCredentialProvider()

    with pytest.raises(DeliveryRuntimeAssemblyError) as raised:
        assemble_object_storage_delivery_v1(config=_config(provider_type="other"), credential_provider=provider)

    assert raised.value.code == "provider_mismatch"
    assert provider.resolve_calls == []


def test_assembled_component_is_not_serializable_and_has_no_authority_methods() -> None:
    assembly = assemble_object_storage_delivery_v1(
        config=_config(), credential_provider=_FakeCredentialProvider()
    )

    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(assembly)
    for method in (
        "create_identity",
        "create_receipt",
        "retry",
        "reconcile",
        "set_checkpoint",
        "decide_delivery",
    ):
        assert not hasattr(assembly, method)
        assert not hasattr(assembly.adapter, method)


def test_runtime_assembly_does_not_persist_provider_state() -> None:
    before = sorted(path.name for path in Path.cwd().iterdir())

    assemble_object_storage_adapter_v1(config=_config(), credential_provider=_FakeCredentialProvider())

    assert sorted(path.name for path in Path.cwd().iterdir()) == before
