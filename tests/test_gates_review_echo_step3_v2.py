"""Human review and authorization guards for Echo Release V2."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examcard.common.hashing import canonical_hash
from examcard.compilers.step4_echo_bridge_v2 import emit_echo_step3_review_sheet_v2
from examcard.gates.review.echo_step3_review_v2 import (
    ingest_echo_step3_review_v2,
    issue_echo_batch_handoff_v2,
)
from examcard.knowledge.contracts.echo_release_v2 import (
    EchoBatchStep4HandoffV2,
    EchoStep3ReviewReceiptV2,
)
from tests.fixtures.step3_v3 import approve_sheet, compile_v3, seed_v3_echo_run


def _sheet(tmp_path: Path) -> tuple[Path, Path, object]:
    typed_path, fixture, _file_sha, compilation = compile_v3(tmp_path)
    run_dir = seed_v3_echo_run(tmp_path, compilation)
    sheet_path = emit_echo_step3_review_sheet_v2(
        echo_run_dir=run_dir, typed_fixture_path=typed_path, out_dir=tmp_path
    )
    return typed_path, sheet_path, fixture


def _release_and_receipt(tmp_path: Path) -> tuple[Path, object, object, object, object]:
    typed_path, sheet_path, fixture = _sheet(tmp_path)
    approve_sheet(sheet_path)
    outcome = ingest_echo_step3_review_v2(sheet_path=sheet_path)
    assert outcome["status"] == "released"

    from examcard.compilers.step4_echo_bridge_v2 import build_echo_release_fixture_v2

    release = build_echo_release_fixture_v2(
        typed_fixture_path=typed_path,
        echo_run_dir=tmp_path / "echo-run-v3",
        review_sheet_path=sheet_path,
        approved_rows=outcome["released"],
        source_echo_run_id="run-v2-review-test",
    )
    return typed_path, fixture, release, outcome["ingest_receipt"], outcome


def _rehash_receipt(receipt: EchoStep3ReviewReceiptV2, **updates: object) -> EchoStep3ReviewReceiptV2:
    payload = receipt.model_dump(mode="json")
    card = dict(payload["released"][0])
    card.update(updates)
    payload["released"][0] = card
    payload["receipt_sha256"] = canonical_hash(
        {key: value for key, value in payload.items() if key not in {"schema_version", "receipt_sha256"}}
    )
    return EchoStep3ReviewReceiptV2.model_validate(payload)


def _rehash_handoff(
    handoff: EchoBatchStep4HandoffV2,
    *,
    cards: list[dict[str, object]] | None = None,
    reviewed_image_references: list[dict[str, object]] | None = None,
    prior_reviewed_indexes: list[int] | None = None,
) -> EchoBatchStep4HandoffV2:
    payload = handoff.model_dump(mode="json")
    if cards is not None:
        payload["cards"] = cards
    if reviewed_image_references is not None:
        payload["reviewed_image_references"] = reviewed_image_references
    if prior_reviewed_indexes is not None:
        payload["prior_reviewed_indexes"] = prior_reviewed_indexes
    payload["handoff_sha256"] = canonical_hash(
        {key: value for key, value in payload.items() if key not in {"schema_version", "handoff_sha256"}}
    )
    return EchoBatchStep4HandoffV2.model_validate(payload)


def test_v2_ingest_waits_then_releases_only_real_review(tmp_path: Path) -> None:
    _typed_path, sheet_path, _fixture = _sheet(tmp_path)
    waiting = ingest_echo_step3_review_v2(sheet_path=sheet_path)
    assert waiting["status"] == "waiting_human"

    approve_sheet(sheet_path)
    released = ingest_echo_step3_review_v2(sheet_path=sheet_path)
    assert released["status"] == "released"
    assert released["released"][0]["review_signoff_sha256"]
    assert released["ingest_receipt"]["schema_version"] == "echo-step3-review-receipt/2.0"


def test_v2_ingest_rejects_v1_sheet_and_missing_authority(tmp_path: Path) -> None:
    _typed_path, sheet_path, _fixture = _sheet(tmp_path)
    raw = json.loads(sheet_path.read_text(encoding="utf-8"))
    raw["schema_version"] = "echo-step3-review-sheet/1.0"
    sheet_path.write_text(json.dumps(raw), encoding="utf-8")
    refused = ingest_echo_step3_review_v2(sheet_path=sheet_path)
    assert refused["status"] == "refused"

    _typed_path, sheet_path, _fixture = _sheet(tmp_path / "missing")
    approve_sheet(sheet_path, reviewer="<待签名>")
    refused = ingest_echo_step3_review_v2(sheet_path=sheet_path)
    assert refused["status"] == "refused"
    assert "reviewer" in refused["detail"]


def test_v2_handoff_requires_explicit_authority_and_binds_receipt(tmp_path: Path) -> None:
    typed_path, sheet_path, fixture = _sheet(tmp_path)
    approve_sheet(sheet_path)
    outcome = ingest_echo_step3_review_v2(sheet_path=sheet_path)
    assert outcome["status"] == "released"

    from examcard.compilers.step4_echo_bridge_v2 import build_echo_release_fixture_v2

    run_dir = tmp_path / "echo-run-v3"
    release = build_echo_release_fixture_v2(
        typed_fixture_path=typed_path,
        echo_run_dir=run_dir,
        review_sheet_path=sheet_path,
        approved_rows=outcome["released"],
        source_echo_run_id="run-v2-review-test",
    )
    handoff = issue_echo_batch_handoff_v2(
        release=release,
        authorized_by="b2-reviewer",
        authorization_evidence={"sheet": outcome["sheet_sha256"], "scope": "threshold"},
        ingest_receipt=outcome["ingest_receipt"],
    )
    assert handoff.batch_generation_authorized is True
    assert handoff.source_release_sha256 == release.fixture_sha256
    assert handoff.source_typed_fixture_sha256 == fixture.fixture_sha256
    assert all(card.disposition == "pending" for card in handoff.cards)
    assert handoff.prior_reviewed_indexes == ()
    assert handoff.reviewed_image_references == ()


def test_v2_handoff_accepts_reviewed_card_only_with_matching_image_reference(tmp_path: Path) -> None:
    _typed_path, _fixture, release, receipt, outcome = _release_and_receipt(tmp_path)
    handoff = issue_echo_batch_handoff_v2(
        release=release,
        authorized_by="b2-reviewer",
        authorization_evidence={"sheet": outcome["sheet_sha256"], "scope": "threshold"},
        ingest_receipt=receipt,
    )
    cards = [dict(row) for row in handoff.model_dump(mode="json")["cards"]]
    cards[0]["disposition"] = "reviewed"
    reviewed = _rehash_handoff(
        handoff,
        cards=cards,
        reviewed_image_references=[
            {"card_index": 1, "image_sha256": "a" * 64, "reference_path": "card-1.png"}
        ],
        prior_reviewed_indexes=[1],
    )
    assert reviewed.cards[0].disposition == "reviewed"
    assert reviewed.prior_reviewed_indexes == (1,)
    assert reviewed.reviewed_image_references[0].card_index == 1


@pytest.mark.parametrize(
    ("case", "disposition", "references", "prior", "message"),
    [
        ("reviewed_without_reference", "reviewed", [], [1], "cover the same cards"),
        (
            "pending_with_reference",
            "pending",
            [{"card_index": 1, "image_sha256": "a" * 64, "reference_path": "card-1.png"}],
            [],
            "cover the same cards",
        ),
        (
            "duplicate_reference_index",
            "reviewed",
            [
                {"card_index": 1, "image_sha256": "a" * 64, "reference_path": "card-1.png"},
                {"card_index": 1, "image_sha256": "b" * 64, "reference_path": "card-1b.png"},
            ],
            [1],
            "duplicate card indexes",
        ),
        (
            "unknown_reference_index",
            "pending",
            [{"card_index": 2, "image_sha256": "a" * 64, "reference_path": "card-2.png"}],
            [],
            "unknown card",
        ),
        (
            "prior_dispositions_drift",
            "reviewed",
            [{"card_index": 1, "image_sha256": "a" * 64, "reference_path": "card-1.png"}],
            [],
            "prior reviewed indexes",
        ),
        (
            "prior_references_drift",
            "reviewed",
            [{"card_index": 1, "image_sha256": "a" * 64, "reference_path": "card-1.png"}],
            [2],
            "prior reviewed indexes",
        ),
    ],
)
def test_v2_handoff_rejects_inconsistent_review_image_authority(
    tmp_path: Path,
    case: str,
    disposition: str,
    references: list[dict[str, object]],
    prior: list[int],
    message: str,
) -> None:
    _typed_path, _fixture, release, receipt, outcome = _release_and_receipt(tmp_path / case)
    handoff = issue_echo_batch_handoff_v2(
        release=release,
        authorized_by="b2-reviewer",
        authorization_evidence={"sheet": outcome["sheet_sha256"], "scope": "threshold"},
        ingest_receipt=receipt,
    )
    cards = [dict(row) for row in handoff.model_dump(mode="json")["cards"]]
    cards[0]["disposition"] = disposition
    with pytest.raises(ValueError, match=message):
        _rehash_handoff(
            handoff,
            cards=cards,
            reviewed_image_references=references,
            prior_reviewed_indexes=prior,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_candidate_id", "candidate:drift:1", "candidate"),
        ("presentation", "constraint", "presentation"),
        ("visible_text_summary", ["authority drift"], "visible text summary"),
    ],
)
def test_v2_handoff_rejects_receipt_release_authority_crossing(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    _typed_path, _fixture, release, receipt, outcome = _release_and_receipt(tmp_path / field)
    drifted_receipt = _rehash_receipt(receipt, **{field: value})
    with pytest.raises(ValueError, match=message):
        issue_echo_batch_handoff_v2(
            release=release,
            authorized_by="b2-reviewer",
            authorization_evidence={"sheet": outcome["sheet_sha256"], "scope": "threshold"},
            ingest_receipt=drifted_receipt,
        )
