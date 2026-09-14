"""Lifecycle tests for the production credential-provider boundary."""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from examcard.config.delivery_backend_config_v1 import DeliveryBackendConfigV1
from examcard.delivery.object_storage_v1 import ObjectStorageDeliveryAdapter
from examcard.security.credential_provider_v1 import (
    CredentialProvider,
    CredentialProviderError,
    resolve_provider_client,
)
from examcard.security.credential_source_v1 import (
    CredentialLeaseV1,
    EnvironmentCredentialSourceV1,
    InjectedDeploymentCredentialSourceV1,
    materialize_credential_lease_v1,
)
from examcard.security.production_credential_provider_v1 import (
    ProductionCredentialProviderV1,
)

SECRET = "production-provider-secret-must-not-escape"


@dataclass
class _FakeObjectStorageProvider:
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
        return {"status": "success", "remote_reference": "fake-object"}

    def lookup(self, *, idempotency_key: str) -> dict[str, str]:
        self.lookup_calls.append(idempotency_key)
        return {"status": "success", "remote_reference": "fake-object"}


class _Source:
    source_type = "injected-test"

    def __init__(self, lease: object | None = None, error: BaseException | None = None) -> None:
        self.lease = lease
        self.error = error
        self.calls = 0

    def resolve(self, *, config: DeliveryBackendConfigV1) -> object:
        del config
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.lease


class _MismatchedSource(_Source):
    provider_type = "different-object-store"


def _config(provider_type: str = "fake-object-store") -> DeliveryBackendConfigV1:
    return DeliveryBackendConfigV1.object_storage(
        provider_type=provider_type,
        endpoint="objects.example.test",
        bucket="exam-artifacts",
        namespace="test",
        region="local",
    )


def _source_for(client: _FakeObjectStorageProvider) -> InjectedDeploymentCredentialSourceV1:
    return InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: client)


def test_production_provider_implements_the_existing_contract() -> None:
    provider = ProductionCredentialProviderV1(
        source=_source_for(_FakeObjectStorageProvider()),
        provider_type="fake-object-store",
    )

    assert isinstance(provider, CredentialProvider)


def test_provider_resolution_is_lazy_and_does_not_upload_or_lookup() -> None:
    client = _FakeObjectStorageProvider()
    source = _source_for(client)
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")

    resolved = provider.resolve(config=_config())

    assert resolved is client
    assert source is not None
    assert client.upload_calls == []
    assert client.lookup_calls == []


def test_explicit_client_factory_receives_only_an_opaque_lease() -> None:
    client = _FakeObjectStorageProvider()
    source = _source_for(client)
    leases: list[CredentialLeaseV1] = []

    def factory(lease: CredentialLeaseV1) -> _FakeObjectStorageProvider:
        leases.append(lease)
        assert SECRET not in repr(lease)
        assert SECRET not in str(lease)
        return materialize_credential_lease_v1(lease)

    provider = ProductionCredentialProviderV1(
        source=source,
        provider_type="fake-object-store",
        client_factory=factory,
    )

    assert provider.resolve(config=_config()) is client
    assert len(leases) == 1


def test_provider_rejects_non_object_storage_config_before_resolving_source() -> None:
    source = _Source()
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")

    with pytest.raises(CredentialProviderError) as raised:
        provider.resolve(config=DeliveryBackendConfigV1.filesystem("destination"))

    assert raised.value.code == "unsupported_backend"
    assert source.calls == 0


def test_provider_and_source_mismatch_fail_closed_before_resolution() -> None:
    source = _Source()
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")
    with pytest.raises(CredentialProviderError) as config_error:
        provider.resolve(config=_config("different-object-store"))
    assert config_error.value.code == "resolution_failed"
    assert source.calls == 0

    mismatched_source = _MismatchedSource(lease=object())
    mismatched_provider = ProductionCredentialProviderV1(
        source=mismatched_source,
        provider_type="fake-object-store",
    )
    with pytest.raises(CredentialProviderError) as source_error:
        mismatched_provider.resolve(config=_config())
    assert source_error.value.code == "resolution_failed"
    assert mismatched_source.calls == 0


