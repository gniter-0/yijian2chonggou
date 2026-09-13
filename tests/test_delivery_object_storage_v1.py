"""Offline tests for the provider-neutral object-storage adapter."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from examcard.delivery.object_storage_v1 import (
    ObjectStorageDeliveryAdapter,
    normalize_provider_exception,
    normalize_provider_response,
    normalize_provider_status,
    sanitize_remote_reference,
)


@dataclass
class _FakeProvider:
    upload_response: object = field(default_factory=lambda: {"status": "success"})
    lookup_response: object = None
    upload_error: BaseException | None = None
    lookup_error: BaseException | None = None
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
        if self.upload_error is not None:
            raise self.upload_error
        return self.upload_response

    def lookup(self, *, idempotency_key: str) -> object:
        self.lookup_calls.append(idempotency_key)
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.lookup_response


class _UploadOnlyProvider:
    def upload(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> object:
        del package_sha256, target_sha256, idempotency_key
        return {"status": "success"}


def _adapter(provider: object) -> ObjectStorageDeliveryAdapter:
    return ObjectStorageDeliveryAdapter(provider)  # type: ignore[arg-type]


def test_success_upload_preserves_all_p6_bindings() -> None:
    provider = _FakeProvider(upload_response={"status": "uploaded", "remote_reference": "s3://bucket/object-1"})
    adapter = _adapter(provider)

    result = adapter.deliver(
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key="c" * 64,
    )

    assert result.status == "success"
    assert result.remote_reference == "s3://bucket/object-1"
    assert provider.upload_calls == [("a" * 64, "b" * 64, "c" * 64)]


def test_known_provider_failure_normalizes_to_failure() -> None:
    adapter = _adapter(_FakeProvider(upload_response={"status": "rejected", "remote_reference": "object-1"}))

    result = adapter.deliver(
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key="c" * 64,
    )

    assert result.status == "failure"
    assert result.remote_reference == "object-1"


@pytest.mark.parametrize("error", [TimeoutError("upload timeout"), ConnectionError("network interrupted")])
def test_timeout_and_network_interruption_are_unknown(error: BaseException) -> None:
    adapter = _adapter(_FakeProvider(upload_error=error))

    result = adapter.deliver(
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key="c" * 64,
    )

    assert result.status == "unknown"
    assert result.remote_reference is None


def test_lookup_success_uses_the_same_idempotency_key() -> None:
    provider = _FakeProvider(lookup_response={"status": "completed", "remote_reference": "object-1"})
    adapter = _adapter(provider)
    key = "d" * 64

    result = adapter.lookup(idempotency_key=key)

    assert result is not None
    assert result.status == "success"
    assert result.remote_reference == "object-1"
    assert provider.lookup_calls == [key]
    assert adapter.supports_lookup is True


def test_lookup_capability_is_optional() -> None:
    adapter = _adapter(_UploadOnlyProvider())

    assert adapter.supports_lookup is False
    assert adapter.lookup(idempotency_key="a" * 64) is None


def test_duplicate_idempotency_key_is_forwarded_unchanged() -> None:
    provider = _FakeProvider(upload_response={"status": "success", "remote_reference": "object-existing"})
    adapter = _adapter(provider)
    key = "e" * 64

    first = adapter.deliver(package_sha256="1" * 64, target_sha256="2" * 64, idempotency_key=key)
    second = adapter.deliver(package_sha256="1" * 64, target_sha256="2" * 64, idempotency_key=key)

    assert first == second
    assert [call[2] for call in provider.upload_calls] == [key, key]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("s3://bucket/path/object-1", "s3://bucket/path/object-1"),
        ("object-1?signature=secret", None),
        ("https://user:password@example.com/object", None),
        ("s3://bucket/object#token", None),
        ("secret-token", None),
        (None, None),
    ],
)
def test_remote_reference_sanitization(value: object, expected: str | None) -> None:
    assert sanitize_remote_reference(value) == expected


def test_unsafe_remote_reference_is_removed_without_changing_success() -> None:
    adapter = _adapter(_FakeProvider(upload_response={"status": "success", "remote_reference": "object?token=secret"}))

    result = adapter.deliver(
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key="c" * 64,
    )

    assert result.status == "success"
    assert result.remote_reference is None


def test_provider_exception_is_normalized_without_leaking_its_message() -> None:
    secret = "credential-value-should-not-escape"
    result = _adapter(_FakeProvider(upload_error=RuntimeError(secret))).deliver(
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key="c" * 64,
    )

    assert result.status == "unknown"
    assert secret not in result.model_dump_json()
    assert secret not in repr(result)


def test_normalizers_are_allowlisted_and_do_not_create_p6_authority() -> None:
    assert normalize_provider_status({"status": "failure"}) == "failure"
    assert normalize_provider_response({"status": "unknown", "receipt": "forbidden"}).model_dump(
        mode="json"
    ) == {"status": "unknown", "remote_reference": None}
    assert normalize_provider_exception(RuntimeError("secret")).model_dump(mode="json") == {
        "status": "unknown",
        "remote_reference": None,
    }
