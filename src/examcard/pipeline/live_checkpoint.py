"""人工检查点①（准入）与②（图像评审）（拆分层 1；设计 §7）。

**文件为契约**：驱动产出待决物料 → 退出码 2 暂停 → 人工写决定文件 → 严格摄取。
webui 只是可选检视器，不在这条链上；摄取端一律严格校验，宁可拒收不猜意图。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Sequence

from ..common.hashing import canonical_hash, sha256_bytes
from ..common.io import atomic_write_bytes, atomic_write_json, png_dimensions, read_json
from ..providers.base import sanitized_error_summary

from .live_support import _utc_now_iso
from .live_stage_step4 import Step4ImageIntegrityError, validate_step4_image_artifact


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


def _validate_review_rows(
    *, run_root: Path, cards: list[Mapping[str, Any]], current_request_bindings: Mapping[int, Mapping[str, Any]] | None
) -> None:
    bindings = dict(current_request_bindings) if current_request_bindings is not None else _request_bindings_from_manifest(run_root)
    storage = Path(run_root) / "step4"
    seen_card_indexes: set[int] = set()
    for row in cards:
        card_index = int(row["card_index"])
        if card_index in seen_card_indexes:
            raise Step4ImageIntegrityError(f"review sheet contains duplicate card {card_index}")
        seen_card_indexes.add(card_index)
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


def validate_ingested_image_review(
    *, run_root: Path, current_request_bindings: Mapping[int, Mapping[str, Any]]
) -> None:
    """Revalidate a previously ingested review chain before ordinary resume success."""

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    try:
        sheet = read_json(checkpoint_dir / "image_review_sheet.json")
    except (OSError, TypeError, ValueError) as error:
        raise Step4ImageIntegrityError("image review sheet is missing or invalid") from error
    if not isinstance(sheet, Mapping):
        raise Step4ImageIntegrityError("image review sheet must be an object")
    if sheet.get("schema") != "image-review-sheet/2.0":
        raise Step4ImageIntegrityError("legacy or invalid image review sheet schema")
    cards = sheet.get("cards")
    if not isinstance(cards, list) or not all(isinstance(card, Mapping) for card in cards):
        raise Step4ImageIntegrityError("image review sheet cards are invalid")
    _validate_review_rows(run_root=run_root, cards=cards, current_request_bindings=current_request_bindings)
    from ..gates.review.image_admission_batch import BatchImageAuthorizationManifest

    manifest = BatchImageAuthorizationManifest.model_validate(
        read_json(checkpoint_dir / "image-authorization-manifest.json")
    )
    rows_by_index = {int(row["card_index"]): row for row in cards}
    if set(rows_by_index) != {decision.card_index for decision in manifest.decisions}:
        raise Step4ImageIntegrityError("image authorization manifest does not match the review sheet")
    for decision in manifest.decisions:
        row = rows_by_index[decision.card_index]
        for key in ("source_card_id", "render_presentation", "image_sha256", "disposition", "reviewer", "basis"):
            if str(row.get(key)) != str(getattr(decision, key)):
                raise Step4ImageIntegrityError(f"image authorization decision drifted for card {decision.card_index}")


def ingest_image_review_sheet(
    *, run_root: Path, current_request_bindings: Mapping[int, Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """检查点②摄取（G-B2）：人工回填 disposition 后构建 v2 批量授权清单。"""

    from ..gates.review.image_admission_batch import build_batch_image_authorization

    checkpoint_dir = Path(run_root) / "checkpoints" / "image_review"
    sheet_path = checkpoint_dir / "image_review_sheet.json"
    if not sheet_path.is_file():
        return {"status": "waiting_human", "detail": f"image review sheet not found: {sheet_path}"}
    try:
        sheet = read_json(sheet_path)
        if sheet.get("schema") != "image-review-sheet/2.0":
            raise Step4ImageIntegrityError("legacy or invalid image review sheet schema")
        cards = sheet.get("cards", [])
        if not isinstance(cards, list) or not all(isinstance(card, Mapping) for card in cards):
            raise Step4ImageIntegrityError("image review sheet cards are invalid")
        _validate_review_rows(run_root=run_root, cards=cards, current_request_bindings=current_request_bindings)
        undecided = [card["card_index"] for card in cards if card.get("disposition") is None]
        if undecided:
            return {"status": "waiting_human", "detail": f"cards without disposition: {undecided}"}
        manifest = build_batch_image_authorization(cards)
    except Exception as error:
        return {"status": "refused", "stop_reason": "gate_refused_admission", "detail": sanitized_error_summary(error)}
    atomic_write_json(checkpoint_dir / "image-authorization-manifest.json", manifest.model_dump(mode="json"))
    approved = sum(1 for card in cards if card.get("disposition") == "approved")
    return {"status": "ingested", "approved_count": approved, "decision_count": len(cards)}
