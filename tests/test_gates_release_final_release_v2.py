from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from examcard.common.hashing import canonical_hash
from examcard.gates.release.final_release_v2 import (
    FinalReleaseAuditV2,
    FinalReleaseBundleV2,
    FinalReleaseExclusionLedgerV2,
    derive_final_release_v2,
)
from examcard.gates.review.image_admission_materialization_v2 import (
    BatchImageAdmissionAuditV2,
    BatchPostReviewQueueV2,
    BatchReviewedImageFixtureV2,
    ImageReviewMaterializationReceiptV2,
)
from examcard.pipeline.live_checkpoint import (
    materialize_final_release_v2,
    materialize_image_review_v2,
    validate_materialized_final_release_v2,
)
from tests.test_gates_review_image_admission_materialization_v2 import _derive as derive_p3
from tests.test_gates_review_image_admission_materialization_v2 import _rows as p3_rows
from tests.test_pipeline_live_run_orchestration import _seed_exam_workspace, _seed_paused_run


def _p3(dispositions: tuple[str, ...]) -> tuple[Any, Any, Any, Any]:
    rows, manifest = p3_rows(dispositions)
    artifacts = derive_p3(rows, manifest)
    assert artifacts.reviewed_fixture is None or isinstance(artifacts.reviewed_fixture, BatchReviewedImageFixtureV2)
    assert isinstance(artifacts.post_review_queue, BatchPostReviewQueueV2)
    assert isinstance(artifacts.admission_audit, BatchImageAdmissionAuditV2)
    from examcard.common.hashing import hash_model

    fixture = artifacts.reviewed_fixture
    queue = artifacts.post_review_queue
    audit = artifacts.admission_audit
    receipt = hash_model(
        ImageReviewMaterializationReceiptV2,
        {
            "image_review_source_binding_sha256": "a" * 64,
            "review_sheet_file_sha256": "b" * 64,
            "authorization_manifest_sha256": audit.authorization_manifest_sha256,
            "authorization_manifest_file_sha256": "c" * 64,
            "request_manifest_file_sha256": "d" * 64,
            "reviewed_fixture_present": fixture is not None,
            "reviewed_fixture_sha256": fixture.fixture_sha256 if fixture is not None else None,
            "reviewed_fixture_file_sha256": "e" * 64 if fixture is not None else None,
            "post_review_queue_sha256": queue.queue_sha256,
            "post_review_queue_file_sha256": "f" * 64,
            "admission_audit_sha256": audit.audit_sha256,
            "admission_audit_file_sha256": "0" * 64,
            "decision_count": audit.decision_count,
            "approved_count": audit.approved_count,
            "rejected_count": audit.rejected_count,
            "manual_review_count": audit.manual_review_count,
            "reviewed_card_indexes": fixture.reviewed_card_indexes if fixture is not None else (),
            "post_review_card_indexes": queue.card_indexes,
        },
        "receipt_sha256",
    )
    return fixture, queue, audit, receipt


def test_final_release_routes_only_approved_cards_and_preserves_order() -> None:
    fixture, queue, audit, receipt = _p3(("approved", "rejected", "manual_review", "approved"))
    result = derive_final_release_v2(
        reviewed_fixture=fixture,
        post_review_queue=queue,
        admission_audit=audit,
        materialization_receipt=receipt,
    )

    assert result.release_bundle is not None
    assert result.release_bundle.release_card_indexes == (1, 4)
    assert result.exclusion_ledger.excluded_card_indexes == (2, 3)
    assert [item.disposition for item in result.exclusion_ledger.items] == ["rejected", "manual_review"]
    assert [item.card_index for item in result.audit.audit_items] == [1, 2, 3, 4]
    assert result.audit.decision_count == 4
    assert result.audit.released_count == 2


def test_final_release_all_approved_has_empty_exclusion_ledger() -> None:
    fixture, queue, audit, receipt = _p3(("approved", "approved"))
    result = derive_final_release_v2(fixture=fixture, queue=queue, audit=audit, receipt=receipt)

    assert result.release_bundle is not None
    assert result.release_bundle.release_card_indexes == (1, 2)
    assert result.exclusion_ledger.items == ()
    assert result.exclusion_ledger.exclusion_count == 0


def test_final_release_zero_approved_has_no_bundle() -> None:
    fixture, queue, audit, receipt = _p3(("rejected", "manual_review"))
    result = derive_final_release_v2(
        reviewed_fixture=fixture,
        post_review_queue=queue,
        admission_audit=audit,
        materialization_receipt=receipt,
    )

    assert result.release_bundle is None
    assert result.exclusion_ledger.exclusion_count == 2
    assert result.audit.released_count == 0


def test_final_release_rejects_duplicate_missing_and_cross_card_p3_routes() -> None:
    fixture, queue, audit, receipt = _p3(("approved", "rejected"))
    assert fixture is not None
    with pytest.raises(ValueError, match="indexes do not match"):
        derive_final_release_v2(
            fixture=fixture,
            queue=queue.model_copy(update={"card_indexes": (2, 2)}),
            audit=audit,
            receipt=receipt,
        )
    with pytest.raises(ValueError, match="indexes do not match"):
        derive_final_release_v2(
            fixture=fixture,
            queue=queue,
            audit=audit.model_copy(update={"audits": audit.audits[:1], "decision_count": 1}),
            receipt=receipt,
        )
    crossed = queue.items[0].model_copy(update={"source_card_id": fixture.cards[0].source_card_id})
    with pytest.raises(ValueError, match="routed card identity mismatch"):
        derive_final_release_v2(
            fixture=fixture,
            queue=queue.model_copy(update={"items": (crossed,)}),
            audit=audit,
            receipt=receipt,
        )


