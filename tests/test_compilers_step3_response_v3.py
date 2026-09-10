"""Strict response-gate tests for the Step 3 V3 single-card echo."""
from __future__ import annotations

import json
from pathlib import Path

from examcard.compilers.step3_response_v3 import (
    build_fake_single_card_result_v3,
    build_single_card_model_input_v3,
    build_single_card_regression_cases_v3,
    encode_single_card_response_v3,
    inspect_single_card_response_v3,
)
from tests.fixtures.step3_v3 import compile_v3


def test_valid_response_preserves_threshold_and_full_provenance(tmp_path: Path) -> None:
    _path, fixture, _file_sha, compilation = compile_v3(tmp_path)
    model_input = build_single_card_model_input_v3(compilation)
    inspection = inspect_single_card_response_v3(
        model_input, encode_single_card_response_v3(build_fake_single_card_result_v3(model_input))
    )

    assert inspection.gate.valid is True
    assert inspection.response is not None
    card = inspection.response.draft_cards[0]
    assert card.presentation == "threshold"
    assert card.source_typed_fixture_sha256 == fixture.fixture_sha256
    assert card.visible_elements[0].source_role == "threshold_token"
    assert card.visible_elements[0].provenance == model_input.visible_elements[0].provenance
    assert card.visible_elements[0].text_sha256 == model_input.visible_elements[0].text_sha256


def test_response_regression_matrix_rejects_threshold_to_constraint_and_unknown_fields(tmp_path: Path) -> None:
    _path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    model_input = build_single_card_model_input_v3(compilation)
    results = {
        name: inspect_single_card_response_v3(model_input, body).gate
        for name, body in build_single_card_regression_cases_v3(model_input).items()
    }

    assert results["valid"].valid is True
    assert results["threshold_to_constraint"].valid is False
    assert results["threshold_to_constraint"].failure_codes
    assert results["missing_element"].valid is False
    assert results["unknown_field"].valid is False


def test_response_gate_rejects_changed_provenance_without_network(tmp_path: Path) -> None:
    _path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    model_input = build_single_card_model_input_v3(compilation)
    valid = encode_single_card_response_v3(build_fake_single_card_result_v3(model_input))
    document = json.loads(json.loads(valid.decode("utf-8"))["choices"][0]["message"]["content"])
    element = document["draft_cards"][0]["visible_elements"][0]
    element["provenance"]["page"] += 1
    body = json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(document)}}]},
        ensure_ascii=False,
    ).encode("utf-8")

    gate = inspect_single_card_response_v3(model_input, body).gate
    assert gate.valid is False
    assert "provenance_exact" in gate.failure_codes
