"""Deterministic contracts for executing an already-authorized publication.

P6 is deliberately downstream of :mod:`publication_v2`.  This module owns
only immutable delivery identities and the adapter boundary; it never reads
files, changes publication authority, or performs network I/O itself.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Final, Literal, Protocol, TypeAlias

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash, hash_model, sha256_text
from ...materials.schema import StrictModel

DELIVERY_TARGET_V2_SCHEMA: Final = "delivery-target/2.0"
DELIVERY_REQUEST_V2_SCHEMA: Final = "delivery-request/2.0"
DELIVERY_EXECUTION_V2_SCHEMA: Final = "delivery-execution/2.0"
DELIVERY_RECEIPT_V2_SCHEMA: Final = "delivery-receipt/2.0"

DeliveryExecutionStatus: TypeAlias = Literal["prepared", "executing", "completed", "failed", "unknown"]
DeliveryReceiptStatus: TypeAlias = Literal["completed", "failed", "unknown"]
DeliveryAdapterStatus: TypeAlias = Literal["success", "failure", "unknown"]


def _self_hash(model: StrictModel, field: str) -> str:
    return canonical_hash(model.model_dump(mode="json", exclude={"schema_version", field}))


def derive_delivery_operation_id(*, publication_receipt_sha256: str, target_sha256: str) -> str:
    """Derive the stable operation identity from the P5 receipt and target."""

    return sha256_text(f"{publication_receipt_sha256}{target_sha256}")


def derive_delivery_identity(*, delivery_operation_id: str, package_sha256: str, artifact_count: int) -> str:
    """Derive the stable identity of the exact package delivery object."""

    return sha256_text(f"{delivery_operation_id}{package_sha256}{artifact_count}")


def derive_idempotency_key(*, delivery_identity: str) -> str:
    """The adapter key is intentionally identical to the delivery identity."""

    return delivery_identity


class DeliveryTargetV2(StrictModel):
    schema_version: Literal["delivery-target/2.0"] = DELIVERY_TARGET_V2_SCHEMA
    target_id: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    destination_reference: str = Field(min_length=1)
    destination_config_sha256: str = Field(pattern=SHA256_HEX)
    allowed_scope: str = Field(min_length=1)
    target_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_target_hash(self) -> "DeliveryTargetV2":
        if self.target_sha256 != _self_hash(self, "target_sha256"):
            raise ValueError("delivery target hash mismatch")
        return self


class DeliveryRequestV2(StrictModel):
    schema_version: Literal["delivery-request/2.0"] = DELIVERY_REQUEST_V2_SCHEMA
    publication_receipt_sha256: str = Field(pattern=SHA256_HEX)
    publication_receipt_file_sha256: str = Field(pattern=SHA256_HEX)
    publication_manifest_sha256: str = Field(pattern=SHA256_HEX)
    publication_manifest_file_sha256: str = Field(pattern=SHA256_HEX)
    package_sha256: str = Field(pattern=SHA256_HEX)
    artifact_count: int = Field(ge=0)
    target_sha256: str = Field(pattern=SHA256_HEX)
    delivery_operation_id: str = Field(pattern=SHA256_HEX)
    delivery_identity: str = Field(pattern=SHA256_HEX)
    idempotency_key: str = Field(pattern=SHA256_HEX)
    request_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_request_identity(self) -> "DeliveryRequestV2":
        operation_id = derive_delivery_operation_id(
            publication_receipt_sha256=self.publication_receipt_sha256,
            target_sha256=self.target_sha256,
        )
        if self.delivery_operation_id != operation_id:
            raise ValueError("delivery operation identity mismatch")
        delivery_identity = derive_delivery_identity(
            delivery_operation_id=operation_id,
            package_sha256=self.package_sha256,
            artifact_count=self.artifact_count,
        )
        if self.delivery_identity != delivery_identity:
            raise ValueError("delivery identity mismatch")
        if self.idempotency_key != derive_idempotency_key(delivery_identity=delivery_identity):
            raise ValueError("delivery idempotency key mismatch")
        if self.request_sha256 != _self_hash(self, "request_sha256"):
            raise ValueError("delivery request hash mismatch")
        return self


class DeliveryExecutionV2(StrictModel):
    schema_version: Literal["delivery-execution/2.0"] = DELIVERY_EXECUTION_V2_SCHEMA
    delivery_identity: str = Field(pattern=SHA256_HEX)
    attempt_number: int = Field(ge=1)
    adapter_type: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    execution_status: DeliveryExecutionStatus
    execution_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_execution_hash(self) -> "DeliveryExecutionV2":
        if self.execution_sha256 != _self_hash(self, "execution_sha256"):
            raise ValueError("delivery execution hash mismatch")
        return self


class DeliveryReceiptV2(StrictModel):
    schema_version: Literal["delivery-receipt/2.0"] = DELIVERY_RECEIPT_V2_SCHEMA
    delivery_operation_id: str = Field(pattern=SHA256_HEX)
    delivery_identity: str = Field(pattern=SHA256_HEX)
    idempotency_key: str = Field(pattern=SHA256_HEX)
    publication_receipt_sha256: str = Field(pattern=SHA256_HEX)
    target_sha256: str = Field(pattern=SHA256_HEX)
    package_sha256: str = Field(pattern=SHA256_HEX)
    artifact_count: int = Field(ge=0)
    delivery_status: DeliveryReceiptStatus
    remote_reference: str | None = None
    receipt_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_receipt_identity(self) -> "DeliveryReceiptV2":
        operation_id = derive_delivery_operation_id(
            publication_receipt_sha256=self.publication_receipt_sha256,
            target_sha256=self.target_sha256,
        )
        if self.delivery_operation_id != operation_id:
            raise ValueError("delivery receipt operation identity mismatch")
        delivery_identity = derive_delivery_identity(
            delivery_operation_id=operation_id,
            package_sha256=self.package_sha256,
            artifact_count=self.artifact_count,
        )
        if self.delivery_identity != delivery_identity or self.idempotency_key != delivery_identity:
            raise ValueError("delivery receipt identity mismatch")
        if self.receipt_sha256 != _self_hash(self, "receipt_sha256"):
            raise ValueError("delivery receipt hash mismatch")
        return self


class DeliveryAdapterResult(StrictModel):
    """Normalized result returned by an adapter; no network behavior here."""

    status: DeliveryAdapterStatus
    remote_reference: str | None = None


class DeliveryAdapter(Protocol):
    """External side-effect boundary supplied by the caller."""

    def deliver(
        self,
        *,
        package_sha256: str,
        target_sha256: str,
        idempotency_key: str,
    ) -> DeliveryAdapterResult | Mapping[str, Any]:
        """Attempt delivery using the supplied immutable identities."""



class DeliveryStatusLookup(Protocol):
    """Optional status lookup surface for adapters that support recovery."""

    def lookup(self, *, idempotency_key: str) -> DeliveryAdapterResult | Mapping[str, Any] | None:
        """Resolve an earlier unknown attempt by its same key."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_delivery_target_v2(
    *,
    target_id: str,
    target_type: str,
    destination_reference: str,
    destination_config_sha256: str,
    allowed_scope: str,
) -> DeliveryTargetV2:
    return hash_model(
        DeliveryTargetV2,
        {
            "target_id": target_id,
            "target_type": target_type,
            "destination_reference": destination_reference,
            "destination_config_sha256": destination_config_sha256,
            "allowed_scope": allowed_scope,
        },
        "target_sha256",
    )


