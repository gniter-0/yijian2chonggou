"""显式生产授权层（规范源 r810，次要参考 r104/r86）：授权决策 → 导出 r84 权威清单 → 严格摄取。

状态机边：显式授权（ProductionAuthorizationDecision，全确认 Literal 恒真）→
export_r84_manifest 派生 r84 HumanDecisionManifest（新鲜身份，回执钉扎双向哈希）→
r84 严格摄取产 reviewed fixture；非 reviewed 处置经 DispositionProjection 原样守恒
（automatic_upgrade 恒 False）。生产语料 schema_version 保真（phase2r810_*）。
"""
from __future__ import annotations

from typing import Iterable, Literal

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures import (
    HASH_PATTERN,
    ID_PATTERN,
    SourcePageBinding,
    TypedContract,
)
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...materials.schema import StrictModel
from .decisions import HUMAN_DECISION_VERSION, HumanDecision, HumanDecisionManifest

AUTHORIZATION_VERSION = "phase2r810_production_authorization_decision_v1"
AUTHORIZATION_MANIFEST_VERSION = "phase2r810_production_authorization_manifest_v1"
DISPOSITION_VERSION = "phase2r810_disposition_projection_item_v1"
DISPOSITION_PROJECTION_VERSION = "phase2r810_disposition_projection_v1"
PRESERVED_QUEUE_VERSION = "phase2r810_preserved_disposition_queue_v1"
AUTHORIZATION_AUDIT_VERSION = "phase2r810_authorization_audit_v1"
R84_RECEIPT_VERSION = "phase2r810_r84_export_receipt_v1"

DISPOSITIONS = ("reviewed_candidate", "manual_review", "blocked", "not_applicable")


class ProductionAuthorizationDecision(StrictModel):
    schema_version: Literal["phase2r810_production_authorization_decision_v1"] = AUTHORIZATION_VERSION
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
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
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
    typed_contract: TypedContract
    typed_contract_sha256: str = Field(pattern=HASH_PATTERN)
    recorded_at: str = Field(min_length=1)
    browser_demo: Literal[False] = False
    production_fixture_authorized: Literal[True] = True
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    decision_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_decision(self) -> "ProductionAuthorizationDecision":
        if self.typed_contract.presentation != self.presentation:
            raise ValueError("authorization presentation mismatch")
        if self.typed_contract_sha256 != canonical_hash(self.typed_contract.model_dump(mode="json")):
            raise ValueError("authorization contract hash mismatch")
        if self.decision_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"decision_sha256"})):
            raise ValueError("authorization decision hash mismatch")
        return self


class ProductionAuthorizationManifest(StrictModel):
    schema_version: Literal["phase2r810_production_authorization_manifest_v1"] = AUTHORIZATION_MANIFEST_VERSION
    manifest_id: str = Field(pattern=r"^manifest:r810:human:[a-z0-9][a-z0-9._:-]{2,255}$")
    r89_frozen_input_audit_sha256: str = Field(pattern=HASH_PATTERN)
    r89_admission_bundle_sha256: str = Field(pattern=HASH_PATTERN)
    r89_source_page_sidecar_sha256: str = Field(pattern=HASH_PATTERN)
    r89_queue_sha256: str = Field(pattern=HASH_PATTERN)
    r86_freeze_sha256: str = Field(pattern=HASH_PATTERN)
    decisions: tuple[ProductionAuthorizationDecision, ...] = Field(min_length=1)
    decision_count: int = Field(ge=0)
    reviewed_candidate_count: int = Field(ge=0)
    production_fixture_authorized: Literal[True] = True
    cards_created: Literal[False] = False
    step3_authorized: Literal[False] = False
    step4_authorized: Literal[False] = False
    manifest_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_manifest(self) -> "ProductionAuthorizationManifest":
        if self.decision_count != len(self.decisions) or self.reviewed_candidate_count != self.decision_count:
            raise ValueError("authorization count mismatch")
        if len({item.production_decision_id for item in self.decisions}) != self.decision_count:
            raise ValueError("authorization identities must be unique")
        if len({item.candidate_id for item in self.decisions}) != self.decision_count:
            raise ValueError("authorization candidates must be unique")
        if any(item.queue_sha256 != self.r89_queue_sha256 for item in self.decisions):
            raise ValueError("authorization crossed queue hash")
        if self.manifest_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"manifest_sha256"})):
            raise ValueError("authorization manifest hash mismatch")
        return self


