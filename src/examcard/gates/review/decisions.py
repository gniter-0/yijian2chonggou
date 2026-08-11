"""权威人工决策与清单：准入内核规范源 phase2r84（生产形态逐字面量保真）。

HumanDecision / HumanDecisionManifest 是「严格摄取」的唯一权威输入：
- 身份必须新鲜（decision:r84:human: / manifest:r84:human: 前缀，§4 参数化差异点 1）；
- browser_demo 恒 False（Literal 级结构不可能，浏览器界产物无法通过本模型）；
- reviewed_candidate 必须五重确认 + 完整 typed contract + 显式生产授权；
- 非 reviewed 决策不得携带授权契约；
- 自哈希绑 admission_legacy_hash r8x 谱系（铁律 bc-b8b9aca6：投影只排除封印字段本身）。

既有生产语料以 phase2r84_* schema_version 写盘（旧仓 data/ 只读），字面量不可改。
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures import (
    HASH_PATTERN,
    ID_PATTERN,
    SourcePageBinding,
    TypedContract,
)
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...materials.schema import StrictModel

HUMAN_DECISION_VERSION = "phase2r84_human_decision_v1"
HUMAN_MANIFEST_VERSION = "phase2r84_human_decision_manifest_v1"

DECISION_IDENTITY_PREFIX = "decision:r84:human:"
MANIFEST_IDENTITY_PREFIX = "manifest:r84:human:"


def require_fresh_identity(value: str, prefix: str, label: str) -> None:
    """内核参数化点：校验器按期身份前缀断言「新鲜身份」。"""
    if not value.startswith(prefix):
        raise ValueError(f"{label} must use a fresh identity with prefix {prefix!r}")


class HumanDecision(StrictModel):
    schema_version: Literal["phase2r84_human_decision_v1"] = HUMAN_DECISION_VERSION
    decision_id: str = Field(pattern=ID_PATTERN)
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
    disposition: Literal["reviewed_candidate", "manual_review", "blocked", "not_applicable"]
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    source_text_confirmed: bool = False
    anchor_confirmed: bool = False
    source_page_confirmed: bool = False
    image_hash_confirmed: bool = False
    typed_contract_confirmed: bool = False
    typed_contract: TypedContract | None = None
    typed_contract_sha256: str | None = None
    recorded_at: str = Field(min_length=1)
    browser_demo: Literal[False] = False
    production_fixture_authorized: bool = False
    decision_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_decision(self) -> "HumanDecision":
        require_fresh_identity(self.decision_id, DECISION_IDENTITY_PREFIX, "human decision")
        if any(page.source_manifest_sha256 != self.source_manifest_sha256 for page in self.pages):
            raise ValueError("human decision crossed source manifest")
        if self.typed_contract is None:
            if self.typed_contract_sha256 is not None:
                raise ValueError("typed contract hash exists without typed contract")
        elif self.typed_contract_sha256 != canonical_hash(self.typed_contract.model_dump(mode="json")):
            raise ValueError("typed contract hash mismatch")
        if self.disposition == "reviewed_candidate":
            if not all(
                (
                    self.source_text_confirmed,
                    self.anchor_confirmed,
                    self.source_page_confirmed,
                    self.image_hash_confirmed,
                    self.typed_contract_confirmed,
                    self.production_fixture_authorized,
                )
            ):
                raise ValueError("reviewed candidate requires five confirmations and fixture authorization")
            if self.typed_contract is None:
                raise ValueError("reviewed candidate requires a complete typed contract")
        elif self.typed_contract is not None or self.typed_contract_confirmed or self.production_fixture_authorized:
            raise ValueError("non-reviewed decision cannot carry an authorized typed contract")
        if self.decision_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"decision_sha256"})):
            raise ValueError("human decision hash mismatch")
        return self


class HumanDecisionManifest(StrictModel):
    schema_version: Literal["phase2r84_human_decision_manifest_v1"] = HUMAN_MANIFEST_VERSION
    manifest_id: str = Field(pattern=ID_PATTERN)
    queue_sha256: str = Field(pattern=HASH_PATTERN)
    decisions: tuple[HumanDecision, ...] = ()
    decision_count: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_manifest(self) -> "HumanDecisionManifest":
        require_fresh_identity(self.manifest_id, MANIFEST_IDENTITY_PREFIX, "human decision manifest")
        if self.decision_count != len(self.decisions):
            raise ValueError("human decision count mismatch")
        ids = [item.decision_id for item in self.decisions]
        candidates = [item.candidate_id for item in self.decisions]
        if len(ids) != len(set(ids)) or len(candidates) != len(set(candidates)):
            raise ValueError("human decision identities must be unique")
        if any(item.queue_sha256 != self.queue_sha256 for item in self.decisions):
            raise ValueError("human decision crossed queue hash")
        if self.manifest_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"manifest_sha256"})):
            raise ValueError("human decision manifest hash mismatch")
        return self


def empty_human_manifest(queue_sha256: str) -> HumanDecisionManifest:
    payload = {
        "manifest_id": f"manifest:r84:human:empty-{queue_sha256[:24]}",
        "queue_sha256": queue_sha256,
        "decisions": (),
        "decision_count": 0,
    }
    return hash_model_r8x(HumanDecisionManifest, payload, "manifest_sha256")  # type: ignore[return-value]


def load_human_manifest(path: Path | None, queue_sha256: str) -> HumanDecisionManifest:
    """严格摄取入口：无清单即空清单；有清单必须绑定当前队列哈希。"""
    if path is None:
        return empty_human_manifest(queue_sha256)
    manifest = HumanDecisionManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))
    if manifest.queue_sha256 != queue_sha256:
        raise ValueError("human decision manifest crossed queue hash")
    return manifest


def validate_human_decision(decision: HumanDecision, queue_item: object, candidate: object) -> None:
    """决策-队列项-候选三方绑定校验（r84 严格摄取语义，队列/候选侧鸭子类型化）。"""
    from .contracts import validate_typed_contract_binding

    if decision.queue_item_id != getattr(queue_item, "queue_item_id") or decision.candidate_id != getattr(candidate, "candidate_id"):
        raise ValueError("human decision crossed candidate identity")
    if (
        decision.subject_id != queue_item.subject_id
        or decision.record_id != queue_item.record_id
        or decision.fact_id != queue_item.fact_id
        or decision.evidence_id != queue_item.evidence_id
    ):
        raise ValueError("human decision crossed source identity")
    if (
        decision.presentation != queue_item.presentation
        or decision.source_manifest_sha256 != queue_item.source_manifest_sha256
        or tuple(decision.anchor_ids) != tuple(queue_item.anchor_ids)
    ):
        raise ValueError("human decision crossed source contract")
    expected_pages = {page.page_ref_id: page for page in queue_item.pages}
    actual_pages = {page.page_ref_id: page for page in decision.pages}
    if set(actual_pages) != set(expected_pages):
        raise ValueError("human decision page set is not exact")
    for page_ref, source_page in expected_pages.items():
        page = actual_pages[page_ref]
        if page.model_dump(mode="json", exclude={"schema_version", "page_binding_sha256"}) != source_page.model_dump(
            mode="json", exclude={"schema_version", "page_binding_sha256"}
        ):
            raise ValueError("human decision page binding mismatch")
    if decision.disposition == "reviewed_candidate":
        validate_typed_contract_binding(decision, queue_item)
