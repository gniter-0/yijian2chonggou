"""P6 delivery contracts and frozen checkpoint regressions."""
from __future__ import annotations

from pathlib import Path

import pytest

from examcard.gates.release.delivery_v2 import (
    DeliveryAdapterResult,
    DeliveryRequestV2,
    DeliveryTargetV2,
    build_delivery_execution_v2,
    build_delivery_receipt_v2,
    build_delivery_request_v2,
    build_delivery_target_v2,
    derive_delivery_identity,
    derive_delivery_operation_id,
    execute_delivery_v2,
    validate_delivery_request_bindings,
)
from examcard.pipeline.live_checkpoint import (
    materialize_delivery_v2,
    validate_materialized_delivery_v2,
)


HASHES = {
    "receipt": "1" * 64,
    "receipt_file": "2" * 64,
    "manifest": "3" * 64,
    "manifest_file": "4" * 64,
    "package": "5" * 64,
    "config": "6" * 64,
}


def _target() -> DeliveryTargetV2:
    return build_delivery_target_v2(
        target_id="target:test",
        target_type="archive",
        destination_reference="offline://fixture",
        destination_config_sha256=HASHES["config"],
        allowed_scope="publication-package-ready",
    )


def _request() -> DeliveryRequestV2:
    target = _target()
    return build_delivery_request_v2(
        publication_receipt_sha256=HASHES["receipt"],
        publication_receipt_file_sha256=HASHES["receipt_file"],
        publication_manifest_sha256=HASHES["manifest"],
        publication_manifest_file_sha256=HASHES["manifest_file"],
        package_sha256=HASHES["package"],
        artifact_count=2,
        target_sha256=target.target_sha256,
    )


def test_delivery_identity_and_idempotency_are_deterministic() -> None:
    target = _target()
    request = _request()
    assert request.delivery_operation_id == derive_delivery_operation_id(
        publication_receipt_sha256=HASHES["receipt"],
        target_sha256=target.target_sha256,
    )
    assert request.delivery_identity == derive_delivery_identity(
        delivery_operation_id=request.delivery_operation_id,
        package_sha256=HASHES["package"],
        artifact_count=2,
    )
    assert request.idempotency_key == request.delivery_identity
    changed_target = build_delivery_target_v2(
        target_id="target:other",
        target_type="archive",
        destination_reference="offline://other",
        destination_config_sha256=HASHES["config"],
        allowed_scope="publication-package-ready",
    )
    changed = build_delivery_request_v2(
        publication_receipt_sha256=HASHES["receipt"],
        publication_receipt_file_sha256=HASHES["receipt_file"],
        publication_manifest_sha256=HASHES["manifest"],
        publication_manifest_file_sha256=HASHES["manifest_file"],
        package_sha256=HASHES["package"],
        artifact_count=2,
        target_sha256=changed_target.target_sha256,
    )
    assert changed.delivery_identity != request.delivery_identity
    assert changed.idempotency_key != request.idempotency_key


@pytest.mark.parametrize(
    "field",
    [
        "publication_receipt_sha256",
        "publication_receipt_file_sha256",
        "publication_manifest_sha256",
        "publication_manifest_file_sha256",
        "publication_package_sha256",
        "publication_artifact_count",
    ],
)
def test_delivery_request_binds_every_p5_identity_field(field: str) -> None:
    target = _target()
    request = _request()
    publication = {
        "publication_receipt_sha256": HASHES["receipt"],
        "publication_receipt_file_sha256": HASHES["receipt_file"],
        "publication_manifest_sha256": HASHES["manifest"],
        "publication_manifest_file_sha256": HASHES["manifest_file"],
        "publication_package_sha256": HASHES["package"],
        "publication_artifact_count": 2,
    }
    bad = dict(publication)
    bad[field] = ("8" * 64) if field != "publication_artifact_count" else 3
    with pytest.raises(ValueError, match="frozen publication authority"):
        validate_delivery_request_bindings(
            request=request,
            publication_identity=bad,
            target=target,
        )


class _FakeAdapter:
    def __init__(self, result: DeliveryAdapterResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    def deliver(self, *, package_sha256: str, target_sha256: str, idempotency_key: str) -> DeliveryAdapterResult:
        self.calls.append((package_sha256, target_sha256, idempotency_key))
        return self.result


@pytest.mark.parametrize("status", ["success", "failure", "unknown"])
def test_execute_delivery_adapter_statuses_are_preserved(status: str) -> None:
    request = _request()
    execution = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=1,
        adapter_type="fake",
        execution_status="executing",
        started_at="2026-09-12T00:00:00Z",
    )
    adapter = _FakeAdapter(DeliveryAdapterResult(status=status))  # type: ignore[arg-type]
    completed, receipt = execute_delivery_v2(request=request, execution=execution, adapter=adapter)  # type: ignore[arg-type]
    expected = {"success": "completed", "failure": "failed", "unknown": "unknown"}[status]
    assert completed.execution_status == expected
    assert receipt.delivery_status == expected
    assert adapter.calls == [(request.package_sha256, request.target_sha256, request.idempotency_key)]


