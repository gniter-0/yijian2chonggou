"""Observation-only inspection of the live pipeline checkpoint boundary.

The inspector intentionally does not use ``RunState.load_or_create`` or any
materialization helper.  Those APIs are part of the execution boundary and
may write state.  This module reads an explicitly supplied run directory,
validates the immutable P4-P6 documents when they are present, and emits a
small allow-listed summary.  Its output is evidence for an operator, never a
checkpoint, receipt, approval, or authority decision.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from ..gates.release.delivery_v2 import (
    DeliveryExecutionV2,
    DeliveryReceiptV2,
    DeliveryRequestV2,
    build_delivery_receipt_v2,
)
from ..gates.release.final_release_v2 import (
    FinalReleaseAuditV2,
    FinalReleaseBundleV2,
    FinalReleaseClosureReceiptV2,
    FinalReleaseExclusionLedgerV2,
)
from ..gates.release.publication_v2 import (
    PublicationAuthorizationV2,
    PublicationManifestV2,
    PublicationReceiptV2,
)


INSPECTION_SCHEMA_VERSION: Final = "checkpoint-inspection/1.0"
CHECKPOINT_NAMES: Final[tuple[str, ...]] = (
    "admission",
    "echo_review",
    "image_review",
    "final_release",
    "publication",
    "delivery",
)

_STAGE_ORDER: Final[tuple[str, ...]] = ("step1", "step2a", "step2b", "step3", "step4")
_STAGE_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "running", "completed", "completed_with_failures", "waiting_human", "skipped", "failed"}
)
_CHECKPOINT_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "waiting_human", "ingested", "refused", "tampered", "prepared", "executing", "unknown", "frozen"}
)
_DELIVERY_STATUSES: Final[frozenset[str]] = frozenset(
    {"absent", "prepared", "executing", "completed", "failed", "unknown", "invalid"}
)
_SAFE_STOP_REASONS: Final[frozenset[str]] = frozenset(
    {
        "completed",
        "delivery_failed",
        "delivery_outcome_unknown",
        "gate_refused_admission",
        "gate_refused_echo_review",
        "gate_refused_publication",
        "missing_upstream_artifact",
        "preflight_step2_unmet",
        "preflight_step3_unmet",
        "waiting_human_admission",
        "waiting_human_echo_review",
        "waiting_human_image_review",
        "waiting_human_publication",
    }
)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_SHA256_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "admission_audit_sha256",
        "admission_authority_sha256",
        "authorization_manifest_sha256",
        "delivery_execution_sha256",
        "delivery_execution_file_sha256",
        "delivery_identity",
        "delivery_operation_id",
        "delivery_receipt_sha256",
        "delivery_receipt_file_sha256",
        "delivery_request_sha256",
        "delivery_request_file_sha256",
        "delivery_target_sha256",
        "final_release_audit_sha256",
        "final_release_audit_file_sha256",
        "final_release_bundle_sha256",
        "final_release_bundle_file_sha256",
        "final_release_exclusion_sha256",
        "final_release_exclusion_file_sha256",
        "final_release_receipt_sha256",
        "final_release_receipt_file_sha256",
        "idempotency_key",
        "image_review_source_binding_sha256",
        "manifest_sha256",
        "manifest_file_sha256",
        "materialization_receipt_sha256",
        "publication_authorization_sha256",
        "publication_authorization_file_sha256",
        "publication_manifest_sha256",
        "publication_manifest_file_sha256",
        "publication_package_sha256",
        "publication_receipt_sha256",
        "publication_receipt_file_sha256",
        "queue_sha256",
        "request_manifest_file_sha256",
        "review_sheet_sha256",
        "source_binding_sha256",
    }
)
_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        *_SHA256_FIELDS,
        "artifact_count",
        "approved_count",
        "decision_count",
        "delivery_status",
        "final_release_status",
        "manual_review_count",
        "publication_artifact_count",
        "publication_status",
        "rejected_count",
        "released_count",
        "status",
    }
)
_CHECKPOINT_FILES: Final[dict[str, tuple[tuple[str, str, bool], ...]]] = {
    "admission": (
        ("queue", "checkpoints/admission/queue.json", True),
        ("progress", "checkpoints/admission/progress.json", False),
        ("decision_manifest", "checkpoints/admission/inbox/review-browser-decisions.json", False),
        ("ingest_audit", "checkpoints/admission/ingest-audit.json", False),
    ),
    "echo_review": (
        ("review_sheet", "checkpoints/echo_review/echo_step3_review_sheet_v2.json", False),
        ("receipt", "checkpoints/echo_review/echo-step3-review-receipt-v2.json", False),
        ("release", "checkpoints/echo_review/echo-batch-release-v2.json", False),
    ),
    "image_review": (
        ("review_sheet", "checkpoints/image_review/image_review_sheet.json", False),
        ("authorization_manifest", "checkpoints/image_review/image-authorization-manifest.json", False),
        ("materialization_receipt", "checkpoints/image_review/image-review-materialization-v2.json", False),
        ("reviewed_fixture", "checkpoints/image_review/reviewed-image-fixture-v2.json", False),
        ("post_review_queue", "checkpoints/image_review/post-review-queue-v2.json", False),
        ("admission_audit", "checkpoints/image_review/image-admission-audit-v2.json", False),
    ),
    "final_release": (
        ("bundle", "checkpoints/final_release/final-release-bundle-v2.json", False),
        ("exclusion", "checkpoints/final_release/final-release-exclusion-ledger-v2.json", True),
        ("audit", "checkpoints/final_release/final-release-audit-v2.json", True),
        ("receipt", "checkpoints/final_release/final-release-closure-v2.json", True),
    ),
    "publication": (
        ("manifest", "checkpoints/publication/publication-manifest-v2.json", True),
        ("authorization", "checkpoints/publication/publication-authorization-v2.json", False),
        ("receipt", "checkpoints/publication/publication-receipt-v2.json", False),
    ),
    "delivery": (
        ("request", "checkpoints/delivery/delivery-request-v2.json", True),
        ("execution", "checkpoints/delivery/delivery-execution-v2.json", True),
        ("receipt", "checkpoints/delivery/delivery-receipt-v2.json", False),
    ),
}


class InspectionError(ValueError):
    """The explicit inspection input cannot be inspected safely."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def render_inspection(value: Mapping[str, Any]) -> bytes:
    """Render an inspection result using the stable machine-readable format."""

    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _require_run_root(run_root: Path) -> Path:
    root = Path(run_root)
    if root.is_symlink() or not root.is_dir():
        raise InspectionError("run root must be an existing regular directory")
    return root


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _issue(issues: list[dict[str, str]], code: str, path: str) -> None:
    issues.append({"code": code, "path": path})


