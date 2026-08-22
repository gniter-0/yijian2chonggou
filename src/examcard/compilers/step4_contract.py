"""Generic Step 4 source-derived contract compiler (distilled from phase2r4).

复制改造自 yijian2 `scripts/knowledge_v2/phase2r4_step4_generic_contract_compiler.py`
（distill:compilers，设计依据 docs/design/p1_compilers_release_gate_design.md §4.3）。

- 输入契约（r2242 三型、Step4PilotHandoffRefresh）按 A 案自
  `knowledge/contracts/admission_fixtures.py` 向下 import（EXPECTED_* 计数常量随
  r2242 契约同处）。
- r4 法定哈希三件套 `_sha256/_json_bytes/_canonical_hash` 依铁律 bc-b8b9aca6 随
  模块逐字节原样（与 r9 血统、r8x 血统禁互换）。
- 参数化（设计 §4.3）：`derive_step4_bundle(fixture, handoff, *, ...)` 纯计算入口
  替代读盘 `_expected_bundle`；`assess_offline_gate` 的 freeze 结果与期望数量改为
  参数注入；fake artifact 文件名前缀参数化（默认保留 "gonglu-step4-card-" 以保
  哈希；模型 pattern 做哈希安全的最小放宽以接受新科目前缀）。
- 旧仓留守（不在本模块）：FROZEN_R33_R319_HASHES 冻结哈希表本体、
  `_historical_identities`/`audit_frozen_history`、`_read_fixture` 读盘断言、
  RUN_DIR 等 11 个输出路径与 `materialize_phase2r4`。本模块仅携带
  `FROZEN_ARTIFACT_COUNT/HISTORICAL_IDENTITY_COUNT` 两个整数（FreezeAudit/Report
  数据模型的 Literal 载荷，用于装载校验历史产物；冻结表校验本身留旧仓）。
- 回归矩阵对 16 卡 gonglu 试点 fixture 的卡位假设（cards[5]=table 等）保持原行为。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Final, Iterator, Literal

from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel
from examcard.knowledge.contracts.admission_fixtures import (
    EXPECTED_CARD_COUNT,
    EXPECTED_DISPLAY_TEXT_COUNT,
    EXPECTED_FACT_COUNT,
    EXPECTED_SEMANTIC_EDGE_COUNT,
    Step3MergedReviewedReleaseCard,
    Step3MergedSourceOwnedDisplayText,
    Step3UnifiedReviewedReleaseFixture,
    Step4PilotHandoffRefresh,
)

__all__ = [
    "ExpectedCounts",
    "FORBIDDEN_MODEL_KEYS",
    "FORBIDDEN_TEXT",
    "GenericCardContract",
    "GenericGeometry",
    "GenericOrderedListContract",
    "GenericOrderedListItem",
    "GenericSemanticEdge",
    "GenericTableCell",
    "GenericTableContract",
    "GenericTokenBinding",
    "GenericVisibleText",
    "MARKERS",
    "OUTPUT_HEIGHT",
    "OUTPUT_WIDTH",
    "Phase2R4BatchEntry",
    "Phase2R4BatchManifest",
    "Phase2R4CacheRecord",
    "Phase2R4ContractBundle",
    "Phase2R4FakeArtifact",
    "Phase2R4FakeArtifactManifest",
    "Phase2R4FreezeAudit",
    "Phase2R4FrozenArtifact",
    "Phase2R4JournalRecord",
    "Phase2R4ModelInput",
    "Phase2R4OfflineGate",
    "Phase2R4PromptContract",
    "Phase2R4RegressionResult",
    "Phase2R4Report",
    "Phase2R4Request",
    "RENDER_PRESENTATIONS",
    "SOURCE_KINDS",
    "SOURCE_PRESENTATIONS",
    "TOKEN_PATTERN",
    "assess_offline_gate",
    "build_regression_matrix",
    "derive_step4_bundle",
]

PHASE: Final = "2R.4"
SCHEMA_VERSION: Final = "phase2r4_step4_generic_source_derived_contract_compiler_v1"
CONTRACT_BUNDLE_VERSION: Final = "step4_generic_contract_bundle_v1"
MODEL_INPUT_VERSION: Final = "step4_generic_model_input_v1"
PROMPT_VERSION: Final = "step4_generic_prompt_contract_v1"
REQUEST_VERSION: Final = "step4_generic_offline_request_v1"
FAKE_ARTIFACT_VERSION: Final = "step4_generic_fake_artifact_manifest_v1"
CACHE_VERSION: Final = "step4_generic_cache_v1"
JOURNAL_VERSION: Final = "step4_generic_journal_v1"
BATCH_VERSION: Final = "step4_generic_batch_manifest_v1"
GATE_VERSION: Final = "step4_generic_batch_offline_gate_v1"
REGRESSION_VERSION: Final = "step4_generic_regression_result_v1"
REPORT_VERSION: Final = "phase2r4_step4_generic_report_v1"

OUTPUT_WIDTH: Final = 1536
OUTPUT_HEIGHT: Final = 1024
_SHA = r"^[0-9a-f]{64}$"
_FACT = r"^fact:[0-9a-f]{24}$"
_CARD = r"^v25-release-card:[0-9a-f]{24}$"
_REQUEST = r"^step4-generic-offline-request:[0-9a-f]{64}$"
_MODEL_INPUT = r"^step4-generic-model-input:[0-9a-f]{24}$"
_JOB = r"^step4-generic-job:[0-9a-f]{24}$"
# 旧仓 pattern 钉死 gonglu 前缀；按设计 §4.3 做哈希安全放宽（pattern 不参与序列化，
# 既有 gonglu 文件名仍匹配），配合 filename_prefix 参数支持新科目。
_FAKE_FILENAME = r"^[a-z0-9][a-z0-9-]*step4-card-[0-9]{2}-[0-9a-f]{8}\.png$"
DEFAULT_FAKE_FILENAME_PREFIX = "gonglu-step4-card-"

SOURCE_PRESENTATIONS = ("table", "diagram", "ordered_list", "constraint", "threshold", "formula")
RENDER_PRESENTATIONS = ("table", "diagram", "ordered_list", "constraint", "formula")
SOURCE_KINDS = (
    "reviewed_fact_assertion",
    "reviewed_semantic_node_label",
    "reviewed_remediation_assertion",
    "reviewed_remediation_ordered_list",
    "reviewed_remediation_table_grid",
    "inherited_reviewed_fact_assertion",
)
FORBIDDEN_MODEL_KEYS = frozenset(
    {
        "golden_title",
        "learning_golden",
        "canonical_title",
        "card_key",
        "review_state",
        "review_basis",
        "reviewer",
        "reviewed_at_utc",
        "manual_decision",
        "expected_texts",
        "image_prompt",
        "layout_description",
        "unknown_field",
    }
)
FORBIDDEN_TEXT = ("watermark", "水印", "evidence-only", "evidence_only", "learning golden")
MARKERS = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")
TOKEN_PATTERN = re.compile(r"[A-Za-z]+[A-Za-z0-9%]*|[0-9]+(?:\.[0-9]+)?(?:[A-Za-z%]+)?|[=+\-*/×÷Σ∑≤≥<>~～^]+")

# 冻结表本体（FROZEN_R33_R319_HASHES）与 audit_frozen_history 留旧仓；此二整数为
# FreezeAudit/Report 数据模型的 Literal 载荷（装载校验历史产物所需）。
FROZEN_ARTIFACT_COUNT: Final = 501
HISTORICAL_IDENTITY_COUNT: Final = 19


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    # r4 法定哈希实现，逐字节原样（铁律 bc-b8b9aca6）
    if isinstance(value, StrictModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_hash(value: Any) -> str:
    return _sha256(_json_bytes(value))


class Phase2R4FrozenArtifact(StrictModel):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA)


class Phase2R4FreezeAudit(StrictModel):
    schema_version: Literal["phase2r4_freeze_audit_v1"] = "phase2r4_freeze_audit_v1"
    valid: bool
    artifact_count: Literal[501] = FROZEN_ARTIFACT_COUNT
    artifacts: tuple[Phase2R4FrozenArtifact, ...] = Field(
        min_length=FROZEN_ARTIFACT_COUNT, max_length=FROZEN_ARTIFACT_COUNT
    )
    aggregate_sha256: str = Field(pattern=_SHA)
    historical_identity_count: Literal[19] = HISTORICAL_IDENTITY_COUNT
    historical_identity_set_sha256: str = Field(pattern=_SHA)
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_audit(self) -> "Phase2R4FreezeAudit":
        exact = len(self.artifacts) == self.artifact_count
        unique = len({item.path for item in self.artifacts}) == self.artifact_count
        if self.valid != (exact and unique and not self.failure_codes):
            raise ValueError("R4 freeze validity mismatch")
        return self


class GenericVisibleText(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    visible_text: str = Field(min_length=1)
    source_kind: Literal[
        "reviewed_fact_assertion",
        "reviewed_semantic_node_label",
        "reviewed_remediation_assertion",
        "reviewed_remediation_ordered_list",
        "reviewed_remediation_table_grid",
        "inherited_reviewed_fact_assertion",
    ]
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    anchor_span_ids: tuple[str, ...] = ()
    source_table_ids: tuple[str, ...] = ()
    continuation_binding_ids: tuple[str, ...] = ()
    complete_table_grid_verified: bool
    semantic_sidecar_sha256: str | None = Field(default=None, pattern=_SHA)
    semantic_node_id: str | None = None
    source_manifest_sha256s: tuple[str, ...] = ()
    crop_sha256s: tuple[str, ...] = ()
    source_text_sha256: str = Field(pattern=_SHA)
    source_display_contract_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_text(self) -> "GenericVisibleText":
        if self.source_text_sha256 != _sha256(self.visible_text.encode("utf-8")):
            raise ValueError("visible text hash does not match source text")
        provenance = (
            self.evidence_ids,
            self.anchor_span_ids,
            self.source_table_ids,
            self.continuation_binding_ids,
            self.source_manifest_sha256s,
            self.crop_sha256s,
        )
        if any(len(item) != len(set(item)) for item in provenance):
            raise ValueError("source provenance values must be unique")
        if self.source_kind == "reviewed_semantic_node_label":
            if self.anchor_span_ids or not self.semantic_sidecar_sha256 or not self.semantic_node_id:
                raise ValueError("semantic node label requires sidecar/node provenance and no text anchor")
        elif not self.anchor_span_ids:
            raise ValueError("non-diagram source text requires an anchor")
        return self


class GenericSemanticEdge(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    layout_relation: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    figure_evidence_id: str = Field(min_length=1)
    semantic_sidecar_sha256: str = Field(pattern=_SHA)
    source_manifest_sha256: str = Field(pattern=_SHA)
    source_edge_contract_sha256: str = Field(pattern=_SHA)
    layout_only: Literal[True] = True
    visible_text_authorized: Literal[False] = False


class GenericTableCell(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    text: str
    evidence_id: str = Field(min_length=1)
    source_cell_contract_sha256: str = Field(pattern=_SHA)


class GenericTableContract(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    source_table_ids: tuple[str, ...] = Field(min_length=1)
    source_text_sha256: str = Field(pattern=_SHA)
    table_state: Literal["reviewed_grid", "source_text_only"]
    table_evidence_id: str | None = None
    row_count: int | None = Field(default=None, ge=1)
    column_count: int | None = Field(default=None, ge=1)
    cells: tuple[GenericTableCell, ...] = ()
    source_grid_contract_sha256: str | None = Field(default=None, pattern=_SHA)

    @model_validator(mode="after")
    def validate_table(self) -> "GenericTableContract":
        if self.table_state == "reviewed_grid":
            if not self.table_evidence_id or not self.row_count or not self.column_count or not self.cells or not self.source_grid_contract_sha256:
                raise ValueError("reviewed table grid requires dimensions, cells and source hash")
            coords = [(cell.row, cell.column) for cell in self.cells]
            if len(coords) != len(set(coords)):
                raise ValueError("table cell coordinates must be unique")
        elif any((self.table_evidence_id, self.row_count, self.column_count, self.cells, self.source_grid_contract_sha256)):
            raise ValueError("source-text-only table cannot invent grid metadata")
        return self


class GenericOrderedListItem(StrictModel):
    ordinal: int = Field(ge=1, le=20)
    marker: str = Field(min_length=1, max_length=1)
    text: str = Field(min_length=1)
    source_item_sha256: str = Field(pattern=_SHA)


class GenericOrderedListContract(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    source_text_sha256: str = Field(pattern=_SHA)
    preamble: str = ""
    items: tuple[GenericOrderedListItem, ...] = Field(min_length=1)
    source_list_contract_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_list(self) -> "GenericOrderedListContract":
        if tuple(item.ordinal for item in self.items) != tuple(range(1, len(self.items) + 1)):
            raise ValueError("ordered-list items must remain sequential")
        if tuple(item.marker for item in self.items) != MARKERS[: len(self.items)]:
            raise ValueError("ordered-list markers must remain complete")
        return self


class GenericTokenBinding(StrictModel):
    fact_id: str = Field(pattern=_FACT)
    text_index: int = Field(ge=0)
    source_text_sha256: str = Field(pattern=_SHA)
    tokens: tuple[str, ...] = ()
    tokens_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_tokens(self) -> "GenericTokenBinding":
        expected = _canonical_hash(self.tokens)
        if self.tokens_sha256 != expected:
            raise ValueError("source token hash does not match token projection")
        return self


class GenericGeometry(StrictModel):
    width: Literal[1536] = OUTPUT_WIDTH
    height: Literal[1024] = OUTPUT_HEIGHT
    contract_kind: Literal[
        "table_grid", "ordered_list_rows", "diagram_nodes_edges", "constraint_blocks", "formula_blocks"
    ]
    element_count: int = Field(ge=1)
    reading_order: Literal["source_order"] = "source_order"
    stable_canvas: Literal[True] = True
    inferred_geometry: Literal[False] = False
    overlap_allowed: Literal[False] = False


class GenericCardContract(StrictModel):
    schema_version: Literal["step4_generic_contract_bundle_v1"] = CONTRACT_BUNDLE_VERSION
    job_id: str = Field(pattern=_JOB)
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_card_id: str = Field(pattern=_CARD)
    sample_id: str = Field(min_length=1)
    source_presentation: Literal["table", "diagram", "ordered_list", "constraint", "threshold", "formula"]
    render_presentation: Literal["table", "diagram", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    visible_texts: tuple[GenericVisibleText, ...] = Field(min_length=1)
    semantic_layout_edges: tuple[GenericSemanticEdge, ...] = ()
    diagram_node_ids: tuple[str, ...] = ()
    table_contracts: tuple[GenericTableContract, ...] = ()
    ordered_list_contracts: tuple[GenericOrderedListContract, ...] = ()
    token_bindings: tuple[GenericTokenBinding, ...] = ()
    geometry: GenericGeometry
    source_fixture_sha256: str = Field(pattern=_SHA)
    source_card_contract_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_card(self) -> "GenericCardContract":
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("Card Fact ids must be unique")
        visible_fact_ids = {item.fact_id for item in self.visible_texts}
        if visible_fact_ids != set(self.fact_ids):
            raise ValueError("visible text provenance must cover Card Facts exactly once")
        if self.render_presentation != "diagram" and len(self.visible_texts) != len(self.fact_ids):
            raise ValueError("non-diagram Cards must bind one visible text to each Fact")
        if self.source_presentation == "threshold" and self.render_presentation != "constraint":
            raise ValueError("threshold must normalize to the constraint renderer")
        if self.source_presentation != "threshold" and self.render_presentation != self.source_presentation:
            raise ValueError("source and render presentation diverged")
        if any(item.fact_id not in self.fact_ids for item in self.semantic_layout_edges):
            raise ValueError("semantic edge escaped its source Card")
        if self.render_presentation == "diagram":
            if not self.diagram_node_ids or not self.semantic_layout_edges:
                raise ValueError("diagram requires nodes and semantic layout edges")
            if len(self.diagram_node_ids) != len(set(self.diagram_node_ids)):
                raise ValueError("diagram node ids must be unique")
        elif self.diagram_node_ids or self.semantic_layout_edges:
            raise ValueError("non-diagram contract cannot carry diagram structure")
        if self.render_presentation == "table" and not self.table_contracts:
            raise ValueError("table requires a source table contract")
        if self.render_presentation != "table" and self.table_contracts:
            raise ValueError("non-table contract cannot carry table structure")
        if self.render_presentation == "ordered_list" and not self.ordered_list_contracts:
            raise ValueError("ordered-list requires a source list contract")
        if self.render_presentation != "ordered_list" and self.ordered_list_contracts:
            raise ValueError("non-list contract cannot carry list structure")
        if self.render_presentation in {"constraint", "formula"}:
            if len(self.token_bindings) != len(self.visible_texts):
                raise ValueError("constraint/formula token bindings must cover every source text")
        elif self.token_bindings:
            raise ValueError("only constraint/formula contracts may carry token bindings")
        return self


class Phase2R4ContractBundle(StrictModel):
    schema_version: Literal["step4_generic_contract_bundle_v1"] = CONTRACT_BUNDLE_VERSION
    source_unified_fixture_sha256: str = Field(pattern=_SHA)
    source_r319_handoff_sha256: str = Field(pattern=_SHA)
    cards: tuple[GenericCardContract, ...] = Field(min_length=EXPECTED_CARD_COUNT, max_length=EXPECTED_CARD_COUNT)
    card_count: Literal[16] = EXPECTED_CARD_COUNT
    fact_count: Literal[28] = EXPECTED_FACT_COUNT
    visible_text_count: Literal[52] = EXPECTED_DISPLAY_TEXT_COUNT
    semantic_edge_count: Literal[24] = EXPECTED_SEMANTIC_EDGE_COUNT


class Phase2R4ModelInput(StrictModel):
    schema_version: Literal["step4_generic_model_input_v1"] = MODEL_INPUT_VERSION
    model_input_id: str = Field(pattern=_MODEL_INPUT)
    source_unified_fixture_sha256: str = Field(pattern=_SHA)
    source_r319_handoff_sha256: str = Field(pattern=_SHA)
    card_count: Literal[16] = EXPECTED_CARD_COUNT
    fact_count: Literal[28] = EXPECTED_FACT_COUNT
    visible_text_count: Literal[52] = EXPECTED_DISPLAY_TEXT_COUNT
    semantic_edge_count: Literal[24] = EXPECTED_SEMANTIC_EDGE_COUNT
    cards: tuple[GenericCardContract, ...] = Field(min_length=EXPECTED_CARD_COUNT, max_length=EXPECTED_CARD_COUNT)

    @model_validator(mode="after")
    def validate_input(self) -> "Phase2R4ModelInput":
        if tuple(item.card_index for item in self.cards) != tuple(range(1, EXPECTED_CARD_COUNT + 1)):
            raise ValueError("model input Cards must remain ordered 1..16")
        facts = [fact_id for card in self.cards for fact_id in card.fact_ids]
        if len(facts) != EXPECTED_FACT_COUNT or len(facts) != len(set(facts)):
            raise ValueError("model input must cover 28 Facts exactly once")
        if len({card.source_card_id for card in self.cards}) != EXPECTED_CARD_COUNT:
            raise ValueError("model input must cover 16 Cards exactly once")
        if sum(len(card.visible_texts) for card in self.cards) != EXPECTED_DISPLAY_TEXT_COUNT:
            raise ValueError("model input must cover 52 source-owned display texts")
        if sum(len(card.semantic_layout_edges) for card in self.cards) != EXPECTED_SEMANTIC_EDGE_COUNT:
            raise ValueError("model input must carry 24 semantic layout edges")
        return self


class Phase2R4PromptContract(StrictModel):
    schema_version: Literal["step4_generic_prompt_contract_v1"] = PROMPT_VERSION
    instructions: tuple[str, ...] = (
        "Render only the supplied source-owned visible_text values verbatim.",
        "Use semantic layout edges for placement only; never render their relation values as visible text.",
        "Preserve the supplied table, ordered-list, diagram, constraint, and formula contracts exactly.",
        "Use a stable 1536x1024 canvas and do not infer source content or add visible text.",
        "Do not use review metadata or presentation text as a content source.",
    )
    supported_render_presentations: tuple[str, ...] = RENDER_PRESENTATIONS
    source_text_only: Literal[True] = True
    relation_values_layout_only: Literal[True] = True
    additional_visible_text_allowed: Literal[False] = False
    real_generation_authorized: Literal[False] = False


class Phase2R4Request(StrictModel):
    schema_version: Literal["step4_generic_offline_request_v1"] = REQUEST_VERSION
    request_id: str = Field(pattern=_REQUEST)
    backend: Literal["deterministic_fake_image_backend_r4_v1"] = "deterministic_fake_image_backend_r4_v1"
    model_input: Phase2R4ModelInput
    prompt_contract: Phase2R4PromptContract
    cache_version: Literal["step4_generic_cache_v1"] = CACHE_VERSION
    journal_version: Literal["step4_generic_journal_v1"] = JOURNAL_VERSION
    real_http_call_budget: Literal[0] = 0
    real_image_generation_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False


class Phase2R4FakeArtifact(StrictModel):
    schema_version: Literal["step4_generic_fake_artifact_manifest_v1"] = FAKE_ARTIFACT_VERSION
    artifact_id: str = Field(pattern=r"^step4-generic-fake-artifact:[0-9a-f]{24}$")
    job_id: str = Field(pattern=_JOB)
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_card_id: str = Field(pattern=_CARD)
    presentation: Literal["table", "diagram", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    rendered_visible_texts: tuple[str, ...] = Field(min_length=1)
    rendered_visible_text_sha256s: tuple[str, ...] = Field(min_length=1)
    rendered_table_cell_sha256s: tuple[str, ...] = ()
    rendered_list_item_sha256s: tuple[str, ...] = ()
    rendered_semantic_edge_sha256s: tuple[str, ...] = ()
    rendered_token_sha256s: tuple[str, ...] = ()
    output_filename: str = Field(pattern=_FAKE_FILENAME)
    width: Literal[1536] = OUTPUT_WIDTH
    height: Literal[1024] = OUTPUT_HEIGHT
    simulated_image_sha256: str = Field(pattern=_SHA)
    artifact_contract_sha256: str = Field(pattern=_SHA)
    materialized: Literal[False] = False
    real_image: Literal[False] = False


class Phase2R4FakeArtifactManifest(StrictModel):
    schema_version: Literal["step4_generic_fake_artifact_manifest_v1"] = FAKE_ARTIFACT_VERSION
    request_id: str = Field(pattern=_REQUEST)
    request_sha256: str = Field(pattern=_SHA)
    artifacts: tuple[Phase2R4FakeArtifact, ...] = Field(min_length=EXPECTED_CARD_COUNT, max_length=EXPECTED_CARD_COUNT)
    artifact_count: Literal[16] = EXPECTED_CARD_COUNT
    materialized_file_count: Literal[0] = 0
    real_image_count: Literal[0] = 0
    aggregate_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_manifest(self) -> "Phase2R4FakeArtifactManifest":
        if tuple(item.card_index for item in self.artifacts) != tuple(range(1, EXPECTED_CARD_COUNT + 1)):
            raise ValueError("fake artifacts must remain ordered 1..16")
        if len({item.output_filename for item in self.artifacts}) != EXPECTED_CARD_COUNT:
            raise ValueError("fake artifact filenames must be unique")
        return self


class Phase2R4CacheRecord(StrictModel):
    schema_version: Literal["step4_generic_cache_v1"] = CACHE_VERSION
    cache_key: str = Field(pattern=_SHA)
    request_id: str = Field(pattern=_REQUEST)
    request_sha256: str = Field(pattern=_SHA)
    artifact_manifest_sha256: str = Field(pattern=_SHA)
    cache_hit: Literal[False] = False
    external_cache_reads: Literal[0] = 0
    external_cache_writes: Literal[0] = 0


class Phase2R4JournalRecord(StrictModel):
    schema_version: Literal["step4_generic_journal_v1"] = JOURNAL_VERSION
    event: Literal["generic_offline_batch_compilation_completed"] = "generic_offline_batch_compilation_completed"
    request_id: str = Field(pattern=_REQUEST)
    request_sha256: str = Field(pattern=_SHA)
    gate_sha256: str = Field(pattern=_SHA)
    card_count: Literal[16] = EXPECTED_CARD_COUNT
    fact_count: Literal[28] = EXPECTED_FACT_COUNT
    network_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0


class Phase2R4BatchEntry(StrictModel):
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_card_id: str = Field(pattern=_CARD)
    sample_id: str = Field(min_length=1)
    source_presentation: str = Field(min_length=2, max_length=32)
    render_presentation: Literal["table", "diagram", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    source_owned_display_text_count: int = Field(ge=1)
    semantic_edge_count: int = Field(ge=0)
    contract_sha256: str = Field(pattern=_SHA)
    model_input_sha256: str = Field(pattern=_SHA)
    disposition: Literal["prior_reviewed_reference", "offline_compiled"]
    offline_gate_valid: Literal[True] = True
    generation_authorized: Literal[False] = False


class Phase2R4BatchManifest(StrictModel):
    schema_version: Literal["step4_generic_batch_manifest_v1"] = BATCH_VERSION
    manifest_id: str = Field(pattern=r"^step4-generic-batch:[0-9a-f]{24}$")
    source_unified_fixture_sha256: str = Field(pattern=_SHA)
    source_r319_handoff_sha256: str = Field(pattern=_SHA)
    cards: tuple[Phase2R4BatchEntry, ...] = Field(min_length=EXPECTED_CARD_COUNT, max_length=EXPECTED_CARD_COUNT)
    card_count: Literal[16] = EXPECTED_CARD_COUNT
    prior_reviewed_card_count: Literal[4] = 4
    offline_compiled_card_count: Literal[12] = 12
    batch_generation_authorized: Literal[False] = False
    real_http_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0

    @model_validator(mode="after")
    def validate_batch(self) -> "Phase2R4BatchManifest":
        if tuple(item.card_index for item in self.cards) != tuple(range(1, EXPECTED_CARD_COUNT + 1)):
            raise ValueError("batch manifest Cards must remain ordered 1..16")
        if tuple(item.card_index for item in self.cards if item.disposition == "prior_reviewed_reference") != (2, 3, 4, 6):
            raise ValueError("batch manifest prior reviewed references changed")
        if sum(item.disposition == "offline_compiled" for item in self.cards) != 12:
            raise ValueError("batch manifest must compile exactly 12 ungenerated Cards")
        if any(item.generation_authorized for item in self.cards):
            raise ValueError("batch manifest cannot authorize generation")
        return self


class Phase2R4OfflineGate(StrictModel):
    schema_version: Literal["step4_generic_batch_offline_gate_v1"] = GATE_VERSION
    valid: bool
    freeze_valid: bool
    source_fixture_valid: bool
    source_r319_handoff_valid: bool
    source_hashes_exact: bool
    card_exactly_once: bool
    fact_exactly_once: bool
    display_text_count_exact: bool
    semantic_edge_count_exact: bool
    presentation_dispatch_exact: bool
    table_contracts_complete: bool
    ordered_list_contracts_complete: bool
    diagram_contracts_complete: bool
    constraint_contracts_complete: bool
    formula_contracts_complete: bool
    source_provenance_exact: bool
    display_text_verbatim: bool
    relation_layout_only: bool
    fake_artifacts_exact: bool
    batch_manifest_exact: bool
    no_cross_sample_grouping: bool
    no_forbidden_model_keys: bool
    no_forbidden_text: bool
    no_unknown_fields: bool
    no_materialized_files: bool
    no_real_calls: bool
    generic_contract_reusable: bool
    generic_step4_offline_ready: bool
    batch_generation_authorized: Literal[False] = False
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_gate(self) -> "Phase2R4OfflineGate":
        checks = self.model_dump(
            exclude={
                "schema_version",
                "valid",
                "generic_step4_offline_ready",
                "batch_generation_authorized",
                "failure_codes",
            }
        )
        expected = all(checks.values()) and not self.failure_codes
        if self.valid != expected or self.generic_step4_offline_ready != expected:
            raise ValueError("R4 generic offline readiness must match every Gate")
        return self


class Phase2R4RegressionResult(StrictModel):
    schema_version: Literal["step4_generic_regression_result_v1"] = REGRESSION_VERSION
    case_id: str = Field(min_length=2)
    expected_failure_codes: tuple[str, ...] = Field(min_length=1)
    observed_failure_codes: tuple[str, ...] = Field(min_length=1)
    caught: bool

    @model_validator(mode="after")
    def validate_caught(self) -> "Phase2R4RegressionResult":
        if self.caught != set(self.expected_failure_codes).issubset(self.observed_failure_codes):
            raise ValueError("R4 regression result does not close its expected failure")
        return self


class Phase2R4Report(StrictModel):
    schema_version: Literal["phase2r4_step4_generic_report_v1"] = REPORT_VERSION
    phase: Literal["2R.4"] = PHASE
    generated_at_utc: str = Field(min_length=20)
    freeze_audit: Phase2R4FreezeAudit
    contract_bundle_sha256: str = Field(pattern=_SHA)
    model_input_sha256: str = Field(pattern=_SHA)
    request_sha256: str = Field(pattern=_SHA)
    fake_artifact_manifest_sha256: str = Field(pattern=_SHA)
    batch_manifest_sha256: str = Field(pattern=_SHA)
    gate: Phase2R4OfflineGate
    regressions: tuple[Phase2R4RegressionResult, ...] = Field(min_length=15, max_length=15)
    all_regressions_caught: bool
    deterministic_fake_backend_calls: Literal[1] = 1
    network_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0
    source_mutations: Literal[0] = 0
    generic_step4_offline_ready: bool
    batch_generation_authorized: Literal[False] = False
    next_required_action: str = Field(min_length=40)

    @model_validator(mode="after")
    def validate_report(self) -> "Phase2R4Report":
        if self.all_regressions_caught != all(item.caught for item in self.regressions):
            raise ValueError("R4 regression summary mismatch")
        if self.generic_step4_offline_ready != self.gate.generic_step4_offline_ready:
            raise ValueError("R4 report readiness mismatch")
        return self


@dataclass(frozen=True)
class ExpectedCounts:
    """门禁期望数量参数对象（设计 §4.3）；缺省即 gonglu 试点 v1 钉扎值。"""

    card_count: int = EXPECTED_CARD_COUNT
    fact_count: int = EXPECTED_FACT_COUNT
    display_text_count: int = EXPECTED_DISPLAY_TEXT_COUNT
    semantic_edge_count: int = EXPECTED_SEMANTIC_EDGE_COUNT
    prior_reviewed_indexes: tuple[int, ...] = (2, 3, 4, 6)


def _source_text_payload(source: Step3MergedSourceOwnedDisplayText) -> dict[str, Any]:
    return {
        "fact_id": source.fact_id,
        "text": source.text,
        "source_kind": source.source_kind,
        "evidence_ids": source.evidence_ids,
        "anchor_span_ids": source.anchor_span_ids,
        "source_table_ids": source.source_table_ids,
        "continuation_binding_ids": source.continuation_binding_ids,
        "complete_table_grid_verified": source.complete_table_grid_verified,
        "semantic_sidecar_sha256": source.semantic_sidecar_sha256,
        "semantic_node_id": source.semantic_node_id,
        "source_manifest_sha256s": source.source_manifest_sha256s,
        "crop_sha256s": source.crop_sha256s,
    }


def _source_card_payload(source: Step3MergedReviewedReleaseCard) -> dict[str, Any]:
    return {
        "card_index": source.card_index,
        "source_card_id": source.source_card_id,
        "sample_id": source.sample_id,
        "presentation": source.presentation,
        "fact_ids": source.fact_ids,
        "display_texts": [_source_text_payload(item) for item in source.display_text_provenance],
        "semantic_edges": [
            {
                "fact_id": edge.fact_id,
                "source_node_id": edge.source_node_id,
                "relation": edge.relation,
                "target_node_id": edge.target_node_id,
                "evidence_ids": edge.evidence_ids,
                "figure_evidence_id": edge.figure_evidence_id,
                "source_manifest_sha256": edge.source_manifest_sha256,
                "semantic_sidecar_sha256": edge.semantic_sidecar_sha256,
            }
            for edge in source.semantic_edge_provenance
        ],
    }


def _extract_tokens(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(TOKEN_PATTERN.findall(text)))


def _derive_visible_text(source: Step3MergedSourceOwnedDisplayText) -> GenericVisibleText:
    return GenericVisibleText(
        fact_id=source.fact_id,
        visible_text=source.text,
        source_kind=source.source_kind,
        evidence_ids=source.evidence_ids,
        anchor_span_ids=source.anchor_span_ids,
        source_table_ids=source.source_table_ids,
        continuation_binding_ids=source.continuation_binding_ids,
        complete_table_grid_verified=source.complete_table_grid_verified,
        semantic_sidecar_sha256=source.semantic_sidecar_sha256,
        semantic_node_id=source.semantic_node_id,
        source_manifest_sha256s=source.source_manifest_sha256s,
        crop_sha256s=source.crop_sha256s,
        source_text_sha256=_sha256(source.text.encode("utf-8")),
        source_display_contract_sha256=_canonical_hash(_source_text_payload(source)),
    )


def _derive_table(source: Step3MergedSourceOwnedDisplayText) -> GenericTableContract:
    grid = source.table_grid
    if grid is None:
        return GenericTableContract(
            fact_id=source.fact_id,
            source_table_ids=source.source_table_ids,
            source_text_sha256=_sha256(source.text.encode("utf-8")),
            table_state="source_text_only",
        )
    cells = tuple(
        GenericTableCell(
            row=cell.row,
            column=cell.column,
            row_span=cell.row_span,
            column_span=cell.column_span,
            text=cell.text,
            evidence_id=cell.evidence_id,
            source_cell_contract_sha256=_canonical_hash(cell),
        )
        for cell in grid.cells
    )
    return GenericTableContract(
        fact_id=source.fact_id,
        source_table_ids=source.source_table_ids,
        source_text_sha256=_sha256(source.text.encode("utf-8")),
        table_state="reviewed_grid",
        table_evidence_id=grid.table_evidence_id,
        row_count=grid.row_count,
        column_count=grid.column_count,
        cells=cells,
        source_grid_contract_sha256=_canonical_hash(grid),
    )


def _derive_list(source: Step3MergedSourceOwnedDisplayText) -> GenericOrderedListContract | None:
    marker_re = re.compile("[" + "".join(MARKERS) + "]")
    matches = list(marker_re.finditer(source.text))
    if not matches:
        return None
    preamble = source.text[: matches[0].start()].strip()
    items: list[GenericOrderedListItem] = []
    for ordinal, match in enumerate(matches, start=1):
        end = matches[ordinal].start() if ordinal < len(matches) else len(source.text)
        text = source.text[match.end() : end].strip()
        payload = {"ordinal": ordinal, "marker": match.group(0), "text": text}
        items.append(GenericOrderedListItem(**payload, source_item_sha256=_canonical_hash(payload)))
    source_contract = {
        "fact_id": source.fact_id,
        "source_text_sha256": _sha256(source.text.encode("utf-8")),
        "preamble": preamble,
        "items": [item.model_dump(mode="json") for item in items],
    }
    return GenericOrderedListContract(
        **source_contract,
        source_list_contract_sha256=_canonical_hash(source_contract),
    )


def _derive_edge(edge: Any) -> GenericSemanticEdge:
    payload = {
        "fact_id": edge.fact_id,
        "source_node_id": edge.source_node_id,
        "relation": edge.relation,
        "target_node_id": edge.target_node_id,
        "evidence_ids": edge.evidence_ids,
        "figure_evidence_id": edge.figure_evidence_id,
        "source_manifest_sha256": edge.source_manifest_sha256,
        "semantic_sidecar_sha256": edge.semantic_sidecar_sha256,
    }
    return GenericSemanticEdge(
        fact_id=edge.fact_id,
        source_node_id=edge.source_node_id,
        target_node_id=edge.target_node_id,
        layout_relation=edge.relation,
        evidence_ids=edge.evidence_ids,
        figure_evidence_id=edge.figure_evidence_id,
        semantic_sidecar_sha256=edge.semantic_sidecar_sha256,
        source_manifest_sha256=edge.source_manifest_sha256,
        source_edge_contract_sha256=_canonical_hash(payload),
    )


def _derive_card(*, source: Step3MergedReviewedReleaseCard, source_fixture_sha256: str) -> GenericCardContract:
    if source.presentation not in SOURCE_PRESENTATIONS:
        raise ValueError(f"unsupported source presentation: {source.presentation}")
    render_presentation = "constraint" if source.presentation == "threshold" else source.presentation
    visible = tuple(_derive_visible_text(item) for item in source.display_text_provenance)
    edges = tuple(_derive_edge(edge) for edge in source.semantic_edge_provenance)
    nodes = tuple(item.semantic_node_id for item in visible if item.semantic_node_id is not None)
    tables = tuple(_derive_table(item) for item in source.display_text_provenance) if render_presentation == "table" else ()
    lists = tuple(contract for item in source.display_text_provenance if (contract := _derive_list(item)) is not None) if render_presentation == "ordered_list" else ()
    tokens: tuple[GenericTokenBinding, ...] = ()
    if render_presentation in {"constraint", "formula"}:
        tokens = tuple(
            GenericTokenBinding(
                fact_id=item.fact_id,
                text_index=index,
                source_text_sha256=item.source_text_sha256,
                tokens=_extract_tokens(item.visible_text),
                tokens_sha256=_canonical_hash(_extract_tokens(item.visible_text)),
            )
            for index, item in enumerate(visible)
        )
    element_count = {
        "diagram": len(nodes),
        "table": sum(len(contract.cells) for contract in tables) or len(visible),
        "ordered_list": sum(len(contract.items) for contract in lists),
        "constraint": len(visible),
        "formula": len(visible),
    }[render_presentation]
    contract_kind = {
        "diagram": "diagram_nodes_edges",
        "table": "table_grid",
        "ordered_list": "ordered_list_rows",
        "constraint": "constraint_blocks",
        "formula": "formula_blocks",
    }[render_presentation]
    identity = {
        "card_index": source.card_index,
        "source_card_id": source.source_card_id,
        "source_fixture_sha256": source_fixture_sha256,
        "source_card_payload": _source_card_payload(source),
        "render_presentation": render_presentation,
    }
    return GenericCardContract(
        job_id=f"step4-generic-job:{_canonical_hash(identity)[:24]}",
        card_index=source.card_index,
        source_card_id=source.source_card_id,
        sample_id=source.sample_id,
        source_presentation=source.presentation,
        render_presentation=render_presentation,
        fact_ids=source.fact_ids,
        visible_texts=visible,
        semantic_layout_edges=edges,
        diagram_node_ids=nodes,
        table_contracts=tables,
        ordered_list_contracts=lists,
        token_bindings=tokens,
        geometry=GenericGeometry(
            contract_kind=contract_kind,
            element_count=element_count,
        ),
        source_fixture_sha256=source_fixture_sha256,
        source_card_contract_sha256=_canonical_hash(_source_card_payload(source)),
    )


def derive_contracts(fixture: Step3UnifiedReviewedReleaseFixture, *, unified_fixture_sha256: str, handoff_sha256: str) -> Phase2R4ContractBundle:
    """参数化改造：源 fixture/handoff 的文件哈希由调用方注入，不读盘。"""
    cards = tuple(_derive_card(source=card, source_fixture_sha256=unified_fixture_sha256) for card in fixture.cards)
    return Phase2R4ContractBundle(
        source_unified_fixture_sha256=unified_fixture_sha256,
        source_r319_handoff_sha256=handoff_sha256,
        cards=cards,
    )


def _derive_model_input(bundle: Phase2R4ContractBundle) -> Phase2R4ModelInput:
    payload = {
        "schema_version": MODEL_INPUT_VERSION,
        "source_unified_fixture_sha256": bundle.source_unified_fixture_sha256,
        "source_r319_handoff_sha256": bundle.source_r319_handoff_sha256,
        "cards": [card.model_dump(mode="json") for card in bundle.cards],
    }
    return Phase2R4ModelInput(
        model_input_id=f"step4-generic-model-input:{_canonical_hash(payload)[:24]}",
        source_unified_fixture_sha256=bundle.source_unified_fixture_sha256,
        source_r319_handoff_sha256=bundle.source_r319_handoff_sha256,
        cards=bundle.cards,
    )


def _derive_request(model_input: Phase2R4ModelInput) -> Phase2R4Request:
    prompt = Phase2R4PromptContract()
    payload = {
        "schema_version": REQUEST_VERSION,
        "model_input": model_input.model_dump(mode="json"),
        "prompt": prompt.model_dump(mode="json"),
    }
    return Phase2R4Request(
        request_id=f"step4-generic-offline-request:{_canonical_hash(payload)}",
        model_input=model_input,
        prompt_contract=prompt,
    )


def _derive_fake_artifact(card: GenericCardContract, *, filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX) -> Phase2R4FakeArtifact:
    visible = tuple(item.visible_text for item in card.visible_texts)
    table_hashes = tuple(cell.source_cell_contract_sha256 for table in card.table_contracts for cell in table.cells)
    list_hashes = tuple(item.source_item_sha256 for contract in card.ordered_list_contracts for item in contract.items)
    edge_hashes = tuple(item.source_edge_contract_sha256 for item in card.semantic_layout_edges)
    token_hashes = tuple(item.tokens_sha256 for item in card.token_bindings)
    filename = f"{filename_prefix}{card.card_index:02d}-{card.source_card_id.rsplit(':', 1)[-1][:8]}.png"
    payload = {
        "job_id": card.job_id,
        "card_index": card.card_index,
        "source_card_id": card.source_card_id,
        "presentation": card.render_presentation,
        "fact_ids": card.fact_ids,
        "rendered_visible_texts": visible,
        "rendered_visible_text_sha256s": tuple(_sha256(text.encode("utf-8")) for text in visible),
        "rendered_table_cell_sha256s": table_hashes,
        "rendered_list_item_sha256s": list_hashes,
        "rendered_semantic_edge_sha256s": edge_hashes,
        "rendered_token_sha256s": token_hashes,
        "output_filename": filename,
        "width": OUTPUT_WIDTH,
        "height": OUTPUT_HEIGHT,
    }
    artifact_id = f"step4-generic-fake-artifact:{_canonical_hash(payload)[:24]}"
    simulated = _sha256(b"phase2r4-deterministic-fake-metadata-not-an-image\0" + _json_bytes(payload))
    return Phase2R4FakeArtifact(
        artifact_id=artifact_id,
        job_id=card.job_id,
        card_index=card.card_index,
        source_card_id=card.source_card_id,
        presentation=card.render_presentation,
        fact_ids=card.fact_ids,
        rendered_visible_texts=visible,
        rendered_visible_text_sha256s=tuple(_sha256(text.encode("utf-8")) for text in visible),
        rendered_table_cell_sha256s=table_hashes,
        rendered_list_item_sha256s=list_hashes,
        rendered_semantic_edge_sha256s=edge_hashes,
        rendered_token_sha256s=token_hashes,
        output_filename=filename,
        simulated_image_sha256=simulated,
        artifact_contract_sha256=_canonical_hash({**payload, "artifact_id": artifact_id, "simulated_image_sha256": simulated}),
    )


def _derive_fake_manifest(request: Phase2R4Request, *, filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX) -> Phase2R4FakeArtifactManifest:
    artifacts = tuple(_derive_fake_artifact(card, filename_prefix=filename_prefix) for card in request.model_input.cards)
    request_sha = _canonical_hash(request)
    aggregate = _sha256(_json_bytes([(item.output_filename, item.artifact_contract_sha256) for item in artifacts]))
    return Phase2R4FakeArtifactManifest(
        request_id=request.request_id,
        request_sha256=request_sha,
        artifacts=artifacts,
        aggregate_sha256=aggregate,
    )


def _derive_batch_manifest(*, bundle: Phase2R4ContractBundle, model_input: Phase2R4ModelInput, handoff: Step4PilotHandoffRefresh) -> Phase2R4BatchManifest:
    prior_reviewed = {item.card_index for item in handoff.cards if item.disposition == "reviewed"}
    entries = tuple(
        Phase2R4BatchEntry(
            card_index=card.card_index,
            source_card_id=card.source_card_id,
            sample_id=card.sample_id,
            source_presentation=card.source_presentation,
            render_presentation=card.render_presentation,
            fact_ids=card.fact_ids,
            source_owned_display_text_count=len(card.visible_texts),
            semantic_edge_count=len(card.semantic_layout_edges),
            contract_sha256=_canonical_hash(card),
            model_input_sha256=_canonical_hash(model_input.cards[card.card_index - 1]),
            disposition="prior_reviewed_reference" if card.card_index in prior_reviewed else "offline_compiled",
        )
        for card in bundle.cards
    )
    payload = {
        "source_unified_fixture_sha256": bundle.source_unified_fixture_sha256,
        "source_r319_handoff_sha256": bundle.source_r319_handoff_sha256,
        "cards": [item.model_dump(mode="json") for item in entries],
    }
    return Phase2R4BatchManifest(
        manifest_id=f"step4-generic-batch:{_canonical_hash(payload)[:24]}",
        source_unified_fixture_sha256=bundle.source_unified_fixture_sha256,
        source_r319_handoff_sha256=bundle.source_r319_handoff_sha256,
        cards=entries,
    )


def derive_step4_bundle(
    fixture: Step3UnifiedReviewedReleaseFixture,
    handoff: Step4PilotHandoffRefresh,
    *,
    unified_fixture_sha256: str,
    handoff_sha256: str,
    filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX,
) -> tuple[Phase2R4ContractBundle, Phase2R4ModelInput, Phase2R4Request, Phase2R4FakeArtifactManifest, Phase2R4BatchManifest]:
    """纯计算入口（设计 §4.3）：已装载 fixture/handoff → 全套离线工件。

    装载与 handoff 秩序断言（reviewed inventory=(2,3,4,6) 等）是调用方职责
    （旧仓 `_read_fixture` 粘合或新流水线装载层）；本入口内 batch manifest 的
    模型校验仍会强制这些 v1 钉扎。
    """
    bundle = derive_contracts(fixture, unified_fixture_sha256=unified_fixture_sha256, handoff_sha256=handoff_sha256)
    model_input = _derive_model_input(bundle)
    request = _derive_request(model_input)
    artifacts = _derive_fake_manifest(request, filename_prefix=filename_prefix)
    batch = _derive_batch_manifest(bundle=bundle, model_input=model_input, handoff=handoff)
    return bundle, model_input, request, artifacts, batch


def _walk_mappings(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from _walk_mappings(child)


def _no_forbidden_model_keys(model_input: Phase2R4ModelInput, request: Phase2R4Request) -> bool:
    raw = {"model_input": model_input.model_dump(mode="json"), "request_model_input": request.model_input.model_dump(mode="json")}
    return not {
        key for mapping in _walk_mappings(raw) for key in mapping if key.casefold() in FORBIDDEN_MODEL_KEYS
    }


def _no_forbidden_text(model_input: Phase2R4ModelInput, artifacts: Phase2R4FakeArtifactManifest) -> bool:
    folded = json.dumps(
        {"model_input": model_input.model_dump(mode="json"), "artifacts": artifacts.model_dump(mode="json")},
        ensure_ascii=False,
    ).casefold()
    return not any(token.casefold() in folded for token in FORBIDDEN_TEXT)


def assess_offline_gate(
    *,
    fixture: Step3UnifiedReviewedReleaseFixture,
    handoff: Step4PilotHandoffRefresh,
    unified_fixture_sha256: str,
    handoff_sha256: str,
    bundle: Phase2R4ContractBundle,
    model_input: Phase2R4ModelInput,
    request: Phase2R4Request,
    artifacts: Phase2R4FakeArtifactManifest,
    batch: Phase2R4BatchManifest,
    freeze_valid: bool,
    expected: ExpectedCounts = ExpectedCounts(),
    filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX,
) -> Phase2R4OfflineGate:
    """纯计算门禁（设计 §4.3）：期望束在内存重算，freeze 结果与期望数量参数注入。

    与旧 `assess_gate` 的检查逻辑逐条一致；仅「读盘重算期望束 + freeze 审计」外壳
    改为参数（旧仓 phase 粘合负责传 `audit_frozen_history(...).valid` 与钉死数量）。
    """
    expected_bundle, expected_input, expected_request, expected_artifacts, expected_batch = derive_step4_bundle(
        fixture,
        handoff,
        unified_fixture_sha256=unified_fixture_sha256,
        handoff_sha256=handoff_sha256,
        filename_prefix=filename_prefix,
    )
    source_cards = fixture.cards
    facts = [fact_id for card in model_input.cards for fact_id in card.fact_ids]
    source_facts = [fact_id for card in source_cards for fact_id in card.fact_ids]
    source_card_ids = [card.source_card_id for card in source_cards]
    input_card_ids = [card.source_card_id for card in model_input.cards]
    rendered_texts = [text for artifact in artifacts.artifacts for text in artifact.rendered_visible_texts]
    source_texts = [text.visible_text for card in expected_input.cards for text in card.visible_texts]
    relation_values = [edge.layout_relation for card in model_input.cards for edge in card.semantic_layout_edges]
    checks = {
        "freeze_valid": freeze_valid,
        "source_fixture_valid": len(fixture.cards) == expected.card_count and fixture.reviewed_fact_count == expected.fact_count,
        "source_r319_handoff_valid": len(handoff.cards) == expected.card_count and tuple(item.card_index for item in handoff.reviewed_image_fixtures) == expected.prior_reviewed_indexes,
        "source_hashes_exact": bundle.source_unified_fixture_sha256 == unified_fixture_sha256 and bundle.source_r319_handoff_sha256 == handoff_sha256 and model_input.source_unified_fixture_sha256 == unified_fixture_sha256 and model_input.source_r319_handoff_sha256 == handoff_sha256,
        "card_exactly_once": len(model_input.cards) == expected.card_count and input_card_ids == source_card_ids and len(set(input_card_ids)) == expected.card_count,
        "fact_exactly_once": facts == source_facts and len(facts) == expected.fact_count and len(set(facts)) == expected.fact_count,
        "display_text_count_exact": sum(len(card.visible_texts) for card in model_input.cards) == expected.display_text_count and len(rendered_texts) == expected.display_text_count,
        "semantic_edge_count_exact": sum(len(card.semantic_layout_edges) for card in model_input.cards) == expected.semantic_edge_count,
        "presentation_dispatch_exact": all(card.render_presentation in RENDER_PRESENTATIONS and (card.render_presentation == ("constraint" if card.source_presentation == "threshold" else card.source_presentation)) for card in model_input.cards),
        "table_contracts_complete": all(
            (card.render_presentation == "table" and bool(card.table_contracts) and card.table_contracts == expected_input.cards[card.card_index - 1].table_contracts)
            or (card.render_presentation != "table" and not card.table_contracts)
            for card in model_input.cards
        ),
        "ordered_list_contracts_complete": all(
            (card.render_presentation == "ordered_list" and bool(card.ordered_list_contracts) and card.ordered_list_contracts == expected_input.cards[card.card_index - 1].ordered_list_contracts)
            or (card.render_presentation != "ordered_list" and not card.ordered_list_contracts)
            for card in model_input.cards
        ),
        "diagram_contracts_complete": all(
            (card.render_presentation == "diagram" and bool(card.diagram_node_ids) and bool(card.semantic_layout_edges) and card.diagram_node_ids == expected_input.cards[card.card_index - 1].diagram_node_ids and card.semantic_layout_edges == expected_input.cards[card.card_index - 1].semantic_layout_edges)
            or (card.render_presentation != "diagram" and not card.diagram_node_ids and not card.semantic_layout_edges)
            for card in model_input.cards
        ),
        "constraint_contracts_complete": all(
            (card.render_presentation == "constraint" and len(card.token_bindings) == len(card.visible_texts) and card.token_bindings == expected_input.cards[card.card_index - 1].token_bindings)
            or card.render_presentation == "formula"
            or (card.render_presentation not in {"constraint", "formula"} and not card.token_bindings)
            for card in model_input.cards
        ),
        "formula_contracts_complete": all(
            (card.render_presentation == "formula" and len(card.token_bindings) == len(card.visible_texts) and card.token_bindings == expected_input.cards[card.card_index - 1].token_bindings)
            or card.render_presentation == "constraint"
            or (card.render_presentation not in {"constraint", "formula"} and not card.token_bindings)
            for card in model_input.cards
        ),
        "source_provenance_exact": model_input == expected_input and bundle == expected_bundle,
        "display_text_verbatim": source_texts == rendered_texts and all(tuple(text.visible_text for text in card.visible_texts) == tuple(text.visible_text for text in expected_input.cards[card.card_index - 1].visible_texts) for card in model_input.cards),
        "relation_layout_only": all(edge.layout_only and not edge.visible_text_authorized for card in model_input.cards for edge in card.semantic_layout_edges) and not any(relation in rendered_texts for relation in relation_values),
        "fake_artifacts_exact": artifacts == expected_artifacts and all(artifact.width == OUTPUT_WIDTH and artifact.height == OUTPUT_HEIGHT and not artifact.materialized and not artifact.real_image for artifact in artifacts.artifacts),
        "batch_manifest_exact": batch == expected_batch,
        "no_cross_sample_grouping": all(card.sample_id == source_cards[card.card_index - 1].sample_id and card.fact_ids == source_cards[card.card_index - 1].fact_ids for card in model_input.cards),
        "no_forbidden_model_keys": _no_forbidden_model_keys(model_input, request),
        "no_forbidden_text": _no_forbidden_text(model_input, artifacts),
        "no_unknown_fields": True,
        "no_materialized_files": artifacts.materialized_file_count == 0,
        "no_real_calls": request.real_http_call_budget == 0 and request.real_image_generation_authorized is False and batch.batch_generation_authorized is False,
        "generic_contract_reusable": all(card.geometry.inferred_geometry is False and card.geometry.stable_canvas for card in model_input.cards) and set(card.render_presentation for card in model_input.cards) == set(RENDER_PRESENTATIONS),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return Phase2R4OfflineGate(
        valid=not failures,
        generic_step4_offline_ready=not failures,
        failure_codes=failures,
        **checks,
    )


def build_regression_matrix(
    *,
    fixture: Step3UnifiedReviewedReleaseFixture,
    handoff: Step4PilotHandoffRefresh,
    unified_fixture_sha256: str,
    handoff_sha256: str,
    bundle: Phase2R4ContractBundle,
    model_input: Phase2R4ModelInput,
    request: Phase2R4Request,
    artifacts: Phase2R4FakeArtifactManifest,
    batch: Phase2R4BatchManifest,
    freeze_valid: bool = True,
    expected: ExpectedCounts = ExpectedCounts(),
    filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX,
) -> tuple[Phase2R4RegressionResult, ...]:
    """15 条 fail-closed 回归（逻辑与旧仓一致；盘面参数化）。

    对 gonglu 试点 fixture 的卡位假设保持原行为：cards[5]=table、cards[2]=
    ordered_list、cards[1]=diagram、cards[13]=formula；不满足时按原实现抛错。
    """

    def result(case_id: str, changed_input: Phase2R4ModelInput = model_input, changed_request: Phase2R4Request = request, changed_artifacts: Phase2R4FakeArtifactManifest = artifacts, changed_batch: Phase2R4BatchManifest = batch, expected_codes: tuple[str, ...] = ()) -> Phase2R4RegressionResult:
        try:
            gate = assess_offline_gate(
                fixture=fixture,
                handoff=handoff,
                unified_fixture_sha256=unified_fixture_sha256,
                handoff_sha256=handoff_sha256,
                bundle=bundle,
                model_input=changed_input,
                request=changed_request,
                artifacts=changed_artifacts,
                batch=changed_batch,
                freeze_valid=freeze_valid,
                expected=expected,
                filename_prefix=filename_prefix,
            )
            observed = gate.failure_codes
        except Exception as exc:
            observed = ("validation_exception", type(exc).__name__)
        return Phase2R4RegressionResult(case_id=case_id, expected_failure_codes=expected_codes, observed_failure_codes=observed, caught=set(expected_codes).issubset(observed))

    missing_input = model_input.model_copy(update={"cards": model_input.cards[:-1]})
    duplicate_fact_card = model_input.cards[0].model_copy(update={"fact_ids": (model_input.cards[0].fact_ids[0], model_input.cards[1].fact_ids[0])})
    duplicate_fact_input = model_input.model_copy(update={"cards": (duplicate_fact_card,) + model_input.cards[1:]})
    truncated_text = model_input.cards[0].visible_texts[0].model_copy(update={"visible_text": model_input.cards[0].visible_texts[0].visible_text[:-1]})
    truncated_card = model_input.cards[0].model_copy(update={"visible_texts": (truncated_text,)})
    truncated_input = model_input.model_copy(update={"cards": (truncated_card,) + model_input.cards[1:]})
    table_card = model_input.cards[5]
    table = table_card.table_contracts[0]
    wrong_cell = table.cells[0].model_copy(update={"text": table.cells[0].text + "X"})
    wrong_table = table.model_copy(update={"cells": (wrong_cell,) + table.cells[1:]})
    wrong_table_card = table_card.model_copy(update={"table_contracts": (wrong_table,)})
    wrong_table_input = model_input.model_copy(update={"cards": model_input.cards[:5] + (wrong_table_card,) + model_input.cards[6:]})
    list_card = model_input.cards[2]
    list_contract = list_card.ordered_list_contracts[0]
    missing_list = list_contract.model_copy(update={"items": list_contract.items[:-1]})
    missing_list_card = list_card.model_copy(update={"ordered_list_contracts": (missing_list,)})
    missing_list_input = model_input.model_copy(update={"cards": model_input.cards[:2] + (missing_list_card,) + model_input.cards[3:]})
    diagram_card = model_input.cards[1]
    missing_edge_card = diagram_card.model_copy(update={"semantic_layout_edges": diagram_card.semantic_layout_edges[:-1]})
    missing_edge_input = model_input.model_copy(update={"cards": (model_input.cards[0], missing_edge_card) + model_input.cards[2:]})
    leaked = artifacts.artifacts[1].model_copy(update={"rendered_visible_texts": artifacts.artifacts[1].rendered_visible_texts + (diagram_card.semantic_layout_edges[0].layout_relation,)})
    leaked_artifacts = artifacts.model_copy(update={"artifacts": (artifacts.artifacts[0], leaked) + artifacts.artifacts[2:]})
    formula_card = model_input.cards[13]
    token_index = next(index for index, token in enumerate(formula_card.token_bindings) if token.tokens)
    token = formula_card.token_bindings[token_index].model_copy(update={"tokens": formula_card.token_bindings[token_index].tokens[:-1]})
    formula_card_changed = formula_card.model_copy(update={"token_bindings": formula_card.token_bindings[:token_index] + (token,) + formula_card.token_bindings[token_index + 1:]})
    formula_input = model_input.model_copy(update={"cards": model_input.cards[:13] + (formula_card_changed,) + model_input.cards[14:]})
    stale_input = model_input.model_copy(update={"source_unified_fixture_sha256": "0" * 64})
    crossed_card = model_input.cards[0].model_copy(update={"sample_id": model_input.cards[7].sample_id})
    crossed_input = model_input.model_copy(update={"cards": (crossed_card,) + model_input.cards[1:]})
    mixed_card = model_input.cards[3].model_copy(update={"render_presentation": "table"})
    mixed_input = model_input.model_copy(update={"cards": model_input.cards[:3] + (mixed_card,) + model_input.cards[4:]})
    extra_text = artifacts.artifacts[0].model_copy(update={"rendered_visible_texts": artifacts.artifacts[0].rendered_visible_texts + ("extra",)})
    extra_artifacts = artifacts.model_copy(update={"artifacts": (extra_text,) + artifacts.artifacts[1:]})
    upgraded_batch = batch.model_copy(update={"batch_generation_authorized": True})
    cases = (
        result("missing_card", missing_input, request.model_copy(update={"model_input": missing_input}), expected_codes=("card_exactly_once", "fact_exactly_once", "display_text_count_exact")),
        result("duplicate_fact", duplicate_fact_input, request.model_copy(update={"model_input": duplicate_fact_input}), expected_codes=("fact_exactly_once", "source_provenance_exact")),
        result("truncated_text", truncated_input, request.model_copy(update={"model_input": truncated_input}), expected_codes=("source_provenance_exact", "display_text_verbatim")),
        result("wrong_table_cell", wrong_table_input, request.model_copy(update={"model_input": wrong_table_input}), expected_codes=("source_provenance_exact", "table_contracts_complete")),
        result("missing_list_item", missing_list_input, request.model_copy(update={"model_input": missing_list_input}), expected_codes=("source_provenance_exact", "ordered_list_contracts_complete")),
        result("missing_diagram_edge", missing_edge_input, request.model_copy(update={"model_input": missing_edge_input}), expected_codes=("source_provenance_exact", "semantic_edge_count_exact", "diagram_contracts_complete")),
        result("relation_leakage", model_input, request, leaked_artifacts, expected_codes=("fake_artifacts_exact", "relation_layout_only")),
        result("formula_token_loss", formula_input, request.model_copy(update={"model_input": formula_input}), expected_codes=("source_provenance_exact", "formula_contracts_complete")),
        result("old_provenance", stale_input, request.model_copy(update={"model_input": stale_input}), expected_codes=("source_hashes_exact", "source_provenance_exact")),
        result("cross_sample_grouping", crossed_input, request.model_copy(update={"model_input": crossed_input}), expected_codes=("source_provenance_exact", "no_cross_sample_grouping")),
        result("presentation_contract_mix", mixed_input, request.model_copy(update={"model_input": mixed_input}), expected_codes=("source_provenance_exact", "presentation_dispatch_exact", "table_contracts_complete")),
        result("extra_visible_text", model_input, request, extra_artifacts, expected_codes=("fake_artifacts_exact", "display_text_verbatim")),
        result("automatic_batch_upgrade", model_input, request, artifacts, upgraded_batch, expected_codes=("batch_manifest_exact", "no_real_calls")),
    )
    raw_input = model_input.model_dump(mode="json")
    raw_input["unknown_field"] = True
    try:
        Phase2R4ModelInput.model_validate(raw_input)
        unknown_observed = ("unknown_field_accepted",)
    except Exception:
        unknown_observed = ("unknown_field_rejected",)
    unknown = Phase2R4RegressionResult(case_id="unknown_field", expected_failure_codes=("unknown_field_rejected",), observed_failure_codes=unknown_observed, caught=unknown_observed == ("unknown_field_rejected",))
    raw_forbidden = model_input.model_dump(mode="json")
    raw_forbidden["golden_title"] = "forbidden"
    try:
        Phase2R4ModelInput.model_validate(raw_forbidden)
        forbidden_observed = ("forbidden_key_accepted",)
    except Exception:
        forbidden_observed = ("forbidden_key_rejected",)
    forbidden = Phase2R4RegressionResult(case_id="forbidden_model_key", expected_failure_codes=("forbidden_key_rejected",), observed_failure_codes=forbidden_observed, caught=forbidden_observed == ("forbidden_key_rejected",))
    return cases + (unknown, forbidden)
