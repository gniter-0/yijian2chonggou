"""Offline tests for the non-authoritative delivery backend config model."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from examcard.config import BackendType, DeliveryBackendConfigV1


def test_valid_filesystem_config_is_typed_and_secret_free(tmp_path: Path) -> None:
    config = DeliveryBackendConfigV1.filesystem(tmp_path / "delivery")

    assert config.backend_type is BackendType.FILESYSTEM
    assert config.destination_root == tmp_path / "delivery"
    assert config.non_secret_dict() == {
        "backend_type": "filesystem-directory-v1",
        "destination_root": str(tmp_path / "delivery"),
        "provider_type": None,
        "endpoint": None,
        "bucket": None,
        "namespace": None,
        "region": None,
    }


def test_valid_object_storage_config_accepts_only_non_secret_fields() -> None:
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3",
        endpoint="s3.us-east-1.amazonaws.com",
        bucket="exam-artifacts",
        namespace="production",
        region="us-east-1",
    )

    assert config.backend_type is BackendType.OBJECT_STORAGE
    assert config.non_secret_dict() == {
        "backend_type": "object-storage-v1",
        "destination_root": None,
        "provider_type": "s3",
        "endpoint": "s3.us-east-1.amazonaws.com",
        "bucket": "exam-artifacts",
        "namespace": "production",
        "region": "us-east-1",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"backend_type": "filesystem-directory-v1"},
        {
            "backend_type": "object-storage-v1",
            "provider_type": "s3",
            "endpoint": "objects.example.test",
        },
        {
            "backend_type": "object-storage-v1",
            "endpoint": "objects.example.test",
            "bucket": "exam-artifacts",
        },
    ],
)
def test_missing_required_fields_fail_closed(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="requires"):
        DeliveryBackendConfigV1.model_validate(payload)


def test_invalid_backend_type_is_rejected_without_fallback() -> None:
    with pytest.raises(ValidationError):
        DeliveryBackendConfigV1.model_validate(
            {"backend_type": "unknown-backend", "destination_root": "/tmp/delivery"}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "backend_type": "filesystem-directory-v1",
            "destination_root": "/tmp/delivery",
            "bucket": "not-valid-for-filesystem",
        },
        {
            "backend_type": "object-storage-v1",
            "destination_root": "/tmp/delivery",
            "provider_type": "s3",
            "endpoint": "objects.example.test",
            "bucket": "exam-artifacts",
        },
    ],
)
def test_backend_specific_fields_cannot_be_mixed(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DeliveryBackendConfigV1.model_validate(payload)


@pytest.mark.parametrize(
    "secret_field",
    ["access_key", "api_key", "credentials", "password", "secret_key", "token"],
)
def test_secret_fields_are_rejected_and_never_serialized(secret_field: str) -> None:
    payload: dict[str, object] = {
        "backend_type": "object-storage-v1",
        "provider_type": "s3",
        "endpoint": "objects.example.test",
        "bucket": "exam-artifacts",
        secret_field: "credential-value-must-not-escape",
    }

    with pytest.raises(ValidationError):
        DeliveryBackendConfigV1.model_validate(payload)


def test_canonical_non_secret_output_is_stable_for_equivalent_input() -> None:
    first = DeliveryBackendConfigV1.from_mapping(
        {
            "region": "us-east-1",
            "bucket": "exam-artifacts",
            "endpoint": "objects.example.test",
            "provider_type": "s3",
            "backend_type": "object-storage-v1",
        }
    )
    second = DeliveryBackendConfigV1.from_mapping(
        {
            "backend_type": "object-storage-v1",
            "provider_type": " s3 ",
            "endpoint": "objects.example.test",
            "bucket": "exam-artifacts",
            "region": "us-east-1",
        }
    )

    assert first.canonical_non_secret() == second.canonical_non_secret()
    assert first.canonical_json() == second.canonical_json()
    assert first.canonical_sha256() == second.canonical_sha256()


def test_from_mapping_does_not_mutate_caller_input() -> None:
    payload: dict[str, object] = {
        "backend_type": "filesystem-directory-v1",
        "destination_root": "/tmp/delivery",
    }
    before = dict(payload)

    DeliveryBackendConfigV1.from_mapping(payload)

    assert payload == before


def test_canonical_output_contains_no_authority_or_runtime_fields() -> None:
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
    )
    output = config.canonical_json().decode("utf-8")

    assert "credential" not in output
    assert "receipt" not in output
    assert "identity" not in output
    assert "checkpoint" not in output
    assert "retry" not in output
    assert "status" not in output
    assert not hasattr(config, "create_receipt")
    assert not hasattr(config, "delivery_identity")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@objects.example.test",
        "objects.example.test?token=secret",
        "objects.example.test#signed-fragment",
    ],
)
def test_endpoint_rejects_credential_bearing_references(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        DeliveryBackendConfigV1.object_storage(
            provider_type="s3", endpoint=endpoint, bucket="exam-artifacts"
        )
