"""人工检查点①（准入）与②（图像评审）（拆分层 1；设计 §7）。

**文件为契约**：驱动产出待决物料 → 退出码 2 暂停 → 人工写决定文件 → 严格摄取。
webui 只是可选检视器，不在这条链上；摄取端一律严格校验，宁可拒收不猜意图。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Sequence

from ..common.io import atomic_write_json, read_json
from ..providers.base import sanitized_error_summary

from .live_support import _utc_now_iso
from .live_stage_step4 import Step4ImageIntegrityError, validate_step4_image_artifact


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