def _has_symlink_component(root: Path, relative: str) -> bool:
    current = root
    for component in PurePosixPath(relative).parts:
        current /= component
        if current.is_symlink():
            return True
    return False


def _file_reference(root: Path, relative: str, issues: list[dict[str, str]]) -> dict[str, Any]:
    path = root / PurePosixPath(relative)
    reference: dict[str, Any] = {"path": relative, "present": False}
    if _has_symlink_component(root, relative):
        _issue(issues, "unsafe_symlink", relative)
        reference["reason"] = "unsafe_symlink"
        return reference
    if not path.exists():
        return reference
    if not path.is_file():
        _issue(issues, "not_regular_file", relative)
        reference["reason"] = "not_regular_file"
        return reference
    try:
        raw = path.read_bytes()
    except OSError:
        _issue(issues, "unreadable_file", relative)
        reference["reason"] = "unreadable_file"
        return reference
    reference.update({"present": True, "bytes": len(raw), "sha256": _sha256(raw)})
    return reference


def _read_json(
    root: Path,
    relative: str,
    issues: list[dict[str, str]],
    *,
    required: bool,
) -> tuple[bytes, dict[str, Any] | None]:
    path = root / PurePosixPath(relative)
    if _has_symlink_component(root, relative):
        _issue(issues, "unsafe_symlink", relative)
        return b"", None
    if not path.exists():
        if required:
            _issue(issues, "missing_required_file", relative)
        return b"", None
    if not path.is_file():
        _issue(issues, "not_regular_file", relative)
        return b"", None
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _issue(issues, "malformed_json", relative)
        return b"", None
    if not isinstance(value, dict):
        _issue(issues, "json_object_required", relative)
        return raw, None
    return raw, value


