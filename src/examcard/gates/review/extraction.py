"""typed contract 提取面：step2/facts 准入产物 + 人工圈选 spec → r84 契约族（补搬 distill:gates-review）。

出处与裁决（T4b 首项验证点，2026-08-13）：评审内核下游（decisions 严格摄取 /
fixtures 构建器 / authorization r810 链）随 distill:gates-review 迁移齐全，但
「typed_contract 生产方」——从 step2/facts 产物构造 TableContract/OrderedListContract/…
（含逐元素 provenance）的提取面——留在旧仓 phase2r71/r8/r81 三个脚本里未曾蒸馏
（原设计 §7 风险 1 把 r8x payload 族判为历史形态；生产链 r85/r87/r89 的契约 JSON 由
人工在工作台撰写，引擎从未有过程序化构造器）。本模块把该提取面按 r8/r81 的管线语义
蒸馏进引擎，目标族改为内核实际消费的 r84 contract 家族（admission_fixtures）。

管线（r8 `_resolve_source → _binding → _typed_payload → _build_candidate` 的蒸馏形态）：

    ExtractionSource（step2/facts 准入产物的中性投影，驱动侧构造）
      + ManualSelectionSpec（人工圈选：呈现/逐元素文本/结构参数/五确认背书）
    → build_typed_contract（五呈现 r84 契约，逐元素 provenance，r8x 谱系封印）
    → build_extraction_decision（r84 HumanDecision，五确认 + 显式生产授权）
    → extract_reviewed_fixture（队列 + 决策清单 + 内核 build_reviewed_candidate/
      build_reviewed_fixture 装配，未圈选项守恒留在 manual_review）

哈希域纪律（gates/review 包级约定）：r84 家族对象（provenance/元素/契约/决策/清单/
候选/fixture）一律走 admission_legacy_hash 的 r8x 谱系（铁律 bc-b8b9aca6，与 r810
生产语料逐字节同谱系）；本模块新增的持久化类型（ExtractionSource /
ManualSelectionSpec / ExtractionQueueItem）属新代码域，封印走 examcard.common.hashing。

身份纪律（design §4 参数化差异点 1 的引擎形态）：本期身份前缀由调用方传
``identity_period``（如 ``x1``）；元素/边身份缺省内容寻址（r8 定式
``canonical_hash(f"{selection_id}|{index}")[:24]``），亦可经 spec 显式指定——
显式身份是 r810 金标回放（用旧仓真实语料对拍构造路径）与工作台迁移的必要通道。
decision/manifest 身份保持内核钉扎的 ``decision:r84:human:`` / ``manifest:r84:human:``
前缀，以 ``<identity_period>-<内容哈希>`` 派生（r810 export 同构定式）。

驱动集成（admitted step2 目录 → ExtractionSource 序列）不在本模块：入口签名以
T4b 驱动侧为权威，见 migration/ 后续小节点。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from pydantic import Field, model_validator

from ...common.hashing import canonical_hash as common_canonical_hash
from ...common.hashing import hash_model as common_hash_model
from ...knowledge.contracts.admission_fixtures import (
    HASH_PATTERN,
    ID_PATTERN,
    NOISE_MARKERS,
    ConstraintContract,
    ConstraintToken,
    DiagramContract,
    DiagramEdge,
    DiagramNode,
    FormulaContract,
    FormulaToken,
    OrderedListContract,
    OrderedListItem,
    SourcePageBinding,
    TableCell,
    TableContract,
    TypedContract,
    TypedElementProvenance,
)
from ...knowledge.contracts.admission_legacy_hash import (
    canonical_hash,
    hash_model_r8x,
    text_hash,
)
from ...materials.schema import StrictModel
from .decisions import (
    DECISION_IDENTITY_PREFIX,
    MANIFEST_IDENTITY_PREFIX,
    HumanDecision,
    HumanDecisionManifest,
    validate_human_decision,
)
from .dispositions import Disposition, Presentation
from .fixtures import ReviewedFixture, build_reviewed_candidate, build_reviewed_fixture
from .queue import ReviewQueueItemBase, queue_sha256 as compute_queue_sha256

__all__ = (
    "EXTRACTION_SOURCE_VERSION",
    "MANUAL_BINDING_SPEC_VERSION",
    "MANUAL_SELECTION_SPEC_VERSION",
    "EXTRACTION_QUEUE_ITEM_VERSION",
    "ROLE_BY_PRESENTATION",
    "ExtractionSource",
    "ManualBindingSpec",
    "ManualSelectionSpec",
    "ExtractionQueueItem",
    "ExtractionBatch",
    "make_source_page_binding",
    "make_extraction_source",
    "make_manual_selection_spec",
    "element_provenance",
    "build_typed_contract",
    "build_queue_item",
    "build_extraction_queue",
    "build_extraction_decision",
    "build_decision_manifest",
    "extract_reviewed_fixture",
    "mark_reviewed",
)

EXTRACTION_SOURCE_VERSION = "examcard_extraction_source_v1"
MANUAL_BINDING_SPEC_VERSION = "examcard_manual_binding_spec_v1"
MANUAL_SELECTION_SPEC_VERSION = "examcard_manual_selection_spec_v1"
EXTRACTION_QUEUE_ITEM_VERSION = "examcard_extraction_queue_item_v1"

# r8 `expected_role` 定式：呈现 ↔ 元素角色一一对应。
ROLE_BY_PRESENTATION: dict[str, str] = {
    "diagram": "diagram_node",
    "table": "table_cell",
    "ordered_list": "ordered_list_item",
    "constraint": "constraint_token",
    "formula": "formula_token",
}

# 元素身份的角色前缀（r8 定式：node:/cell:/item:/constraint:/formula:）。
_ELEMENT_ID_PREFIX: dict[str, str] = {
    "diagram": "node",
    "table": "cell",
    "ordered_list": "item",
    "constraint": "constraint",
    "formula": "formula",
}

_IDENTITY_PERIOD_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")


def _require_identity_period(identity_period: str) -> str:
    if _IDENTITY_PERIOD_PATTERN.fullmatch(identity_period) is None:
        raise ValueError(
            "identity period must be a lowercase audit token ([a-z0-9][a-z0-9._-]*): "
            f"{identity_period!r}"
        )
    return identity_period


class ExtractionSource(StrictModel):
    """step2/facts 准入产物的中性投影（r71 SourceRecordAdmission 的蒸馏形态）。

    驱动侧把「已准入的 step2/facts 产物」逐 Fact×Evidence×呈现 投影成本模型；
    锚点与源页证据必须齐备（r71 队列完整形状口径），页绑定不得跨 source manifest。
    """

    schema_version: Literal["examcard_extraction_source_v1"] = EXTRACTION_SOURCE_VERSION
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: Presentation
    source_manifest_sha256: str = Field(pattern=HASH_PATTERN)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    pages: tuple[SourcePageBinding, ...] = Field(min_length=1)
    source_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_source(self) -> "ExtractionSource":
        if len({item for item in self.anchor_ids}) != len(self.anchor_ids):
            raise ValueError("extraction source anchor ids must be unique")
        page_refs = [page.page_ref_id for page in self.pages]
        if len(page_refs) != len(set(page_refs)):
            raise ValueError("extraction source page refs must be unique")
        if any(page.source_manifest_sha256 != self.source_manifest_sha256 for page in self.pages):
            raise ValueError("extraction source page crossed source manifest")
        if self.source_sha256 != common_canonical_hash(
            self.model_dump(mode="json", exclude={"schema_version", "source_sha256"})
        ):
            raise ValueError("extraction source hash mismatch")
        return self


class ManualBindingSpec(StrictModel):
    """单个元素的人工圈选：角色 + 源页原文，结构参数按呈现可选。

    ``element_id``/``order`` 缺省内容寻址/按序派生；显式给值用于 r810 金标回放
    与工作台迁移（旧语料元素身份是人工撰写的，不满足内容寻址）。
    """

    schema_version: Literal["examcard_manual_binding_spec_v1"] = MANUAL_BINDING_SPEC_VERSION
    source_role: Literal["diagram_node", "table_cell", "ordered_list_item", "constraint_token", "formula_token"]
    text: str = Field(min_length=1)
    element_id: str | None = Field(default=None, pattern=ID_PATTERN)
    order: int | None = Field(default=None, ge=1)
    marker: str = ""
    row: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    row_span: int | None = Field(default=None, ge=1)
    column_span: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_binding(self) -> "ManualBindingSpec":
        coordinates = (self.row, self.column, self.row_span, self.column_span)
        if self.source_role == "table_cell":
            if any(value is not None for value in coordinates) and any(value is None for value in coordinates):
                raise ValueError("table cell coordinates must be given completely or not at all")
        elif any(value is not None for value in coordinates):
            raise ValueError("only table cells may carry grid coordinates")
        if self.marker and self.source_role != "ordered_list_item":
            raise ValueError("only ordered-list items may carry a marker")
        return self


class ManualSelectionSpec(StrictModel):
    """人工圈选 spec（r8/r81 ManualSelectionSpec 的蒸馏形态，含五确认背书）。

    spec 本身就是「工作台之外单独撰写的权威人工输入」（两界原则的权威侧）：
    reviewer/notes/recorded_at 与 excluded_noise_kinds 构成决策背书，蒸馏自
    r8 HumanSourcePageAudit 的 Literal 恒真确认字段组。
    """

    schema_version: Literal["examcard_manual_selection_spec_v1"] = MANUAL_SELECTION_SPEC_VERSION
    selection_id: str = Field(pattern=r"^selection:[a-z0-9][a-z0-9._:-]{2,255}$")
    subject_id: str = Field(pattern=ID_PATTERN)
    record_id: str = Field(pattern=ID_PATTERN)
    fact_id: str = Field(pattern=ID_PATTERN)
    evidence_id: str = Field(pattern=ID_PATTERN)
    presentation: Presentation
    page_ref_id: str = Field(pattern=ID_PATTERN)
    bindings: tuple[ManualBindingSpec, ...] = Field(min_length=1)
    diagram_edges: tuple[tuple[int, int], ...] = ()
    diagram_edge_ids: tuple[str, ...] = ()
    table_rows: int | None = Field(default=None, ge=1)
    table_columns: int | None = Field(default=None, ge=1)
    excluded_noise_kinds: tuple[str, ...] = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    notes: str = Field(min_length=3)
    recorded_at: str = Field(min_length=1)
    selection_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_selection(self) -> "ManualSelectionSpec":
        expected_role = ROLE_BY_PRESENTATION[self.presentation]
        if any(item.source_role != expected_role for item in self.bindings):
            raise ValueError("manual binding role does not match presentation")
        if self.presentation != "diagram" and (self.diagram_edges or self.diagram_edge_ids):
            raise ValueError("only diagram selections may carry edges")
        if any(
            len(edge) != 2 or not (1 <= edge[0] <= len(self.bindings)) or not (1 <= edge[1] <= len(self.bindings))
            for edge in self.diagram_edges
        ):
            raise ValueError("diagram selection contains an out-of-range edge")
        if self.diagram_edge_ids and len(self.diagram_edge_ids) != len(self.diagram_edges):
            raise ValueError("explicit diagram edge ids must cover every edge")
        if self.presentation == "table":
            if self.table_rows is None or self.table_columns is None:
                raise ValueError("table selection requires grid dimensions")
            explicit = [item for item in self.bindings if item.row is not None]
            if explicit and len(explicit) != len(self.bindings):
                raise ValueError("table selections mix explicit and derived cell coordinates")
            if not explicit and self.table_rows * self.table_columns != len(self.bindings):
                raise ValueError("derived table selection must cover the complete grid")
        elif self.table_rows is not None or self.table_columns is not None:
            raise ValueError("only table selections may carry grid dimensions")
        orders = [item.order for item in self.bindings if item.order is not None]
        if orders and (len(orders) != len(self.bindings) or sorted(orders) != list(range(1, len(self.bindings) + 1))):
            raise ValueError("explicit binding orders must be a complete 1..N permutation")
        explicit_ids = [item.element_id for item in self.bindings if item.element_id is not None]
        if explicit_ids and len(explicit_ids) != len(self.bindings):
            raise ValueError("explicit element ids must cover every binding")
        if len(set(explicit_ids)) != len(explicit_ids):
            raise ValueError("explicit element ids must be unique")
        if any(marker in item.text for item in self.bindings for marker in NOISE_MARKERS):
            raise ValueError("manual source text contains excluded noise")
        if self.selection_sha256 != common_canonical_hash(
            self.model_dump(mode="json", exclude={"schema_version", "selection_sha256"})
        ):
            raise ValueError("manual selection hash mismatch")
        return self


class ExtractionQueueItem(ReviewQueueItemBase):
    """提取期评审队列项（r8 R8ReviewQueueItem 的蒸馏形态，新代码域封印）。"""

    schema_version: Literal["examcard_extraction_queue_item_v1"] = EXTRACTION_QUEUE_ITEM_VERSION
    source_sha256: str = Field(pattern=HASH_PATTERN)
    disposition: Disposition = "manual_review"
    typed_contract_authorized: Literal[False] = False
    last_decision_id: str | None = Field(default=None, pattern=ID_PATTERN)
    entry_sha256: str = Field(pattern=HASH_PATTERN)

    @model_validator(mode="after")
    def validate_entry(self) -> "ExtractionQueueItem":
        if self.disposition == "reviewed_candidate" and not self.last_decision_id:
            raise ValueError("reviewed extraction queue item requires an explicit decision id")
        if self.entry_sha256 != common_canonical_hash(
            self.model_dump(mode="json", exclude={"schema_version", "entry_sha256"})
        ):
            raise ValueError("extraction queue item hash mismatch")
        return self


@dataclass(frozen=True)
class ExtractionBatch:
    """extract_reviewed_fixture 的装配结果（驱动侧负责持久化）。"""

    sources: tuple[ExtractionSource, ...]
    queue_items: tuple[ExtractionQueueItem, ...]
    queue_sha256: str
    manifest: HumanDecisionManifest
    fixture: ReviewedFixture


def make_source_page_binding(
    *,
    page_ref_id: str,
    page: int,
    source_file: str,
    image_path: str,
    image_sha256: str,
    image_width: int,
    image_height: int,
    page_sha256: str,
    source_manifest_sha256: str,
) -> SourcePageBinding:
    """构造 r84 SourcePageBinding（r8 `_page_model` 的模型侧蒸馏；文件核验属驱动侧）。"""
    return hash_model_r8x(
        SourcePageBinding,
        {
            "page_ref_id": page_ref_id,
            "page": page,
            "source_file": source_file,
            "image_path": image_path,
            "image_sha256": image_sha256,
            "image_width": image_width,
            "image_height": image_height,
            "page_sha256": page_sha256,
            "source_manifest_sha256": source_manifest_sha256,
        },
        "page_binding_sha256",
    )  # type: ignore[return-value]


def make_extraction_source(
    *,
    subject_id: str,
    record_id: str,
    fact_id: str,
    evidence_id: str,
    presentation: Presentation,
    source_manifest_sha256: str,
    anchor_ids: Sequence[str],
    pages: Sequence[SourcePageBinding],
) -> ExtractionSource:
    return common_hash_model(
        ExtractionSource,
        {
            "subject_id": subject_id,
            "record_id": record_id,
            "fact_id": fact_id,
            "evidence_id": evidence_id,
            "presentation": presentation,
            "source_manifest_sha256": source_manifest_sha256,
            "anchor_ids": tuple(anchor_ids),
            # 嵌套件先归一成模型：common hash_model 经 model_construct 计算封印，
            # 裸 dict 不补默认字段会导致封印与验证后 dump 不一致。
            "pages": tuple(
                page if isinstance(page, SourcePageBinding) else SourcePageBinding.model_validate(page)
                for page in pages
            ),
        },
        "source_sha256",
    )


def make_manual_selection_spec(payload: dict[str, Any]) -> ManualSelectionSpec:
    """封印人工圈选 spec（新代码域 hash_model；payload 不含 selection_sha256）。"""
    document = dict(payload)
    document["bindings"] = tuple(
        item if isinstance(item, ManualBindingSpec) else ManualBindingSpec.model_validate(item)
        for item in document.get("bindings", ())
    )
    return common_hash_model(ManualSelectionSpec, document, "selection_sha256")


def element_provenance(source: ExtractionSource, page_ref_id: str) -> TypedElementProvenance:
    """逐元素 provenance（r84 形态）：绑定源身份四元组 + 全锚点集 + 选定源页。

    r84 契约绑定校验要求 provenance.anchor_ids 与决策/队列的锚点集逐一相等，
    故此处携带完整 anchor_ids（r8 payload 族的单锚点形态不适用于 contract 族）。
    """
    page = next((item for item in source.pages if item.page_ref_id == page_ref_id), None)
    if page is None:
        raise ValueError(f"selected source page is not part of the extraction source: {page_ref_id}")
    return hash_model_r8x(
        TypedElementProvenance,
        {
            "subject_id": source.subject_id,
            "record_id": source.record_id,
            "fact_id": source.fact_id,
            "evidence_id": source.evidence_id,
            "anchor_ids": source.anchor_ids,
            "page_ref_id": page.page_ref_id,
            "page": page.page,
            "image_sha256": page.image_sha256,
            "source_manifest_sha256": source.source_manifest_sha256,
        },
        "provenance_sha256",
    )  # type: ignore[return-value]


def _spec_matches_source(spec: ManualSelectionSpec, source: ExtractionSource) -> None:
    """r8 `_resolve_source` 的绑定核验蒸馏：spec 不得跨源身份四元组或呈现。"""
    if (
        spec.subject_id != source.subject_id
        or spec.record_id != source.record_id
        or spec.fact_id != source.fact_id
        or spec.evidence_id != source.evidence_id
    ):
        raise ValueError("manual selection crossed the source identity quadruple")
    if spec.presentation != source.presentation:
        raise ValueError("manual selection crossed the source presentation")


def _element_id(spec: ManualSelectionSpec, binding: ManualBindingSpec, index: int, identity_period: str) -> str:
    if binding.element_id is not None:
        return binding.element_id
    prefix = _ELEMENT_ID_PREFIX[spec.presentation]
    return f"{prefix}:{identity_period}:{canonical_hash(f'{spec.selection_id}|{index}')[:24]}"


def _ordered_bindings(spec: ManualSelectionSpec) -> tuple[tuple[int, ManualBindingSpec], ...]:
    """圈选序 → 元素序：显式 order 用其值，否则按列出顺序 1..N。"""
    if any(item.order is not None for item in spec.bindings):
        return tuple((item.order, item) for item in spec.bindings)  # type: ignore[misc]
    return tuple((index, item) for index, item in enumerate(spec.bindings, start=1))


def _text_element_payload(
    element_id: str, binding: ManualBindingSpec, provenance: TypedElementProvenance
) -> dict[str, Any]:
    return {
        "element_id": element_id,
        "text": binding.text,
        "provenance": provenance,
        "text_sha256": text_hash(binding.text),
    }


def build_typed_contract(
    spec: ManualSelectionSpec,
    source: ExtractionSource,
    *,
    identity_period: str,
) -> TypedContract:
    """五呈现 r84 契约构造（r8 `_typed_payload` 蒸馏，元素直嵌 text+provenance）。

    元素序号（reading_order/order/token_order）取圈选序（或显式 order 置换）；
    表格网格缺省行主序 1×1 铺满（r8 定式），显式坐标支持跨行列合并单元格。
    """
    _require_identity_period(identity_period)
    _spec_matches_source(spec, source)
    provenance = element_provenance(source, spec.page_ref_id)
    ordered = sorted(_ordered_bindings(spec), key=lambda pair: pair[0])
    if spec.presentation == "diagram":
        nodes = tuple(
            hash_model_r8x(
                DiagramNode,
                {**_text_element_payload(_element_id(spec, binding, order, identity_period), binding, provenance), "reading_order": order},
                "element_sha256",
            )
            for order, binding in ordered
        )
        node_ids = [item.element_id for item in nodes]
        edge_ids = spec.diagram_edge_ids or tuple(
            f"edge:{identity_period}:{canonical_hash(f'{spec.selection_id}|{source_index}|{target_index}')[:24]}"
            for source_index, target_index in spec.diagram_edges
        )
        edges = tuple(
            hash_model_r8x(
                DiagramEdge,
                {
                    "edge_id": edge_id,
                    "source_node_id": node_ids[source_index - 1],
                    "target_node_id": node_ids[target_index - 1],
                    "provenance": provenance,
                },
                "edge_sha256",
            )
            for edge_id, (source_index, target_index) in zip(edge_ids, spec.diagram_edges, strict=True)
        )
        return hash_model_r8x(DiagramContract, {"nodes": nodes, "edges": edges}, "contract_sha256")  # type: ignore[return-value]
    if spec.presentation == "table":
        assert spec.table_rows is not None and spec.table_columns is not None
        cells = []
        for order, binding in ordered:
            if binding.row is not None:
                row, column = binding.row, binding.column
                row_span, column_span = binding.row_span, binding.column_span
            else:
                row = (order - 1) // spec.table_columns + 1
                column = (order - 1) % spec.table_columns + 1
                row_span = column_span = 1
            cells.append(
                hash_model_r8x(
                    TableCell,
                    {
                        **_text_element_payload(_element_id(spec, binding, order, identity_period), binding, provenance),
                        "row": row,
                        "column": column,
                        "row_span": row_span,
                        "column_span": column_span,
                        "reading_order": order,
                    },
                    "element_sha256",
                )
            )
        return hash_model_r8x(
            TableContract,
            {"rows": spec.table_rows, "columns": spec.table_columns, "cells": tuple(cells)},
            "contract_sha256",
        )  # type: ignore[return-value]
    if spec.presentation == "ordered_list":
        items = tuple(
            hash_model_r8x(
                OrderedListItem,
                {
                    **_text_element_payload(_element_id(spec, binding, order, identity_period), binding, provenance),
                    "order": order,
                    "marker": binding.marker,
                },
                "element_sha256",
            )
            for order, binding in ordered
        )
        return hash_model_r8x(
            OrderedListContract, {"items": items, "continuation_binding_ids": ()}, "contract_sha256"
        )  # type: ignore[return-value]
    token_type, contract_type = (
        (ConstraintToken, ConstraintContract) if spec.presentation == "constraint" else (FormulaToken, FormulaContract)
    )
    tokens = tuple(
        hash_model_r8x(
            token_type,
            {
                **_text_element_payload(_element_id(spec, binding, order, identity_period), binding, provenance),
                "token_order": order,
            },
            "element_sha256",
        )
        for order, binding in ordered
    )
    return hash_model_r8x(contract_type, {"tokens": tokens}, "contract_sha256")  # type: ignore[return-value]


def build_queue_item(source: ExtractionSource, *, identity_period: str) -> ExtractionQueueItem:
    """内容寻址队列项（r8 `_queue_item` 蒸馏）：身份派生自源投影封印。"""
    _require_identity_period(identity_period)
    return common_hash_model(
        ExtractionQueueItem,
        {
            "queue_item_id": f"queue:{identity_period}:{common_canonical_hash(source.source_sha256)[:24]}",
            "subject_id": source.subject_id,
            "record_id": source.record_id,
            "fact_id": source.fact_id,
            "evidence_id": source.evidence_id,
            "presentation": source.presentation,
            "source_manifest_sha256": source.source_manifest_sha256,
            "anchor_ids": source.anchor_ids,
            "pages": source.pages,
            "source_sha256": source.source_sha256,
            "disposition": "manual_review",
            "last_decision_id": None,
        },
        "entry_sha256",
    )


def build_extraction_queue(
    sources: Sequence[ExtractionSource], *, identity_period: str
) -> tuple[tuple[ExtractionQueueItem, ...], str]:
    """r71 队列源头形态：全部 manual_review 起步；返回 (队列, queue_sha256 链哈希)。"""
    items = tuple(build_queue_item(source, identity_period=identity_period) for source in sources)
    if len({item.queue_item_id for item in items}) != len(items):
        raise ValueError("extraction queue derived duplicate identities (duplicate sources?)")
    return items, compute_queue_sha256(items)


def mark_reviewed(item: ExtractionQueueItem, decision: HumanDecision) -> ExtractionQueueItem:
    """r8 `_mark_reviewed` 蒸馏：显式决策把队列项迁移到 reviewed_candidate 并重封。

    重封 payload 直接取模型属性（原生 tuple/模型类型）而非 model_dump 的 list 形态，
    避免 hash_model 经 model_construct 序列化时的 pydantic 类型告警（封印值不受影响，
    canonical json 对 list/tuple 同形）。
    """
    if decision.queue_item_id != item.queue_item_id:
        raise ValueError("decision crossed queue item identity")
    payload = {
        "queue_item_id": item.queue_item_id,
        "subject_id": item.subject_id,
        "record_id": item.record_id,
        "fact_id": item.fact_id,
        "evidence_id": item.evidence_id,
        "presentation": item.presentation,
        "source_manifest_sha256": item.source_manifest_sha256,
        "anchor_ids": item.anchor_ids,
        "pages": item.pages,
        "source_sha256": item.source_sha256,
        "disposition": "reviewed_candidate",
        "typed_contract_authorized": False,
        "last_decision_id": decision.decision_id,
    }
    return common_hash_model(ExtractionQueueItem, payload, "entry_sha256")


def build_extraction_decision(
    spec: ManualSelectionSpec,
    source: ExtractionSource,
    queue_item: ExtractionQueueItem,
    *,
    queue_sha256: str,
    identity_period: str,
) -> HumanDecision:
    """从圈选 spec 装配 r84 权威决策（五确认 = spec 的显式人工背书，两界原则权威侧）。"""
    _require_identity_period(identity_period)
    _spec_matches_source(spec, source)
    if queue_item.source_sha256 != source.source_sha256:
        raise ValueError("queue item is not derived from this extraction source")
    contract = build_typed_contract(spec, source, identity_period=identity_period)
    candidate_id = f"candidate:{identity_period}:{canonical_hash(f'{spec.selection_sha256}|{queue_item.entry_sha256}')[:24]}"
    payload = {
        "decision_id": f"{DECISION_IDENTITY_PREFIX}{identity_period}-{canonical_hash(candidate_id)[:24]}",
        "candidate_id": candidate_id,
        "queue_item_id": queue_item.queue_item_id,
        "queue_sha256": queue_sha256,
        "subject_id": source.subject_id,
        "record_id": source.record_id,
        "fact_id": source.fact_id,
        "evidence_id": source.evidence_id,
        "presentation": source.presentation,
        "source_manifest_sha256": source.source_manifest_sha256,
        "anchor_ids": source.anchor_ids,
        "pages": source.pages,
        "disposition": "reviewed_candidate",
        "reviewer": spec.reviewer,
        "reason": spec.notes,
        "source_text_confirmed": True,
        "anchor_confirmed": True,
        "source_page_confirmed": True,
        "image_hash_confirmed": True,
        "typed_contract_confirmed": True,
        "typed_contract": contract,
        "typed_contract_sha256": canonical_hash(contract.model_dump(mode="json")),
        "recorded_at": spec.recorded_at,
        "browser_demo": False,
        "production_fixture_authorized": True,
    }
    return hash_model_r8x(HumanDecision, payload, "decision_sha256")  # type: ignore[return-value]


def build_decision_manifest(
    decisions: Sequence[HumanDecision], *, queue_sha256: str, identity_period: str
) -> HumanDecisionManifest:
    _require_identity_period(identity_period)
    payload = {
        "manifest_id": f"{MANIFEST_IDENTITY_PREFIX}{identity_period}-{canonical_hash(queue_sha256)[:24]}",
        "queue_sha256": queue_sha256,
        "decisions": tuple(decisions),
        "decision_count": len(decisions),
    }
    return hash_model_r8x(HumanDecisionManifest, payload, "manifest_sha256")  # type: ignore[return-value]


class _CandidateRef:
    """validate_human_decision 的候选侧鸭子类型载体（提取期候选身份即决策候选身份）。"""

    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id


def extract_reviewed_fixture(
    sources: Sequence[ExtractionSource],
    specs: Sequence[ManualSelectionSpec],
    *,
    identity_period: str,
) -> ExtractionBatch:
    """提取面主装配：源投影 + 圈选 spec → 队列/决策清单/r84 reviewed fixture。

    - 每份 spec 必须唯一命中一个源投影（身份四元组 + 呈现），一源至多一份 spec；
    - 圈选项经内核 `validate_human_decision`（含契约三方绑定校验）后进 fixture；
    - 未圈选源守恒留在 manual_review（处置守恒：升格只能来自显式人工决策本身）。

    r810 显式生产授权链（authorization.export_r84_manifest）在内核既有，超出
    提取面范围；驱动可将本 fixture 的候选继续送授权层。
    """
    _require_identity_period(identity_period)
    by_identity: dict[tuple[str, str, str, str, str], int] = {}
    for index, source in enumerate(sources):
        key = (source.subject_id, source.record_id, source.fact_id, source.evidence_id, source.presentation)
        if key in by_identity:
            raise ValueError(f"duplicate extraction source projection: {key}")
        by_identity[key] = index
    queue_items, chain_sha256 = build_extraction_queue(sources, identity_period=identity_period)
    claimed: set[int] = set()
    decisions: list[HumanDecision] = []
    candidates = []
    final_items = list(queue_items)
    for spec in specs:
        key = (spec.subject_id, spec.record_id, spec.fact_id, spec.evidence_id, spec.presentation)
        index = by_identity.get(key)
        if index is None:
            raise ValueError(f"manual selection has no matching extraction source: {key}")
        if index in claimed:
            raise ValueError(f"extraction source already claimed by another selection: {key}")
        claimed.add(index)
        source, queue_item = sources[index], queue_items[index]
        decision = build_extraction_decision(
            spec, source, queue_item, queue_sha256=chain_sha256, identity_period=identity_period
        )
        validate_human_decision(decision, queue_item, _CandidateRef(decision.candidate_id))
        candidates.append(build_reviewed_candidate(queue_item, decision.candidate_id, decision))
        final_items[index] = mark_reviewed(queue_item, decision)
        decisions.append(decision)
    manifest = build_decision_manifest(decisions, queue_sha256=chain_sha256, identity_period=identity_period)
    fixture = build_reviewed_fixture(chain_sha256, candidates)
    return ExtractionBatch(
        sources=tuple(sources),
        queue_items=tuple(final_items),
        queue_sha256=chain_sha256,
        manifest=manifest,
        fixture=fixture,
    )
