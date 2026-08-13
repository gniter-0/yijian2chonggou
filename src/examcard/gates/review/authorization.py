"""显式生产授权层（规范源 r810，次要参考 r104/r86）：授权决策 → 导出 r84 权威清单 → 严格摄取。

状态机边：显式授权（ProductionAuthorizationDecision，全确认 Literal 恒真）→
export_r84_manifest 派生 r84 HumanDecisionManifest（新鲜身份，回执钉扎双向哈希）→
r84 严格摄取产 reviewed fixture；非 reviewed 处置经 DispositionProjection 原样守恒
（automatic_upgrade 恒 False）。生产语料 schema_version 保真（phase2r810_*）。

装配半程（P3 方案 §8.2 v1.3 补搬，蒸馏自旧仓 phase2r810 build_batch）：模型与
export 之外，本模块补齐旧仓从未随迁的**构造调用**——authorize_extraction_batch
把提取面产出（extraction.ExtractionBatch）经「授权决策构造 → r84 导出 → 严格摄取
重建（validate_human_decision + build_reviewed_candidate/fixture）→
ReviewedTypedEvidenceFixture（r810 形态）构造」走完，新考试 step3 正路
（自建 typed-evidence 链 → v2 单卡回显）自此闭环。冻结槽位语义（r89_*/r86_*
字段名随 schema 保真，引擎期以提取面工件绑定）：admission bundle ← 提取期 r84
fixture 封印；frozen input audit ← 源投影封印集；source page sidecar ← 源页绑定
集；queue ← 提取期队列链哈希；r86 freeze ← 提取期人工决策清单封印。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

from pydantic import Field, model_validator

from ...knowledge.contracts.admission_fixtures import (
    HASH_PATTERN,
    ID_PATTERN,
    DiagramContract,
    ReviewedCandidate,
    ReviewedTypedEvidenceFixture,
    SourcePageBinding,
    TypedContract,
)
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...materials.schema import StrictModel
from .contracts import contract_elements
from .decisions import (
    HUMAN_DECISION_VERSION,
    HumanDecision,
    HumanDecisionManifest,
    validate_human_decision,
)
from .fixtures import ReviewedFixture, build_reviewed_candidate, build_reviewed_fixture

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


# ---------------------------------------------------------------------------
# 装配半程（§8.2 v1.3 补搬）：授权决策/清单构造 → 严格摄取重建 → r810 fixture 构造
# ---------------------------------------------------------------------------


def source_text_snapshot(contract: Any) -> tuple[str, ...]:
    """r810 `_source_texts` 定式：契约元素按存储序的原文快照（授权决策必带）。

    diagram 随旧仓 nodes-only 语义（r89 L574-581 / r87 L719-728 / r810 L756 委托 r89）：
    布局边无原文，快照只出节点文本。存储序取节点与旧仓「按 reading_order 排序」严格
    等价——DiagramContract 校验器钉死节点存储序 == reading_order 连续 1..N
    （admission_fixtures.py L224）。
    """
    if isinstance(contract, DiagramContract):
        return tuple(node.text for node in contract.nodes)
    return tuple(element.text for element in contract_elements(contract))


def build_authorization_decision(
    candidate: ReviewedCandidate,
    *,
    production_decision_id: str,
    admission_candidate_id: str,
    admission_candidate_sha256: str,
    source_decision_sha256: str,
    queue_sha256: str,
    reviewer: str,
    reason: str,
    recorded_at: str,
) -> ProductionAuthorizationDecision:
    """r810 `_make_authorization_decision` 蒸馏：逐字段绑定被授权候选（跨源即拒）。"""
    payload = {
        "production_decision_id": production_decision_id,
        "r89_admission_candidate_id": admission_candidate_id,
        "r89_candidate_sha256": admission_candidate_sha256,
        "source_decision_id": candidate.source_decision_id,
        "source_decision_sha256": source_decision_sha256,
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
        "source_text_snapshot": source_text_snapshot(candidate.typed_contract),
        "disposition": "reviewed_candidate",
        "reviewer": reviewer,
        "reason": reason,
        "source_text_confirmed": True,
        "anchor_confirmed": True,
        "source_page_confirmed": True,
        "image_hash_confirmed": True,
        "typed_contract_confirmed": True,
        "typed_contract": candidate.typed_contract,
        "typed_contract_sha256": candidate.typed_contract_sha256,
        "recorded_at": recorded_at,
        "browser_demo": False,
        "production_fixture_authorized": True,
        "cards_created": False,
        "step3_authorized": False,
        "step4_authorized": False,
    }
    return hash_model_r8x(ProductionAuthorizationDecision, payload, "decision_sha256")  # type: ignore[return-value]


def strict_ingest_exported_manifest(
    exported: HumanDecisionManifest,
    queue_items: Sequence[Any],
) -> ReviewedFixture:
    """严格摄取重建（r810 `_ingest_r84` 的引擎形态）：导出清单 → r84 reviewed fixture。

    每份导出决策经内核三方绑定校验（`validate_human_decision`，含契约逐元素
    provenance/噪声旗标走查），再经 `build_reviewed_candidate` 重建候选；候选
    ``source_decision_id`` 因此绑定**导出决策**身份（r810 语料同构）。

    候选序 = ``queue_items`` 的**队列序**（r84 定式：摄取按评审队列迭代，不按
    清单决策序——r810 生产语料实证两序不同，fixture 封印随队列序）。无决策的
    队列项守恒跳过（留在队列）；决策绑定不存在的队列项则整批拒绝。
    """

    class _CandidateRef:
        def __init__(self, candidate_id: str) -> None:
            self.candidate_id = candidate_id

    decisions_by_queue_item: dict[str, HumanDecision] = {}
    for decision in exported.decisions:
        if decision.queue_item_id in decisions_by_queue_item:
            raise ValueError(f"exported manifest binds one queue item twice: {decision.queue_item_id}")
        decisions_by_queue_item[decision.queue_item_id] = decision
    candidates = []
    ingested_queue_items: set[str] = set()
    for queue_item in queue_items:
        decision = decisions_by_queue_item.get(queue_item.queue_item_id)
        if decision is None:
            continue
        validate_human_decision(decision, queue_item, _CandidateRef(decision.candidate_id))
        candidates.append(build_reviewed_candidate(queue_item, decision.candidate_id, decision))
        ingested_queue_items.add(queue_item.queue_item_id)
    missing = set(decisions_by_queue_item) - ingested_queue_items
    if missing:
        raise ValueError(f"exported decisions reference unknown queue items: {sorted(missing)}")
    return build_reviewed_fixture(exported.queue_sha256, candidates)


def build_reviewed_typed_evidence_fixture(
    *,
    admission_bundle_sha256: str,
    authorization_manifest_sha256: str,
    exported_manifest_sha256: str,
    ingested_fixture: ReviewedFixture,
) -> ReviewedTypedEvidenceFixture:
    """r810 fixture 构造调用（旧仓 build_batch 内联形态的蒸馏；全仓此前无此调用）。"""
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
    return hash_model_r8x(ReviewedTypedEvidenceFixture, payload, "fixture_sha256")  # type: ignore[return-value]


@dataclass(frozen=True)
class AuthorizedFixtureBundle:
    """authorize_extraction_batch 的装配结果（驱动侧负责持久化与审计登记）。"""

    authorization: ProductionAuthorizationManifest
    exported: HumanDecisionManifest
    receipt: R84ExportReceipt
    ingested_fixture: ReviewedFixture
    fixture: ReviewedTypedEvidenceFixture


def authorize_extraction_batch(
    batch: Any,
    *,
    identity_period: str,
    reviewer: str,
    reason: str,
    recorded_at: str,
) -> AuthorizedFixtureBundle:
    """提取批 → 显式生产授权 → r810 ReviewedTypedEvidenceFixture（新考试 step3 正路闭环）。

    ``batch`` 为 extraction.ExtractionBatch（鸭子类型：sources / queue_items /
    queue_sha256 / manifest / fixture）。冻结槽位以提取面工件绑定（见模块 docstring）；
    授权身份按 ``identity_period`` 内容寻址派生（decision:r810:human:/
    admission:r89:production:/manifest:r810:human: 前缀随 schema 保真）。
    """
    from .extraction import _require_identity_period  # 延迟导入避免环

    _require_identity_period(identity_period)
    if not batch.fixture.candidates:
        raise ValueError("extraction batch has no reviewed candidates to authorize")
    decision_hashes = {item.decision_id: item.decision_sha256 for item in batch.manifest.decisions}
    known_queue_items = {item.queue_item_id for item in batch.queue_items}
    decisions = []
    for candidate in sorted(batch.fixture.candidates, key=lambda item: item.candidate_id):
        source_decision_sha256 = decision_hashes.get(candidate.source_decision_id)
        if source_decision_sha256 is None:
            raise ValueError(f"candidate is not bound to a manifest decision: {candidate.candidate_id}")
        if candidate.queue_item_id not in known_queue_items:
            raise ValueError(f"candidate references an unknown queue item: {candidate.queue_item_id}")
        suffix = canonical_hash(f"{candidate.candidate_sha256}|{batch.queue_sha256}")[:24]
        decisions.append(
            build_authorization_decision(
                candidate,
                production_decision_id=f"decision:r810:human:{identity_period}-{suffix}",
                admission_candidate_id=f"admission:r89:production:{identity_period}-{suffix}",
                admission_candidate_sha256=candidate.candidate_sha256,
                source_decision_sha256=source_decision_sha256,
                queue_sha256=batch.queue_sha256,
                reviewer=reviewer,
                reason=reason,
                recorded_at=recorded_at,
            )
        )
    manifest_payload = {
        "manifest_id": f"manifest:r810:human:{identity_period}-{canonical_hash(batch.queue_sha256)[:24]}",
        "r89_frozen_input_audit_sha256": canonical_hash([source.source_sha256 for source in batch.sources]),
        "r89_admission_bundle_sha256": batch.fixture.fixture_sha256,
        "r89_source_page_sidecar_sha256": canonical_hash(
            [page.model_dump(mode="json") for source in batch.sources for page in source.pages]
        ),
        "r89_queue_sha256": batch.queue_sha256,
        "r86_freeze_sha256": batch.manifest.manifest_sha256,
        "decisions": tuple(decisions),
        "decision_count": len(decisions),
        "reviewed_candidate_count": len(decisions),
        "production_fixture_authorized": True,
        "cards_created": False,
        "step3_authorized": False,
        "step4_authorized": False,
    }
    authorization = hash_model_r8x(ProductionAuthorizationManifest, manifest_payload, "manifest_sha256")
    exported, receipt = export_r84_manifest(authorization)  # type: ignore[arg-type]
    ingested = strict_ingest_exported_manifest(exported, batch.queue_items)
    fixture = build_reviewed_typed_evidence_fixture(
        admission_bundle_sha256=batch.fixture.fixture_sha256,
        authorization_manifest_sha256=authorization.manifest_sha256,
        exported_manifest_sha256=exported.manifest_sha256,
        ingested_fixture=ingested,
    )
    return AuthorizedFixtureBundle(
        authorization=authorization,  # type: ignore[arg-type]
        exported=exported,
        receipt=receipt,
        ingested_fixture=ingested,
        fixture=fixture,
    )
