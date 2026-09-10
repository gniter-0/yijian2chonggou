"""End-to-end tests for the threshold-capable V2 typed-evidence exporter."""
from __future__ import annotations

import json
from typing import Any

import pytest

from examcard.gates.review.decisions import HumanDecisionManifest
from examcard.gates.review.decisions_v2 import HumanDecisionManifestV2, HumanDecisionV2
from examcard.gates.review.authorization_v2 import (
    ProductionAuthorizationManifestV2,
    build_authorization_decision_v2,
    build_reviewed_typed_evidence_fixture_v2,
    export_r84_manifest_v2,
    strict_ingest_exported_manifest_v2,
)
from examcard.gates.review.typed_evidence_export import ExplicitProductionAuthorization
from examcard.gates.review.typed_evidence_export_v2 import export_reviewed_typed_evidence_fixture_v2
from examcard.gates.review.fixtures_v2 import build_reviewed_candidate_v2, build_reviewed_fixture_v2
from examcard.gates.review.authorization import ProductionAuthorizationDecision
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture
from examcard.knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2
from examcard.gates.review.queue_v2 import AdmissionQueueItemV2
from tests.fixtures.admission_kernel import (
    CANDIDATE_ID,
    make_queue_item as make_v1_queue_item,
    make_source_page,
)
from tests.test_admission_threshold_v2 import (
    make_decision,
    make_queue_item,
    make_v1_decision,
)


def _source_manifest(decision: HumanDecisionV2) -> HumanDecisionManifestV2:
    return hash_model_r8x(
        HumanDecisionManifestV2,
        {
            "manifest_id": "manifest:r84:human:v2-export-source-0001",
            "queue_sha256": decision.queue_sha256,
            "decisions": (decision,),
            "decision_count": 1,
        },
        "manifest_sha256",
    )


def _authorization(**overrides: Any) -> ExplicitProductionAuthorization:
    payload: dict[str, Any] = {
        "reviewer": "human-operator",
        "reason": "explicit threshold production authorization",
        "recorded_at": "2026-09-10T00:00:00Z",
        "identity_period": "v2-export",
    }
    payload.update(overrides)
    return ExplicitProductionAuthorization.model_validate(payload)


def _setup() -> tuple[HumanDecisionManifestV2, HumanDecisionV2, AdmissionQueueItemV2, ExplicitProductionAuthorization]:
    queue_item = make_queue_item()
    decision = make_decision(queue_item)
    return _source_manifest(decision), decision, queue_item, _authorization()


def _export() -> ReviewedTypedEvidenceFixtureV2:
    source_manifest, decision, queue_item, authorization = _setup()
    return export_reviewed_typed_evidence_fixture_v2(
        source_manifest=source_manifest,
        released=(decision,),
        queue_items_by_candidate={decision.candidate_id: queue_item},
        authorization=authorization,
    )


def _rebuild_chain() -> tuple[
    ReviewedTypedEvidenceFixtureV2,
    HumanDecisionManifestV2,
    Any,
    ProductionAuthorizationManifestV2,
    HumanDecisionManifestV2,
    Any,
    Any,
]:
    source_manifest, decision, queue_item, authorization = _setup()
    candidate = build_reviewed_candidate_v2(queue_item, decision.candidate_id, decision)
    initial_fixture = build_reviewed_fixture_v2(source_manifest.queue_sha256, (candidate,))
    authorized_decision = build_authorization_decision_v2(
        candidate,
        decision,
        queue_sha256=source_manifest.queue_sha256,
        identity_period=authorization.identity_period,
        reviewer=authorization.reviewer,
        reason=authorization.reason,
        recorded_at=authorization.recorded_at,
    )
    authorization_manifest = hash_model_r8x(
        ProductionAuthorizationManifestV2,
        {
            "manifest_id": f"manifest:r810:human:{authorization.identity_period}-{canonical_hash(source_manifest.queue_sha256)[:24]}",
            "r89_frozen_input_audit_sha256": canonical_hash([queue_item.model_dump(mode="json")]),
            "r89_admission_bundle_sha256": initial_fixture.fixture_sha256,
            "r89_source_page_sidecar_sha256": canonical_hash(
                [page.model_dump(mode="json") for page in decision.pages]
            ),
            "r89_queue_sha256": source_manifest.queue_sha256,
            "r86_freeze_sha256": source_manifest.manifest_sha256,
            "decisions": (authorized_decision,),
            "decision_count": 1,
            "reviewed_candidate_count": 1,
        },
        "manifest_sha256",
    )
    exported_manifest, receipt = export_r84_manifest_v2(authorization_manifest)
    ingested_fixture = strict_ingest_exported_manifest_v2(exported_manifest, (queue_item,))
    final_fixture = build_reviewed_typed_evidence_fixture_v2(
        admission_bundle_sha256=initial_fixture.fixture_sha256,
        authorization_manifest_sha256=authorization_manifest.manifest_sha256,
        exported_manifest_sha256=exported_manifest.manifest_sha256,
        ingested_fixture=ingested_fixture,
    )
    return (
        final_fixture,
        source_manifest,
        initial_fixture,
        authorization_manifest,
        exported_manifest,
        ingested_fixture,
        receipt,
    )


