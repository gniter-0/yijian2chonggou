"""Read-only observation of the frozen P6/P7 delivery boundary.

This module reports the durable delivery snapshot already written by the
pipeline.  It never constructs an adapter, calls delivery or reconciliation,
persists a result, or treats a marker as a new authority decision.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from ..gates.release.delivery_v2 import (
    DeliveryExecutionV2,
    DeliveryReceiptV2,
    DeliveryRequestV2,
    build_delivery_receipt_v2,
)

_DELIVERY_FILES: Final[dict[str, str]] = {
    "request": "delivery-request-v2.json",
    "execution": "delivery-execution-v2.json",
    "receipt": "delivery-receipt-v2.json",
}
_MARKER_FILENAME: Final[str] = "delivery-complete-v1.json"
_SHA256_FIELDS: Final[tuple[str, ...]] = (
    "delivery_identity",
    "package_sha256",
    "target_sha256",
    "publication_manifest_sha256",
    "publication_manifest_file_sha256",
    "publication_receipt_sha256",
    "publication_receipt_file_sha256",
)


class DeliveryAuditError(ValueError):
    """The explicit delivery evidence cannot be inspected safely."""


def _empty_audit() -> dict[str, Any]:
    return {
        "delivery_operation_id": None,
        "delivery_identity": None,
        "execution_status": None,
        "receipt_status": None,
        "attempt_number": None,
        "adapter_type": None,
        "marker_observation": {
            "status": "not_observed",
            "reason": "delivery_request_missing",
        },
        "history_availability": "current_snapshot_only",
        "reconciliation_events": "not_persisted",
    }


def _require_run_root(run_root: Path) -> Path:
    root = Path(run_root)
    if root.is_symlink() or not root.is_dir():
        raise DeliveryAuditError("run root must be an existing regular directory")
    return root


def _read_optional_model(
    path: Path,
    model_type: type[Any],
    label: str,
) -> Any | None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise DeliveryAuditError(f"{label} is not a regular file")
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        return model_type.model_validate_json(raw)
    except (OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise DeliveryAuditError(f"{label} is invalid") from error


def _read_checkpoint_status(root: Path) -> str | None:
    """Read only the delivery status needed to preserve an unknown outcome."""

    path = root / "run_state.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    checkpoints = value.get("checkpoints")
    delivery = checkpoints.get("delivery") if isinstance(checkpoints, Mapping) else None
    status = delivery.get("status") if isinstance(delivery, Mapping) else None
    return status if isinstance(status, str) else None


def _has_symlink_component(root: Path, relative: str) -> bool:
    current = root
    for component in PurePosixPath(relative).parts:
        current /= component
        if current.is_symlink():
            return True
    return False


def _marker_observation(
    run_root: Path,
    request: DeliveryRequestV2,
    destination_root: Path | None,
) -> dict[str, Any]:
    if destination_root is None:
        return {"status": "not_observed", "reason": "destination_root_not_supplied"}

    destination = Path(destination_root)
    if destination.is_symlink() or not destination.is_dir():
        return {"status": "not_observed", "reason": "destination_unavailable"}

    try:
        destination_resolved = destination.resolve(strict=True)
        checkpoints_resolved = (run_root / "checkpoints").resolve(strict=False)
        destination_resolved.relative_to(checkpoints_resolved)
        return {"status": "not_observed", "reason": "destination_inside_run_checkpoints"}
    except ValueError:
        pass
    except OSError:
        return {"status": "not_observed", "reason": "destination_unavailable"}

    # The public P7 target constructor is identity derivation only.  The
    # adapter and its deliver/lookup surfaces are intentionally never used.
    try:
        from ..delivery.filesystem_v1 import build_filesystem_delivery_target

        target = build_filesystem_delivery_target(destination)
    except (OSError, TypeError, ValueError):
        return {"status": "not_observed", "reason": "destination_identity_unavailable"}
    if target.target_sha256 != request.target_sha256:
        return {"status": "destination_mismatch"}

    relative_marker = f"{request.idempotency_key}/{_MARKER_FILENAME}"
    if _has_symlink_component(destination, relative_marker):
        return {"status": "unsafe", "relative_path": relative_marker}
    marker_path = destination / request.idempotency_key / _MARKER_FILENAME
    if not marker_path.exists():
        return {"status": "missing", "relative_path": relative_marker}
    if marker_path.is_symlink() or not marker_path.is_file():
        return {"status": "unsafe", "relative_path": relative_marker}

    try:
        from ..delivery.filesystem_v1 import FilesystemDeliveryMarker

        marker = FilesystemDeliveryMarker.model_validate_json(marker_path.read_bytes())
    except (OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        return {"status": "invalid", "relative_path": relative_marker}

    expected = {
        "delivery_identity": request.delivery_identity,
        "package_sha256": request.package_sha256,
        "target_sha256": request.target_sha256,
        "publication_manifest_sha256": request.publication_manifest_sha256,
        "publication_manifest_file_sha256": request.publication_manifest_file_sha256,
        "publication_receipt_sha256": request.publication_receipt_sha256,
        "publication_receipt_file_sha256": request.publication_receipt_file_sha256,
    }
    mismatches = [
        field for field in _SHA256_FIELDS if getattr(marker, field) != expected[field]
    ]
    if marker.idempotency_key != request.idempotency_key:
        mismatches.append("idempotency_key")
    if marker.artifact_count != request.artifact_count:
        mismatches.append("artifact_count")
    result: dict[str, Any] = {
        "relative_path": relative_marker,
        "marker_sha256": marker.marker_sha256,
    }
    if mismatches:
        result["status"] = "mismatch"
        result["mismatch_fields"] = sorted(mismatches)
    else:
        result["status"] = "present_matching"
    return result


def _validate_bindings(
    request: DeliveryRequestV2,
    execution: DeliveryExecutionV2 | None,
    receipt: DeliveryReceiptV2 | None,
) -> None:
    if execution is not None and execution.delivery_identity != request.delivery_identity:
        raise DeliveryAuditError("delivery execution identity does not match request")
    if receipt is None:
        return
    if execution is None:
        raise DeliveryAuditError("delivery receipt exists without execution")
    expected = build_delivery_receipt_v2(
        request=request,
        delivery_status=receipt.delivery_status,
        remote_reference=receipt.remote_reference,
    )
    if receipt.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise DeliveryAuditError("delivery receipt identity does not match request")
    if receipt.delivery_status != execution.execution_status:
        raise DeliveryAuditError("delivery receipt status does not match execution")


def inspect_delivery_audit(
    run_root: Path,
    *,
    destination_root: Path | None = None,
) -> dict[str, Any]:
    """Return an allow-listed delivery snapshot without changing any state."""

    root = _require_run_root(run_root)
    checkpoint_dir = root / "checkpoints" / "delivery"
    request = _read_optional_model(
        checkpoint_dir / _DELIVERY_FILES["request"],
        DeliveryRequestV2,
        "delivery request",
    )
    if request is None:
        return _empty_audit()

    execution = _read_optional_model(
        checkpoint_dir / _DELIVERY_FILES["execution"],
        DeliveryExecutionV2,
        "delivery execution",
    )
    receipt = _read_optional_model(
        checkpoint_dir / _DELIVERY_FILES["receipt"],
        DeliveryReceiptV2,
        "delivery receipt",
    )
    _validate_bindings(request, execution, receipt)

    checkpoint_status = _read_checkpoint_status(root)
    execution_status = execution.execution_status if execution is not None else None
    # A persisted unknown checkpoint is never promoted by observation.  When
    # no receipt exists, retain unknown even if an older execution snapshot is
    # still present.
    if checkpoint_status == "unknown" and receipt is None:
        execution_status = "unknown"

    return {
        "delivery_operation_id": request.delivery_operation_id,
        "delivery_identity": request.delivery_identity,
        "execution_status": execution_status,
        "receipt_status": receipt.delivery_status if receipt is not None else None,
        "attempt_number": execution.attempt_number if execution is not None else None,
        "adapter_type": execution.adapter_type if execution is not None else None,
        "marker_observation": _marker_observation(root, request, destination_root),
        "history_availability": "current_snapshot_only",
        "reconciliation_events": "not_persisted",
    }


__all__ = ("DeliveryAuditError", "inspect_delivery_audit")
