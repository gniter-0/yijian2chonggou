"""Focused tests for the first-class threshold admission family."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from examcard.gates.review.contracts_v2 import validate_typed_contract_binding_v2
from examcard.gates.review.decisions_v2 import (
    HumanDecisionManifestV2,
    HumanDecisionV2,
    validate_human_decision_v2,
)
from examcard.gates.review.fixtures import build_reviewed_candidate
from examcard.gates.review.fixtures_v2 import build_reviewed_candidate_v2
from examcard.gates.review.queue import AdmissionQueueItem
from examcard.gates.review.queue_v2 import AdmissionQueueItemV2
from examcard.knowledge.contracts.admission_fixtures import ReviewedCandidate, TypedContract
from examcard.knowledge.contracts.admission_fixtures_typed import (
    ConstraintContract,
    ConstraintToken,
    OrderedListContract,
    OrderedListItem,
    TableCell,
    TableContract,
    TypedElementProvenance,
)
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x, text_hash
from examcard.knowledge.contracts.admission_v2 import (
    ReviewedCandidateV2,
    ThresholdContract,
    ThresholdToken,
    TypedContractV2,
)
from tests.fixtures.admission_kernel import (
    ANCHOR_IDS,
    CANDIDATE_ID,
    EVIDENCE_ID,
    FACT_ID,
    RECORD_ID,
    SOURCE_MANIFEST_SHA256,
    SUBJECT_ID,
    make_diagram_contract,
    make_formula_contract,
    make_provenance,
    make_source_page,
)


def make_threshold_token(
    *,
    order: int = 1,
    element_id: str = "element:test:threshold-0001",
    text: str = "不小于0.25m",
    provenance: TypedElementProvenance | None = None,
) -> ThresholdToken:
    return hash_model_r8x(
        ThresholdToken,
        {
            "element_id": element_id,
            "text": text,
            "provenance": provenance or make_provenance(),
            "text_sha256": text_hash(text),
            "token_order": order,
        },
        "element_sha256",
    )


def make_threshold_contract(*tokens: ThresholdToken | None) -> ThresholdContract:
    values = tuple(token for token in tokens if token is not None) or (make_threshold_token(),)
    return hash_model_r8x(ThresholdContract, {"tokens": values}, "contract_sha256")


def make_queue_item(**overrides: Any) -> AdmissionQueueItemV2:
    payload: dict[str, Any] = {
        "candidate_id": CANDIDATE_ID,
        "queue_item_id": "queue:test:threshold-0001",
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": "threshold",
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "anchor_ids": ANCHOR_IDS,
        "pages": (make_source_page(),),
        "disposition": "manual_review",
        "decision_audit_id": "audit:test:threshold-0001",
        "reason": "threshold requires human review",
    }
    payload.update(overrides)
    return hash_model_r8x(AdmissionQueueItemV2, payload, "entry_sha256")


def make_decision(
    queue_item: AdmissionQueueItemV2 | None = None,
    *,
    contract: Any | None = None,
    disposition: str = "reviewed_candidate",
    candidate_id: str = CANDIDATE_ID,
    queue_sha256: str = "d" * 64,
    **overrides: Any,
) -> HumanDecisionV2:
    item = queue_item or make_queue_item()
    reviewed = disposition == "reviewed_candidate"
    typed_contract = contract if contract is not None else (make_threshold_contract() if reviewed else None)
    payload: dict[str, Any] = {
        "decision_id": "decision:r84:human:threshold-0001",
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
        "reason": "explicit source-page threshold review",
        "source_text_confirmed": reviewed,
        "anchor_confirmed": reviewed,
        "source_page_confirmed": reviewed,
        "image_hash_confirmed": reviewed,
        "typed_contract_confirmed": reviewed,
        "typed_contract": typed_contract,
        "typed_contract_sha256": canonical_hash(typed_contract.model_dump(mode="json")) if typed_contract else None,
        "recorded_at": "2026-08-12T00:00:00Z",
        "production_fixture_authorized": reviewed,
    }
    payload.update(overrides)
    return hash_model_r8x(HumanDecisionV2, payload, "decision_sha256")


def make_v1_queue_item() -> AdmissionQueueItem:
    return hash_model_r8x(
        AdmissionQueueItem,
        {
            "candidate_id": CANDIDATE_ID,
            "queue_item_id": "queue:test:v1-0001",
            "subject_id": SUBJECT_ID,
            "record_id": RECORD_ID,
            "fact_id": FACT_ID,
            "evidence_id": EVIDENCE_ID,
            "presentation": "formula",
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "anchor_ids": ANCHOR_IDS,
            "pages": (make_source_page(),),
            "disposition": "manual_review",
            "decision_audit_id": "audit:test:v1-0001",
            "reason": "formula requires human review",
        },
        "entry_sha256",
    )


def make_v1_decision(queue_item: AdmissionQueueItem) -> Any:
    from examcard.gates.review.decisions import HumanDecision

    contract = make_formula_contract()
    payload: dict[str, Any] = {
        "decision_id": "decision:r84:human:v1-0001",
        "candidate_id": CANDIDATE_ID,
        "queue_item_id": queue_item.queue_item_id,
        "queue_sha256": "d" * 64,
        "subject_id": queue_item.subject_id,
        "record_id": queue_item.record_id,
        "fact_id": queue_item.fact_id,
        "evidence_id": queue_item.evidence_id,
        "presentation": queue_item.presentation,
        "source_manifest_sha256": queue_item.source_manifest_sha256,
        "anchor_ids": queue_item.anchor_ids,
        "pages": queue_item.pages,
        "disposition": "reviewed_candidate",
        "reviewer": "tester",
        "reason": "explicit source-page formula review",
        "source_text_confirmed": True,
        "anchor_confirmed": True,
        "source_page_confirmed": True,
        "image_hash_confirmed": True,
        "typed_contract_confirmed": True,
        "typed_contract": contract,
        "typed_contract_sha256": canonical_hash(contract.model_dump(mode="json")),
        "recorded_at": "2026-08-12T00:00:00Z",
        "production_fixture_authorized": True,
    }
    return hash_model_r8x(HumanDecision, payload, "decision_sha256")


def make_table_contract() -> Any:
    text = "表格单元"
    cell = hash_model_r8x(
        TableCell,
        {
            "element_id": "element:test:table-0001",
            "text": text,
            "provenance": make_provenance(),
            "text_sha256": text_hash(text),
            "row": 1,
            "column": 1,
            "row_span": 1,
            "column_span": 1,
            "reading_order": 1,
        },
        "element_sha256",
    )
    return hash_model_r8x(TableContract, {"rows": 1, "columns": 1, "cells": (cell,)}, "contract_sha256")


def make_ordered_list_contract() -> Any:
    text = "第一项"
    item = hash_model_r8x(
        OrderedListItem,
        {
            "element_id": "element:test:list-0001",
            "text": text,
            "provenance": make_provenance(),
            "text_sha256": text_hash(text),
            "order": 1,
        },
        "element_sha256",
    )
    return hash_model_r8x(OrderedListContract, {"items": (item,)}, "contract_sha256")


def make_constraint_contract() -> ConstraintContract:
    text = "必须满足约束"
    token = hash_model_r8x(
        ConstraintToken,
        {
            "element_id": "element:test:constraint-0001",
            "text": text,
            "provenance": make_provenance(),
            "text_sha256": text_hash(text),
            "token_order": 1,
        },
        "element_sha256",
    )
    return hash_model_r8x(ConstraintContract, {"tokens": (token,)}, "contract_sha256")


def test_threshold_token_preserves_exact_source_text_and_role() -> None:
    token = make_threshold_token(text="③地基为冻胀土层时，基底高程应在最大冻结深度以下不小于0.25m。")
    assert token.text == "③地基为冻胀土层时，基底高程应在最大冻结深度以下不小于0.25m。"
    assert token.source_role == "threshold_token"
    assert token.token_order == 1
    assert type(token).__mro__[1].__name__ == "TypedTextBase"


def test_threshold_token_rejects_bad_text_hash() -> None:
    with pytest.raises(ValidationError, match="source text hash mismatch"):
        ThresholdToken(
            element_id="element:test:threshold-0001",
            text="不小于0.25m",
            provenance=make_provenance(),
            text_sha256="0" * 64,
            element_sha256="0" * 64,
            token_order=1,
        )


def test_threshold_token_rejects_bad_provenance_hash() -> None:
    with pytest.raises(ValidationError, match="provenance hash mismatch"):
        TypedElementProvenance.model_validate(
            {**make_provenance().model_dump(mode="json"), "provenance_sha256": "0" * 64}
        )


def test_threshold_token_rejects_bad_source_role() -> None:
    with pytest.raises(ValidationError, match="threshold_token"):
        ThresholdToken.model_validate({**make_threshold_token().model_dump(mode="json"), "source_role": "constraint_token"})


def test_threshold_contract_accepts_contiguous_order_and_unique_elements() -> None:
    contract = make_threshold_contract(
        make_threshold_token(order=1, element_id="element:test:threshold-0001"),
        make_threshold_token(order=2, element_id="element:test:threshold-0002"),
    )
    assert contract.presentation == "threshold"
    assert [item.token_order for item in contract.tokens] == [1, 2]


@pytest.mark.parametrize("orders", [(1, 3), (2, 1)])
def test_threshold_contract_rejects_gapped_or_non_contiguous_order(orders: tuple[int, int]) -> None:
    with pytest.raises(ValidationError, match="token order must be contiguous"):
        make_threshold_contract(
            make_threshold_token(order=orders[0], element_id="element:test:threshold-0001"),
            make_threshold_token(order=orders[1], element_id="element:test:threshold-0002"),
        )


def test_threshold_contract_rejects_duplicate_elements() -> None:
    first = make_threshold_token(order=1, element_id="element:test:threshold-0001")
    second = make_threshold_token(order=2, element_id="element:test:threshold-0001")
    with pytest.raises(ValidationError, match="identities must be unique"):
        make_threshold_contract(first, second)


def test_threshold_contract_rejects_bad_self_hash() -> None:
    contract = make_threshold_contract()
    with pytest.raises(ValidationError, match="contract hash mismatch"):
        ThresholdContract.model_validate({**contract.model_dump(mode="json"), "contract_sha256": "0" * 64})


def test_typed_contract_v2_covers_all_six_presentation_families() -> None:
    contracts = (
        make_diagram_contract(),
        make_table_contract(),
        make_ordered_list_contract(),
        make_constraint_contract(),
        make_threshold_contract(),
        make_formula_contract(),
    )
    validated = tuple(TypeAdapter(TypedContractV2).validate_python(contract) for contract in contracts)
    assert tuple(contract.presentation for contract in validated) == (
        "diagram",
        "table",
        "ordered_list",
        "constraint",
        "threshold",
        "formula",
    )


def test_v2_authority_chain_is_real_and_preserves_threshold() -> None:
    queue_item = make_queue_item()
    decision = make_decision(queue_item)
    candidate_ref = SimpleNamespace(candidate_id=CANDIDATE_ID)
    validate_human_decision_v2(decision, queue_item, candidate_ref)
    candidate = build_reviewed_candidate_v2(queue_item, CANDIDATE_ID, decision)
    assert candidate.presentation == decision.presentation == queue_item.presentation == "threshold"
    assert candidate.source_decision_id == decision.decision_id
    assert candidate.typed_contract == decision.typed_contract
    manifest = hash_model_r8x(
        HumanDecisionManifestV2,
        {
            "manifest_id": "manifest:r84:human:threshold-0001",
            "queue_sha256": decision.queue_sha256,
            "decisions": (decision,),
            "decision_count": 1,
        },
        "manifest_sha256",
    )
    assert manifest.decisions == (decision,)


def test_v2_human_decision_rejects_contract_presentation_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match typed contract"):
        make_decision(contract=make_threshold_contract(), presentation="constraint")


def test_v2_reviewed_candidate_rejects_contract_presentation_mismatch() -> None:
    candidate = build_reviewed_candidate_v2(make_queue_item(), CANDIDATE_ID, make_decision())
    payload = {**candidate.model_dump(mode="json"), "presentation": "constraint"}
    with pytest.raises(ValidationError, match="presentation mismatch"):
        ReviewedCandidateV2.model_validate(payload)


@pytest.mark.parametrize("model_factory", [make_v1_queue_item, lambda: make_v1_decision(make_v1_queue_item())])
def test_v1_complete_objects_still_reject_threshold_presentation(model_factory: Any) -> None:
    from examcard.gates.review.decisions import HumanDecision

    obj = model_factory()
    model_type = AdmissionQueueItem if isinstance(obj, AdmissionQueueItem) else HumanDecision
    payload = obj.model_dump(mode="json")
    payload["presentation"] = "threshold"
    with pytest.raises(ValidationError) as raised:
        model_type.model_validate(payload)
    assert any(error.get("type") == "literal_error" and error.get("loc", ())[-1] == "presentation" for error in raised.value.errors())


def test_v1_complete_reviewed_candidate_still_rejects_threshold_presentation() -> None:
    queue_item = make_v1_queue_item()
    decision = make_v1_decision(queue_item)
    candidate = build_reviewed_candidate(queue_item, CANDIDATE_ID, decision)
    payload = candidate.model_dump(mode="json")
    payload["presentation"] = "threshold"
    with pytest.raises(ValidationError) as raised:
        ReviewedCandidate.model_validate(payload)
    assert any(error.get("type") == "literal_error" and error.get("loc", ())[-1] == "presentation" for error in raised.value.errors())


def test_v1_complete_typed_contract_still_rejects_threshold_presentation() -> None:
    payload = make_formula_contract().model_dump(mode="json")
    payload["presentation"] = "threshold"
    with pytest.raises(ValidationError) as raised:
        TypeAdapter(TypedContract).validate_python(payload)
    assert any(error.get("type") == "union_tag_invalid" for error in raised.value.errors())


def test_v2_human_decision_rejects_wrong_candidate_reference() -> None:
    with pytest.raises(ValueError, match="candidate identity"):
        validate_human_decision_v2(make_decision(), make_queue_item(), SimpleNamespace(candidate_id="candidate:test:0002"))


def test_v2_human_decision_rejects_wrong_queue_item() -> None:
    original = make_queue_item()
    crossed = make_queue_item(fact_id="fact:test:0002")
    with pytest.raises(ValueError, match="source identity"):
        validate_human_decision_v2(make_decision(original), crossed, SimpleNamespace(candidate_id=CANDIDATE_ID))


@pytest.mark.parametrize("field", ["fact_id", "evidence_id"])
def test_v2_human_decision_rejects_crossed_fact_or_evidence(field: str) -> None:
    value = "fact:test:0002" if field == "fact_id" else "evidence:test:0002"
    with pytest.raises(ValueError, match="source identity"):
        validate_human_decision_v2(
            make_decision(**{field: value}),
            make_queue_item(),
            SimpleNamespace(candidate_id=CANDIDATE_ID),
        )


def test_v2_human_decision_rejects_crossed_anchor() -> None:
    with pytest.raises(ValueError, match="anchor binding"):
        validate_human_decision_v2(
            make_decision(anchor_ids=("anchor:test:0002",)),
            make_queue_item(),
            SimpleNamespace(candidate_id=CANDIDATE_ID),
        )


def test_v2_human_decision_rejects_crossed_source_manifest() -> None:
    changed_manifest = "e" * 64
    changed_page = make_source_page(source_manifest_sha256=changed_manifest)
    with pytest.raises(ValueError, match="source manifest"):
        validate_human_decision_v2(
            make_decision(source_manifest_sha256=changed_manifest, pages=(changed_page,)),
            make_queue_item(),
            SimpleNamespace(candidate_id=CANDIDATE_ID),
        )


def test_v2_human_decision_rejects_non_exact_page_set() -> None:
    second = make_source_page(page_ref_id="page:test:0002", page=2)
    with pytest.raises(ValueError, match="page set"):
        validate_human_decision_v2(
            make_decision(pages=(make_source_page(), second)),
            make_queue_item(),
            SimpleNamespace(candidate_id=CANDIDATE_ID),
        )


def test_v2_human_decision_rejects_crossed_page_image() -> None:
    changed_page = make_source_page(image_sha256="f" * 64)
    with pytest.raises(ValueError, match="page binding"):
        validate_human_decision_v2(
            make_decision(pages=(changed_page,)),
            make_queue_item(),
            SimpleNamespace(candidate_id=CANDIDATE_ID),
        )


def test_v2_human_decision_rejects_constraint_queue_crossing() -> None:
    with pytest.raises(ValueError, match="queue presentation"):
        validate_human_decision_v2(
            make_decision(),
            make_queue_item(presentation="constraint"),
            SimpleNamespace(candidate_id=CANDIDATE_ID),
        )


def test_v2_contract_binding_rejects_crossed_provenance() -> None:
    contract = make_threshold_contract(make_threshold_token(provenance=make_provenance(fact_id="fact:test:0002")))
    with pytest.raises(ValueError, match="crossed Fact/Evidence"):
        validate_typed_contract_binding_v2(make_decision(contract=contract), make_queue_item())


def test_v2_self_hashes_reject_tampering() -> None:
    decision = make_decision()
    with pytest.raises(ValidationError, match="hash mismatch"):
        HumanDecisionV2.model_validate({**decision.model_dump(mode="json"), "reason": "tampered"})
    contract = make_threshold_contract()
    with pytest.raises(ValidationError, match="hash mismatch"):
        ThresholdContract.model_validate({**contract.model_dump(mode="json"), "contract_sha256": "0" * 64})
    candidate = build_reviewed_candidate_v2(make_queue_item(), CANDIDATE_ID, decision)
    with pytest.raises(ValidationError, match="hash mismatch"):
        ReviewedCandidateV2.model_validate({**candidate.model_dump(mode="json"), "reviewer": "tampered"})


def test_v2_builder_rejects_non_reviewed_decision() -> None:
    item = make_queue_item()
    blocked = make_decision(item, disposition="blocked")
    with pytest.raises(ValueError, match="only reviewed decisions"):
        build_reviewed_candidate_v2(item, CANDIDATE_ID, blocked)


def test_v2_builder_rejects_candidate_id_drift() -> None:
    item = make_queue_item()
    with pytest.raises(ValueError, match="candidate identity"):
        build_reviewed_candidate_v2(item, "candidate:test:0002", make_decision(item))


def test_constraint_token_is_not_a_threshold_token() -> None:
    assert ConstraintToken is not ThresholdToken
