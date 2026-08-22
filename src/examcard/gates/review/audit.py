"""决策审计：DecisionAudit + admission_basis 推导 + 「browser demo 不可授权」不变量（规范源 r84）。

审计是唯一允许记录浏览器输入的地方，但结构上（Literal 恒 False + 校验器）不可能借
审计授权生产：effective_disposition == reviewed_candidate 时 browser_demo 与
production_fixture_authorized 必须为 False 且六项确认齐备。生产语料 schema_version
保真（phase2r84_decision_audit_v1 / phase2r84_browser_input_audit_v1）。
"""
from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures import HASH_PATTERN, ID_PATTERN
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...materials.schema import StrictModel
from .decisions import HumanDecision
from .dispositions import derive_admission_basis, effective_disposition
from .queue import QueueItemLike

DECISION_AUDIT_VERSION: Final = "phase2r84_decision_audit_v1"
BROWSER_INPUT_AUDIT_VERSION: Final = "phase2r84_browser_input_audit_v1"


class DecisionAudit(StrictModel):
    schema_version: Literal["phase2r84_decision_audit_v1"] = DECISION_AUDIT_VERSION
    audit_id: str = Field(pattern=ID_PATTERN)
    candidate_id: str = Field(pattern=ID_PATTERN)
    queue_item_id: str = Field(pattern=ID_PATTERN)
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    source_decision_id: str | None = None
    source_decision_sha256: str | None = None
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    input_disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    effective_disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    source_text_confirmed: bool = False
    anchor_confirmed: bool = False
    source_page_confirmed: bool = False
    image_hash_confirmed: bool = False
    typed_contract_confirmed: bool = False
    typed_contract_complete: bool = False
    browser_demo: bool = False
    production_fixture_authorized: Literal[False] = False
    admission_basis: Literal[
        "no_production_human_decision",
        "browser_demo_not_authority",
        "explicit_human_reviewed_candidate",
        "explicit_human_manual_review",
        "explicit_human_blocked",
        "explicit_human_not_applicable",
    ]
    audit_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_audit(self) -> "DecisionAudit":
        if self.browser_demo and self.effective_disposition == "reviewed_candidate":
            raise ValueError("browser demo cannot authorize a reviewed candidate")
        if self.source_decision_id is None and self.source_decision_sha256 is not None:
            raise ValueError("source decision hash requires source decision")
        if self.effective_disposition == "reviewed_candidate":
            if self.browser_demo or self.production_fixture_authorized:
                raise ValueError("audit cannot authorize downstream production")
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
                raise ValueError("reviewed audit lacks complete confirmations")
        if self.audit_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"audit_sha256"})):
            raise ValueError("decision audit hash mismatch")
        return self


class BrowserInputAudit(StrictModel):
    schema_version: Literal["phase2r84_browser_input_audit_v1"] = BROWSER_INPUT_AUDIT_VERSION
    source_manifest_path: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    source_audit_path: str = Field(min_length=1)
    source_audit_sha256: str = Field(pattern=HASH_PATTERN)
    decision_count: int = Field(ge=0)
    browser_demo_count: int = Field(ge=0)
    rejected_for_production_count: int = Field(ge=0)
    production_authorized_count: Literal[0] = 0
    audit_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_browser_input(self) -> "BrowserInputAudit":
        if self.rejected_for_production_count != self.browser_demo_count:
            raise ValueError("every browser demo must be rejected for production")
        if self.production_authorized_count != 0:
            raise ValueError("browser input cannot authorize production")
        if self.audit_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"audit_sha256"})):
            raise ValueError("browser input audit hash mismatch")
        return self


def build_decision_audit(
    *,
    candidate_id: str,
    queue_item: QueueItemLike,
    decision: HumanDecision | None = None,
    browser_demo: bool = False,
    audit_id: str | None = None,
) -> DecisionAudit:
    """r84 `_audit_for` 定式：无决策 → 待审兜底；有决策 → 降格与 basis 推导。"""
    identity = audit_id or f"audit:r84:{candidate_id.split(':')[-1]}"
    if decision is None:
        payload = {
            "audit_id": identity,
            "candidate_id": candidate_id,
            "queue_item_id": queue_item.queue_item_id,
            "subject_id": queue_item.subject_id,
            "record_id": queue_item.record_id,
            "fact_id": queue_item.fact_id,
            "evidence_id": queue_item.evidence_id,
            "presentation": queue_item.presentation,
            "source_manifest_sha256": queue_item.source_manifest_sha256,
            "anchor_ids": queue_item.anchor_ids,
            "source_decision_id": None,
            "source_decision_sha256": None,
            "reviewer": "review-ingestion",
            "reason": "awaiting explicit human source-page decision",
            "input_disposition": "manual_review",
            "effective_disposition": "manual_review",
            "browser_demo": False,
            "admission_basis": derive_admission_basis(None, browser_demo=False),
        }
    else:
        payload = {
            "audit_id": identity,
            "candidate_id": candidate_id,
            "queue_item_id": queue_item.queue_item_id,
            "subject_id": decision.subject_id,
            "record_id": decision.record_id,
            "fact_id": decision.fact_id,
            "evidence_id": decision.evidence_id,
            "presentation": decision.presentation,
            "source_manifest_sha256": decision.source_manifest_sha256,
            "anchor_ids": decision.anchor_ids,
            "source_decision_id": decision.decision_id,
            "source_decision_sha256": decision.decision_sha256,
            "reviewer": decision.reviewer,
            "reason": decision.reason,
            "input_disposition": decision.disposition,
            "effective_disposition": effective_disposition(decision.disposition, browser_demo=browser_demo),
            "source_text_confirmed": decision.source_text_confirmed,
            "anchor_confirmed": decision.anchor_confirmed,
            "source_page_confirmed": decision.source_page_confirmed,
            "image_hash_confirmed": decision.image_hash_confirmed,
            "typed_contract_confirmed": decision.typed_contract_confirmed,
            "typed_contract_complete": decision.typed_contract is not None,
            "browser_demo": browser_demo,
            "admission_basis": derive_admission_basis(decision.disposition, browser_demo=browser_demo),
        }
    return hash_model_r8x(DecisionAudit, payload, "audit_sha256")
