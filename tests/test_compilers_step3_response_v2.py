"""compilers.step3_response_v2 单测（T3 单卡回显契约；蓝本 test_compilers_step3_response.py）。

覆盖四面（方案 §4-4）：strict schema 快照、inspector 正/负样本（合规回显/字段
漂移/空数组逃逸/schema_version 错版）、构造函数与 v1 投影对拍、r810 真金样回放
（旧仓只读，语料缺席时跳过——与 test_gates_review_replay.py 同一先例）。
全部离线（T4 禁网守卫下运行）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from examcard.compilers.step3_card import Step3Compilation, compile_reviewed_fixture
from examcard.compilers.step3_response import (
    PRESENTATIONS as V1_REPRESENTATIVES,
    build_response_model_input,
)
from examcard.compilers.step3_response_v2 import (
    INPUT_VERSION_V2,
    RESULT_CARD_VERSION_V2,
    RESULT_SCHEMA_V2,
    RESULT_VERSION_V2,
    SUPPORTED_PRESENTATIONS,
    build_fake_single_card_result,
    build_single_card_model_input,
    build_single_card_prompt,
    build_single_card_regression_cases,
    encode_single_card_response,
    inspect_single_card_response,
    make_single_card_inspector,
)
from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash_r10x
from tests.fixtures.compilers_synthetic import make_reviewed_typed_evidence_fixture

SOURCE_SHA = "2" * 64
# strict schema 快照指纹（法定哈希口径）。pydantic 由 uv.lock 钉版；schema 发生任何
# 字节级漂移（字段增删、const 改动、required/additionalProperties 松动）都会击穿本值。
RESULT_SCHEMA_V2_CANONICAL_SHA256 = "0bc5741630370a72646950f875122b7189071e628b2e70fafbe0dea58220487f"

R810_RUN = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2") / (
    "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1"
)


@pytest.fixture(scope="module")
def compilation() -> Step3Compilation:
    return compile_reviewed_fixture(make_reviewed_typed_evidence_fixture(), source_fixture_sha256=SOURCE_SHA)


@pytest.fixture(scope="module")
def single_inputs(compilation: Step3Compilation):
    return tuple(
        build_single_card_model_input(contract, projected)
        for contract, projected in zip(compilation.contracts, compilation.model_input.cards, strict=True)
    )


def _pair_by_presentation(compilation: Step3Compilation, presentation: str):
    pair = next(
        (
            (contract, projected)
            for contract, projected in zip(compilation.contracts, compilation.model_input.cards, strict=True)
            if contract.presentation == presentation
        ),
        None,
    )
    assert pair is not None, presentation
    return pair


def _v1_view(contract: Any, projected: Any) -> SimpleNamespace:
    """v1 build_response_model_input 需要的组合卡视图（含 geometry；pilot 组合适配器同构）。"""
    return SimpleNamespace(
        contract_id=contract.contract_id,
        source_candidate_id=contract.source_candidate_id,
        fact_ids=contract.fact_ids,
        presentation=contract.presentation,
        source_binding=projected.source_binding,
        visible_elements=projected.visible_elements,
        layout=projected.layout,
        geometry=projected.geometry,
    )


# ---------------------------------------------------------------------------
# 1. strict schema 快照
# ---------------------------------------------------------------------------


def _object_nodes(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            yield node
        for value in node.values():
            yield from _object_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _object_nodes(value)


def test_result_schema_v2_pins_single_card_and_versions() -> None:
    draft_cards = RESULT_SCHEMA_V2["properties"]["draft_cards"]
    assert draft_cards["minItems"] == 1
    assert draft_cards["maxItems"] == 1
    assert RESULT_SCHEMA_V2["properties"]["schema_version"]["const"] == RESULT_VERSION_V2
    card_schema = RESULT_SCHEMA_V2["$defs"]["SingleCardDraftCard"]
    assert card_schema["properties"]["schema_version"]["const"] == RESULT_CARD_VERSION_V2
    assert card_schema["properties"]["disposition"]["const"] == "draft"
    assert set(card_schema["properties"]) == {
        "schema_version",
        "contract_id",
        "source_candidate_id",
        "fact_ids",
        "presentation",
        "source_binding",
        "visible_elements",
        "layout",
        "disposition",
    }
    # v2 有意收窄：geometry（全常量模型，零信息量）不进回显。
    assert "geometry" not in card_schema["properties"]


def test_result_schema_v2_is_strict_everywhere() -> None:
    nodes = list(_object_nodes(RESULT_SCHEMA_V2))
    assert nodes
    for node in nodes:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])


def test_result_schema_v2_snapshot_hash_is_frozen() -> None:
    assert canonical_hash_r10x(RESULT_SCHEMA_V2) == RESULT_SCHEMA_V2_CANONICAL_SHA256


# ---------------------------------------------------------------------------
# 2. 构造函数：五族覆盖、身份确定性、配对守卫、与 v1 投影对拍
# ---------------------------------------------------------------------------


def test_v2_covers_all_five_presentations(single_inputs) -> None:
    assert tuple(item.presentation for item in single_inputs) == SUPPORTED_PRESENTATIONS
    for item in single_inputs:
        assert item.schema_version == INPUT_VERSION_V2
        assert item.model_input_id.startswith("step3-v2-model-input:")
        assert item.source_fixture_sha256 == SOURCE_SHA


def test_model_input_identity_is_deterministic(compilation: Step3Compilation, single_inputs) -> None:
    rebuilt = tuple(
        build_single_card_model_input(contract, projected)
        for contract, projected in zip(compilation.contracts, compilation.model_input.cards, strict=True)
    )
    assert rebuilt == single_inputs
    assert len({item.model_input_id for item in single_inputs}) == len(single_inputs)


def test_pairing_guard_rejects_presentation_mismatch(compilation: Step3Compilation) -> None:
    table_contract, _ = _pair_by_presentation(compilation, "table")
    _, formula_projected = _pair_by_presentation(compilation, "formula")
    with pytest.raises(ValueError, match="pairing mismatch"):
        build_single_card_model_input(table_contract, formula_projected)


def test_pairing_guard_rejects_fixture_mismatch(compilation: Step3Compilation) -> None:
    table_contract, table_projected = _pair_by_presentation(compilation, "table")
    forged = SimpleNamespace(
        contract_id=table_contract.contract_id,
        source_candidate_id=table_contract.source_candidate_id,
        fact_ids=table_contract.fact_ids,
        presentation=table_contract.presentation,
        source_fixture_sha256="f" * 64,
        source_typed_contract_sha256=table_contract.source_typed_contract_sha256,
    )
    with pytest.raises(ValueError, match="source fixture differ"):
        build_single_card_model_input(forged, table_projected)


def test_v2_projection_parity_with_v1_representatives(compilation: Step3Compilation, single_inputs) -> None:
    """对拍 v1：三代表卡逐字段等值（v2 仅摊平并去 geometry/逐卡 fixture 哈希）。"""
    views = [_v1_view(*_pair_by_presentation(compilation, presentation)) for presentation in V1_REPRESENTATIVES]
    v1_input = build_response_model_input(views, source_fixture_sha256=SOURCE_SHA)
    v2_by_contract = {item.contract_id: item for item in single_inputs}
    for v1_card in v1_input.cards:
        v2 = v2_by_contract[v1_card.contract_id]
        assert v2.source_candidate_id == v1_card.source_candidate_id
        assert v2.fact_ids == v1_card.fact_ids
        assert v2.presentation == v1_card.presentation
        assert v2.source_binding == v1_card.source_binding
        assert v2.visible_elements == v1_card.visible_elements
        assert v2.layout == v1_card.layout
        assert v2.source_fixture_sha256 == v1_card.source_fixture_sha256


def test_prompt_contract_is_deterministic() -> None:
    first = build_single_card_prompt()
    second = build_single_card_prompt()
    assert first == second
    assert first.prompt_id.startswith("step3-v2-prompt:")
    assert first.allowed_presentations == SUPPORTED_PRESENTATIONS
    assert first.response_contract == "exact-single-card-document-inside-exact-assistant-envelope"


# ---------------------------------------------------------------------------
# 3. inspector 正/负样本
# ---------------------------------------------------------------------------


def test_valid_fake_echo_passes_gate_for_every_presentation(single_inputs) -> None:
    for model_input in single_inputs:
        body = encode_single_card_response(build_fake_single_card_result(model_input))
        inspection = inspect_single_card_response(model_input, body)
        assert inspection.response is not None, model_input.presentation
        assert inspection.gate.valid, (model_input.presentation, inspection.gate.failure_codes)
        assert inspection.gate.failure_codes == ()


def test_regression_cases_fail_closed_with_dedicated_codes(single_inputs) -> None:
    model_input = single_inputs[1]  # table 卡：layout 分支最全
    cases = build_single_card_regression_cases(model_input)
    expected_codes = {
        "wrapper_invalid": "wrapper-fields-exact",
        "empty_draft_cards_escape": "empty-draft-cards-escape",
        "result_version_echoed_input": "result-version-echoed-input",
        "result_version_wrong": "result-version-mismatch",
        "card_version_echoed_input": "card-version-echoed-input",
        "draft_cards_duplicated": "draft-cards-not-single",
        "source_text_drift": "source_text_exact",
        "fact_binding_drift": "result-schema",  # Fact 绑定漂移在 draft 卡自校验即拒
        "layout_drift": "layout_exact",
        "provenance_drift": "source_binding_exact",
        "unknown_top_level": "result-fields-exact",
        "nested_unknown_field": "result-schema",
        "result_missing_top_level": "draft-cards-not-array",
    }
    assert set(cases) == {"valid", *expected_codes}
    for name, body in cases.items():
        inspection = inspect_single_card_response(model_input, body)
        if name == "valid":
            assert inspection.gate.valid, inspection.gate.failure_codes
            continue
        assert not inspection.gate.valid, name
        assert expected_codes[name] in inspection.gate.failure_codes, (name, inspection.gate.failure_codes)


def test_single_card_inspector_adapter_returns_gate(single_inputs) -> None:
    model_input = single_inputs[0]
    inspector = make_single_card_inspector(model_input)
    gate = inspector(encode_single_card_response(build_fake_single_card_result(model_input)))
    assert gate.valid
    bad = inspector(b"not-json")
    assert not bad.valid
    assert "wrapper-json-invalid" in bad.failure_codes
    wrapper = inspector(b"[]")
    assert not wrapper.valid
    assert "wrapper-fields-exact" in wrapper.failure_codes


def test_cross_card_echo_is_field_drift(single_inputs) -> None:
    """整卡张冠李戴（回显了别的卡）必须判负——单卡语义下的跨样本防线。"""
    body = encode_single_card_response(build_fake_single_card_result(single_inputs[1]))
    inspection = inspect_single_card_response(single_inputs[2], body)
    assert not inspection.gate.valid
    assert "identity_exact" in inspection.gate.failure_codes


# ---------------------------------------------------------------------------
# 4. r810 真金样回放（旧仓只读；语料缺席跳过——replay 测试同一先例）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden() -> Step3Compilation:
    fixture_bytes = (R810_RUN / "reviewed-typed-evidence-fixture.json").read_bytes()
    fixture = ReviewedTypedEvidenceFixture.model_validate_json(fixture_bytes.decode("utf-8"))
    return compile_reviewed_fixture(fixture, source_fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest())


@pytest.mark.skipif(not R810_RUN.is_dir(), reason="r810 语料缺席（旧仓 reviewed 语料不在本机）")
class TestR810GoldenReplay:
    def test_every_golden_card_builds_and_fake_echo_passes(self, golden: Step3Compilation) -> None:
        pairs = list(zip(golden.contracts, golden.model_input.cards, strict=True))
        # 语料口径勘误（T3 实测钉死）：r810 typed-evidence 链编译 6 张契约（多科目
        # shizheng/ganghang/tongxin；v1 驱动 dry-run 报告 compiled_contract_count=6
        # 同证）。方案里的「黄金五章 16 卡」属 r2242 step4 发布语料（V1 学习卡形状，
        # 无 typed layout/source_binding），结构上不可喂 v2 回显——详见 v2 驱动头注。
        assert len(pairs) == 6
        for contract, projected in pairs:
            model_input = build_single_card_model_input(contract, projected)
            body = encode_single_card_response(build_fake_single_card_result(model_input))
            inspection = inspect_single_card_response(model_input, body)
            assert inspection.gate.valid, (contract.contract_id, inspection.gate.failure_codes)

    def test_representative_trio_matches_v1_projection(self, golden: Step3Compilation) -> None:
        views = [_v1_view(*_pair_by_presentation(golden, presentation)) for presentation in V1_REPRESENTATIVES]
        v1_input = build_response_model_input(views, source_fixture_sha256=golden.source_fixture_sha256)
        for presentation, v1_card in zip(V1_REPRESENTATIVES, v1_input.cards, strict=True):
            contract, projected = _pair_by_presentation(golden, presentation)
            v2 = build_single_card_model_input(contract, projected)
            assert v2.contract_id == v1_card.contract_id
            assert v2.source_candidate_id == v1_card.source_candidate_id
            assert v2.fact_ids == v1_card.fact_ids
            assert v2.source_binding == v1_card.source_binding
            assert v2.visible_elements == v1_card.visible_elements
            assert v2.layout == v1_card.layout
            assert v2.source_fixture_sha256 == v1_card.source_fixture_sha256