def test_successful_retry_keeps_the_same_delivery_identity() -> None:
    request = _request()
    first_execution = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=1,
        adapter_type="fake",
        execution_status="executing",
        started_at="2026-09-12T00:00:00Z",
    )
    failed_adapter = _FakeAdapter(DeliveryAdapterResult(status="failure"))
    _, failed_receipt = execute_delivery_v2(request=request, execution=first_execution, adapter=failed_adapter)  # type: ignore[arg-type]
    second_execution = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=2,
        adapter_type="fake",
        execution_status="executing",
        started_at="2026-09-12T00:01:00Z",
    )
    success_adapter = _FakeAdapter(DeliveryAdapterResult(status="success"))
    _, success_receipt = execute_delivery_v2(request=request, execution=second_execution, adapter=success_adapter)  # type: ignore[arg-type]
    assert failed_receipt.delivery_status == "failed"
    assert success_receipt.delivery_status == "completed"
    assert failed_receipt.delivery_identity == success_receipt.delivery_identity == request.delivery_identity
    assert failed_receipt.idempotency_key == success_receipt.idempotency_key == request.idempotency_key


def test_delivery_checkpoint_is_receipt_last_and_frozen_read_only(tmp_path: Path) -> None:
    request = _request()
    prepared = materialize_delivery_v2(run_root=tmp_path, request=request)
    assert prepared["delivery_status"] == "prepared"
    paths = tmp_path / "checkpoints" / "delivery"
    assert (paths / "delivery-request-v2.json").is_file()
    assert not (paths / "delivery-receipt-v2.json").exists()

    executing = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=1,
        adapter_type="fake",
        execution_status="executing",
        started_at="2026-09-12T00:00:00Z",
    )
    materialize_delivery_v2(run_root=tmp_path, request=request, execution=executing)
    completed = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=1,
        adapter_type="fake",
        execution_status="completed",
        started_at="2026-09-12T00:00:00Z",
    )
    receipt = build_delivery_receipt_v2(request=request, delivery_status="completed")
    frozen = materialize_delivery_v2(
        run_root=tmp_path,
        request=request,
        execution=completed,
        receipt=receipt,
        allow_retry_replace=True,
    )
    assert frozen["delivery_status"] == "completed"
    receipt_before = (paths / "delivery-receipt-v2.json").read_bytes()
    assert validate_materialized_delivery_v2(run_root=tmp_path, expected_authority=frozen) == frozen
    assert (paths / "delivery-receipt-v2.json").read_bytes() == receipt_before
    assert materialize_delivery_v2(run_root=tmp_path, request=request, execution=completed, receipt=receipt) == frozen


def test_delivery_conflicting_request_and_missing_frozen_child_fail_closed(tmp_path: Path) -> None:
    request = _request()
    materialize_delivery_v2(run_root=tmp_path, request=request)
    changed = build_delivery_request_v2(
        publication_receipt_sha256=HASHES["receipt"],
        publication_receipt_file_sha256=HASHES["receipt_file"],
        publication_manifest_sha256=HASHES["manifest"],
        publication_manifest_file_sha256=HASHES["manifest_file"],
        package_sha256="7" * 64,
        artifact_count=2,
        target_sha256=request.target_sha256,
    )
    with pytest.raises(Exception, match="conflicts"):
        materialize_delivery_v2(run_root=tmp_path, request=changed)


def test_frozen_delivery_missing_child_is_not_recreated(tmp_path: Path) -> None:
    request = _request()
    execution = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=1,
        adapter_type="fake",
        execution_status="completed",
        started_at="2026-09-12T00:00:00Z",
    )
    receipt = build_delivery_receipt_v2(request=request, delivery_status="completed")
    frozen = materialize_delivery_v2(
        run_root=tmp_path,
        request=request,
        execution=execution,
        receipt=receipt,
    )
    child = tmp_path / "checkpoints" / "delivery" / "delivery-execution-v2.json"
    child.unlink()
    with pytest.raises(Exception, match="missing"):
        validate_materialized_delivery_v2(run_root=tmp_path, expected_authority=frozen)
    assert not child.exists()
