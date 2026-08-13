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


def test_regression_matrix_keeps_legacy_case_order_when_all_families_present(compilation: Step3Compilation) -> None:
    assert tuple(item.case_id for item in compilation.regressions) == (
        "unknown-field",
        "forbidden-model-key",
        "truncated-source-text",
        "duplicate-element",
        "wrong-table-cell",
        "missing-list-item",
        "formula-token-loss",
        "relation-leakage",
        "cross-sample-grouping",
        "presentation-contract-mix",
        "extra-visible-text",
        "old-provenance",
        "batch-upgrade",
        "production-card",
        "model-input-mutation",
    )


@pytest.fixture(scope="module")
def b1_family_mix_compilation() -> Step3Compilation:
    """P8 彩排 B1 同形批：table/constraint/formula，无 ordered_list/diagram 族（且首卡为 table）。"""
    return compile_reviewed_fixture(
        make_reviewed_typed_evidence_fixture(("table", "constraint", "formula")),
        source_fixture_sha256=SOURCE_SHA,
    )


def test_family_absent_probe_is_skipped_not_false_positive(b1_family_mix_compilation: Step3Compilation) -> None:
    case_ids = tuple(item.case_id for item in b1_family_mix_compilation.regressions)
    assert "missing-list-item" not in case_ids
    assert "wrong-table-cell" in case_ids
    assert "formula-token-loss" in case_ids
    assert "presentation-contract-mix" in case_ids
    assert len(b1_family_mix_compilation.regressions) == 14
    assert all(item.caught for item in b1_family_mix_compilation.regressions)


def test_family_mix_batch_composite_gate_recovers_valid(b1_family_mix_compilation: Step3Compilation) -> None:
    """aaef1e2 §2(a) 更正的回归钉：无 ordered_list 族批的复合 valid 不再被探针假阳拖垮。"""
    assert b1_family_mix_compilation.gate.valid
    assert b1_family_mix_compilation.gate.failure_codes == ()
    checks = {item.check_id: item.passed for item in b1_family_mix_compilation.gate.checks}
    assert checks["fail-closed-regressions"] is True
    assert set(b1_family_mix_compilation.batch.not_applicable_presentations) == {"diagram", "ordered_list"}


def test_no_table_batch_compiles_without_probe_payload_crash() -> None:
    """旧实现缺 table 族时在变异载荷构造处抛 IndexError；呈现族感知后应正常编译且门禁有效。"""
    compilation = compile_reviewed_fixture(
        make_reviewed_typed_evidence_fixture(("ordered_list", "constraint")),
        source_fixture_sha256=SOURCE_SHA,
    )
    case_ids = tuple(item.case_id for item in compilation.regressions)
    assert "wrong-table-cell" not in case_ids
    assert "formula-token-loss" not in case_ids
    assert "missing-list-item" in case_ids
    assert "presentation-contract-mix" in case_ids
    assert all(item.caught for item in compilation.regressions)
    assert compilation.gate.valid


def test_all_table_batch_skips_identity_mix_probe() -> None:
    """全 table 批无混族可造：presentation-contract-mix 恒等变异假阳改为跳过。"""
    compilation = compile_reviewed_fixture(
        make_reviewed_typed_evidence_fixture(("table",)),
        source_fixture_sha256=SOURCE_SHA,
    )
    case_ids = tuple(item.case_id for item in compilation.regressions)
    assert "presentation-contract-mix" not in case_ids
    assert case_ids == (
        "unknown-field",
        "forbidden-model-key",
        "truncated-source-text",
        "duplicate-element",
        "wrong-table-cell",
        "relation-leakage",
        "cross-sample-grouping",
        "extra-visible-text",
        "old-provenance",
        "batch-upgrade",
        "production-card",
        "model-input-mutation",
    )
    assert all(item.caught for item in compilation.regressions)
    assert compilation.gate.valid


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