def build_delivery_request_v2(
    *,
    publication_receipt_sha256: str,
    publication_receipt_file_sha256: str,
    publication_manifest_sha256: str,
    publication_manifest_file_sha256: str,
    package_sha256: str,
    artifact_count: int,
    target_sha256: str,
) -> DeliveryRequestV2:
    operation_id = derive_delivery_operation_id(
        publication_receipt_sha256=publication_receipt_sha256,
        target_sha256=target_sha256,
    )
    identity = derive_delivery_identity(
        delivery_operation_id=operation_id,
        package_sha256=package_sha256,
        artifact_count=artifact_count,
    )
    return hash_model(
        DeliveryRequestV2,
        {
            "publication_receipt_sha256": publication_receipt_sha256,
            "publication_receipt_file_sha256": publication_receipt_file_sha256,
            "publication_manifest_sha256": publication_manifest_sha256,
            "publication_manifest_file_sha256": publication_manifest_file_sha256,
            "package_sha256": package_sha256,
            "artifact_count": artifact_count,
            "target_sha256": target_sha256,
            "delivery_operation_id": operation_id,
            "delivery_identity": identity,
            "idempotency_key": identity,
        },
        "request_sha256",
    )


def build_delivery_execution_v2(
    *,
    delivery_identity: str,
    attempt_number: int,
    adapter_type: str,
    execution_status: DeliveryExecutionStatus,
    started_at: str | None = None,
) -> DeliveryExecutionV2:
    return hash_model(
        DeliveryExecutionV2,
        {
            "delivery_identity": delivery_identity,
            "attempt_number": attempt_number,
            "adapter_type": adapter_type,
            "started_at": started_at or _utc_now_iso(),
            "execution_status": execution_status,
        },
        "execution_sha256",
    )