def test_threshold_export_is_end_to_end_and_preserves_threshold() -> None:
    fixture = _export()
    candidate = fixture.candidates[0]

    assert isinstance(fixture, ReviewedTypedEvidenceFixtureV2)
    assert fixture.fixture_authorized is True
    assert fixture.cards_created is False
    assert fixture.step3_authorized is False
    assert fixture.step4_authorized is False
    assert candidate.presentation == "threshold"
    assert candidate.typed_contract.presentation == "threshold"
    assert candidate.typed_contract.tokens[0].source_role == "threshold_token"
    assert candidate.source_decision_id.startswith("decision:r84:human:r810v2-")


def test_threshold_export_closes_four_independent_hashes() -> None:
    fixture, source_manifest, initial_fixture, authorization_manifest, exported_manifest, ingested_fixture, _receipt = (
        _rebuild_chain()
    )
    public_fixture = _export()

    assert public_fixture == fixture
    assert fixture.r89_admission_bundle_sha256 == initial_fixture.fixture_sha256
    assert fixture.authorization_manifest_sha256 == authorization_manifest.manifest_sha256
    assert fixture.exported_r84_manifest_sha256 == exported_manifest.manifest_sha256
    assert fixture.r84_fixture_sha256 == ingested_fixture.fixture_sha256
    assert authorization_manifest.r86_freeze_sha256 == source_manifest.manifest_sha256
    assert fixture.fixture_sha256 == canonical_hash(fixture.model_dump(mode="json", exclude={"fixture_sha256"}))


def test_threshold_export_json_roundtrip_is_strict() -> None:
    fixture = _export()
    restored = ReviewedTypedEvidenceFixtureV2.model_validate(
        json.loads(json.dumps(fixture.model_dump(mode="json"), ensure_ascii=False))
    )

    assert restored == fixture
    assert restored.candidates[0].typed_contract.presentation == "threshold"
    assert restored.candidates[0].typed_contract.tokens[0].source_role == "threshold_token"


def test_export_requires_released_decision_to_be_in_source_manifest() -> None:
    source_manifest, _decision, queue_item, authorization = _setup()
    foreign = make_decision(decision_id="decision:r84:human:foreign-0001")

    with pytest.raises(ValueError, match="absent from source manifest"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(foreign,),
            queue_items_by_candidate={foreign.candidate_id: queue_item},
            authorization=authorization,
        )


def test_export_rejects_changed_decision_hash() -> None:
    source_manifest, decision, queue_item, authorization = _setup()
    payload = decision.model_dump(mode="json")
    payload["reason"] = "changed after source manifest freeze"
    changed = hash_model_r8x(HumanDecisionV2, payload, "decision_sha256")

    with pytest.raises(ValueError, match="hash crossed source manifest"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(changed,),
            queue_items_by_candidate={changed.candidate_id: queue_item},
            authorization=authorization,
        )


def test_export_rejects_v1_source_manifest() -> None:
    _source, _decision, queue_item, authorization = _setup()
    v1_queue = make_v1_queue_item()
    v1_decision = make_v1_decision(v1_queue)
    v1_manifest = hash_model_r8x(
        HumanDecisionManifest,
        {
            "manifest_id": "manifest:r84:human:v1-export-0001",
            "queue_sha256": "d" * 64,
            "decisions": (v1_decision,),
            "decision_count": 1,
        },
        "manifest_sha256",
    )

    with pytest.raises(ValueError, match="source manifest must be a V2 object"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=v1_manifest,
            released=(_decision,),
            queue_items_by_candidate={_decision.candidate_id: queue_item},
            authorization=authorization,
        )


def test_export_rejects_v1_released_decision() -> None:
    source_manifest, _decision, queue_item, authorization = _setup()
    v1_queue = make_v1_queue_item()
    v1_decision = make_v1_decision(v1_queue)

    with pytest.raises(ValueError, match="released decision must be a V2 object"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(v1_decision,),
            queue_items_by_candidate={_decision.candidate_id: queue_item},
            authorization=authorization,
        )


def test_export_rejects_v1_queue_item() -> None:
    source_manifest, decision, _queue_item, authorization = _setup()
    v1_queue = make_v1_queue_item()

    with pytest.raises(ValueError, match="queue item must be a V2 object"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(decision,),
            queue_items_by_candidate={CANDIDATE_ID: v1_queue},
            authorization=authorization,
        )


