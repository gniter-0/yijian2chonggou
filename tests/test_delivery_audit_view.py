from __future__ import annotations

import json
from pathlib import Path

import pytest

from examcard.common.hashing import hash_model
from examcard.delivery.filesystem_v1 import (
    FILESYSTEM_DELIVERY_MARKER_FILENAME,
    FilesystemDeliveryMarker,
    build_filesystem_delivery_target,
)
from examcard.diagnostics.checkpoint_inspection_v1 import render_inspection
from examcard.diagnostics.delivery_audit_v1 import (
    DeliveryAuditError,
    inspect_delivery_audit,
)
from examcard.gates.release.delivery_v2 import (
    DeliveryExecutionV2,
    DeliveryReceiptV2,
    DeliveryRequestV2,
    build_delivery_execution_v2,
    build_delivery_receipt_v2,
    build_delivery_request_v2,
)
from examcard.pipeline.live_checkpoint import materialize_delivery_v2


HASHES = {
    "receipt": "1" * 64,
    "receipt_file": "2" * 64,
    "manifest": "3" * 64,
    "manifest_file": "4" * 64,
    "package": "5" * 64,
}


def _write_marker(destination: Path, request: DeliveryRequestV2, *, target_sha256: str | None = None) -> None:
    marker = hash_model(
        FilesystemDeliveryMarker,
        {
            "delivery_identity": request.delivery_identity,
            "idempotency_key": request.idempotency_key,
            "package_sha256": request.package_sha256,
            "target_sha256": target_sha256 or request.target_sha256,
            "publication_manifest_sha256": request.publication_manifest_sha256,
            "publication_manifest_file_sha256": request.publication_manifest_file_sha256,
            "publication_receipt_sha256": request.publication_receipt_sha256,
            "publication_receipt_file_sha256": request.publication_receipt_file_sha256,
            "authorization_sha256": "6" * 64,
            "authorization_file_sha256": "7" * 64,
            "artifact_count": request.artifact_count,
            "files": (),
        },
        "marker_sha256",
    )
    target = destination / request.idempotency_key
    target.mkdir()
    (target / FILESYSTEM_DELIVERY_MARKER_FILENAME).write_bytes(
        json.dumps(marker.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _fixture(
    tmp_path: Path,
    *,
    status: str = "completed",
    marker: bool = False,
    marker_target_sha256: str | None = None,
) -> tuple[Path, Path, DeliveryRequestV2, DeliveryExecutionV2, DeliveryReceiptV2 | None]:
    run_root = tmp_path / "run"
    destination = tmp_path / "destination"
    run_root.mkdir()
    destination.mkdir()
    target = build_filesystem_delivery_target(destination)
    request = build_delivery_request_v2(
        publication_receipt_sha256=HASHES["receipt"],
        publication_receipt_file_sha256=HASHES["receipt_file"],
        publication_manifest_sha256=HASHES["manifest"],
        publication_manifest_file_sha256=HASHES["manifest_file"],
        package_sha256=HASHES["package"],
        artifact_count=1,
        target_sha256=target.target_sha256,
    )
    execution = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=2,
        adapter_type="filesystem-directory-v1",
        execution_status=status,  # type: ignore[arg-type]
        started_at="2026-09-13T00:00:00Z",
    )
    receipt = (
        build_delivery_receipt_v2(request=request, delivery_status=status)  # type: ignore[arg-type]
        if status in {"completed", "failed", "unknown"}
        else None
    )
    materialize_delivery_v2(
        run_root=run_root,
        request=request,
        execution=execution,
        receipt=receipt,
    )
    if marker:
        _write_marker(destination, request, target_sha256=marker_target_sha256)
    return run_root, destination, request, execution, receipt


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_audit_output_is_deterministic_and_allow_listed(tmp_path: Path) -> None:
    run_root, destination, _request, _execution, _receipt = _fixture(tmp_path, marker=True)
    first = render_inspection(inspect_delivery_audit(run_root, destination_root=destination))
    second = render_inspection(inspect_delivery_audit(run_root, destination_root=destination))
    assert first == second
    assert set(json.loads(first)) == {
        "delivery_operation_id",
        "delivery_identity",
        "execution_status",
        "receipt_status",
        "attempt_number",
        "adapter_type",
        "marker_observation",
        "history_availability",
        "reconciliation_events",
    }
    assert b"current_snapshot_only" in first
    assert b"not_persisted" in first


def test_audit_does_not_mutate_delivery_or_destination(tmp_path: Path) -> None:
    run_root, destination, _request, _execution, _receipt = _fixture(tmp_path, marker=True)
    before_run = _snapshot(run_root)
    before_destination = _snapshot(destination)
    inspect_delivery_audit(run_root, destination_root=destination)
    assert _snapshot(run_root) == before_run
    assert _snapshot(destination) == before_destination


def test_audit_never_invokes_delivery_or_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root, destination, _request, _execution, _receipt = _fixture(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("delivery adapter surface must not be called")

    from examcard.delivery.filesystem_v1 import FilesystemDirectoryDeliveryAdapter

    monkeypatch.setattr(FilesystemDirectoryDeliveryAdapter, "deliver", fail)
    monkeypatch.setattr(FilesystemDirectoryDeliveryAdapter, "lookup", fail)
    result = inspect_delivery_audit(run_root, destination_root=destination)
    assert result["execution_status"] == "completed"


def test_unknown_execution_and_receipt_status_are_preserved(tmp_path: Path) -> None:
    run_root, _destination, _request, _execution, _receipt = _fixture(tmp_path, status="unknown")
    result = inspect_delivery_audit(run_root)
    assert result["execution_status"] == "unknown"
    assert result["receipt_status"] == "unknown"
    assert result["history_availability"] == "current_snapshot_only"


def test_missing_destination_is_not_observed(tmp_path: Path) -> None:
    run_root, _destination, _request, _execution, _receipt = _fixture(tmp_path)
    result = inspect_delivery_audit(run_root)
    assert result["marker_observation"] == {
        "status": "not_observed",
        "reason": "destination_root_not_supplied",
    }


def test_marker_mismatch_is_reported_without_promotion(tmp_path: Path) -> None:
    run_root, destination, request, _execution, _receipt = _fixture(
        tmp_path,
        marker=True,
        marker_target_sha256="0" * 64,
    )
    result = inspect_delivery_audit(run_root, destination_root=destination)
    observation = result["marker_observation"]
    assert observation["status"] == "mismatch"
    assert "target_sha256" in observation["mismatch_fields"]
    assert result["receipt_status"] == "completed"
    assert result["delivery_identity"] == request.delivery_identity


def test_destination_identity_mismatch_is_observed_safely(tmp_path: Path) -> None:
    run_root, _destination, _request, _execution, _receipt = _fixture(tmp_path)
    wrong_destination = tmp_path / "wrong-destination"
    wrong_destination.mkdir()
    result = inspect_delivery_audit(run_root, destination_root=wrong_destination)
    assert result["marker_observation"] == {"status": "destination_mismatch"}


def test_missing_delivery_is_an_empty_observation(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    result = inspect_delivery_audit(run_root)
    assert result["delivery_operation_id"] is None
    assert result["marker_observation"]["status"] == "not_observed"


def test_invalid_delivery_evidence_fails_without_writing(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    delivery = run_root / "checkpoints" / "delivery"
    delivery.mkdir(parents=True)
    (delivery / "delivery-request-v2.json").write_text("{", encoding="utf-8")
    before = _snapshot(run_root)
    with pytest.raises(DeliveryAuditError):
        inspect_delivery_audit(run_root)
    assert _snapshot(run_root) == before


def test_forbidden_fields_are_not_emitted(tmp_path: Path) -> None:
    run_root, destination, _request, _execution, _receipt = _fixture(tmp_path, marker=True)
    serialized = render_inspection(inspect_delivery_audit(run_root, destination_root=destination)).decode().casefold()
    for forbidden in ("secret", "credential", "authorization", "token", "manual_override", "override"):
        assert forbidden not in serialized


def test_existing_inspection_cli_remains_compatible(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    from migration import inspect_checkpoint

    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "run_state.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "exam_id": "exam-1",
                "stages_requested": ["step1"],
                "stage_states": {"step1": {"status": "completed"}},
                "checkpoints": {},
            }
        ),
        encoding="utf-8",
    )
    assert inspect_checkpoint.main(["inspect", "run", "--run-root", str(run_root)]) == 0
    output, error = capfd.readouterr()
    assert error == ""
    assert json.loads(output)["inspection_mode"] == "observation_only"


def test_audit_cli_requires_explicit_run_root() -> None:
    from migration import inspect_checkpoint

    with pytest.raises(SystemExit) as error:
        inspect_checkpoint._parse_args(["inspect", "delivery-audit"])
    assert error.value.code == 2
