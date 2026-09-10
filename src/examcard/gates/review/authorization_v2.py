"""Parallel r810 authorization/export chain for threshold-capable admission."""
from __future__ import annotations

import re
from typing import Final, Literal, Sequence

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures_constants import HASH_PATTERN, ID_PATTERN
from ...knowledge.contracts.admission_fixtures_typed import (
    ConstraintContract,
    DiagramContract,
    FormulaContract,
    OrderedListContract,
    SourcePageBinding,
    TableContract,
)
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...knowledge.contracts.admission_v2 import (
    ReviewedCandidateV2,
    ReviewedTypedEvidenceFixtureV2,
    ThresholdContract,
    PresentationV2,
    TypedContractV2,
)
from ...materials.schema import StrictModel
from .contracts_v2 import validate_typed_contract_binding_v2
from .decisions_v2 import HumanDecisionManifestV2, HumanDecisionV2, validate_human_decision_v2
from .fixtures_v2 import ReviewedFixtureV2, build_reviewed_candidate_v2, build_reviewed_fixture_v2
from .queue_v2 import AdmissionQueueItemV2

AUTHORIZATION_V2_VERSION: Final = "phase2r810_production_authorization_decision_v2"
AUTHORIZATION_MANIFEST_V2_VERSION: Final = "phase2r810_production_authorization_manifest_v2"
R84_RECEIPT_V2_VERSION: Final = "phase2r810_r84_export_receipt_v2"


class ProductionAuthorizationDecisionV2(StrictModel):
    """Explicit production authorization whose presentation supports threshold."""

    schema_version: Literal["phase2r810_production_authorization_decision_v2"] = AUTHORIZATION_V2_VERSION
    production_decision_id: str = Field(pattern=r"^decision:r810:human:[a-z0-9][a-z0-9._:-]{2,255}$")
    r89_admission_candidate_id: str = Field(pattern=r"^admission:r89:production:[a-z0-9][a-z0-9._:-]{2,255}$")
    r89_candidate_sha256: str = Field(pattern=HASH_PATTERN)
    source_decision_id: str = Field(pattern=ID_PATTERN)
    source_decision_sha256: str = Field(pattern=HASH_PATTERN)
    candidate_id: str = Field(pattern=ID_PATTERN)
    queue_item_id: str = Field(pattern=ID_PATTERN)
    queue_sha256: str = Field(pattern=HASH_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: PresentationV2
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    pages: tuple[SourcePageBinding, ...] = Field(min_length=1)
    source_text_snapshot: tuple[str, ...] = Field(min_length=1)
    disposition: Literal["reviewed_candidate"] = "reviewed_candidate"
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    source_text_confirmed: Literal[True] = True
    anchor_confirmed: Literal[True] = True
    source_page_confirmed: Literal[True] = True
    image_hash_confirmed: Literal[True] = True
    typed_contract_confirmed: Literal[True] = True
    typed_contract: TypedContractV2
    typed_contract_sha256: str = Field(pattern=HASH_PATTERN)
    recorded_at: str = Field(min_length=1)
    browser_demo: Literal[False] = False
    production_fixture_authorized: Literal[True] = True
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    decision_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_decision(self) -> "ProductionAuthorizationDecisionV2":
        if self.typed_contract.presentation != self.presentation:
            raise ValueError("authorization V2 presentation mismatch")
        if self.typed_contract_sha256 != canonical_hash(self.typed_contract.model_dump(mode="json")):
            raise ValueError("authorization V2 contract hash mismatch")
        if self.source_text_snapshot != source_text_snapshot_v2(self.typed_contract):
            raise ValueError("authorization V2 source snapshot mismatch")
        if self.decision_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"decision_sha256"})):
            raise ValueError("authorization V2 decision hash mismatch")
        return self


