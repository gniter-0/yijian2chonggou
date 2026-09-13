"""Offline contract tests for object-storage registry assembly."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from examcard.delivery.backend_registry_v1 import DeliveryBackendRegistryV1
from examcard.delivery.filesystem_v1 import (
    FilesystemDirectoryDeliveryAdapter,
    build_filesystem_delivery_target,
)
from examcard.delivery.object_storage_registry_v1 import (
    OBJECT_STORAGE_BACKEND_TYPE,
    register_object_storage_v1,
)
from examcard.delivery.object_storage_v1 import ObjectStorageDeliveryAdapter


@dataclass
class _LookupProvider:
    upload_response: object = field(default_factory=lambda: {"status": "success"})
    lookup_response: object = field(default_factory=lambda: {"status": "success"})
    credential: str = "credential-must-stay-in-memory"
    upload_calls: list[tuple[str, str, str]] = field(default_factory=list)
    lookup_calls: list[str] = field(default_factory=list)

    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> object:
        self.upload_calls.append((package_sha256, target_sha256, idempotency_key))
        return self.upload_response

    def lookup(self, *, idempotency_key: str) -> object:
        self.lookup_calls.append(idempotency_key)
        return self.lookup_response


@dataclass
class _UploadOnlyProvider:
    upload_calls: list[tuple[str, str, str]] = field(default_factory=list)

    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> object:
        self.upload_calls.append((package_sha256, target_sha256, idempotency_key))
        return {"status": "success", "remote_reference": "object-1"}


def test_object_storage_backend_is_selected_through_the_registry() -> None:
    registry = DeliveryBackendRegistryV1()
    provider = _LookupProvider()

    registration = register_object_storage_v1(registry, provider=provider)
    adapter = registry.create(OBJECT_STORAGE_BACKEND_TYPE)

    assert registration.backend_type == OBJECT_STORAGE_BACKEND_TYPE
    assert registry.backend_types() == (OBJECT_STORAGE_BACKEND_TYPE,)
    assert isinstance(adapter, ObjectStorageDeliveryAdapter)
    assert registry.registration(OBJECT_STORAGE_BACKEND_TYPE) == registration


def test_factory_uses_the_injected_provider_without_reconfiguration() -> None:
    registry = DeliveryBackendRegistryV1()
    provider = _LookupProvider()
    register_object_storage_v1(registry, provider=provider)
    adapter = registry.create(OBJECT_STORAGE_BACKEND_TYPE)

    result = adapter.deliver(
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key="c" * 64,
    )

    assert result.status == "success"
    assert provider.upload_calls == [("a" * 64, "b" * 64, "c" * 64)]


def test_lookup_capability_is_preserved_for_a_provider_that_supports_it() -> None:
    registry = DeliveryBackendRegistryV1()
    provider = _LookupProvider()
    register_object_storage_v1(registry, provider=provider)
    adapter = registry.create(OBJECT_STORAGE_BACKEND_TYPE)
    key = "d" * 64

    result = adapter.lookup(idempotency_key=key)  # type: ignore[attr-defined]

    assert registry.registration(OBJECT_STORAGE_BACKEND_TYPE).capabilities.supports_lookup is True
    assert result is not None
    assert result.status == "success"
    assert provider.lookup_calls == [key]


def test_lookupless_provider_has_no_lookup_surface_for_p6() -> None:
    registry = DeliveryBackendRegistryV1()
    provider = _UploadOnlyProvider()
    register_object_storage_v1(registry, provider=provider)
    adapter = registry.create(OBJECT_STORAGE_BACKEND_TYPE)

    assert registry.registration(OBJECT_STORAGE_BACKEND_TYPE).capabilities.supports_lookup is False
    assert not callable(getattr(adapter, "lookup", None))
    result = adapter.deliver(
        package_sha256="e" * 64,
        target_sha256="f" * 64,
        idempotency_key="1" * 64,
    )

    assert result.status == "success"
    assert provider.upload_calls == [("e" * 64, "f" * 64, "1" * 64)]


def test_package_target_and_idempotency_bindings_are_forwarded_unchanged() -> None:
    registry = DeliveryBackendRegistryV1()
    provider = _LookupProvider()
    register_object_storage_v1(registry, provider=provider)
    adapter = registry.create(OBJECT_STORAGE_BACKEND_TYPE)

    adapter.deliver(
        package_sha256="2" * 64,
        target_sha256="3" * 64,
        idempotency_key="4" * 64,
    )

    assert provider.upload_calls == [("2" * 64, "3" * 64, "4" * 64)]


def test_registry_assembly_creates_no_receipts_or_checkpoints() -> None:
    before = sorted(path.name for path in Path.cwd().iterdir())
    registry = DeliveryBackendRegistryV1()
    register_object_storage_v1(registry, provider=_UploadOnlyProvider())
    registry.create(OBJECT_STORAGE_BACKEND_TYPE)

    assert sorted(path.name for path in Path.cwd().iterdir()) == before
    assert not hasattr(registry, "create_receipt")
    assert not hasattr(registry, "set_checkpoint")
    assert not hasattr(registry, "decide")


def test_existing_filesystem_registration_contract_remains_available() -> None:
    publication_dir = Path.cwd()
    destination_root = Path.cwd()
    target = build_filesystem_delivery_target(destination_root)
    registry = DeliveryBackendRegistryV1()
    registry.register(
        "filesystem-directory-v1",
        lambda: FilesystemDirectoryDeliveryAdapter(
            publication_dir=publication_dir,
            destination_root=destination_root,
            target=target,
        ),
    )

    adapter = registry.create("filesystem-directory-v1")

    assert isinstance(adapter, FilesystemDirectoryDeliveryAdapter)
    assert adapter.target.model_dump(mode="json") == target.model_dump(mode="json")


def test_provider_credentials_and_unsafe_references_do_not_escape_results() -> None:
    secret = "credential-must-stay-in-memory"
    provider = _LookupProvider(
        upload_response={"status": "success", "remote_reference": "object?token=" + secret}
    )
    registry = DeliveryBackendRegistryV1()
    registration = register_object_storage_v1(registry, provider=provider)
    adapter = registry.create(OBJECT_STORAGE_BACKEND_TYPE)

    result = adapter.deliver(
        package_sha256="5" * 64,
        target_sha256="6" * 64,
        idempotency_key="7" * 64,
    )

    assert result.status == "success"
    assert result.remote_reference is None
    assert secret not in repr(registration)
    assert secret not in repr(registry)
    assert secret not in result.model_dump_json()
