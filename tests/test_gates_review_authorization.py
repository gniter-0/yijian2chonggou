"""显式生产授权层：r810 授权→r84 导出→守恒投影。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from examcard.gates.review.authorization import (
    DispositionProjectionItem,
    ProductionAuthorizationDecision,
    ProductionAuthorizationManifest,
    assert_no_silent_upgrade,
    export_r84_manifest,
)
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from tests.fixtures.admission_kernel import (
    ANCHOR_IDS,
    EVIDENCE_ID,
    FACT_ID,
    RECORD_ID,
    SOURCE_MANIFEST_SHA256,
    SUBJECT_ID,
    make_formula_contract,
    make_source_page,
)

QUEUE_SHA = "d" * 64


def _authorization_decision(**overrides: object) -> ProductionAuthorizationDecision:
    contract = make_formula_contract()
    payload: dict[str, object] = {
        "production_decision_id": "decision:r810:human:test-0001",
        "r89_admission_candidate_id": "admission:r89:production:test-0001",
        "r89_candidate_sha256": "e" * 64,
        "source_decision_id": "decision:r89:test:0001",
        "source_decision_sha256": "f" * 64,
        "candidate_id": "candidate:test:0001",
        "queue_item_id": "queue:test:0001",
        "queue_sha256": QUEUE_SHA,
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": "formula",
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "anchor_ids": ANCHOR_IDS,
        "pages": (make_source_page(),),
        "source_text_snapshot": ("E=mc^2",),
        "reviewer": "tester",
        "reason": "explicit production authorization",
        "typed_contract": contract,
        "typed_contract_sha256": canonical_hash(contract.model_dump(mode="json")),
        "recorded_at": "2026-08-12T00:00:00Z",
    }
    payload.update(overrides)
    return hash_model_r8x(ProductionAuthorizationDecision, payload, "decision_sha256")  # type: ignore[return-value]


def _authorization_manifest(decision: ProductionAuthorizationDecision) -> ProductionAuthorizationManifest:
    return hash_model_r8x(  # type: ignore[return-value]
        ProductionAuthorizationManifest,
        {
            "manifest_id": "manifest:r810:human:test-0001",
            "r89_frozen_input_audit_sha256": "1" * 64,
            "r89_admission_bundle_sha256": "2" * 64,
            "r89_source_page_sidecar_sha256": "3" * 64,
            "r89_queue_sha256": QUEUE_SHA,
            "r86_freeze_sha256": "4" * 64,
            "decisions": (decision,),
            "decision_count": 1,
            "reviewed_candidate_count": 1,
        },
        "manifest_sha256",
    )


def test_authorization_decision_is_fully_confirmed_by_construction() -> None:
    decision = _authorization_decision()
    assert decision.disposition == "reviewed_candidate"
    with pytest.raises(ValidationError):
        _authorization_decision(browser_demo=True)
    with pytest.raises(ValidationError):
        _authorization_decision(production_fixture_authorized=False)


def test_authorization_manifest_binds_queue_chain() -> None:
    decision = _authorization_decision()
    manifest = _authorization_manifest(decision)
    assert manifest.reviewed_candidate_count == 1
    crossed = manifest.model_dump(mode="json")
    crossed["r89_queue_sha256"] = "5" * 64
    with pytest.raises(ValidationError, match="crossed queue hash"):
        ProductionAuthorizationManifest.model_validate(crossed)


def test_export_r84_manifest_derives_fresh_sealed_authority() -> None:
    manifest = _authorization_manifest(_authorization_decision())
    exported, receipt = export_r84_manifest(manifest)
    assert exported.manifest_id.startswith("manifest:r84:human:r810-")
    assert exported.decisions[0].decision_id.startswith("decision:r84:human:r810-")
    assert exported.decisions[0].production_fixture_authorized is True
    assert exported.queue_sha256 == QUEUE_SHA
    assert receipt.source_manifest_sha256 == manifest.manifest_sha256
    assert receipt.exported_manifest_sha256 == exported.manifest_sha256
    assert receipt.exported_reviewed_candidate_count == 1


def test_projection_item_enforces_authorization_closure() -> None:
    base = {
        "candidate_id": "candidate:test:0001",
        "queue_item_id": "queue:test:0001",
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": "formula",
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "anchor_ids": ANCHOR_IDS,
        "source_decision_id": "decision:r89:test:0001",
        "source_decision_sha256": "f" * 64,
        "input_disposition": "manual_review",
        "effective_disposition": "manual_review",
        "reason": "disposition preserved verbatim",
    }
    item = hash_model_r8x(DispositionProjectionItem, dict(base), "entry_sha256")
    assert item.automatic_upgrade is False
    with pytest.raises(ValidationError, match="carries authorization"):
        hash_model_r8x(DispositionProjectionItem, {**base, "authorization_id": "auth:test:0001"}, "entry_sha256")
    with pytest.raises(ValidationError, match="lacks explicit authorization"):
        hash_model_r8x(
            DispositionProjectionItem,
            {**base, "input_disposition": "reviewed_candidate", "effective_disposition": "reviewed_candidate"},
            "entry_sha256",
        )


def test_assert_no_silent_upgrade_detects_change() -> None:
    base = {
        "candidate_id": "candidate:test:0001",
        "queue_item_id": "queue:test:0001",
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": "formula",
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "anchor_ids": ANCHOR_IDS,
        "source_decision_id": "decision:r89:test:0001",
        "source_decision_sha256": "f" * 64,
        "input_disposition": "blocked",
        "effective_disposition": "blocked",
        "reason": "disposition preserved verbatim",
    }
    preserved = hash_model_r8x(DispositionProjectionItem, dict(base), "entry_sha256")
    assert_no_silent_upgrade([preserved])
    changed = hash_model_r8x(
        DispositionProjectionItem,
        {**base, "input_disposition": "manual_review", "effective_disposition": "blocked"},
        "entry_sha256",
    )
    with pytest.raises(ValueError, match="silent disposition change"):
        assert_no_silent_upgrade([changed])