class ProductionAuthorizationManifestV2(StrictModel):
    """Manifest binding V2 authorization decisions to one queue/source chain."""

    schema_version: Literal["phase2r810_production_authorization_manifest_v2"] = AUTHORIZATION_MANIFEST_V2_VERSION
    manifest_id: str = Field(pattern=r"^manifest:r810:human:[a-z0-9][a-z0-9._:-]{2,255}$")
    r89_frozen_input_audit_sha256: str = Field(pattern=HASH_PATTERN)
    r89_admission_bundle_sha256: str = Field(pattern=HASH_PATTERN)
    r89_source_page_sidecar_sha256: str = Field(pattern=HASH_PATTERN)
    r89_queue_sha256: str = Field(pattern=HASH_PATTERN)
    r86_freeze_sha256: str = Field(pattern=HASH_PATTERN)
    decisions: tuple[ProductionAuthorizationDecisionV2, ...] = Field(min_length=1)
    decision_count: int = Field(ge=0)
    reviewed_candidate_count: int = Field(ge=0)
    production_fixture_authorized: Literal[True] = True
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    manifest_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_manifest(self) -> "ProductionAuthorizationManifestV2":
        if self.decision_count != len(self.decisions) or self.reviewed_candidate_count != self.decision_count:
            raise ValueError("authorization V2 count mismatch")
        if len({item.production_decision_id for item in self.decisions}) != self.decision_count:
            raise ValueError("authorization V2 identities must be unique")
        if len({item.candidate_id for item in self.decisions}) != self.decision_count:
            raise ValueError("authorization V2 candidates must be unique")
        if any(item.queue_sha256 != self.r89_queue_sha256 for item in self.decisions):
            raise ValueError("authorization V2 crossed queue hash")
        if self.manifest_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"manifest_sha256"})):
            raise ValueError("authorization V2 manifest hash mismatch")
        return self


class R84ExportReceiptV2(StrictModel):
    """Bidirectional seal for the V2 r84 export."""

    schema_version: Literal["phase2r810_r84_export_receipt_v2"] = R84_RECEIPT_V2_VERSION
    source_manifest_id: str = Field(pattern=ID_PATTERN)
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    exported_manifest_id: str = Field(pattern=ID_PATTERN)
    exported_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    exported_decision_count: int = Field(ge=0)
    exported_reviewed_candidate_count: int = Field(ge=0)
    receipt_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_receipt(self) -> "R84ExportReceiptV2":
        if self.receipt_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"receipt_sha256"})):
            raise ValueError("r84 V2 export receipt hash mismatch")
        return self


def source_text_snapshot_v2(contract: object) -> tuple[str, ...]:
    """Return exact source text in contract storage order, without normalization."""

    if isinstance(contract, DiagramContract):
        return tuple(node.text for node in contract.nodes)
    if isinstance(contract, TableContract):
        return tuple(cell.text for cell in contract.cells)
    if isinstance(contract, OrderedListContract):
        return tuple(item.text for item in contract.items)
    if isinstance(contract, (ConstraintContract, ThresholdContract, FormulaContract)):
        return tuple(token.text for token in contract.tokens)
    raise TypeError(f"unsupported typed contract for V2 source snapshot: {type(contract)!r}")


def _require_v2(value: object, expected: type[object], label: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"{label} must be a V2 object, got {type(value).__name__}")


def validate_authorization_decision_v2(
    authorization: ProductionAuthorizationDecisionV2,
    candidate: ReviewedCandidateV2,
    source_decision: HumanDecisionV2,
) -> None:
    """Prove the exact candidate/source decision binding independently of construction."""

    _require_v2(authorization, ProductionAuthorizationDecisionV2, "authorization")
    _require_v2(candidate, ReviewedCandidateV2, "candidate")
    _require_v2(source_decision, HumanDecisionV2, "source decision")
    fields = (
        "candidate_id",
        "queue_item_id",
        "subject_id",
        "record_id",
        "fact_id",
        "evidence_id",
        "presentation",
        "source_manifest_sha256",
        "anchor_ids",
        "pages",
        "typed_contract",
        "typed_contract_sha256",
    )
    for field in fields:
        if getattr(authorization, field) != getattr(candidate, field):
            raise ValueError(f"authorization V2 crossed candidate field: {field}")
        if getattr(candidate, field) != getattr(source_decision, field):
            raise ValueError(f"authorization V2 crossed source decision field: {field}")
    if authorization.queue_sha256 != source_decision.queue_sha256:
        raise ValueError("authorization V2 crossed queue hash")
    if authorization.r89_candidate_sha256 != candidate.candidate_sha256:
        raise ValueError("authorization V2 crossed candidate hash")
    if authorization.source_decision_id != source_decision.decision_id:
        raise ValueError("authorization V2 crossed source decision identity")
    if authorization.source_decision_sha256 != source_decision.decision_sha256:
        raise ValueError("authorization V2 crossed source decision hash")
    if candidate.source_decision_id != source_decision.decision_id:
        raise ValueError("candidate V2 crossed source decision identity")
    if authorization.source_text_snapshot != source_text_snapshot_v2(candidate.typed_contract):
        raise ValueError("authorization V2 crossed source snapshot")
    validate_typed_contract_binding_v2(source_decision, _candidate_queue_shape(candidate))


