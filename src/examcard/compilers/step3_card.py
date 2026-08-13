"""Generic Step 3 source-derived card compiler (distilled from phase2r9).

复制改造自 yijian2 `scripts/knowledge_v2/phase2r9_generic_step3_card_compiler.py`
（distill:compilers，设计依据 docs/design/p1_compilers_release_gate_design.md §4.1）。

- 契约族（SourceProvenance…Step3CardContract、FakeArtifact）已按总指挥 A 案下沉
  `knowledge/contracts/admission_fixtures.py`，本模块向下 import。
- 法定哈希依铁律 bc-b8b9aca6 绑定 `knowledge/contracts/admission_legacy_hash.py`
  的 r9/r10x 变体（旧仓 r10/r92/r98 即 import r9 同一实现；跨变体输出等价已由
  真产物黄金重放实证）。`_hash_model` 为 r9 模块自有 builder，逐字节原样带走。
- 批工件模型（ModelCard/Step3ModelInput/DraftBundle/BatchManifest/OfflineGate…）
  与纯函数原样带走；schema_version Literal 与 ID 前缀逐字节保真（§6 红线）。
- 旧仓留守（不在本模块）：RUN_DIR/SOURCE_FIXTURE_PATH/REPORT_PATH、prior-freeze
  构建与重验（phase 冻结链粘合）、`_render_report`/`materialize_r9`。
- 新入口 `compile_reviewed_fixture`：已装载 fixture → 全套离线工件，纯计算不读盘。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel
from examcard.knowledge.contracts.admission_fixtures import (
    DRAFT_VERSION,
    DiagramEdgeRef,
    DiagramNodeRef,
    DisplayElement,
    FakeArtifact,
    GeometryConstraints,
    LayoutContract,
    ListItemRef,
    ReviewedTypedEvidenceFixture,
    SourceProvenance,
    Step3CardContract,
    TableCellRef,
    TokenRef,
)
from examcard.knowledge.contracts.admission_legacy_hash import (
    canonical_hash_r10x as canonical_hash,  # r9/r10x 变体（r9 同源；铁律 bc-b8b9aca6）
)

__all__ = [
    "Artifacts",
    "BATCH_VERSION",
    "BatchManifest",
    "DISPLAY_ROLES",
    "DraftBundle",
    "FORBIDDEN_KEYS",
    "GATE_VERSION",
    "GateCheck",
    "MODEL_ID",
    "MODEL_INPUT_VERSION",
    "ModelCard",
    "ModelLayout",
    "ModelSourceBinding",
    "ModelVisibleElement",
    "OfflineGate",
    "OfflineRequestPlan",
    "PHASE",
    "PRESENTATIONS",
    "PROMPT_VERSION",
    "PresentationCounts",
    "PromptContract",
    "REGRESSION_VERSION",
    "REPORT_VERSION",
    "REQUEST_ID",
    "REQUEST_VERSION",
    "Readiness",
    "RegressionResult",
    "Report",
    "Step3Compilation",
    "Step3ModelInput",
    "build_regression_matrix",
    "compile_candidate",
    "compile_reviewed_fixture",
]

PHASE = "2R.9"
SOURCE_FIXTURE_VERSION = "phase2r810_reviewed_typed_evidence_fixture_v1"
MODEL_INPUT_VERSION = "phase2r9_step3_model_input_v1"
PROMPT_VERSION = "phase2r9_step3_prompt_contract_v1"
REQUEST_VERSION = "phase2r9_step3_offline_request_v1"
BATCH_VERSION = "phase2r9_step3_batch_manifest_v1"
GATE_VERSION = "phase2r9_step3_offline_gate_v1"
REGRESSION_VERSION = "phase2r9_step3_regression_result_v1"
REPORT_VERSION = "phase2r9_step3_report_v1"

PRESENTATIONS = ("diagram", "table", "ordered_list", "constraint", "formula")
DISPLAY_ROLES = ("diagram_node", "table_cell", "ordered_list_item", "constraint_token", "formula_token")
HASH = r"^[0-9a-f]{64}$"
ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"
SOURCE_CANDIDATE = r"^candidate:[a-z0-9][a-z0-9._:-]{2,255}$"
MODEL_ID = r"^step3-model-input:[0-9a-f]{24}$"
REQUEST_ID = r"^step3-offline-request:[0-9a-f]{64}$"

FORBIDDEN_KEYS = frozenset(
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


def _hash_model(model_type: type[StrictModel], payload: dict[str, Any], field: str) -> StrictModel:
    # r9 自有 builder，逐字节原样（不排除 schema_version；r9 的调用载荷不含该键）
    payload = dict(payload)
    payload[field] = canonical_hash({key: value for key, value in payload.items() if key != field})
    return model_type.model_validate(payload)


class ModelSourceBinding(StrictModel):
    schema_version: Literal["phase2r9_model_source_binding_v1"] = "phase2r9_model_source_binding_v1"
    subject_id: str = Field(pattern=ID)
    record_id: str = Field(pattern=ID)
    fact_id: str = Field(pattern=ID)
    evidence_id: str = Field(pattern=ID)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    page_ref_id: str = Field(pattern=ID)
    page: int = Field(ge=1)
    image_sha256: str = Field(pattern=HASH)
    source_manifest_sha256: str = Field(pattern=HASH)


class ModelVisibleElement(StrictModel):
    schema_version: Literal["phase2r9_model_visible_element_v1"] = "phase2r9_model_visible_element_v1"
    element_id: str = Field(pattern=ID)
    source_text: str = Field(min_length=1)
    source_order: int = Field(ge=1)


class ModelLayout(StrictModel):
    schema_version: Literal["phase2r9_model_layout_v1"] = "phase2r9_model_layout_v1"
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    typed_contract_sha256: str = Field(pattern=HASH)
    element_ids: tuple[str, ...] = Field(min_length=1)
    table_rows: int | None = Field(default=None, ge=1)
    table_columns: int | None = Field(default=None, ge=1)
    table_coordinates: tuple[TableCellRef, ...] = ()
    list_order: tuple[ListItemRef, ...] = ()
    token_order: tuple[TokenRef, ...] = ()
    diagram_nodes: tuple[DiagramNodeRef, ...] = ()
    diagram_edges: tuple[DiagramEdgeRef, ...] = ()

    @model_validator(mode="after")
    def validate_layout(self) -> "ModelLayout":
        refs: tuple[str, ...]
        if self.presentation == "table":
            refs = tuple(item.element_id for item in self.table_coordinates)
        elif self.presentation == "ordered_list":
            refs = tuple(item.element_id for item in self.list_order)
        elif self.presentation in {"formula", "constraint"}:
            refs = tuple(item.element_id for item in self.token_order)
        else:
            refs = tuple(item.element_id for item in self.diagram_nodes)
        if refs != self.element_ids:
            raise ValueError("model layout element order mismatch")
        return self


class ModelCard(StrictModel):
    schema_version: Literal[MODEL_INPUT_VERSION] = MODEL_INPUT_VERSION
    source_fixture_sha256: str = Field(pattern=HASH)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    source_binding: ModelSourceBinding
    visible_elements: tuple[ModelVisibleElement, ...] = Field(min_length=1)
    layout: ModelLayout
    geometry: GeometryConstraints

    @model_validator(mode="after")
    def validate_model_card(self) -> "ModelCard":
        if self.layout.presentation != self.presentation:
            raise ValueError("model presentation mismatch")
        if tuple(item.element_id for item in self.visible_elements) != self.layout.element_ids:
            raise ValueError("model visible elements must match layout exactly once")
        return self


class Step3ModelInput(StrictModel):
    schema_version: Literal[MODEL_INPUT_VERSION] = MODEL_INPUT_VERSION
    model_input_id: str = Field(pattern=MODEL_ID)
    source_fixture_sha256: str = Field(pattern=HASH)
    cards: tuple[ModelCard, ...] = Field(min_length=1)
    card_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_input(self) -> "Step3ModelInput":
        if self.card_count != len(self.cards):
            raise ValueError("model input card count mismatch")
        if any(card.source_fixture_sha256 != self.source_fixture_sha256 for card in self.cards):
            raise ValueError("model input source fixture mismatch")
        expected_id = f"step3-model-input:{canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "model_input_id"}))[:24]}"
        if self.model_input_id != expected_id:
            raise ValueError("model input identity mismatch")
        return self


class PromptContract(StrictModel):
    schema_version: Literal[PROMPT_VERSION] = PROMPT_VERSION
    prompt_id: str = Field(pattern=r"^step3-prompt:[0-9a-f]{24}$")
    source_fixture_sha256: str = Field(pattern=HASH)
    allowed_presentations: tuple[Literal["diagram", "table", "ordered_list", "constraint", "formula"], ...] = PRESENTATIONS
    render_policy: tuple[str, ...] = (
        "render_only_supplied_source_text",
        "preserve_source_order_and_typed_geometry",
        "use_relations_only_as_layout_provenance",
        "emit_no_additional_text_or_branding",
    )
    prompt_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_prompt(self) -> "PromptContract":
        if any(key in self.prompt_text for key in FORBIDDEN_KEYS):
            raise ValueError("prompt contains forbidden model input key")
        expected = f"step3-prompt:{canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "prompt_id"}))[:24]}"
        if self.prompt_id != expected:
            raise ValueError("prompt identity mismatch")
        return self


class OfflineRequestPlan(StrictModel):
    schema_version: Literal[REQUEST_VERSION] = REQUEST_VERSION
    request_id: str = Field(pattern=REQUEST_ID)
    model_input_id: str = Field(pattern=MODEL_ID)
    prompt_id: str = Field(pattern=r"^step3-prompt:[0-9a-f]{24}$")
    backend: Literal["deterministic_fake"] = "deterministic_fake"
    network_call_budget: Literal[0] = 0
    model_call_budget: Literal[0] = 0
    relay_call_budget: Literal[0] = 0
    image_api_call_budget: Literal[0] = 0


class DraftBundle(StrictModel):
    schema_version: Literal[DRAFT_VERSION] = DRAFT_VERSION
    source_fixture_sha256: str = Field(pattern=HASH)
    artifacts: tuple[FakeArtifact, ...] = Field(min_length=1)
    artifact_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_bundle(self) -> "DraftBundle":
        if self.artifact_count != len(self.artifacts):
            raise ValueError("draft artifact count mismatch")
        return self


class PresentationCounts(StrictModel):
    schema_version: Literal["phase2r9_presentation_counts_v1"] = "phase2r9_presentation_counts_v1"
    diagram: int = Field(ge=0)
    table: int = Field(ge=0)
    ordered_list: int = Field(ge=0)
    constraint: int = Field(ge=0)
    formula: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.diagram + self.table + self.ordered_list + self.constraint + self.formula


class BatchManifest(StrictModel):
    schema_version: Literal[BATCH_VERSION] = BATCH_VERSION
    phase: Literal[PHASE] = PHASE
    source_fixture_sha256: str = Field(pattern=HASH)
    contract_ids: tuple[str, ...] = Field(min_length=1)
    card_contract_count: int = Field(ge=1)
    presentation_counts: PresentationCounts
    not_applicable_presentations: tuple[Literal["diagram", "table", "ordered_list", "constraint", "formula"], ...]
    batch_generation_authorized: Literal[False] = False
    production_cards_created: Literal[False] = False
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0

    @model_validator(mode="after")
    def validate_batch(self) -> "BatchManifest":
        if self.card_contract_count != len(self.contract_ids):
            raise ValueError("batch contract count mismatch")
        if len(self.contract_ids) != len(set(self.contract_ids)):
            raise ValueError("batch contract IDs must be unique")
        if self.presentation_counts.total != self.card_contract_count:
            raise ValueError("presentation count mismatch")
        if set(self.not_applicable_presentations) & set(
            presentation for presentation, count in self.presentation_counts.model_dump(mode="json").items() if presentation != "schema_version" and count > 0
        ):
            raise ValueError("presentations with contracts cannot be not_applicable")
        return self


class GateCheck(StrictModel):
    schema_version: Literal["phase2r9_gate_check_v1"] = "phase2r9_gate_check_v1"
    check_id: str = Field(pattern=ID)
    passed: bool
    detail: str = Field(min_length=1)


class OfflineGate(StrictModel):
    schema_version: Literal[GATE_VERSION] = GATE_VERSION
    phase: Literal[PHASE] = PHASE
    valid: bool
    source_fixture_authorized: bool
    source_contracts_valid: bool
    provenance_valid: bool
    source_text_exactly_once: bool
    model_input_clean: bool
    deterministic_draft_valid: bool
    batch_generation_authorized: Literal[False] = False
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "OfflineGate":
        expected = all(item.passed for item in self.checks) and not self.failure_codes
        if self.valid != expected:
            raise ValueError("R9 offline gate validity mismatch")
        if self.valid and not all((self.source_fixture_authorized, self.source_contracts_valid, self.provenance_valid, self.source_text_exactly_once, self.model_input_clean, self.deterministic_draft_valid)):
            raise ValueError("R9 valid gate has a failed aggregate check")
        if self.gate_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"})):
            raise ValueError("R9 gate hash mismatch")
        return self


class RegressionResult(StrictModel):
    schema_version: Literal[REGRESSION_VERSION] = REGRESSION_VERSION
    case_id: str = Field(pattern=ID)
    expected_failure_code: str = Field(pattern=ID)
    caught: bool
    detail: str = Field(min_length=1)
    regression_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_regression(self) -> "RegressionResult":
        if self.regression_sha256 != canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "regression_sha256"})):
            raise ValueError("R9 regression hash mismatch")
        return self


class Readiness(StrictModel):
    schema_version: Literal["phase2r9_readiness_v1"] = "phase2r9_readiness_v1"
    phase: Literal[PHASE] = PHASE
    valid: bool
    generic_step3_offline_ready: bool
    card_contract_count: int = Field(ge=1)
    supported_presentations: tuple[Literal["diagram", "table", "ordered_list", "constraint", "formula"], ...] = PRESENTATIONS
    not_applicable_presentations: tuple[Literal["diagram", "table", "ordered_list", "constraint", "formula"], ...]
    batch_generation_authorized: Literal[False] = False
    production_cards_created: Literal[False] = False
    readiness_sha256: str = Field(pattern=HASH)


class Artifacts(StrictModel):
    schema_version: Literal["phase2r9_artifacts_v1"] = "phase2r9_artifacts_v1"
    phase: Literal[PHASE] = PHASE
    source_fixture_sha256: str = Field(pattern=HASH)
    source_mutations: Literal[0] = 0
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    production_cards_created: Literal[False] = False
    step3_sent: Literal[False] = False
    step4_authorized: Literal[False] = False
    artifacts_sha256: str = Field(pattern=HASH)


class Report(StrictModel):
    schema_version: Literal[REPORT_VERSION] = REPORT_VERSION
    phase: Literal[PHASE] = PHASE
    disposition: Literal["accepted_offline"] = "accepted_offline"
    source_fixture_sha256: str = Field(pattern=HASH)
    card_contract_count: int = Field(ge=1)
    presentation_counts: PresentationCounts
    not_applicable_presentations: tuple[str, ...]
    source_text_count: int = Field(ge=1)
    source_text_exactly_once: bool
    provenance_gate: bool
    generic_step3_offline_ready: bool
    batch_generation_authorized: Literal[False] = False
    production_cards_created: Literal[False] = False
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    regression_count: int = Field(ge=1)
    regression_caught_count: int = Field(ge=0)
    test_summary: str = "not_recorded"
    targeted_junit_sha256: str = "not_recorded"
    full_junit_sha256: str = "not_recorded"
    report_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_report(self) -> "Report":
        if self.regression_caught_count > self.regression_count:
            raise ValueError("R9 regression count mismatch")
        return self


@dataclass(frozen=True)
class Step3Compilation:
    """与旧 Compilation 同构，去掉 root / source_fixture_path / freeze 三个盘面字段。"""

    source_fixture_sha256: str
    source_fixture: ReviewedTypedEvidenceFixture
    contracts: tuple[Step3CardContract, ...]
    model_input: Step3ModelInput
    prompt: PromptContract
    request: OfflineRequestPlan
    draft: DraftBundle
    batch: BatchManifest
    regressions: tuple[RegressionResult, ...]
    gate: OfflineGate
    readiness: Readiness
    artifacts: Artifacts
    report: Report


def _provenance(raw: dict[str, Any]) -> SourceProvenance:
    return SourceProvenance.model_validate(
        {
            "subject_id": raw["subject_id"],
            "record_id": raw["record_id"],
            "fact_id": raw["fact_id"],
            "evidence_id": raw["evidence_id"],
            "anchor_ids": tuple(raw["anchor_ids"]),
            "page_ref_id": raw["page_ref_id"],
            "page": raw["page"],
            "image_sha256": raw["image_sha256"],
            "source_manifest_sha256": raw["source_manifest_sha256"],
        }
    )


def _display(raw: dict[str, Any], role: str, order: int) -> DisplayElement:
    provenance = _provenance(raw["provenance"])
    return DisplayElement(
        element_id=raw["element_id"],
        text=raw["text"],
        source_role=role,
        source_order=order,
        provenance=provenance,
        text_sha256=raw["text_sha256"],
    )


def _layout_from_contract(raw: dict[str, Any]) -> tuple[tuple[DisplayElement, ...], LayoutContract]:
    presentation = raw["presentation"]
    if presentation == "table":
        cells = tuple(_display(cell, "table_cell", cell["reading_order"]) for cell in raw.get("cells", ()))
        refs = tuple(TableCellRef.model_validate({key: cell[key] for key in ("element_id", "row", "column", "row_span", "column_span", "reading_order")}) for cell in raw["cells"])
        return cells, LayoutContract(presentation=presentation, table_rows=raw["rows"], table_columns=raw["columns"], table_cells=refs)
    if presentation == "ordered_list":
        items = tuple(_display(item, "ordered_list_item", item["order"]) for item in raw.get("items", ()))
        refs = tuple(ListItemRef.model_validate({"element_id": item["element_id"], "order": item["order"], "marker": item["marker"]}) for item in raw["items"])
        return items, LayoutContract(presentation=presentation, list_items=refs)
    if presentation in {"constraint", "formula"}:
        role = "constraint_token" if presentation == "constraint" else "formula_token"
        tokens = tuple(_display(token, role, token["token_order"]) for token in raw.get("tokens", ()))
        refs = tuple(TokenRef.model_validate({"element_id": token["element_id"], "token_order": token["token_order"]}) for token in raw["tokens"])
        if presentation == "constraint":
            return tokens, LayoutContract(presentation=presentation, constraint_blocks=refs)
        return tokens, LayoutContract(presentation=presentation, token_refs=refs)
    if presentation == "diagram":
        nodes = tuple(_display(node, "diagram_node", node["reading_order"]) for node in raw.get("nodes", ()))
        node_refs = tuple(DiagramNodeRef(element_id=node["element_id"], reading_order=node["reading_order"]) for node in raw["nodes"])
        edge_refs = tuple(
            DiagramEdgeRef(
                edge_id=edge["edge_id"],
                source_node_id=edge["source_node_id"],
                target_node_id=edge["target_node_id"],
                provenance=_provenance(edge["provenance"]),
            )
            for edge in raw.get("edges", ())
        )
        return nodes, LayoutContract(presentation=presentation, diagram_nodes=node_refs, diagram_edges=edge_refs)
    raise ValueError(f"unsupported typed presentation: {presentation}")


def compile_candidate(candidate: Any, source_fixture_sha256: str) -> Step3CardContract:
    raw = candidate.typed_contract.model_dump(mode="json")
    elements, layout = _layout_from_contract(raw)
    if not elements:
        raise ValueError("typed contract has no source-owned display elements")
    expected_binding = {
        "subject_id": candidate.subject_id,
        "record_id": candidate.record_id,
        "fact_id": candidate.fact_id,
        "evidence_id": candidate.evidence_id,
        "source_manifest_sha256": candidate.source_manifest_sha256,
    }
    for element in elements:
        provenance = element.provenance
        if any(getattr(provenance, key) != value for key, value in expected_binding.items()):
            raise ValueError("typed element crossed source binding")
        if tuple(provenance.anchor_ids) != tuple(candidate.anchor_ids):
            raise ValueError("typed element crossed source anchor binding")
    for edge in layout.diagram_edges:
        if edge.provenance.fact_id != candidate.fact_id or edge.provenance.evidence_id != candidate.evidence_id:
            raise ValueError("diagram edge crossed source binding")
    base = {
        "contract_id": "step3-card-contract:" + "0" * 24,
        "source_candidate_id": candidate.candidate_id,
        "fact_ids": (candidate.fact_id,),
        "presentation": candidate.presentation,
        "source_fixture_sha256": source_fixture_sha256,
        "source_typed_contract_sha256": candidate.typed_contract_sha256,
        "display_elements": elements,
        "layout": layout,
        "geometry": GeometryConstraints(),
    }
    base["contract_id"] = f"step3-card-contract:{canonical_hash({key: value for key, value in base.items() if key != "contract_id"})[:24]}"
    return Step3CardContract(**base, contract_sha256=canonical_hash(base))


def _model_card(contract: Step3CardContract) -> ModelCard:
    first = contract.display_elements[0].provenance
    binding = ModelSourceBinding(
        subject_id=first.subject_id,
        record_id=first.record_id,
        fact_id=first.fact_id,
        evidence_id=first.evidence_id,
        anchor_ids=first.anchor_ids,
        page_ref_id=first.page_ref_id,
        page=first.page,
        image_sha256=first.image_sha256,
        source_manifest_sha256=first.source_manifest_sha256,
    )
    visible = tuple(
        ModelVisibleElement(element_id=item.element_id, source_text=item.text, source_order=item.source_order)
        for item in contract.display_elements
    )
    layout = contract.layout
    model_layout = ModelLayout(
        presentation=contract.presentation,
        typed_contract_sha256=contract.source_typed_contract_sha256,
        element_ids=tuple(item.element_id for item in visible),
        table_rows=layout.table_rows,
        table_columns=layout.table_columns,
        table_coordinates=layout.table_cells,
        list_order=layout.list_items,
        token_order=layout.token_refs or layout.constraint_blocks,
        diagram_nodes=layout.diagram_nodes,
        diagram_edges=layout.diagram_edges,
    )
    return ModelCard(
        source_fixture_sha256=contract.source_fixture_sha256,
        presentation=contract.presentation,
        source_binding=binding,
        visible_elements=visible,
        layout=model_layout,
        geometry=contract.geometry,
    )


def _make_model_input(contracts: tuple[Step3CardContract, ...], source_fixture_sha256: str) -> Step3ModelInput:
    base = {"model_input_id": "step3-model-input:" + "0" * 24, "source_fixture_sha256": source_fixture_sha256, "cards": tuple(_model_card(contract) for contract in contracts), "card_count": len(contracts)}
    base["model_input_id"] = f"step3-model-input:{canonical_hash({key: value for key, value in base.items() if key != "model_input_id"})[:24]}"
    return Step3ModelInput(**base)


def _make_prompt(source_fixture_sha256: str) -> PromptContract:
    base = {
        "prompt_id": "step3-prompt:" + "0" * 24,
        "source_fixture_sha256": source_fixture_sha256,
        "allowed_presentations": PRESENTATIONS,
        "render_policy": (
            "render_only_supplied_source_text",
            "preserve_source_order_and_typed_geometry",
            "use_relations_only_as_layout_provenance",
            "emit_no_additional_text_or_branding",
        ),
        "prompt_text": "Render each supplied source-owned text exactly once using only its typed presentation geometry and provenance. Preserve source order. Semantic relations may affect layout only and must never become visible text. Emit no additional text, title, branding, watermark, or evidence-only content.",
    }
    base["prompt_id"] = f"step3-prompt:{canonical_hash({key: value for key, value in base.items() if key != "prompt_id"})[:24]}"
    return PromptContract(**base)


def _make_request(model_input: Step3ModelInput, prompt: PromptContract) -> OfflineRequestPlan:
    base = {"request_id": "step3-offline-request:" + "0" * 64, "model_input_id": model_input.model_input_id, "prompt_id": prompt.prompt_id}
    base["request_id"] = f"step3-offline-request:{canonical_hash({key: value for key, value in base.items() if key != "request_id"})}"
    return OfflineRequestPlan(**base)


def _make_draft(contracts: tuple[Step3CardContract, ...], source_fixture_sha256: str) -> DraftBundle:
    artifacts: list[FakeArtifact] = []
    for contract in contracts:
        base = {
            "contract_id": contract.contract_id,
            "presentation": contract.presentation,
            "rendered_element_ids": tuple(item.element_id for item in contract.display_elements),
            "rendered_source_texts": tuple(item.text for item in contract.display_elements),
            "relation_texts": (),
            "extra_texts": (),
            "materialized": False,
            "real_image": False,
        }
        artifacts.append(FakeArtifact(**base, artifact_sha256=canonical_hash(base)))
    return DraftBundle(source_fixture_sha256=source_fixture_sha256, artifacts=tuple(artifacts), artifact_count=len(artifacts))


def _presentation_counts(contracts: Sequence[Step3CardContract]) -> PresentationCounts:
    counts = {presentation: 0 for presentation in PRESENTATIONS}
    for contract in contracts:
        counts[contract.presentation] += 1
    return PresentationCounts(**counts)


def _make_batch(contracts: tuple[Step3CardContract, ...], source_fixture_sha256: str) -> BatchManifest:
    counts = _presentation_counts(contracts)
    return BatchManifest(
        source_fixture_sha256=source_fixture_sha256,
        contract_ids=tuple(contract.contract_id for contract in contracts),
        card_contract_count=len(contracts),
        presentation_counts=counts,
        not_applicable_presentations=tuple(presentation for presentation, count in counts.model_dump(mode="json").items() if presentation != "schema_version" and count == 0),
    )


def _fail_closed(case_id: str, expected: str, callback: Any) -> RegressionResult:
    caught = False
    detail = "unexpectedly accepted"
    try:
        callback()
    except (ValueError, TypeError, KeyError) as exc:
        caught = True
        detail = str(exc)[:500] or type(exc).__name__
    payload = {"case_id": case_id, "expected_failure_code": expected, "caught": caught, "detail": detail}
    return RegressionResult(**payload, regression_sha256=canonical_hash(payload))


def build_regression_matrix(contracts: tuple[Step3CardContract, ...], model_input: Step3ModelInput, draft: DraftBundle, batch: BatchManifest) -> tuple[RegressionResult, ...]:
    """fail-closed 回归矩阵，呈现族感知（五族齐备批与旧仓 phase2r9 十五条逐字节一致）。

    十一条通用探针恒定发射；四条「族适用」探针仅在批内存在可被真实变异的目标时
    发射，否则跳过（不发射行，而非记假 caught）：

    - `wrong-table-cell` / `missing-list-item` / `formula-token-loss`：需批内存在
      对应呈现族契约。旧实现缺族时回退 `contracts[0]`——对空 `list_items` 删项、
      对空 `token_refs` 清空均为无操作变异，探针被「unexpectedly accepted」（族缺
      席假阳，P8 彩排 B1 三卡批复合 valid 即因此失败；独立验证记录级更正
      aaef1e2/9dc5f89）；缺 table 族时更在构造变异载荷处抛 IndexError。
    - `presentation-contract-mix`：改用首个非 table 契约做混族变异（`contracts[0]`
      为 table 时旧实现强改 presentation="table" 是恒等变异，同类假阳）；全 table
      批无混族可造，跳过。

    r810 试点 fixture 五族齐备且首卡为 diagram，四条族探针全部发射且载荷不变，
    矩阵输出与旧仓逐字节一致（migration/verify_compilers_parity 对拍面不变）。
    对 fixture 内容的既有假设保持原行为：`contracts[0]` 必须存在（空 fixture 在
    compile 阶段即拒绝）。
    """
    baseline = contracts[0]
    model_raw = model_input.cards[0].model_dump(mode="json")
    draft_raw = draft.artifacts[0].model_dump(mode="json")
    batch_raw = batch.model_dump(mode="json")
    duplicate_raw = baseline.model_dump(mode="json")
    duplicate_raw["display_elements"] = [*duplicate_raw["display_elements"], duplicate_raw["display_elements"][0]]
    results = [
        _fail_closed("unknown-field", "unknown-field-rejected", lambda: ModelCard.model_validate({**model_raw, "unknown_field": True})),
        _fail_closed("forbidden-model-key", "forbidden-key-rejected", lambda: ModelCard.model_validate({**model_raw, "card_key": "x"})),
        _fail_closed("truncated-source-text", "source-text-hash-rejected", lambda: DisplayElement.model_validate({**baseline.display_elements[0].model_dump(mode="json"), "text": baseline.display_elements[0].text[:-1]})),
        _fail_closed("duplicate-element", "duplicate-element-rejected", lambda: Step3CardContract.model_validate(duplicate_raw)),
    ]
    table = next((item for item in contracts if item.presentation == "table"), None)
    if table is not None:
        table_raw = table.model_dump(mode="json")
        table_layout = table.layout.model_dump(mode="json")
        table_layout["table_cells"] = [
            {**table.layout.table_cells[0].model_dump(mode="json"), "element_id": "wrong:cell"},
            *[item.model_dump(mode="json") for item in table.layout.table_cells[1:]],
        ]
        table_raw["layout"] = table_layout
        results.append(_fail_closed("wrong-table-cell", "table-binding-rejected", lambda: Step3CardContract.model_validate(table_raw)))
    ordered_list = next((item for item in contracts if item.presentation == "ordered_list"), None)
    if ordered_list is not None:
        list_raw = ordered_list.model_dump(mode="json")
        list_layout = ordered_list.layout.model_dump(mode="json")
        list_layout["list_items"] = [item.model_dump(mode="json") for item in ordered_list.layout.list_items[:-1]]
        list_raw["layout"] = list_layout
        results.append(_fail_closed("missing-list-item", "list-binding-rejected", lambda: Step3CardContract.model_validate(list_raw)))
    formula = next((item for item in contracts if item.presentation == "formula"), None)
    if formula is not None:
        formula_raw = formula.model_dump(mode="json")
        formula_layout = formula.layout.model_dump(mode="json")
        formula_layout["token_refs"] = []
        formula_raw["layout"] = formula_layout
        results.append(_fail_closed("formula-token-loss", "formula-binding-rejected", lambda: Step3CardContract.model_validate(formula_raw)))
    results.append(_fail_closed("relation-leakage", "relation-text-rejected", lambda: FakeArtifact.model_validate({**draft_raw, "relation_texts": ["display relation"]})))
    results.append(_fail_closed("cross-sample-grouping", "cross-source-rejected", lambda: Step3CardContract.model_validate({**baseline.model_dump(mode="json"), "source_candidate_id": "candidate:cross-sample"})))
    mix_source = next((item for item in contracts if item.presentation != "table"), None)
    if mix_source is not None:
        mix_raw = {**mix_source.layout.model_dump(mode="json"), "presentation": "table"}
        results.append(_fail_closed("presentation-contract-mix", "presentation-mix-rejected", lambda: LayoutContract.model_validate(mix_raw)))
    results.extend(
        [
            _fail_closed("extra-visible-text", "extra-text-rejected", lambda: FakeArtifact.model_validate({**draft_raw, "extra_texts": ["extra"]})),
            _fail_closed("old-provenance", "source-fixture-rejected", lambda: Step3CardContract.model_validate({**baseline.model_dump(mode="json"), "source_fixture_sha256": "0" * 64})),
            _fail_closed("batch-upgrade", "batch-generation-rejected", lambda: BatchManifest.model_validate({**batch_raw, "batch_generation_authorized": True})),
            _fail_closed("production-card", "production-boundary-rejected", lambda: BatchManifest.model_validate({**batch_raw, "production_cards_created": True})),
            _fail_closed("model-input-mutation", "model-input-identity-rejected", lambda: Step3ModelInput.model_validate({**model_input.model_dump(mode="json"), "model_input_id": "step3-model-input:" + "0" * 24})),
        ]
    )
    return tuple(results)


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in FORBIDDEN_KEYS or _contains_forbidden(item) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return any(_contains_forbidden(item) for item in value)
    return False


def _make_gate(contracts: tuple[Step3CardContract, ...], model_input: Step3ModelInput, draft: DraftBundle, batch: BatchManifest, regressions: tuple[RegressionResult, ...], source_fixture_authorized: bool, provenance_valid: bool) -> OfflineGate:
    checks = (
        GateCheck(check_id="source-fixture-authorized", passed=source_fixture_authorized, detail="R8.10 reviewed typed Evidence fixture is authorized and downstream-disabled"),
        GateCheck(check_id="typed-contracts-valid", passed=bool(contracts), detail="all available typed presentations compile through one generic contract"),
        GateCheck(check_id="provenance-exact", passed=provenance_valid, detail="Fact, Evidence, anchor, page, image and source manifest bindings are preserved"),
        GateCheck(check_id="source-text-exactly-once", passed=all(len({item.element_id for item in contract.display_elements}) == len(contract.display_elements) for contract in contracts), detail="source-owned text elements are unique and source ordered"),
        GateCheck(check_id="model-input-clean", passed=not _contains_forbidden(model_input.model_dump(mode="json")), detail="model input has no forbidden review or golden fields"),
        GateCheck(check_id="deterministic-draft-exact", passed=all(tuple(artifact.rendered_element_ids) == tuple(item.element_id for item in contract.display_elements) and tuple(artifact.rendered_source_texts) == tuple(item.text for item in contract.display_elements) for artifact, contract in zip(draft.artifacts, contracts)), detail="fake draft exactly mirrors the source contract"),
        GateCheck(check_id="five-type-dispatch", passed=set(batch.not_applicable_presentations) | {item.presentation for item in contracts} == set(PRESENTATIONS), detail="five presentation families have explicit supported or not_applicable disposition"),
        GateCheck(check_id="fail-closed-regressions", passed=bool(regressions) and all(item.caught for item in regressions), detail="unknown, provenance, layout, relation and downstream mutations fail closed"),
        GateCheck(check_id="downstream-disabled", passed=not batch.batch_generation_authorized and not batch.production_cards_created and batch.network_calls == 0, detail="offline batch does not authorize production Cards, Step 3 transport or Step 4"),
    )
    payload = {"source_fixture_authorized": source_fixture_authorized, "source_contracts_valid": bool(contracts), "provenance_valid": provenance_valid, "source_text_exactly_once": checks[3].passed, "model_input_clean": checks[4].passed, "deterministic_draft_valid": checks[5].passed, "batch_generation_authorized": False, "checks": checks, "failure_codes": tuple(item.check_id for item in checks if not item.passed)}
    return OfflineGate(**payload, phase=PHASE, valid=all(item.passed for item in checks), gate_sha256=canonical_hash({**payload, "phase": PHASE, "valid": all(item.passed for item in checks)}))


def compile_reviewed_fixture(fixture: ReviewedTypedEvidenceFixture, *, source_fixture_sha256: str) -> Step3Compilation:
    """纯计算入口：已装载 fixture → 全套离线工件。不读盘、不做 prior freeze。

    `source_fixture_sha256` 与旧仓 `build_batch` 的 `file_hash(source_path)` 同语义
    （fixture 落盘文件的 SHA-256）；装载与冻结链校验是调用方（旧仓 phase 粘合或
    新流水线装载层）的职责。
    """
    if not fixture.fixture_authorized or fixture.cards_created or fixture.step3_authorized or fixture.step4_authorized:
        raise ValueError("R8.10 fixture is not an offline-only reviewed source fixture")
    contracts = tuple(compile_candidate(candidate, source_fixture_sha256) for candidate in fixture.candidates)
    if len({contract.source_candidate_id for contract in contracts}) != len(contracts):
        raise ValueError("source candidates must compile exactly once")
    model_input = _make_model_input(contracts, source_fixture_sha256)
    prompt = _make_prompt(source_fixture_sha256)
    request = _make_request(model_input, prompt)
    draft = _make_draft(contracts, source_fixture_sha256)
    batch = _make_batch(contracts, source_fixture_sha256)
    provenance_valid = all(
        all(item.provenance.fact_id in contract.fact_ids and item.provenance.evidence_id for item in contract.display_elements)
        for contract in contracts
    )
    regressions = build_regression_matrix(contracts, model_input, draft, batch)
    gate = _make_gate(contracts, model_input, draft, batch, regressions, True, provenance_valid)
    readiness_payload = {
        "phase": PHASE,
        "valid": gate.valid,
        "generic_step3_offline_ready": gate.valid,
        "card_contract_count": len(contracts),
        "not_applicable_presentations": batch.not_applicable_presentations,
        "batch_generation_authorized": False,
        "production_cards_created": False,
    }
    readiness = Readiness(**readiness_payload, readiness_sha256=canonical_hash(readiness_payload))
    artifact_payload = {
        "phase": PHASE,
        "source_fixture_sha256": source_fixture_sha256,
        "source_mutations": 0,
        "network_calls": 0,
        "model_calls": 0,
        "relay_calls": 0,
        "image_api_calls": 0,
        "production_cards_created": False,
        "step3_sent": False,
        "step4_authorized": False,
    }
    artifacts = Artifacts(**artifact_payload, artifacts_sha256=canonical_hash(artifact_payload))
    report_payload = {
        "phase": PHASE,
        "source_fixture_sha256": source_fixture_sha256,
        "card_contract_count": len(contracts),
        "presentation_counts": _presentation_counts(contracts),
        "not_applicable_presentations": batch.not_applicable_presentations,
        "source_text_count": sum(len(contract.display_elements) for contract in contracts),
        "source_text_exactly_once": gate.source_text_exactly_once,
        "provenance_gate": gate.provenance_valid,
        "generic_step3_offline_ready": readiness.generic_step3_offline_ready,
        "batch_generation_authorized": False,
        "production_cards_created": False,
        "network_calls": 0,
        "model_calls": 0,
        "relay_calls": 0,
        "image_api_calls": 0,
        "regression_count": len(regressions),
        "regression_caught_count": sum(item.caught for item in regressions),
    }
    report = _hash_model(Report, report_payload, "report_sha256")  # type: ignore[assignment]
    return Step3Compilation(
        source_fixture_sha256=source_fixture_sha256,
        source_fixture=fixture,
        contracts=contracts,
        model_input=model_input,
        prompt=prompt,
        request=request,
        draft=draft,
        batch=batch,
        regressions=regressions,
        gate=gate,
        readiness=readiness,
        artifacts=artifacts,
        report=report,
    )
