"""Offline tests for the non-authoritative delivery backend registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from examcard.common.hashing import sha256_text
from examcard.delivery.backend_registry_v1 import (
    DeliveryBackendCapabilities,
    DeliveryBackendRegistryV1,
)
from examcard.delivery.filesystem_v1 import (
    FilesystemDirectoryDeliveryAdapter,
    build_filesystem_delivery_target,
)
from examcard.gates.release.delivery_v2 import (
    DeliveryAdapterResult,
    normalize_adapter_result,
)


class _RecordingAdapter:
    def __init__(self, *, result: DeliveryAdapterResult | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.result = result or DeliveryAdapterResult(status="success")

    def deliver(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> DeliveryAdapterResult:
        self.calls.append((package_sha256, target_sha256, idempotency_key))
        return self.result


class _LookupAdapter(_RecordingAdapter):
    def lookup(self, *, idempotency_key: str) -> DeliveryAdapterResult:
        return DeliveryAdapterResult(status="unknown", remote_reference=idempotency_key)


def test_registration_and_factory_lookup_are_explicit_and_process_local() -> None:
    registry = DeliveryBackendRegistryV1()
    adapter = _RecordingAdapter()
    registration = registry.register("test", lambda: adapter)

    assert registry.backend_types() == ("test",)
    assert registry.registration("test") == registration
    assert registry.create("test") is adapter
    with pytest.raises(KeyError, match="unknown delivery backend"):
        registry.registration("missing")


def test_duplicate_registration_is_rejected_without_replacement() -> None:
    registry = DeliveryBackendRegistryV1()
    first = _RecordingAdapter()
    second = _RecordingAdapter()
    registry.register("test", lambda: first)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("test", lambda: second)
    assert registry.create("test") is first


def test_filesystem_adapter_is_compatible_with_the_frozen_p6_seam() -> None:
    # Only construct the adapter here; no delivery is invoked and therefore
    # the existing workspace directory is not modified.
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
        capabilities=DeliveryBackendCapabilities(supports_lookup=True),
    )

    adapter = registry.create("filesystem-directory-v1")
    assert isinstance(adapter, FilesystemDirectoryDeliveryAdapter)
    assert callable(adapter.deliver)
    assert callable(adapter.lookup)


@pytest.mark.parametrize("status", ["success", "failure", "unknown"])
def test_p6_result_normalization_preserves_all_adapter_statuses(status: str) -> None:
    result = normalize_adapter_result({"status": status})

    assert result is not None
    assert result.status == status


def test_lookup_capability_is_optional() -> None:
    registry = DeliveryBackendRegistryV1()
    without_lookup = _RecordingAdapter()
    with_lookup = _LookupAdapter()
    registry.register("without-lookup", lambda: without_lookup)
    registry.register(
        "with-lookup",
        lambda: with_lookup,
        capabilities=DeliveryBackendCapabilities(supports_lookup=True),
    )

    assert registry.registration("without-lookup").capabilities.supports_lookup is False
    assert registry.create("without-lookup") is without_lookup
    assert registry.registration("with-lookup").capabilities.supports_lookup is True
    assert registry.create("with-lookup") is with_lookup


def test_lookup_capability_must_match_the_adapter_surface() -> None:
    registry = DeliveryBackendRegistryV1()
    registry.register(
        "incorrect",
        _RecordingAdapter,
        capabilities=DeliveryBackendCapabilities(supports_lookup=True),
    )

    with pytest.raises(TypeError, match="lookup support"):
        registry.create("incorrect")


def test_registry_passes_the_same_idempotency_key_to_the_adapter() -> None:
    registry = DeliveryBackendRegistryV1()
    adapter = _RecordingAdapter()
    registry.register("test", lambda: adapter)
    created = registry.create("test")
    key = sha256_text("frozen-delivery-identity")

    result = created.deliver(
        package_sha256="1" * 64,
        target_sha256="2" * 64,
        idempotency_key=key,
    )

    assert result.status == "success"
    assert adapter.calls == [("1" * 64, "2" * 64, key)]


def test_registry_does_not_create_receipts_or_make_authority_decisions() -> None:
    registry = DeliveryBackendRegistryV1()
    registry.register("test", _RecordingAdapter)
    created = registry.create("test")
    result = created.deliver(
        package_sha256="1" * 64,
        target_sha256="2" * 64,
        idempotency_key="3" * 64,
    )

    assert isinstance(result, DeliveryAdapterResult)
    assert set(result.model_dump(mode="json")) == {"status", "remote_reference"}
    assert not hasattr(registry, "create_receipt")
    assert not hasattr(registry, "set_checkpoint")
    assert not hasattr(registry, "decide")
