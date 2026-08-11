"""Strict Step 3 response contract (distilled from phase2r92).

复制改造自 yijian2 `scripts/knowledge_v2/phase2r92_generic_step3_response_contract.py`
（distill:compilers，设计依据 docs/design/p1_compilers_release_gate_design.md §4.2）。

- 「模型响应」被限定为字节级精确的 OpenAI 信封（choices→message→content 为字符串
  化 JSON 文档）；`inspect_response` 逐层校验并产出 12 项 `ResponseGate`；
  `build_regression_cases` 生成 17 个变异响应字节串。本模块是 providers 兼容层
  （r93–r97 家族）的共同底座，`make_inner_inspector` 即其 §6.3 约定的回调适配器。
- 法定哈希依铁律 bc-b8b9aca6：`canonical_hash` 绑定 admission_legacy_hash 的
  r9/r10x 变体（r92 旧仓即 import r9 同一实现）；r92 模块自带的局部实现
  （`_jsonable`/`canonical_json`/`sha256_*`/`_hash_model`/`_collect_keys`）逐字节
  原样带走。`strict_schema` 依终裁改引 `examcard.common.schema`（同一 r91 血统）。
- v1 契约保真：`ResponseModelInput`/`ResponseDocument` 的三代表卡钉扎
  （card_count==3、(table, ordered_list, formula) 固定序）是已落盘 v1 契约的一部分，
  **原样保留**；`build_response_model_input` 的参数化只解除「读 R91 冻结运行」的
  盘面钉死（改收已装载卡对象），卡数/表达族不限的诉求属 v2 新版本号范畴（§6 红线）。
- 旧仓留守（不在本模块）：CHECKPOINT 冻结链（git 依赖）、R91 失败证据装载、
  `materialize_offline`/`finalize_acceptance` 与全部 RUN_DIR/报告路径。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from examcard.materials.schema import StrictModel
from examcard.common.schema import strict_schema
from examcard.compilers.step3_card import (
    GeometryConstraints,
    ModelLayout,
    ModelSourceBinding,
    ModelVisibleElement,
)
from examcard.knowledge.contracts.admission_legacy_hash import (
    canonical_hash_r10x as canonical_hash,  # r9/r10x 变体（r92 旧仓即 import r9 实现）
)

__all__ = [
    "AcceptanceReport",
    "CHECKPOINT_COMMIT",
    "FORBIDDEN_KEYS",
    "GateCheck",
    "JUnitReceipt",
    "MachineReport",
    "OfflineGate",
    "PRESENTATIONS",
    "PromptContract",
    "RequestIdentity",
    "ResponseChoice",
    "ResponseDocument",
    "ResponseDraftCard",
    "ResponseEnvelope",
    "ResponseInputCard",
    "ResponseInspection",
    "ResponseGate",
    "ResponseMessage",
    "ResponseModelInput",
    "build_fake_result",
    "build_prompt",
    "build_regression_cases",
    "build_request",
    "build_response_model_input",
    "canonical_json",
    "encode_response",
    "inspect_response",
    "make_inner_inspector",
    "sha256_bytes",
    "sha256_text",
    "strict_schema",
]

PHASE = "2R.9.2"
# 历史机器报告的数据格式标识（参与既有 report_sha256）；仅作字符串常量，无 git 依赖。
CHECKPOINT_COMMIT = "b73d261e06c878da46a05509f9154379b1437bf6"
PRESENTATIONS = ("table", "ordered_list", "formula")

INPUT_VERSION = "phase2r92_response_model_input_v1"
INPUT_CARD_VERSION = "phase2r92_response_input_card_v1"
PROMPT_VERSION = "phase2r92_response_prompt_v1"
ENVELOPE_VERSION = "phase2r92_response_envelope_v1"
MESSAGE_VERSION = "phase2r92_response_message_v1"
CHOICE_VERSION = "phase2r92_response_choice_v1"
RESULT_VERSION = "phase2r92_step3_result_v1"
RESULT_CARD_VERSION = "phase2r92_step3_draft_card_v1"
GATE_VERSION = "phase2r92_response_gate_v1"
OFFLINE_GATE_VERSION = "phase2r92_offline_gate_v1"
REPORT_VERSION = "phase2r92_machine_report_v1"
ACCEPTANCE_VERSION = "phase2r92_acceptance_report_v1"
REQUEST_VERSION = "phase2r92_offline_request_v1"

HASH = r"^[0-9a-f]{64}$"
ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"
CONTRACT_ID = r"^step3-card-contract:[0-9a-f]{24}$"
FORBIDDEN_KEYS = frozenset(
    {
        "golden_title",
        "canonical_title",
        "card_key",
        "review_state",
        "review_basis",
        "reviewer",
        "manual_decision",
        "unknown_field",
        "image_prompt",
        "layout_description",
        "evidence_only",
    }
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
    # r92 模块自带局部实现，逐字节原样（用于信封字节构造；与 canonical_hash 的
    # r9 血统实现并存，禁互换——铁律 bc-b8b9aca6 同源警示）
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _hash_model(model_type: type[StrictModel], payload: dict[str, Any], field: str) -> StrictModel:
    document = dict(payload)
    draft = model_type.model_construct(**{**document, field: "0" * 64})
    document[field] = canonical_hash(draft.model_dump(mode="json", exclude={"schema_version", field}))
    return model_type.model_validate(document)


def _collect_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_collect_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_collect_keys(item) for item in value))
    return set()


class ResponseInputCard(StrictModel):
    schema_version: Literal[INPUT_CARD_VERSION] = INPUT_CARD_VERSION
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=ID)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: Literal["table", "ordered_list", "formula"]
    source_fixture_sha256: str = Field(pattern=HASH)
    source_binding: ModelSourceBinding
    visible_elements: tuple[ModelVisibleElement, ...] = Field(min_length=1)
    layout: ModelLayout
    geometry: GeometryConstraints

    @model_validator(mode="after")
    def validate_card(self) -> "ResponseInputCard":
        if self.presentation != self.layout.presentation:
            raise ValueError("R9.2 input presentation mismatch")
        if self.source_binding.fact_id not in self.fact_ids:
            raise ValueError("R9.2 input Fact binding mismatch")
        if tuple(item.element_id for item in self.visible_elements) != self.layout.element_ids:
            raise ValueError("R9.2 input visible elements are not exactly once")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("R9.2 input Fact IDs must be unique")
        return self


class ResponseModelInput(StrictModel):
    schema_version: Literal[INPUT_VERSION] = INPUT_VERSION
    model_input_id: str = Field(pattern=r"^step3-r92-model-input:[0-9a-f]{24}$")
    source_fixture_sha256: str = Field(pattern=HASH)
    cards: tuple[ResponseInputCard, ...] = Field(min_length=3, max_length=3)
    card_count: Literal[3] = 3

    @model_validator(mode="after")
    def validate_input(self) -> "ResponseModelInput":
        if tuple(item.presentation for item in self.cards) != PRESENTATIONS:
            raise ValueError("R9.2 representative order mismatch")
        if len({item.contract_id for item in self.cards}) != 3:
            raise ValueError("R9.2 contracts must be unique")
        if len({item.source_candidate_id for item in self.cards}) != 3:
            raise ValueError("R9.2 cross-sample grouping refused")
        if any(item.source_fixture_sha256 != self.source_fixture_sha256 for item in self.cards):
            raise ValueError("R9.2 source fixture binding mismatch")
        expected = f"step3-r92-model-input:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'model_input_id'}))[:24]}"
        if self.model_input_id != expected:
            raise ValueError("R9.2 model input identity mismatch")
        return self


class PromptContract(StrictModel):
    schema_version: Literal[PROMPT_VERSION] = PROMPT_VERSION
    prompt_id: str = Field(pattern=r"^step3-r92-prompt:[0-9a-f]{24}$")
    allowed_presentations: tuple[Literal["table", "ordered_list", "formula"], ...] = PRESENTATIONS
    response_contract: Literal["exact-model-document-inside-exact-assistant-envelope"] = "exact-model-document-inside-exact-assistant-envelope"
    prompt_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_prompt(self) -> "PromptContract":
        if any(key in self.prompt_text for key in FORBIDDEN_KEYS):
            raise ValueError("R9.2 prompt contains forbidden key")
        expected = f"step3-r92-prompt:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'prompt_id'}))[:24]}"
        if self.prompt_id != expected:
            raise ValueError("R9.2 prompt identity mismatch")
        return self


class ResponseDraftCard(StrictModel):
    schema_version: Literal[RESULT_CARD_VERSION] = RESULT_CARD_VERSION
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=ID)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: Literal["table", "ordered_list", "formula"]
    source_binding: ModelSourceBinding
    visible_elements: tuple[ModelVisibleElement, ...] = Field(min_length=1)
    layout: ModelLayout
    geometry: GeometryConstraints
    disposition: Literal["draft"] = "draft"

    @model_validator(mode="after")
    def validate_card(self) -> "ResponseDraftCard":
        if self.presentation != self.layout.presentation:
            raise ValueError("R9.2 result presentation mismatch")
        if tuple(item.element_id for item in self.visible_elements) != self.layout.element_ids:
            raise ValueError("R9.2 result visible elements are not layout-exact")
        if self.source_binding.fact_id not in self.fact_ids:
            raise ValueError("R9.2 result Fact binding mismatch")
        return self


class ResponseDocument(StrictModel):
    schema_version: Literal[RESULT_VERSION] = RESULT_VERSION
    draft_cards: tuple[ResponseDraftCard, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_document(self) -> "ResponseDocument":
        if tuple(item.presentation for item in self.draft_cards) != PRESENTATIONS:
            raise ValueError("R9.2 response presentation order mismatch")
        if len({item.contract_id for item in self.draft_cards}) != 3:
            raise ValueError("R9.2 response contract identities must be exactly once")
        if len({item.source_candidate_id for item in self.draft_cards}) != 3:
            raise ValueError("R9.2 response cross-sample grouping refused")
        return self


class ResponseMessage(StrictModel):
    role: Literal["assistant"] = "assistant"
    content: str = Field(min_length=1)


class ResponseChoice(StrictModel):
    index: Literal[0] = 0
    message: ResponseMessage


class ResponseEnvelope(StrictModel):
    choices: tuple[ResponseChoice, ...] = Field(min_length=1, max_length=1)


class GateCheck(StrictModel):
    schema_version: Literal[GATE_VERSION] = GATE_VERSION
    check_id: str = Field(pattern=ID)
    passed: bool
    detail: str = Field(min_length=1)


class ResponseGate(StrictModel):
    schema_version: Literal[GATE_VERSION] = GATE_VERSION
    valid: bool
    envelope_exact: bool
    content_text: bool
    content_json: bool
    result_schema: bool
    contract_exactly_once: bool
    fact_card_exactly_once: bool
    source_text_exact: bool
    evidence_anchor_exact: bool
    typed_contract_exact: bool
    provenance_exact: bool
    no_unknown_fields: bool
    relation_text_absent: bool
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "ResponseGate":
        flags = (
            self.envelope_exact,
            self.content_text,
            self.content_json,
            self.result_schema,
            self.contract_exactly_once,
            self.fact_card_exactly_once,
            self.source_text_exact,
            self.evidence_anchor_exact,
            self.typed_contract_exact,
            self.provenance_exact,
            self.no_unknown_fields,
            self.relation_text_absent,
        )
        if self.valid != all(flags) or self.valid != (not self.failure_codes):
            raise ValueError("R9.2 response Gate validity mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
        if self.gate_sha256 != expected:
            raise ValueError("R9.2 response Gate hash mismatch")
        return self


class OfflineGate(StrictModel):
    schema_version: Literal[OFFLINE_GATE_VERSION] = OFFLINE_GATE_VERSION
    valid: bool
    step3_response_offline_ready: bool
    prior_freeze_valid: bool
    source_failure_evidence_valid: bool
    source_model_input_valid: bool
    deterministic_valid_response: bool
    regression_fail_closed: bool
    network_calls: Literal[0] = 0
    checks: tuple[GateCheck, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "OfflineGate":
        flags = (
            self.prior_freeze_valid,
            self.source_failure_evidence_valid,
            self.source_model_input_valid,
            self.deterministic_valid_response,
            self.regression_fail_closed,
        )
        if self.valid != all(flags) or self.step3_response_offline_ready != self.valid:
            raise ValueError("R9.2 offline Gate validity mismatch")
        if self.valid != (not self.failure_codes):
            raise ValueError("R9.2 offline Gate failure mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
        if self.gate_sha256 != expected:
            raise ValueError("R9.2 offline Gate hash mismatch")
        return self


class RequestIdentity(StrictModel):
    schema_version: Literal[REQUEST_VERSION] = REQUEST_VERSION
    request_id: str = Field(pattern=r"^step3-r92-offline-request:[0-9a-f]{64}$")
    model_input_sha256: str = Field(pattern=HASH)
    prompt_sha256: str = Field(pattern=HASH)
    response_schema_sha256: str = Field(pattern=HASH)
    response_envelope_schema_sha256: str = Field(pattern=HASH)
    prior_failure_evidence_sha256: str = Field(pattern=HASH)
    checkpoint_commit: Literal[CHECKPOINT_COMMIT] = CHECKPOINT_COMMIT
    network_calls: Literal[0] = 0


class MachineReport(StrictModel):
    schema_version: Literal[REPORT_VERSION] = REPORT_VERSION
    phase: Literal[PHASE] = PHASE
    checkpoint_commit: Literal[CHECKPOINT_COMMIT] = CHECKPOINT_COMMIT
    prior_freeze_sha256: str = Field(pattern=HASH)
    failure_evidence_sha256: str = Field(pattern=HASH)
    model_input_sha256: str = Field(pattern=HASH)
    prompt_sha256: str = Field(pattern=HASH)
    response_schema_sha256: str = Field(pattern=HASH)
    response_envelope_schema_sha256: str = Field(pattern=HASH)
    request_identity: RequestIdentity
    offline_gate: OfflineGate
    network_calls: Literal[0] = 0
    reviewed_fixture_created: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    source_mutations: Literal[0] = 0
    report_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_report(self) -> "MachineReport":
        if self.offline_gate.step3_response_offline_ready is not True:
            raise ValueError("R9.2 report cannot be ready when offline Gate fails")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "report_sha256"}))
        if self.report_sha256 != expected:
            raise ValueError("R9.2 machine report hash mismatch")
        return self


class JUnitReceipt(StrictModel):
    schema_version: Literal[ACCEPTANCE_VERSION] = ACCEPTANCE_VERSION
    label: Literal["targeted", "full"]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=HASH)
    tests: int = Field(ge=0)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class AcceptanceReport(StrictModel):
    schema_version: Literal[ACCEPTANCE_VERSION] = ACCEPTANCE_VERSION
    phase: Literal[PHASE] = PHASE
    offline_ready: Literal[True] = True
    all_tests_passed: bool
    receipts: tuple[JUnitReceipt, ...] = Field(min_length=2, max_length=2)
    machine_report_sha256: str = Field(pattern=HASH)
    accepted: bool
    network_calls: Literal[0] = 0
    reviewed_fixture_created: Literal[False] = False
    step4_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    acceptance_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_acceptance(self) -> "AcceptanceReport":
        if self.accepted != (self.all_tests_passed and self.offline_ready):
            raise ValueError("R9.2 acceptance status mismatch")
        if {item.label for item in self.receipts} != {"targeted", "full"}:
            raise ValueError("R9.2 acceptance requires targeted and full JUnit")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "acceptance_sha256"}))
        if self.acceptance_sha256 != expected:
            raise ValueError("R9.2 acceptance hash mismatch")
        return self


@dataclass(frozen=True)
class ResponseInspection:
    response: ResponseDocument | None
    gate: ResponseGate


def build_response_model_input(cards: Sequence[Any], *, source_fixture_sha256: str) -> ResponseModelInput:
    """参数化改造：收已装载卡对象（r9 `ModelCard` 同构面），不读 R91 冻结运行。

    卡对象需具备 `contract_id / source_candidate_id / fact_ids / presentation /
    source_binding / visible_elements / layout / geometry` 字段（旧仓 R91
    PilotModelInput.cards 与 r9 编译产物均满足）。v1 契约钉扎三代表卡与
    (table, ordered_list, formula) 固定序——由 `ResponseModelInput` 模型自身校验，
    非本函数断言；放宽卡数/表达族属 v2 新版本号范畴。
    """
    input_cards = tuple(
        ResponseInputCard(
            contract_id=card.contract_id,
            source_candidate_id=card.source_candidate_id,
            fact_ids=card.fact_ids,
            presentation=card.presentation,
            source_fixture_sha256=source_fixture_sha256,
            source_binding=card.source_binding,
            visible_elements=card.visible_elements,
            layout=card.layout,
            geometry=card.geometry,
        )
        for card in cards
    )
    payload = {
        "source_fixture_sha256": source_fixture_sha256,
        "cards": input_cards,
        "card_count": 3,
    }
    return ResponseModelInput(
        model_input_id=f"step3-r92-model-input:{canonical_hash(payload)[:24]}",
        **payload,
    )


def build_prompt() -> PromptContract:
    text = (
        "Return only one JSON object with exactly schema_version and draft_cards. Preserve every supplied contract, Fact, source "
        "binding, source text, typed layout, geometry and provenance exactly once and in source order. Keep every "
        "card draft-only. Do not add fields, text, titles, relations, branding, watermark, OCR corrections, inferred "
        "content, regrouping or cross-sample data."
    )
    return PromptContract(prompt_id=f"step3-r92-prompt:{canonical_hash({'allowed_presentations': PRESENTATIONS, 'response_contract': 'exact-model-document-inside-exact-assistant-envelope', 'prompt_text': text})[:24]}", prompt_text=text)


def build_fake_result(model_input: ResponseModelInput) -> ResponseDocument:
    return ResponseDocument(
        draft_cards=tuple(
            ResponseDraftCard(
                contract_id=card.contract_id,
                source_candidate_id=card.source_candidate_id,
                fact_ids=card.fact_ids,
                presentation=card.presentation,
                source_binding=card.source_binding,
                visible_elements=card.visible_elements,
                layout=card.layout,
                geometry=card.geometry,
            )
            for card in model_input.cards
        )
    )


def encode_response(result: ResponseDocument) -> bytes:
    content = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    envelope = {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    return canonical_json(envelope)


def _checks(model_input: ResponseModelInput, response: ResponseDocument | None, flags: dict[str, bool], failures: list[str]) -> ResponseGate:
    expected = model_input.cards
    actual = response.draft_cards if response is not None else ()
    contract_ok = bool(response) and tuple(item.contract_id for item in actual) == tuple(item.contract_id for item in expected)
    fact_ok = bool(response) and tuple(item.fact_ids for item in actual) == tuple(item.fact_ids for item in expected)
    text_ok = bool(response) and tuple(item.visible_elements for item in actual) == tuple(item.visible_elements for item in expected)
    binding_ok = bool(response) and tuple(item.source_binding for item in actual) == tuple(item.source_binding for item in expected)
    typed_ok = bool(response) and tuple((item.layout, item.geometry) for item in actual) == tuple((item.layout, item.geometry) for item in expected)
    provenance_ok = binding_ok and all(item.source_binding.fact_id in item.fact_ids for item in actual)
    cross_sample_ok = bool(response) and len({item.source_candidate_id for item in actual}) == len(actual)
    serialized = "\n".join(element.source_text for card in actual for element in card.visible_elements)
    relation_values = [
        edge.relation
        for card in expected
        for edge in card.layout.diagram_edges
        if getattr(edge, "relation", None)
    ]
    relation_ok = not any(value in serialized for value in relation_values)
    unknown_ok = not (set(_collect_keys(response.model_dump(mode="json")) & FORBIDDEN_KEYS) if response else set())
    values = {
        "envelope_exact": flags.get("envelope_exact", False),
        "content_text": flags.get("content_text", False),
        "content_json": flags.get("content_json", False),
        "result_schema": flags.get("result_schema", False),
        "contract_exactly_once": contract_ok,
        "fact_card_exactly_once": fact_ok,
        "source_text_exact": text_ok,
        "evidence_anchor_exact": binding_ok,
        "typed_contract_exact": typed_ok,
        "provenance_exact": provenance_ok,
        "no_unknown_fields": flags.get("no_unknown_fields", unknown_ok),
        "relation_text_absent": relation_ok,
    }
    details = {
        "envelope_exact": "raw assistant envelope has exactly one strict choice",
        "content_text": "assistant message content is text",
        "content_json": "assistant content is one JSON object",
        "result_schema": "model document validates against the R9.2 strict schema",
        "contract_exactly_once": "three representative contract IDs are source ordered and unique",
        "fact_card_exactly_once": "Fact bindings match each source card exactly",
        "source_text_exact": "all source-owned display text is byte-identical",
        "evidence_anchor_exact": "Evidence, anchor, page and source bindings match",
        "typed_contract_exact": "typed layout and geometry match",
        "provenance_exact": "source provenance is complete and unchanged",
        "no_unknown_fields": "no forbidden or unknown model fields are admitted",
        "relation_text_absent": "layout-only relation wording is absent from visible text",
    }
    checks = tuple(GateCheck(check_id=key, passed=value, detail=details[key]) for key, value in values.items())
    all_valid = all(values.values())
    failures.extend(key for key, value in values.items() if not value and key not in failures)
    return _hash_model(
        ResponseGate,
        {**values, "valid": all_valid, "checks": checks, "failure_codes": tuple(failures)},
        "gate_sha256",
    )  # type: ignore[return-value]


def inspect_response(model_input: ResponseModelInput, body: bytes) -> ResponseInspection:
    flags = {"envelope_exact": False, "content_text": False, "content_json": False, "result_schema": False, "no_unknown_fields": False}
    failures: list[str] = []
    response: ResponseDocument | None = None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        failures.append("wrapper-json-invalid")
        return ResponseInspection(None, _checks(model_input, None, flags, failures))
    if not isinstance(raw, dict) or set(raw) != {"choices"}:
        failures.append("wrapper-fields-exact")
    elif not isinstance(raw["choices"], list) or len(raw["choices"]) != 1:
        failures.append("wrapper-choice-count")
    else:
        choice = raw["choices"][0]
        if not isinstance(choice, dict) or set(choice) != {"index", "message"} or choice.get("index") != 0:
            failures.append("wrapper-choice-shape")
        else:
            message = choice["message"]
            if not isinstance(message, dict) or set(message) != {"role", "content"} or message.get("role") != "assistant":
                failures.append("wrapper-message-shape")
            else:
                flags["envelope_exact"] = True
                if not isinstance(message.get("content"), str):
                    failures.append("content-not-text")
                else:
                    flags["content_text"] = True
                    try:
                        document = json.loads(message["content"])
                        flags["content_json"] = isinstance(document, dict)
                        if not flags["content_json"]:
                            failures.append("result-json-object")
                        elif set(document) != {"schema_version", "draft_cards"}:
                            failures.append("result-fields-exact")
                        else:
                            try:
                                response = ResponseDocument.model_validate(document)
                                flags["result_schema"] = True
                                flags["no_unknown_fields"] = True
                            except Exception:
                                failures.append("result-schema")
                    except (json.JSONDecodeError, TypeError):
                        failures.append("content-json-invalid")
    if flags["envelope_exact"] and not flags["content_json"] and "content-json-invalid" not in failures:
        failures.append("content-json-invalid")
    return ResponseInspection(response, _checks(model_input, response, flags, failures))


def make_inner_inspector(model_input: ResponseModelInput) -> Callable[[bytes], ResponseGate]:
    """providers 稿 §6.3 约定的下游结果门禁回调适配器。

    providers 的 `inspect_chat_envelope` 转发完整最小信封字节（r94 原样 choices /
    r96 剥 transport-only 重投影），本闭包直接喂 `inspect_response` 并回其
    `ResponseGate`（valid / failure_codes 最小面已锁定）。
    """

    def _inspect(body: bytes) -> ResponseGate:
        return inspect_response(model_input, body).gate

    return _inspect


def build_request(model_input: ResponseModelInput, prompt: PromptContract, result_schema: Mapping[str, Any], envelope_schema: Mapping[str, Any], prior_failure_hash: str) -> RequestIdentity:
    seed = {
        "phase": PHASE,
        "model_input_id": model_input.model_input_id,
        "model_input_sha256": canonical_hash(model_input),
        "prompt_sha256": sha256_text(prompt.prompt_text),
        "response_schema_sha256": canonical_hash(result_schema),
        "response_envelope_schema_sha256": canonical_hash(envelope_schema),
        "prior_failure_evidence_sha256": prior_failure_hash,
    }
    digest = canonical_hash(seed)
    return RequestIdentity(
        request_id=f"step3-r92-offline-request:{digest}",
        model_input_sha256=canonical_hash(model_input),
        prompt_sha256=sha256_text(prompt.prompt_text),
        response_schema_sha256=canonical_hash(result_schema),
        response_envelope_schema_sha256=canonical_hash(envelope_schema),
        prior_failure_evidence_sha256=prior_failure_hash,
    )


def build_regression_cases(model_input: ResponseModelInput) -> dict[str, bytes]:
    valid = encode_response(build_fake_result(model_input))
    document = json.loads(json.loads(valid)["choices"][0]["message"]["content"])
    cases: dict[str, bytes] = {"valid": valid}
    cases["wrapper_invalid"] = b"[]"
    cases["wrapper_missing_choices"] = canonical_json({})
    cases["wrapper_unknown_field"] = canonical_json({"choices": json.loads(valid)["choices"], "unknown_field": True})
    cases["content_non_text"] = canonical_json({"choices": [{"index": 0, "message": {"role": "assistant", "content": {}}}]})
    cases["content_invalid_json"] = canonical_json({"choices": [{"index": 0, "message": {"role": "assistant", "content": "{"}}]})
    for name, mutate in (
        ("result_schema_version_mismatch", lambda value: value.update({"schema_version": "wrong"})),
        ("result_missing_top_level", lambda value: value.pop("draft_cards")),
        ("result_unknown_top_level", lambda value: value.update({"unknown_field": True})),
    ):
        changed = json.loads(json.dumps(document, ensure_ascii=False))
        mutate(changed)
        cases[name] = canonical_json({"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(changed, ensure_ascii=False, sort_keys=True)}}]})
    cards = document["draft_cards"]
    mutations = {
        "draft_cards_omitted": cards[:-1],
        "draft_cards_duplicated": [cards[0], cards[0], cards[2]],
        "draft_card_wrong_type": [cards[0], {**cards[1], "presentation": "table"}, cards[2]],
        "fact_binding_mismatch": [cards[0], {**cards[1], "fact_ids": ["fact:wrong"]}, cards[2]],
        "source_text_mutation": [cards[0], {**cards[1], "visible_elements": [{**cards[1]["visible_elements"][0], "source_text": cards[1]["visible_elements"][0]["source_text"] + " mutated"}, *cards[1]["visible_elements"][1:]]}, cards[2]],
        "typed_contract_mismatch": [cards[0], {**cards[1], "layout": {**cards[1]["layout"], "typed_contract_sha256": "0" * 64}}, cards[2]],
        "provenance_mismatch": [{**cards[0], "source_binding": {**cards[0]["source_binding"], "page": cards[0]["source_binding"]["page"] + 1}}, cards[1], cards[2]],
        "cross_sample_grouping": [cards[0], {**cards[1], "source_candidate_id": cards[0]["source_candidate_id"]}, cards[2]],
        "nested_unknown_field": [{**cards[0], "unknown_field": True}, cards[1], cards[2]],
    }
    for name, mutated_cards in mutations.items():
        changed = {**document, "draft_cards": mutated_cards}
        cases[name] = canonical_json({"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(changed, ensure_ascii=False, sort_keys=True)}}]})
    return cases
