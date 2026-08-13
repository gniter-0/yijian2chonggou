"""gates/review 内核测试装置：可复用的 r84 家族最小合法对象构建器。

全部封印经 admission_legacy_hash 的 r8x 构建器（与生产语料同谱系）；
标识符满足 ID_PATTERN（小写），哈希用确定性占位值便于断言。
"""
from __future__ import annotations

from typing import Any, Literal

from examcard.gates.review.decisions import HumanDecision, HumanDecisionManifest
from examcard.gates.review.queue import ReviewQueueItemBase
from examcard.knowledge.contracts.admission_fixtures import (
    DiagramContract,
    DiagramEdge,
    DiagramNode,
    FormulaContract,
    FormulaToken,
    SourcePageBinding,
    TypedElementProvenance,
)
from examcard.knowledge.contracts.admission_legacy_hash import (
    canonical_hash,
    hash_model_r8x,
    text_hash,
)

SUBJECT_ID = "subject.gonglu"
RECORD_ID = "record:test:0001"
FACT_ID = "fact:test:0001"
EVIDENCE_ID = "evidence:test:0001"
ANCHOR_IDS = ("anchor:test:0001",)
PAGE_REF_ID = "page:test:0001"
SOURCE_MANIFEST_SHA256 = "a" * 64
IMAGE_SHA256 = "b" * 64
PAGE_SHA256 = "c" * 64
QUEUE_ITEM_ID = "queue:test:0001"
CANDIDATE_ID = "candidate:test:0001"


class KernelQueueItem(ReviewQueueItemBase):
    """测试用队列项：基类核心 + 测试期 schema_version。"""

    schema_version: Literal["examcard_test_queue_item_v1"] = "examcard_test_queue_item_v1"


class KernelCandidate:
    def __init__(self, candidate_id: str = CANDIDATE_ID) -> None:
        self.candidate_id = candidate_id


def make_source_page(**overrides: Any) -> SourcePageBinding:
    payload: dict[str, Any] = {
        "page_ref_id": PAGE_REF_ID,
        "page": 1,
        "source_file": "input_pdf/测试.pdf",
        "image_path": "output_images/测试/page_0001.png",
        "image_sha256": IMAGE_SHA256,
        "image_width": 1200,
        "image_height": 1700,
        "page_sha256": PAGE_SHA256,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
    }
    payload.update(overrides)
    return hash_model_r8x(SourcePageBinding, payload, "page_binding_sha256")  # type: ignore[return-value]


def make_provenance(**overrides: Any) -> TypedElementProvenance:
    payload: dict[str, Any] = {
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "anchor_ids": ANCHOR_IDS,
        "page_ref_id": PAGE_REF_ID,
        "page": 1,
        "image_sha256": IMAGE_SHA256,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
    }
    payload.update(overrides)
    return hash_model_r8x(TypedElementProvenance, payload, "provenance_sha256")  # type: ignore[return-value]


def make_formula_contract(provenance: TypedElementProvenance | None = None, text: str = "E=mc^2") -> FormulaContract:
    bound = provenance or make_provenance()
    token = hash_model_r8x(
        FormulaToken,
        {
            "element_id": "element:test:0001",
            "text": text,
            "provenance": bound,
            "text_sha256": text_hash(text),
            "token_order": 1,
        },
        "element_sha256",
    )
    return hash_model_r8x(FormulaContract, {"tokens": (token,)}, "contract_sha256")  # type: ignore[return-value]


def make_diagram_contract(
    *,
    node_texts: tuple[str, ...] = ("项目经理", "技术负责人"),
    edges: tuple[tuple[int, int], ...] = ((1, 2),),
    node_provenance: TypedElementProvenance | None = None,
    edge_provenance: TypedElementProvenance | None = None,
) -> DiagramContract:
    """最小合法带边 diagram 契约（P4b）；node/edge provenance 可分别注入（越界钉扎用）。"""
    bound_nodes = node_provenance or make_provenance()
    bound_edges = edge_provenance or make_provenance()
    nodes = tuple(
        hash_model_r8x(
            DiagramNode,
            {
                "element_id": f"element:test:node-{order:04d}",
                "text": text,
                "provenance": bound_nodes,
                "text_sha256": text_hash(text),
                "reading_order": order,
            },
            "element_sha256",
        )
        for order, text in enumerate(node_texts, start=1)
    )
    diagram_edges = tuple(
        hash_model_r8x(
            DiagramEdge,
            {
                "edge_id": f"edge:test:{index:04d}",
                "source_node_id": nodes[source - 1].element_id,
                "target_node_id": nodes[target - 1].element_id,
                "provenance": bound_edges,
            },
            "edge_sha256",
        )
        for index, (source, target) in enumerate(edges, start=1)
    )
    return hash_model_r8x(DiagramContract, {"nodes": nodes, "edges": diagram_edges}, "contract_sha256")  # type: ignore[return-value]


def make_queue_item(**overrides: Any) -> KernelQueueItem:
    payload: dict[str, Any] = {
        "queue_item_id": QUEUE_ITEM_ID,
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": "formula",
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "anchor_ids": ANCHOR_IDS,
        "pages": (make_source_page(),),
    }
    payload.update(overrides)
    return KernelQueueItem(**payload)


def make_human_decision(
    queue_item: KernelQueueItem | None = None,
    *,
    disposition: str = "reviewed_candidate",
    candidate_id: str = CANDIDATE_ID,
    queue_sha256: str = "d" * 64,
    contract: FormulaContract | DiagramContract | None = None,
    **overrides: Any,
) -> HumanDecision:
    item = queue_item or make_queue_item()
    reviewed = disposition == "reviewed_candidate"
    typed_contract = contract if contract is not None else (make_formula_contract() if reviewed else None)
    payload: dict[str, Any] = {
        "decision_id": "decision:r84:human:test-0001",
        "candidate_id": candidate_id,
        "queue_item_id": item.queue_item_id,
        "queue_sha256": queue_sha256,
        "subject_id": item.subject_id,
        "record_id": item.record_id,
        "fact_id": item.fact_id,
        "evidence_id": item.evidence_id,
        "presentation": item.presentation,
        "source_manifest_sha256": item.source_manifest_sha256,
        "anchor_ids": item.anchor_ids,
        "pages": item.pages,
        "disposition": disposition,
        "reviewer": "tester",
        "reason": "explicit human source-page review",
        "source_text_confirmed": reviewed,
        "anchor_confirmed": reviewed,
        "source_page_confirmed": reviewed,
        "image_hash_confirmed": reviewed,
        "typed_contract_confirmed": reviewed,
        "typed_contract": typed_contract,
        "typed_contract_sha256": canonical_hash(typed_contract.model_dump(mode="json")) if typed_contract is not None else None,
        "recorded_at": "2026-08-12T00:00:00Z",
        "browser_demo": False,
        "production_fixture_authorized": reviewed,
    }
    payload.update(overrides)
    return hash_model_r8x(HumanDecision, payload, "decision_sha256")  # type: ignore[return-value]


def make_manifest(decisions: tuple[HumanDecision, ...], queue_sha256: str = "d" * 64) -> HumanDecisionManifest:
    return hash_model_r8x(  # type: ignore[return-value]
        HumanDecisionManifest,
        {
            "manifest_id": "manifest:r84:human:test-0001",
            "queue_sha256": queue_sha256,
            "decisions": decisions,
            "decision_count": len(decisions),
        },
        "manifest_sha256",
    )
