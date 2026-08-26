"""离线闸门与回归矩阵（拆分层 4）。

ExpectedCounts 期望计数、_walk_mappings/_no_forbidden_* 禁用项校验、
assess_offline_gate 组装 Phase2R4OfflineGate、build_regression_matrix 逐卡回归。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator
from examcard.knowledge.contracts.admission_fixtures import (
    EXPECTED_CARD_COUNT,
    EXPECTED_DISPLAY_TEXT_COUNT,
    EXPECTED_FACT_COUNT,
    EXPECTED_SEMANTIC_EDGE_COUNT,
    Step3UnifiedReviewedReleaseFixture,
    Step4PilotHandoffRefresh,
)

from .step4_contract_constants import (
    DEFAULT_FAKE_FILENAME_PREFIX,
    FORBIDDEN_MODEL_KEYS,
    FORBIDDEN_TEXT,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    RENDER_PRESENTATIONS,
)
from .step4_contract_models_phase import (
    Phase2R4BatchManifest,
    Phase2R4ContractBundle,
    Phase2R4FakeArtifactManifest,
    Phase2R4ModelInput,
    Phase2R4OfflineGate,
    Phase2R4RegressionResult,
    Phase2R4Request,
)
from .step4_contract_derive import derive_step4_bundle


@dataclass(frozen=True)
class ExpectedCounts:
    """门禁期望数量参数对象（设计 §4.3）；缺省即 gonglu 试点 v1 钉扎值。"""

    card_count: int = EXPECTED_CARD_COUNT
    fact_count: int = EXPECTED_FACT_COUNT
    display_text_count: int = EXPECTED_DISPLAY_TEXT_COUNT
    semantic_edge_count: int = EXPECTED_SEMANTIC_EDGE_COUNT
    prior_reviewed_indexes: tuple[int, ...] = (2, 3, 4, 6)


def _walk_mappings(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from _walk_mappings(child)


def _no_forbidden_model_keys(model_input: Phase2R4ModelInput, request: Phase2R4Request) -> bool:
    raw = {"model_input": model_input.model_dump(mode="json"), "request_model_input": request.model_input.model_dump(mode="json")}
    return not {
        key for mapping in _walk_mappings(raw) for key in mapping if key.casefold() in FORBIDDEN_MODEL_KEYS
    }


def _no_forbidden_text(model_input: Phase2R4ModelInput, artifacts: Phase2R4FakeArtifactManifest) -> bool:
    folded = json.dumps(
        {"model_input": model_input.model_dump(mode="json"), "artifacts": artifacts.model_dump(mode="json")},
        ensure_ascii=False,
    ).casefold()
    return not any(token.casefold() in folded for token in FORBIDDEN_TEXT)


def assess_offline_gate(
    *,
    fixture: Step3UnifiedReviewedReleaseFixture,
    handoff: Step4PilotHandoffRefresh,
    unified_fixture_sha256: str,
    handoff_sha256: str,
    bundle: Phase2R4ContractBundle,
    model_input: Phase2R4ModelInput,
    request: Phase2R4Request,
    artifacts: Phase2R4FakeArtifactManifest,
    batch: Phase2R4BatchManifest,
    freeze_valid: bool,
    expected: ExpectedCounts = ExpectedCounts(),
    filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX,
) -> Phase2R4OfflineGate:
    """纯计算门禁（设计 §4.3）：期望束在内存重算，freeze 结果与期望数量参数注入。

    与旧 `assess_gate` 的检查逻辑逐条一致；仅「读盘重算期望束 + freeze 审计」外壳
    改为参数（旧仓 phase 粘合负责传 `audit_frozen_history(...).valid` 与钉死数量）。
    """
    expected_bundle, expected_input, expected_request, expected_artifacts, expected_batch = derive_step4_bundle(
        fixture,
        handoff,
        unified_fixture_sha256=unified_fixture_sha256,
        handoff_sha256=handoff_sha256,
        filename_prefix=filename_prefix,
    )
    source_cards = fixture.cards
    facts = [fact_id for card in model_input.cards for fact_id in card.fact_ids]
    source_facts = [fact_id for card in source_cards for fact_id in card.fact_ids]
    source_card_ids = [card.source_card_id for card in source_cards]
    input_card_ids = [card.source_card_id for card in model_input.cards]
    rendered_texts = [text for artifact in artifacts.artifacts for text in artifact.rendered_visible_texts]
    source_texts = [text.visible_text for card in expected_input.cards for text in card.visible_texts]
    relation_values = [edge.layout_relation for card in model_input.cards for edge in card.semantic_layout_edges]
    checks = {
        "freeze_valid": freeze_valid,
        "source_fixture_valid": len(fixture.cards) == expected.card_count and fixture.reviewed_fact_count == expected.fact_count,
        "source_r319_handoff_valid": len(handoff.cards) == expected.card_count and tuple(item.card_index for item in handoff.reviewed_image_fixtures) == expected.prior_reviewed_indexes,
        "source_hashes_exact": bundle.source_unified_fixture_sha256 == unified_fixture_sha256 and bundle.source_r319_handoff_sha256 == handoff_sha256 and model_input.source_unified_fixture_sha256 == unified_fixture_sha256 and model_input.source_r319_handoff_sha256 == handoff_sha256,
        "card_exactly_once": len(model_input.cards) == expected.card_count and input_card_ids == source_card_ids and len(set(input_card_ids)) == expected.card_count,
        "fact_exactly_once": facts == source_facts and len(facts) == expected.fact_count and len(set(facts)) == expected.fact_count,
        "display_text_count_exact": sum(len(card.visible_texts) for card in model_input.cards) == expected.display_text_count and len(rendered_texts) == expected.display_text_count,
        "semantic_edge_count_exact": sum(len(card.semantic_layout_edges) for card in model_input.cards) == expected.semantic_edge_count,
        "presentation_dispatch_exact": all(card.render_presentation in RENDER_PRESENTATIONS and (card.render_presentation == ("constraint" if card.source_presentation == "threshold" else card.source_presentation)) for card in model_input.cards),
        "table_contracts_complete": all(
            (card.render_presentation == "table" and bool(card.table_contracts) and card.table_contracts == expected_input.cards[card.card_index - 1].table_contracts)
            or (card.render_presentation != "table" and not card.table_contracts)
            for card in model_input.cards
        ),
        "ordered_list_contracts_complete": all(
            (card.render_presentation == "ordered_list" and bool(card.ordered_list_contracts) and card.ordered_list_contracts == expected_input.cards[card.card_index - 1].ordered_list_contracts)
            or (card.render_presentation != "ordered_list" and not card.ordered_list_contracts)
            for card in model_input.cards
        ),
        "diagram_contracts_complete": all(
            (card.render_presentation == "diagram" and bool(card.diagram_node_ids) and bool(card.semantic_layout_edges) and card.diagram_node_ids == expected_input.cards[card.card_index - 1].diagram_node_ids and card.semantic_layout_edges == expected_input.cards[card.card_index - 1].semantic_layout_edges)
            or (card.render_presentation != "diagram" and not card.diagram_node_ids and not card.semantic_layout_edges)
            for card in model_input.cards
        ),
        "constraint_contracts_complete": all(
            (card.render_presentation == "constraint" and len(card.token_bindings) == len(card.visible_texts) and card.token_bindings == expected_input.cards[card.card_index - 1].token_bindings)
            or card.render_presentation == "formula"
            or (card.render_presentation not in {"constraint", "formula"} and not card.token_bindings)
            for card in model_input.cards
        ),
        "formula_contracts_complete": all(
            (card.render_presentation == "formula" and len(card.token_bindings) == len(card.visible_texts) and card.token_bindings == expected_input.cards[card.card_index - 1].token_bindings)
            or card.render_presentation == "constraint"
            or (card.render_presentation not in {"constraint", "formula"} and not card.token_bindings)
            for card in model_input.cards
        ),
        "source_provenance_exact": model_input == expected_input and bundle == expected_bundle,
        "display_text_verbatim": source_texts == rendered_texts and all(tuple(text.visible_text for text in card.visible_texts) == tuple(text.visible_text for text in expected_input.cards[card.card_index - 1].visible_texts) for card in model_input.cards),
        "relation_layout_only": all(edge.layout_only and not edge.visible_text_authorized for card in model_input.cards for edge in card.semantic_layout_edges) and not any(relation in rendered_texts for relation in relation_values),
        "fake_artifacts_exact": artifacts == expected_artifacts and all(artifact.width == OUTPUT_WIDTH and artifact.height == OUTPUT_HEIGHT and not artifact.materialized and not artifact.real_image for artifact in artifacts.artifacts),
        "batch_manifest_exact": batch == expected_batch,
        "no_cross_sample_grouping": all(card.sample_id == source_cards[card.card_index - 1].sample_id and card.fact_ids == source_cards[card.card_index - 1].fact_ids for card in model_input.cards),
        "no_forbidden_model_keys": _no_forbidden_model_keys(model_input, request),
        "no_forbidden_text": _no_forbidden_text(model_input, artifacts),
        "no_unknown_fields": True,
        "no_materialized_files": artifacts.materialized_file_count == 0,
        "no_real_calls": request.real_http_call_budget == 0 and request.real_image_generation_authorized is False and batch.batch_generation_authorized is False,
        "generic_contract_reusable": all(card.geometry.inferred_geometry is False and card.geometry.stable_canvas for card in model_input.cards) and set(card.render_presentation for card in model_input.cards) == set(RENDER_PRESENTATIONS),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return Phase2R4OfflineGate(
        valid=not failures,
        generic_step4_offline_ready=not failures,
        failure_codes=failures,
        **checks,
    )


def build_regression_matrix(
    *,
    fixture: Step3UnifiedReviewedReleaseFixture,
    handoff: Step4PilotHandoffRefresh,
    unified_fixture_sha256: str,
    handoff_sha256: str,
    bundle: Phase2R4ContractBundle,
    model_input: Phase2R4ModelInput,
    request: Phase2R4Request,
    artifacts: Phase2R4FakeArtifactManifest,
    batch: Phase2R4BatchManifest,
    freeze_valid: bool = True,
    expected: ExpectedCounts = ExpectedCounts(),
    filename_prefix: str = DEFAULT_FAKE_FILENAME_PREFIX,
) -> tuple[Phase2R4RegressionResult, ...]:
    """15 条 fail-closed 回归（逻辑与旧仓一致；盘面参数化）。

    对 gonglu 试点 fixture 的卡位假设保持原行为：cards[5]=table、cards[2]=
    ordered_list、cards[1]=diagram、cards[13]=formula；不满足时按原实现抛错。
    """

    def result(case_id: str, changed_input: Phase2R4ModelInput = model_input, changed_request: Phase2R4Request = request, changed_artifacts: Phase2R4FakeArtifactManifest = artifacts, changed_batch: Phase2R4BatchManifest = batch, expected_codes: tuple[str, ...] = ()) -> Phase2R4RegressionResult:
        try:
            gate = assess_offline_gate(
                fixture=fixture,
                handoff=handoff,
                unified_fixture_sha256=unified_fixture_sha256,
                handoff_sha256=handoff_sha256,
                bundle=bundle,
                model_input=changed_input,
                request=changed_request,
                artifacts=changed_artifacts,
                batch=changed_batch,
                freeze_valid=freeze_valid,
                expected=expected,
                filename_prefix=filename_prefix,
            )
            observed = gate.failure_codes
        except Exception as exc:
            observed = ("validation_exception", type(exc).__name__)
        return Phase2R4RegressionResult(case_id=case_id, expected_failure_codes=expected_codes, observed_failure_codes=observed, caught=set(expected_codes).issubset(observed))

    missing_input = model_input.model_copy(update={"cards": model_input.cards[:-1]})
    duplicate_fact_card = model_input.cards[0].model_copy(update={"fact_ids": (model_input.cards[0].fact_ids[0], model_input.cards[1].fact_ids[0])})
    duplicate_fact_input = model_input.model_copy(update={"cards": (duplicate_fact_card,) + model_input.cards[1:]})
    truncated_text = model_input.cards[0].visible_texts[0].model_copy(update={"visible_text": model_input.cards[0].visible_texts[0].visible_text[:-1]})
    truncated_card = model_input.cards[0].model_copy(update={"visible_texts": (truncated_text,)})
    truncated_input = model_input.model_copy(update={"cards": (truncated_card,) + model_input.cards[1:]})
    table_card = model_input.cards[5]
    table = table_card.table_contracts[0]
    wrong_cell = table.cells[0].model_copy(update={"text": table.cells[0].text + "X"})
    wrong_table = table.model_copy(update={"cells": (wrong_cell,) + table.cells[1:]})
    wrong_table_card = table_card.model_copy(update={"table_contracts": (wrong_table,)})
    wrong_table_input = model_input.model_copy(update={"cards": model_input.cards[:5] + (wrong_table_card,) + model_input.cards[6:]})
    list_card = model_input.cards[2]
    list_contract = list_card.ordered_list_contracts[0]
    missing_list = list_contract.model_copy(update={"items": list_contract.items[:-1]})
    missing_list_card = list_card.model_copy(update={"ordered_list_contracts": (missing_list,)})
    missing_list_input = model_input.model_copy(update={"cards": model_input.cards[:2] + (missing_list_card,) + model_input.cards[3:]})
    diagram_card = model_input.cards[1]
    missing_edge_card = diagram_card.model_copy(update={"semantic_layout_edges": diagram_card.semantic_layout_edges[:-1]})
    missing_edge_input = model_input.model_copy(update={"cards": (model_input.cards[0], missing_edge_card) + model_input.cards[2:]})
    leaked = artifacts.artifacts[1].model_copy(update={"rendered_visible_texts": artifacts.artifacts[1].rendered_visible_texts + (diagram_card.semantic_layout_edges[0].layout_relation,)})
    leaked_artifacts = artifacts.model_copy(update={"artifacts": (artifacts.artifacts[0], leaked) + artifacts.artifacts[2:]})
    formula_card = model_input.cards[13]
    token_index = next(index for index, token in enumerate(formula_card.token_bindings) if token.tokens)
    token = formula_card.token_bindings[token_index].model_copy(update={"tokens": formula_card.token_bindings[token_index].tokens[:-1]})
    formula_card_changed = formula_card.model_copy(update={"token_bindings": formula_card.token_bindings[:token_index] + (token,) + formula_card.token_bindings[token_index + 1:]})
    formula_input = model_input.model_copy(update={"cards": model_input.cards[:13] + (formula_card_changed,) + model_input.cards[14:]})
    stale_input = model_input.model_copy(update={"source_unified_fixture_sha256": "0" * 64})
    crossed_card = model_input.cards[0].model_copy(update={"sample_id": model_input.cards[7].sample_id})
    crossed_input = model_input.model_copy(update={"cards": (crossed_card,) + model_input.cards[1:]})
    mixed_card = model_input.cards[3].model_copy(update={"render_presentation": "table"})
    mixed_input = model_input.model_copy(update={"cards": model_input.cards[:3] + (mixed_card,) + model_input.cards[4:]})
    extra_text = artifacts.artifacts[0].model_copy(update={"rendered_visible_texts": artifacts.artifacts[0].rendered_visible_texts + ("extra",)})
    extra_artifacts = artifacts.model_copy(update={"artifacts": (extra_text,) + artifacts.artifacts[1:]})
    upgraded_batch = batch.model_copy(update={"batch_generation_authorized": True})
    cases = (
        result("missing_card", missing_input, request.model_copy(update={"model_input": missing_input}), expected_codes=("card_exactly_once", "fact_exactly_once", "display_text_count_exact")),
        result("duplicate_fact", duplicate_fact_input, request.model_copy(update={"model_input": duplicate_fact_input}), expected_codes=("fact_exactly_once", "source_provenance_exact")),
        result("truncated_text", truncated_input, request.model_copy(update={"model_input": truncated_input}), expected_codes=("source_provenance_exact", "display_text_verbatim")),
        result("wrong_table_cell", wrong_table_input, request.model_copy(update={"model_input": wrong_table_input}), expected_codes=("source_provenance_exact", "table_contracts_complete")),
        result("missing_list_item", missing_list_input, request.model_copy(update={"model_input": missing_list_input}), expected_codes=("source_provenance_exact", "ordered_list_contracts_complete")),
        result("missing_diagram_edge", missing_edge_input, request.model_copy(update={"model_input": missing_edge_input}), expected_codes=("source_provenance_exact", "semantic_edge_count_exact", "diagram_contracts_complete")),
        result("relation_leakage", model_input, request, leaked_artifacts, expected_codes=("fake_artifacts_exact", "relation_layout_only")),
        result("formula_token_loss", formula_input, request.model_copy(update={"model_input": formula_input}), expected_codes=("source_provenance_exact", "formula_contracts_complete")),
        result("old_provenance", stale_input, request.model_copy(update={"model_input": stale_input}), expected_codes=("source_hashes_exact", "source_provenance_exact")),
        result("cross_sample_grouping", crossed_input, request.model_copy(update={"model_input": crossed_input}), expected_codes=("source_provenance_exact", "no_cross_sample_grouping")),
        result("presentation_contract_mix", mixed_input, request.model_copy(update={"model_input": mixed_input}), expected_codes=("source_provenance_exact", "presentation_dispatch_exact", "table_contracts_complete")),
        result("extra_visible_text", model_input, request, extra_artifacts, expected_codes=("fake_artifacts_exact", "display_text_verbatim")),
        result("automatic_batch_upgrade", model_input, request, artifacts, upgraded_batch, expected_codes=("batch_manifest_exact", "no_real_calls")),
    )
    raw_input = model_input.model_dump(mode="json")
    raw_input["unknown_field"] = True
    try:
        Phase2R4ModelInput.model_validate(raw_input)
        unknown_observed = ("unknown_field_accepted",)
    except Exception:
        unknown_observed = ("unknown_field_rejected",)
    unknown = Phase2R4RegressionResult(case_id="unknown_field", expected_failure_codes=("unknown_field_rejected",), observed_failure_codes=unknown_observed, caught=unknown_observed == ("unknown_field_rejected",))
    raw_forbidden = model_input.model_dump(mode="json")
    raw_forbidden["golden_title"] = "forbidden"
    try:
        Phase2R4ModelInput.model_validate(raw_forbidden)
        forbidden_observed = ("forbidden_key_accepted",)
    except Exception:
        forbidden_observed = ("forbidden_key_rejected",)
    forbidden = Phase2R4RegressionResult(case_id="forbidden_model_key", expected_failure_codes=("forbidden_key_rejected",), observed_failure_codes=forbidden_observed, caught=forbidden_observed == ("forbidden_key_rejected",))
    return cases + (unknown, forbidden)
