"""Offline tests for production credential-provider composition."""
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
from examcard.security.credential_source_v1 import (
    CredentialLeaseV1,
    InjectedDeploymentCredentialSourceV1,
)
from examcard.security.production_credential_provider_v1 import (
    ProductionCredentialProviderV1,
)

SECRET = "synthetic-production-secret-must-not-escape"


@dataclass
class _FakeProvider:
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
        return {"status": "success", "remote_reference": "fake-object-1"}

    def lookup(self, *, idempotency_key: str) -> dict[str, str]:
        self.lookup_calls.append(idempotency_key)
        return {"status": "success", "remote_reference": "fake-object-1"}


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


def _source_for(client: _FakeProvider) -> InjectedDeploymentCredentialSourceV1:
    return InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: client)


def test_production_provider_resolves_an_injected_client_without_delivery() -> None:
    client = _FakeProvider()
    provider = ProductionCredentialProviderV1(
        source=_source_for(client),
        provider_type="fake-object-store",
    )

    resolved = provider.resolve(config=_config())

    assert resolved is client
    assert client.upload_calls == []
    assert client.lookup_calls == []


def test_explicit_client_factory_receives_only_an_opaque_lease() -> None:
    client = _FakeProvider()
    source = _source_for(client)
    received: list[CredentialLeaseV1] = []

    def factory(lease: CredentialLeaseV1) -> _FakeProvider:
        received.append(lease)
        assert SECRET not in repr(lease)
        assert SECRET not in str(lease)
        return client

    provider = ProductionCredentialProviderV1(
        source=source,
        provider_type="fake-object-store",
        client_factory=factory,
    )

    assert provider.resolve(config=_config()) is client
    assert len(received) == 1


def test_provider_construction_is_lazy_and_has_no_authority_surface() -> None:
    source = _Source()
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")

    assert source.calls == 0
    for method in (
        "upload",
        "lookup",
        "retry",
        "reconcile",
        "create_identity",
        "create_receipt",
        "set_checkpoint",
    ):
        assert not hasattr(provider, method)


def test_provider_rejects_non_object_storage_config_before_source_resolution() -> None:
    source = _Source()
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")

    with pytest.raises(CredentialProviderError) as raised:
        provider.resolve(config=DeliveryBackendConfigV1.filesystem("destination"))

    assert raised.value.code == "unsupported_backend"
    assert source.calls == 0


def test_provider_mismatch_fails_closed_before_source_resolution() -> None:
    source = _Source()
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")

    with pytest.raises(CredentialProviderError) as raised:
        provider.resolve(config=_config(provider_type="other-object-store"))

    assert raised.value.code == "resolution_failed"
    assert source.calls == 0


def test_source_declared_provider_mismatch_fails_closed() -> None:
    source = _MismatchedSource(
        lease=InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: _FakeProvider()).resolve(
            config=_config()
        )
    )
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")

    with pytest.raises(CredentialProviderError) as raised:
        provider.resolve(config=_config())

    assert raised.value.code == "resolution_failed"
    assert source.calls == 0


def test_missing_credentials_fail_closed_with_a_fixed_redacted_error(capsys) -> None:
    source = _Source(error=RuntimeError(SECRET))
    provider = ProductionCredentialProviderV1(source=source, provider_type="fake-object-store")

    with pytest.raises(CredentialProviderError) as raised:
        provider.resolve(config=_config())

    assert raised.value.code == "resolution_failed"
    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    print(provider)
    print(raised.value)
    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err


def test_invalid_lease_and_invalid_client_fail_closed() -> None:
    invalid_lease_provider = ProductionCredentialProviderV1(
        source=_Source(lease=object()),
        provider_type="fake-object-store",
    )
    with pytest.raises(CredentialProviderError) as invalid_lease_error:
        invalid_lease_provider.resolve(config=_config())
    assert invalid_lease_error.value.code == "resolution_failed"

    invalid_client_provider = ProductionCredentialProviderV1(
        source=_Source(
            lease=CredentialLeaseV1(source_type="test", materializer=lambda: object())
        ),
        provider_type="fake-object-store",
    )
    with pytest.raises(CredentialProviderError) as invalid_client_error:
        invalid_client_provider.resolve(config=_config())
    assert invalid_client_error.value.code == "resolution_failed"


def test_provider_client_resolution_preserves_lookup_capability_and_p6_bindings() -> None:
    client = _FakeProvider()
    provider = ProductionCredentialProviderV1(
        source=_source_for(client),
        provider_type="fake-object-store",
    )
    resolved = resolve_provider_client(credential_provider=provider, config=_config())
    key = "c" * 64

    assert resolved.lookup(idempotency_key=key) == {  # type: ignore[attr-defined]
        "status": "success",
        "remote_reference": "fake-object-1",
    }
    assert resolved.upload(  # type: ignore[attr-defined]
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key=key,
    ) == {"status": "success", "remote_reference": "fake-object-1"}
    assert client.lookup_calls == [key]
    assert client.upload_calls == [("a" * 64, "b" * 64, key)]


def test_rotation_resolves_new_clients_without_changing_config_or_delivery_bindings() -> None:
    clients = iter((_FakeProvider(credential="first"), _FakeProvider(credential="second")))

    def build(*, config: DeliveryBackendConfigV1) -> _FakeProvider:
        del config
        return next(clients)

    config = _config()
    provider = ProductionCredentialProviderV1(
        source=InjectedDeploymentCredentialSourceV1(client_builder=build),
        provider_type="fake-object-store",
    )
    first = provider.resolve(config=config)
    second = provider.resolve(config=config)

    assert first is not second
    assert config.canonical_sha256() == _config().canonical_sha256()
    bindings = {
        "package_sha256": "a" * 64,
        "target_sha256": "b" * 64,
        "idempotency_key": "c" * 64,
    }
    first.upload(**bindings)
    second.upload(**bindings)
    expected_calls = [(bindings["package_sha256"], bindings["target_sha256"], bindings["idempotency_key"])]
    assert first.upload_calls == expected_calls
    assert second.upload_calls == expected_calls


def test_secret_values_never_appear_in_provider_output_or_serialization() -> None:
    provider = ProductionCredentialProviderV1(
        source=_source_for(_FakeProvider()),
        provider_type="fake-object-store",
    )

    assert SECRET not in repr(provider)
    assert SECRET not in str(provider)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(provider)


def test_resolution_does_not_persist_provider_or_delivery_state() -> None:
    before = sorted(path.name for path in Path.cwd().iterdir())
    provider = ProductionCredentialProviderV1(
        source=_source_for(_FakeProvider()),
        provider_type="fake-object-store",
    )
    provider.resolve(config=_config())

    assert sorted(path.name for path in Path.cwd().iterdir()) == before
