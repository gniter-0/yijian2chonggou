"""Threshold-capable V2 authorization and r84 export tests."""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from examcard.gates.review.authorization_v2 import (
    ProductionAuthorizationDecisionV2,
    ProductionAuthorizationManifestV2,
    R84ExportReceiptV2,
    build_authorization_decision_v2,
    export_r84_decision_v2,
    export_r84_manifest_v2,
    source_text_snapshot_v2,
    validate_authorization_decision_v2,
)
from examcard.gates.review.decisions_v2 import HUMAN_MANIFEST_V2_VERSION, HumanDecisionManifestV2
from examcard.gates.review.fixtures_v2 import build_reviewed_candidate_v2
from examcard.knowledge.contracts.admission_legacy_hash import hash_model_r8x
from examcard.knowledge.contracts.admission_v2 import ReviewedCandidateV2
from tests.fixtures.admission_kernel import CANDIDATE_ID, make_diagram_contract
from tests.test_admission_threshold_v2 import (
    make_decision,
    make_queue_item,
    make_threshold_contract,
    make_threshold_token,
)


QUEUE_SHA = "d" * 64


def _source_manifest(decision: Any) -> HumanDecisionManifestV2:
    return hash_model_r8x(
        HumanDecisionManifestV2,
        {
            "manifest_id": "manifest:r84:human:source-threshold-0001",
            "queue_sha256": decision.queue_sha256,
            "decisions": (decision,),
            "decision_count": 1,
        },
        "manifest_sha256",
    )


def _chain() -> tuple[Any, Any, Any]:
    queue_item = make_queue_item()
    decision = make_decision(queue_item)
    candidate = build_reviewed_candidate_v2(queue_item, CANDIDATE_ID, decision)
    authorization = build_authorization_decision_v2(
        candidate,
        decision,
        queue_sha256=decision.queue_sha256,
        identity_period="test",
        reviewer="operator",
        reason="explicit threshold production authorization",
        recorded_at="2026-09-10T00:00:00Z",
    )
    return decision, candidate, authorization


def _manifest(authorization: ProductionAuthorizationDecisionV2) -> ProductionAuthorizationManifestV2:
    return hash_model_r8x(
        ProductionAuthorizationManifestV2,
        {
            "manifest_id": "manifest:r810:human:test-threshold-0001",
            "r89_frozen_input_audit_sha256": "1" * 64,
            "r89_admission_bundle_sha256": "2" * 64,
            "r89_source_page_sidecar_sha256": "3" * 64,
            "r89_queue_sha256": authorization.queue_sha256,
            "r86_freeze_sha256": "4" * 64,
            "decisions": (authorization,),
            "decision_count": 1,
            "reviewed_candidate_count": 1,
        },
        "manifest_sha256",
    )


def _rehash(
    model_type: type[Any],
    value: Any,
    field: str,
    **updates: Any,
) -> Any:
    payload = value.model_dump(mode="json")
    payload.update(updates)
    return hash_model_r8x(model_type, payload, field)


def test_authorization_v2_accepts_threshold_candidate() -> None:
    decision, candidate, authorization = _chain()
    validate_authorization_decision_v2(authorization, candidate, decision)
    assert authorization.presentation == "threshold"
    assert authorization.typed_contract.presentation == "threshold"


def test_authorization_v2_preserves_threshold_contract_and_snapshot() -> None:
    decision, candidate, authorization = _chain()
    assert authorization.typed_contract == candidate.typed_contract == decision.typed_contract
    assert authorization.typed_contract_sha256 == candidate.typed_contract_sha256
    assert authorization.source_text_snapshot == ("不小于0.25m",)
    assert authorization.source_text_snapshot == source_text_snapshot_v2(authorization.typed_contract)


