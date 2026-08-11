"""compilers/gates-release 测试用合成 fixture 构建器（全离线、确定性）。

按 admission_fixtures 各模型的自哈希配方逐层构造合法实例：
r84 家族（r8x 变体 canonical_hash，排除自身哈希字段）、r10 家族
（canonical_hash_r10x，排除 schema_version+哈希字段）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from examcard.knowledge.contracts.admission_fixtures import (
    ConstraintContract,
    ConstraintToken,
    DiagramContract,
    DiagramEdge,
    DiagramNode,
    FormulaContract,
    FormulaToken,
    OrderedListContract,
    OrderedListItem,
    ReviewedCandidate,
    ReviewedStep3Card,
    ReviewedStep3Fixture,
    ReviewedTypedEvidenceFixture,
    SourcePageBinding,
    Step3HumanDecision,
    TableCell,
    TableContract,
    TypedElementProvenance,
)
from examcard.knowledge.contracts.admission_legacy_hash import (
    canonical_hash,
    canonical_hash_r10x,
    text_hash,
)

PRESENTATIONS = ("diagram", "table", "ordered_list", "constraint", "formula")


def _hex(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _hex24(seed: str) -> str:
    return _hex(seed)[:24]


def _self_hashed(model_type: type, payload: dict[str, Any], field: str):
    draft = model_type.model_construct(**{**payload, field: "0" * 64})
    payload = dict(payload)
    payload[field] = canonical_hash(draft.model_dump(mode="json", exclude={field}))
    return model_type.model_validate(payload)


def _provenance(presentation: str) -> TypedElementProvenance:
    payload = {
        "subject_id": "subject:test",
        "record_id": f"record:test:{presentation}",
        "fact_id": f"fact:test:{presentation}",
        "evidence_id": f"evidence:test:{presentation}",
        "anchor_ids": (f"anchor:test:{presentation}:1",),
        "page_ref_id": f"page:test:{presentation}",
        "page": 1,
        "image_sha256": _hex(f"image:{presentation}"),
        "source_manifest_sha256": _hex(f"manifest:{presentation}"),
    }
    return _self_hashed(TypedElementProvenance, payload, "provenance_sha256")


def _element_payload(presentation: str, index: int, text: str) -> dict[str, Any]:
    return {
        "element_id": f"element:test:{presentation}:{index}",
        "text": text,
        "provenance": _provenance(presentation),
        "text_sha256": text_hash(text),
    }


def _element(model_type: type, presentation: str, index: int, text: str, **extra: Any):
    payload = {**_element_payload(presentation, index, text), **extra}
    return _self_hashed(model_type, payload, "element_sha256")


def _contract(model_type: type, payload: dict[str, Any]):
    draft = model_type.model_construct(**{**payload, "contract_sha256": "0" * 64})
    payload = dict(payload)
    payload["contract_sha256"] = canonical_hash(draft.model_dump(mode="json", exclude={"contract_sha256"}))
    return model_type.model_validate(payload)


def make_typed_contract(presentation: str):
    if presentation == "table":
        cells = (
            _element(TableCell, presentation, 1, "表头甲", row=1, column=1, row_span=1, column_span=1, reading_order=1),
            _element(TableCell, presentation, 2, "表头乙", row=1, column=2, row_span=1, column_span=1, reading_order=2),
        )
        return _contract(TableContract, {"rows": 1, "columns": 2, "cells": cells})
    if presentation == "ordered_list":
        items = (
            _element(OrderedListItem, presentation, 1, "第一项", order=1, marker="①"),
            _element(OrderedListItem, presentation, 2, "第二项", order=2, marker="②"),
        )
        return _contract(OrderedListContract, {"items": items})
    if presentation == "constraint":
        tokens = (
            _element(ConstraintToken, presentation, 1, "不小于", token_order=1),
            _element(ConstraintToken, presentation, 2, "300mm", token_order=2),
        )
        return _contract(ConstraintContract, {"tokens": tokens})
    if presentation == "formula":
        tokens = (
            _element(FormulaToken, presentation, 1, "Q=", token_order=1),
            _element(FormulaToken, presentation, 2, "V×A", token_order=2),
        )
        return _contract(FormulaContract, {"tokens": tokens})
    if presentation == "diagram":
        nodes = (
            _element(DiagramNode, presentation, 1, "开工准备", reading_order=1),
            _element(DiagramNode, presentation, 2, "验收交付", reading_order=2),
        )
        edge_payload = {
            "edge_id": f"edge:test:{presentation}:1",
            "source_node_id": nodes[0].element_id,
            "target_node_id": nodes[1].element_id,
            "provenance": _provenance(presentation),
        }
        edge = _self_hashed(DiagramEdge, edge_payload, "edge_sha256")
        return _contract(DiagramContract, {"nodes": nodes, "edges": (edge,)})
    raise ValueError(presentation)


def _page(presentation: str) -> SourcePageBinding:
    payload = {
        "page_ref_id": f"page:test:{presentation}",
        "page": 1,
        "source_file": f"sources/test-{presentation}.pdf",
        "image_path": f"pages/test-{presentation}-p1.png",
        "image_sha256": _hex(f"image:{presentation}"),
        "image_width": 1024,
        "image_height": 1536,
        "page_sha256": _hex(f"page:{presentation}"),
        "source_manifest_sha256": _hex(f"manifest:{presentation}"),
    }
    return _self_hashed(SourcePageBinding, payload, "page_binding_sha256")


def make_reviewed_candidate(presentation: str) -> ReviewedCandidate:
    contract = make_typed_contract(presentation)
    payload = {
        "candidate_id": f"candidate:test:{presentation}",
        "queue_item_id": f"queue:test:{presentation}",
        "subject_id": "subject:test",
        "record_id": f"record:test:{presentation}",
        "fact_id": f"fact:test:{presentation}",
        "evidence_id": f"evidence:test:{presentation}",
        "presentation": presentation,
        "source_manifest_sha256": _hex(f"manifest:{presentation}"),
        "anchor_ids": (f"anchor:test:{presentation}:1",),
        "pages": (_page(presentation),),
        "typed_contract": contract,
        "typed_contract_sha256": canonical_hash(contract.model_dump(mode="json")),
        "source_decision_id": f"decision:test:{presentation}",
        "reviewer": "synthetic-reviewer",
    }
    return _self_hashed(ReviewedCandidate, payload, "candidate_sha256")


def make_reviewed_typed_evidence_fixture() -> ReviewedTypedEvidenceFixture:
    candidates = tuple(make_reviewed_candidate(presentation) for presentation in PRESENTATIONS)
    payload = {
        "r89_admission_bundle_sha256": _hex("r89-bundle"),
        "authorization_manifest_sha256": _hex("authorization-manifest"),
        "exported_r84_manifest_sha256": _hex("exported-r84"),
        "r84_fixture_sha256": _hex("r84-fixture"),
        "candidates": candidates,
        "reviewed_candidate_count": len(candidates),
    }
    return _self_hashed(ReviewedTypedEvidenceFixture, payload, "fixture_sha256")


def make_reviewed_step3_fixture(release_cards, *, source_fixture_sha256: str, release_sha256: str) -> ReviewedStep3Fixture:
    """由 closure 的 DeterministicDraftCard 序列构造 reviewed Step3 fixture（r10 家族配方）。"""
    reviewed_cards = []
    for index, draft_card in enumerate(release_cards, start=1):
        contract = draft_card.contract
        provenance = tuple(item.provenance for item in contract.display_elements)
        decision_payload = {
            "decision_id": f"step3-human-decision:{_hex24(f'decision:{index}')}",
            "queue_item_id": f"step3-review-queue:{_hex24(f'queue:{index}')}",
            "queue_sha256": _hex("queue"),
            "contract_id": contract.contract_id,
            "source_candidate_id": contract.source_candidate_id,
            "fact_ids": contract.fact_ids,
            "evidence_ids": tuple(sorted({item.evidence_id for item in provenance})),
            "anchor_ids": tuple(sorted({anchor for item in provenance for anchor in item.anchor_ids})),
            "source_texts": tuple(item.text for item in contract.display_elements),
            "typed_contract_sha256": contract.source_typed_contract_sha256,
            "provenance_sha256": canonical_hash_r10x(provenance),
            "presentation": contract.presentation,
            "geometry_sha256": canonical_hash_r10x(contract.geometry),
            "disposition": "reviewed_candidate",
            "reviewer": "synthetic-reviewer",
            "reason": "synthetic reviewed decision",
            "source_text_confirmed": True,
            "fact_card_confirmed": True,
            "evidence_anchor_confirmed": True,
            "typed_contract_confirmed": True,
            "provenance_confirmed": True,
            "presentation_confirmed": True,
            "geometry_confirmed": True,
            "explicit_human_authorization": True,
        }
        decision_draft = Step3HumanDecision.model_construct(**{**decision_payload, "decision_sha256": "0" * 64})
        decision_payload["decision_sha256"] = canonical_hash_r10x(decision_draft.model_dump(mode="json", exclude={"schema_version", "decision_sha256"}))
        decision = Step3HumanDecision.model_validate(decision_payload)
        card_payload = {
            "queue_item_id": decision.queue_item_id,
            "contract": contract,
            "artifact": draft_card,
            "decision": decision,
        }
        card_draft = ReviewedStep3Card.model_construct(**{**card_payload, "reviewed_card_sha256": "0" * 64})
        card_payload["reviewed_card_sha256"] = canonical_hash_r10x(card_draft.model_dump(mode="json", exclude={"schema_version", "reviewed_card_sha256"}))
        reviewed_cards.append(ReviewedStep3Card.model_validate(card_payload))
    fixture_payload = {
        "source_fixture_sha256": source_fixture_sha256,
        "deterministic_release_sha256": release_sha256,
        "decision_manifest_sha256": _hex("decision-manifest"),
        "cards": tuple(reviewed_cards),
        "reviewed_card_count": len(reviewed_cards),
        "fixture_authorized": bool(reviewed_cards),
    }
    fixture_draft = ReviewedStep3Fixture.model_construct(**{**fixture_payload, "fixture_sha256": "0" * 64})
    fixture_payload["fixture_sha256"] = canonical_hash_r10x(fixture_draft.model_dump(mode="json", exclude={"schema_version", "fixture_sha256"}))
    return ReviewedStep3Fixture.model_validate(fixture_payload)
