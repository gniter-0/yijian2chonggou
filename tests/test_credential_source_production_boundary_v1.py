"""Lifecycle and isolation tests for production credential sources."""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping

import pytest

from examcard.config.delivery_backend_config_v1 import DeliveryBackendConfigV1
from examcard.security.credential_source_v1 import (
    CredentialSourceError,
    EnvironmentCredentialSourceV1,
    ExternalSecretManagerCredentialSourceV1,
    InjectedDeploymentCredentialSourceV1,
    materialize_credential_lease_v1,
)

SECRET = "production-source-secret-must-not-escape"


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


class _FakeSecretReader:
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


def test_environment_source_requires_an_injected_mapping() -> None:
    with pytest.raises(TypeError):
        EnvironmentCredentialSourceV1(
            client_builder=lambda **_kwargs: _FakeObjectStorageProvider(),
            variable_names={"token": "FAKE_OBJECT_TOKEN"},
        )


def test_environment_source_resolves_only_from_the_injected_mapping() -> None:
    seen: list[Mapping[str, str]] = []

    def build(*, config: DeliveryBackendConfigV1, credential_values: Mapping[str, str]) -> _FakeObjectStorageProvider:
        del config
        seen.append(credential_values)
        return _FakeObjectStorageProvider(credential=credential_values["token"])

    source = EnvironmentCredentialSourceV1(
        client_builder=build,
        variable_names={"token": "FAKE_OBJECT_TOKEN"},
        environ={"FAKE_OBJECT_TOKEN": SECRET},
    )
    lease = source.resolve(config=_config())
    client = materialize_credential_lease_v1(lease)

    assert client.credential == SECRET
    assert seen == [{"token": SECRET}]
    assert SECRET not in repr(source)
    assert SECRET not in str(source)
    assert SECRET not in repr(lease)
    assert SECRET not in str(lease)


def test_environment_source_reports_missing_credentials_without_leaking_values() -> None:
    source = EnvironmentCredentialSourceV1(
        client_builder=lambda **_kwargs: _FakeObjectStorageProvider(),
        variable_names={"token": "MISSING_FAKE_OBJECT_TOKEN"},
        environ={},
    )

    with pytest.raises(CredentialSourceError) as raised:
        source.resolve(config=_config())

    assert raised.value.code == "credentials_missing"
    assert SECRET not in str(raised.value)
    assert raised.value.__context__ is None


def test_external_secret_reader_is_injected_and_sdk_neutral() -> None:
    reader = _FakeSecretReader({"token": SECRET})

    def build(*, config: DeliveryBackendConfigV1, credential_values: Mapping[str, str]) -> _FakeObjectStorageProvider:
        del config
        return _FakeObjectStorageProvider(credential=credential_values["token"])

    source = ExternalSecretManagerCredentialSourceV1(
        reader=reader,
        secret_reference="secret://deployment/object-storage",
        client_builder=build,
    )
    lease = source.resolve(config=_config())
    client = materialize_credential_lease_v1(lease)

    assert reader.calls == [("secret://deployment/object-storage", "fake-object-store")]
    assert client.credential == SECRET
    assert SECRET not in repr(source)
    assert SECRET not in repr(lease)


def test_external_reader_invalid_credentials_fail_closed() -> None:
    source = ExternalSecretManagerCredentialSourceV1(
        reader=_FakeSecretReader({"token": ""}),
        secret_reference="secret://deployment/object-storage",
        client_builder=lambda **_kwargs: _FakeObjectStorageProvider(),
    )

    with pytest.raises(CredentialSourceError) as raised:
        source.resolve(config=_config())

    assert raised.value.code == "credentials_invalid"
    assert SECRET not in str(raised.value)


def test_external_reader_unavailable_error_is_fixed_and_unlinked() -> None:
    source = ExternalSecretManagerCredentialSourceV1(
        reader=_FakeSecretReader(error=RuntimeError(SECRET)),
        secret_reference="secret://deployment/object-storage",
        client_builder=lambda **_kwargs: _FakeObjectStorageProvider(),
    )

    with pytest.raises(CredentialSourceError) as raised:
        source.resolve(config=_config())

    assert raised.value.code == "source_unavailable"
    assert SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_credential_lease_is_single_use_and_releases_its_materializer() -> None:
    clients: list[_FakeObjectStorageProvider] = []

    def build(*, config: DeliveryBackendConfigV1) -> _FakeObjectStorageProvider:
        del config
        client = _FakeObjectStorageProvider()
        clients.append(client)
        return client

    lease = InjectedDeploymentCredentialSourceV1(client_builder=build).resolve(config=_config())
    assert materialize_credential_lease_v1(lease) is clients[0]

    with pytest.raises(CredentialSourceError) as raised:
        materialize_credential_lease_v1(lease)

    assert raised.value.code == "credentials_invalid"
    assert len(clients) == 1
    assert SECRET not in str(raised.value)


def test_rotation_resolves_a_fresh_client_from_a_fresh_lease() -> None:
    clients = iter((_FakeObjectStorageProvider(credential="first"), _FakeObjectStorageProvider(credential="second")))
    source = InjectedDeploymentCredentialSourceV1(
        client_builder=lambda **_kwargs: next(clients),
    )
    config = _config()

    first = materialize_credential_lease_v1(source.resolve(config=config))
    second = materialize_credential_lease_v1(source.resolve(config=config))

    assert first is not second
    assert first.credential == "first"
    assert second.credential == "second"
    assert config.canonical_sha256() == _config().canonical_sha256()


def test_injected_source_does_not_perform_provider_operations() -> None:
    client = _FakeObjectStorageProvider()
    source = InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: client)

    materialize_credential_lease_v1(source.resolve(config=_config()))

    assert client.upload_calls == []
    assert client.lookup_calls == []


def test_source_and_lease_are_redacted_nonserializable_and_authority_free(capsys) -> None:
    source = InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: _FakeObjectStorageProvider())
    lease = source.resolve(config=_config())

    assert SECRET not in repr(source)
    assert SECRET not in str(source)
    assert SECRET not in repr(lease)
    assert SECRET not in str(lease)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(source)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(lease)
    with pytest.raises(TypeError):
        vars(lease)
    for method in ("upload", "lookup", "retry", "reconcile", "create_identity", "create_receipt", "set_checkpoint"):
        assert not hasattr(lease, method)

    print(source)
    print(lease)
    output = capsys.readouterr()
    assert SECRET not in output.out
    assert SECRET not in output.err


def test_source_resolution_does_not_persist_files_or_delivery_state() -> None:
    before = sorted(path.name for path in Path.cwd().iterdir())
    source = InjectedDeploymentCredentialSourceV1(client_builder=lambda **_kwargs: _FakeObjectStorageProvider())
    materialize_credential_lease_v1(source.resolve(config=_config()))

    assert sorted(path.name for path in Path.cwd().iterdir()) == before