class DispositionProjectionItem(StrictModel):
    schema_version: Literal["phase2r810_disposition_projection_item_v1"] = DISPOSITION_VERSION
    candidate_id: str = Field(pattern=ID_PATTERN)
    queue_item_id: str = Field(pattern=ID_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    source_decision_id: str = Field(pattern=ID_PATTERN)
    source_decision_sha256: str = Field(pattern=HASH_PATTERN)
    input_disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    effective_disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    authorization_id: str | None = None
    exported_r84_decision_id: str | None = None
    typed_contract_sha256: str | None = None
    page_binding_closed: Literal[True] = True
    browser_demo: Literal[False] = False
    automatic_upgrade: Literal[False] = False
    reason: str = Field(min_length=3)
    entry_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_item(self) -> "DispositionProjectionItem":
        if self.effective_disposition == "reviewed_candidate":
            if not self.authorization_id or not self.exported_r84_decision_id or not self.typed_contract_sha256:
                raise ValueError("reviewed disposition lacks explicit authorization closure")
        elif self.authorization_id or self.exported_r84_decision_id:
            raise ValueError("non-reviewed disposition carries authorization")
        if self.entry_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"entry_sha256"})):
            raise ValueError("disposition item hash mismatch")
        return self


class DispositionProjection(StrictModel):
    schema_version: Literal["phase2r810_disposition_projection_v1"] = DISPOSITION_PROJECTION_VERSION
    r89_decision_audit_bundle_sha256: str = Field(pattern=HASH_PATTERN)
    r89_queue_sha256: str = Field(pattern=HASH_PATTERN)
    candidates: tuple[DispositionProjectionItem, ...] = Field(min_length=1)
    candidate_count: int = Field(ge=0)
    reviewed_candidate_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    not_applicable_count: int = Field(ge=0)
    projection_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_projection(self) -> "DispositionProjection":
        if self.candidate_count != len(self.candidates):
            raise ValueError("projection count mismatch")
        counts = {state: sum(item.effective_disposition == state for item in self.candidates) for state in DISPOSITIONS}
        expected = tuple(counts[state] for state in DISPOSITIONS)
        actual = (self.reviewed_candidate_count, self.manual_review_count, self.blocked_count, self.not_applicable_count)
        if actual != expected:
            raise ValueError("projection disposition counts mismatch")
        if self.projection_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"projection_sha256"})):
            raise ValueError("projection hash mismatch")
        return self


class PreservedDispositionQueue(StrictModel):
    schema_version: Literal["phase2r810_preserved_disposition_queue_v1"] = PRESERVED_QUEUE_VERSION
    disposition: Literal["manual_review", "not_applicable"]
    r89_queue_sha256: str = Field(pattern=HASH_PATTERN)
    candidates: tuple[DispositionProjectionItem, ...] = ()
    queue_count: int = Field(ge=0)
    queue_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_queue(self) -> "PreservedDispositionQueue":
        if self.queue_count != len(self.candidates) or any(item.effective_disposition != self.disposition for item in self.candidates):
            raise ValueError("preserved queue disposition mismatch")
        if self.queue_sha256 != canonical_hash([item.model_dump(mode="json") for item in self.candidates]):
            raise ValueError("preserved queue hash mismatch")
        return self


