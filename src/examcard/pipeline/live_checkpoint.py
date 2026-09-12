"""人工检查点①（准入）与②（图像评审）（拆分层 1；设计 §7）。

**文件为契约**：驱动产出待决物料 → 退出码 2 暂停 → 人工写决定文件 → 严格摄取。
webui 只是可选检视器，不在这条链上；摄取端一律严格校验，宁可拒收不猜意图。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Sequence

from ..common.hashing import canonical_hash, hash_model, sha256_bytes
from ..common.io import atomic_write_bytes, atomic_write_json, png_dimensions, read_json
from ..providers.base import sanitized_error_summary
from ..gates.review.image_admission_batch import BatchImageAuthorizationManifest

from .live_support import _utc_now_iso
from .live_stage_step4 import (
    Step4ImageIntegrityError,
    step4_image_provenance_path,
    validate_step4_image_artifact,
)


_ADMISSION_QUEUE_V2_SCHEMA = "phase2r84_admission_queue_v2"
_ADMISSION_QUEUE_V2_DIR = "admission"
_ADMISSION_MANIFEST_V2_NAMES = (
    "human-decision-manifest-v2.json",
    "human-decision-manifest.json",
    "decision-manifest-v2.json",
)
_ADMISSION_AUTHORIZATION_V2_NAMES = (
    "explicit-production-authorization-v2.json",
    "explicit-production-authorization.json",
)
_ECHO_REVIEW_V2_DIR = "echo_review"
_IMAGE_REVIEW_HUMAN_FIELDS = frozenset(("disposition", "reviewer", "basis"))
_IMAGE_REVIEW_EMISSION_IDENTITY_FIELDS = (
    "source_binding_sha256",
    "image_review_source_binding_sha256",
    "request_manifest_file_sha256",
    "reviewed_card_indexes",
    "reviewed_card_count",
    "provenance_file_sha256_by_card",
)
_IMAGE_REVIEW_AUTHORITY_IDENTITY_FIELDS = (
    *_IMAGE_REVIEW_EMISSION_IDENTITY_FIELDS,
    "sheet_file_sha256",
    "review_sheet_file_sha256",
    "manifest_sha256",
    "manifest_file_sha256",
    "decision_count",
    "approved_count",
    "rejected_count",
    "manual_review_count",
)


def _safe_checkpoint_name(value: str) -> str:
    """Turn an identity into a stable local filename without changing the identity."""

    return value.replace(":", "_").replace("/", "_").replace("\\", "_")


def _queue_v2_sha256(items: Sequence[Any]) -> str:
    return canonical_hash([item.model_dump(mode="json") for item in items])


def _page_artifact_value(
    page_artifacts: Mapping[Any, Any] | None, page: Any
) -> Any:
    if page_artifacts is None:
        return None
    for key in (
        page.page_ref_id,
        (page.source_manifest_sha256, page.page),
        page.page,
    ):
        if key in page_artifacts:
            return page_artifacts[key]
    return None


def _artifact_bytes(value: Any, *, label: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, Path) or isinstance(value, str):
        path = Path(value)
        if not path.is_file():
            raise ValueError(f"{label} is missing: {path}")
        return path.read_bytes()
    raise ValueError(f"{label} must be bytes or a file path")


def _artifact_parts(value: Any, *, page_ref_id: str) -> tuple[bytes, bytes]:
    """Read a page artifact supplied by the CLI/test harness.

    The accepted mapping form is intentionally small: ``image_bytes`` and
    ``page_bytes`` may each be bytes or paths.  A two-tuple is also accepted
    for lightweight offline tests.
    """

    if isinstance(value, Mapping):
        image_value = value.get("image_bytes", value.get("image_path"))
        page_value = value.get("page_bytes", value.get("page_path"))
        if image_value is None or page_value is None:
            raise ValueError(f"page artifact is incomplete: {page_ref_id}")
        return (
            _artifact_bytes(image_value, label=f"image artifact {page_ref_id}"),
            _artifact_bytes(page_value, label=f"page artifact {page_ref_id}"),
        )
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return (
            _artifact_bytes(value[0], label=f"image artifact {page_ref_id}"),
            _artifact_bytes(value[1], label=f"page artifact {page_ref_id}"),
        )
    raise ValueError(f"page artifact is incomplete: {page_ref_id}")


def _resolve_checkpoint_file(run_root: Path, checkpoint_dir: Path, value: str) -> Path:
    raw = Path(value)
    candidates = (raw,) if raw.is_absolute() else (run_root / raw, checkpoint_dir / raw)
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(run_root.resolve())
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    raise ValueError(f"checkpoint artifact is missing or outside the run: {value}")


def _validate_v2_page_artifact(run_root: Path, checkpoint_dir: Path, page: Any) -> None:
    image_path = _resolve_checkpoint_file(run_root, checkpoint_dir, page.image_path)
    image_bytes = image_path.read_bytes()
    if sha256_bytes(image_bytes) != page.image_sha256:
        raise ValueError(f"source page image hash mismatch: {page.page_ref_id}")
    width, height = png_dimensions(image_path)
    if (width, height) != (page.image_width, page.image_height):
        raise ValueError(f"source page image dimensions mismatch: {page.page_ref_id}")
    page_json = checkpoint_dir / "pages" / f"{_safe_checkpoint_name(page.page_ref_id)}.json"
    if not page_json.is_file():
        raise ValueError(f"source page evidence is missing: {page.page_ref_id}")
    if sha256_bytes(page_json.read_bytes()) != page.page_sha256:
        raise ValueError(f"source page evidence hash mismatch: {page.page_ref_id}")


def _load_v2_queue(run_root: Path) -> tuple[Path, dict[str, Any], tuple[Any, ...], dict[str, Any]]:
    from ..gates.review.queue_v2 import AdmissionQueueItemV2

    checkpoint_dir = run_root / "checkpoints" / _ADMISSION_QUEUE_V2_DIR
    queue_path = checkpoint_dir / "queue-v2.json"
    raw = read_json(queue_path)
    if not isinstance(raw, Mapping) or raw.get("schema_version") != _ADMISSION_QUEUE_V2_SCHEMA:
        raise ValueError("V2 admission queue envelope is missing or invalid")
    rows = raw.get("candidates")
    if not isinstance(rows, list) or not rows:
        raise ValueError("V2 admission queue has no candidates")
    items = tuple(AdmissionQueueItemV2.model_validate(row) for row in rows)
    queue_sha256 = raw.get("queue_sha256")
    if not isinstance(queue_sha256, str) or queue_sha256 != _queue_v2_sha256(items):
        raise ValueError("V2 admission queue hash mismatch")
    if raw.get("queue_count") != len(items):
        raise ValueError("V2 admission queue count mismatch")
    expected_step3_count = raw.get("expected_step3_count")
    if expected_step3_count != len(items):
        raise ValueError("V2 admission queue expected Step3 count mismatch")
    source_provenance = raw.get("source_provenance")
    if not isinstance(source_provenance, list) or not source_provenance:
        raise ValueError("V2 admission queue source provenance is missing")
    if raw.get("source_provenance_sha256") != canonical_hash(source_provenance):
        raise ValueError("V2 admission queue source provenance hash mismatch")
    provenance_count = 0
    provenance_candidate_ids: list[str] = []
    provenance_request_ids: list[str] = []
    for row in source_provenance:
        if not isinstance(row, Mapping):
            raise ValueError("V2 admission queue source provenance row is invalid")
        if not isinstance(row.get("sample_id"), str) or not row.get("sample_id"):
            raise ValueError("V2 admission queue source provenance sample identity is missing")
        if not isinstance(row.get("request_id"), str) or not row.get("request_id"):
            raise ValueError("V2 admission queue source provenance request identity is missing")
        provenance_request_ids.append(str(row["request_id"]))
        if not isinstance(row.get("successful_attempt_reason"), str) or not row.get("successful_attempt_reason"):
            raise ValueError("V2 admission queue source provenance successful attempt reason is missing")
        row_count = row.get("expected_step3_count")
        if not isinstance(row_count, int) or row_count < 1:
            raise ValueError("V2 admission queue source provenance expected Step3 count is invalid")
        proposal_provenance = row.get("proposal_source_provenance")
        if not isinstance(proposal_provenance, list) or len(proposal_provenance) != row_count:
            raise ValueError("V2 admission queue proposal source provenance count mismatch")
        candidate_ids = row.get("candidate_ids")
        if not isinstance(candidate_ids, list) or len(candidate_ids) != row_count or any(
            not isinstance(candidate_id, str) for candidate_id in candidate_ids
        ):
            raise ValueError("V2 admission queue source provenance candidate identities are invalid")
        provenance_candidate_ids.extend(candidate_ids)
        provenance_count += row_count
    if provenance_count != expected_step3_count:
        raise ValueError("V2 admission queue source provenance total count mismatch")
    if len({item.candidate_id for item in items}) != len(items):
        raise ValueError("V2 admission queue candidate identities are not unique")
    if set(provenance_candidate_ids) != {item.candidate_id for item in items}:
        raise ValueError("V2 admission queue source provenance does not cover the queue")
    request_ids = raw.get("request_ids")
    if not isinstance(request_ids, list) or set(provenance_request_ids) != set(request_ids):
        raise ValueError("V2 admission queue source provenance request identities do not match")
    drafts: dict[str, Any] = {}
    draft_files = raw.get("contract_draft_files")
    if not isinstance(draft_files, Mapping):
        raise ValueError("V2 admission queue contract draft file bindings are missing")
    for item in items:
        draft_path = checkpoint_dir / "contract-drafts" / f"{_safe_checkpoint_name(item.candidate_id)}.json"
        draft = read_json(draft_path)
        if not isinstance(draft, Mapping) or draft.get("candidate_id") != item.candidate_id:
            raise ValueError(f"V2 typed contract draft is missing or crossed candidate: {item.candidate_id}")
        typed_contract = draft.get("typed_contract")
        if not isinstance(typed_contract, Mapping):
            raise ValueError(f"V2 typed contract draft is incomplete: {item.candidate_id}")
        if draft.get("typed_contract_sha256") != canonical_hash(typed_contract):
            raise ValueError(f"V2 typed contract draft hash mismatch: {item.candidate_id}")
        draft_binding = draft_files.get(item.candidate_id)
        if not isinstance(draft_binding, Mapping):
            raise ValueError(f"V2 typed contract draft file binding is missing: {item.candidate_id}")
        expected_draft_path = draft_path.resolve().relative_to(run_root.resolve()).as_posix()
        if draft_binding.get("path") != expected_draft_path:
            raise ValueError(f"V2 typed contract draft path binding drifted: {item.candidate_id}")
        if draft_binding.get("typed_contract_sha256") != draft.get("typed_contract_sha256"):
            raise ValueError(f"V2 typed contract draft contract hash drifted: {item.candidate_id}")
        if draft_binding.get("file_sha256") != sha256_bytes(draft_path.read_bytes()):
            raise ValueError(f"V2 typed contract draft file hash mismatch: {item.candidate_id}")
        drafts[item.candidate_id] = dict(draft)
        for page in item.pages:
            _validate_v2_page_artifact(run_root, checkpoint_dir, page)
    return checkpoint_dir, dict(raw), items, drafts


def build_admission_checkpoint_v2(
    *,
    run_root: Path,
    projection: Any | Sequence[Any],
    page_artifacts: Mapping[Any, Any] | None = None,
    workbench_renderer: Callable[[Sequence[Mapping[str, Any]]], str] | None = None,
    source_provenance: Sequence[Mapping[str, Any]] | None = None,
    expected_step3_count: int | None = None,
) -> dict[str, Any]:
    """Materialize the V2 Step2B admission checkpoint.

    The projection is produced by ``project_step2b_to_admission_v2``.  This
    function only persists already source-bound objects; it never creates a
    candidate, chooses an anchor, or creates human authority.
    """

    from ..gates.review.step2b_admission_v2 import AdmissionProjectionV2

    projections = (projection,) if isinstance(projection, AdmissionProjectionV2) else tuple(projection)
    if not projections or not all(isinstance(item, AdmissionProjectionV2) for item in projections):
        raise ValueError("V2 admission checkpoint requires one or more AdmissionProjectionV2 objects")
    items = tuple(item for value in projections for item in value.queue_items)
    drafts = {candidate_id: draft for value in projections for candidate_id, draft in value.contract_drafts_by_candidate.items()}
    if not items or len({item.candidate_id for item in items}) != len(items):
        raise ValueError("V2 admission checkpoint requires unique non-empty candidates")
    if set(drafts) != {item.candidate_id for item in items}:
        raise ValueError("V2 admission checkpoint contract drafts do not cover the queue")
    expected_count = len(items) if expected_step3_count is None else expected_step3_count
    if expected_count != len(items):
        raise ValueError("V2 admission checkpoint expected Step3 count does not match the queue")
    if source_provenance is None:
        provenance_rows: list[dict[str, Any]] = []
        for value in projections:
            value_items = tuple(value.queue_items)
            provenance_rows.append(
                {
                    "sample_id": value.sample_id,
                    "successful_attempt_reason": "direct-builder",
                    "request_id": value.request_id,
                    "expected_step3_count": len(value_items),
                    "candidate_ids": [item.candidate_id for item in value_items],
                    "proposal_source_provenance": [
                        {
                            "candidate_id": item.candidate_id,
                            "queue_item_id": item.queue_item_id,
                            "subject_id": item.subject_id,
                            "record_id": item.record_id,
                            "fact_id": item.fact_id,
                            "evidence_id": item.evidence_id,
                            "presentation": item.presentation,
                            "source_manifest_sha256": item.source_manifest_sha256,
                            "anchor_ids": list(item.anchor_ids),
                            "pages": [page.model_dump(mode="json") for page in item.pages],
                        }
                        for item in value_items
                    ],
                }
            )
    else:
        provenance_rows = [dict(row) for row in source_provenance]
    if not provenance_rows:
        raise ValueError("V2 admission checkpoint source provenance is empty")
    if sum(int(row.get("expected_step3_count", -1)) for row in provenance_rows) != expected_count:
        raise ValueError("V2 admission checkpoint source provenance count mismatch")
    expected_samples = {value.sample_id for value in projections}
    if {row.get("sample_id") for row in provenance_rows} != expected_samples:
        raise ValueError("V2 admission checkpoint source provenance samples do not match projections")
    for item in items:
        for page in item.pages:
            value = _page_artifact_value(page_artifacts, page)
            if value is not None:
                image_bytes, page_bytes = _artifact_parts(value, page_ref_id=page.page_ref_id)
                if sha256_bytes(image_bytes) != page.image_sha256 or sha256_bytes(page_bytes) != page.page_sha256:
                    raise ValueError(f"V2 page artifact bytes do not match binding: {page.page_ref_id}")
                if page.image_path.startswith("checkpoints/"):
                    target = Path(run_root) / page.image_path
                else:
                    target = Path(run_root) / "checkpoints" / "admission" / page.image_path
                atomic_write_bytes(target, image_bytes)
                page_json = Path(run_root) / "checkpoints" / "admission" / "pages" / f"{_safe_checkpoint_name(page.page_ref_id)}.json"
                atomic_write_bytes(page_json, page_bytes)
            else:
                # A caller may have rendered the page before invoking this
                # function.  Refuse a missing artifact rather than emitting a
                # queue that cannot be revalidated on resume.
                checkpoint_dir = Path(run_root) / "checkpoints" / "admission"
                _validate_v2_page_artifact(Path(run_root), checkpoint_dir, page)

    checkpoint_dir = Path(run_root) / "checkpoints" / "admission"
    inbox = checkpoint_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    queue_sha256 = _queue_v2_sha256(items)
    envelope = {
        "schema_version": _ADMISSION_QUEUE_V2_SCHEMA,
        "request_ids": [value.request_id for value in projections],
        "sample_ids": [value.sample_id for value in projections],
        "queue_sha256": queue_sha256,
        "queue_count": len(items),
        "candidates": [item.model_dump(mode="json") for item in items],
    }
    drafts_dir = checkpoint_dir / "contract-drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    draft_file_bindings: dict[str, dict[str, Any]] = {}
    for candidate_id, draft in drafts.items():
        draft_path = drafts_dir / f"{_safe_checkpoint_name(candidate_id)}.json"
        atomic_write_json(draft_path, draft)
        draft_file_bindings[candidate_id] = {
            "path": draft_path.relative_to(Path(run_root)).as_posix(),
            "file_sha256": sha256_bytes(draft_path.read_bytes()),
            "typed_contract_sha256": draft["typed_contract_sha256"],
        }
    envelope["expected_step3_count"] = expected_count
    envelope["source_provenance"] = provenance_rows
    envelope["source_provenance_sha256"] = canonical_hash(provenance_rows)
    envelope["contract_draft_files"] = draft_file_bindings
    queue_path = checkpoint_dir / "queue-v2.json"
    atomic_write_json(queue_path, envelope)
    queue_file_sha256 = sha256_bytes(queue_path.read_bytes())
    if workbench_renderer is not None:
        html = workbench_renderer([item.model_dump(mode="json") for item in items])
        (checkpoint_dir / "workbench-v2.html").write_text(html, encoding="utf-8")
    atomic_write_json(
        checkpoint_dir / "human-review-v2.template.json",
        {
            "schema_version": "phase2r84_human_decision_manifest_v2",
            "manifest_id": "<human-supplied-manifest-id>",
            "queue_sha256": queue_sha256,
            "decisions": [],
            "decision_count": 0,
            "manifest_sha256": "<human-supplied-manifest-sha256>",
            "instructions": "Replace placeholders with a fully hashed HumanDecisionManifestV2; browser output is not authority.",
        },
    )
    atomic_write_json(
        checkpoint_dir / "explicit-production-authorization.template.json",
        {
            "reviewer": "<human reviewer>",
            "reason": "<explicit human authorization basis>",
            "recorded_at": "<human recorded timestamp>",
            "identity_period": "<identity period>",
            "instructions": "Supply explicit human values; do not use browser-demo output or generated placeholders.",
        },
    )
    (checkpoint_dir / "INSTRUCTIONS.txt").write_text(
        "V2 admission checkpoint: place human-decision-manifest-v2.json and "
        "explicit-production-authorization-v2.json in inbox/, then resume the same run.\n"
        "The manifest must contain exactly one decision for every queue candidate.\n",
        encoding="utf-8",
    )
    return {
        "checkpoint": "admission",
        "schema_version": _ADMISSION_QUEUE_V2_SCHEMA,
        "queue_sha256": queue_sha256,
        "queue_file_sha256": queue_file_sha256,
        "queue_count": len(items),
        "queue_path": str(queue_path),
        "inbox_path": str(inbox),
        "fixture_path": str(checkpoint_dir / "reviewed-typed-evidence-fixture-v2.json"),
        "status": "waiting_human",
    }


def _first_existing(path: Path, names: Sequence[str]) -> Path | None:
    for name in names:
        candidate = path / name
        if candidate.is_file():
            return candidate
    return None


def _authority_identity(
    *,
    queue_doc: Mapping[str, Any],
    queue_file_sha256: str,
    manifest: Any,
    manifest_file_sha256: str,
    auth_input_sha256: str,
    auth_file_sha256: str,
    fixture: Any,
    fixture_file_sha256: str,
) -> dict[str, Any]:
    return {
        "queue_sha256": str(queue_doc["queue_sha256"]),
        "queue_file_sha256": queue_file_sha256,
        "human_manifest_sha256": manifest.manifest_sha256,
        "human_manifest_file_sha256": manifest_file_sha256,
        "authorization_input_sha256": auth_input_sha256,
        "authorization_file_sha256": auth_file_sha256,
        "fixture_sha256": fixture.fixture_sha256,
        "fixture_file_sha256": fixture_file_sha256,
        "expected_step3_count": int(queue_doc["expected_step3_count"]),
        "released_count": int(fixture.reviewed_candidate_count),
    }


def _compare_authority_identity(
    expected: Mapping[str, Any] | None, actual: Mapping[str, Any]
) -> None:
    if expected is None:
        return
    for key in (
        "queue_sha256",
        "queue_file_sha256",
        "human_manifest_sha256",
        "human_manifest_file_sha256",
        "authorization_input_sha256",
        "authorization_file_sha256",
        "fixture_sha256",
        "fixture_file_sha256",
        "expected_step3_count",
        "released_count",
    ):
        if expected.get(key) != actual.get(key):
            raise ValueError(f"V2 authority identity drifted: {key}")


def ingest_admission_checkpoint_v2(
    *,
    run_root: Path,
    verify_only: bool = False,
    expected_authority: Mapping[str, Any] | None = None,
    expected_step3_count: int | None = None,
    expected_queue_file_sha256: str | None = None,
    source_validator: Callable[..., None] | None,
) -> dict[str, Any]:
    """Strictly ingest V2 human authority and close the external fixture chain."""

    try:
        from ..gates.review.typed_evidence_export import ExplicitProductionAuthorization
        from ..gates.review.decisions_v2 import HumanDecisionManifestV2, validate_human_decision_v2
        from ..gates.review.typed_evidence_export_v2 import export_reviewed_typed_evidence_fixture_v2

        checkpoint_dir, queue_doc, queue_items, drafts = _load_v2_queue(Path(run_root))
        queue_count = int(queue_doc["expected_step3_count"])
        if expected_step3_count is not None and queue_count != expected_step3_count:
            raise ValueError("V2 admission queue count does not match the current Step3 expectation")
        if expected_queue_file_sha256 is None:
            raise ValueError("V2 admission queue file identity is missing from the resume ticket")
        current_queue_file_sha256 = sha256_bytes((checkpoint_dir / "queue-v2.json").read_bytes())
        if current_queue_file_sha256 != expected_queue_file_sha256:
            raise ValueError("V2 admission queue file identity drifted")
        if not callable(source_validator):
            raise ValueError("V2 admission source validator is required")
        source_validator(
            run_root=Path(run_root),
            queue_doc=queue_doc,
            queue_items=queue_items,
        )
        inbox = checkpoint_dir / "inbox"
        manifest_path = _first_existing(inbox, _ADMISSION_MANIFEST_V2_NAMES)
        auth_path = _first_existing(inbox, _ADMISSION_AUTHORIZATION_V2_NAMES)
        if manifest_path is None or auth_path is None:
            missing = []
            if manifest_path is None:
                missing.append("human-decision-manifest-v2.json")
            if auth_path is None:
                missing.append("explicit-production-authorization-v2.json")
            return {"status": "waiting_human", "detail": "missing V2 authority input: " + ", ".join(missing)}
        manifest_raw = read_json(manifest_path)
        auth_raw = read_json(auth_path)
        prior_audit_path = checkpoint_dir / "external-authority-audit-v2.json"
        prior_audit = read_json(prior_audit_path) if prior_audit_path.is_file() else None
        current_manifest_file_sha256 = sha256_bytes(manifest_path.read_bytes())
        current_auth_file_sha256 = sha256_bytes(auth_path.read_bytes())
        current_auth_input_sha256 = canonical_hash(auth_raw)
        if isinstance(prior_audit, Mapping):
            expected_external = {
                "queue_file_sha256": current_queue_file_sha256,
                "human_manifest_file_sha256": current_manifest_file_sha256,
                "authorization_file_sha256": current_auth_file_sha256,
                "authorization_input_sha256": current_auth_input_sha256,
            }
            for key, actual in expected_external.items():
                if prior_audit.get(key) != actual:
                    raise ValueError(f"V2 external authority artifact tampered: {key}")
        manifest = HumanDecisionManifestV2.model_validate(manifest_raw)
        authorization = ExplicitProductionAuthorization.model_validate(auth_raw)
        queue_sha256 = str(queue_doc["queue_sha256"])
        if manifest.queue_sha256 != queue_sha256:
            raise ValueError("V2 human manifest crossed the frozen queue hash")
        queue_by_candidate = {item.candidate_id: item for item in queue_items}
        decisions_by_candidate = {item.candidate_id: item for item in manifest.decisions}
        if len(decisions_by_candidate) != len(manifest.decisions):
            raise ValueError("V2 human manifest contains duplicate candidates")
        missing_candidates = set(queue_by_candidate) - set(decisions_by_candidate)
        unknown_candidates = set(decisions_by_candidate) - set(queue_by_candidate)
        if missing_candidates:
            return {
                "status": "waiting_human",
                "detail": f"V2 human manifest is incomplete; missing candidates: {sorted(missing_candidates)}",
            }
        if unknown_candidates:
            raise ValueError(f"V2 human manifest references unknown candidates: {sorted(unknown_candidates)}")
        dispositions = {decision.disposition for decision in manifest.decisions}
        if "blocked" in dispositions or "not_applicable" in dispositions:
            raise ValueError("V2 human authority contains a terminal refusal disposition")
        if "manual_review" in dispositions:
            return {
                "status": "waiting_human",
                "detail": "V2 human authority still contains manual_review dispositions",
            }
        if len(manifest.decisions) != queue_count or dispositions != {"reviewed_candidate"}:
            raise ValueError("V2 human authority does not release every expected Step3 candidate")
        for decision in manifest.decisions:
            queue_item = queue_by_candidate[decision.candidate_id]
            validate_human_decision_v2(decision, queue_item, type("CandidateRef", (), {"candidate_id": decision.candidate_id})())
            draft = drafts[decision.candidate_id]
            if decision.disposition == "reviewed_candidate":
                if decision.typed_contract is None or draft["typed_contract"] != decision.typed_contract.model_dump(mode="json"):
                    raise ValueError(f"V2 human decision crossed typed contract draft: {decision.candidate_id}")
        released = tuple(decisions_by_candidate[item.candidate_id] for item in queue_items)
        if len(released) != queue_count:
            raise ValueError("V2 human authority released an unexpected candidate count")
        fixture = export_reviewed_typed_evidence_fixture_v2(
            source_manifest=manifest,
            released=released,
            queue_items_by_candidate=queue_by_candidate,
            authorization=authorization,
        )
        fixture_path = checkpoint_dir / "reviewed-typed-evidence-fixture-v2.json"
        fixture_payload = fixture.model_dump(mode="json")
        if fixture_path.is_file():
            persisted = read_json(fixture_path)
            if persisted != fixture_payload:
                raise ValueError("persisted V2 typed fixture does not match the frozen authority inputs")
        elif verify_only:
            raise ValueError("persisted V2 typed fixture is missing")
        else:
            atomic_write_json(fixture_path, fixture_payload)
        current_fixture_file_sha256 = sha256_bytes(fixture_path.read_bytes())
        identity = _authority_identity(
            queue_doc=queue_doc,
            queue_file_sha256=current_queue_file_sha256,
            manifest=manifest,
            manifest_file_sha256=current_manifest_file_sha256,
            auth_input_sha256=current_auth_input_sha256,
            auth_file_sha256=current_auth_file_sha256,
            fixture=fixture,
            fixture_file_sha256=current_fixture_file_sha256,
        )
        _compare_authority_identity(expected_authority, identity)
        if isinstance(prior_audit, Mapping):
            if prior_audit.get("fixture_file_sha256") != current_fixture_file_sha256:
                raise ValueError("V2 external authority artifact tampered: fixture_file_sha256")
        audit = {
            "schema_version": "examcard_admission_external_authority_audit_v2",
            "queue_sha256": queue_sha256,
            "queue_file_sha256": current_queue_file_sha256,
            "human_manifest_path": str(manifest_path),
            "human_manifest_file_sha256": current_manifest_file_sha256,
            "human_manifest_sha256": manifest.manifest_sha256,
            "authorization_path": str(auth_path),
            "authorization_file_sha256": current_auth_file_sha256,
            "authorization_input_sha256": current_auth_input_sha256,
            "fixture_path": str(fixture_path),
            "fixture_file_sha256": current_fixture_file_sha256,
            "fixture_sha256": fixture.fixture_sha256,
            "released_count": len(released),
            "expected_step3_count": queue_count,
        }
        if not verify_only:
            atomic_write_json(checkpoint_dir / "external-authority-audit-v2.json", audit)
        return {
            "status": "ingested",
            "released_count": len(released),
            "fixture_path": str(fixture_path),
            "fixture_sha256": fixture.fixture_sha256,
            "fixture_file_sha256": audit["fixture_file_sha256"],
            "authority_identity": identity,
            "audit": audit,
        }
    except Exception as error:
        return {
            "status": "refused",
            "stop_reason": "gate_refused_admission",
            "detail": sanitized_error_summary(error),
        }


def verify_admission_checkpoint_v2(
    *,
    run_root: Path,
    expected_authority: Mapping[str, Any] | None = None,
    expected_step3_count: int | None = None,
    expected_queue_file_sha256: str | None = None,
    source_validator: Callable[..., None] | None,
) -> dict[str, Any]:
    """Revalidate an already ingested V2 checkpoint without rewriting its fixture."""

    return ingest_admission_checkpoint_v2(
        run_root=run_root,
        verify_only=True,
        expected_authority=expected_authority,
        expected_step3_count=expected_step3_count,
        expected_queue_file_sha256=expected_queue_file_sha256,
        source_validator=source_validator,
    )


def _echo_review_v2_paths(run_root: Path) -> tuple[Path, Path, Path, Path]:
    checkpoint_dir = Path(run_root) / "checkpoints" / _ECHO_REVIEW_V2_DIR
    return (
        checkpoint_dir,
        checkpoint_dir / "echo_step3_review_sheet_v2.json",
        checkpoint_dir / "echo-step3-review-receipt-v2.json",
        checkpoint_dir / "echo-batch-release-v2.json",
    )


def _validate_echo_review_v2_source(
    *, run_root: Path, ticket: Mapping[str, Any], expected_step3_count: int, run_id: str
) -> tuple[Path, Path, Any, Any, str, str]:
    """Validate the immutable source side of a same-run Echo V2 checkpoint."""

    from ..knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2
    from ..knowledge.contracts.echo_release_v2 import EchoStep3ReviewSheetV2

    checkpoint_dir, default_sheet_path, _receipt_path, _release_path = _echo_review_v2_paths(run_root)
    if ticket.get("source_echo_run_id") != run_id:
        raise ValueError("same-run Echo review checkpoint belongs to another run")
    typed_value = ticket.get("source_typed_fixture_path")
    sheet_value = ticket.get("review_sheet_path", str(default_sheet_path))
    if not isinstance(typed_value, str) or not isinstance(sheet_value, str):
        raise ValueError("same-run Echo review source paths are missing")
    typed_path = _resolve_checkpoint_file(Path(run_root), checkpoint_dir, typed_value)
    sheet_path = _resolve_checkpoint_file(Path(run_root), checkpoint_dir, sheet_value)
    typed_raw = typed_path.read_bytes()
    typed_file_sha256 = sha256_bytes(typed_raw)
    if typed_file_sha256 != ticket.get("source_typed_fixture_file_sha256"):
        raise ValueError("same-run Echo review typed fixture file drifted")
    fixture = ReviewedTypedEvidenceFixtureV2.model_validate_json(typed_raw.decode("utf-8"))
    if fixture.fixture_sha256 != ticket.get("source_typed_fixture_sha256"):
        raise ValueError("same-run Echo review typed fixture authority drifted")
    _validate_echo_step3_records_v3(
        run_root=Path(run_root),
        fixture=fixture,
        fixture_file_sha256=typed_file_sha256,
        expected_step3_count=expected_step3_count,
    )
    sheet_raw = sheet_path.read_bytes()
    sheet = EchoStep3ReviewSheetV2.model_validate_json(sheet_raw.decode("utf-8"))
    if sheet.source_typed_fixture_path != typed_path.as_posix():
        raise ValueError("same-run Echo review crossed the typed fixture path")
    if (
        sheet.source_typed_fixture_file_sha256 != typed_file_sha256
        or sheet.source_typed_fixture_sha256 != fixture.fixture_sha256
    ):
        raise ValueError("same-run Echo review crossed the typed fixture binding")
    if Path(sheet.source_echo_run_dir).resolve() != Path(run_root).resolve():
        raise ValueError("same-run Echo review crossed the source Echo run")
    if len(sheet.cards) != expected_step3_count or ticket.get("expected_step3_count") != expected_step3_count:
        raise ValueError("same-run Echo review card count drifted")
    if sheet.sheet_sha256 != ticket.get("review_sheet_sha256"):
        raise ValueError("same-run Echo review sheet binding drifted")
    sheet_file_sha256 = sha256_bytes(sheet_raw)
    if ticket.get("status") == "ingested" and sheet_file_sha256 != ticket.get("review_sheet_file_sha256"):
        raise ValueError("same-run Echo review sheet file drifted after ingest")
    return typed_path, sheet_path, fixture, sheet, typed_file_sha256, sheet_file_sha256


def _validate_echo_step3_records_v3(
    *, run_root: Path, fixture: Any, fixture_file_sha256: str, expected_step3_count: int
) -> None:
    """Re-inspect every persisted Step3 V3 response/gate pair before Echo use."""

    from ..compilers.step3_card_v3 import compile_reviewed_fixture_v3
    from ..compilers.step3_response_v3 import build_single_card_model_input_v3, make_single_card_inspector_v3

    compilation = compile_reviewed_fixture_v3(
        fixture, source_typed_fixture_file_sha256=fixture_file_sha256
    )
    if len(compilation.contracts) != expected_step3_count:
        raise ValueError("same-run Echo Step3 record count drifted")
    for sequence, contract in enumerate(compilation.contracts, start=1):
        record_dir = Path(run_root) / "step3" / "cards" / f"{sequence:02d}_{contract.contract_id.rsplit(':', 1)[-1]}"
        raw_files = sorted(record_dir.glob("attempt-*.response.raw.json"))
        gate_files = sorted(record_dir.glob("attempt-*.gate.json"))
        if not raw_files:
            raise ValueError(f"same-run Echo Step3 response record is missing: {contract.contract_id}")
        if not gate_files:
            raise ValueError(f"same-run Echo Step3 gate record is missing: {contract.contract_id}")
        raw_bytes = raw_files[-1].read_bytes()
        model_input = build_single_card_model_input_v3(compilation, index=sequence - 1)
        inspection = make_single_card_inspector_v3(model_input)(raw_bytes)
        if not inspection.valid:
            raise ValueError(f"same-run Echo Step3 response gate no longer validates: {contract.contract_id}")
        stored_gate = read_json(gate_files[-1])
        if stored_gate != inspection.model_dump(mode="json"):
            raise ValueError(f"same-run Echo Step3 stored gate drifted: {contract.contract_id}")


_ECHO_FROZEN_IDENTITY_FIELDS = (
    "source_echo_run_id",
    "source_typed_fixture_file_sha256",
    "source_typed_fixture_sha256",
    "review_sheet_sha256",
    "review_sheet_file_sha256",
    "ingest_receipt_sha256",
    "release_sha256",
    "release_file_sha256",
    "handoff_sha256",
    "handoff_file_sha256",
    "released_count",
    "expected_step3_count",
)


def _validate_frozen_echo_review_identity(ticket: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    """Refuse any change to the authority identity frozen by a prior ingest."""

    for field in _ECHO_FROZEN_IDENTITY_FIELDS:
        if field not in ticket:
            raise ValueError(f"same-run Echo review frozen identity is missing: {field}")
        if ticket[field] != current.get(field):
            raise ValueError(f"same-run Echo review frozen identity drifted: {field}")


def emit_echo_step3_review_checkpoint_v2(
    *, run_root: Path, typed_fixture_path: Path, expected_step3_count: int, source_echo_run_id: str
) -> dict[str, Any]:
    """Emit and bind a same-run V2 Step3 review sheet without human authority."""

    from ..compilers.step4_echo_bridge_v2 import emit_echo_step3_review_sheet_v2
    from ..knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2
    from ..knowledge.contracts.echo_release_v2 import EchoStep3ReviewSheetV2

    run_root = Path(run_root)
    typed_fixture_path = Path(typed_fixture_path).resolve()
    typed_raw = typed_fixture_path.read_bytes()
    fixture = ReviewedTypedEvidenceFixtureV2.model_validate_json(typed_raw.decode("utf-8"))
    typed_file_sha256 = sha256_bytes(typed_raw)
    _validate_echo_step3_records_v3(
        run_root=run_root,
        fixture=fixture,
        fixture_file_sha256=typed_file_sha256,
        expected_step3_count=expected_step3_count,
    )
    checkpoint_dir, _sheet_path, _receipt_path, _release_path = _echo_review_v2_paths(run_root)
    sheet_path = emit_echo_step3_review_sheet_v2(
        echo_run_dir=run_root,
        typed_fixture_path=typed_fixture_path,
        out_dir=checkpoint_dir,
    )
    sheet_raw = Path(sheet_path).read_bytes()
    sheet = EchoStep3ReviewSheetV2.model_validate_json(sheet_raw.decode("utf-8"))
    if len(sheet.cards) != expected_step3_count:
        raise ValueError("same-run Echo review emitted an unexpected Step3 card count")
    if Path(sheet.source_echo_run_dir).resolve() != run_root.resolve():
        raise ValueError("same-run Echo review emitted a crossed source run")
    if sheet.source_typed_fixture_path != typed_fixture_path.as_posix():
        raise ValueError("same-run Echo review emitted a crossed typed fixture path")
    if (
        sheet.source_typed_fixture_file_sha256 != typed_file_sha256
        or sheet.source_typed_fixture_sha256 != fixture.fixture_sha256
    ):
        raise ValueError("same-run Echo review emitted a crossed typed fixture binding")
    return {
        "checkpoint": "echo_review",
        "schema_version": sheet.schema_version,
        "source_echo_run_id": source_echo_run_id,
        "source_typed_fixture_path": str(typed_fixture_path),
        "source_typed_fixture_file_sha256": typed_file_sha256,
        "source_typed_fixture_sha256": fixture.fixture_sha256,
        "review_sheet_path": str(Path(sheet_path)),
        "review_sheet_sha256": sheet.sheet_sha256,
        "review_sheet_file_sha256": sha256_bytes(sheet_raw),
        "expected_step3_count": expected_step3_count,
        "status": "waiting_human",
    }


def ingest_echo_step3_review_checkpoint_v2(
    *, run_root: Path, ticket: Mapping[str, Any], expected_step3_count: int, source_echo_run_id: str
) -> dict[str, Any]:
    """Ingest or revalidate the same-run V2 review checkpoint and its release chain."""

    try:
        from ..compilers.step4_echo_bridge_v2 import build_echo_release_fixture_v2
        from ..gates.review.echo_step3_review_v2 import ingest_echo_step3_review_v2, issue_echo_batch_handoff_v2
        from ..knowledge.contracts.echo_release_v2 import (
            EchoBatchReviewedReleaseFixtureV2,
            EchoBatchStep4HandoffV2,
            EchoStep3ReviewReceiptV2,
        )

        run_root = Path(run_root)
        checkpoint_dir, _sheet_path, receipt_path, release_path = _echo_review_v2_paths(run_root)
        typed_path, sheet_path, fixture, sheet, typed_file_sha256, sheet_file_sha256 = _validate_echo_review_v2_source(
            run_root=run_root,
            ticket=ticket,
            expected_step3_count=expected_step3_count,
            run_id=source_echo_run_id,
        )
        ingest = ingest_echo_step3_review_v2(sheet_path=sheet_path)
        if ingest["status"] == "waiting_human":
            return ingest
        if ingest["status"] != "released":
            return {"status": "refused", "detail": str(ingest.get("detail", "V2 Echo review was refused"))}
        receipt = ingest.get("ingest_receipt")
        if receipt is None:
            raise ValueError("same-run Echo review ingest did not produce a V2 receipt")
        approved = {card.contract_id for card in sheet.cards if card.disposition == "approved"}
        first_approved = next((card for card in sheet.cards if card.contract_id in approved), None)
        if first_approved is None or not isinstance(first_approved.b2_review, dict):
            raise ValueError("same-run Echo review has no explicit authorization evidence")
        release = build_echo_release_fixture_v2(
            typed_fixture_path=typed_path,
            echo_run_dir=run_root,
            review_sheet_path=sheet_path,
            approved_rows=ingest["released"],
            source_echo_run_id=source_echo_run_id,
        )
        handoff = issue_echo_batch_handoff_v2(
            release=release,
            authorized_by=str(first_approved.reviewer or ""),
            authorization_evidence=dict(first_approved.b2_review),
            ingest_receipt=receipt,
        )
        receipt_payload = receipt.model_dump(mode="json")
        release_payload = release.model_dump(mode="json")
        handoff_payload = handoff.model_dump(mode="json")
        persisted_receipt_path = checkpoint_dir / "echo-step3-review-receipt-v2.json"
        persisted_release_path = checkpoint_dir / "echo-batch-release-v2.json"
        persisted_handoff_path = checkpoint_dir / "echo-batch-handoff-v2.json"
        if ticket.get("status") == "ingested":
            if not persisted_receipt_path.is_file() or not persisted_release_path.is_file() or not persisted_handoff_path.is_file():
                raise ValueError("same-run Echo review persisted release chain is incomplete")
            if read_json(persisted_receipt_path) != receipt_payload:
                raise ValueError("same-run Echo review receipt drifted")
            if read_json(persisted_release_path) != release_payload:
                raise ValueError("same-run Echo release drifted from the frozen review")
            if read_json(persisted_handoff_path) != handoff_payload:
                raise ValueError("same-run Echo handoff drifted from the frozen release")
        else:
            atomic_write_json(persisted_receipt_path, receipt_payload)
            atomic_write_json(persisted_release_path, release_payload)
            atomic_write_json(persisted_handoff_path, handoff_payload)
        persisted_receipt = EchoStep3ReviewReceiptV2.model_validate(read_json(persisted_receipt_path))
        persisted_release = EchoBatchReviewedReleaseFixtureV2.model_validate(read_json(persisted_release_path))
        persisted_handoff = EchoBatchStep4HandoffV2.model_validate(read_json(persisted_handoff_path))
        if persisted_release.card_count != persisted_handoff.card_count or persisted_release.card_count < 1:
            raise ValueError("same-run Echo release/handoff card count is invalid")
        if (
            persisted_release.source_typed_fixture_file_sha256 != typed_file_sha256
            or persisted_release.source_typed_fixture_sha256 != fixture.fixture_sha256
            or persisted_release.review_sheet_sha256 != sheet_file_sha256
            or persisted_handoff.review_ingest_receipt_sha256 != persisted_receipt.receipt_sha256
        ):
            raise ValueError("same-run Echo release chain source binding drifted")
        result = {
            "status": "ingested",
            "detail": f"released={persisted_release.card_count}",
            "source_echo_run_id": source_echo_run_id,
            "source_typed_fixture_path": str(typed_path),
            "source_typed_fixture_file_sha256": typed_file_sha256,
            "source_typed_fixture_sha256": fixture.fixture_sha256,
            "review_sheet_path": str(sheet_path),
            "review_sheet_sha256": sheet.sheet_sha256,
            "review_sheet_file_sha256": sheet_file_sha256,
            "ingest_receipt_path": str(persisted_receipt_path),
            "ingest_receipt_sha256": persisted_receipt.receipt_sha256,
            "release_path": str(persisted_release_path),
            "release_sha256": persisted_release.fixture_sha256,
            "release_file_sha256": sha256_bytes(persisted_release_path.read_bytes()),
            "handoff_path": str(persisted_handoff_path),
            "handoff_sha256": persisted_handoff.handoff_sha256,
            "handoff_file_sha256": sha256_bytes(persisted_handoff_path.read_bytes()),
            "expected_step3_count": expected_step3_count,
            "released_count": persisted_release.card_count,
        }
        if ticket.get("status") == "ingested":
            _validate_frozen_echo_review_identity(ticket, result)
        return result
    except Exception as error:
        return {
            "status": "refused",
            "stop_reason": "gate_refused_echo_review",
            "detail": sanitized_error_summary(error),
        }


# ---------------------------------------------------------------------------
# 人工检查点（§7：文件为契约；驱动产出物料→退出码 2 暂停→严格摄取）
# ---------------------------------------------------------------------------


def build_admission_checkpoint(
    *,
    run_root: Path,
    queue_items: Sequence[Mapping[str, Any]],
    workbench_renderer: Callable[[Sequence[Mapping[str, Any]]], str] | None = None,
) -> dict[str, Any]:
    """检查点①物料（G-A1/G-A3/G-A4）：queue.json + workbench.html + progress + inbox。

    ``queue_items`` 逐条为 AdmissionQueueItem 的构造载荷（不含 entry_sha256，
    disposition 恒 manual_review）；封印走 r8x 法定哈希谱系。
    """

    from ..gates.review.queue import AdmissionQueueItem, ManualReviewQueue, queue_sha256 as _queue_sha256
    from ..knowledge.contracts.admission_legacy_hash import canonical_hash as legacy_hash, hash_model_r8x

    checkpoint_dir = Path(run_root) / "checkpoints" / "admission"
    inbox = checkpoint_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    sealed_items = []
    for payload in queue_items:
        document = dict(payload)
        document.setdefault("disposition", "manual_review")
        sealed_items.append(hash_model_r8x(AdmissionQueueItem, document, "entry_sha256"))
    queue_hash = _queue_sha256(sealed_items)
    queue = ManualReviewQueue(
        queue_sha256=queue_hash,
        candidates=tuple(sealed_items),
        queue_count=len(sealed_items),
        queue_sha256_projection=legacy_hash([item.model_dump(mode="json") for item in sealed_items]),
    )
    atomic_write_json(checkpoint_dir / "queue.json", queue.model_dump(mode="json"))
    workbench_path = None
    if workbench_renderer is not None:
        html = workbench_renderer([item.model_dump(mode="json") for item in sealed_items])
        workbench_path = checkpoint_dir / "workbench.html"
        workbench_path.write_text(html, encoding="utf-8")
    from ..gates.review.browser import build_progress

    progress = build_progress(
        queue_hash,
        [
            {
                "candidate_id": item.candidate_id,
                "queue_item_id": item.queue_item_id,
                "subject_id": item.subject_id,
                "presentation": item.presentation,
                "disposition": item.disposition,
                "explicit_decision": False,
            }
            for item in sealed_items
        ],
    )
    atomic_write_json(checkpoint_dir / "progress.json", progress.model_dump(mode="json"))
    instructions = (
        "checkpoint(1) admission: review the queue, then drop review-browser-decisions.json into inbox/ "
        "(or an authoritative human-decision-manifest.json), and resume the run with --resume <run_id>.\n"
        "workbench static server (run from repo root):\n"
        "  .venv\\Scripts\\python.exe -m http.server 8777 --bind 127.0.0.1 --directory <exam workspace root>\n"
    )
    (checkpoint_dir / "INSTRUCTIONS.txt").write_text(instructions, encoding="utf-8")
    return {
        "checkpoint": "admission",
        "queue_sha256": queue_hash,
        "queue_count": len(sealed_items),
        "queue_path": str(checkpoint_dir / "queue.json"),
        "workbench_path": str(workbench_path) if workbench_path else None,
        "inbox_path": str(inbox),
        "status": "waiting_human",
    }


def ingest_admission_decisions(
    *,
    run_root: Path,
    candidates_by_id: Mapping[str, Any],
    expected_queue_sha256: str,
    decision_builder: Callable[[Any, Any], Any],
) -> dict[str, Any]:
    """检查点①严格摄取（G-A2）：raw 浏览器信封 → 分类降格 → 权威决策重建。

    浏览器导出的 ``reviewed_candidate`` 一律降格 manual_review（r82 定式，本函数
    不新造语义）；权威放行集合只能来自 decision_builder 产出的 CLI 侧权威决策。
    决定文件非法/哈希不链 → ``gate_refused_admission``。
    """

    from ..gates.review.browser import RawReviewDraftManifest, classify_browser_decision, normalize_raw_draft

    inbox = Path(run_root) / "checkpoints" / "admission" / "inbox"
    raw_path = inbox / "review-browser-decisions.json"
    if not raw_path.is_file():
        return {"status": "waiting_human", "detail": f"decision file not found: {raw_path}"}
    try:
        raw = RawReviewDraftManifest.model_validate(read_json(raw_path))
        classified = [classify_browser_decision(decision) for decision in raw.decisions]
        normalized = normalize_raw_draft(
            raw,
            dict(candidates_by_id),
            expected_queue_sha256=expected_queue_sha256,
            decision_builder=decision_builder,
        )
    except Exception as error:
        return {
            "status": "refused",
            "stop_reason": "gate_refused_admission",
            "detail": sanitized_error_summary(error),
        }
    released = tuple(
        decision for decision in normalized if getattr(decision, "disposition", None) == "reviewed_candidate"
    )
    audit = {
        "decision_count": len(normalized),
        "classified": [{"disposition": disposition, "reason": reason} for disposition, reason in classified],
        "released_count": len(released),
        "ingested_at_utc": _utc_now_iso(),
    }
    atomic_write_json(Path(run_root) / "checkpoints" / "admission" / "ingest-audit.json", audit)
    return {"status": "ingested", "released_count": len(released), "released": released, "audit": audit}


_IMAGE_REVIEW_MATERIALIZATION_DIR = "image_review"
_IMAGE_REVIEW_MATERIALIZATION_FILES = {
    "fixture": "reviewed-image-fixture-v2.json",
    "queue": "post-review-queue-v2.json",
    "audit": "image-admission-audit-v2.json",
    "receipt": "image-review-materialization-v2.json",
}


def _image_review_materialization_paths(run_root: Path) -> dict[str, Path]:
    checkpoint_dir = Path(run_root) / "checkpoints" / _IMAGE_REVIEW_MATERIALIZATION_DIR
    return {name: checkpoint_dir / filename for name, filename in _IMAGE_REVIEW_MATERIALIZATION_FILES.items()}


def _load_image_review_materialization_inputs(
    *,
    run_root: Path,
    current_request_bindings: Mapping[int, Mapping[str, Any]] | None,
    expected_authority: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], Any]:
    """Load P2 authority and derive P3 inputs without trusting P3 files."""

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    sheet_path = checkpoint_dir / "image_review_sheet.json"
    manifest_path = checkpoint_dir / "image-authorization-manifest.json"
    try:
        sheet_raw = sheet_path.read_bytes()
        sheet = read_json(sheet_path)
        manifest_raw = manifest_path.read_bytes()
        manifest = BatchImageAuthorizationManifest.model_validate(read_json(manifest_path))
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError("image review P2 authority is missing or invalid") from error
    if not isinstance(sheet, Mapping) or sheet.get("schema") != "image-review-sheet/2.0":
        raise Step4ImageIntegrityError("legacy or invalid image review sheet schema")
    cards = sheet.get("cards")
    if not isinstance(cards, list) or not all(isinstance(card, Mapping) for card in cards):
        raise Step4ImageIntegrityError("image review sheet cards are invalid")
    provenance_file_sha256_by_card = _validate_review_rows(
        run_root=Path(run_root), cards=cards, current_request_bindings=current_request_bindings
    )
    current_authority = _image_review_identity(
        run_root=Path(run_root),
        sheet_path=sheet_path,
        sheet_raw=sheet_raw,
        sheet=sheet,
        cards=cards,
        provenance_file_sha256_by_card=provenance_file_sha256_by_card,
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_raw=manifest_raw,
    )
    if expected_authority is not None:
        _validate_expected_image_review_identity(
            expected=expected_authority,
            current=current_authority,
            fields=_IMAGE_REVIEW_AUTHORITY_IDENTITY_FIELDS,
        )
    from ..gates.review.image_admission_materialization_v2 import derive_image_review_materialization_v2

    artifacts = derive_image_review_materialization_v2(
        rows=cards,
        authorization_manifest=manifest,
        image_review_source_binding_sha256=current_authority["image_review_source_binding_sha256"],
        review_sheet_file_sha256=current_authority["review_sheet_file_sha256"],
        authorization_manifest_file_sha256=current_authority["manifest_file_sha256"],
        request_manifest_file_sha256=current_authority["request_manifest_file_sha256"],
        provenance_file_sha256_by_card=provenance_file_sha256_by_card,
    )
    return current_authority, artifacts


def _read_materialization_model(path: Path, model_type: type[Any]) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
        model = model_type.model_validate(read_json(path))
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError(f"invalid image review materialization artifact: {path.name}") from error
    return model, raw


def _require_materialization_model(path: Path, expected: Any, *, allow_create: bool) -> tuple[Any, bytes]:
    if path.is_file():
        actual, raw = _read_materialization_model(path, type(expected))
        if actual.model_dump(mode="json") != expected.model_dump(mode="json"):
            raise Step4ImageIntegrityError(f"image review materialization artifact conflicts: {path.name}")
        return actual, raw
    if not allow_create:
        raise Step4ImageIntegrityError(f"image review materialization artifact is missing: {path.name}")
    atomic_write_json(path, expected.model_dump(mode="json"))
    return _read_materialization_model(path, type(expected))


def _image_review_materialization_identity(
    *,
    receipt: Any,
    receipt_raw: bytes,
    fixture_raw: bytes | None,
    queue_raw: bytes,
    audit_raw: bytes,
) -> dict[str, Any]:
    return {
        "materialization_status": "frozen",
        "materialization_receipt_sha256": receipt.receipt_sha256,
        "materialization_receipt_file_sha256": sha256_bytes(receipt_raw),
        "reviewed_fixture_present": receipt.reviewed_fixture_present,
        "reviewed_fixture_sha256": receipt.reviewed_fixture_sha256,
        "reviewed_fixture_file_sha256": sha256_bytes(fixture_raw) if fixture_raw is not None else None,
        "post_review_queue_sha256": receipt.post_review_queue_sha256,
        "post_review_queue_file_sha256": sha256_bytes(queue_raw),
        "admission_audit_sha256": receipt.admission_audit_sha256,
        "admission_audit_file_sha256": sha256_bytes(audit_raw),
        "materialized_decision_count": receipt.decision_count,
        "materialized_approved_count": receipt.approved_count,
        "materialized_rejected_count": receipt.rejected_count,
        "materialized_manual_review_count": receipt.manual_review_count,
        "materialized_reviewed_card_indexes": list(receipt.reviewed_card_indexes),
        "materialized_post_review_card_indexes": list(receipt.post_review_card_indexes),
    }


def _build_image_review_materialization_receipt(
    *,
    authority: Mapping[str, Any],
    artifacts: Any,
    fixture_raw: bytes | None,
    queue_raw: bytes,
    audit_raw: bytes,
) -> Any:
    from ..gates.review.image_admission_materialization_v2 import ImageReviewMaterializationReceiptV2

    fixture = artifacts.reviewed_fixture
    queue = artifacts.post_review_queue
    audit = artifacts.admission_audit
    return hash_model(
        ImageReviewMaterializationReceiptV2,
        {
            "image_review_source_binding_sha256": authority["image_review_source_binding_sha256"],
            "review_sheet_file_sha256": authority["review_sheet_file_sha256"],
            "authorization_manifest_sha256": authority["manifest_sha256"],
            "authorization_manifest_file_sha256": authority["manifest_file_sha256"],
            "request_manifest_file_sha256": authority["request_manifest_file_sha256"],
            "reviewed_fixture_present": fixture is not None,
            "reviewed_fixture_sha256": fixture.fixture_sha256 if fixture is not None else None,
            "reviewed_fixture_file_sha256": sha256_bytes(fixture_raw) if fixture_raw is not None else None,
            "post_review_queue_sha256": queue.queue_sha256,
            "post_review_queue_file_sha256": sha256_bytes(queue_raw),
            "admission_audit_sha256": audit.audit_sha256,
            "admission_audit_file_sha256": sha256_bytes(audit_raw),
            "decision_count": authority["decision_count"],
            "approved_count": authority["approved_count"],
            "rejected_count": authority["rejected_count"],
            "manual_review_count": authority["manual_review_count"],
            "reviewed_card_indexes": tuple(fixture.reviewed_card_indexes) if fixture is not None else (),
            "post_review_card_indexes": tuple(queue.card_indexes),
        },
        "receipt_sha256",
    )


def materialize_image_review_v2(
    *,
    run_root: Path,
    current_request_bindings: Mapping[int, Mapping[str, Any]] | None = None,
    expected_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize or recover P3 files, freezing only after the full chain validates.

    A pre-existing receipt changes recovery semantics: all child files must
    already exist and match, because the receipt is written last.
    """

    authority, artifacts = _load_image_review_materialization_inputs(
        run_root=Path(run_root),
        current_request_bindings=current_request_bindings,
        expected_authority=expected_authority,
    )
    paths = _image_review_materialization_paths(Path(run_root))
    receipt_exists = paths["receipt"].is_file()
    allow_create_children = not receipt_exists
    fixture_raw: bytes | None = None
    if artifacts.reviewed_fixture is None:
        if paths["fixture"].exists():
            raise Step4ImageIntegrityError("zero-approved image review cannot contain a reviewed fixture")
    else:
        _fixture, fixture_raw = _require_materialization_model(
            paths["fixture"], artifacts.reviewed_fixture, allow_create=allow_create_children
        )
    _queue, queue_raw = _require_materialization_model(
        paths["queue"], artifacts.post_review_queue, allow_create=allow_create_children
    )
    _audit, audit_raw = _require_materialization_model(
        paths["audit"], artifacts.admission_audit, allow_create=allow_create_children
    )
    expected_receipt = _build_image_review_materialization_receipt(
        authority=authority,
        artifacts=artifacts,
        fixture_raw=fixture_raw,
        queue_raw=queue_raw,
        audit_raw=audit_raw,
    )
    if paths["receipt"].is_file():
        receipt, receipt_raw = _read_materialization_model(paths["receipt"], type(expected_receipt))
        if receipt.model_dump(mode="json") != expected_receipt.model_dump(mode="json"):
            raise Step4ImageIntegrityError("image review materialization receipt conflicts with its artifacts")
    else:
        atomic_write_json(paths["receipt"], expected_receipt.model_dump(mode="json"))
        receipt, receipt_raw = _read_materialization_model(paths["receipt"], type(expected_receipt))
    return _image_review_materialization_identity(
        receipt=receipt,
        receipt_raw=receipt_raw,
        fixture_raw=fixture_raw,
        queue_raw=queue_raw,
        audit_raw=audit_raw,
    )


