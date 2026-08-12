"""Step 3 v2 single-card echo response contract (new versioned module).

设计依据 docs/prework/P3_优化施工方案_T1-T5_20260812.md §4（T3）与 §8-1 修订：
v1 冻结契约（step3_response.py，phase2r92 血统）钉扎「三代表卡一次性整体回显」
（输出 ~4-6K tokens 超长受约束生成），在两中继三路径全部证伪（chatgpt2api 空数组
逃逸；2chat 网关掐断/上游 APIError）。v2 把回显颗粒降为**单卡一发**（输出
~1-1.5K tokens，与 step2b 已 5/5 全绿的量级同档），并解除三代表卡/固定序钉扎，
覆盖全部五个表达族——这是「新卡自动出布局→出图」断点的修复件。

冻结纪律（§6 红线 / 铁律 bc-b8b9aca6）：
- v1 的 ``step3_response.py``、法定哈希实现、既有 fixture **零改动**；本模块只
  新增文件，向 v1 只做 import 复用（``FORBIDDEN_KEYS``/``canonical_json``/
  ``sha256_*``），不触碰其任何定义。
- 法定哈希绑定 ``admission_legacy_hash.canonical_hash_r10x``（r9/r10x 变体，与
  v1 同源同一实现）。

字段口径（与 v1 卡对象一致，仅两处**有意**收窄，均不损失信息）：
- ``SingleCardModelInput`` 摊平单卡投影七字段 contract_id / source_candidate_id /
  fact_ids / presentation / source_binding / visible_elements / layout；
  contract 身份面来自 ``Step3Compilation.contracts[i]``，model 投影面来自
  ``model_input.cards[i]``——两序列保序一一对应（pilot 已验证），组合适配见
  ``build_single_card_model_input``。
- ``geometry`` 不进回显：``GeometryConstraints`` 是全 Literal 常量模型（零信息
  量），回显纯耗 tokens；下游装配一律从编译产物取 geometry，不从模型回声取。
- 逐卡 ``source_fixture_sha256`` 上提为输入顶层单字段（单卡语义下逐卡携带冗余）；
  结果文档与 v1 相同不回显 fixture 哈希。

回显确认语义与 v1 相同：一字不改逐字段回显。已知失败模式已内建判负：
- 空数组逃逸（strict minItems 不被中继强制）→ ``empty-draft-cards-escape``；
- schema_version 错版（模型把 input 版本号错当 result 版本号回显）→
  ``result-version-echoed-input`` / ``card-version-echoed-input``；
- 字段漂移（任一回显字段与输入不等）→ 对应 ``*_exact`` 门禁位判负。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import Field, model_validator

from examcard.common.schema import strict_schema
from examcard.compilers.step3_card import (
    ModelLayout,
    ModelSourceBinding,
    ModelVisibleElement,
)
from examcard.compilers.step3_response import (
    FORBIDDEN_KEYS,
    canonical_json,
    sha256_bytes,
    sha256_text,
)
from examcard.knowledge.contracts.admission_legacy_hash import (
    canonical_hash_r10x as canonical_hash,  # r9/r10x 变体，与 v1 契约同源（铁律 bc-b8b9aca6）
)
from examcard.materials.schema import StrictModel

__all__ = [
    "GATE_VERSION_V2",
    "GateCheckV2",
    "INPUT_VERSION_V2",
    "PROMPT_VERSION_V2",
    "RESULT_CARD_VERSION_V2",
    "RESULT_SCHEMA_V2",
    "RESULT_VERSION_V2",
    "SUPPORTED_PRESENTATIONS",
    "PromptContractV2",
    "SingleCardDraftCard",
    "SingleCardInspection",
    "SingleCardModelInput",
    "SingleCardResponseDocument",
    "SingleCardResponseGate",
    "build_fake_single_card_result",
    "build_single_card_model_input",
    "build_single_card_prompt",
    "build_single_card_regression_cases",
    "encode_single_card_response",
    "inspect_single_card_response",
    "make_single_card_inspector",
    "prompt_v2_sha256",
    "result_schema_v2_sha256",
]

# v2 版本号族：input 与 result 的顶层 schema_version 刻意不同——「模型把 input
# 版本号错当 result 版本号回显」是实测已知失败模式，版本可辨性是判负的前提。
INPUT_VERSION_V2 = "step3-response/2.0"
RESULT_VERSION_V2 = "step3-response-result/2.0"
RESULT_CARD_VERSION_V2 = "step3-response-draft-card/2.0"
PROMPT_VERSION_V2 = "step3-response-prompt/2.0"
GATE_VERSION_V2 = "step3-response-gate/2.0"

# v2 解除三代表卡钉扎：五个表达族全部可发（v1 的 (table, ordered_list, formula)
# 固定序属 v1 契约本体，原样留在 v1 模块）。
SUPPORTED_PRESENTATIONS = ("diagram", "table", "ordered_list", "constraint", "formula")

HASH = r"^[0-9a-f]{64}$"
ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"
CONTRACT_ID = r"^step3-card-contract:[0-9a-f]{24}$"
MODEL_INPUT_ID_V2 = r"^step3-v2-model-input:[0-9a-f]{24}$"
PROMPT_ID_V2 = r"^step3-v2-prompt:[0-9a-f]{24}$"

_PresentationLiteral = Literal["diagram", "table", "ordered_list", "constraint", "formula"]


class SingleCardModelInput(StrictModel):
    """单卡模型输入：v1 卡对象的摊平投影（geometry 与逐卡 fixture 哈希除外，见模块头）。"""

    schema_version: Literal[INPUT_VERSION_V2] = INPUT_VERSION_V2
    model_input_id: str = Field(pattern=MODEL_INPUT_ID_V2)
    source_fixture_sha256: str = Field(pattern=HASH)
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=ID)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: _PresentationLiteral
    source_binding: ModelSourceBinding
    visible_elements: tuple[ModelVisibleElement, ...] = Field(min_length=1)
    layout: ModelLayout

    @model_validator(mode="after")
    def validate_input(self) -> "SingleCardModelInput":
        if self.presentation != self.layout.presentation:
            raise ValueError("v2 input presentation mismatch")
        if self.source_binding.fact_id not in self.fact_ids:
            raise ValueError("v2 input Fact binding mismatch")
        if tuple(item.element_id for item in self.visible_elements) != self.layout.element_ids:
            raise ValueError("v2 input visible elements are not layout-exact")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("v2 input Fact IDs must be unique")
        expected = f"step3-v2-model-input:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'model_input_id'}))[:24]}"
        if self.model_input_id != expected:
            raise ValueError("v2 model input identity mismatch")
        return self


class PromptContractV2(StrictModel):
    schema_version: Literal[PROMPT_VERSION_V2] = PROMPT_VERSION_V2
    prompt_id: str = Field(pattern=PROMPT_ID_V2)
    allowed_presentations: tuple[_PresentationLiteral, ...] = SUPPORTED_PRESENTATIONS
    response_contract: Literal["exact-single-card-document-inside-exact-assistant-envelope"] = (
        "exact-single-card-document-inside-exact-assistant-envelope"
    )
    prompt_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_prompt(self) -> "PromptContractV2":
        if any(key in self.prompt_text for key in FORBIDDEN_KEYS):
            raise ValueError("v2 prompt contains forbidden key")
        expected = f"step3-v2-prompt:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'prompt_id'}))[:24]}"
        if self.prompt_id != expected:
            raise ValueError("v2 prompt identity mismatch")
        return self


class SingleCardDraftCard(StrictModel):
    """结果卡：输入七字段的逐字回显 + draft-only 处置（与 v1 结果卡同语义，去 geometry）。"""

    schema_version: Literal[RESULT_CARD_VERSION_V2] = RESULT_CARD_VERSION_V2
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(pattern=ID)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: _PresentationLiteral
    source_binding: ModelSourceBinding
    visible_elements: tuple[ModelVisibleElement, ...] = Field(min_length=1)
    layout: ModelLayout
    disposition: Literal["draft"] = "draft"

    @model_validator(mode="after")
    def validate_card(self) -> "SingleCardDraftCard":
        if self.presentation != self.layout.presentation:
            raise ValueError("v2 result presentation mismatch")
        if tuple(item.element_id for item in self.visible_elements) != self.layout.element_ids:
            raise ValueError("v2 result visible elements are not layout-exact")
        if self.source_binding.fact_id not in self.fact_ids:
            raise ValueError("v2 result Fact binding mismatch")
        return self


class SingleCardResponseDocument(StrictModel):
    schema_version: Literal[RESULT_VERSION_V2] = RESULT_VERSION_V2
    draft_cards: tuple[SingleCardDraftCard, ...] = Field(min_length=1, max_length=1)


# strict json_schema：递归「全属性 required + additionalProperties:false」，
# draft_cards 带 minItems=1/maxItems=1，schema_version/disposition 以 const 钉死。
# 注意：中继是否强制执行本 schema 不可信（chatgpt2api 实测不强制 minItems），
# 内容门禁 inspect_single_card_response 才是判定权威。
RESULT_SCHEMA_V2: dict[str, Any] = strict_schema(SingleCardResponseDocument)


class GateCheckV2(StrictModel):
    schema_version: Literal[GATE_VERSION_V2] = GATE_VERSION_V2
    check_id: str = Field(pattern=ID)
    passed: bool
    detail: str = Field(min_length=1)


class SingleCardResponseGate(StrictModel):
    schema_version: Literal[GATE_VERSION_V2] = GATE_VERSION_V2
    valid: bool
    envelope_exact: bool
    content_text: bool
    content_json: bool
    result_version_exact: bool
    card_count_exact: bool
    result_schema: bool
    identity_exact: bool
    fact_ids_exact: bool
    source_binding_exact: bool
    source_text_exact: bool
    layout_exact: bool
    provenance_exact: bool
    no_unknown_fields: bool
    relation_text_absent: bool
    checks: tuple[GateCheckV2, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "SingleCardResponseGate":
        flags = (
            self.envelope_exact,
            self.content_text,
            self.content_json,
            self.result_version_exact,
            self.card_count_exact,
            self.result_schema,
            self.identity_exact,
            self.fact_ids_exact,
            self.source_binding_exact,
            self.source_text_exact,
            self.layout_exact,
            self.provenance_exact,
            self.no_unknown_fields,
            self.relation_text_absent,
        )
        if self.valid != all(flags) or self.valid != (not self.failure_codes):
            raise ValueError("v2 response Gate validity mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
        if self.gate_sha256 != expected:
            raise ValueError("v2 response Gate hash mismatch")
        return self


@dataclass(frozen=True)
class SingleCardInspection:
    response: SingleCardResponseDocument | None
    gate: SingleCardResponseGate


def build_single_card_model_input(
    contract: Any,
    projection: Any,
    *,
    source_fixture_sha256: str | None = None,
) -> SingleCardModelInput:
    """从 ``Step3Compilation`` 的逐卡组合构造单卡模型输入。

    ``contract`` 取 ``compilation.contracts[i]``（身份面：contract_id /
    source_candidate_id / fact_ids / presentation），``projection`` 取
    ``compilation.model_input.cards[i]``（投影面：source_binding /
    visible_elements / layout）——两序列保序一一对应（pilot 组合适配器先例）。
    错位组合是静默数据腐蚀源，故先做配对守卫：presentation 必须一致；两面各自
    携带 ``source_fixture_sha256`` / typed-contract 哈希时必须互证一致。
    """
    if contract.presentation != projection.presentation:
        raise ValueError("v2 pairing mismatch: contract and projection presentation differ")
    contract_fixture = getattr(contract, "source_fixture_sha256", None)
    projection_fixture = getattr(projection, "source_fixture_sha256", None)
    if contract_fixture and projection_fixture and contract_fixture != projection_fixture:
        raise ValueError("v2 pairing mismatch: contract and projection source fixture differ")
    typed_sha = getattr(contract, "source_typed_contract_sha256", None)
    if typed_sha and typed_sha != projection.layout.typed_contract_sha256:
        raise ValueError("v2 pairing mismatch: typed contract hash differs")
    fixture_sha = source_fixture_sha256 or projection_fixture or contract_fixture
    if not fixture_sha:
        raise ValueError("v2 model input requires a source fixture sha256")
    payload = {
        "source_fixture_sha256": fixture_sha,
        "contract_id": contract.contract_id,
        "source_candidate_id": contract.source_candidate_id,
        "fact_ids": tuple(contract.fact_ids),
        "presentation": contract.presentation,
        "source_binding": projection.source_binding,
        "visible_elements": tuple(projection.visible_elements),
        "layout": projection.layout,
    }
    return SingleCardModelInput(
        model_input_id=f"step3-v2-model-input:{canonical_hash(payload)[:24]}",
        **payload,
    )


def build_single_card_prompt() -> PromptContractV2:
    text = (
        "Return only one JSON object with exactly schema_version and draft_cards. draft_cards must contain "
        "exactly one card that echoes the supplied card verbatim: preserve the supplied contract, Fact list, "
        "source binding, source text and typed layout exactly once, byte for byte, in source order. An empty "
        "draft_cards array is invalid. Keep the card draft-only. Do not add fields, text, titles, relations, "
        "branding, watermark, OCR corrections, inferred content, regrouping or cross-sample data."
    )
    payload = {
        "allowed_presentations": SUPPORTED_PRESENTATIONS,
        "response_contract": "exact-single-card-document-inside-exact-assistant-envelope",
        "prompt_text": text,
    }
    return PromptContractV2(
        prompt_id=f"step3-v2-prompt:{canonical_hash(payload)[:24]}",
        prompt_text=text,
    )


def build_fake_single_card_result(model_input: SingleCardModelInput) -> SingleCardResponseDocument:
    """确定性合规回显（离线正样本 / 回归基线）。"""
    return SingleCardResponseDocument(
        draft_cards=(
            SingleCardDraftCard(
                contract_id=model_input.contract_id,
                source_candidate_id=model_input.source_candidate_id,
                fact_ids=model_input.fact_ids,
                presentation=model_input.presentation,
                source_binding=model_input.source_binding,
                visible_elements=model_input.visible_elements,
                layout=model_input.layout,
            ),
        )
    )


def encode_single_card_response(result: SingleCardResponseDocument) -> bytes:
    """把结果文档编码为字节级最小 assistant 信封（与 v1 encode_response 同构）。"""
    content = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    envelope = {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    return canonical_json(envelope)


def _checks_v2(
    model_input: SingleCardModelInput,
    response: SingleCardResponseDocument | None,
    flags: dict[str, bool],
    failures: list[str],
) -> SingleCardResponseGate:
    card = response.draft_cards[0] if response is not None else None
    identity_ok = card is not None and (
        card.contract_id == model_input.contract_id
        and card.source_candidate_id == model_input.source_candidate_id
        and card.presentation == model_input.presentation
    )
    fact_ok = card is not None and card.fact_ids == model_input.fact_ids
    binding_ok = card is not None and card.source_binding == model_input.source_binding
    text_ok = card is not None and card.visible_elements == model_input.visible_elements
    layout_ok = card is not None and card.layout == model_input.layout
    provenance_ok = binding_ok and card is not None and card.source_binding.fact_id in card.fact_ids
    serialized = "\n".join(element.source_text for element in card.visible_elements) if card is not None else ""
    relation_values = [
        edge.relation
        for edge in model_input.layout.diagram_edges
        if getattr(edge, "relation", None)
    ]
    relation_ok = not any(value in serialized for value in relation_values)
    values = {
        "envelope_exact": flags.get("envelope_exact", False),
        "content_text": flags.get("content_text", False),
        "content_json": flags.get("content_json", False),
        "result_version_exact": flags.get("result_version_exact", False),
        "card_count_exact": flags.get("card_count_exact", False),
        "result_schema": flags.get("result_schema", False),
        "identity_exact": identity_ok,
        "fact_ids_exact": fact_ok,
        "source_binding_exact": binding_ok,
        "source_text_exact": text_ok,
        "layout_exact": layout_ok,
        "provenance_exact": provenance_ok,
        "no_unknown_fields": flags.get("no_unknown_fields", False),
        "relation_text_absent": relation_ok,
    }
    details = {
        "envelope_exact": "raw assistant envelope has exactly one strict choice",
        "content_text": "assistant message content is text",
        "content_json": "assistant content is one JSON object",
        "result_version_exact": "result schema_version is the v2 result version, not the echoed input version",
        "card_count_exact": "draft_cards carries exactly one card (empty-array escape refused)",
        "result_schema": "model document validates against the v2 strict schema",
        "identity_exact": "contract, candidate and presentation identities echo the supplied card",
        "fact_ids_exact": "Fact bindings match the supplied card exactly",
        "source_binding_exact": "Evidence, anchor, page and source bindings match",
        "source_text_exact": "all source-owned display text is byte-identical",
        "layout_exact": "typed layout matches the supplied card",
        "provenance_exact": "source provenance is complete and unchanged",
        "no_unknown_fields": "no forbidden or unknown model fields are admitted",
        "relation_text_absent": "layout-only relation wording is absent from visible text",
    }
    checks = tuple(GateCheckV2(check_id=key, passed=value, detail=details[key]) for key, value in values.items())
    all_valid = all(values.values())
    failures.extend(key for key, value in values.items() if not value and key not in failures)
    payload = {**values, "valid": all_valid, "checks": checks, "failure_codes": tuple(failures)}
    draft = SingleCardResponseGate.model_construct(**{**payload, "gate_sha256": "0" * 64})
    payload["gate_sha256"] = canonical_hash(draft.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
    return SingleCardResponseGate.model_validate(payload)


def inspect_single_card_response(model_input: SingleCardModelInput, body: bytes) -> SingleCardInspection:
    """字节级信封 + 内容双关门禁（判定权威；中继侧 strict 强制不可信）。"""
    flags = {
        "envelope_exact": False,
        "content_text": False,
        "content_json": False,
        "result_version_exact": False,
        "card_count_exact": False,
        "result_schema": False,
        "no_unknown_fields": False,
    }
    failures: list[str] = []
    response: SingleCardResponseDocument | None = None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        failures.append("wrapper-json-invalid")
        return SingleCardInspection(None, _checks_v2(model_input, None, flags, failures))
    content: str | None = None
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
                    content = message["content"]
    document: Any = None
    if content is not None:
        try:
            document = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            failures.append("content-json-invalid")
    if document is not None:
        if not isinstance(document, dict):
            failures.append("result-json-object")
        else:
            flags["content_json"] = True
            version = document.get("schema_version")
            if version == RESULT_VERSION_V2:
                flags["result_version_exact"] = True
            elif version == INPUT_VERSION_V2:
                # 已知失败模式：模型把 input 的版本号错当 result 版本号回显。
                failures.append("result-version-echoed-input")
            else:
                failures.append("result-version-mismatch")
            cards_raw = document.get("draft_cards")
            if isinstance(cards_raw, list):
                if not cards_raw:
                    # 已知失败模式：strict minItems 未被中继强制时的空数组逃逸。
                    failures.append("empty-draft-cards-escape")
                elif len(cards_raw) == 1:
                    flags["card_count_exact"] = True
                    first = cards_raw[0]
                    if isinstance(first, dict) and first.get("schema_version") == INPUT_VERSION_V2:
                        failures.append("card-version-echoed-input")
                else:
                    failures.append("draft-cards-not-single")
            else:
                failures.append("draft-cards-not-array")
            if set(document) != {"schema_version", "draft_cards"}:
                failures.append("result-fields-exact")
            else:
                try:
                    response = SingleCardResponseDocument.model_validate(document)
                    flags["result_schema"] = True
                    flags["no_unknown_fields"] = True
                except Exception:
                    failures.append("result-schema")
    if flags["envelope_exact"] and not flags["content_json"] and "content-json-invalid" not in failures:
        failures.append("content-json-invalid")
    return SingleCardInspection(response, _checks_v2(model_input, response, flags, failures))


def make_single_card_inspector(model_input: SingleCardModelInput) -> Callable[[bytes], SingleCardResponseGate]:
    """providers 稿 §6.3 内容门禁回调适配器（与 v1 make_inner_inspector 同构）。

    providers 的 ``inspect_chat_envelope`` 转发完整最小信封字节（r94 原样 choices /
    r96 剥 transport-only 重投影），本闭包直接喂 ``inspect_single_card_response``
    并回其 ``SingleCardResponseGate``（{valid, failure_codes} 最小面）。
    """

    def _inspect(body: bytes) -> SingleCardResponseGate:
        return inspect_single_card_response(model_input, body).gate

    return _inspect


def build_single_card_regression_cases(model_input: SingleCardModelInput) -> dict[str, bytes]:
    """离线回归字节串：1 条合规 + 已知失败模式与经典变异各一（fail-closed 自证）。"""
    valid = encode_single_card_response(build_fake_single_card_result(model_input))
    document = json.loads(json.loads(valid.decode("utf-8"))["choices"][0]["message"]["content"])
    card = document["draft_cards"][0]

    def envelope_of(changed: dict[str, Any]) -> bytes:
        content = json.dumps(changed, ensure_ascii=False, sort_keys=True)
        return canonical_json({"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]})

    def with_card(mutated_card: dict[str, Any]) -> dict[str, Any]:
        return {**document, "draft_cards": [mutated_card]}

    cases: dict[str, bytes] = {"valid": valid}
    cases["wrapper_invalid"] = b"[]"
    cases["empty_draft_cards_escape"] = envelope_of({**document, "draft_cards": []})
    cases["result_version_echoed_input"] = envelope_of({**document, "schema_version": INPUT_VERSION_V2})
    cases["result_version_wrong"] = envelope_of({**document, "schema_version": "wrong"})
    cases["card_version_echoed_input"] = envelope_of(with_card({**card, "schema_version": INPUT_VERSION_V2}))
    cases["draft_cards_duplicated"] = envelope_of({**document, "draft_cards": [card, card]})
    cases["source_text_drift"] = envelope_of(
        with_card(
            {
                **card,
                "visible_elements": [
                    {**card["visible_elements"][0], "source_text": card["visible_elements"][0]["source_text"] + " mutated"},
                    *card["visible_elements"][1:],
                ],
            }
        )
    )
    cases["fact_binding_drift"] = envelope_of(with_card({**card, "fact_ids": ["fact:wrong"]}))
    cases["layout_drift"] = envelope_of(
        with_card({**card, "layout": {**card["layout"], "typed_contract_sha256": "0" * 64}})
    )
    cases["provenance_drift"] = envelope_of(
        with_card({**card, "source_binding": {**card["source_binding"], "page": card["source_binding"]["page"] + 1}})
    )
    cases["unknown_top_level"] = envelope_of({**document, "unknown_field": True})
    cases["nested_unknown_field"] = envelope_of(with_card({**card, "unknown_field": True}))
    cases["result_missing_top_level"] = envelope_of({"schema_version": document["schema_version"]})
    return cases


# 供驱动/审计记录 schema 与 prompt 指纹（sha256_bytes/sha256_text 自 v1 复用）。
def result_schema_v2_sha256() -> str:
    return sha256_bytes(json.dumps(RESULT_SCHEMA_V2, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def prompt_v2_sha256() -> str:
    return sha256_text(build_single_card_prompt().prompt_text)