def test_missing_or_unavailable_credentials_fail_closed_without_secret_errors() -> None:
    source = EnvironmentCredentialSourceV1(
        client_builder=lambda **_kwargs: _FakeObjectStorageProvider(),
        variable_names={"token": "MISSING_FAKE_OBJECT_TOKEN"},
        environ={},
    )
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")
    with pytest.raises(CredentialProviderError) as missing:
        provider.resolve(config=_config())
    assert missing.value.code == "resolution_failed"
    assert SECRET not in str(missing.value)

    unavailable_source = _Source(error=RuntimeError(SECRET))
    unavailable_provider = ProductionCredentialProviderV1(
        source=unavailable_source,
        provider_type="fake-object-store",
    )
    with pytest.raises(CredentialProviderError) as unavailable:
        unavailable_provider.resolve(config=_config())
    assert unavailable.value.code == "resolution_failed"
    assert SECRET not in repr(unavailable.value)
    assert unavailable.value.__cause__ is None
    assert unavailable.value.__context__ is None


def test_invalid_lease_or_provider_client_fails_closed() -> None:
    invalid_lease_provider = ProductionCredentialProviderV1(
        source=_Source(lease=object()),
        provider_type="fake-object-store",
    )
    with pytest.raises(CredentialProviderError) as invalid_lease:
        invalid_lease_provider.resolve(config=_config())
    assert invalid_lease.value.code == "resolution_failed"

    invalid_client_provider = ProductionCredentialProviderV1(
        source=_Source(lease=CredentialLeaseV1(source_type="test", materializer=lambda: object())),
        provider_type="fake-object-store",
    )
    with pytest.raises(CredentialProviderError) as invalid_client:
        invalid_client_provider.resolve(config=_config())
    assert invalid_client.value.code == "resolution_failed"


def test_rotation_returns_fresh_clients_without_changing_delivery_bindings() -> None:
    clients = iter((_FakeObjectStorageProvider(credential="first"), _FakeObjectStorageProvider(credential="second")))
    provider = ProductionCredentialProviderV1(
        source=InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: next(clients)),
        provider_type="fake-object-store",
    )
    config = _config()
    first = provider.resolve(config=config)
    second = provider.resolve(config=config)

    assert first is not second
    assert first.credential == "first"
    assert second.credential == "second"
    assert config.canonical_sha256() == _config().canonical_sha256()

    bindings = {
        "package_sha256": "a" * 64,
        "target_sha256": "b" * 64,
        "idempotency_key": "c" * 64,
    }
    first.upload(**bindings)
    second.upload(**bindings)
    expected = [(bindings["package_sha256"], bindings["target_sha256"], bindings["idempotency_key"])]
    assert first.upload_calls == expected
    assert second.upload_calls == expected


def test_p6_facing_wrapper_is_opaque_and_preserves_lookup_capability() -> None:
    provider = ProductionCredentialProviderV1(
        source=_source_for(_FakeObjectStorageProvider()),
        provider_type="fake-object-store",
    )
    resolved = resolve_provider_client(credential_provider=provider, config=_config())

    assert SECRET not in repr(resolved)
    assert SECRET not in str(resolved)
    assert callable(getattr(resolved, "lookup", None))
    for method in ("create_identity", "create_receipt", "retry", "reconcile", "set_checkpoint"):
        assert not hasattr(resolved, method)


def test_unknown_provider_outcome_remains_below_this_boundary() -> None:
    class _UnknownProvider(_FakeObjectStorageProvider):
        def upload(
            self,
            *,
            package_sha256: str,
            target_sha256: str,
            idempotency_key: str,
        ) -> dict[str, str]:
            del package_sha256, target_sha256, idempotency_key
            return {"status": "provider-specific-ambiguous"}

    provider = ProductionCredentialProviderV1(
        source=_source_for(_UnknownProvider()),
        provider_type="fake-object-store",
    )
    resolved = resolve_provider_client(credential_provider=provider, config=_config())
    adapter = ObjectStorageDeliveryAdapter(resolved)

    assert not hasattr(provider, "deliver")
    assert adapter.deliver(
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key="c" * 64,
    ).status == "unknown"


def test_provider_construction_is_lazy_for_resume_short_circuits() -> None:
    source = _Source(error=RuntimeError(SECRET))
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")

    # A completed-receipt path can return before P8 asks this provider to resolve.
    assert provider.provider_type == "fake-object-store"
    assert source.calls == 0


def test_provider_is_redacted_nonserializable_and_does_not_persist_state(capsys) -> None:
    provider = ProductionCredentialProviderV1(
        source=_source_for(_FakeObjectStorageProvider()),
        provider_type="fake-object-store",
    )
    before = sorted(path.name for path in Path.cwd().iterdir())

    assert SECRET not in repr(provider)
    assert SECRET not in str(provider)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(provider)
    provider.resolve(config=_config())

    print(provider)
    output = capsys.readouterr()
    assert SECRET not in output.out
    assert SECRET not in output.err
    assert sorted(path.name for path in Path.cwd().iterdir()) == before
