"""驱动侧转换器入口（T4b 权威签名定稿件）：检查点①放行集 → r810 reviewed typed-evidence fixture。

签名与硬约束由 T4b 驱动方定稿（2026-08-13 对接件）：内存对象进、对象出——驱动包办
全部磁盘 I/O，本模块**纯装配零读写零外呼**；返回对象须原样通过
``compilers.step3_card.compile_reviewed_fixture``；r810 谱系四哈希
（r89_admission_bundle / authorization_manifest / exported_r84_manifest / r84_fixture）
一律在授权链内部推导，驱动不得传入任意哈希；法定哈希只走 admission_legacy_hash
既有实现（铁律 bc-b8b9aca6）；失败一律 ``ValueError``（fail-closed）。

链条（authorization.py 装配半程的入口化）：released 决策逐条过内核三方绑定校验 →
r84 候选/fixture（released 序）→ 显式人工授权（``ExplicitProductionAuthorization``，
必须出自人工输入，驱动不得代造）→ 授权决策/清单 → export_r84 → 严格摄取重建 →
``ReviewedTypedEvidenceFixture``。冻结槽位引擎期语义（字段名随 schema 保真）：
admission bundle ← released 序 r84 fixture 封印；frozen input audit ← 队列项封印集；
source page sidecar ← 放行决策页绑定集；queue ← 调用方链哈希；r86 freeze ← released
决策封印集。
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from pydantic import Field

from ...knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture
from ...knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from ...materials.schema import StrictModel
from .authorization import (
    ProductionAuthorizationManifest,
    build_authorization_decision,
    build_reviewed_typed_evidence_fixture,
    export_r84_manifest,
    strict_ingest_exported_manifest,
)
from .decisions import HumanDecision, validate_human_decision
from .fixtures import build_reviewed_candidate, build_reviewed_fixture

__all__ = (
    "EXPLICIT_AUTHORIZATION_VERSION",
    "ExplicitProductionAuthorization",
    "export_reviewed_typed_evidence_fixture",
)

EXPLICIT_AUTHORIZATION_VERSION = "examcard_explicit_production_authorization_v1"


class ExplicitProductionAuthorization(StrictModel):
    """显式人工生产授权输入（最小面）：reviewer/依据/时间必须出自人工，驱动不得代造。

    ``identity_period`` 为本期身份寻址标签（decision:r810:human:<period>-… 等派生），
    缺省 ``x1``；授权内容原样进入授权决策/清单（r8x 封印随链留痕、可审计）。
    """

    schema_version: Literal["examcard_explicit_production_authorization_v1"] = EXPLICIT_AUTHORIZATION_VERSION
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    recorded_at: str = Field(min_length=1)
    identity_period: str = Field(default="x1", pattern=r"^[a-z0-9][a-z0-9._-]*$")


class _CandidateRef:
    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id


def _queue_item_dump(queue_item: Any) -> dict[str, Any]:
    dump = getattr(queue_item, "model_dump", None)
    if not callable(dump):
        raise ValueError("queue items must be pydantic-model-like (AdmissionQueueItem duck face)")
    return dump(mode="json")


def export_reviewed_typed_evidence_fixture(
    *,
    released: Sequence[HumanDecision],
    queue_items_by_candidate: Mapping[str, Any],
    queue_sha256: str,
    authorization: ExplicitProductionAuthorization,
) -> ReviewedTypedEvidenceFixture:
    """检查点①放行集 → r810 reviewed typed-evidence fixture（T4b 驱动权威入口）。

    - ``released``：全部 ``disposition=="reviewed_candidate"`` 且携完整 typed_contract
      的 r84 权威决策；候选序 = released 序（后续摄取与 fixture 封印随之）；
    - ``queue_items_by_candidate``：candidate_id → 冻结队列项（页/锚绑定源）；
    - ``queue_sha256``：链绑定，逐决策校验，越界即拒；
    - 返回对象直接可喂 ``compile_reviewed_fixture(fixture, source_fixture_sha256=…)``
      （offline-only 不变量由 r810 模型 Literal 结构保证）。
    """
    if not released:
        raise ValueError("released set is empty: nothing to export")
    authorization = ExplicitProductionAuthorization.model_validate(authorization)
    candidates = []
    ordered_queue_items = []
    decision_by_candidate: dict[str, HumanDecision] = {}
    for decision in released:
        if decision.disposition != "reviewed_candidate":
            raise ValueError(f"released decision is not a reviewed candidate: {decision.decision_id}")
        if decision.typed_contract is None or decision.typed_contract_sha256 is None:
            raise ValueError(f"released decision lacks a complete typed contract: {decision.decision_id}")
        if decision.queue_sha256 != queue_sha256:
            raise ValueError(f"released decision crossed the queue hash chain: {decision.decision_id}")
        if decision.candidate_id in decision_by_candidate:
            raise ValueError(f"released set binds one candidate twice: {decision.candidate_id}")
        queue_item = queue_items_by_candidate.get(decision.candidate_id)
        if queue_item is None:
            raise ValueError(f"released decision has no frozen queue item: {decision.candidate_id}")
        validate_human_decision(decision, queue_item, _CandidateRef(decision.candidate_id))
        candidates.append(build_reviewed_candidate(queue_item, decision.candidate_id, decision))
        ordered_queue_items.append(queue_item)
        decision_by_candidate[decision.candidate_id] = decision
    admission_fixture = build_reviewed_fixture(queue_sha256, candidates)

    period = authorization.identity_period
    authorization_decisions = []
    for candidate in candidates:
        source_decision = decision_by_candidate[candidate.candidate_id]
        suffix = canonical_hash(f"{candidate.candidate_sha256}|{queue_sha256}")[:24]
        authorization_decisions.append(
            build_authorization_decision(
                candidate,
                production_decision_id=f"decision:r810:human:{period}-{suffix}",
                admission_candidate_id=f"admission:r89:production:{period}-{suffix}",
                admission_candidate_sha256=candidate.candidate_sha256,
                source_decision_sha256=source_decision.decision_sha256,
                queue_sha256=queue_sha256,
                reviewer=authorization.reviewer,
                reason=authorization.reason,
                recorded_at=authorization.recorded_at,
            )
        )
    manifest_payload = {
        "manifest_id": f"manifest:r810:human:{period}-{canonical_hash(queue_sha256)[:24]}",
        "r89_frozen_input_audit_sha256": canonical_hash([_queue_item_dump(item) for item in ordered_queue_items]),
        "r89_admission_bundle_sha256": admission_fixture.fixture_sha256,
        "r89_source_page_sidecar_sha256": canonical_hash(
            [page.model_dump(mode="json") for decision in released for page in decision.pages]
        ),
        "r89_queue_sha256": queue_sha256,
        "r86_freeze_sha256": canonical_hash([decision.decision_sha256 for decision in released]),
        "decisions": tuple(authorization_decisions),
        "decision_count": len(authorization_decisions),
        "reviewed_candidate_count": len(authorization_decisions),
        "production_fixture_authorized": True,
        "cards_created": False,
        "step3_authorized": False,
        "step4_authorized": False,
    }
    authorization_manifest = hash_model_r8x(ProductionAuthorizationManifest, manifest_payload, "manifest_sha256")
    exported, _receipt = export_r84_manifest(authorization_manifest)  # type: ignore[arg-type]
    ingested = strict_ingest_exported_manifest(exported, ordered_queue_items)
    return build_reviewed_typed_evidence_fixture(
        admission_bundle_sha256=admission_fixture.fixture_sha256,
        authorization_manifest_sha256=authorization_manifest.manifest_sha256,
        exported_manifest_sha256=exported.manifest_sha256,
        ingested_fixture=ingested,
    )