def _safe_identity(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    result: dict[str, Any] = {}
    for key in sorted(_IDENTITY_FIELDS):
        item = value.get(key)
        if key.endswith("_sha256") or key.endswith("_identity") or key.endswith("_operation_id") or key.endswith("_key"):
            if isinstance(item, str) and _SHA256_RE.fullmatch(item):
                result[key] = item
        elif key == "delivery_status":
            if isinstance(item, str) and item in _DELIVERY_STATUSES:
                result[key] = item
        elif key in {"final_release_status", "publication_status"}:
            if isinstance(item, str) and item in {"frozen", "waiting_human", "absent"}:
                result[key] = item
        elif key == "status":
            if isinstance(item, str) and item in _CHECKPOINT_STATUSES:
                result[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            result[key] = item
        elif isinstance(item, (list, tuple)) and all(isinstance(index, int) for index in item):
            result[key] = list(item)
    return result


def _checkpoint_state(
    state: Mapping[str, Any] | None,
    name: str,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    checkpoints = state.get("checkpoints") if isinstance(state, Mapping) else None
    value = checkpoints.get(name) if isinstance(checkpoints, Mapping) else None
    if value is None:
        return {"present": False, "status": "absent", "identity": {}}
    if not isinstance(value, Mapping):
        _issue(issues, "checkpoint_not_object", f"run_state.json:checkpoints.{name}")
        return {"present": True, "status": "invalid", "identity": {}}
    status = value.get("status")
    if not isinstance(status, str) or status not in _CHECKPOINT_STATUSES:
        _issue(issues, "invalid_checkpoint_status", f"run_state.json:checkpoints.{name}")
        status_value = "invalid"
    else:
        status_value = status
    return {"present": True, "status": status_value, "identity": _safe_identity(value)}


def _stage_summary(state: Mapping[str, Any] | None, issues: list[dict[str, str]]) -> tuple[list[dict[str, str]], str | None]:
    if not isinstance(state, Mapping):
        return [], None
    raw_states = state.get("stage_states")
    if not isinstance(raw_states, Mapping):
        _issue(issues, "stage_states_missing", "run_state.json")
        return [], None
    names = [str(name) for name in raw_states if isinstance(name, str)]
    names.sort(key=lambda name: (_STAGE_ORDER.index(name) if name in _STAGE_ORDER else len(_STAGE_ORDER), name))
    stages: list[dict[str, str]] = []
    for name in names:
        value = raw_states.get(name)
        status = value.get("status") if isinstance(value, Mapping) else None
        if not isinstance(status, str) or status not in _STAGE_STATUSES:
            _issue(issues, "invalid_stage_status", f"run_state.json:stage_states.{name}")
            status = "invalid"
        stages.append({"stage": name, "status": status})
    current: str | None = None
    for wanted in ("running", "waiting_human", "failed", "pending"):
        match = next((item["stage"] for item in stages if item["status"] == wanted), None)
        if match is not None:
            current = match
            break
    if current is None:
        completed = [item["stage"] for item in stages if item["status"] not in {"skipped", "invalid"}]
        current = completed[-1] if completed else None
    return stages, current


def _run_state_summary(root: Path, issues: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    relative = "run_state.json"
    reference = _file_reference(root, relative, issues)
    _raw, state = _read_json(root, relative, issues, required=True)
    summary: dict[str, Any] = {"file": reference, "valid": state is not None}
    if state is None:
        return summary, None
    run_id = state.get("run_id")
    exam_id = state.get("exam_id")
    if not isinstance(run_id, str) or not run_id:
        _issue(issues, "run_id_missing", relative)
        run_id = None
    if not isinstance(exam_id, str) or not exam_id:
        _issue(issues, "exam_id_missing", relative)
        exam_id = None
    stages, current = _stage_summary(state, issues)
    requested = state.get("stages_requested")
    if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
        _issue(issues, "requested_stages_invalid", relative)
        requested_values: list[str] = []
    else:
        requested_values = list(requested)
    summary["run"] = {
        "run_id": run_id,
        "exam_id": exam_id,
        "requested_stages": requested_values,
        "current_stage_observed": current,
        "stage_states": stages,
    }
    return summary, state


def _report_summary(root: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    relative = "live_run_report.json"
    reference = _file_reference(root, relative, issues)
    _raw, value = _read_json(root, relative, issues, required=False)
    result: dict[str, Any] = {"file": reference, "valid": value is not None if reference["present"] else None}
    if value is not None:
        result["completed"] = value.get("completed") if isinstance(value.get("completed"), bool) else None
        stop_reason = value.get("stop_reason")
        result["stop_reason"] = stop_reason if stop_reason in _SAFE_STOP_REASONS else "unclassified"
        stages = value.get("stages")
        result["stage_count"] = len(stages) if isinstance(stages, list) else None
    return result


def _freeze_reference(root: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    return {"file": _file_reference(root, "live_run_freeze.json", issues)}


def _group_files(root: Path, name: str, issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {"name": label, "required": required, **_file_reference(root, relative, issues)}
        for label, relative, required in _CHECKPOINT_FILES[name]
    ]


def _model(
    root: Path,
    relative: str,
    model_type: type[Any],
    issues: list[dict[str, str]],
    *,
    required: bool,
) -> tuple[bytes, Any | None]:
    raw, value = _read_json(root, relative, issues, required=required)
    if value is None:
        return raw, None
    try:
        return raw, model_type.model_validate_json(raw)
    except (TypeError, ValueError):
        _issue(issues, "invalid_contract", relative)
        return raw, None


def _compare_checkpoint_identity(
    checkpoint: Mapping[str, Any],
    expected: Mapping[str, Any],
    issues: list[dict[str, str]],
    path: str,
) -> None:
    identity = checkpoint.get("identity")
    if not isinstance(identity, Mapping):
        return
    for key, value in expected.items():
        if key in identity and identity[key] != value:
            _issue(issues, "identity_mismatch", f"{path}:{key}")


def _final_release_summary(root: Path, state_checkpoint: Mapping[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    files = _group_files(root, "final_release", issues)
    active = bool(state_checkpoint.get("present")) or (root / "checkpoints" / "final_release").exists()
    base: dict[str, Any] = {
        "status": "absent" if not state_checkpoint.get("present") else state_checkpoint.get("status", "invalid"),
        "checkpoint": dict(state_checkpoint),
        "files": files,
        "schemas": {},
        "identity": {},
    }
    loaded: dict[str, tuple[Any, bytes]] = {}
    specs: tuple[tuple[str, str, type[Any], bool], ...] = (
        ("bundle", "checkpoints/final_release/final-release-bundle-v2.json", FinalReleaseBundleV2, False),
        ("exclusion", "checkpoints/final_release/final-release-exclusion-ledger-v2.json", FinalReleaseExclusionLedgerV2, True),
        ("audit", "checkpoints/final_release/final-release-audit-v2.json", FinalReleaseAuditV2, True),
        ("receipt", "checkpoints/final_release/final-release-closure-v2.json", FinalReleaseClosureReceiptV2, True),
    )
    for label, relative, model_type, required in specs:
        raw, value = _model(root, relative, model_type, issues, required=required and active)
        if value is not None:
            loaded[label] = (value, raw)
            base["schemas"][label] = value.schema_version
    receipt_pair = loaded.get("receipt")
    if receipt_pair is not None:
        receipt, receipt_raw = receipt_pair
        identity = {
            "final_release_receipt_sha256": receipt.closure_sha256,
            "final_release_receipt_file_sha256": _sha256(receipt_raw),
            "final_release_bundle_sha256": receipt.bundle_sha256,
            "final_release_bundle_file_sha256": (
                _sha256(loaded["bundle"][1]) if "bundle" in loaded else None
            ),
            "final_release_exclusion_sha256": receipt.exclusion_sha256,
            "final_release_exclusion_file_sha256": (
                _sha256(loaded["exclusion"][1]) if "exclusion" in loaded else None
            ),
            "final_release_audit_sha256": receipt.audit_sha256,
            "final_release_audit_file_sha256": (
                _sha256(loaded["audit"][1]) if "audit" in loaded else None
            ),
            "final_release_released_count": receipt.released_count,
            "final_release_rejected_count": receipt.rejected_count,
            "final_release_manual_review_count": receipt.manual_review_count,
            "final_release_release_bundle_present": receipt.release_bundle_present,
        }
        base["identity"] = {key: value for key, value in identity.items() if value is not None}
        _compare_checkpoint_identity(state_checkpoint, base["identity"], issues, "run_state.json:checkpoints.final_release")
    return base


def _publication_summary(root: Path, state_checkpoint: Mapping[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    active = bool(state_checkpoint.get("present")) or (root / "checkpoints" / "publication").exists()
    base: dict[str, Any] = {
        "status": "absent" if not state_checkpoint.get("present") else state_checkpoint.get("status", "invalid"),
        "checkpoint": dict(state_checkpoint),
        "files": _group_files(root, "publication", issues),
        "schemas": {},
        "identity": {},
        "authorization_material": "not emitted",
        "artifacts": [],
    }
    _manifest_raw, manifest = _model(
        root,
        "checkpoints/publication/publication-manifest-v2.json",
        PublicationManifestV2,
        issues,
        required=active,
    )
    authorization_raw, authorization = _model(
        root,
        "checkpoints/publication/publication-authorization-v2.json",
        PublicationAuthorizationV2,
        issues,
        required=False,
    )
    receipt_raw, receipt = _model(
        root,
        "checkpoints/publication/publication-receipt-v2.json",
        PublicationReceiptV2,
        issues,
        required=False,
    )
    if manifest is not None:
        base["schemas"]["manifest"] = manifest.schema_version
        for artifact in manifest.artifacts:
            relative = artifact.relative_path
            if not isinstance(relative, str) or "\\" in relative:
                _issue(issues, "unsafe_artifact_path", "checkpoints/publication/publication-manifest-v2.json")
                continue
            candidate = PurePosixPath(relative)
            if candidate.is_absolute() or ".." in candidate.parts:
                _issue(issues, "unsafe_artifact_path", "checkpoints/publication/publication-manifest-v2.json")
                continue
            artifact_relative = (PurePosixPath("checkpoints/publication") / candidate).as_posix()
            reference = _file_reference(root, artifact_relative, issues)
            if not reference["present"]:
                _issue(issues, "missing_declared_artifact", artifact_relative)
            elif reference.get("sha256") != artifact.sha256:
                _issue(issues, "artifact_hash_mismatch", artifact_relative)
            base["artifacts"].append(
                {
                    "path": artifact_relative,
                    "sha256": artifact.sha256,
                    "present": reference["present"],
                }
            )
    if authorization is not None:
        base["schemas"]["authorization"] = authorization.schema_version
    if receipt is not None:
        base["schemas"]["receipt"] = receipt.schema_version
        identity = {
            "publication_manifest_sha256": receipt.publication_manifest_sha256,
            "publication_manifest_file_sha256": receipt.publication_manifest_file_sha256,
            "publication_authorization_sha256": receipt.authorization_sha256,
            "publication_authorization_file_sha256": (
                _sha256(authorization_raw) if authorization is not None else None
            ),
            "publication_receipt_sha256": receipt.receipt_sha256,
            "publication_receipt_file_sha256": _sha256(receipt_raw),
            "publication_artifact_count": receipt.artifact_count,
            "publication_package_sha256": receipt.package_sha256,
        }
        base["identity"] = {key: value for key, value in identity.items() if value is not None}
        _compare_checkpoint_identity(state_checkpoint, identity, issues, "run_state.json:checkpoints.publication")
    elif state_checkpoint.get("status") == "frozen":
        _issue(issues, "frozen_receipt_missing", "checkpoints/publication/publication-receipt-v2.json")
    return base


def _delivery_status(
    state_checkpoint: Mapping[str, Any], request: Any | None, execution: Any | None, receipt: Any | None
) -> str:
    if state_checkpoint.get("status") in {"unknown", "tampered", "refused"}:
        return "unknown" if state_checkpoint.get("status") == "unknown" else "invalid"
    if receipt is not None:
        return receipt.delivery_status
    if execution is not None and execution.execution_status in {"executing", "unknown"}:
        return "unknown"
    if execution is not None:
        return execution.execution_status
    if request is not None:
        return "prepared"
    return "absent"


def _delivery_summary(root: Path, state_checkpoint: Mapping[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    active = bool(state_checkpoint.get("present")) or (root / "checkpoints" / "delivery").exists()
    base: dict[str, Any] = {
        "status": "absent" if not state_checkpoint.get("present") else state_checkpoint.get("status", "invalid"),
        "delivery_status": "absent",
        "checkpoint": dict(state_checkpoint),
        "files": _group_files(root, "delivery", issues),
        "schemas": {},
        "identity": {},
        "receipt_reference": None,
        "filesystem_marker": {"observation": "not_read", "reference_present": False},
    }
    request_raw, request = _model(
        root,
        "checkpoints/delivery/delivery-request-v2.json",
        DeliveryRequestV2,
        issues,
        required=active,
    )
    execution_raw, execution = _model(
        root,
        "checkpoints/delivery/delivery-execution-v2.json",
        DeliveryExecutionV2,
        issues,
        required=active,
    )
    receipt_raw, receipt = _model(
        root, "checkpoints/delivery/delivery-receipt-v2.json", DeliveryReceiptV2, issues, required=False
    )
    if request is not None:
        base["schemas"]["request"] = request.schema_version
    if execution is not None:
        base["schemas"]["execution"] = execution.schema_version
    if receipt is not None:
        base["schemas"]["receipt"] = receipt.schema_version
    if request is not None and execution is not None and execution.delivery_identity != request.delivery_identity:
        _issue(issues, "identity_mismatch", "checkpoints/delivery/delivery-execution-v2.json:delivery_identity")
    if request is not None and receipt is not None:
        try:
            expected_receipt = build_delivery_receipt_v2(
                request=request,
                delivery_status=receipt.delivery_status,
                remote_reference=receipt.remote_reference,
            )
            if expected_receipt.model_dump(mode="json") != receipt.model_dump(mode="json"):
                _issue(issues, "identity_mismatch", "checkpoints/delivery/delivery-receipt-v2.json")
        except (TypeError, ValueError):
            _issue(issues, "identity_mismatch", "checkpoints/delivery/delivery-receipt-v2.json")
    if request is not None:
        identity: dict[str, Any] = {
            "delivery_request_sha256": request.request_sha256,
            "delivery_request_file_sha256": _sha256(request_raw),
            "delivery_target_sha256": request.target_sha256,
            "delivery_operation_id": request.delivery_operation_id,
            "delivery_identity": request.delivery_identity,
            "idempotency_key": request.idempotency_key,
        }
        if execution is not None:
            identity.update(
                {
                    "delivery_execution_sha256": execution.execution_sha256,
                    "delivery_execution_file_sha256": _sha256(execution_raw),
                }
            )
        if receipt is not None:
            identity.update(
                {
                    "delivery_receipt_sha256": receipt.receipt_sha256,
                    "delivery_receipt_file_sha256": _sha256(receipt_raw),
                }
            )
            base["receipt_reference"] = {"path": "checkpoints/delivery/delivery-receipt-v2.json", "sha256": _sha256(receipt_raw)}
            base["filesystem_marker"] = {
                "observation": "not_read",
                "reference_present": receipt.remote_reference is not None,
                "reference_kind": "opaque" if receipt.remote_reference is not None else None,
            }
        base["identity"] = identity
        _compare_checkpoint_identity(state_checkpoint, identity, issues, "run_state.json:checkpoints.delivery")
    base["delivery_status"] = _delivery_status(state_checkpoint, request, execution, receipt)
    if base["status"] in {"absent", "pending"}:
        base["status"] = base["delivery_status"]
    return base


def _checkpoint_summary(root: Path, state: Mapping[str, Any] | None, name: str, issues: list[dict[str, str]]) -> dict[str, Any]:
    checkpoint = _checkpoint_state(state, name, issues)
    result: dict[str, Any] = {
        "name": name,
        "state": checkpoint,
        "files": _group_files(root, name, issues),
    }
    if name == "final_release":
        result["details"] = _final_release_summary(root, checkpoint, issues)
    elif name == "publication":
        result["details"] = _publication_summary(root, checkpoint, issues)
    elif name == "delivery":
        result["details"] = _delivery_summary(root, checkpoint, issues)
    return result


def _finalize(summary: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    unique = {(item["code"], item["path"]): item for item in issues}
    summary["issues"] = [unique[key] for key in sorted(unique)]
    summary["inspection_status"] = "invalid" if summary["issues"] else "ok"
    return summary


def inspect_run(run_root: Path) -> dict[str, Any]:
    """Inspect all known checkpoint families under an explicit run root."""

    root = _require_run_root(run_root)
    issues: list[dict[str, str]] = []
    run_state, state = _run_state_summary(root, issues)
    summary: dict[str, Any] = {
        "schema_version": INSPECTION_SCHEMA_VERSION,
        "inspection_mode": "observation_only",
        "run_state": run_state,
        "run_report": _report_summary(root, issues),
        "run_freeze": _freeze_reference(root, issues),
        "checkpoints": {
            name: _checkpoint_summary(root, state, name, issues) for name in CHECKPOINT_NAMES
        },
    }
    run_info = run_state.get("run")
    if isinstance(run_info, Mapping):
        summary["run"] = dict(run_info)
    summary["publication"] = summary["checkpoints"]["publication"]["details"]
    summary["delivery"] = summary["checkpoints"]["delivery"]["details"]
    return _finalize(summary, issues)


def inspect_checkpoint(run_root: Path, name: str | None = None) -> dict[str, Any]:
    """Inspect one named checkpoint, or all known checkpoints when omitted."""

    if name is not None and name not in CHECKPOINT_NAMES:
        raise InspectionError(f"unknown checkpoint: {name}")
    result = inspect_run(run_root)
    if name is None:
        return result
    selected = result["checkpoints"][name]
    return _finalize(
        {
            "schema_version": INSPECTION_SCHEMA_VERSION,
            "inspection_mode": "observation_only",
            "checkpoint": selected,
            "run": result.get("run"),
            "issues": result["issues"],
        },
        list(result["issues"]),
    )


def inspect_delivery(run_root: Path) -> dict[str, Any]:
    """Inspect P6 delivery state without looking up or changing a destination."""

    result = inspect_run(run_root)
    return {
        "schema_version": INSPECTION_SCHEMA_VERSION,
        "inspection_mode": "observation_only",
        "run": result.get("run"),
        "delivery": result["delivery"],
        "issues": result["issues"],
        "inspection_status": result["inspection_status"],
    }


__all__ = (
    "CHECKPOINT_NAMES",
    "INSPECTION_SCHEMA_VERSION",
    "InspectionError",
    "inspect_checkpoint",
    "inspect_delivery",
    "inspect_run",
    "render_inspection",
)
