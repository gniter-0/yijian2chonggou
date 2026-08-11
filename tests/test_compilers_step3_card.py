"""compilers.step3_card 单测（蓝本：旧仓 test_phase2r9_generic_step3_card_compiler.py）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from examcard.compilers.step3_card import (
    PRESENTATIONS,
    Step3Compilation,
    compile_reviewed_fixture,
)
from tests.fixtures.compilers_synthetic import make_reviewed_typed_evidence_fixture

SOURCE_SHA = "1" * 64


@pytest.fixture(scope="module")
def compilation() -> Step3Compilation:
    return compile_reviewed_fixture(make_reviewed_typed_evidence_fixture(), source_fixture_sha256=SOURCE_SHA)


def test_compiles_all_five_presentations_exactly_once(compilation: Step3Compilation) -> None:
    assert len(compilation.contracts) == 5
    assert {contract.presentation for contract in compilation.contracts} == set(PRESENTATIONS)
    assert compilation.batch.not_applicable_presentations == ()
    counts = compilation.batch.presentation_counts
    assert (counts.diagram, counts.table, counts.ordered_list, counts.constraint, counts.formula) == (1, 1, 1, 1, 1)


def test_offline_gate_valid_and_downstream_disabled(compilation: Step3Compilation) -> None:
    assert compilation.gate.valid
    assert compilation.gate.failure_codes == ()
    assert compilation.readiness.generic_step3_offline_ready
    assert compilation.batch.batch_generation_authorized is False
    assert compilation.batch.production_cards_created is False
    assert compilation.request.network_call_budget == 0


def test_regression_matrix_is_fifteen_and_fail_closed(compilation: Step3Compilation) -> None:
    assert len(compilation.regressions) == 15
    assert all(item.caught for item in compilation.regressions)


def test_contract_identity_is_content_derived(compilation: Step3Compilation) -> None:
    for contract in compilation.contracts:
        assert contract.contract_id.startswith("step3-card-contract:")
        assert contract.source_fixture_sha256 == SOURCE_SHA
    assert len({contract.contract_id for contract in compilation.contracts}) == 5


def test_draft_mirrors_contracts_exactly(compilation: Step3Compilation) -> None:
    for artifact, contract in zip(compilation.draft.artifacts, compilation.contracts, strict=True):
        assert artifact.contract_id == contract.contract_id
        assert artifact.rendered_source_texts == tuple(item.text for item in contract.display_elements)
        assert artifact.materialized is False and artifact.real_image is False


def test_unauthorized_fixture_is_rejected() -> None:
    stub = SimpleNamespace(
        fixture_authorized=False, cards_created=False, step3_authorized=False, step4_authorized=False, candidates=()
    )
    with pytest.raises(ValueError, match="offline-only reviewed source fixture"):
        compile_reviewed_fixture(stub, source_fixture_sha256=SOURCE_SHA)  # type: ignore[arg-type]


def test_downstream_escalated_fixture_is_rejected() -> None:
    stub = SimpleNamespace(
        fixture_authorized=True, cards_created=False, step3_authorized=True, step4_authorized=False, candidates=()
    )
    with pytest.raises(ValueError, match="offline-only reviewed source fixture"):
        compile_reviewed_fixture(stub, source_fixture_sha256=SOURCE_SHA)  # type: ignore[arg-type]
