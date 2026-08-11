"""图片目检准入状态机（r11101 家族）。"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from examcard.gates.review.image_admission import (
    IMAGE_PRESENTATIONS,
    ProductionAuthorizationManifest,
    SourceImageBinding,
    admit_images,
    build_image_manifest,
    image_binding_decision,
    validate_image_decision,
)
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash_r10x, hash_model_r10x

FREEZE_SHA = "9" * 64


def _binding(presentation: str, index: int) -> SourceImageBinding:
    card_hex = f"{index:024x}"
    payload: dict[str, Any] = {
        "source_card_id": f"step3-card-contract:{card_hex}",
        "presentation": presentation,
        "source_texts": (f"素材文本 {presentation}",),
        "fact_ids": (f"fact:r11101:{index}",),
        "evidence_ids": (f"evidence:r11101:{index}",),
        "anchor_ids": (f"anchor:r11101:{index}",),
        "source_page_ref_ids": (f"page:r11101:{index}",),
        "request_sha256": "1" * 64,
        "image_record_sha256": "2" * 64,
        "image_path": f"images/step4-card-{card_hex}.png",
        "image_sha256": "3" * 64,
        "image_gate_sha256": "4" * 64,
        "source_contract_sha256": "5" * 64,
        "typed_contract_sha256": "6" * 64,
        "provenance_sha256": "7" * 64,
        "geometry_sha256": "8" * 64,
        "source_text_sha256": canonical_hash_r10x((f"素材文本 {presentation}",)),
    }
    return hash_model_r10x(SourceImageBinding, payload, "binding_sha256")  # type: ignore[return-value]


@pytest.fixture()
def bindings() -> tuple[SourceImageBinding, ...]:
    return tuple(_binding(presentation, index + 1) for index, presentation in enumerate(IMAGE_PRESENTATIONS))


def test_decision_authorization_coupling(bindings: tuple[SourceImageBinding, ...]) -> None:
    decision = image_binding_decision(bindings[0], FREEZE_SHA, "approved", "reviewer-1")
    assert decision.explicit_human_authorization is True
    payload = decision.model_dump(mode="json")
    payload["explicit_human_authorization"] = False
    with pytest.raises(ValidationError, match="explicit human authorization"):
        type(decision).model_validate(payload)
    with pytest.raises(ValueError, match="unknown human disposition"):
        image_binding_decision(bindings[0], FREEZE_SHA, "reviewed_candidate", "reviewer-1")


def test_manifest_requires_exact_presentation_coverage(bindings: tuple[SourceImageBinding, ...]) -> None:
    with pytest.raises(ValueError, match="explicitly cover"):
        build_image_manifest(bindings, FREEZE_SHA, {"table": "approved"}, "reviewer-1")
    manifest = build_image_manifest(
        bindings, FREEZE_SHA, {"table": "approved", "ordered_list": "rejected", "formula": "manual_review"}, "reviewer-1"
    )
    assert manifest.decision_count == 3


def test_manifest_rejects_crossed_freeze(bindings: tuple[SourceImageBinding, ...]) -> None:
    manifest = build_image_manifest(
        bindings, FREEZE_SHA, {"table": "approved", "ordered_list": "approved", "formula": "approved"}, "reviewer-1"
    )
    payload = manifest.model_dump(mode="json")
    payload["input_freeze_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="crossed input freeze"):
        ProductionAuthorizationManifest.model_validate(payload)


def test_validate_image_decision_blocks_mutated_binding(bindings: tuple[SourceImageBinding, ...]) -> None:
    decision = image_binding_decision(bindings[0], FREEZE_SHA, "approved", "reviewer-1")
    validate_image_decision(decision, bindings[0], FREEZE_SHA)
    with pytest.raises(ValueError, match="crossed source binding"):
        validate_image_decision(decision, bindings[1], FREEZE_SHA)
    with pytest.raises(ValueError, match="crossed source binding"):
        validate_image_decision(decision, bindings[0], "0" * 64)


def test_admit_images_state_machine(bindings: tuple[SourceImageBinding, ...]) -> None:
    manifest = build_image_manifest(
        bindings, FREEZE_SHA, {"table": "approved", "ordered_list": "rejected", "formula": "manual_review"}, "reviewer-1"
    )
    fixture, queue, session = admit_images(manifest, bindings, FREEZE_SHA)
    assert fixture is not None and fixture.reviewed_card_count == 1
    assert fixture.cards[0].presentation == "table"
    assert fixture.batch_generation_authorized is False
    assert {item.disposition for item in queue} == {"rejected", "manual_review"}
    assert (session.admitted_count, session.rejected_count, session.manual_review_count) == (1, 1, 1)


def test_admit_images_without_approval_produces_no_fixture(bindings: tuple[SourceImageBinding, ...]) -> None:
    manifest = build_image_manifest(
        bindings, FREEZE_SHA, {"table": "rejected", "ordered_list": "rejected", "formula": "manual_review"}, "reviewer-1"
    )
    fixture, queue, session = admit_images(manifest, bindings, FREEZE_SHA)
    assert fixture is None
    assert len(queue) == 3
    assert session.admitted_count == 0


def test_admit_images_rejects_crossed_freeze(bindings: tuple[SourceImageBinding, ...]) -> None:
    manifest = build_image_manifest(
        bindings, FREEZE_SHA, {"table": "approved", "ordered_list": "approved", "formula": "approved"}, "reviewer-1"
    )
    with pytest.raises(ValueError, match="does not bind input freeze"):
        admit_images(manifest, bindings, "0" * 64)
