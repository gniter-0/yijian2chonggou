"""Typed V2 Echo review ingestion and human-authorized handoff issuance."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...common.hashing import canonical_hash, hash_model
from ...knowledge.contracts.echo_release_v2 import (
    EchoBatchCardDispositionV2,
    EchoBatchReviewedReleaseFixtureV2,
    EchoBatchStep4HandoffV2,
    EchoStep3ReviewReceiptCardV2,
    EchoStep3ReviewReceiptV2,
    EchoStep3ReviewSheetV2,
)

__all__ = ("ingest_echo_step3_review_v2", "issue_echo_batch_handoff_v2")


def ingest_echo_step3_review_v2(*, sheet_path: Path) -> dict[str, Any]:
    """Parse only the exact V2 typed sheet and produce an authority receipt.

    V1 sheets and arbitrary dictionaries are deliberately rejected before any
    disposition is interpreted.
    """

    try:
        raw = Path(sheet_path).read_bytes()
        sheet = EchoStep3ReviewSheetV2.model_validate_json(raw.decode("utf-8"))
    except Exception:
        return {"status": "refused", "detail": "V2 review sheet schema rejected"}
    undecided = [card.card_seq for card in sheet.cards if card.disposition is None]
    if undecided:
        return {
            "status": "waiting_human",
            "detail": f"cards without disposition: {undecided}",
            "sheet_sha256": hashlib.sha256(raw).hexdigest(),
        }
    released: list[dict[str, Any]] = []
    for card in sheet.cards:
        disposition = card.disposition
        if disposition not in {"approved", "rejected", "manual_review"}:
            return {"status": "refused", "detail": f"card {card.card_seq} has an invalid disposition"}
        if disposition != "approved":
            continue
        reviewer = str(card.reviewer or "")
        if not reviewer.strip() or reviewer.startswith("<"):
            return {"status": "refused", "detail": f"card {card.card_seq} lacks a real reviewer signature"}
        basis = str(card.basis or "").strip()
        if not basis:
            return {"status": "refused", "detail": f"card {card.card_seq} lacks a review basis"}
        if not isinstance(card.b2_review, dict) or not card.b2_review:
            return {"status": "refused", "detail": f"card {card.card_seq} lacks b2_review evidence"}
        if card.echo_gate_valid is not True:
            return {"status": "refused", "detail": f"card {card.card_seq} lacks a valid V2 echo gate"}
        released.append(
            {
                "card_seq": card.card_seq,
                "contract_id": card.contract_id,
                "source_candidate_id": card.source_candidate_id,
                "presentation": card.presentation,
                "source_contract_sha256": card.source_contract_sha256,
                "echo_record_sha256": card.echo_record_sha256,
                "echo_gate_valid": True,
                "source_typed_fixture_file_sha256": card.source_typed_fixture_file_sha256,
                "source_typed_fixture_sha256": card.source_typed_fixture_sha256,
                "visible_text_summary": card.visible_text_summary,
                "reviewer": reviewer,
                "basis": basis,
                "b2_review": dict(card.b2_review),
                "review_signoff_sha256": canonical_hash(dict(card.b2_review)),
            }
        )
    if not released:
        return {"status": "refused", "detail": "no approved V2 cards after review", "released": []}
    receipt_cards = tuple(EchoStep3ReviewReceiptCardV2.model_validate(row) for row in released)
    receipt = hash_model(
        EchoStep3ReviewReceiptV2,
        {
            "sheet_sha256": hashlib.sha256(raw).hexdigest(),
            "batch_id": sheet.batch_id,
            "source_typed_fixture_file_sha256": sheet.source_typed_fixture_file_sha256,
            "source_typed_fixture_sha256": sheet.source_typed_fixture_sha256,
            "released": receipt_cards,
        },
        "receipt_sha256",
    )
    return {
        "status": "released",
        "detail": f"released={len(receipt_cards)}",
        "released": [card.model_dump(mode="json") for card in receipt_cards],
        "sheet_sha256": receipt.sheet_sha256,
        "ingest_receipt_sha256": receipt.receipt_sha256,
        "ingest_receipt": receipt,
    }


def issue_echo_batch_handoff_v2(
    *,
    release: EchoBatchReviewedReleaseFixtureV2,
    authorized_by: str,
    authorization_evidence: Mapping[str, Any],
    ingest_receipt: EchoStep3ReviewReceiptV2,
) -> EchoBatchStep4HandoffV2:
    """Issue V2 handoff only from a V2 release and explicit human authority."""

    if not isinstance(release, EchoBatchReviewedReleaseFixtureV2):
        raise TypeError("V2 handoff issuance requires EchoBatchReviewedReleaseFixtureV2")
    if not authorized_by.strip() or authorized_by.startswith("<"):
        raise ValueError("V2 handoff issuance requires a real authorizer signature")
    if not isinstance(authorization_evidence, Mapping) or not authorization_evidence:
        raise ValueError("V2 handoff issuance requires non-empty authorization evidence")
    if not isinstance(ingest_receipt, EchoStep3ReviewReceiptV2):
        raise TypeError("V2 handoff issuance requires a typed V2 ingest receipt")
    if ingest_receipt.batch_id != release.batch_id:
        raise ValueError("V2 ingest receipt does not bind the release batch")
    if ingest_receipt.source_typed_fixture_file_sha256 != release.source_typed_fixture_file_sha256:
        raise ValueError("V2 ingest receipt crossed fixture file hash")
    if ingest_receipt.source_typed_fixture_sha256 != release.source_typed_fixture_sha256:
        raise ValueError("V2 ingest receipt crossed fixture authority hash")
    if ingest_receipt.sheet_sha256 != release.review_sheet_sha256:
        raise ValueError("V2 ingest receipt does not bind the release review sheet")
    receipt_by_contract = {item.contract_id: item for item in ingest_receipt.released}
    if set(receipt_by_contract) != {item.source_card_id for item in release.cards}:
        raise ValueError("V2 ingest receipt released contract set does not match release")
    for card in release.cards:
        receipt_card = receipt_by_contract[card.source_card_id]
        if receipt_card.reviewer != card.reviewer or receipt_card.review_signoff_sha256 != card.review_signoff_sha256:
            raise ValueError("V2 release reviewer/signoff is not bound to the ingest receipt")
        if receipt_card.source_contract_sha256 != card.source_contract_sha256:
            raise ValueError("V2 release contract hash is not bound to the ingest receipt")
        if receipt_card.echo_record_sha256 != card.echo_record_sha256:
            raise ValueError("V2 release echo record is not bound to the ingest receipt")
        if receipt_card.source_candidate_id != card.source_candidate_id:
            raise ValueError("V2 release candidate is not bound to the ingest receipt")
        if receipt_card.presentation != card.source_presentation:
            raise ValueError("V2 release source presentation is not bound to the ingest receipt")
        if receipt_card.visible_text_summary != tuple(item.visible_text for item in card.visible_texts[:3]):
            raise ValueError("V2 release visible text summary is not bound to the ingest receipt")
    evidence_hash = canonical_hash(dict(authorization_evidence))
    dispositions = tuple(
        EchoBatchCardDispositionV2(card_index=card.card_index, source_card_id=card.source_card_id, disposition="pending")
        for card in release.cards
    )
    source_handoff = canonical_hash(
        {
            "release": release.fixture_sha256,
            "review_ingest_receipt": ingest_receipt.receipt_sha256,
            "cards": [item.model_dump(mode="json") for item in dispositions],
            "authorization_evidence_sha256": evidence_hash,
        }
    )
    payload = {
        "batch_id": release.batch_id,
        "source_release_sha256": release.fixture_sha256,
        "source_handoff_sha256": source_handoff,
        "source_typed_fixture_file_sha256": release.source_typed_fixture_file_sha256,
        "source_typed_fixture_sha256": release.source_typed_fixture_sha256,
        "review_ingest_receipt_sha256": ingest_receipt.receipt_sha256,
        "card_count": release.card_count,
        "cards": dispositions,
        "reviewed_image_references": (),
        "prior_reviewed_indexes": (),
        "authorized_by": authorized_by,
        "authorization_evidence_sha256": evidence_hash,
    }
    return hash_model(EchoBatchStep4HandoffV2, payload, "handoff_sha256")