def test_authorization_v2_hash_is_deterministic() -> None:
    first = _chain()[2]
    second = _chain()[2]
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.decision_sha256 == second.decision_sha256


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_id", "candidate:test:0002", "candidate field"),
        ("queue_item_id", "queue:test:threshold-0002", "candidate field"),
        ("fact_id", "fact:test:0002", "candidate field"),
        ("evidence_id", "evidence:test:0002", "candidate field"),
        ("source_manifest_sha256", "e" * 64, "candidate field"),
        ("source_decision_id", "decision:r84:human:threshold-0002", "source decision identity"),
    ],
)
def test_authorization_v2_rejects_binding_drift(field: str, value: str, message: str) -> None:
    decision, candidate, authorization = _chain()
    changed = _rehash(ProductionAuthorizationDecisionV2, authorization, "decision_sha256", **{field: value})
    with pytest.raises(ValueError, match=message):
        validate_authorization_decision_v2(changed, candidate, decision)


def test_authorization_v2_rejects_candidate_hash_drift() -> None:
    decision, candidate, authorization = _chain()
    changed = _rehash(
        ReviewedCandidateV2,
        candidate,
        "candidate_sha256",
        reviewer="another operator",
    )
    with pytest.raises(ValueError, match="candidate hash"):
        validate_authorization_decision_v2(authorization, changed, decision)


def test_authorization_v2_rejects_source_decision_hash_drift() -> None:
    decision, candidate, authorization = _chain()
    changed = _rehash(
        ProductionAuthorizationDecisionV2,
        authorization,
        "decision_sha256",
        source_decision_sha256="e" * 64,
    )
    with pytest.raises(ValueError, match="source decision hash"):
        validate_authorization_decision_v2(changed, candidate, decision)


def test_authorization_v2_rejects_typed_contract_hash_drift() -> None:
    _decision, _candidate, authorization = _chain()
    with pytest.raises(ValidationError, match="contract hash"):
        ProductionAuthorizationDecisionV2.model_validate(
            {**authorization.model_dump(mode="json"), "typed_contract_sha256": "0" * 64}
        )


def test_authorization_v2_rejects_source_snapshot_drift() -> None:
    _decision, _candidate, authorization = _chain()
    with pytest.raises(ValidationError, match="source snapshot"):
        ProductionAuthorizationDecisionV2.model_validate(
            {**authorization.model_dump(mode="json"), "source_text_snapshot": ("tampered",)}
        )


def test_authorization_v2_rejects_self_hash_tamper() -> None:
    _decision, _candidate, authorization = _chain()
    with pytest.raises(ValidationError, match="decision hash"):
        ProductionAuthorizationDecisionV2.model_validate(
            {**authorization.model_dump(mode="json"), "reason": "tampered"}
        )


def test_authorization_v2_rejects_presentation_drift() -> None:
    _decision, _candidate, authorization = _chain()
    with pytest.raises(ValidationError, match="presentation"):
        ProductionAuthorizationDecisionV2.model_validate(
            {**authorization.model_dump(mode="json"), "presentation": "constraint"}
        )


def test_authorization_manifest_v2_rejects_duplicate_authorization_id() -> None:
    _decision, _candidate, authorization = _chain()
    duplicate = _rehash(
        ProductionAuthorizationDecisionV2,
        authorization,
        "decision_sha256",
        candidate_id="candidate:test:0002",
    )
    with pytest.raises(ValidationError, match="identities must be unique"):
        hash_model_r8x(
            ProductionAuthorizationManifestV2,
            {
                "manifest_id": "manifest:r810:human:duplicate-0001",
                "r89_frozen_input_audit_sha256": "1" * 64,
                "r89_admission_bundle_sha256": "2" * 64,
                "r89_source_page_sidecar_sha256": "3" * 64,
                "r89_queue_sha256": QUEUE_SHA,
                "r86_freeze_sha256": "4" * 64,
                "decisions": (authorization, duplicate),
                "decision_count": 2,
                "reviewed_candidate_count": 2,
            },
            "manifest_sha256",
        )


