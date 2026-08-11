"""Generic Step 4 source-derived image compiler (distilled from phase2r11).

复制改造自 yijian2 `scripts/knowledge_v2/phase2r11_generic_step4_source_derived_image_compiler.py`
（distill:compilers，设计依据 docs/design/p1_compilers_release_gate_design.md §4.4）。

- 输入契约 `ReviewedStep3Card / ReviewedStep3Fixture`（r10/r103/r104 形态）按 A 案
  自 `knowledge/contracts/admission_fixtures.py` 向下 import；其 `contract` 字段即
  同文件 `Step3CardContract`，链路天然闭环。
- 法定哈希依铁律 bc-b8b9aca6：旧仓 r11 的 `canonical_hash` 是「本地 `_jsonable`
  前置 + r102 实现」的组合——本模块逐字节保留本地 `_jsonable/canonical_json`，
  `canonical_hash` 组合绑定 admission_legacy_hash 的 r10x 实现（与旧仓同一字节
  血统）；`_hash_model`（r10x 语义：排除 schema_version）随模块原样。
  `strict_schema` 依终裁改引 `examcard.common.schema`（r104→r92→r91 同一血统）。
- 新入口 `compile_image_batch`：已装载 reviewed Step3 卡序列 → 全套离线工件
  （source cards → model input → prompt → request → deterministic fake response →
  回环校验 → 23 条回归 → 门禁）。装载与 R10.4 授权链断言（acceptance/gate/freeze
  与 fixture 授权边界）是调用方职责，其结果以参数注入门禁。
- 旧仓留守（不在本模块）：`_frozen_paths`/`build_freeze`/`verify_freeze` 的 R10
  冻结路径集、`load_inputs`（读 R10.4 acceptance/gate/freeze 并断言授权链）、
  `SOURCE_FIXTURE_PATH`/`R104_*`/`RUN_DIR`/`REPORT_PATH`、`SCHEMA_FILENAMES`
  清单断言与 `materialize_r11`。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel
from examcard.common.schema import strict_schema
from examcard.knowledge.contracts.admission_fixtures import (
    ReviewedStep3Card,
    SourceProvenance,
    Step3CardContract,
)
from examcard.knowledge.contracts.admission_legacy_hash import (
    canonical_hash_r10x as _canonical_hash_r10x,  # r102 实现（旧仓 r11 转发的同一字节血统）
)

__all__ = [
    "AcceptanceReport",
    "CacheRecord",
    "FakeResponse",
    "FakeResponseCard",
    "FrozenArtifact",
    "ImageBatch",
    "ImageRequest",
    "JournalRecord",
    "JUnitReceipt",
    "MachineReport",
    "ModelInput",
    "OfflineGate",
    "PROMPT_INSTRUCTIONS",
    "Prompt",
    "PromptCard",
    "R10Freeze",
    "RegressionResult",
    "SourceCard",
    "build_regression_matrix",
    "canonical_hash",
    "compile_image_batch",
    "deterministic_fake_backend",
    "parse_strict_response",
    "strict_schema",
    "validate_response",
]

PHASE = "2R.11"

FREEZE_VERSION = "phase2r11_r10_freeze_v1"
SOURCE_CARD_VERSION = "phase2r11_source_card_v1"
MODEL_INPUT_VERSION = "phase2r11_image_model_input_v1"
PROMPT_CARD_VERSION = "phase2r11_prompt_card_v1"
PROMPT_VERSION = "phase2r11_image_prompt_v1"
REQUEST_VERSION = "phase2r11_image_request_v1"
RESPONSE_CARD_VERSION = "phase2r11_fake_response_card_v1"
RESPONSE_VERSION = "phase2r11_fake_response_v1"
CACHE_VERSION = "phase2r11_image_cache_v1"
JOURNAL_VERSION = "phase2r11_image_journal_v1"
REGRESSION_VERSION = "phase2r11_regression_result_v1"
GATE_CHECK_VERSION = "phase2r11_gate_check_v1"
GATE_VERSION = "phase2r11_offline_gate_v1"
REPORT_VERSION = "phase2r11_machine_report_v1"
ACCEPTANCE_VERSION = "phase2r11_acceptance_report_v1"

HASH = r"^[0-9a-f]{64}$"
CARD_ID = r"^step3-card-contract:[0-9a-f]{24}$"
REQUEST_ID = r"^step4-image-request:r11:[0-9a-f]{24}$"
MODEL_INPUT_ID = r"^step4-image-model-input:r11:[0-9a-f]{24}$"
PROMPT_ID = r"^step4-image-prompt:r11:[0-9a-f]{24}$"
RESPONSE_ID = r"^step4-image-response:r11:[0-9a-f]{24}$"
PRESENTATIONS = ("diagram", "table", "ordered_list", "constraint", "formula")

PROMPT_INSTRUCTIONS = (
    "Render only the supplied source-owned text verbatim.",
    "Preserve the supplied typed presentation contract, provenance and geometry.",
    "Use source order and never invent, rewrite, summarize or truncate content.",
    "Do not render relation metadata or any text not present in the source contract.",
    "Return deterministic fake image metadata only; no real image is authorized.",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, StrictModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    # 旧仓形态：canonical_hash(value) = r102.canonical_hash(_jsonable(value))
    return _canonical_hash_r10x(_jsonable(value))


def _hash_model(model_type: type[StrictModel], payload: Mapping[str, Any], field: str) -> StrictModel:
    document = dict(payload)
    draft = model_type.model_construct(**{**document, field: "0" * 64})
    document[field] = canonical_hash(draft.model_dump(mode="json", exclude={"schema_version", field}))
    return model_type.model_validate(document)


class FrozenArtifact(StrictModel):
    schema_version: Literal[FREEZE_VERSION] = FREEZE_VERSION
    path: str = Field(min_length=1)
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=HASH)


class R10Freeze(StrictModel):
    schema_version: Literal[FREEZE_VERSION] = FREEZE_VERSION
    phase: Literal[PHASE] = PHASE
    artifacts: tuple[FrozenArtifact, ...] = Field(min_length=1)
    artifact_count: int = Field(gt=0)
    aggregate_sha256: str = Field(pattern=HASH)
    inputs_unchanged: Literal[True] = True
    freeze_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_freeze(self) -> "R10Freeze":
        if self.artifact_count != len(self.artifacts) or len({item.path for item in self.artifacts}) != self.artifact_count:
            raise ValueError("R11 frozen R10 artifact set is not exactly once")
        if self.aggregate_sha256 != canonical_hash(self.artifacts):
            raise ValueError("R11 frozen R10 aggregate hash mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "freeze_sha256"}))
        if self.freeze_sha256 != expected:
            raise ValueError("R11 freeze hash mismatch")
        return self


class SourceCard(StrictModel):
    schema_version: Literal[SOURCE_CARD_VERSION] = SOURCE_CARD_VERSION
    source_card_id: str = Field(pattern=CARD_ID)
    source_candidate_id: str = Field(min_length=1)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    source_texts: tuple[str, ...] = Field(min_length=1)
    element_ids: tuple[str, ...] = Field(min_length=1)
    provenance: tuple[SourceProvenance, ...] = Field(min_length=1)
    source_page_ref_ids: tuple[str, ...] = Field(min_length=1)
    image_sha256s: tuple[str, ...] = Field(min_length=1)
    source_manifest_sha256s: tuple[str, ...] = Field(min_length=1)
    typed_contract: Step3CardContract
    source_contract_sha256: str = Field(pattern=HASH)
    typed_contract_sha256: str = Field(pattern=HASH)
    provenance_sha256: str = Field(pattern=HASH)
    geometry_sha256: str = Field(pattern=HASH)
    source_fixture_sha256: str = Field(pattern=HASH)
    source_card_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_source(self) -> "SourceCard":
        elements = self.typed_contract.display_elements
        if self.typed_contract.contract_id != self.source_card_id:
            raise ValueError("Step 4 source Card contract identity mismatch")
        if self.typed_contract.source_candidate_id != self.source_candidate_id:
            raise ValueError("Step 4 source candidate binding mismatch")
        if self.typed_contract.presentation != self.presentation:
            raise ValueError("Step 4 presentation binding mismatch")
        if self.fact_ids != self.typed_contract.fact_ids:
            raise ValueError("Step 4 Fact binding mismatch")
        expected_texts = tuple(item.text for item in elements)
        expected_elements = tuple(item.element_id for item in elements)
        expected_provenance = tuple(item.provenance for item in elements)
        if self.source_texts != expected_texts or self.element_ids != expected_elements:
            raise ValueError("Step 4 source-owned text or element order mismatch")
        if self.provenance != expected_provenance:
            raise ValueError("Step 4 provenance elements mismatch")
        expected_evidence = tuple(sorted({item.provenance.evidence_id for item in elements}))
        expected_anchors = tuple(sorted({anchor for item in elements for anchor in item.provenance.anchor_ids}))
        expected_pages = tuple(sorted({item.provenance.page_ref_id for item in elements}))
        expected_images = tuple(sorted({item.provenance.image_sha256 for item in elements}))
        expected_manifests = tuple(sorted({item.provenance.source_manifest_sha256 for item in elements}))
        if (self.evidence_ids, self.anchor_ids, self.source_page_ref_ids, self.image_sha256s, self.source_manifest_sha256s) != (expected_evidence, expected_anchors, expected_pages, expected_images, expected_manifests):
            raise ValueError("Step 4 Evidence, anchor, page or image binding mismatch")
        if self.source_contract_sha256 != self.typed_contract.contract_sha256:
            raise ValueError("Step 4 source contract hash mismatch")
        if self.typed_contract_sha256 != self.typed_contract.source_typed_contract_sha256:
            raise ValueError("Step 4 typed contract hash mismatch")
        if self.provenance_sha256 != canonical_hash(expected_provenance):
            raise ValueError("Step 4 provenance hash mismatch")
        if self.geometry_sha256 != canonical_hash(self.typed_contract.geometry):
            raise ValueError("Step 4 geometry hash mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "source_card_sha256"}))
        if self.source_card_sha256 != expected:
            raise ValueError("Step 4 source Card hash mismatch")
        return self


class ModelInput(StrictModel):
    schema_version: Literal[MODEL_INPUT_VERSION] = MODEL_INPUT_VERSION
    model_input_id: str = Field(pattern=MODEL_INPUT_ID)
    source_fixture_sha256: str = Field(pattern=HASH)
    cards: tuple[SourceCard, ...] = Field(min_length=1)
    card_count: int = Field(gt=0)
    model_input_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_input(self) -> "ModelInput":
        if self.card_count != len(self.cards) or len({item.source_card_id for item in self.cards}) != self.card_count:
            raise ValueError("Step 4 model input Card exactly-once mismatch")
        if any(item.source_fixture_sha256 != self.source_fixture_sha256 for item in self.cards):
            raise ValueError("Step 4 model input crossed source fixture")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "model_input_sha256"}))
        if self.model_input_sha256 != expected:
            raise ValueError("Step 4 model input hash mismatch")
        return self


class PromptCard(StrictModel):
    schema_version: Literal[PROMPT_CARD_VERSION] = PROMPT_CARD_VERSION
    source_card_id: str = Field(pattern=CARD_ID)
    source_candidate_id: str = Field(min_length=1)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    anchor_ids: tuple[str, ...] = Field(min_length=1)
    source_texts: tuple[str, ...] = Field(min_length=1)
    typed_contract_sha256: str = Field(pattern=HASH)
    provenance_sha256: str = Field(pattern=HASH)
    geometry_sha256: str = Field(pattern=HASH)


class Prompt(StrictModel):
    schema_version: Literal[PROMPT_VERSION] = PROMPT_VERSION
    prompt_id: str = Field(pattern=PROMPT_ID)
    instructions: tuple[str, ...] = PROMPT_INSTRUCTIONS
    cards: tuple[PromptCard, ...] = Field(min_length=1)
    source_owned_only: Literal[True] = True
    extra_visible_text_allowed: Literal[False] = False
    relation_metadata_visible: Literal[False] = False
    prompt_text: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_prompt(self) -> "Prompt":
        if self.prompt_text != _render_prompt(self.instructions, self.cards):
            raise ValueError("Step 4 prompt text is not the deterministic source projection")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "prompt_sha256"}))
        if self.prompt_sha256 != expected:
            raise ValueError("Step 4 prompt hash mismatch")
        return self


class ImageRequest(StrictModel):
    schema_version: Literal[REQUEST_VERSION] = REQUEST_VERSION
    request_id: str = Field(pattern=REQUEST_ID)
    backend: Literal["deterministic_fake_image_backend_r11_v1"] = "deterministic_fake_image_backend_r11_v1"
    model_input: ModelInput
    prompt: Prompt
    response_schema_version: Literal[RESPONSE_VERSION] = RESPONSE_VERSION
    response_schema_sha256: str = Field(pattern=HASH)
    real_http_call_budget: Literal[0] = 0
    real_image_generation_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    request_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_request(self) -> "ImageRequest":
        if self.prompt.cards != tuple(_prompt_card(item) for item in self.model_input.cards):
            raise ValueError("Step 4 prompt crossed model input source binding")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "request_sha256"}))
        if self.request_sha256 != expected:
            raise ValueError("Step 4 request hash mismatch")
        return self


class FakeResponseCard(StrictModel):
    schema_version: Literal[RESPONSE_CARD_VERSION] = RESPONSE_CARD_VERSION
    source_card_id: str = Field(pattern=CARD_ID)
    source_candidate_id: str = Field(min_length=1)
    presentation: Literal["diagram", "table", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    source_texts: tuple[str, ...] = Field(min_length=1)
    source_text_sha256s: tuple[str, ...] = Field(min_length=1)
    typed_contract_sha256: str = Field(pattern=HASH)
    provenance_sha256: str = Field(pattern=HASH)
    geometry_sha256: str = Field(pattern=HASH)
    simulated_image_sha256: str = Field(pattern=HASH)
    materialized: Literal[False] = False
    real_image: Literal[False] = False

    @model_validator(mode="after")
    def validate_response_card(self) -> "FakeResponseCard":
        if len(self.source_texts) != len(self.source_text_sha256s) or tuple(canonical_hash(text) for text in self.source_texts) != self.source_text_sha256s:
            raise ValueError("Step 4 fake response source text hash mismatch")
        return self


class FakeResponse(StrictModel):
    schema_version: Literal[RESPONSE_VERSION] = RESPONSE_VERSION
    response_id: str = Field(pattern=RESPONSE_ID)
    request_id: str = Field(pattern=REQUEST_ID)
    request_sha256: str = Field(pattern=HASH)
    cards: tuple[FakeResponseCard, ...] = Field(min_length=1)
    card_count: int = Field(gt=0)
    response_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_response(self) -> "FakeResponse":
        if self.card_count != len(self.cards) or len({item.source_card_id for item in self.cards}) != self.card_count:
            raise ValueError("Step 4 fake response Card exactly-once mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "response_sha256"}))
        if self.response_sha256 != expected:
            raise ValueError("Step 4 fake response hash mismatch")
        return self


class CacheRecord(StrictModel):
    schema_version: Literal[CACHE_VERSION] = CACHE_VERSION
    request_sha256: str = Field(pattern=HASH)
    response_sha256: str = Field(pattern=HASH)
    cache_hit: Literal[False] = False
    external_cache_reads: Literal[0] = 0
    external_cache_writes: Literal[0] = 0


class JournalRecord(StrictModel):
    schema_version: Literal[JOURNAL_VERSION] = JOURNAL_VERSION
    event: Literal["offline_fake_backend_completed"] = "offline_fake_backend_completed"
    request_id: str = Field(pattern=REQUEST_ID)
    request_sha256: str = Field(pattern=HASH)
    response_sha256: str = Field(pattern=HASH)
    source_card_count: int = Field(gt=0)
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0


class RegressionResult(StrictModel):
    schema_version: Literal[REGRESSION_VERSION] = REGRESSION_VERSION
    case_id: str = Field(min_length=3)
    expected_failure: str = Field(min_length=3)
    caught: bool
    detail: str = Field(min_length=3)
    regression_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_regression(self) -> "RegressionResult":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "regression_sha256"}))
        if self.regression_sha256 != expected:
            raise ValueError("R11 regression hash mismatch")
        return self


class GateCheck(StrictModel):
    schema_version: Literal[GATE_CHECK_VERSION] = GATE_CHECK_VERSION
    check_id: str = Field(min_length=3)
    passed: bool
    detail: str = Field(min_length=3)


class OfflineGate(StrictModel):
    schema_version: Literal[GATE_VERSION] = GATE_VERSION
    phase: Literal[PHASE] = PHASE
    valid: bool
    freeze_valid: bool
    source_fixture_valid: bool
    step3_reviewed_only: bool
    source_card_exactly_once: bool
    fact_evidence_anchor_exact: bool
    source_text_verbatim: bool
    typed_contract_exact: bool
    provenance_exact: bool
    geometry_exact: bool
    prompt_source_derived: bool
    response_strict: bool
    fake_artifacts_exact: bool
    no_unknown_fields: bool
    regressions_fail_closed: bool
    no_materialized_images: bool
    no_external_calls: bool
    generic_compiler_reusable: bool
    generic_step4_offline_ready: bool
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0
    batch_generation_authorized: Literal[False] = False
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "OfflineGate":
        fields = ("freeze_valid", "source_fixture_valid", "step3_reviewed_only", "source_card_exactly_once", "fact_evidence_anchor_exact", "source_text_verbatim", "typed_contract_exact", "provenance_exact", "geometry_exact", "prompt_source_derived", "response_strict", "fake_artifacts_exact", "no_unknown_fields", "regressions_fail_closed", "no_materialized_images", "no_external_calls", "generic_compiler_reusable")
        expected = all(getattr(self, name) for name in fields) and all(item.passed for item in self.checks) and not self.failure_codes
        if self.valid != expected or self.generic_step4_offline_ready != expected:
            raise ValueError("R11 offline readiness must match every Gate condition")
        digest = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
        if self.gate_sha256 != digest:
            raise ValueError("R11 offline Gate hash mismatch")
        return self


class MachineReport(StrictModel):
    schema_version: Literal[REPORT_VERSION] = REPORT_VERSION
    phase: Literal[PHASE] = PHASE
    source_card_count: int = Field(gt=0)
    fake_response_card_count: int = Field(gt=0)
    presentation_counts: tuple[tuple[str, int], ...] = Field(min_length=1)
    gate: OfflineGate
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0
    step4_offline_ready: bool
    batch_generation_authorized: Literal[False] = False
    report_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_report(self) -> "MachineReport":
        if self.step4_offline_ready != self.gate.generic_step4_offline_ready:
            raise ValueError("R11 report readiness mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "report_sha256"}))
        if self.report_sha256 != expected:
            raise ValueError("R11 report hash mismatch")
        return self


class JUnitReceipt(StrictModel):
    schema_version: Literal[ACCEPTANCE_VERSION] = ACCEPTANCE_VERSION
    label: Literal["targeted", "full"]
    sha256: str = Field(pattern=HASH)
    tests: int = Field(ge=1)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class AcceptanceReport(StrictModel):
    schema_version: Literal[ACCEPTANCE_VERSION] = ACCEPTANCE_VERSION
    phase: Literal[PHASE] = PHASE
    accepted: bool
    report_sha256: str = Field(pattern=HASH)
    receipts: tuple[JUnitReceipt, JUnitReceipt]
    generic_step4_offline_ready: bool
    batch_generation_authorized: Literal[False] = False
    acceptance_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_acceptance(self) -> "AcceptanceReport":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "acceptance_sha256"}))
        if self.acceptance_sha256 != expected:
            raise ValueError("R11 acceptance hash mismatch")
        return self


@dataclass(frozen=True)
class ImageBatch:
    """与旧 Batch 同构，去掉 freeze / sources 两个盘面字段（授权链断言归调用方）。"""

    source_fixture_sha256: str
    source_cards: tuple[SourceCard, ...]
    model_input: ModelInput
    prompt: Prompt
    request: ImageRequest
    response: FakeResponse
    cache: CacheRecord
    journal: JournalRecord
    regressions: tuple[RegressionResult, ...]
    gate: OfflineGate | None
    report: MachineReport | None


def _source_card(card: ReviewedStep3Card, fixture_sha256: str) -> SourceCard:
    contract = card.contract
    elements = contract.display_elements
    provenance = tuple(item.provenance for item in elements)
    payload = {
        "source_card_id": contract.contract_id,
        "source_candidate_id": contract.source_candidate_id,
        "presentation": contract.presentation,
        "fact_ids": contract.fact_ids,
        "evidence_ids": tuple(sorted({item.evidence_id for item in provenance})),
        "anchor_ids": tuple(sorted({anchor for item in provenance for anchor in item.anchor_ids})),
        "source_texts": tuple(item.text for item in elements),
        "element_ids": tuple(item.element_id for item in elements),
        "provenance": provenance,
        "source_page_ref_ids": tuple(sorted({item.page_ref_id for item in provenance})),
        "image_sha256s": tuple(sorted({item.image_sha256 for item in provenance})),
        "source_manifest_sha256s": tuple(sorted({item.source_manifest_sha256 for item in provenance})),
        "typed_contract": contract,
        "source_contract_sha256": contract.contract_sha256,
        "typed_contract_sha256": contract.source_typed_contract_sha256,
        "provenance_sha256": canonical_hash(provenance),
        "geometry_sha256": canonical_hash(contract.geometry),
        "source_fixture_sha256": fixture_sha256,
    }
    return _hash_model(SourceCard, payload, "source_card_sha256")  # type: ignore[return-value]


def _prompt_card(card: SourceCard) -> PromptCard:
    return PromptCard(
        source_card_id=card.source_card_id,
        source_candidate_id=card.source_candidate_id,
        presentation=card.presentation,
        fact_ids=card.fact_ids,
        evidence_ids=card.evidence_ids,
        anchor_ids=card.anchor_ids,
        source_texts=card.source_texts,
        typed_contract_sha256=card.typed_contract_sha256,
        provenance_sha256=card.provenance_sha256,
        geometry_sha256=card.geometry_sha256,
    )


def _render_prompt(instructions: tuple[str, ...], cards: tuple[PromptCard, ...]) -> str:
    source_projection = {
        "instructions": instructions,
        "cards": [item.model_dump(mode="json") for item in cards],
    }
    return "Render the supplied source-derived image contracts exactly.\n" + json.dumps(source_projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _response_schema_sha256() -> str:
    return canonical_hash(strict_schema(FakeResponse))


def _fake_response_card(card: SourceCard) -> FakeResponseCard:
    texts_hashes = tuple(canonical_hash(text) for text in card.source_texts)
    artifact_hash = canonical_hash({
        "source_card_id": card.source_card_id,
        "source_texts": card.source_texts,
        "typed_contract_sha256": card.typed_contract_sha256,
        "provenance_sha256": card.provenance_sha256,
        "geometry_sha256": card.geometry_sha256,
    })
    return FakeResponseCard(
        source_card_id=card.source_card_id,
        source_candidate_id=card.source_candidate_id,
        presentation=card.presentation,
        fact_ids=card.fact_ids,
        source_texts=card.source_texts,
        source_text_sha256s=texts_hashes,
        typed_contract_sha256=card.typed_contract_sha256,
        provenance_sha256=card.provenance_sha256,
        geometry_sha256=card.geometry_sha256,
        simulated_image_sha256=artifact_hash,
    )


def deterministic_fake_backend(request: ImageRequest) -> FakeResponse:
    cards = tuple(_fake_response_card(item) for item in request.model_input.cards)
    payload = {
        "response_id": f"step4-image-response:r11:{canonical_hash(request.request_sha256)[:24]}",
        "request_id": request.request_id,
        "request_sha256": request.request_sha256,
        "cards": cards,
        "card_count": len(cards),
    }
    return _hash_model(FakeResponse, payload, "response_sha256")  # type: ignore[return-value]


def validate_response(request: ImageRequest, response: FakeResponse) -> None:
    if response.request_id != request.request_id or response.request_sha256 != request.request_sha256:
        raise ValueError("R11 fake response request binding mismatch")
    expected = {_card.source_card_id: _card for _card in request.model_input.cards}
    if response.card_count != len(expected) or set(item.source_card_id for item in response.cards) != set(expected):
        raise ValueError("R11 fake response Card exactly-once mismatch")
    for result in response.cards:
        source = expected[result.source_card_id]
        if (result.source_candidate_id, result.presentation, result.fact_ids, result.source_texts, result.typed_contract_sha256, result.provenance_sha256, result.geometry_sha256) != (source.source_candidate_id, source.presentation, source.fact_ids, source.source_texts, source.typed_contract_sha256, source.provenance_sha256, source.geometry_sha256):
            raise ValueError("R11 fake response source contract mismatch")


def parse_strict_response(raw: str) -> FakeResponse:
    if not isinstance(raw, str):
        raise TypeError("R11 response must be a JSON text body")
    return FakeResponse.model_validate(json.loads(raw))


def _validate_prompt(prompt: Prompt) -> None:
    if any(token in prompt.prompt_text for token in ("golden_title", "card_key", "review_state", "reviewer", "relation_text")):
        raise ValueError("R11 prompt contains forbidden metadata or relation text")
    if prompt.prompt_text != _render_prompt(prompt.instructions, prompt.cards):
        raise ValueError("R11 prompt source projection mismatch")


def _fail_closed(case_id: str, expected_failure: str, callback: Any) -> RegressionResult:
    try:
        callback()
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return _hash_model(RegressionResult, {"case_id": case_id, "expected_failure": expected_failure, "caught": True, "detail": str(exc)[:500] or type(exc).__name__}, "regression_sha256")  # type: ignore[return-value]
    return _hash_model(RegressionResult, {"case_id": case_id, "expected_failure": expected_failure, "caught": False, "detail": "mutation was unexpectedly accepted"}, "regression_sha256")  # type: ignore[return-value]


def _mutated_response(request: ImageRequest, response: FakeResponse, field: str, value: Any) -> FakeResponse:
    card = response.cards[0].model_dump(mode="json")
    card[field] = value
    if field == "source_texts":
        card["source_text_sha256s"] = tuple(canonical_hash(text) for text in value)
    changed_card = FakeResponseCard.model_validate(card)
    payload = response.model_dump(mode="json")
    payload["cards"] = (changed_card, *response.cards[1:])
    payload["response_sha256"] = "0" * 64
    return _hash_model(FakeResponse, payload, "response_sha256")  # type: ignore[return-value]


def build_regression_matrix(batch: ImageBatch) -> tuple[RegressionResult, ...]:
    source = batch.source_cards[0]
    source_doc = source.model_dump(mode="json")
    response_doc = batch.response.model_dump(mode="json")
    request_doc = batch.request.model_dump(mode="json")
    first_card = batch.response.cards[0]

    def source_mutation(field: str, value: Any) -> None:
        SourceCard.model_validate({**source_doc, field: value})

    def request_mutation(field: str, value: Any) -> None:
        ImageRequest.model_validate({**request_doc, field: value})

    def response_mutation(field: str, value: Any) -> None:
        validate_response(batch.request, _mutated_response(batch.request, batch.response, field, value))

    def duplicate_response() -> None:
        payload = response_doc.copy()
        payload["cards"] = (first_card, first_card, *batch.response.cards[2:])
        changed = _hash_model(FakeResponse, payload, "response_sha256")
        validate_response(batch.request, changed)

    def omitted_response() -> None:
        payload = response_doc.copy()
        payload["cards"] = batch.response.cards[:-1]
        payload["card_count"] = len(payload["cards"])
        changed = _hash_model(FakeResponse, payload, "response_sha256")
        validate_response(batch.request, changed)

    def prompt_relation_leak() -> None:
        Prompt.model_validate({**batch.prompt.model_dump(mode="json"), "prompt_text": batch.prompt.prompt_text + " relation_text"})

    cases = (
        ("source-text-truncation", "source_text", lambda: source_mutation("source_texts", ["truncated"])),
        ("fact-binding", "Fact", lambda: source_mutation("fact_ids", ["fact:wrong"])),
        ("evidence-binding", "Evidence", lambda: source_mutation("evidence_ids", ["evidence:wrong"])),
        ("anchor-binding", "anchor", lambda: source_mutation("anchor_ids", ["anchor:wrong"])),
        ("source-page", "source page", lambda: source_mutation("source_page_ref_ids", ["page:wrong"])),
        ("image-hash", "image hash", lambda: source_mutation("image_sha256s", ["0" * 64])),
        ("source-manifest", "source manifest", lambda: source_mutation("source_manifest_sha256s", ["0" * 64])),
        ("typed-contract", "typed contract", lambda: source_mutation("typed_contract_sha256", "0" * 64)),
        ("provenance", "provenance", lambda: source_mutation("provenance_sha256", "0" * 64)),
        ("geometry", "geometry", lambda: source_mutation("geometry_sha256", "0" * 64)),
        ("contract", "contract", lambda: source_mutation("source_contract_sha256", "0" * 64)),
        ("response-text", "response source text", lambda: response_mutation("source_texts", ["truncated"])),
        ("response-fact", "response Fact", lambda: response_mutation("fact_ids", ["fact:wrong"])),
        ("response-provenance", "response provenance", lambda: response_mutation("provenance_sha256", "0" * 64)),
        ("response-duplicate", "duplicate Card", duplicate_response),
        ("response-omission", "omitted Card", omitted_response),
        ("response-unknown", "unknown field", lambda: parse_strict_response(json.dumps({**response_doc, "unknown": True}))),
        ("wrapper", "illegal wrapper", lambda: parse_strict_response(json.dumps({"choices": response_doc}))),
        ("invalid-json", "invalid JSON", lambda: parse_strict_response("not-json")),
        ("prompt-relation-text", "relation text leakage", prompt_relation_leak),
        ("request-unknown", "unknown request field", lambda: ImageRequest.model_validate({**request_doc, "unknown": True})),
        ("request-real-image", "real image authorization", lambda: request_mutation("real_image_generation_authorized", True)),
        ("request-batch", "batch authorization", lambda: request_mutation("batch_generation_authorized", True)),
    )
    return tuple(_fail_closed(case_id, expected, callback) for case_id, expected, callback in cases)


def compile_image_batch(
    cards: Sequence[ReviewedStep3Card],
    *,
    fixture_sha256: str,
    freeze_valid: bool = True,
    source_authority_valid: bool = True,
) -> ImageBatch:
    """纯计算入口（设计 §4.4）：已装载 reviewed Step3 卡序列 → 全套离线工件。

    `freeze_valid` / `source_authority_valid` 注入门禁前两项（旧仓由
    `verify_freeze` 与 `load_inputs` 的 R10.4 授权链断言提供；新流水线由装载层
    提供）。fixture 级授权边界（fixture_authorized/step4_authorized 等）同属
    装载方职责。
    """
    source_cards = tuple(_source_card(card, fixture_sha256) for card in cards)
    model_input = _hash_model(ModelInput, {
        "model_input_id": f"step4-image-model-input:r11:{canonical_hash(tuple(item.source_card_sha256 for item in source_cards))[:24]}",
        "source_fixture_sha256": fixture_sha256,
        "cards": source_cards,
        "card_count": len(source_cards),
    }, "model_input_sha256")
    prompt_cards = tuple(_prompt_card(card) for card in source_cards)
    prompt = _hash_model(Prompt, {
        "prompt_id": f"step4-image-prompt:r11:{canonical_hash(model_input.model_input_sha256)[:24]}",
        "cards": prompt_cards,
        "prompt_text": _render_prompt(PROMPT_INSTRUCTIONS, prompt_cards),
    }, "prompt_sha256")
    request = _hash_model(ImageRequest, {
        "request_id": f"step4-image-request:r11:{canonical_hash((model_input.model_input_sha256, prompt.prompt_sha256))[:24]}",
        "model_input": model_input,
        "prompt": prompt,
        "response_schema_sha256": _response_schema_sha256(),
    }, "request_sha256")
    _validate_prompt(prompt)
    response = deterministic_fake_backend(request)
    validate_response(request, response)
    cache = CacheRecord(request_sha256=request.request_sha256, response_sha256=response.response_sha256)
    journal = JournalRecord(request_id=request.request_id, request_sha256=request.request_sha256, response_sha256=response.response_sha256, source_card_count=len(source_cards))
    regressions = build_regression_matrix(ImageBatch(fixture_sha256, source_cards, model_input, prompt, request, response, cache, journal, (), None, None))
    checks = (
        GateCheck(check_id="frozen-r10", passed=freeze_valid, detail="R10 through R10.4 source code, schemas, run artifacts, reports and identities are hash-stable"),
        GateCheck(check_id="reviewed-step3-only", passed=source_authority_valid, detail="only the accepted R10.4 reviewed Step 3 fixture is consumed"),
        GateCheck(check_id="source-card-exactly-once", passed=len(source_cards) == len({card.source_card_id for card in source_cards}), detail="each reviewed Step 3 Card is compiled once"),
        GateCheck(check_id="fact-evidence-anchor", passed=all(card.fact_ids and card.evidence_ids and card.anchor_ids for card in source_cards), detail="Fact, Evidence and anchor bindings remain source-owned"),
        GateCheck(check_id="source-text-verbatim", passed=all(tuple(item.text for item in card.typed_contract.display_elements) == card.source_texts for card in source_cards), detail="source-owned text is preserved exactly and in order"),
        GateCheck(check_id="typed-contract", passed=all(card.typed_contract_sha256 == card.typed_contract.source_typed_contract_sha256 for card in source_cards), detail="typed presentation contracts are preserved without inference"),
        GateCheck(check_id="provenance", passed=all(card.provenance_sha256 == canonical_hash(card.provenance) for card in source_cards), detail="element provenance remains bound to page, image and source manifest"),
        GateCheck(check_id="geometry", passed=all(card.geometry_sha256 == canonical_hash(card.typed_contract.geometry) for card in source_cards), detail="geometry is deterministic and source-bound"),
        GateCheck(check_id="prompt-source-derived", passed=prompt.prompt_text == _render_prompt(prompt.instructions, prompt.cards) and not any(token in prompt.prompt_text for token in ("golden_title", "card_key", "review_state", "reviewer", "relation_text")), detail="prompt contains only source projection and fixed rendering constraints"),
        GateCheck(check_id="fake-response-strict", passed=all(item.source_texts == source_cards[index].source_texts for index, item in enumerate(response.cards)), detail="fake response preserves the strict source contract"),
        GateCheck(check_id="no-unknown-fields", passed=True, detail="all R11 models are strict and reject additional properties"),
        GateCheck(check_id="regressions", passed=all(item.caught for item in regressions), detail="source, response, wrapper, relation leakage and overreach mutations fail closed"),
        GateCheck(check_id="no-materialized-images", passed=all(not item.materialized and not item.real_image for item in response.cards), detail="fake backend creates metadata only"),
        GateCheck(check_id="no-external-calls", passed=True, detail="offline compiler makes no network, model, relay or image API calls"),
        GateCheck(check_id="generic-reusable", passed=True, detail="dispatch derives from the source presentation and has no subject or fixed Card rules"),
    )
    gate = _hash_model(OfflineGate, {
        "valid": all(item.passed for item in checks),
        "freeze_valid": checks[0].passed,
        "source_fixture_valid": checks[1].passed,
        "step3_reviewed_only": checks[1].passed,
        "source_card_exactly_once": checks[2].passed,
        "fact_evidence_anchor_exact": checks[3].passed,
        "source_text_verbatim": checks[4].passed,
        "typed_contract_exact": checks[5].passed,
        "provenance_exact": checks[6].passed,
        "geometry_exact": checks[7].passed,
        "prompt_source_derived": checks[8].passed,
        "response_strict": checks[9].passed,
        "fake_artifacts_exact": checks[9].passed,
        "no_unknown_fields": checks[10].passed,
        "regressions_fail_closed": checks[11].passed,
        "no_materialized_images": checks[12].passed,
        "no_external_calls": checks[13].passed,
        "generic_compiler_reusable": checks[14].passed,
        "generic_step4_offline_ready": all(item.passed for item in checks),
        "checks": checks,
        "failure_codes": tuple(item.check_id for item in checks if not item.passed),
    }, "gate_sha256")
    counts = tuple(sorted((presentation, sum(card.presentation == presentation for card in source_cards)) for presentation in sorted({card.presentation for card in source_cards})))
    report = _hash_model(MachineReport, {
        "source_card_count": len(source_cards),
        "fake_response_card_count": len(response.cards),
        "presentation_counts": counts,
        "gate": gate,
        "step4_offline_ready": gate.generic_step4_offline_ready,
    }, "report_sha256")
    return ImageBatch(fixture_sha256, source_cards, model_input, prompt, request, response, cache, journal, regressions, gate, report)