class AuthorizationAudit(StrictModel):
    schema_version: Literal["phase2r810_authorization_audit_v1"] = AUTHORIZATION_AUDIT_VERSION
    audit_id: str = Field(pattern=ID_PATTERN)
    candidate_id: str = Field(pattern=ID_PATTERN)
    source_decision_id: str = Field(pattern=ID_PATTERN)
    source_decision_sha256: str = Field(pattern=HASH_PATTERN)
    queue_item_id: str = Field(pattern=ID_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    input_disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    effective_disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    authorization_id: str | None = None
    authorization_sha256: str | None = None
    exported_r84_decision_id: str | None = None
    source_text_confirmed: bool = False
    anchor_confirmed: bool = False
    source_page_confirmed: bool = False
    image_hash_confirmed: bool = False
    typed_contract_confirmed: bool = False
    typed_contract_complete: bool = False
    typed_contract_sha256: str | None = None
    page_binding_closed: Literal[True] = True
    browser_demo: Literal[False] = False
    production_fixture_authorized: Literal[False] = False
    admission_basis: Literal["explicit_human_reviewed_candidate", "r89_disposition_preserved"]
    reason: str = Field(min_length=3)
    audit_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_audit(self) -> "AuthorizationAudit":
        if self.effective_disposition == "reviewed_candidate":
            if self.admission_basis != "explicit_human_reviewed_candidate" or not self.authorization_id or not self.exported_r84_decision_id:
                raise ValueError("reviewed audit lacks explicit production authorization")
            if not all(
                (
                    self.source_text_confirmed,
                    self.anchor_confirmed,
                    self.source_page_confirmed,
                    self.image_hash_confirmed,
                    self.typed_contract_confirmed,
                    self.typed_contract_complete,
                )
            ):
                raise ValueError("reviewed audit lacks five confirmations")
        elif self.authorization_id or self.authorization_sha256 or self.exported_r84_decision_id:
            raise ValueError("preserved audit carries authorization")
        if self.audit_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"audit_sha256"})):
            raise ValueError("authorization audit hash mismatch")
        return self


class R84ExportReceipt(StrictModel):
    schema_version: Literal["phase2r810_r84_export_receipt_v1"] = R84_RECEIPT_VERSION
    source_manifest_id: str = Field(pattern=ID_PATTERN)
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    exported_manifest_id: str = Field(pattern=ID_PATTERN)
    exported_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    exported_decision_count: int = Field(ge=0)
    exported_reviewed_candidate_count: int = Field(ge=0)
    receipt_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_receipt(self) -> "R84ExportReceipt":
        if self.receipt_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"receipt_sha256"})):
            raise ValueError("r84 export receipt hash mismatch")
        return self


def export_r84_decision(decision: ProductionAuthorizationDecision) -> HumanDecision:
    """r810 定式：授权决策派生 r84 权威决策（新鲜身份 = 授权 id 的内容哈希派生）。"""
    payload = {
        "schema_version": HUMAN_DECISION_VERSION,
        "decision_id": f"decision:r84:human:r810-{canonical_hash(decision.production_decision_id)[:24]}",
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
    return hash_model_r8x(HumanDecision, payload, "decision_sha256")  # type: ignore[return-value]


def export_r84_manifest(manifest: ProductionAuthorizationManifest) -> tuple[HumanDecisionManifest, R84ExportReceipt]:
    decisions = tuple(export_r84_decision(item) for item in manifest.decisions)
    payload = {
        "manifest_id": f"manifest:r84:human:r810-{canonical_hash(manifest.manifest_id)[:24]}",
        "queue_sha256": manifest.r89_queue_sha256,
        "decisions": decisions,
        "decision_count": len(decisions),
    }
    exported = hash_model_r8x(HumanDecisionManifest, payload, "manifest_sha256")
    receipt_payload = {
        "source_manifest_id": manifest.manifest_id,
        "source_manifest_sha256": manifest.manifest_sha256,
        "exported_manifest_id": exported.manifest_id,
        "exported_manifest_sha256": exported.manifest_sha256,
        "exported_decision_count": len(decisions),
        "exported_reviewed_candidate_count": len(decisions),
    }
    receipt = hash_model_r8x(R84ExportReceipt, receipt_payload, "receipt_sha256")
    return exported, receipt  # type: ignore[return-value]


def assert_no_silent_upgrade(items: Iterable[DispositionProjectionItem]) -> None:
    """处置守恒（投影侧）：输入与生效处置不一致即违例（r810 automatic_upgrade 恒 False）。"""
    for item in items:
        if item.input_disposition != item.effective_disposition:
            raise ValueError(f"silent disposition change on {item.candidate_id}: {item.input_disposition} -> {item.effective_disposition}")
