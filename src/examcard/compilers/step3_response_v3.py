"""Step 3 V3 strict single-card response lane.

The response compiler is parallel to the frozen V1/V2 modules.  It echoes the
V3 model input, including per-element provenance, without projecting source
``threshold`` into renderer ``constraint`` semantics.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Final, Literal, cast

from pydantic import Field, model_validator

from examcard.common.schema import strict_schema
from examcard.materials.schema import StrictModel
from examcard.compilers.step3_card_v3 import Step3CompilationV3
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash_r10x as canonical_hash
from examcard.knowledge.contracts.step3_card_v3 import (
    CONTRACT_ID,
    HASH,
    ModelCardV3,
    ModelLayoutV3,
    ModelSourceBindingV3,
    ModelVisibleElementV3,
    PresentationV3,
    validate_model_binding_consistency,
)

INPUT_VERSION_V3: Final = "step3-response/3.0"
RESULT_VERSION_V3: Final = "step3-response-result/3.0"
RESULT_CARD_VERSION_V3: Final = "step3-response-draft-card/3.0"
PROMPT_VERSION_V3: Final = "step3-response-prompt/3.0"
GATE_VERSION_V3: Final = "step3-response-gate/3.0"
MODEL_INPUT_ID_V3: Final = r"^step3-v3-model-input:[0-9a-f]{24}$"
PROMPT_ID_V3: Final = r"^step3-v3-prompt:[0-9a-f]{24}$"
SUPPORTED_PRESENTATIONS: Final[tuple[str, ...]] = (
    "diagram", "table", "ordered_list", "constraint", "threshold", "formula"
)


class SingleCardModelInputV3(StrictModel):
    schema_version: Literal["step3-response/3.0"] = INPUT_VERSION_V3
    model_input_id: str = Field(pattern=MODEL_INPUT_ID_V3)
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(min_length=3)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: PresentationV3
    source_binding: ModelSourceBindingV3
    visible_elements: tuple[ModelVisibleElementV3, ...] = Field(min_length=1)
    layout: ModelLayoutV3

    @model_validator(mode="after")
    def validate_input(self) -> "SingleCardModelInputV3":
        if self.presentation != self.layout.presentation:
            raise ValueError("V3 input presentation mismatch")
        validate_model_binding_consistency(self.source_binding, self.visible_elements, self.fact_ids)
        if tuple(item.element_id for item in self.visible_elements) != self.layout.element_ids:
            raise ValueError("V3 input visible elements are not layout-exact")
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("V3 input Fact IDs must be unique")
        if self.presentation == "threshold" and any(item.source_role != "threshold_token" for item in self.visible_elements):
            raise ValueError("V3 threshold input source role mismatch")
        expected = f"step3-v3-model-input:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'model_input_id'}))[:24]}"
        if self.model_input_id != expected:
            raise ValueError("V3 model input identity mismatch")
        return self


class PromptContractV3(StrictModel):
    schema_version: Literal["step3-response-prompt/3.0"] = PROMPT_VERSION_V3
    prompt_id: str = Field(pattern=PROMPT_ID_V3)
    allowed_presentations: tuple[PresentationV3, ...] = cast(tuple[PresentationV3, ...], SUPPORTED_PRESENTATIONS)
    response_contract: Literal["exact-single-card-document-inside-exact-assistant-envelope"] = (
        "exact-single-card-document-inside-exact-assistant-envelope"
    )
    prompt_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_prompt(self) -> "PromptContractV3":
        expected = f"step3-v3-prompt:{canonical_hash(self.model_dump(mode='json', exclude={'schema_version', 'prompt_id'}))[:24]}"
        if self.prompt_id != expected:
            raise ValueError("V3 prompt identity mismatch")
        return self


class SingleCardDraftCardV3(StrictModel):
    schema_version: Literal["step3-response-draft-card/3.0"] = RESULT_CARD_VERSION_V3
    contract_id: str = Field(pattern=CONTRACT_ID)
    source_candidate_id: str = Field(min_length=3)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: PresentationV3
    source_typed_fixture_file_sha256: str = Field(pattern=HASH)
    source_typed_fixture_sha256: str = Field(pattern=HASH)
    source_binding: ModelSourceBindingV3
    visible_elements: tuple[ModelVisibleElementV3, ...] = Field(min_length=1)
    layout: ModelLayoutV3
    disposition: Literal["draft"] = "draft"

    @model_validator(mode="after")
    def validate_card(self) -> "SingleCardDraftCardV3":
        if self.presentation != self.layout.presentation:
            raise ValueError("V3 result presentation mismatch")
        if tuple(item.element_id for item in self.visible_elements) != self.layout.element_ids:
            raise ValueError("V3 result visible elements are not layout-exact")
        validate_model_binding_consistency(self.source_binding, self.visible_elements, self.fact_ids)
        if self.presentation == "threshold" and any(item.source_role != "threshold_token" for item in self.visible_elements):
            raise ValueError("V3 threshold result source role mismatch")
        return self


class SingleCardResponseDocumentV3(StrictModel):
    schema_version: Literal["step3-response-result/3.0"] = RESULT_VERSION_V3
    draft_cards: tuple[SingleCardDraftCardV3, ...] = Field(min_length=1, max_length=1)


RESULT_SCHEMA_V3: dict[str, Any] = strict_schema(SingleCardResponseDocumentV3)


class GateCheckV3(StrictModel):
    schema_version: Literal["step3-response-gate-check/3.0"] = "step3-response-gate-check/3.0"
    check_id: str = Field(min_length=1)
    passed: bool
    detail: str = Field(min_length=1)


class SingleCardResponseGateV3(StrictModel):
    schema_version: Literal["step3-response-gate/3.0"] = GATE_VERSION_V3
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
    source_order_exact: bool
    source_role_exact: bool
    text_hash_exact: bool
    provenance_exact: bool
    layout_exact: bool
    no_unknown_fields: bool
    relation_text_absent: bool
    checks: tuple[GateCheckV3, ...] = Field(min_length=1)
    failure_codes: tuple[str, ...] = ()
    gate_sha256: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def validate_gate(self) -> "SingleCardResponseGateV3":
        flags = (
            self.envelope_exact, self.content_text, self.content_json, self.result_version_exact,
            self.card_count_exact, self.result_schema, self.identity_exact, self.fact_ids_exact,
            self.source_binding_exact, self.source_text_exact, self.source_order_exact,
            self.source_role_exact, self.text_hash_exact, self.provenance_exact,
            self.layout_exact, self.no_unknown_fields, self.relation_text_absent,
        )
        if self.valid != all(flags) or self.valid != (not self.failure_codes):
            raise ValueError("V3 response gate validity mismatch")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
        if self.gate_sha256 != expected:
            raise ValueError("V3 response gate hash mismatch")
        return self


@dataclass(frozen=True)
class SingleCardInspectionV3:
    response: SingleCardResponseDocumentV3 | None
    gate: SingleCardResponseGateV3


def build_single_card_model_input_v3(compilation: Step3CompilationV3, index: int = 0) -> SingleCardModelInputV3:
    contract = compilation.contracts[index]
    projection: ModelCardV3 = compilation.model_input.cards[index]
    if contract.contract_id != projection.contract_id or contract.presentation != projection.presentation:
        raise ValueError("V3 contract/model pairing mismatch")
    payload = {
        "source_typed_fixture_file_sha256": compilation.source_typed_fixture_file_sha256,
        "source_typed_fixture_sha256": compilation.source_typed_fixture_sha256,
        "contract_id": contract.contract_id,
        "source_candidate_id": contract.source_candidate_id,
        "fact_ids": contract.fact_ids,
        "presentation": contract.presentation,
        "source_binding": projection.source_binding,
        "visible_elements": projection.visible_elements,
        "layout": projection.layout,
    }
    return SingleCardModelInputV3(model_input_id=f"step3-v3-model-input:{canonical_hash(payload)[:24]}", **payload)


def build_single_card_prompt_v3() -> PromptContractV3:
    text = (
        "Return exactly one draft card that echoes MODEL_INPUT_JSON. Preserve contract, candidate, Facts, "
        "both fixture hashes, source binding, every visible element's id/text/order/role/text hash/full "
        "provenance and layout exactly. Preserve threshold as threshold. Do not add fields, text, relations, "
        "OCR corrections, inferred content, regrouping, or branding."
    )
    payload = {
        "allowed_presentations": SUPPORTED_PRESENTATIONS,
        "response_contract": "exact-single-card-document-inside-exact-assistant-envelope",
        "prompt_text": text,
    }
    return PromptContractV3(prompt_id=f"step3-v3-prompt:{canonical_hash(payload)[:24]}", prompt_text=text)


def build_fake_single_card_result_v3(model_input: SingleCardModelInputV3) -> SingleCardResponseDocumentV3:
    card_payload = model_input.model_dump(mode="python", exclude={"schema_version", "model_input_id"})
    return SingleCardResponseDocumentV3(draft_cards=(SingleCardDraftCardV3(**card_payload),))


def encode_single_card_response_v3(result: SingleCardResponseDocumentV3) -> bytes:
    content = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    envelope = {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _gate(
    model_input: SingleCardModelInputV3,
    response: SingleCardResponseDocumentV3 | None,
    flags: dict[str, bool],
    failures: list[str],
) -> SingleCardResponseGateV3:
    card = response.draft_cards[0] if response else None
    identity = bool(card and card.contract_id == model_input.contract_id and card.source_candidate_id == model_input.source_candidate_id and card.presentation == model_input.presentation)
    facts = bool(card and card.fact_ids == model_input.fact_ids)
    binding = bool(card and card.source_binding == model_input.source_binding and card.source_typed_fixture_file_sha256 == model_input.source_typed_fixture_file_sha256 and card.source_typed_fixture_sha256 == model_input.source_typed_fixture_sha256)
    text = bool(card and tuple(item.source_text for item in card.visible_elements) == tuple(item.source_text for item in model_input.visible_elements))
    order = bool(card and tuple(item.source_order for item in card.visible_elements) == tuple(item.source_order for item in model_input.visible_elements))
    role = bool(card and tuple(item.source_role for item in card.visible_elements) == tuple(item.source_role for item in model_input.visible_elements))
    hashes = bool(card and tuple(item.text_sha256 for item in card.visible_elements) == tuple(item.text_sha256 for item in model_input.visible_elements))
    provenance = bool(card and tuple(item.provenance for item in card.visible_elements) == tuple(item.provenance for item in model_input.visible_elements))
    layout = bool(card and card.layout == model_input.layout)
    values = {
        "envelope_exact": flags.get("envelope_exact", False), "content_text": flags.get("content_text", False),
        "content_json": flags.get("content_json", False), "result_version_exact": flags.get("result_version_exact", False),
        "card_count_exact": flags.get("card_count_exact", False), "result_schema": flags.get("result_schema", False),
        "identity_exact": identity, "fact_ids_exact": facts, "source_binding_exact": binding,
        "source_text_exact": text, "source_order_exact": order, "source_role_exact": role,
        "text_hash_exact": hashes, "provenance_exact": provenance, "layout_exact": layout,
        "no_unknown_fields": flags.get("no_unknown_fields", False), "relation_text_absent": True,
    }
    failures.extend(key for key, value in values.items() if not value and key not in failures)
    checks = tuple(GateCheckV3(check_id=key, passed=value, detail=key) for key, value in values.items())
    payload: dict[str, Any] = {**values, "valid": all(values.values()), "checks": checks, "failure_codes": tuple(failures)}
    draft = SingleCardResponseGateV3.model_construct(**{**payload, "gate_sha256": "0" * 64})
    payload["gate_sha256"] = canonical_hash(draft.model_dump(mode="json", exclude={"schema_version", "gate_sha256"}))
    return SingleCardResponseGateV3.model_validate(payload)


def inspect_single_card_response_v3(model_input: SingleCardModelInputV3, body: bytes) -> SingleCardInspectionV3:
    flags = {name: False for name in ("envelope_exact", "content_text", "content_json", "result_version_exact", "card_count_exact", "result_schema", "no_unknown_fields")}
    failures: list[str] = []
    response: SingleCardResponseDocumentV3 | None = None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        failures.append("wrapper-json-invalid")
        return SingleCardInspectionV3(None, _gate(model_input, None, flags, failures))
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
            if document.get("schema_version") != RESULT_VERSION_V3:
                failures.append("result-version-mismatch")
            else:
                flags["result_version_exact"] = True
            cards = document.get("draft_cards")
            if isinstance(cards, list) and len(cards) == 1:
                flags["card_count_exact"] = True
            else:
                failures.append("draft-cards-not-single")
            if set(document) != {"schema_version", "draft_cards"}:
                failures.append("result-fields-exact")
            else:
                try:
                    response = SingleCardResponseDocumentV3.model_validate(document)
                    flags["result_schema"] = True
                    flags["no_unknown_fields"] = True
                except Exception:
                    failures.append("result-schema")
    return SingleCardInspectionV3(response, _gate(model_input, response, flags, failures))


def make_single_card_inspector_v3(model_input: SingleCardModelInputV3) -> Callable[[bytes], SingleCardResponseGateV3]:
    return lambda body: inspect_single_card_response_v3(model_input, body).gate


def build_single_card_regression_cases_v3(model_input: SingleCardModelInputV3) -> dict[str, bytes]:
    valid = encode_single_card_response_v3(build_fake_single_card_result_v3(model_input))
    document = json.loads(json.loads(valid.decode("utf-8"))["choices"][0]["message"]["content"])

    def envelope(doc: dict[str, Any]) -> bytes:
        content = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    card = document["draft_cards"][0]
    return {
        "valid": valid,
        "empty_draft_cards": envelope({**document, "draft_cards": []}),
        "threshold_to_constraint": envelope({**document, "draft_cards": [{**card, "presentation": "constraint", "layout": {**card["layout"], "presentation": "constraint"}}]}),
        "missing_element": envelope({**document, "draft_cards": [{**card, "visible_elements": list(card["visible_elements"][:-1]), "layout": {**card["layout"], "element_ids": list(card["layout"]["element_ids"][:-1]), "threshold_blocks": list(card["layout"].get("threshold_blocks", []))[:-1]}}]}),
        "unknown_field": envelope({**document, "unknown": True}),
    }


def result_schema_v3_sha256() -> str:
    return canonical_hash(RESULT_SCHEMA_V3)


def prompt_v3_sha256() -> str:
    return canonical_hash(build_single_card_prompt_v3().model_dump(mode="json"))


__all__ = [
    "GATE_VERSION_V3", "INPUT_VERSION_V3", "PROMPT_VERSION_V3", "RESULT_CARD_VERSION_V3", "RESULT_SCHEMA_V3", "RESULT_VERSION_V3",
    "SingleCardDraftCardV3", "SingleCardInspectionV3", "SingleCardModelInputV3", "SingleCardResponseDocumentV3", "SingleCardResponseGateV3",
    "build_fake_single_card_result_v3", "build_single_card_model_input_v3", "build_single_card_prompt_v3", "build_single_card_regression_cases_v3",
    "encode_single_card_response_v3", "inspect_single_card_response_v3", "make_single_card_inspector_v3", "prompt_v3_sha256", "result_schema_v3_sha256",
]
