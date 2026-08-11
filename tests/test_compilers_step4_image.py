"""compilers.step4_image 单测（蓝本：旧仓 test_phase2r11_...py；装载/授权链用例留旧仓）。"""
from __future__ import annotations

import json

import pytest

from examcard.compilers.step3_card import compile_reviewed_fixture
from examcard.compilers.step4_image import (
    ImageBatch,
    compile_image_batch,
    parse_strict_response,
    validate_response,
)
from examcard.gates.release.closure import build_deterministic_release
from tests.fixtures.compilers_synthetic import (
    make_reviewed_step3_fixture,
    make_reviewed_typed_evidence_fixture,
)

SOURCE_SHA = "5" * 64
FIXTURE_SHA = "6" * 64


@pytest.fixture(scope="module")
def batch() -> ImageBatch:
    compilation = compile_reviewed_fixture(make_reviewed_typed_evidence_fixture(), source_fixture_sha256=SOURCE_SHA)
    release = build_deterministic_release(
        compilation,
        source_fixture_path_label="tests/synthetic/reviewed-typed-evidence-fixture.json",
        expected_card_count=5,
    )
    reviewed = make_reviewed_step3_fixture(
        release.bundle.cards, source_fixture_sha256=SOURCE_SHA, release_sha256=release.bundle.release_sha256
    )
    assert reviewed.fixture_authorized and reviewed.reviewed_card_count == 5
    return compile_image_batch(reviewed.cards, fixture_sha256=FIXTURE_SHA)


def test_source_cards_bind_reviewed_contracts(batch: ImageBatch) -> None:
    assert len(batch.source_cards) == 5
    for card in batch.source_cards:
        assert card.source_fixture_sha256 == FIXTURE_SHA
        assert card.source_texts == tuple(item.text for item in card.typed_contract.display_elements)


def test_request_and_prompt_are_source_derived(batch: ImageBatch) -> None:
    assert batch.request.request_id.startswith("step4-image-request:r11:")
    assert batch.request.real_http_call_budget == 0
    assert batch.request.real_image_generation_authorized is False
    assert batch.prompt.prompt_text.startswith("Render the supplied source-derived image contracts exactly.")


def test_fake_backend_round_trip(batch: ImageBatch) -> None:
    validate_response(batch.request, batch.response)
    parsed = parse_strict_response(json.dumps(batch.response.model_dump(mode="json"), ensure_ascii=False))
    assert parsed == batch.response
    assert all(not card.materialized and not card.real_image for card in batch.response.cards)


def test_gate_valid_and_regressions_fail_closed(batch: ImageBatch) -> None:
    assert batch.gate is not None and batch.gate.valid
    assert len(batch.regressions) == 23
    assert all(item.caught for item in batch.regressions)
    assert batch.report is not None and batch.report.step4_offline_ready


def test_injected_authority_failure_fails_gate() -> None:
    compilation = compile_reviewed_fixture(make_reviewed_typed_evidence_fixture(), source_fixture_sha256=SOURCE_SHA)
    release = build_deterministic_release(
        compilation,
        source_fixture_path_label="tests/synthetic/reviewed-typed-evidence-fixture.json",
        expected_card_count=5,
    )
    reviewed = make_reviewed_step3_fixture(
        release.bundle.cards, source_fixture_sha256=SOURCE_SHA, release_sha256=release.bundle.release_sha256
    )
    result = compile_image_batch(reviewed.cards, fixture_sha256=FIXTURE_SHA, source_authority_valid=False)
    assert result.gate is not None and not result.gate.valid
    assert "reviewed-step3-only" in result.gate.failure_codes