def test_export_revalidates_explicit_authorization_boundary() -> None:
    source_manifest, decision, queue_item, _authorization_value = _setup()
    duck_authorization = type(
        "DuckAuthorization",
        (),
        {
            "reviewer": "human-operator",
            "reason": "explicit threshold production authorization",
            "recorded_at": "2026-09-10T00:00:00Z",
            "identity_period": "x:y",
        },
    )()

    with pytest.raises(ValueError, match="validation error"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(decision,),
            queue_items_by_candidate={decision.candidate_id: queue_item},
            authorization=duck_authorization,  # type: ignore[arg-type]
        )


def test_export_rejects_duplicate_released_candidate() -> None:
    source_manifest, decision, queue_item, authorization = _setup()

    with pytest.raises(ValueError, match="binds one candidate twice"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(decision, decision),
            queue_items_by_candidate={decision.candidate_id: queue_item},
            authorization=authorization,
        )


def test_export_rejects_missing_queue_item() -> None:
    source_manifest, decision, _queue_item, authorization = _setup()

    with pytest.raises(ValueError, match="no frozen queue item"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(decision,),
            queue_items_by_candidate={},
            authorization=authorization,
        )


def test_export_rejects_non_reviewed_decision() -> None:
    queue_item = make_queue_item()
    decision = make_decision(queue_item, disposition="manual_review")
    source_manifest = _source_manifest(decision)

    with pytest.raises(ValueError, match="not a reviewed candidate"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(decision,),
            queue_items_by_candidate={decision.candidate_id: queue_item},
            authorization=_authorization(),
        )


def test_export_rejects_crossed_queue_source_manifest() -> None:
    source_manifest, decision, queue_item, authorization = _setup()
    foreign_queue = make_queue_item(
        source_manifest_sha256="f" * 64,
        pages=(make_source_page(source_manifest_sha256="f" * 64),),
    )

    with pytest.raises(ValueError, match="source manifest"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(decision,),
            queue_items_by_candidate={decision.candidate_id: foreign_queue},
            authorization=authorization,
        )


def test_exported_manifest_has_fresh_decision_binding_and_receipt() -> None:
    source_manifest, decision, queue_item, authorization = _setup()
    fixture = export_reviewed_typed_evidence_fixture_v2(
        source_manifest=source_manifest,
        released=(decision,),
        queue_items_by_candidate={decision.candidate_id: queue_item},
        authorization=authorization,
    )

    assert fixture.candidates[0].source_decision_id != decision.decision_id
    assert fixture.candidates[0].source_decision_id.startswith("decision:r84:human:r810v2-")
    assert fixture.candidates[0].typed_contract.presentation == "threshold"

    # Rebuild the V2 authorization manifest to assert its public export receipt shape.
    authorization_candidate = fixture.candidates[0]
    assert authorization_candidate.candidate_sha256 != decision.decision_sha256
    assert len(authorization_candidate.typed_contract.tokens) == 1


def test_empty_released_set_fails_closed() -> None:
    source_manifest, _decision, _queue_item, authorization = _setup()

    with pytest.raises(ValueError, match="released V2 set is empty"):
        export_reviewed_typed_evidence_fixture_v2(
            source_manifest=source_manifest,
            released=(),
            queue_items_by_candidate={},
            authorization=authorization,
        )


def test_gate_local_reviewed_fixture_v2_preserves_empty_v1_semantics() -> None:
    fixture = build_reviewed_fixture_v2("d" * 64, ())

    assert fixture.candidates == ()
    assert fixture.reviewed_candidate_count == 0
    assert fixture.fixture_authorized is False


def test_v1_authorization_family_still_rejects_threshold_presentation() -> None:
    from tests.test_gates_review_authorization import _authorization_decision

    valid_v1 = _authorization_decision()
    payload = valid_v1.model_dump(mode="json")
    payload["presentation"] = "threshold"

    with pytest.raises(ValueError, match="literal_error"):
        ProductionAuthorizationDecision.model_validate(payload)


def test_v1_final_fixture_still_rejects_v2_threshold_candidate_family() -> None:
    fixture = _export()
    payload = {
        "r89_admission_bundle_sha256": fixture.r89_admission_bundle_sha256,
        "authorization_manifest_sha256": fixture.authorization_manifest_sha256,
        "exported_r84_manifest_sha256": fixture.exported_r84_manifest_sha256,
        "r84_fixture_sha256": fixture.r84_fixture_sha256,
        "candidates": [fixture.candidates[0].model_dump(mode="json")],
        "reviewed_candidate_count": 1,
        "fixture_authorized": True,
        "cards_created": False,
        "step3_authorized": False,
        "step4_authorized": False,
        "fixture_sha256": "0" * 64,
    }

    with pytest.raises(ValueError, match="threshold"):
        ReviewedTypedEvidenceFixture.model_validate(payload)