def _candidate_queue_shape(candidate: ReviewedCandidateV2) -> AdmissionQueueItemV2:
    """Build a strict queue-shaped copy for the V2 contract binding gate."""

    payload = {
        "candidate_id": candidate.candidate_id,
        "queue_item_id": candidate.queue_item_id,
        "subject_id": candidate.subject_id,
        "record_id": candidate.record_id,
        "fact_id": candidate.fact_id,
        "evidence_id": candidate.evidence_id,
        "presentation": candidate.presentation,
        "source_manifest_sha256": candidate.source_manifest_sha256,
        "anchor_ids": candidate.anchor_ids,
        "pages": candidate.pages,
        "disposition": "manual_review",
        "decision_audit_id": f"audit:r810:v2:{canonical_hash(candidate.candidate_id)[:24]}",
        "reason": "authorization V2 binding validation",
    }
    return hash_model_r8x(AdmissionQueueItemV2, payload, "entry_sha256")


def build_authorization_decision_v2(
    candidate: ReviewedCandidateV2,
    source_decision: HumanDecisionV2,
    *,
    queue_sha256: str,
    identity_period: str,
    reviewer: str,
    reason: str,
    recorded_at: str,
) -> ProductionAuthorizationDecisionV2:
    """Build a V2 authorization decision from exact V2 source objects."""

    _require_v2(candidate, ReviewedCandidateV2, "candidate")
    _require_v2(source_decision, HumanDecisionV2, "source decision")
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", identity_period) is None:
        raise ValueError("authorization V2 identity period must match ExplicitProductionAuthorization")
    if queue_sha256 != source_decision.queue_sha256:
        raise ValueError("authorization V2 queue hash does not match source decision")
    suffix = canonical_hash(f"{candidate.candidate_sha256}|{queue_sha256}")[:24]
    payload = {
        "production_decision_id": f"decision:r810:human:{identity_period}-{suffix}",
        "r89_admission_candidate_id": f"admission:r89:production:{identity_period}-{suffix}",
        "r89_candidate_sha256": candidate.candidate_sha256,
        "source_decision_id": source_decision.decision_id,
        "source_decision_sha256": source_decision.decision_sha256,
        "candidate_id": candidate.candidate_id,
        "queue_item_id": candidate.queue_item_id,
        "queue_sha256": queue_sha256,
        "subject_id": candidate.subject_id,
        "record_id": candidate.record_id,
        "fact_id": candidate.fact_id,
        "evidence_id": candidate.evidence_id,
        "presentation": candidate.presentation,
        "source_manifest_sha256": candidate.source_manifest_sha256,
        "anchor_ids": candidate.anchor_ids,
        "pages": candidate.pages,
        "source_text_snapshot": source_text_snapshot_v2(candidate.typed_contract),
        "reviewer": reviewer,
        "reason": reason,
        "typed_contract": candidate.typed_contract,
        "typed_contract_sha256": candidate.typed_contract_sha256,
        "recorded_at": recorded_at,
    }
    authorization = hash_model_r8x(ProductionAuthorizationDecisionV2, payload, "decision_sha256")
    validate_authorization_decision_v2(authorization, candidate, source_decision)
    return authorization


def export_r84_decision_v2(decision: ProductionAuthorizationDecisionV2) -> HumanDecisionV2:
    """Export one V2 authorization decision to a fresh V2 r84 human decision."""

    _require_v2(decision, ProductionAuthorizationDecisionV2, "authorization decision")
    payload = {
        "decision_id": f"decision:r84:human:r810v2-{canonical_hash(decision.production_decision_id)[:24]}",
        "candidate_id": decision.candidate_id,
        "queue_item_id": decision.queue_item_id,
        "queue_sha256": decision.queue_sha256,
        "subject_id": decision.subject_id,
        "record_id": decision.record_id,
        "fact_id": decision.fact_id,
        "evidence_id": decision.evidence_id,
        "presentation": decision.presentation,
        "source_manifest_sha256": decision.source_manifest_sha256,
        "anchor_ids": decision.anchor_ids,
        "pages": decision.pages,
        "disposition": "reviewed_candidate",
        "reviewer": decision.reviewer,
        "reason": decision.reason,
        "source_text_confirmed": True,
        "anchor_confirmed": True,
        "source_page_confirmed": True,
        "image_hash_confirmed": True,
        "typed_contract_confirmed": True,
        "typed_contract": decision.typed_contract,
        "typed_contract_sha256": decision.typed_contract_sha256,
        "recorded_at": decision.recorded_at,
        "browser_demo": False,
        "production_fixture_authorized": True,
    }
    return hash_model_r8x(HumanDecisionV2, payload, "decision_sha256")


