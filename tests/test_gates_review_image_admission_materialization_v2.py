from __future__ import annotations

from typing import Any

import pytest

from examcard.common.hashing import canonical_hash
from examcard.gates.review.image_admission_batch import build_batch_image_authorization
from examcard.gates.review.image_admission_materialization_v2 import (
    derive_image_review_materialization_v2,
)


_H = "a" * 64


def _rows(dispositions: tuple[str, ...]) -> tuple[list[dict[str, Any]], object]:
    rows: list[dict[str, Any]] = []
    for index, disposition in enumerate(dispositions, start=1):
        rows.append(
            {
                "card_index": index,
                "source_card_id": f"source:card:{index}",
                "sample_id": f"sample-{index}",
                "render_presentation": "table" if index % 2 else "diagram",
                "image_path": f"step4/images/card-{index}.png",
                "image_sha256": f"{index:064x}",
                "image_provenance_sha256": f"{index + 10:064x}",
                "http_payload_sha256": f"{index + 20:064x}",
                "disposition": disposition,
                "reviewer": "reviewer-1",
                "basis": "pixel review complete",
            }
        )
    manifest = build_batch_image_authorization(rows)
    for row, decision in zip(rows, manifest.decisions, strict=True):
        row["decision_sha256"] = decision.decision_sha256
    return rows, manifest


def _derive(rows: list[dict[str, Any]], manifest: object):
    return derive_image_review_materialization_v2(
        rows=rows,
        authorization_manifest=manifest,  # type: ignore[arg-type]
        image_review_source_binding_sha256=_H,
        review_sheet_file_sha256="b" * 64,
        authorization_manifest_file_sha256="c" * 64,
        request_manifest_file_sha256="d" * 64,
        provenance_file_sha256_by_card={
            row["card_index"]: f"{row['card_index'] + 30:064x}" for row in rows
        },
    )


def test_materialization_routes_mixed_batch_in_frozen_order() -> None:
    rows, manifest = _rows(("approved", "rejected", "manual_review", "approved"))
    artifacts = _derive(rows, manifest)

    assert [card.card_index for card in artifacts.reviewed_fixture.cards] == [1, 4]  # type: ignore[union-attr]
    assert [item.card_index for item in artifacts.post_review_queue.items] == [2, 3]
    assert [item.disposition for item in artifacts.post_review_queue.items] == ["rejected", "manual_review"]
    assert [item.card_index for item in artifacts.admission_audit.audits] == [1, 2, 3, 4]
    assert artifacts.admission_audit.decision_count == 4
    assert (artifacts.admission_audit.approved_count, artifacts.admission_audit.rejected_count, artifacts.admission_audit.manual_review_count) == (2, 1, 1)
    assert all(item.network_calls == 0 and item.model_calls == 0 for item in artifacts.admission_audit.audits)


def test_materialization_all_approved_keeps_empty_queue() -> None:
    rows, manifest = _rows(("approved", "approved"))
    artifacts = _derive(rows, manifest)

    assert artifacts.reviewed_fixture is not None
    assert artifacts.post_review_queue.items == ()
    assert artifacts.post_review_queue.queue_count == 0
    assert artifacts.admission_audit.post_review_queue_sha256 == artifacts.post_review_queue.queue_sha256
    assert all(item.admitted for item in artifacts.admission_audit.audits)


def test_materialization_zero_approved_is_valid_without_fixture() -> None:
    rows, manifest = _rows(("rejected", "manual_review"))
    artifacts = _derive(rows, manifest)

    assert artifacts.reviewed_fixture is None
    assert artifacts.post_review_queue.queue_count == 2
    assert artifacts.admission_audit.reviewed_fixture_sha256 is None
    assert artifacts.admission_audit.reviewed_fixture_sha256 is None
    assert [item.routed_output for item in artifacts.admission_audit.audits] == ["post_review", "post_review"]


@pytest.mark.parametrize("field", ["render_presentation", "image_sha256", "image_provenance_sha256", "http_payload_sha256"])
def test_materialization_rejects_image_identity_drift(field: str) -> None:
    rows, manifest = _rows(("approved", "rejected"))
    rows[0][field] = "drifted" if field != "image_sha256" else "f" * 64
    with pytest.raises((ValueError, TypeError)):
        _derive(rows, manifest)


@pytest.mark.parametrize("field", ["disposition", "reviewer", "basis", "decision_sha256"])
def test_materialization_rejects_decision_authority_drift(field: str) -> None:
    rows, manifest = _rows(("approved", "rejected"))
    rows[0][field] = "manual_review" if field == "disposition" else "drifted"
    with pytest.raises((ValueError, TypeError)):
        _derive(rows, manifest)


def test_materialization_rejects_reordered_rows_and_provenance_gaps() -> None:
    rows, manifest = _rows(("approved", "rejected", "manual_review"))
    with pytest.raises(ValueError):
        _derive([rows[1], rows[0], rows[2]], manifest)
    with pytest.raises(ValueError):
        derive_image_review_materialization_v2(
            rows=rows,
            authorization_manifest=manifest,  # type: ignore[arg-type]
            image_review_source_binding_sha256=_H,
            review_sheet_file_sha256="b" * 64,
            authorization_manifest_file_sha256="c" * 64,
            request_manifest_file_sha256="d" * 64,
            provenance_file_sha256_by_card={1: "e" * 64, 2: "f" * 64},
        )


def test_materialization_is_deterministic_and_uses_common_hashes() -> None:
    rows, manifest = _rows(("approved", "rejected", "manual_review"))
    first = _derive(rows, manifest)
    second = _derive(rows, manifest)

    assert first == second
    assert first.reviewed_fixture is not None
    assert first.reviewed_fixture.fixture_sha256 == second.reviewed_fixture.fixture_sha256
    assert canonical_hash(first.post_review_queue.model_dump(mode="json", exclude={"queue_sha256"})) == canonical_hash(
        second.post_review_queue.model_dump(mode="json", exclude={"queue_sha256"})
    )
