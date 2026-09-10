"""Public V2 threshold-capable reviewed typed-evidence exporter."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Mapping, Sequence

from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2
from .authorization_v2 import (
    ProductionAuthorizationManifestV2,
    build_authorization_decision_v2,
    build_reviewed_typed_evidence_fixture_v2,
    export_r84_manifest_v2,
    strict_ingest_exported_manifest_v2,
)
from .decisions_v2 import HumanDecisionManifestV2, HumanDecisionV2, validate_human_decision_v2
from .fixtures_v2 import build_reviewed_candidate_v2, build_reviewed_fixture_v2
from .queue_v2 import AdmissionQueueItemV2
from .typed_evidence_export import ExplicitProductionAuthorization


def _require_v2(value: object, expected: type[object], label: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"{label} must be a V2 object, got {type(value).__name__}")


def export_reviewed_typed_evidence_fixture_v2(
    *,
    source_manifest: HumanDecisionManifestV2,
    released: Sequence[HumanDecisionV2],
    queue_items_by_candidate: Mapping[str, AdmissionQueueItemV2],
    authorization: ExplicitProductionAuthorization,
) -> ReviewedTypedEvidenceFixtureV2:
    """Assemble and close a released V2 threshold authority chain."""

    _require_v2(source_manifest, HumanDecisionManifestV2, "source manifest")
    authorization = ExplicitProductionAuthorization.model_validate(authorization)
    if not released:
        raise ValueError("released V2 set is empty: nothing to export")
    for decision in released:
        _require_v2(decision, HumanDecisionV2, "released decision")
    for item in queue_items_by_candidate.values():
        _require_v2(item, AdmissionQueueItemV2, "queue item")

    source_by_id = {decision.decision_id: decision for decision in source_manifest.decisions}
    seen_candidates: set[str] = set()
    ordered_queue_items: list[AdmissionQueueItemV2] = []
    candidates = []
    decision_by_candidate: dict[str, HumanDecisionV2] = {}
    for decision in released:
        source_decision = source_by_id.get(decision.decision_id)
        if source_decision is None:
            raise ValueError(f"released decision is absent from source manifest: {decision.decision_id}")
        if source_decision.decision_sha256 != decision.decision_sha256:
            raise ValueError(f"released decision hash crossed source manifest: {decision.decision_id}")
        if source_decision.model_dump(mode="json") != decision.model_dump(mode="json"):
            raise ValueError(f"released decision payload crossed source manifest: {decision.decision_id}")
        if decision.disposition != "reviewed_candidate":
            raise ValueError(f"released decision is not a reviewed candidate: {decision.decision_id}")
        if decision.typed_contract is None or decision.typed_contract_sha256 is None:
            raise ValueError(f"released decision lacks a complete typed contract: {decision.decision_id}")
        if decision.queue_sha256 != source_manifest.queue_sha256:
            raise ValueError(f"released decision crossed the queue hash chain: {decision.decision_id}")
        if decision.candidate_id in seen_candidates:
            raise ValueError(f"released set binds one candidate twice: {decision.candidate_id}")
        queue_item = queue_items_by_candidate.get(decision.candidate_id)
        if queue_item is None:
            raise ValueError(f"released decision has no frozen queue item: {decision.candidate_id}")
        validate_human_decision_v2(decision, queue_item, SimpleNamespace(candidate_id=decision.candidate_id))
        candidates.append(build_reviewed_candidate_v2(queue_item, decision.candidate_id, decision))
        ordered_queue_items.append(queue_item)
        decision_by_candidate[decision.candidate_id] = decision
        seen_candidates.add(decision.candidate_id)

    admission_fixture = build_reviewed_fixture_v2(source_manifest.queue_sha256, candidates)
    authorization_decisions = tuple(
        build_authorization_decision_v2(
            candidate,
            decision_by_candidate[candidate.candidate_id],
            queue_sha256=source_manifest.queue_sha256,
            identity_period=authorization.identity_period,
            reviewer=authorization.reviewer,
            reason=authorization.reason,
            recorded_at=authorization.recorded_at,
        )
        for candidate in candidates
    )
    manifest_payload = {
        "manifest_id": (
            f"manifest:r810:human:{authorization.identity_period}-"
            f"{canonical_hash(source_manifest.queue_sha256)[:24]}"
        ),
        "r89_frozen_input_audit_sha256": canonical_hash(
            [item.model_dump(mode="json") for item in ordered_queue_items]
        ),
        "r89_admission_bundle_sha256": admission_fixture.fixture_sha256,
        "r89_source_page_sidecar_sha256": canonical_hash(
            [page.model_dump(mode="json") for decision in released for page in decision.pages]
        ),
        "r89_queue_sha256": source_manifest.queue_sha256,
        "r86_freeze_sha256": source_manifest.manifest_sha256,
        "decisions": authorization_decisions,
        "decision_count": len(authorization_decisions),
        "reviewed_candidate_count": len(authorization_decisions),
        "production_fixture_authorized": True,
        "cards_created": False,
        "step3_authorized": False,
        "step4_authorized": False,
    }
    authorization_manifest = hash_model_r8x(
        ProductionAuthorizationManifestV2, manifest_payload, "manifest_sha256"
    )
    exported_manifest, _receipt = export_r84_manifest_v2(authorization_manifest)
    ingested_fixture = strict_ingest_exported_manifest_v2(exported_manifest, ordered_queue_items)
    return build_reviewed_typed_evidence_fixture_v2(
        admission_bundle_sha256=admission_fixture.fixture_sha256,
        authorization_manifest_sha256=authorization_manifest.manifest_sha256,
        exported_manifest_sha256=exported_manifest.manifest_sha256,
        ingested_fixture=ingested_fixture,
    )


__all__ = ("export_reviewed_typed_evidence_fixture_v2",)
