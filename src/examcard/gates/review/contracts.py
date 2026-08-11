"""typed contract 绑定校验（模型本体已按总指挥 A 案下沉 knowledge/contracts/admission_fixtures）。

本模块只承载桶内判定逻辑：契约元素遍历、契约-决策-队列三方绑定校验（r84 规范源）、
以及工作台结构预检（r83 客户端预检的服务端等价物）。模型一律向下 import，零复制。
"""
from __future__ import annotations

from typing import Any, Mapping

from ...knowledge.contracts.admission_fixtures import (
    NOISE_MARKERS,
    ConstraintContract,
    DiagramContract,
    FormulaContract,
    OrderedListContract,
    TableContract,
    TypedContract,
)

__all__ = (
    "NOISE_MARKERS",
    "TypedContract",
    "DiagramContract",
    "TableContract",
    "OrderedListContract",
    "ConstraintContract",
    "FormulaContract",
    "contract_elements",
    "validate_typed_contract_binding",
    "precheck_contract_payload",
)


def contract_elements(contract: Any) -> tuple[Any, ...]:
    if isinstance(contract, DiagramContract):
        return tuple(contract.nodes) + tuple(contract.edges)
    if isinstance(contract, TableContract):
        return tuple(contract.cells)
    if isinstance(contract, OrderedListContract):
        return tuple(contract.items)
    if isinstance(contract, (ConstraintContract, FormulaContract)):
        return tuple(contract.tokens)
    raise TypeError(f"unsupported typed contract: {type(contract)!r}")


def validate_typed_contract_binding(decision: Any, queue_item: Any) -> None:
    """r84 语义：契约每个元素的 provenance 必须逐字段绑定决策与队列项，禁带派生/噪声旗标。"""
    contract = decision.typed_contract
    if contract is None:
        raise ValueError("typed contract is missing")
    if contract.presentation != decision.presentation:
        raise ValueError("typed contract presentation mismatch")
    source_pages = {page.page_ref_id: page for page in decision.pages}
    if not source_pages:
        raise ValueError("typed contract has no source page")
    for element in contract_elements(contract):
        provenance = element.provenance
        if (
            provenance.subject_id != decision.subject_id
            or provenance.record_id != decision.record_id
            or provenance.fact_id != decision.fact_id
            or provenance.evidence_id != decision.evidence_id
        ):
            raise ValueError("typed element crossed Fact/Evidence binding")
        if provenance.anchor_ids != decision.anchor_ids or provenance.source_manifest_sha256 != decision.source_manifest_sha256:
            raise ValueError("typed element crossed anchor or source manifest")
        page = source_pages.get(provenance.page_ref_id)
        if page is None or page.page != provenance.page or page.image_sha256 != provenance.image_sha256:
            raise ValueError("typed element crossed source page or image hash")
        if (
            not getattr(element, "human_confirmed", True)
            or getattr(element, "ocr_derived", True)
            or getattr(element, "assertion_derived", True)
            or getattr(element, "filename_derived", True)
            or getattr(element, "legacy_hint_derived", True)
            or getattr(element, "watermark", True)
        ):
            raise ValueError("typed element has an unauthorized derivation or noise flag")
    if (
        queue_item.presentation != decision.presentation
        or queue_item.fact_id != decision.fact_id
        or queue_item.evidence_id != decision.evidence_id
    ):
        raise ValueError("decision crossed queue presentation or Fact/Evidence")
    if not queue_item.anchor_ids or tuple(queue_item.anchor_ids) != tuple(decision.anchor_ids):
        raise ValueError("decision crossed queue anchor binding")


def precheck_contract_payload(payload: Mapping[str, Any] | None, presentation: str) -> tuple[str, ...]:
    """工作台结构预检（r83 JS 预检的服务端等价物）：返回问题码元组，空即通过。

    预检只看结构完整性；最终 hash/provenance 校验仍由严格摄取完成。
    """
    problems: list[str] = []
    if not payload:
        return ("contract_missing",)
    if payload.get("presentation") != presentation:
        problems.append("presentation_mismatch")
    if not payload.get("schema_version") or not payload.get("contract_sha256"):
        problems.append("schema_or_hash_missing")
    if presentation == "diagram":
        nodes = payload.get("nodes")
        if not isinstance(nodes, (list, tuple)) or not nodes or not isinstance(payload.get("edges"), (list, tuple)):
            problems.append("diagram_structure_incomplete")
    elif presentation == "table":
        cells = payload.get("cells")
        rows, columns = payload.get("rows"), payload.get("columns")
        if not isinstance(cells, (list, tuple)) or not cells or not (isinstance(rows, int) and rows > 0 and isinstance(columns, int) and columns > 0):
            problems.append("table_structure_incomplete")
    elif presentation == "ordered_list":
        items = payload.get("items")
        if not isinstance(items, (list, tuple)) or not items or payload.get("continuation_binding_ids"):
            problems.append("ordered_list_structure_incomplete")
    elif presentation in ("constraint", "formula"):
        tokens = payload.get("tokens")
        if not isinstance(tokens, (list, tuple)) or not tokens:
            problems.append("token_structure_incomplete")
    else:
        problems.append("unknown_presentation")
    return tuple(problems)