def build_delivery_receipt_v2(
    *,
    request: DeliveryRequestV2,
    delivery_status: DeliveryReceiptStatus,
    remote_reference: str | None = None,
) -> DeliveryReceiptV2:
    return hash_model(
        DeliveryReceiptV2,
        {
            "delivery_operation_id": request.delivery_operation_id,
            "delivery_identity": request.delivery_identity,
            "idempotency_key": request.idempotency_key,
            "publication_receipt_sha256": request.publication_receipt_sha256,
            "target_sha256": request.target_sha256,
            "package_sha256": request.package_sha256,
            "artifact_count": request.artifact_count,
            "delivery_status": delivery_status,
            "remote_reference": remote_reference,
        },
        "receipt_sha256",
    )


def normalize_adapter_result(value: DeliveryAdapterResult | Mapping[str, Any] | None) -> DeliveryAdapterResult | None:
    if value is None:
        return None
    if isinstance(value, DeliveryAdapterResult):
        return value
    if isinstance(value, Mapping):
        return DeliveryAdapterResult.model_validate(dict(value))
    raise TypeError("delivery adapter result must be DeliveryAdapterResult or a mapping")


def delivery_status_from_adapter(status: DeliveryAdapterStatus) -> DeliveryReceiptStatus:
    if status == "success":
        return "completed"
    if status == "failure":
        return "failed"
    return "unknown"


def execute_delivery_v2(
    *,
    request: DeliveryRequestV2,
    execution: DeliveryExecutionV2,
    adapter: DeliveryAdapter,
) -> tuple[DeliveryExecutionV2, DeliveryReceiptV2]:
    """Run one adapter attempt and derive its receipt without persistence."""

    if execution.delivery_identity != request.delivery_identity:
        raise ValueError("delivery execution identity does not match request")
    result = normalize_adapter_result(
        adapter.deliver(
            package_sha256=request.package_sha256,
            target_sha256=request.target_sha256,
            idempotency_key=request.idempotency_key,
        )
    )
    if result is None:
        raise ValueError("delivery adapter returned no result")
    receipt_status = delivery_status_from_adapter(result.status)
    completed_execution = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=execution.attempt_number,
        adapter_type=execution.adapter_type,
        started_at=execution.started_at,
        execution_status=receipt_status,
    )
    receipt = build_delivery_receipt_v2(
        request=request,
        delivery_status=receipt_status,
        remote_reference=result.remote_reference,
    )
    return completed_execution, receipt


def validate_delivery_request_bindings(
    *,
    request: DeliveryRequestV2,
    publication_identity: Mapping[str, Any],
    target: DeliveryTargetV2,
) -> None:
    """Fail closed unless a request exactly matches frozen P5 and target data."""

    try:
        expected = build_delivery_request_v2(
            publication_receipt_sha256=str(publication_identity["publication_receipt_sha256"]),
            publication_receipt_file_sha256=str(publication_identity["publication_receipt_file_sha256"]),
            publication_manifest_sha256=str(publication_identity["publication_manifest_sha256"]),
            publication_manifest_file_sha256=str(publication_identity["publication_manifest_file_sha256"]),
            package_sha256=str(publication_identity["publication_package_sha256"]),
            artifact_count=int(publication_identity["publication_artifact_count"]),
            target_sha256=target.target_sha256,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("frozen publication authority is missing delivery bindings") from error
    if request.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise ValueError("delivery request does not match frozen publication authority")


__all__ = (
    "DELIVERY_TARGET_V2_SCHEMA",
    "DELIVERY_REQUEST_V2_SCHEMA",
    "DELIVERY_EXECUTION_V2_SCHEMA",
    "DELIVERY_RECEIPT_V2_SCHEMA",
    "DeliveryExecutionStatus",
    "DeliveryReceiptStatus",
    "DeliveryAdapterStatus",
    "DeliveryTargetV2",
    "DeliveryRequestV2",
    "DeliveryExecutionV2",
    "DeliveryReceiptV2",
    "DeliveryAdapterResult",
    "DeliveryAdapter",
    "DeliveryStatusLookup",
    "derive_delivery_operation_id",
    "derive_delivery_identity",
    "derive_idempotency_key",
    "build_delivery_target_v2",
    "build_delivery_request_v2",
    "build_delivery_execution_v2",
    "build_delivery_receipt_v2",
    "normalize_adapter_result",
    "delivery_status_from_adapter",
    "execute_delivery_v2",
    "validate_delivery_request_bindings",
)
