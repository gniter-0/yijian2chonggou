"""Offline tests for the opaque production credential-source seams."""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pytest

from examcard.config.delivery_backend_config_v1 import DeliveryBackendConfigV1
from examcard.security.credential_source_v1 import (
    CredentialLeaseV1,
    CredentialSourceError,
    EnvironmentCredentialSourceV1,
    ExternalSecretManagerCredentialSourceV1,
    InjectedDeploymentCredentialSourceV1,
    materialize_credential_lease_v1,
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


class _Reader:
    def __init__(self, values: Mapping[str, str] | None = None, error: Exception | None = None) -> None:
        self.values = values
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def read(self, *, secret_reference: str, provider_type: str) -> Mapping[str, str]:
        self.calls.append((secret_reference, provider_type))
        if self.error is not None:
            raise self.error
        assert self.values is not None
        return self.values


def _config() -> DeliveryBackendConfigV1:
    return DeliveryBackendConfigV1.object_storage(
        provider_type="fake-object-store",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
        namespace="test",
        region="local",
    )


def test_injected_deployment_source_returns_an_opaque_lease() -> None:
    clients: list[_FakeProvider] = []

    def build(*, config: DeliveryBackendConfigV1) -> _FakeProvider:
        del config
        client = _FakeProvider()
        clients.append(client)
        return client

    source = InjectedDeploymentCredentialSourceV1(client_builder=build)
    lease = source.resolve(config=_config())

    assert source.source_type == "injected-deployment"
    assert isinstance(lease, CredentialLeaseV1)
    assert materialize_credential_lease_v1(lease) is clients[0]
    assert clients[0].upload_calls == []
    assert clients[0].lookup_calls == []


def test_environment_source_uses_injected_environment_and_hides_values() -> None:
    clients: list[_FakeProvider] = []

    def build(*, config: DeliveryBackendConfigV1, credential_values: Mapping[str, str]) -> _FakeProvider:
        del config
        client = _FakeProvider(credential=credential_values["token"])
        clients.append(client)
        return client

    source = EnvironmentCredentialSourceV1(
        client_builder=build,
        variable_names={"token": "FAKE_OBJECT_TOKEN"},
        environ={"FAKE_OBJECT_TOKEN": SECRET},
    )
    lease = source.resolve(config=_config())

    assert SECRET not in repr(source)
    assert SECRET not in str(source)
    assert SECRET not in repr(lease)
    assert SECRET not in str(lease)
    assert materialize_credential_lease_v1(lease) is clients[0]


def test_environment_source_fails_closed_when_a_credential_is_missing() -> None:
    source = EnvironmentCredentialSourceV1(
        client_builder=lambda **_kwargs: _FakeProvider(),
        variable_names={"token": "MISSING_FAKE_OBJECT_TOKEN"},
        environ={},
    )

    with pytest.raises(CredentialSourceError) as raised:
        source.resolve(config=_config())

    assert raised.value.code == "credentials_missing"
    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_external_secret_manager_reader_is_an_sdk_neutral_injection_seam() -> None:
    reader = _Reader({"token": SECRET})
    clients: list[_FakeProvider] = []

    def build(*, config: DeliveryBackendConfigV1, credential_values: Mapping[str, str]) -> _FakeProvider:
        del config
        client = _FakeProvider(credential=credential_values["token"])
        clients.append(client)
        return client

    source = ExternalSecretManagerCredentialSourceV1(
        reader=reader,
        secret_reference="secret://deployment/object-storage",
        client_builder=build,
    )
    lease = source.resolve(config=_config())

    assert reader.calls == [("secret://deployment/object-storage", "fake-object-store")]
    assert materialize_credential_lease_v1(lease) is clients[0]
    assert SECRET not in repr(source)
    assert SECRET not in str(source)
    assert SECRET not in repr(lease)
    assert SECRET not in str(lease)


def test_secret_manager_failure_is_redacted_and_not_chained() -> None:
    source = ExternalSecretManagerCredentialSourceV1(
        reader=_Reader(error=RuntimeError(SECRET)),
        secret_reference="secret://deployment/object-storage",
        client_builder=lambda **_kwargs: _FakeProvider(),
    )

    with pytest.raises(CredentialSourceError) as raised:
        source.resolve(config=_config())

    assert raised.value.code == "source_unavailable"
    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_invalid_lease_materializer_is_redacted() -> None:
    lease = CredentialLeaseV1(
        source_type="test",
        materializer=lambda: (_ for _ in ()).throw(RuntimeError(SECRET)),
    )

    with pytest.raises(CredentialSourceError) as raised:
        materialize_credential_lease_v1(lease)

    assert raised.value.code == "credentials_invalid"
    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_lease_and_source_reject_pickle_without_serializing_secrets() -> None:
    source = InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: _FakeProvider())
    lease = source.resolve(config=_config())

    for value in (source, lease):
        with pytest.raises(TypeError, match="cannot be serialized"):
            pickle.dumps(value)
        assert SECRET not in repr(value)
        assert SECRET not in str(value)


def test_credential_lease_exposes_only_safe_metadata_and_no_authority_methods() -> None:
    lease = InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: _FakeProvider()).resolve(
        config=_config()
    )

    assert lease.source_type == "injected-deployment"
    assert lease.expires_at is None
    for method in (
        "create_identity",
        "create_receipt",
        "retry",
        "reconcile",
        "set_checkpoint",
        "deliver",
        "lookup",
    ):
        assert not hasattr(lease, method)


def test_credential_rotation_creates_fresh_clients_without_changing_safe_config() -> None:
    clients = iter((_FakeProvider(credential="first"), _FakeProvider(credential="second")))

    def build(*, config: DeliveryBackendConfigV1) -> _FakeProvider:
        del config
        return next(clients)

    source = InjectedDeploymentCredentialSourceV1(client_builder=build)
    config = _config()
    first = materialize_credential_lease_v1(source.resolve(config=config))
    second = materialize_credential_lease_v1(source.resolve(config=config))

    assert first is not second
    assert config.canonical_sha256() == _config().canonical_sha256()
    bindings = {
        "package_sha256": "a" * 64,
        "target_sha256": "b" * 64,
        "idempotency_key": "c" * 64,
    }
    first.upload(**bindings)
    second.upload(**bindings)
    assert first.upload_calls == [(bindings["package_sha256"], bindings["target_sha256"], bindings["idempotency_key"])]
    assert second.upload_calls == [(bindings["package_sha256"], bindings["target_sha256"], bindings["idempotency_key"])]


def test_source_does_not_create_files_or_persistent_state() -> None:
    before = sorted(path.name for path in Path.cwd().iterdir())
    source = InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: _FakeProvider())
    materialize_credential_lease_v1(source.resolve(config=_config()))

    assert sorted(path.name for path in Path.cwd().iterdir()) == before
