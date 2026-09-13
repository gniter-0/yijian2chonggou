from __future__ import annotations

import json
from pathlib import Path

from examcard.diagnostics.checkpoint_inspection_v1 import (
    inspect_delivery,
    inspect_run,
    render_inspection,
)
from examcard.gates.release.delivery_v2 import (
    build_delivery_execution_v2,
    build_delivery_receipt_v2,
    build_delivery_request_v2,
    build_delivery_target_v2,
)


def _delivery_run(tmp_path: Path) -> Path:
    run_root = tmp_path / "run-1"
    (run_root / "checkpoints").mkdir(parents=True)
    target = build_delivery_target_v2(
        target_id="target:test",
        target_type="archive",
        destination_reference="offline://fixture",
        destination_config_sha256="a" * 64,
        allowed_scope="publication-package-ready",
    )
    request = build_delivery_request_v2(
        publication_receipt_sha256="b" * 64,
        publication_receipt_file_sha256="c" * 64,
        publication_manifest_sha256="d" * 64,
        publication_manifest_file_sha256="e" * 64,
        package_sha256="f" * 64,
        artifact_count=1,
        target_sha256=target.target_sha256,
    )
    execution = build_delivery_execution_v2(
        delivery_identity=request.delivery_identity,
        attempt_number=1,
        adapter_type="test",
        execution_status="completed",
        started_at="2026-09-13T00:00:00Z",
    )
    receipt = build_delivery_receipt_v2(request=request, delivery_status="completed")
    delivery = run_root / "checkpoints" / "delivery"
    delivery.mkdir()
    for name, model in (
        ("delivery-request-v2.json", request),
        ("delivery-execution-v2.json", execution),
        ("delivery-receipt-v2.json", receipt),
    ):
        (delivery / name).write_bytes(
            json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
        )
    run_state = {
        "run_id": "run-1",
        "exam_id": "exam-1",
        "stages_requested": ["step4"],
        "stage_states": {"step4": {"status": "completed"}},
        "checkpoints": {
            "delivery": {
                "status": "frozen",
                "delivery_status": "completed",
                "delivery_request_sha256": request.request_sha256,
                "delivery_request_file_sha256": "0" * 64,
                "delivery_target_sha256": request.target_sha256,
                "delivery_operation_id": request.delivery_operation_id,
                "delivery_identity": request.delivery_identity,
                "idempotency_key": request.idempotency_key,
                "delivery_execution_sha256": execution.execution_sha256,
                "delivery_execution_file_sha256": "0" * 64,
                "delivery_receipt_sha256": receipt.receipt_sha256,
                "delivery_receipt_file_sha256": "0" * 64,
            }
        },
    }
    (run_root / "run_state.json").write_text(
        json.dumps(run_state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return run_root


def test_same_input_produces_identical_output_bytes(tmp_path: Path) -> None:
    run_root = _delivery_run(tmp_path)
    assert render_inspection(inspect_run(run_root)) == render_inspection(inspect_run(run_root))


def test_inspection_does_not_mutate_checkpoint_or_receipt_bytes(tmp_path: Path) -> None:
    run_root = _delivery_run(tmp_path)
    paths = [run_root / "run_state.json", *sorted((run_root / "checkpoints" / "delivery").iterdir())]
    before = {path: path.read_bytes() for path in paths}
    inspect_run(run_root)
    assert {path: path.read_bytes() for path in paths} == before


def test_unknown_delivery_is_preserved_and_destination_is_not_read(tmp_path: Path) -> None:
    run_root = _delivery_run(tmp_path)
    state_path = run_root / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["checkpoints"]["delivery"]["status"] = "unknown"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = inspect_delivery(run_root)
    assert result["delivery"]["delivery_status"] == "unknown"
    assert result["delivery"]["filesystem_marker"]["observation"] == "not_read"


def test_missing_partial_and_malformed_checkpoints_are_reported(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    missing = inspect_run(run_root)
    assert missing["inspection_status"] == "invalid"
    assert any(item["code"] == "missing_required_file" for item in missing["issues"])

    (run_root / "run_state.json").write_text(
        json.dumps({"run_id": "run", "exam_id": "exam", "stage_states": {}, "checkpoints": {}}),
        encoding="utf-8",
    )
    partial = inspect_run(run_root)
    assert partial["inspection_status"] == "invalid"
    (run_root / "checkpoints").mkdir()
    (run_root / "checkpoints" / "delivery").mkdir()
    (run_root / "checkpoints" / "delivery" / "delivery-request-v2.json").write_text("{", encoding="utf-8")
    malformed = inspect_delivery(run_root)
    assert any(item["code"] == "malformed_json" for item in malformed["issues"])


def test_identity_mismatch_and_forbidden_fields_are_not_emitted(tmp_path: Path) -> None:
    run_root = _delivery_run(tmp_path)
    state_path = run_root / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["checkpoints"]["delivery"]["delivery_identity"] = "1" * 64
    state["run_config"] = {"api_key": "secret", "manual_override": "no"}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = inspect_run(run_root)
    serialized = render_inspection(result).decode("utf-8").casefold()
    assert "api_key" not in serialized
    assert "manual_override" not in serialized
    assert any(item["code"] == "identity_mismatch" for item in result["issues"])


def test_output_does_not_add_authority_fields(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    result = inspect_run(run_root)
    serialized = json.dumps(result).casefold()
    assert "authority" not in serialized
    assert "override" not in serialized
