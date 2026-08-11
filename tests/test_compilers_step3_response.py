"""compilers.step3_response 单测（蓝本：旧仓 test_phase2r92_generic_step3_response_contract.py）。"""
from __future__ import annotations

import pytest

from examcard.compilers.step3_card import compile_reviewed_fixture
from examcard.compilers.step3_response import (
    PRESENTATIONS,
    ResponseModelInput,
    build_fake_result,
    build_prompt,
    build_regression_cases,
    build_request,
    build_response_model_input,
    encode_response,
    inspect_response,
    make_inner_inspector,
    strict_schema,
    ResponseDocument,
    ResponseEnvelope,
)
from tests.fixtures.compilers_synthetic import make_reviewed_typed_evidence_fixture

SOURCE_SHA = "2" * 64


@pytest.fixture(scope="module")
def model_input() -> ResponseModelInput:
    compilation = compile_reviewed_fixture(make_reviewed_typed_evidence_fixture(), source_fixture_sha256=SOURCE_SHA)
    by_presentation = {card.presentation: card for card in compilation.model_input.cards}
    ordered = [by_presentation[presentation] for presentation in PRESENTATIONS]
    cards = [
        type("CardView", (), {})() for _ in ordered
    ]
    for view, card, contract in zip(cards, ordered, [next(c for c in compilation.contracts if c.presentation == p) for p in PRESENTATIONS], strict=True):
        view.contract_id = contract.contract_id
        view.source_candidate_id = contract.source_candidate_id
        view.fact_ids = contract.fact_ids
        view.presentation = card.presentation
        view.source_binding = card.source_binding
        view.visible_elements = card.visible_elements
        view.layout = card.layout
        view.geometry = card.geometry
    return build_response_model_input(cards, source_fixture_sha256=SOURCE_SHA)


def test_model_input_pins_three_representatives(model_input: ResponseModelInput) -> None:
    assert model_input.card_count == 3
    assert tuple(card.presentation for card in model_input.cards) == PRESENTATIONS
    assert model_input.model_input_id.startswith("step3-r92-model-input:")


def test_valid_fake_response_passes_all_twelve_gates(model_input: ResponseModelInput) -> None:
    body = encode_response(build_fake_result(model_input))
    inspection = inspect_response(model_input, body)
    assert inspection.response is not None
    assert inspection.gate.valid
    assert inspection.gate.failure_codes == ()


def test_regression_cases_are_seventeen_mutations_and_fail_closed(model_input: ResponseModelInput) -> None:
    cases = build_regression_cases(model_input)
    assert len(cases) == 18  # 1 条 valid + 17 条变异（设计 §4.2）
    assert sum(1 for name in cases if name != "valid") == 17
    for name, body in cases.items():
        inspection = inspect_response(model_input, body)
        if name == "valid":
            assert inspection.gate.valid, name
        else:
            assert not inspection.gate.valid, name
            assert inspection.gate.failure_codes, name


def test_inner_inspector_adapter_returns_gate(model_input: ResponseModelInput) -> None:
    inspector = make_inner_inspector(model_input)
    gate = inspector(encode_response(build_fake_result(model_input)))
    assert gate.valid
    bad = inspector(b"not-json")
    assert not bad.valid
    assert "wrapper-json-invalid" in bad.failure_codes
    wrapper = inspector(b"[]")
    assert not wrapper.valid
    assert "wrapper-fields-exact" in wrapper.failure_codes


def test_request_identity_is_deterministic(model_input: ResponseModelInput) -> None:
    prompt = build_prompt()
    result_schema = strict_schema(ResponseDocument)
    envelope_schema = strict_schema(ResponseEnvelope)
    first = build_request(model_input, prompt, result_schema, envelope_schema, "3" * 64)
    second = build_request(model_input, prompt, result_schema, envelope_schema, "3" * 64)
    assert first == second
    assert first.request_id.startswith("step3-r92-offline-request:")
    assert first.network_calls == 0
