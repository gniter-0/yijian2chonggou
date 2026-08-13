"""echo 批 release/handoff 新版本号家族（P8 补链施工 #1/#2；规范 = echo_step4_bridge_design.md §3.2/§3.3）。

设计红线（§0）：**新增模块/新版本号，pilot 原件零改动**——r319 `Step4PilotHandoffRefresh`、
r2242 `Step3UnifiedReviewedReleaseFixture` 与其全部 Literal 钉扎原字节保留；本家族与之平行，
互不引用对方版本号。

本模块＝**纯数据契约**（A 案纪律：跨桶模型下沉 knowledge/contracts，零向上依赖）；
谱系四哈希的链内派生构建器在 `compilers/step4_echo_bridge.build_echo_release_fixture`
（其消费 compile_reviewed_fixture，依赖方向禁止 knowledge→compilers 边），授权签发在
`gates/review/echo_step3_review.issue_echo_batch_handoff`——
`EchoBatchStep4Handoff.batch_generation_authorized: Literal[True]` 只能经摄取门禁签发，
把 r319 钉死的「试点不授权批产（False）」翻转为**人工签发的正向事实**。

自证哈希走 ``common.hashing``（新产出域；与旧契约法定哈希域不掺混——铁律 bc-b8b9aca6）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash
from ...materials.schema import StrictModel

__all__ = (
    "ECHO_RELEASE_VERSION",
    "ECHO_HANDOFF_VERSION",
    "EchoReleaseVisibleText",
    "EchoReleaseSemanticEdge",
    "EchoReleaseTableCell",
    "EchoReleaseTableView",
    "EchoBatchReleaseCard",
    "EchoBatchReviewedReleaseFixture",
    "EchoBatchCardDisposition",
    "EchoReviewedImageReference",
    "EchoBatchStep4Handoff",
    "load_echo_release_fixture",
    "load_echo_handoff",
)

ECHO_RELEASE_VERSION = "echo-batch-release/1.0"
ECHO_HANDOFF_VERSION = "echo-batch-step4-handoff/1.0"

_ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"
_BATCH_ID = r"^echo-batch:[0-9a-f]{24}$"
_PRESENTATIONS = ("diagram", "table", "ordered_list", "constraint", "formula")


class EchoReleaseVisibleText(StrictModel):
    """step4 出图/词级门禁共同消费面：字段名对齐 prompt 构建器 duck 契约（§3.4 判据）。"""

    element_id: str = Field(pattern=_ID)
    visible_text: str = Field(min_length=1)
    fact_id: str = Field(pattern=_ID)
    source_order: int = Field(ge=1)
    semantic_node_id: str | None = None  # diagram 节点元素＝其 element_id；其余卡型 None


class EchoReleaseSemanticEdge(StrictModel):
    source_node_id: str = Field(pattern=_ID)
    target_node_id: str = Field(pattern=_ID)
    # typed 层 DiagramEdgeRef 为 layout_only 且无关系词字段；echo 家族以常量提示布局连线
    # （prompt 硬性要求禁其成为可见文字，v1 冻结口径 relation_layout_only 一致）。
    layout_relation: str = Field(default="connects", min_length=1)


class EchoReleaseTableCell(StrictModel):
    row: int = Field(ge=0)  # 0 基——对齐 prompt v2 TABLE_GRID 块标注口径（typed 层 1 基，构建器折算）
    column: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    text: str = Field(min_length=1)


class EchoReleaseTableView(StrictModel):
    table_state: Literal["reviewed_grid"] = "reviewed_grid"
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    cells: tuple[EchoReleaseTableCell, ...] = Field(min_length=1)


class EchoBatchReleaseCard(StrictModel):
    schema_version: Literal["echo-batch-release-card/1.0"] = "echo-batch-release-card/1.0"
    card_index: int = Field(ge=1)  # N 卡通用：无上限钉扎（对照 pilot le=16，勘误见设计 §7 风险项）
    source_card_id: str = Field(pattern=r"^step3-card-contract:[0-9a-f]{24}$")
    source_candidate_id: str = Field(pattern=_ID)
    sample_id: str = Field(pattern=_ID)
    source_presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    render_presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    visible_texts: tuple[EchoReleaseVisibleText, ...] = Field(min_length=1)
    diagram_node_ids: tuple[str, ...] = ()
    semantic_layout_edges: tuple[EchoReleaseSemanticEdge, ...] = ()
    table_contracts: tuple[EchoReleaseTableView, ...] = ()
    # 谱系四哈希（链内派生，见 build_echo_release_fixture）
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    source_contract_sha256: str = Field(pattern=SHA256_HEX)
    echo_record_sha256: str = Field(pattern=SHA256_HEX)
    review_signoff_sha256: str = Field(pattern=SHA256_HEX)
    reviewer: str = Field(min_length=1)
    card_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_card(self) -> "EchoBatchReleaseCard":
        if self.source_presentation != self.render_presentation:
            raise ValueError("echo release card presentations must match in v1.0")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("echo release card fact ids must be unique")
        if self.render_presentation == "diagram":
            if not self.diagram_node_ids or not self.semantic_layout_edges:
                raise ValueError("diagram card requires node ids and layout edges")
            labelled = {item.semantic_node_id for item in self.visible_texts if item.semantic_node_id}
            if set(self.diagram_node_ids) - labelled:
                raise ValueError("every diagram node id needs a labelled visible text")
        if self.render_presentation == "table" and not self.table_contracts:
            raise ValueError("table card requires a reviewed grid view")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "card_sha256"}))
        if self.card_sha256 != expected:
            raise ValueError("echo release card hash mismatch")
        return self


class EchoBatchReviewedReleaseFixture(StrictModel):
    schema_version: Literal["echo-batch-release/1.0"] = ECHO_RELEASE_VERSION
    batch_id: str = Field(pattern=_BATCH_ID)
    source_typed_fixture_path: str = Field(min_length=1)
    source_typed_fixture_sha256: str = Field(pattern=SHA256_HEX)
    source_echo_run_id: str = Field(min_length=1)
    review_sheet_sha256: str = Field(pattern=SHA256_HEX)
    card_count: int = Field(ge=1)
    cards: tuple[EchoBatchReleaseCard, ...] = Field(min_length=1)
    fixture_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_fixture(self) -> "EchoBatchReviewedReleaseFixture":
        if self.card_count != len(self.cards):
            raise ValueError("echo release card count mismatch")
        if tuple(card.card_index for card in self.cards) != tuple(range(1, len(self.cards) + 1)):
            raise ValueError("echo release card indexes must be a dense 1..N sequence")
        if len({card.source_card_id for card in self.cards}) != len(self.cards):
            raise ValueError("echo release source card ids must be unique")
        if any(card.source_typed_fixture_sha256 != self.source_typed_fixture_sha256 for card in self.cards):
            raise ValueError("echo release cards must bind the fixture-level typed source hash")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "fixture_sha256"}))
        if self.fixture_sha256 != expected:
            raise ValueError("echo release fixture hash mismatch")
        return self


class EchoBatchCardDisposition(StrictModel):
    card_index: int = Field(ge=1)
    source_card_id: str = Field(pattern=r"^step3-card-contract:[0-9a-f]{24}$")
    disposition: Literal["pending", "reviewed"]


class EchoReviewedImageReference(StrictModel):
    """增量重渲批携带旧图引用（derive 的 prior_reviewed 跳过语义载体；新批缺省不携带）。"""

    card_index: int = Field(ge=1)
    image_sha256: str = Field(pattern=SHA256_HEX)
    reference_path: str = Field(min_length=1)


class EchoBatchStep4Handoff(StrictModel):
    schema_version: Literal["echo-batch-step4-handoff/1.0"] = ECHO_HANDOFF_VERSION
    batch_id: str = Field(pattern=_BATCH_ID)
    source_release_sha256: str = Field(pattern=SHA256_HEX)
    card_count: int = Field(ge=1)
    cards: tuple[EchoBatchCardDisposition, ...] = Field(min_length=1)
    reviewed_image_references: tuple[EchoReviewedImageReference, ...] = ()
    batch_generation_authorized: Literal[True] = True
    authorized_by: str = Field(min_length=1)
    authorization_evidence_sha256: str = Field(pattern=SHA256_HEX)
    handoff_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_handoff(self) -> "EchoBatchStep4Handoff":
        if self.card_count != len(self.cards):
            raise ValueError("echo handoff card count mismatch")
        if tuple(item.card_index for item in self.cards) != tuple(range(1, len(self.cards) + 1)):
            raise ValueError("echo handoff card indexes must be a dense 1..N sequence")
        reviewed = {item.card_index for item in self.cards if item.disposition == "reviewed"}
        referenced = {item.card_index for item in self.reviewed_image_references}
        if reviewed != referenced:
            raise ValueError("reviewed dispositions and image references must cover the same cards")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "handoff_sha256"}))
        if self.handoff_sha256 != expected:
            raise ValueError("echo handoff hash mismatch")
        return self


# ---------------------------------------------------------------------------
# 装载器（构建器在 compilers/step4_echo_bridge.py：其消费 compile_reviewed_fixture，
# 依赖方向纪律（A 案，engineering_skeleton bc-888c6031 谱系）禁止 knowledge→compilers 边）
# ---------------------------------------------------------------------------


def load_echo_release_fixture(path: Path) -> EchoBatchReviewedReleaseFixture:
    return EchoBatchReviewedReleaseFixture.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def load_echo_handoff(path: Path) -> EchoBatchStep4Handoff:
    return EchoBatchStep4Handoff.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