def validate_materialized_image_review_v2(
    *,
    run_root: Path,
    current_request_bindings: Mapping[int, Mapping[str, Any]] | None = None,
    expected_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only validation of a frozen P3 chain; never regenerates artifacts."""

    if expected_authority is None or expected_authority.get("materialization_status") != "frozen":
        raise Step4ImageIntegrityError("frozen image review materialization status is missing")
    paths = _image_review_materialization_paths(Path(run_root))
    if not paths["receipt"].is_file():
        raise Step4ImageIntegrityError("frozen image review materialization receipt is missing")
    identity = materialize_image_review_v2(
        run_root=Path(run_root),
        current_request_bindings=current_request_bindings,
        expected_authority=expected_authority,
    )
    for key, expected in (
        ("materialization_receipt_sha256", expected_authority.get("materialization_receipt_sha256") if expected_authority else None),
        ("materialization_receipt_file_sha256", expected_authority.get("materialization_receipt_file_sha256") if expected_authority else None),
        ("post_review_queue_sha256", expected_authority.get("post_review_queue_sha256") if expected_authority else None),
        ("post_review_queue_file_sha256", expected_authority.get("post_review_queue_file_sha256") if expected_authority else None),
        ("admission_audit_sha256", expected_authority.get("admission_audit_sha256") if expected_authority else None),
        ("admission_audit_file_sha256", expected_authority.get("admission_audit_file_sha256") if expected_authority else None),
    ):
        if expected is None:
            raise Step4ImageIntegrityError(f"frozen image review materialization identity is missing: {key}")
        if identity.get(key) != expected:
            raise Step4ImageIntegrityError(f"frozen image review materialization identity drifted: {key}")
    for key in (
        "reviewed_fixture_present",
        "reviewed_fixture_sha256",
        "reviewed_fixture_file_sha256",
        "materialized_decision_count",
        "materialized_approved_count",
        "materialized_rejected_count",
        "materialized_manual_review_count",
        "materialized_reviewed_card_indexes",
        "materialized_post_review_card_indexes",
    ):
        if expected_authority is None or key not in expected_authority:
            raise Step4ImageIntegrityError(f"frozen image review materialization identity is missing: {key}")
        if identity.get(key) != expected_authority[key]:
            raise Step4ImageIntegrityError(f"frozen image review materialization identity drifted: {key}")
    return identity


def emit_image_review_sheet(*, run_root: Path, step4_outcome: Mapping[str, Any], storage: Path) -> Path:
    """检查点②评审底稿（G-B3），只登记当前仍完整绑定的图片。"""

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for card in step4_outcome.get("cards", ()):  # 仅登记成功出图卡；失败卡留在 step4 报告
        if card.get("outcome") != "success":
            continue
        try:
            provenance = validate_step4_image_artifact(
                storage=Path(storage),
                card_index=int(card["card_index"]),
                source_card_id=str(card["source_card_id"]),
                sample_id=str(card["sample_id"]),
                render_presentation=str(card["render_presentation"]),
                http_payload_sha256=str(card["http_payload_sha256"]),
                image_path_value=str(card["image_path"]),
                image_sha256=str(card["image_sha256"]),
                provenance_sha256=str(card["image_provenance_sha256"]),
            )
        except (KeyError, TypeError, ValueError, OSError) as error:
            if isinstance(error, Step4ImageIntegrityError):
                raise
            raise Step4ImageIntegrityError("cannot emit review sheet for an unbound Step4 image") from error
        rows.append(
            {
                "card_index": provenance.card_index,
                "source_card_id": provenance.source_card_id,
                "sample_id": provenance.sample_id,
                "render_presentation": provenance.render_presentation,
                "image_path": provenance.image_path,
                "image_sha256": provenance.image_sha256,
                "image_provenance_sha256": provenance.provenance_sha256,
                "http_payload_sha256": provenance.http_payload_sha256,
                "disposition": None,
                "reviewer": None,
                "basis": None,
            }
        )
    sheet_path = checkpoint_dir / "image_review_sheet.json"
    atomic_write_json(sheet_path, {"schema": "image-review-sheet/2.0", "cards": rows})
    return sheet_path


def _request_bindings_from_manifest(run_root: Path) -> dict[int, dict[str, Any]]:
    path = Path(run_root) / "step4" / "requests-manifest.json"
    if not path.is_file():
        raise Step4ImageIntegrityError("Step4 request manifest is missing")
    rows = read_json(path)
    if not isinstance(rows, list):
        raise Step4ImageIntegrityError("Step4 request manifest is invalid")
    bindings: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise Step4ImageIntegrityError("Step4 request manifest contains an invalid row")
        card_index = int(row["card_index"])
        if card_index in bindings:
            raise Step4ImageIntegrityError("Step4 request manifest contains duplicate cards")
        bindings[card_index] = dict(row)
    return bindings


def _validate_request_binding_alignment(
    *, run_root: Path, current_request_bindings: Mapping[int, Mapping[str, Any]] | None
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Compare rebuilt Step4 bindings with the persisted request manifest."""

    persisted = _request_bindings_from_manifest(run_root)
    current = (
        {card_index: dict(row) for card_index, row in current_request_bindings.items()}
        if current_request_bindings is not None
        else dict(persisted)
    )
    if list(current) != list(persisted):
        raise Step4ImageIntegrityError("review rows do not cover the exact Step4 request order")
    for card_index, persisted_row in persisted.items():
        current_row = current.get(card_index)
        if current_row is None:
            raise Step4ImageIntegrityError(f"current Step4 request is missing card {card_index}")
        for key in ("card_index", "source_card_id", "sample_id", "render_presentation", "http_payload_sha256"):
            if str(current_row.get(key)) != str(persisted_row.get(key)):
                raise Step4ImageIntegrityError(f"Step4 request binding drifted for card {card_index}: {key}")
    return current, persisted


def _validate_review_rows(
    *, run_root: Path, cards: list[Mapping[str, Any]], current_request_bindings: Mapping[int, Mapping[str, Any]] | None
) -> dict[int, str]:
    bindings, persisted = _validate_request_binding_alignment(
        run_root=run_root, current_request_bindings=current_request_bindings
    )
    storage = Path(run_root) / "step4"
    seen_card_indexes: set[int] = set()
    ordered_card_indexes: list[int] = []
    provenance_file_sha256_by_card: dict[int, str] = {}
    for row in cards:
        card_index = int(row["card_index"])
        if card_index in seen_card_indexes:
            raise Step4ImageIntegrityError(f"review sheet contains duplicate card {card_index}")
        seen_card_indexes.add(card_index)
        ordered_card_indexes.append(card_index)
        current = bindings.get(card_index)
        if current is None:
            raise Step4ImageIntegrityError(f"review row card {card_index} is not in the current Step4 request")
        for key in ("source_card_id", "sample_id", "render_presentation", "http_payload_sha256"):
            if str(row.get(key)) != str(current.get(key)):
                raise Step4ImageIntegrityError(f"review row card {card_index} has stale {key}")
        sample_id = str(row.get("sample_id", ""))
        if not sample_id:
            raise Step4ImageIntegrityError(f"review row card {card_index} has no sample identity")
        validate_step4_image_artifact(
            storage=storage,
            card_index=card_index,
            source_card_id=str(row["source_card_id"]),
            sample_id=sample_id,
            render_presentation=str(row["render_presentation"]),
            http_payload_sha256=str(current["http_payload_sha256"]),
            image_path_value=str(row["image_path"]),
            image_sha256=str(row["image_sha256"]),
            provenance_sha256=str(row["image_provenance_sha256"]),
        )
        provenance_path = step4_image_provenance_path(storage, card_index, str(row["source_card_id"]))
        provenance_file_sha256_by_card[card_index] = sha256_bytes(provenance_path.read_bytes())
    expected_card_indexes = list(persisted)
    if ordered_card_indexes != expected_card_indexes:
        raise Step4ImageIntegrityError("review sheet does not cover the exact Step4 request cards")
    return provenance_file_sha256_by_card


def _image_review_source_binding(
    *, sheet: Mapping[str, Any], cards: Sequence[Mapping[str, Any]], provenance_file_sha256_by_card: Mapping[int, str]
) -> str:
    """Hash every immutable sheet/source field while excluding human-fill cells."""

    payload = {key: value for key, value in sheet.items() if key != "cards"}
    bound_cards: list[dict[str, Any]] = []
    for row in cards:
        bound = {key: value for key, value in row.items() if key not in _IMAGE_REVIEW_HUMAN_FIELDS}
        card_index = int(row["card_index"])
        bound["provenance_file_sha256"] = provenance_file_sha256_by_card[card_index]
        bound_cards.append(bound)
    payload["cards"] = bound_cards
    return canonical_hash(payload)


def _image_review_provenance_file_bindings(provenance_file_sha256_by_card: Mapping[int, str]) -> dict[str, str]:
    return {str(card_index): value for card_index, value in provenance_file_sha256_by_card.items()}


def _validate_expected_image_review_identity(
    *, expected: Mapping[str, Any] | None, current: Mapping[str, Any], fields: Sequence[str]
) -> None:
    if expected is None:
        raise Step4ImageIntegrityError("image review frozen authority identity is missing")
    for field in fields:
        if field not in expected:
            raise Step4ImageIntegrityError(f"image review frozen authority identity is missing: {field}")
        if expected[field] != current.get(field):
            raise Step4ImageIntegrityError(f"image review frozen authority drifted: {field}")


def _image_review_identity(
    *,
    run_root: Path,
    sheet_path: Path,
    sheet_raw: bytes,
    sheet: Mapping[str, Any],
    cards: Sequence[Mapping[str, Any]],
    provenance_file_sha256_by_card: Mapping[int, str],
    manifest_path: Path | None = None,
    manifest: Any | None = None,
    manifest_raw: bytes | None = None,
) -> dict[str, Any]:
    request_manifest_path = Path(run_root) / "step4" / "requests-manifest.json"
    if not request_manifest_path.is_file():
        raise Step4ImageIntegrityError("Step4 request manifest is missing")
    request_manifest_raw = request_manifest_path.read_bytes()
    reviewed_card_indexes = [int(row["card_index"]) for row in cards]
    source_binding_sha256 = _image_review_source_binding(
        sheet=sheet, cards=cards, provenance_file_sha256_by_card=provenance_file_sha256_by_card
    )
    identity: dict[str, Any] = {
        "sheet_path": str(sheet_path),
        "review_sheet_path": str(sheet_path),
        "source_binding_sha256": source_binding_sha256,
        "image_review_source_binding_sha256": source_binding_sha256,
        "request_manifest_file_sha256": sha256_bytes(request_manifest_raw),
        "reviewed_card_indexes": reviewed_card_indexes,
        "reviewed_card_count": len(reviewed_card_indexes),
        "provenance_file_sha256_by_card": _image_review_provenance_file_bindings(provenance_file_sha256_by_card),
        "sheet_file_sha256": sha256_bytes(sheet_raw),
        "review_sheet_file_sha256": sha256_bytes(sheet_raw),
    }
    if manifest is not None and manifest_path is not None and manifest_raw is not None:
        manifest_file_sha256 = sha256_bytes(manifest_raw)
        identity.update(
            {
                "manifest_path": str(manifest_path),
                "manifest_sha256": manifest.manifest_sha256,
                "manifest_file_sha256": manifest_file_sha256,
                "decision_count": manifest.decision_count,
                "approved_count": manifest.approved_count,
                "rejected_count": manifest.rejected_count,
                "manual_review_count": manifest.manual_review_count,
            }
        )
    return identity


def image_review_emission_identity(
    *,
    run_root: Path,
    sheet_path: Path | None = None,
    current_request_bindings: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the immutable identity that may survive human sheet edits."""

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    path = checkpoint_dir / "image_review_sheet.json" if sheet_path is None else Path(sheet_path)
    try:
        path.relative_to(Path(run_root))
    except ValueError as error:
        raise Step4ImageIntegrityError("image review sheet is outside the current run") from error
    try:
        sheet_raw = path.read_bytes()
        sheet = read_json(path)
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError("image review sheet is missing or invalid") from error
    if not isinstance(sheet, Mapping) or sheet.get("schema") != "image-review-sheet/2.0":
        raise Step4ImageIntegrityError("legacy or invalid image review sheet schema")
    cards = sheet.get("cards")
    if not isinstance(cards, list) or not all(isinstance(card, Mapping) for card in cards):
        raise Step4ImageIntegrityError("image review sheet cards are invalid")
    provenance_file_sha256_by_card = _validate_review_rows(
        run_root=run_root, cards=cards, current_request_bindings=current_request_bindings
    )
    identity = _image_review_identity(
        run_root=run_root,
        sheet_path=path,
        sheet_raw=sheet_raw,
        sheet=sheet,
        cards=cards,
        provenance_file_sha256_by_card=provenance_file_sha256_by_card,
    )
    return {key: identity[key] for key in _IMAGE_REVIEW_EMISSION_IDENTITY_FIELDS} | {
        "sheet_path": identity["sheet_path"],
        "review_sheet_path": identity["review_sheet_path"],
    }


def validate_ingested_image_review(
    *,
    run_root: Path,
    current_request_bindings: Mapping[int, Mapping[str, Any]],
    expected_authority: Mapping[str, Any] | None = None,
) -> None:
    """Revalidate a previously ingested review chain before ordinary resume success."""

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    try:
        sheet_path = checkpoint_dir / "image_review_sheet.json"
        sheet_raw = sheet_path.read_bytes()
        sheet = read_json(sheet_path)
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError("image review sheet is missing or invalid") from error
    if not isinstance(sheet, Mapping):
        raise Step4ImageIntegrityError("image review sheet must be an object")
    if sheet.get("schema") != "image-review-sheet/2.0":
        raise Step4ImageIntegrityError("legacy or invalid image review sheet schema")
    cards = sheet.get("cards")
    if not isinstance(cards, list) or not all(isinstance(card, Mapping) for card in cards):
        raise Step4ImageIntegrityError("image review sheet cards are invalid")
    provenance_file_sha256_by_card = _validate_review_rows(
        run_root=run_root, cards=cards, current_request_bindings=current_request_bindings
    )
    from ..gates.review.image_admission_batch import BatchImageAuthorizationManifest

    manifest_path = checkpoint_dir / "image-authorization-manifest.json"
    try:
        manifest_raw = manifest_path.read_bytes()
        manifest = BatchImageAuthorizationManifest.model_validate(read_json(manifest_path))
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError("image authorization manifest is missing or invalid") from error
    rows_by_index = {int(row["card_index"]): row for row in cards}
    row_indexes = [int(row["card_index"]) for row in cards]
    decision_indexes = [decision.card_index for decision in manifest.decisions]
    if row_indexes != decision_indexes:
        raise Step4ImageIntegrityError("image authorization manifest does not match the review sheet")
    for decision in manifest.decisions:
        row = rows_by_index[decision.card_index]
        for key in ("source_card_id", "render_presentation", "image_sha256", "disposition", "reviewer", "basis"):
            if str(row.get(key)) != str(getattr(decision, key)):
                raise Step4ImageIntegrityError(f"image authorization decision drifted for card {decision.card_index}")
    from ..gates.review.image_admission_batch import build_batch_image_authorization

    rebuilt = build_batch_image_authorization(cards)
    if rebuilt.model_dump(mode="json") != manifest.model_dump(mode="json"):
        raise Step4ImageIntegrityError("image authorization manifest does not match the frozen review sheet")
    current_identity = _image_review_identity(
        run_root=run_root,
        sheet_path=sheet_path,
        sheet_raw=sheet_raw,
        sheet=sheet,
        cards=cards,
        provenance_file_sha256_by_card=provenance_file_sha256_by_card,
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_raw=manifest_raw,
    )
    _validate_expected_image_review_identity(
        expected=expected_authority,
        current=current_identity,
        fields=_IMAGE_REVIEW_AUTHORITY_IDENTITY_FIELDS,
    )


def ingest_image_review_sheet(
    *,
    run_root: Path,
    current_request_bindings: Mapping[int, Mapping[str, Any]] | None = None,
    expected_source_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """检查点②摄取（G-B2）：人工回填 disposition 后构建 v2 批量授权清单。"""

    from ..gates.review.image_admission_batch import (
        BatchImageAuthorizationManifest,
        build_batch_image_authorization,
    )

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    sheet_path = checkpoint_dir / "image_review_sheet.json"
    manifest_path = checkpoint_dir / "image-authorization-manifest.json"
    if not sheet_path.is_file():
        return {"status": "waiting_human", "detail": f"image review sheet not found: {sheet_path}"}
    try:
        sheet_raw = sheet_path.read_bytes()
        sheet = read_json(sheet_path)
        if sheet.get("schema") != "image-review-sheet/2.0":
            raise Step4ImageIntegrityError("legacy or invalid image review sheet schema")
        cards = sheet.get("cards", [])
        if not isinstance(cards, list) or not all(isinstance(card, Mapping) for card in cards):
            raise Step4ImageIntegrityError("image review sheet cards are invalid")
        provenance_file_sha256_by_card = _validate_review_rows(
            run_root=run_root, cards=cards, current_request_bindings=current_request_bindings
        )
        emission_identity = _image_review_identity(
            run_root=run_root,
            sheet_path=sheet_path,
            sheet_raw=sheet_raw,
            sheet=sheet,
            cards=cards,
            provenance_file_sha256_by_card=provenance_file_sha256_by_card,
        )
        if expected_source_identity is not None:
            _validate_expected_image_review_identity(
                expected=expected_source_identity,
                current=emission_identity,
                fields=_IMAGE_REVIEW_EMISSION_IDENTITY_FIELDS,
            )
        undecided = [card["card_index"] for card in cards if card.get("disposition") is None]
        if undecided:
            return {"status": "waiting_human", "detail": f"cards without disposition: {undecided}"}
        rebuilt_manifest = build_batch_image_authorization(cards)
        if manifest_path.exists():
            manifest_raw = manifest_path.read_bytes()
            manifest = BatchImageAuthorizationManifest.model_validate(read_json(manifest_path))
            if manifest.model_dump(mode="json") != rebuilt_manifest.model_dump(mode="json"):
                raise Step4ImageIntegrityError("existing image authorization manifest does not match the review sheet")
        else:
            atomic_write_json(manifest_path, rebuilt_manifest.model_dump(mode="json"))
            manifest_raw = manifest_path.read_bytes()
            manifest = BatchImageAuthorizationManifest.model_validate(read_json(manifest_path))
            if manifest.model_dump(mode="json") != rebuilt_manifest.model_dump(mode="json"):
                raise Step4ImageIntegrityError("persisted image authorization manifest does not match the review sheet")
    except Exception as error:
        return {"status": "refused", "stop_reason": "gate_refused_admission", "detail": sanitized_error_summary(error)}
    authority = _image_review_identity(
        run_root=run_root,
        sheet_path=sheet_path,
        sheet_raw=sheet_raw,
        sheet=sheet,
        cards=cards,
        provenance_file_sha256_by_card=provenance_file_sha256_by_card,
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_raw=manifest_raw,
    )
    return {"status": "ingested", **authority}


# ---------------------------------------------------------------------------
# P4 final release closure V2.  This layer owns bytes and RunState identity;
# the release gate itself remains a pure compiler in final_release_v2.py.
# ---------------------------------------------------------------------------

_FINAL_RELEASE_V2_DIR = "final_release"
_FINAL_RELEASE_V2_FILES = {
    "bundle": "final-release-bundle-v2.json",
    "exclusion": "final-release-exclusion-ledger-v2.json",
    "audit": "final-release-audit-v2.json",
    "receipt": "final-release-closure-v2.json",
}
_FINAL_RELEASE_IDENTITY_FIELDS = frozenset(
    {
        "final_release_status",
        "final_release_receipt_sha256",
        "final_release_receipt_file_sha256",
        "final_release_bundle_sha256",
        "final_release_bundle_file_sha256",
        "final_release_exclusion_sha256",
        "final_release_exclusion_file_sha256",
        "final_release_audit_sha256",
        "final_release_audit_file_sha256",
        "final_release_decision_count",
        "final_release_released_count",
        "final_release_rejected_count",
        "final_release_manual_review_count",
        "final_release_released_card_indexes",
        "final_release_excluded_card_indexes",
        "final_release_release_bundle_present",
    }
)


def _final_release_paths(run_root: Path) -> dict[str, Path]:
    checkpoint_dir = Path(run_root) / "checkpoints" / _FINAL_RELEASE_V2_DIR
    return {name: checkpoint_dir / filename for name, filename in _FINAL_RELEASE_V2_FILES.items()}


def _read_final_release_model(path: Path, model_type: type[Any]) -> tuple[Any, bytes]:
    if not path.is_file():
        raise Step4ImageIntegrityError(f"final release artifact is missing: {path.name}")
    try:
        raw = path.read_bytes()
        model = model_type.model_validate(read_json(path))
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError(f"invalid final release artifact: {path.name}") from error
    return model, raw


def _require_final_release_model(path: Path, expected: Any, *, allow_create: bool) -> tuple[Any, bytes]:
    if path.is_file():
        actual, raw = _read_final_release_model(path, type(expected))
        if actual.model_dump(mode="json") != expected.model_dump(mode="json"):
            raise Step4ImageIntegrityError(f"final release artifact conflicts: {path.name}")
        return actual, raw
    if not allow_create:
        raise Step4ImageIntegrityError(f"frozen final release artifact is missing: {path.name}")
    atomic_write_json(path, expected.model_dump(mode="json"))
    return _read_final_release_model(path, type(expected))


def _load_final_release_p3_artifacts(
    run_root: Path,
) -> tuple[Any | None, Any, Any, Any, bytes, bytes | None, bytes, bytes]:
    from ..gates.review.image_admission_materialization_v2 import (
        BatchImageAdmissionAuditV2,
        BatchPostReviewQueueV2,
        BatchReviewedImageFixtureV2,
        ImageReviewMaterializationReceiptV2,
    )

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    fixture_path = checkpoint_dir / "reviewed-image-fixture-v2.json"
    queue_path = checkpoint_dir / "post-review-queue-v2.json"
    audit_path = checkpoint_dir / "image-admission-audit-v2.json"
    receipt_path = checkpoint_dir / "image-review-materialization-v2.json"
    receipt, receipt_raw = _read_final_release_model(receipt_path, ImageReviewMaterializationReceiptV2)
    queue, queue_raw = _read_final_release_model(queue_path, BatchPostReviewQueueV2)
    audit, audit_raw = _read_final_release_model(audit_path, BatchImageAdmissionAuditV2)
    fixture: Any | None = None
    fixture_raw: bytes | None = None
    if receipt.reviewed_fixture_present:
        fixture, fixture_raw = _read_final_release_model(fixture_path, BatchReviewedImageFixtureV2)
    elif fixture_path.exists():
        raise Step4ImageIntegrityError("P3 zero-approval materialization contains a reviewed fixture")
    return fixture, queue, audit, receipt, receipt_raw, fixture_raw, queue_raw, audit_raw


def _p3_identity_from_receipt(
    *,
    fixture: Any | None,
    queue: Any,
    audit: Any,
    receipt: Any,
    receipt_raw: bytes,
    fixture_raw: bytes | None,
    queue_raw: bytes,
    audit_raw: bytes,
) -> dict[str, Any]:
    return {
        "materialization_status": "frozen",
        "materialization_receipt_sha256": receipt.receipt_sha256,
        "materialization_receipt_file_sha256": sha256_bytes(receipt_raw),
        "reviewed_fixture_present": fixture is not None,
        "reviewed_fixture_sha256": fixture.fixture_sha256 if fixture is not None else None,
        "reviewed_fixture_file_sha256": sha256_bytes(fixture_raw) if fixture_raw is not None else None,
        "post_review_queue_sha256": queue.queue_sha256,
        "post_review_queue_file_sha256": sha256_bytes(queue_raw),
        "admission_audit_sha256": audit.audit_sha256,
        "admission_audit_file_sha256": sha256_bytes(audit_raw),
        "materialized_decision_count": receipt.decision_count,
        "materialized_approved_count": receipt.approved_count,
        "materialized_rejected_count": receipt.rejected_count,
        "materialized_manual_review_count": receipt.manual_review_count,
        "materialized_reviewed_card_indexes": list(receipt.reviewed_card_indexes),
        "materialized_post_review_card_indexes": list(receipt.post_review_card_indexes),
    }


def _load_and_validate_final_release_p3(
    *, run_root: Path, expected_p3_authority: Mapping[str, Any] | None
) -> tuple[Any | None, Any, Any, Any, bytes, bytes | None, bytes, bytes]:
    fixture, queue, audit, receipt, receipt_raw, fixture_raw, queue_raw, audit_raw = (
        _load_final_release_p3_artifacts(Path(run_root))
    )
    derived_identity = _p3_identity_from_receipt(
        fixture=fixture,
        queue=queue,
        audit=audit,
        receipt=receipt,
        receipt_raw=receipt_raw,
        fixture_raw=fixture_raw,
        queue_raw=queue_raw,
        audit_raw=audit_raw,
    )
    if expected_p3_authority is not None:
        for field, expected_value in derived_identity.items():
            if field in expected_p3_authority and expected_p3_authority[field] != expected_value:
                raise Step4ImageIntegrityError(f"P3 materialization identity drifted: {field}")
    p2_fields = set(_IMAGE_REVIEW_AUTHORITY_IDENTITY_FIELDS)
    if expected_p3_authority is not None and p2_fields.issubset(expected_p3_authority):
        expected = dict(expected_p3_authority)
    else:
        current_p2, _ = _load_image_review_materialization_inputs(
            run_root=Path(run_root),
            current_request_bindings=None,
            expected_authority=None,
        )
        expected = current_p2
    expected.update(derived_identity)
    validate_materialized_image_review_v2(
        run_root=Path(run_root),
        expected_authority=expected,
    )
    return fixture, queue, audit, receipt, receipt_raw, fixture_raw, queue_raw, audit_raw


def _build_final_release_receipt(
    *,
    p3_receipt: Any,
    p3_receipt_raw: bytes,
    fixture: Any | None,
    queue: Any,
    audit: Any,
    p4_bundle: Any | None,
    p4_bundle_raw: bytes | None,
    p4_exclusion: Any,
    p4_exclusion_raw: bytes,
    p4_audit: Any,
    p4_audit_raw: bytes,
) -> Any:
    from ..gates.release.final_release_v2 import FinalReleaseClosureReceiptV2

    return hash_model(
        FinalReleaseClosureReceiptV2,
        {
            "reviewed_fixture_sha256": fixture.fixture_sha256 if fixture is not None else None,
            "reviewed_fixture_file_sha256": p3_receipt.reviewed_fixture_file_sha256,
            "post_review_queue_sha256": queue.queue_sha256,
            "post_review_queue_file_sha256": p3_receipt.post_review_queue_file_sha256,
            "admission_audit_sha256": audit.audit_sha256,
            "admission_audit_file_sha256": p3_receipt.admission_audit_file_sha256,
            "materialization_receipt_sha256": p3_receipt.receipt_sha256,
            "materialization_receipt_file_sha256": sha256_bytes(p3_receipt_raw),
            "bundle_sha256": p4_bundle.bundle_sha256 if p4_bundle is not None else None,
            "bundle_file_sha256": sha256_bytes(p4_bundle_raw) if p4_bundle_raw is not None else None,
            "exclusion_sha256": p4_exclusion.exclusion_sha256,
            "exclusion_file_sha256": sha256_bytes(p4_exclusion_raw),
            "audit_sha256": p4_audit.audit_sha256,
            "audit_file_sha256": sha256_bytes(p4_audit_raw),
            "decision_count": p3_receipt.decision_count,
            "released_count": p3_receipt.approved_count,
            "rejected_count": p3_receipt.rejected_count,
            "manual_review_count": p3_receipt.manual_review_count,
            "released_card_indexes": tuple(p4_bundle.release_card_indexes) if p4_bundle is not None else (),
            "excluded_card_indexes": tuple(p4_exclusion.excluded_card_indexes),
            "release_bundle_present": p4_bundle is not None,
        },
        "closure_sha256",
    )


def _final_release_identity(
    *,
    receipt: Any,
    receipt_raw: bytes,
    bundle: Any | None,
    bundle_raw: bytes | None,
    exclusion: Any,
    exclusion_raw: bytes,
    audit: Any,
    audit_raw: bytes,
) -> dict[str, Any]:
    return {
        "final_release_status": "frozen",
        "final_release_receipt_sha256": receipt.closure_sha256,
        "final_release_receipt_file_sha256": sha256_bytes(receipt_raw),
        "final_release_bundle_sha256": bundle.bundle_sha256 if bundle is not None else None,
        "final_release_bundle_file_sha256": sha256_bytes(bundle_raw) if bundle_raw is not None else None,
        "final_release_exclusion_sha256": exclusion.exclusion_sha256,
        "final_release_exclusion_file_sha256": sha256_bytes(exclusion_raw),
        "final_release_audit_sha256": audit.audit_sha256,
        "final_release_audit_file_sha256": sha256_bytes(audit_raw),
        "final_release_decision_count": receipt.decision_count,
        "final_release_released_count": receipt.released_count,
        "final_release_rejected_count": receipt.rejected_count,
        "final_release_manual_review_count": receipt.manual_review_count,
        "final_release_released_card_indexes": list(receipt.released_card_indexes),
        "final_release_excluded_card_indexes": list(receipt.excluded_card_indexes),
        "final_release_release_bundle_present": receipt.release_bundle_present,
    }


def materialize_final_release_v2(
    *,
    run_root: Path,
    expected_p3_authority: Mapping[str, Any] | None = None,
    expected_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize or recover P4 files and freeze the final closure receipt."""

    from ..gates.release.final_release_v2 import derive_final_release_v2

    if expected_p3_authority is not None and expected_authority is not None:
        raise TypeError("P4 expected authority was supplied more than once")
    p3_expected = expected_p3_authority if expected_p3_authority is not None else expected_authority
    fixture, queue, audit, p3_receipt, p3_receipt_raw, fixture_raw, queue_raw, audit_raw = (
        _load_and_validate_final_release_p3(run_root=Path(run_root), expected_p3_authority=p3_expected)
    )
    derived = derive_final_release_v2(
        reviewed_fixture=fixture,
        post_review_queue=queue,
        admission_audit=audit,
        materialization_receipt=p3_receipt,
    )
    paths = _final_release_paths(Path(run_root))
    receipt_exists = paths["receipt"].is_file()
    allow_create = not receipt_exists
    paths["bundle"].parent.mkdir(parents=True, exist_ok=True)
    bundle = None
    bundle_raw: bytes | None = None
    if derived.release_bundle is None:
        if paths["bundle"].exists():
            raise Step4ImageIntegrityError("zero-approved final release cannot contain a release bundle")
    else:
        bundle, bundle_raw = _require_final_release_model(paths["bundle"], derived.release_bundle, allow_create=allow_create)
    exclusion, exclusion_raw = _require_final_release_model(
        paths["exclusion"], derived.exclusion_ledger, allow_create=allow_create
    )
    final_audit, final_audit_raw = _require_final_release_model(paths["audit"], derived.audit, allow_create=allow_create)
    expected_receipt = _build_final_release_receipt(
        p3_receipt=p3_receipt,
        p3_receipt_raw=p3_receipt_raw,
        fixture=fixture,
        queue=queue,
        audit=audit,
        p4_bundle=bundle,
        p4_bundle_raw=bundle_raw,
        p4_exclusion=exclusion,
        p4_exclusion_raw=exclusion_raw,
        p4_audit=final_audit,
        p4_audit_raw=final_audit_raw,
    )
    if paths["receipt"].is_file():
        receipt, receipt_raw = _read_final_release_model(paths["receipt"], type(expected_receipt))
        if receipt.model_dump(mode="json") != expected_receipt.model_dump(mode="json"):
            raise Step4ImageIntegrityError("final release closure receipt conflicts with its artifacts")
    else:
        atomic_write_json(paths["receipt"], expected_receipt.model_dump(mode="json"))
        receipt, receipt_raw = _read_final_release_model(paths["receipt"], type(expected_receipt))
    return _final_release_identity(
        receipt=receipt,
        receipt_raw=receipt_raw,
        bundle=bundle,
        bundle_raw=bundle_raw,
        exclusion=exclusion,
        exclusion_raw=exclusion_raw,
        audit=final_audit,
        audit_raw=final_audit_raw,
    )


def validate_materialized_final_release_v2(
    *,
    run_root: Path,
    expected_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate frozen P4 bytes and identity without regenerating anything."""

    from ..gates.release.final_release_v2 import (
        FinalReleaseAuditV2,
        FinalReleaseBundleV2,
        FinalReleaseClosureReceiptV2,
        FinalReleaseExclusionLedgerV2,
        derive_final_release_v2,
    )

    if expected_authority is None or expected_authority.get("final_release_status") != "frozen":
        raise Step4ImageIntegrityError("frozen final release status is missing")
    paths = _final_release_paths(Path(run_root))
    if not paths["receipt"].is_file():
        raise Step4ImageIntegrityError("frozen final release closure receipt is missing")
    fixture, queue, audit, p3_receipt, p3_receipt_raw, _fixture_raw, _queue_raw, _audit_raw = (
        _load_and_validate_final_release_p3(run_root=Path(run_root), expected_p3_authority=expected_authority)
    )
    derived = derive_final_release_v2(
        reviewed_fixture=fixture,
        post_review_queue=queue,
        admission_audit=audit,
        materialization_receipt=p3_receipt,
    )
    bundle = None
    bundle_raw: bytes | None = None
    if derived.release_bundle is None:
        if paths["bundle"].exists():
            raise Step4ImageIntegrityError("zero-approved final release has an unexpected bundle")
    else:
        bundle, bundle_raw = _read_final_release_model(paths["bundle"], FinalReleaseBundleV2)
        if bundle.model_dump(mode="json") != derived.release_bundle.model_dump(mode="json"):
            raise Step4ImageIntegrityError("frozen final release bundle drifted")
    exclusion, exclusion_raw = _read_final_release_model(paths["exclusion"], FinalReleaseExclusionLedgerV2)
    final_audit, final_audit_raw = _read_final_release_model(paths["audit"], FinalReleaseAuditV2)
    if exclusion.model_dump(mode="json") != derived.exclusion_ledger.model_dump(mode="json"):
        raise Step4ImageIntegrityError("frozen final release exclusion ledger drifted")
    if final_audit.model_dump(mode="json") != derived.audit.model_dump(mode="json"):
        raise Step4ImageIntegrityError("frozen final release audit drifted")
    receipt, receipt_raw = _read_final_release_model(paths["receipt"], FinalReleaseClosureReceiptV2)
    expected_receipt = _build_final_release_receipt(
        p3_receipt=p3_receipt,
        p3_receipt_raw=p3_receipt_raw,
        fixture=fixture,
        queue=queue,
        audit=audit,
        p4_bundle=bundle,
        p4_bundle_raw=bundle_raw,
        p4_exclusion=exclusion,
        p4_exclusion_raw=exclusion_raw,
        p4_audit=final_audit,
        p4_audit_raw=final_audit_raw,
    )
    if receipt.model_dump(mode="json") != expected_receipt.model_dump(mode="json"):
        raise Step4ImageIntegrityError("frozen final release closure receipt drifted")
    current = _final_release_identity(
        receipt=receipt,
        receipt_raw=receipt_raw,
        bundle=bundle,
        bundle_raw=bundle_raw,
        exclusion=exclusion,
        exclusion_raw=exclusion_raw,
        audit=final_audit,
        audit_raw=final_audit_raw,
    )
    for key in _FINAL_RELEASE_IDENTITY_FIELDS:
        if key not in expected_authority:
            raise Step4ImageIntegrityError(f"frozen final release identity is missing: {key}")
        if current.get(key) != expected_authority[key]:
            raise Step4ImageIntegrityError(f"frozen final release identity drifted: {key}")
    return current


# ---------------------------------------------------------------------------
# P5 publication authority V2.  Publication is an explicit, downstream
# boundary: it copies only the bytes selected by the already-frozen P4 bundle,
# waits for a separately supplied human authorization, and writes its receipt
# last.  No delivery or external side effect belongs here.
# ---------------------------------------------------------------------------

_PUBLICATION_V2_DIR = "publication"
_PUBLICATION_V2_FILES = {
    "manifest": "publication-manifest-v2.json",
    "authorization": "publication-authorization-v2.json",
    "receipt": "publication-receipt-v2.json",
}
_PUBLICATION_IDENTITY_FIELDS = frozenset(
    {
        "publication_status",
        "publication_manifest_sha256",
        "publication_manifest_file_sha256",
        "publication_authorization_sha256",
        "publication_authorization_file_sha256",
        "publication_receipt_sha256",
        "publication_receipt_file_sha256",
        "publication_artifact_count",
        "publication_package_sha256",
    }
)


def _publication_paths(run_root: Path) -> dict[str, Path]:
    checkpoint_dir = Path(run_root) / "checkpoints" / _PUBLICATION_V2_DIR
    return {name: checkpoint_dir / filename for name, filename in _PUBLICATION_V2_FILES.items()}


def _publication_artifact_path(run_root: Path, card_index: int, name: str) -> Path:
    return Path(run_root) / "checkpoints" / _PUBLICATION_V2_DIR / "artifacts" / f"card-{card_index:04d}" / name


def _read_publication_model(path: Path, model_type: type[Any]) -> tuple[Any, bytes]:
    if path.is_symlink() or not path.is_file():
        raise Step4ImageIntegrityError(f"publication artifact is missing: {path.name}")
    try:
        raw = path.read_bytes()
        model = model_type.model_validate(read_json(path))
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError(f"invalid publication artifact: {path.name}") from error
    return model, raw


def _persist_publication_model(
    path: Path, expected: Any, *, allow_create: bool, label: str
) -> tuple[Any, bytes]:
    if path.is_file():
        actual, raw = _read_publication_model(path, type(expected))
        if actual.model_dump(mode="json") != expected.model_dump(mode="json"):
            raise Step4ImageIntegrityError(f"publication artifact conflicts: {label}")
        return actual, raw
    if path.exists() or not allow_create:
        raise Step4ImageIntegrityError(f"publication artifact is missing or not a regular file: {label}")
    atomic_write_json(path, expected.model_dump(mode="json"))
    return _read_publication_model(path, type(expected))


def _persist_publication_bytes(path: Path, content: bytes, *, allow_create: bool, label: str) -> bytes:
    if path.is_symlink():
        raise Step4ImageIntegrityError(f"publication byte artifact is not a regular file: {label}")
    if path.is_file():
        actual = path.read_bytes()
        if actual != content:
            raise Step4ImageIntegrityError(f"publication byte artifact conflicts: {label}")
        return actual
    if path.exists() or not allow_create:
        raise Step4ImageIntegrityError(f"publication byte artifact is missing or not a regular file: {label}")
    atomic_write_bytes(path, content)
    return path.read_bytes()


def _resolve_publication_p4_authority(
    expected_p4_authority: Mapping[str, Any] | None,
    p4_authority: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if expected_p4_authority is not None and p4_authority is not None and dict(expected_p4_authority) != dict(p4_authority):
        raise TypeError("P4 authority was supplied more than once")
    resolved = expected_p4_authority if expected_p4_authority is not None else p4_authority
    if resolved is None:
        raise TypeError("frozen P4 authority is required")
    return resolved


def _load_publication_p4(
    *, run_root: Path, expected_p4_authority: Mapping[str, Any]
) -> tuple[Any, Any | None, Any, Mapping[str, Any]]:
    from ..gates.release.final_release_v2 import (
        FinalReleaseAuditV2,
        FinalReleaseBundleV2,
        FinalReleaseClosureReceiptV2,
        FinalReleaseExclusionLedgerV2,
    )

    p4_identity = validate_materialized_final_release_v2(
        run_root=Path(run_root), expected_authority=expected_p4_authority
    )
    paths = _final_release_paths(Path(run_root))
    receipt, _receipt_raw = _read_final_release_model(paths["receipt"], FinalReleaseClosureReceiptV2)
    bundle = None
    if receipt.release_bundle_present:
        bundle, _bundle_raw = _read_final_release_model(paths["bundle"], FinalReleaseBundleV2)
    elif paths["bundle"].exists():
        raise Step4ImageIntegrityError("zero-release P4 contains an unexpected bundle")
    _exclusion, _exclusion_raw = _read_final_release_model(paths["exclusion"], FinalReleaseExclusionLedgerV2)
    audit, _audit_raw = _read_final_release_model(paths["audit"], FinalReleaseAuditV2)
    return receipt, bundle, audit, p4_identity


def _publication_source_artifacts(
    *, run_root: Path, p4_bundle: Any | None
) -> tuple[list[Any], list[tuple[Path, bytes]]]:
    from ..gates.release.publication_v2 import build_publication_artifact_v2

    descriptors: list[Any] = []
    copies: list[tuple[Path, bytes]] = []
    if p4_bundle is None:
        return descriptors, copies
    storage = Path(run_root) / "step4"
    for card in p4_bundle.release_cards:
        provenance = validate_step4_image_artifact(
            storage=storage,
            card_index=card.card_index,
            source_card_id=card.source_card_id,
            sample_id=card.sample_id,
            render_presentation=card.render_presentation,
            http_payload_sha256=card.http_payload_sha256,
            image_path_value=card.image_path,
            image_sha256=card.image_sha256,
            provenance_sha256=card.image_provenance_sha256,
        )
        if provenance.provenance_sha256 != card.image_provenance_sha256:
            raise Step4ImageIntegrityError("P4 image provenance identity does not match the source card")
        image_path = storage / Path(card.image_path)
        provenance_path = step4_image_provenance_path(storage, card.card_index, card.source_card_id)
        image_bytes = image_path.read_bytes()
        provenance_bytes = provenance_path.read_bytes()
        if sha256_bytes(provenance_bytes) != card.provenance_file_sha256:
            raise Step4ImageIntegrityError("P4 provenance file bytes drifted from the source card")
        image_descriptor = build_publication_artifact_v2(
            artifact_id=f"card:{card.card_index}:image",
            relative_path=f"artifacts/card-{card.card_index:04d}/image.png",
            media_type="image/png",
            content=image_bytes,
            source_release_card_sha256=card.release_card_sha256,
        )
        provenance_descriptor = build_publication_artifact_v2(
            artifact_id=f"card:{card.card_index}:provenance",
            relative_path=f"artifacts/card-{card.card_index:04d}/image-provenance.json",
            media_type="application/json",
            content=provenance_bytes,
            source_release_card_sha256=card.release_card_sha256,
        )
        descriptors.extend((image_descriptor, provenance_descriptor))
        copies.extend(
            (
                (_publication_artifact_path(run_root, card.card_index, "image.png"), image_bytes),
                (
                    _publication_artifact_path(run_root, card.card_index, "image-provenance.json"),
                    provenance_bytes,
                ),
            )
        )
    return descriptors, copies


def _publication_identity(
    *,
    manifest: Any,
    manifest_raw: bytes,
    authorization: Any,
    authorization_raw: bytes,
    receipt: Any,
    receipt_raw: bytes,
) -> dict[str, Any]:
    return {
        "publication_status": "frozen",
        "publication_manifest_sha256": manifest.manifest_sha256,
        "publication_manifest_file_sha256": sha256_bytes(manifest_raw),
        "publication_authorization_sha256": authorization.authorization_sha256,
        "publication_authorization_file_sha256": sha256_bytes(authorization_raw),
        "publication_receipt_sha256": receipt.receipt_sha256,
        "publication_receipt_file_sha256": sha256_bytes(receipt_raw),
        "publication_artifact_count": manifest.artifact_count,
        "publication_package_sha256": receipt.package_sha256,
    }


def _publication_pending_identity(*, manifest: Any, manifest_raw: bytes) -> dict[str, Any]:
    return {
        "publication_status": "waiting_human",
        "publication_manifest_sha256": manifest.manifest_sha256,
        "publication_manifest_file_sha256": sha256_bytes(manifest_raw),
        "publication_artifact_count": manifest.artifact_count,
    }


def _coerce_publication_authorization(value: Any | None) -> Any | None:
    if value is None:
        return None
    from ..gates.release.publication_v2 import PublicationAuthorizationV2

    if isinstance(value, PublicationAuthorizationV2):
        return value
    if isinstance(value, Mapping):
        try:
            return PublicationAuthorizationV2.model_validate(dict(value))
        except (TypeError, ValueError) as error:
            raise Step4ImageIntegrityError("publication authorization is invalid") from error
    raise TypeError("publication authorization must be PublicationAuthorizationV2 or a mapping")


def materialize_publication_v2(
    *,
    run_root: Path,
    expected_p4_authority: Mapping[str, Any] | None = None,
    p4_authority: Mapping[str, Any] | None = None,
    authorization: Any | None = None,
    publication_authorization: Any | None = None,
) -> dict[str, Any]:
    """Materialize or recover P5 authority; receipt creation is receipt-last."""

    from ..gates.release.publication_v2 import (
        PublicationAuthorizationV2,
        PublicationManifestV2,
        PublicationReceiptV2,
        build_publication_manifest_v2,
        derive_publication_v2,
    )

    authority = _resolve_publication_p4_authority(expected_p4_authority, p4_authority)
    if authorization is not None and publication_authorization is not None and authorization != publication_authorization:
        raise TypeError("publication authorization was supplied more than once")
    supplied_authorization = _coerce_publication_authorization(
        authorization if authorization is not None else publication_authorization
    )
    paths = _publication_paths(Path(run_root))
    receipt_exists = paths["receipt"].is_file()
    if paths["receipt"].exists() and not receipt_exists:
        raise Step4ImageIntegrityError("publication receipt is not a regular file")
    if receipt_exists:
        existing_manifest, existing_manifest_raw = _read_publication_model(
            paths["manifest"], PublicationManifestV2
        )
        existing_authorization, existing_authorization_raw = _read_publication_model(
            paths["authorization"], PublicationAuthorizationV2
        )
        existing_receipt, existing_receipt_raw = _read_publication_model(
            paths["receipt"], PublicationReceiptV2
        )
        existing_identity = _publication_identity(
            manifest=existing_manifest,
            manifest_raw=existing_manifest_raw,
            authorization=existing_authorization,
            authorization_raw=existing_authorization_raw,
            receipt=existing_receipt,
            receipt_raw=existing_receipt_raw,
        )
        for field in _PUBLICATION_IDENTITY_FIELDS.intersection(authority):
            if authority[field] != existing_identity.get(field):
                raise Step4ImageIntegrityError(f"frozen publication identity drifted: {field}")
        if supplied_authorization is not None and (
            supplied_authorization.model_dump(mode="json")
            != existing_authorization.model_dump(mode="json")
        ):
            raise Step4ImageIntegrityError("publication authorization conflicts with the frozen authorization")
        return validate_materialized_publication_v2(
            run_root=Path(run_root),
            expected_authority=dict(authority) | existing_identity,
        )

    p4_receipt, p4_bundle, p4_audit, p4_identity = _load_publication_p4(
        run_root=Path(run_root), expected_p4_authority=authority
    )
    descriptors, copies = _publication_source_artifacts(run_root=Path(run_root), p4_bundle=p4_bundle)
    manifest_expected = build_publication_manifest_v2(
        final_release_receipt=p4_receipt,
        final_release_bundle=p4_bundle,
        final_release_audit=p4_audit,
        p4_receipt_identity=p4_identity,
        artifacts=descriptors,
    )
    allow_create = True
    for destination, content in copies:
        _persist_publication_bytes(destination, content, allow_create=allow_create, label=destination.name)
    manifest, manifest_raw = _persist_publication_model(
        paths["manifest"], manifest_expected, allow_create=allow_create, label=paths["manifest"].name
    )

    stored_authorization: PublicationAuthorizationV2 | None = None
    authorization_raw: bytes | None = None
    if paths["authorization"].exists():
        if not paths["authorization"].is_file():
            raise Step4ImageIntegrityError("publication authorization is not a regular file")
        stored_authorization, authorization_raw = _read_publication_model(
            paths["authorization"], PublicationAuthorizationV2
        )
        if supplied_authorization is not None and stored_authorization.model_dump(mode="json") != supplied_authorization.model_dump(mode="json"):
            raise Step4ImageIntegrityError("publication authorization conflicts with the supplied authorization")
    elif supplied_authorization is not None:
        stored_authorization, authorization_raw = _persist_publication_model(
            paths["authorization"],
            supplied_authorization,
            allow_create=True,
            label=paths["authorization"].name,
        )
    else:
        return _publication_pending_identity(manifest=manifest, manifest_raw=manifest_raw)

    if stored_authorization is None or authorization_raw is None:
        raise Step4ImageIntegrityError("publication authorization is missing")
    derived = derive_publication_v2(
        final_release_receipt=p4_receipt,
        final_release_bundle=p4_bundle,
        final_release_audit=p4_audit,
        p4_receipt_identity=p4_identity,
        artifacts=descriptors,
        authorization=stored_authorization,
        publication_manifest=manifest,
        publication_manifest_file_sha256=sha256_bytes(manifest_raw),
    )
    atomic_write_json(paths["receipt"], derived.receipt.model_dump(mode="json"))
    receipt, receipt_raw = _read_publication_model(paths["receipt"], type(derived.receipt))
    if receipt.model_dump(mode="json") != derived.receipt.model_dump(mode="json"):
        raise Step4ImageIntegrityError("publication receipt did not persist deterministically")
    return _publication_identity(
        manifest=manifest,
        manifest_raw=manifest_raw,
        authorization=stored_authorization,
        authorization_raw=authorization_raw,
        receipt=receipt,
        receipt_raw=receipt_raw,
    )


def validate_materialized_publication_v2(
    *, run_root: Path, expected_authority: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate frozen P5 bytes and P4-derived package descriptors without writing."""

    from ..gates.release.publication_v2 import (
        PublicationAuthorizationV2,
        PublicationManifestV2,
        PublicationReceiptV2,
        build_publication_manifest_v2,
        derive_publication_v2,
    )

    if expected_authority.get("publication_status") != "frozen":
        raise Step4ImageIntegrityError("frozen publication status is missing")
    missing = _PUBLICATION_IDENTITY_FIELDS.difference(expected_authority)
    if missing:
        raise Step4ImageIntegrityError(
            f"frozen publication identity is missing: {', '.join(sorted(missing))}"
        )
    p4_receipt, p4_bundle, p4_audit, p4_identity = _load_publication_p4(
        run_root=Path(run_root), expected_p4_authority=expected_authority
    )
    paths = _publication_paths(Path(run_root))
    manifest, manifest_raw = _read_publication_model(paths["manifest"], PublicationManifestV2)
    authorization, authorization_raw = _read_publication_model(
        paths["authorization"], PublicationAuthorizationV2
    )
    receipt, receipt_raw = _read_publication_model(paths["receipt"], PublicationReceiptV2)
    descriptors, copies = _publication_source_artifacts(run_root=Path(run_root), p4_bundle=p4_bundle)
    for destination, content in copies:
        _persist_publication_bytes(destination, content, allow_create=False, label=destination.name)
    expected_manifest = build_publication_manifest_v2(
        final_release_receipt=p4_receipt,
        final_release_bundle=p4_bundle,
        final_release_audit=p4_audit,
        p4_receipt_identity=p4_identity,
        artifacts=descriptors,
    )
    if manifest.model_dump(mode="json") != expected_manifest.model_dump(mode="json"):
        raise Step4ImageIntegrityError("frozen publication manifest drifted")
    derived = derive_publication_v2(
        final_release_receipt=p4_receipt,
        final_release_bundle=p4_bundle,
        final_release_audit=p4_audit,
        p4_receipt_identity=p4_identity,
        artifacts=descriptors,
        authorization=authorization,
        publication_manifest=manifest,
        publication_manifest_file_sha256=sha256_bytes(manifest_raw),
    )
    if receipt.model_dump(mode="json") != derived.receipt.model_dump(mode="json"):
        raise Step4ImageIntegrityError("frozen publication receipt drifted")
    current = _publication_identity(
        manifest=manifest,
        manifest_raw=manifest_raw,
        authorization=authorization,
        authorization_raw=authorization_raw,
        receipt=receipt,
        receipt_raw=receipt_raw,
    )
    for field in _PUBLICATION_IDENTITY_FIELDS:
        if current.get(field) != expected_authority[field]:
            raise Step4ImageIntegrityError(f"frozen publication identity drifted: {field}")
    return current


# ---------------------------------------------------------------------------
# P6 external delivery V2.  Delivery is downstream of P5 and owns a separate
# checkpoint; it never rewrites publication artifacts or publication authority.
# ---------------------------------------------------------------------------

_DELIVERY_V2_DIR = "delivery"
_DELIVERY_V2_FILES = {
    "request": "delivery-request-v2.json",
    "execution": "delivery-execution-v2.json",
    "receipt": "delivery-receipt-v2.json",
}
_DELIVERY_IDENTITY_FIELDS = frozenset(
    {
        "delivery_status",
        "delivery_request_sha256",
        "delivery_request_file_sha256",
        "delivery_target_sha256",
        "delivery_operation_id",
        "delivery_identity",
        "idempotency_key",
        "delivery_execution_sha256",
        "delivery_execution_file_sha256",
        "delivery_receipt_sha256",
        "delivery_receipt_file_sha256",
    }
)


def _delivery_paths(run_root: Path) -> dict[str, Path]:
    checkpoint_dir = Path(run_root) / "checkpoints" / _DELIVERY_V2_DIR
    return {name: checkpoint_dir / filename for name, filename in _DELIVERY_V2_FILES.items()}


def delivery_v2_paths(run_root: Path) -> dict[str, Path]:
    """Return the P6 checkpoint paths without creating any files."""

    return _delivery_paths(Path(run_root))


def _read_delivery_model(path: Path, model_type: type[Any]) -> tuple[Any, bytes]:
    if not path.is_file():
        raise Step4ImageIntegrityError(f"delivery artifact is missing: {path.name}")
    try:
        raw = path.read_bytes()
        model = model_type.model_validate(read_json(path))
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError(f"invalid delivery artifact: {path.name}") from error
    return model, raw


def _persist_delivery_model(
    path: Path,
    expected: Any,
    *,
    allow_create: bool,
    label: str,
    allow_retry_replace: bool = False,
) -> tuple[Any, bytes]:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Step4ImageIntegrityError(f"delivery artifact is not a regular file: {label}")
    if path.is_file():
        actual, raw = _read_delivery_model(path, type(expected))
        if actual.model_dump(mode="json") == expected.model_dump(mode="json"):
            return actual, raw
        if not allow_retry_replace:
            raise Step4ImageIntegrityError(f"delivery artifact conflicts: {label}")
        from ..gates.release.delivery_v2 import DeliveryExecutionV2

        if not isinstance(actual, DeliveryExecutionV2) or not isinstance(expected, DeliveryExecutionV2):
            raise Step4ImageIntegrityError(f"delivery artifact conflicts: {label}")
        same_attempt_completion = (
            actual.delivery_identity == expected.delivery_identity
            and actual.attempt_number == expected.attempt_number
            and (
                (
                    actual.execution_status == "executing"
                    and expected.execution_status in {"completed", "failed", "unknown"}
                )
                or (actual.execution_status == "prepared" and expected.execution_status == "executing")
            )
        )
        next_attempt = (
            actual.delivery_identity == expected.delivery_identity
            and expected.attempt_number > actual.attempt_number
            and actual.execution_status in {"failed", "unknown"}
            and expected.execution_status in {"prepared", "executing"}
        )
        if not (same_attempt_completion or next_attempt):
            raise Step4ImageIntegrityError(f"delivery artifact conflicts: {label}")
        atomic_write_json(path, expected.model_dump(mode="json"))
        return _read_delivery_model(path, type(expected))
    if not allow_create:
        raise Step4ImageIntegrityError(f"frozen delivery artifact is missing: {label}")
    atomic_write_json(path, expected.model_dump(mode="json"))
    return _read_delivery_model(path, type(expected))


def _validate_delivery_models(
    *,
    request: Any,
    execution: Any,
    receipt: Any,
) -> None:
    from ..gates.release.delivery_v2 import (
        DeliveryExecutionV2,
        DeliveryReceiptV2,
        DeliveryRequestV2,
    )

    if not isinstance(request, DeliveryRequestV2):
        raise Step4ImageIntegrityError("delivery request is not V2")
    if not isinstance(execution, DeliveryExecutionV2):
        raise Step4ImageIntegrityError("delivery execution is not V2")
    if not isinstance(receipt, DeliveryReceiptV2):
        raise Step4ImageIntegrityError("delivery receipt is not V2")
    if execution.delivery_identity != request.delivery_identity:
        raise Step4ImageIntegrityError("delivery execution identity does not match request")
    from ..gates.release.delivery_v2 import build_delivery_receipt_v2

    expected_receipt = build_delivery_receipt_v2(
        request=request,
        delivery_status=receipt.delivery_status,
        remote_reference=receipt.remote_reference,
    )
    if receipt.model_dump(mode="json") != expected_receipt.model_dump(mode="json"):
        raise Step4ImageIntegrityError("delivery receipt identity does not match request")
    if receipt.delivery_status != execution.execution_status:
        raise Step4ImageIntegrityError("delivery receipt status does not match execution")


def _delivery_identity(
    *,
    paths: Mapping[str, Path],
    request: Any,
    request_raw: bytes,
    execution: Any,
    execution_raw: bytes,
    receipt: Any | None,
    receipt_raw: bytes | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "delivery_status": receipt.delivery_status if receipt is not None else execution.execution_status,
        "delivery_request_sha256": request.request_sha256,
        "delivery_request_file_sha256": sha256_bytes(request_raw),
        "delivery_target_sha256": request.target_sha256,
        "delivery_operation_id": request.delivery_operation_id,
        "delivery_identity": request.delivery_identity,
        "idempotency_key": request.idempotency_key,
        "delivery_execution_sha256": execution.execution_sha256,
        "delivery_execution_file_sha256": sha256_bytes(execution_raw),
        "delivery_request_path": str(paths["request"]),
        "delivery_execution_path": str(paths["execution"]),
    }
    if receipt is not None and receipt_raw is not None:
        result.update(
            {
                "delivery_receipt_sha256": receipt.receipt_sha256,
                "delivery_receipt_file_sha256": sha256_bytes(receipt_raw),
                "delivery_receipt_path": str(paths["receipt"]),
            }
        )
    return result


def materialize_delivery_v2(
    *,
    run_root: Path,
    request: Any,
    execution: Any | None = None,
    receipt: Any | None = None,
    allow_retry_replace: bool = False,
) -> dict[str, Any]:
    """Persist P6 request/execution and, last, its frozen receipt.

    Existing request and receipt bytes are immutable.  A non-frozen execution
    may be replaced only for an explicit same-identity retry after a failed or
    unknown attempt; callers must never use that escape to change the request.
    """

    from ..gates.release.delivery_v2 import (
        DeliveryExecutionV2,
        DeliveryReceiptV2,
        DeliveryRequestV2,
    )

    if not isinstance(request, DeliveryRequestV2):
        raise TypeError("delivery request must be DeliveryRequestV2")
    if execution is not None and not isinstance(execution, DeliveryExecutionV2):
        raise TypeError("delivery execution must be DeliveryExecutionV2")
    if receipt is not None and not isinstance(receipt, DeliveryReceiptV2):
        raise TypeError("delivery receipt must be DeliveryReceiptV2")
    if receipt is not None and execution is None:
        raise ValueError("delivery receipt requires its execution")

    paths = _delivery_paths(Path(run_root))
    receipt_exists = paths["receipt"].exists()
    if receipt_exists:
        existing_request, request_raw = _read_delivery_model(paths["request"], DeliveryRequestV2)
        existing_execution, execution_raw = _read_delivery_model(paths["execution"], DeliveryExecutionV2)
        existing_receipt, receipt_raw = _read_delivery_model(paths["receipt"], DeliveryReceiptV2)
        _validate_delivery_models(
            request=existing_request,
            execution=existing_execution,
            receipt=existing_receipt,
        )
        if existing_request.model_dump(mode="json") != request.model_dump(mode="json"):
            raise Step4ImageIntegrityError("frozen delivery request conflicts")
        if execution is not None and existing_execution.model_dump(mode="json") != execution.model_dump(mode="json"):
            raise Step4ImageIntegrityError("frozen delivery execution conflicts")
        if receipt is not None and existing_receipt.model_dump(mode="json") != receipt.model_dump(mode="json"):
            raise Step4ImageIntegrityError("frozen delivery receipt conflicts")
        return _delivery_identity(
            paths=paths,
            request=existing_request,
            request_raw=request_raw,
            execution=existing_execution,
            execution_raw=execution_raw,
            receipt=existing_receipt,
            receipt_raw=receipt_raw,
        )

    stored_request, request_raw = _persist_delivery_model(
        paths["request"], request, allow_create=True, label=paths["request"].name
    )
    if execution is None:
        return {
            "delivery_status": "prepared",
            "delivery_request_sha256": stored_request.request_sha256,
            "delivery_request_file_sha256": sha256_bytes(request_raw),
            "delivery_target_sha256": stored_request.target_sha256,
            "delivery_operation_id": stored_request.delivery_operation_id,
            "delivery_identity": stored_request.delivery_identity,
            "idempotency_key": stored_request.idempotency_key,
            "delivery_request_path": str(paths["request"]),
        }

    stored_execution, execution_raw = _persist_delivery_model(
        paths["execution"],
        execution,
        allow_create=True,
        label=paths["execution"].name,
        allow_retry_replace=allow_retry_replace,
    )
    if stored_execution.delivery_identity != stored_request.delivery_identity:
        raise Step4ImageIntegrityError("delivery execution identity does not match request")
    if receipt is None:
        return _delivery_identity(
            paths=paths,
            request=stored_request,
            request_raw=request_raw,
            execution=stored_execution,
            execution_raw=execution_raw,
            receipt=None,
            receipt_raw=None,
        )

    stored_receipt, receipt_raw = _persist_delivery_model(
        paths["receipt"], receipt, allow_create=True, label=paths["receipt"].name
    )
    _validate_delivery_models(
        request=stored_request,
        execution=stored_execution,
        receipt=stored_receipt,
    )
    return _delivery_identity(
        paths=paths,
        request=stored_request,
        request_raw=request_raw,
        execution=stored_execution,
        execution_raw=execution_raw,
        receipt=stored_receipt,
        receipt_raw=receipt_raw,
    )


def validate_materialized_delivery_v2(
    *,
    run_root: Path,
    expected_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read and validate a frozen P6 receipt without writing any files."""

    from ..gates.release.delivery_v2 import (
        DeliveryExecutionV2,
        DeliveryReceiptV2,
        DeliveryRequestV2,
    )

    paths = _delivery_paths(Path(run_root))
    request, request_raw = _read_delivery_model(paths["request"], DeliveryRequestV2)
    execution, execution_raw = _read_delivery_model(paths["execution"], DeliveryExecutionV2)
    receipt, receipt_raw = _read_delivery_model(paths["receipt"], DeliveryReceiptV2)
    _validate_delivery_models(request=request, execution=execution, receipt=receipt)
    current = _delivery_identity(
        paths=paths,
        request=request,
        request_raw=request_raw,
        execution=execution,
        execution_raw=execution_raw,
        receipt=receipt,
        receipt_raw=receipt_raw,
    )
    if expected_authority is not None:
        missing = _DELIVERY_IDENTITY_FIELDS.difference(expected_authority)
        if missing:
            raise Step4ImageIntegrityError(
                f"frozen delivery identity is partial: {', '.join(sorted(missing))}"
            )
        for field in _DELIVERY_IDENTITY_FIELDS:
            if current.get(field) != expected_authority[field]:
                raise Step4ImageIntegrityError(f"frozen delivery identity drifted: {field}")
    return current