def test_authorization_manifest_v2_rejects_duplicate_candidate() -> None:
    _decision, _candidate, authorization = _chain()
    duplicate = _rehash(
        ProductionAuthorizationDecisionV2,
        authorization,
        "decision_sha256",
        production_decision_id="decision:r810:human:test-threshold-0002",
    )
    with pytest.raises(ValidationError, match="candidates must be unique"):
        hash_model_r8x(
            ProductionAuthorizationManifestV2,
            {
                "manifest_id": "manifest:r810:human:duplicate-candidate-0001",
                "r89_frozen_input_audit_sha256": "1" * 64,
                "r89_admission_bundle_sha256": "2" * 64,
                "r89_source_page_sidecar_sha256": "3" * 64,
                "r89_queue_sha256": QUEUE_SHA,
                "r86_freeze_sha256": "4" * 64,
                "decisions": (authorization, duplicate),
                "decision_count": 2,
                "reviewed_candidate_count": 2,
            },
            "manifest_sha256",
        )


def test_authorization_manifest_v2_rejects_crossed_queue_hash() -> None:
    _decision, _candidate, authorization = _chain()
    crossed = _rehash(
        ProductionAuthorizationDecisionV2,
        authorization,
        "decision_sha256",
        queue_sha256="e" * 64,
    )
    with pytest.raises(ValidationError, match="crossed queue hash"):
        hash_model_r8x(
            ProductionAuthorizationManifestV2,
            {
                "manifest_id": "manifest:r810:human:crossed-queue-0001",
                "r89_frozen_input_audit_sha256": "1" * 64,
                "r89_admission_bundle_sha256": "2" * 64,
                "r89_source_page_sidecar_sha256": "3" * 64,
                "r89_queue_sha256": authorization.queue_sha256,
                "r86_freeze_sha256": "4" * 64,
                "decisions": (crossed,),
                "decision_count": 1,
                "reviewed_candidate_count": 1,
            },
            "manifest_sha256",
        )


def test_authorization_manifest_v2_rejects_self_hash_tamper() -> None:
    manifest = _manifest(_chain()[2])
    with pytest.raises(ValidationError, match="manifest hash"):
        ProductionAuthorizationManifestV2.model_validate(
            {**manifest.model_dump(mode="json"), "r86_freeze_sha256": "9" * 64}
        )


def test_export_r84_v2_derives_fresh_decision_identity_and_preserves_threshold() -> None:
    _decision, _candidate, authorization = _chain()
    exported = export_r84_decision_v2(authorization)
    assert exported.decision_id.startswith("decision:r84:human:r810v2-")
    assert exported.decision_id != authorization.source_decision_id
    assert exported.presentation == "threshold"
    assert exported.typed_contract is not None
    assert exported.typed_contract.presentation == "threshold"
    assert exported.typed_contract_sha256 == authorization.typed_contract_sha256


def test_export_r84_manifest_v2_binds_receipt() -> None:
    manifest = _manifest(_chain()[2])
    exported, receipt = export_r84_manifest_v2(manifest)
    assert exported.schema_version == HUMAN_MANIFEST_V2_VERSION
    assert exported.decisions[0].presentation == "threshold"
    assert receipt.source_manifest_id == manifest.manifest_id
    assert receipt.source_manifest_sha256 == manifest.manifest_sha256
    assert receipt.exported_manifest_id == exported.manifest_id
    assert receipt.exported_manifest_sha256 == exported.manifest_sha256
    assert receipt.exported_decision_count == 1
    assert receipt.model_validate(receipt.model_dump(mode="json")) == receipt
    assert isinstance(receipt, R84ExportReceiptV2)


def test_source_text_snapshot_v2_diagram_is_nodes_only() -> None:
    assert source_text_snapshot_v2(make_diagram_contract()) == ("项目经理", "技术负责人")


def test_threshold_multi_token_snapshot_preserves_storage_order() -> None:
    token = make_decision().typed_contract.tokens[0]
    contract = make_threshold_contract(
        token,
        make_threshold_token(
            order=2,
            element_id="element:test:threshold-0002",
            text=token.text,
            provenance=token.provenance,
        ),
    )
    assert source_text_snapshot_v2(contract) == ("不小于0.25m", "不小于0.25m")