def export_r84_manifest_v2(
    manifest: ProductionAuthorizationManifestV2,
) -> tuple[HumanDecisionManifestV2, R84ExportReceiptV2]:
    """Export a V2 authorization manifest and seal a bidirectional receipt."""

    _require_v2(manifest, ProductionAuthorizationManifestV2, "authorization manifest")
    decisions = tuple(export_r84_decision_v2(item) for item in manifest.decisions)
    exported_payload = {
        "manifest_id": f"manifest:r84:human:r810v2-{canonical_hash(manifest.manifest_id)[:24]}",
        "queue_sha256": manifest.r89_queue_sha256,
        "decisions": decisions,
        "decision_count": len(decisions),
    }
    exported = hash_model_r8x(HumanDecisionManifestV2, exported_payload, "manifest_sha256")
    receipt_payload = {
        "source_manifest_id": manifest.manifest_id,
        "source_manifest_sha256": manifest.manifest_sha256,
        "exported_manifest_id": exported.manifest_id,
        "exported_manifest_sha256": exported.manifest_sha256,
        "exported_decision_count": len(decisions),
        "exported_reviewed_candidate_count": len(decisions),
    }
    receipt = hash_model_r8x(R84ExportReceiptV2, receipt_payload, "receipt_sha256")
    return exported, receipt


def strict_ingest_exported_manifest_v2(
    exported: HumanDecisionManifestV2,
    queue_items: Sequence[AdmissionQueueItemV2],
) -> ReviewedFixtureV2:
    """Rebuild a V2 reviewed fixture in the authoritative queue order."""

    _require_v2(exported, HumanDecisionManifestV2, "exported manifest")
    for item in queue_items:
        _require_v2(item, AdmissionQueueItemV2, "queue item")
    decisions_by_queue_item: dict[str, HumanDecisionV2] = {}
    for decision in exported.decisions:
        if decision.queue_item_id in decisions_by_queue_item:
            raise ValueError(f"exported V2 manifest binds one queue item twice: {decision.queue_item_id}")
        decisions_by_queue_item[decision.queue_item_id] = decision
    candidates: list[ReviewedCandidateV2] = []
    ingested_queue_items: set[str] = set()
    for queue_item in queue_items:
        bound = decisions_by_queue_item.get(queue_item.queue_item_id)
        if bound is None:
            continue
        validate_human_decision_v2(bound, queue_item, type("_CandidateRef", (), {"candidate_id": bound.candidate_id})())
        candidates.append(build_reviewed_candidate_v2(queue_item, bound.candidate_id, bound))
        ingested_queue_items.add(queue_item.queue_item_id)
    missing = set(decisions_by_queue_item) - ingested_queue_items
    if missing:
        raise ValueError(f"exported V2 decisions reference unknown queue items: {sorted(missing)}")
    return build_reviewed_fixture_v2(exported.queue_sha256, candidates)


def build_reviewed_typed_evidence_fixture_v2(
    *,
    admission_bundle_sha256: str,
    authorization_manifest_sha256: str,
    exported_manifest_sha256: str,
    ingested_fixture: ReviewedFixtureV2,
) -> ReviewedTypedEvidenceFixtureV2:
    """Build the final cross-boundary V2 fixture from the closed artifact chain."""

    _require_v2(ingested_fixture, ReviewedFixtureV2, "ingested fixture")
    payload = {
        "r89_admission_bundle_sha256": admission_bundle_sha256,
        "authorization_manifest_sha256": authorization_manifest_sha256,
        "exported_r84_manifest_sha256": exported_manifest_sha256,
        "r84_fixture_sha256": ingested_fixture.fixture_sha256,
        "candidates": ingested_fixture.candidates,
        "reviewed_candidate_count": ingested_fixture.reviewed_candidate_count,
        "fixture_authorized": True,
        "cards_created": False,
        "step3_authorized": False,
        "step4_authorized": False,
    }
    return hash_model_r8x(ReviewedTypedEvidenceFixtureV2, payload, "fixture_sha256")


__all__ = (
    "AUTHORIZATION_MANIFEST_V2_VERSION",
    "AUTHORIZATION_V2_VERSION",
    "R84_RECEIPT_V2_VERSION",
    "ProductionAuthorizationDecisionV2",
    "ProductionAuthorizationManifestV2",
    "R84ExportReceiptV2",
    "build_authorization_decision_v2",
    "build_reviewed_typed_evidence_fixture_v2",
    "export_r84_decision_v2",
    "export_r84_manifest_v2",
    "source_text_snapshot_v2",
    "strict_ingest_exported_manifest_v2",
    "validate_authorization_decision_v2",
)