def test_final_release_rejects_upstream_hash_and_legacy_type_mismatch() -> None:
    fixture, queue, audit, receipt = _p3(("approved", "rejected"))
    with pytest.raises(ValueError, match="reviewed fixture hash"):
        derive_final_release_v2(
            fixture=fixture,
            queue=queue,
            audit=audit,
            receipt=receipt.model_copy(update={"reviewed_fixture_sha256": "0" * 64}),
        )
    with pytest.raises(TypeError, match="BatchPostReviewQueueV2"):
        derive_final_release_v2(
            fixture=fixture,
            queue=object(),  # type: ignore[arg-type]
            audit=audit,
            receipt=receipt,
        )


def test_final_release_is_deterministic_and_uses_common_v2_hashes() -> None:
    fixture, queue, audit, receipt = _p3(("approved", "rejected", "manual_review"))
    first = derive_final_release_v2(fixture=fixture, queue=queue, audit=audit, receipt=receipt)
    second = derive_final_release_v2(fixture=fixture, queue=queue, audit=audit, receipt=receipt)

    assert first == second
    assert first.release_bundle is not None
    assert first.release_bundle.bundle_sha256 == canonical_hash(
        first.release_bundle.model_dump(mode="json", exclude={"schema_version", "bundle_sha256"})
    )


def _materialized_p3_run(tmp_path: Path, *, zero_approved: bool = False) -> tuple[Path, dict[str, Any]]:
    _seed_exam_workspace(tmp_path)
    run_root = _seed_paused_run(tmp_path, "p4-final-release-test")
    if zero_approved:
        sheet_path = run_root / "checkpoints" / "image_review" / "image_review_sheet.json"
        sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
        for row in sheet["cards"]:
            row.update({"disposition": "rejected", "reviewer": "p4-tester", "basis": "not releasable"})
        sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    from examcard.pipeline.live_checkpoint import ingest_image_review_sheet

    authority = ingest_image_review_sheet(run_root=run_root)
    assert authority["status"] == "ingested"
    p3_identity = materialize_image_review_v2(run_root=run_root, expected_authority=authority)
    return run_root, p3_identity


def test_final_release_persistence_freezes_receipt_last_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_root, p3_identity = _materialized_p3_run(tmp_path)
    writes: list[str] = []
    from examcard.pipeline import live_checkpoint

    original_write = live_checkpoint.atomic_write_json

    def record_write(path: Path, payload: Any) -> None:
        writes.append(Path(path).name)
        original_write(path, payload)

    monkeypatch.setattr(live_checkpoint, "atomic_write_json", record_write)
    first = materialize_final_release_v2(run_root=run_root, expected_p3_authority=p3_identity)
    assert writes[-1] == "final-release-closure-v2.json"
    directory = run_root / "checkpoints" / "final_release"
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    second = materialize_final_release_v2(run_root=run_root, expected_p3_authority=p3_identity)
    assert second == first
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_final_release_matching_partial_recovers_but_receipt_missing_child_fails(tmp_path: Path) -> None:
    run_root, p3_identity = _materialized_p3_run(tmp_path)
    first = materialize_final_release_v2(run_root=run_root, expected_p3_authority=p3_identity)
    directory = run_root / "checkpoints" / "final_release"
    receipt_path = directory / "final-release-closure-v2.json"
    receipt_path.unlink()
    materialize_final_release_v2(run_root=run_root, expected_p3_authority=p3_identity)
    assert receipt_path.is_file()
    (directory / "final-release-audit-v2.json").unlink()
    with pytest.raises(Exception, match="missing"):
        validate_materialized_final_release_v2(run_root=run_root, expected_authority=first)


def test_final_release_zero_approved_persists_no_release_bundle(tmp_path: Path) -> None:
    run_root, p3_identity = _materialized_p3_run(tmp_path, zero_approved=True)
    identity = materialize_final_release_v2(run_root=run_root, expected_p3_authority=p3_identity)
    directory = run_root / "checkpoints" / "final_release"
    receipt = json.loads((directory / "final-release-closure-v2.json").read_text(encoding="utf-8"))
    assert identity["final_release_release_bundle_present"] is False
    assert receipt["release_bundle_present"] is False
    assert not (directory / "final-release-bundle-v2.json").exists()


def test_final_release_frozen_byte_and_ticket_tamper_refuse_without_regeneration(tmp_path: Path) -> None:
    run_root, p3_identity = _materialized_p3_run(tmp_path)
    identity = materialize_final_release_v2(run_root=run_root, expected_p3_authority=p3_identity)
    directory = run_root / "checkpoints" / "final_release"
    audit_path = directory / "final-release-audit-v2.json"
    original = audit_path.read_bytes()
    audit_path.write_bytes(original + b"\n")
    with pytest.raises(Exception, match="drifted|conflicts"):
        validate_materialized_final_release_v2(run_root=run_root, expected_authority=identity)
    audit_path.write_bytes(original)
    tampered = dict(identity)
    tampered["final_release_receipt_sha256"] = "0" * 64
    with pytest.raises(Exception, match="drifted"):
        validate_materialized_final_release_v2(run_root=run_root, expected_authority=tampered)


def test_final_release_contract_models_are_strict_v2_only() -> None:
    assert FinalReleaseBundleV2.model_fields["schema_version"].default == "final-release-bundle/2.0"
    assert FinalReleaseExclusionLedgerV2.model_fields["schema_version"].default == "final-release-exclusion-ledger/2.0"
    assert FinalReleaseAuditV2.model_fields["schema_version"].default == "final-release-audit/2.0"
    with pytest.raises(Exception):
        FinalReleaseBundleV2.model_validate({"schema_version": "legacy-release", "release_cards": []})
