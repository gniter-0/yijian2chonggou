"""typed contract 提取面测试（gates/review/extraction）。

三层覆盖，全离线（规范 T4）：
1. 五呈现契约构造器：结构、r8x 谱系封印、内容寻址身份的确定性与期隔离；
2. 圈选 spec 与装配管线的 fail-closed 边界（角色/网格/边/序/噪声/跨源）；
3. r810 金标回放（旧仓真实语料，缺席跳过）：用新提取器从裸字段重建全部六候选的
   契约与内核候选，逐字节对拍既有 reviewed fixture——这正是「提取面 = 生产方」的
   构造路径证明（语料呈现覆盖 table/ordered_list/formula；diagram/constraint 由
   第 1 层合成用例覆盖，属旧语料局限，见任务书验证口径）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from examcard.gates.review.decisions import HumanDecision, validate_human_decision
from examcard.gates.review.extraction import (
    ROLE_BY_PRESENTATION,
    ExtractionSource,
    ManualSelectionSpec,
    build_extraction_decision,
    build_extraction_queue,
    build_queue_item,
    build_typed_contract,
    element_provenance,
    extract_reviewed_fixture,
    make_extraction_source,
    make_manual_selection_spec,
    make_source_page_binding,
    mark_reviewed,
)
from examcard.gates.review.fixtures import build_reviewed_candidate
from examcard.gates.review.queue import validate_queue_chain
from examcard.knowledge.contracts.admission_fixtures import (
    DiagramContract,
    ReviewedTypedEvidenceFixture,
    TableContract,
)
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash

from tests.fixtures.admission_kernel import (
    ANCHOR_IDS,
    EVIDENCE_ID,
    FACT_ID,
    RECORD_ID,
    SOURCE_MANIFEST_SHA256,
    SUBJECT_ID,
    make_source_page,
)
from tests.conftest import legacy_path, requires_legacy_corpus

_R810_REL = "data/semantic_llm_v2/phase2r810_r89_reviewed_admission_authorization_run1"
R810_RUN = legacy_path(_R810_REL)


def _source(presentation: str, **overrides: Any) -> ExtractionSource:
    payload: dict[str, Any] = {
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": presentation,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "anchor_ids": ANCHOR_IDS,
        "pages": (make_source_page(),),
    }
    payload.update(overrides)
    return make_extraction_source(**payload)


def _spec(presentation: str, bindings: tuple[dict[str, Any], ...], **overrides: Any) -> ManualSelectionSpec:
    payload: dict[str, Any] = {
        "selection_id": f"selection:test:{presentation}-0001",
        "subject_id": SUBJECT_ID,
        "record_id": RECORD_ID,
        "fact_id": FACT_ID,
        "evidence_id": EVIDENCE_ID,
        "presentation": presentation,
        "page_ref_id": "page:test:0001",
        "bindings": tuple({"source_role": ROLE_BY_PRESENTATION[presentation], **item} for item in bindings),
        "excluded_noise_kinds": ("watermark",),
        "reviewer": "tester",
        "notes": "explicit human source-page selection",
        "recorded_at": "2026-08-13T00:00:00Z",
    }
    payload.update(overrides)
    return make_manual_selection_spec(payload)


class TestTypedContractBuilders:
    def test_formula_contract_structure_and_lineage(self) -> None:
        source = _source("formula")
        contract = build_typed_contract(
            _spec("formula", ({"text": "E=mc^2"}, {"text": "m=E/c^2"})), source, identity_period="x1"
        )
        assert contract.presentation == "formula"
        assert [item.token_order for item in contract.tokens] == [1, 2]
        assert all(item.provenance.anchor_ids == ANCHOR_IDS for item in contract.tokens)
        # r8x 谱系封印：契约哈希 = legacy canonical_hash（排除封印字段本身），非 common 域。
        assert contract.contract_sha256 == canonical_hash(
            contract.model_dump(mode="json", exclude={"contract_sha256"})
        )

    def test_constraint_contract_orders(self) -> None:
        contract = build_typed_contract(
            _spec("constraint", ({"text": "坡度不大于 3%"}, {"text": "净高不小于 5m"})),
            _source("constraint"),
            identity_period="x1",
        )
        assert contract.presentation == "constraint"
        assert [item.token_order for item in contract.tokens] == [1, 2]

    def test_ordered_list_contract_markers(self) -> None:
        contract = build_typed_contract(
            _spec("ordered_list", ({"text": "先斩后奏", "marker": "（一）"}, {"text": "先奏后斩", "marker": "（二）"})),
            _source("ordered_list"),
            identity_period="x1",
        )
        assert [item.order for item in contract.items] == [1, 2]
        assert [item.marker for item in contract.items] == ["（一）", "（二）"]

    def test_table_contract_derived_row_major_grid(self) -> None:
        contract = build_typed_contract(
            _spec(
                "table",
                ({"text": "指标"}, {"text": "限值"}, {"text": "宽度"}, {"text": "3.5m"}),
                table_rows=2,
                table_columns=2,
            ),
            _source("table"),
            identity_period="x1",
        )
        assert isinstance(contract, TableContract)
        assert [(cell.row, cell.column) for cell in contract.cells] == [(1, 1), (1, 2), (2, 1), (2, 2)]
        assert all(cell.row_span == cell.column_span == 1 for cell in contract.cells)

    def test_table_contract_explicit_coordinates_support_spans(self) -> None:
        contract = build_typed_contract(
            _spec(
                "table",
                (
                    {"text": "总表头", "row": 1, "column": 1, "row_span": 1, "column_span": 2},
                    {"text": "甲", "row": 2, "column": 1, "row_span": 1, "column_span": 1},
                    {"text": "乙", "row": 2, "column": 2, "row_span": 1, "column_span": 1},
                ),
                table_rows=2,
                table_columns=2,
            ),
            _source("table"),
            identity_period="x1",
        )
        assert contract.cells[0].column_span == 2
        assert [cell.reading_order for cell in contract.cells] == [1, 2, 3]

    def test_diagram_contract_nodes_and_edges(self) -> None:
        contract = build_typed_contract(
            _spec(
                "diagram",
                ({"text": "项目经理"}, {"text": "技术负责人"}, {"text": "施工员"}),
                diagram_edges=((1, 2), (2, 3)),
            ),
            _source("diagram"),
            identity_period="x1",
        )
        assert isinstance(contract, DiagramContract)
        assert [node.reading_order for node in contract.nodes] == [1, 2, 3]
        assert len(contract.edges) == 2
        assert contract.edges[0].source_node_id == contract.nodes[0].element_id
        assert contract.edges[1].target_node_id == contract.nodes[2].element_id

    def test_content_addressed_ids_deterministic_and_period_scoped(self) -> None:
        source = _source("formula")
        spec = _spec("formula", ({"text": "E=mc^2"},))
        first = build_typed_contract(spec, source, identity_period="x1")
        second = build_typed_contract(spec, source, identity_period="x1")
        other_period = build_typed_contract(spec, source, identity_period="x2")
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert first.tokens[0].element_id.startswith("formula:x1:")
        assert other_period.tokens[0].element_id.startswith("formula:x2:")
        assert first.contract_sha256 != other_period.contract_sha256

    def test_explicit_element_ids_and_orders_respected(self) -> None:
        contract = build_typed_contract(
            _spec(
                "ordered_list",
                (
                    {"text": "乙", "element_id": "item:legacy:002", "order": 2},
                    {"text": "甲", "element_id": "item:legacy:001", "order": 1},
                ),
            ),
            _source("ordered_list"),
            identity_period="x1",
        )
        assert [item.element_id for item in contract.items] == ["item:legacy:001", "item:legacy:002"]
        assert [item.text for item in contract.items] == ["甲", "乙"]

    def test_unknown_page_ref_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="not part of the extraction source"):
            build_typed_contract(
                _spec("formula", ({"text": "E=mc^2"},), page_ref_id="page:test:9999"),
                _source("formula"),
                identity_period="x1",
            )

    def test_provenance_rebuild_matches_source_fields(self) -> None:
        source = _source("formula")
        provenance = element_provenance(source, "page:test:0001")
        assert provenance.anchor_ids == source.anchor_ids
        assert provenance.image_sha256 == source.pages[0].image_sha256
        assert provenance.provenance_sha256 == canonical_hash(
            provenance.model_dump(mode="json", exclude={"provenance_sha256"})
        )


class TestSpecFailClosed:
    def test_role_presentation_mismatch(self) -> None:
        with pytest.raises(ValueError, match="binding role does not match"):
            _spec("table", ({"source_role": "formula_token", "text": "x"},), table_rows=1, table_columns=1)

    def test_noise_text_rejected(self) -> None:
        with pytest.raises(ValueError, match="excluded noise"):
            _spec("formula", ({"text": "扫码关注环球网校"},))

    def test_diagram_edge_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="out-of-range edge"):
            _spec("diagram", ({"text": "甲"}, {"text": "乙"}), diagram_edges=((1, 3),))

    def test_edges_on_non_diagram_rejected(self) -> None:
        with pytest.raises(ValueError, match="only diagram selections"):
            _spec("formula", ({"text": "x=1"},), diagram_edges=((1, 1),))

    def test_table_requires_dimensions(self) -> None:
        with pytest.raises(ValueError, match="grid dimensions"):
            _spec("table", ({"text": "x"},))

    def test_derived_grid_must_be_complete(self) -> None:
        with pytest.raises(ValueError, match="complete grid"):
            _spec("table", ({"text": "x"},), table_rows=2, table_columns=2)

    def test_mixed_explicit_and_derived_coordinates_rejected(self) -> None:
        with pytest.raises(ValueError, match="mix explicit and derived"):
            _spec(
                "table",
                ({"text": "a", "row": 1, "column": 1, "row_span": 1, "column_span": 1}, {"text": "b"}),
                table_rows=1,
                table_columns=2,
            )

    def test_partial_cell_coordinates_rejected(self) -> None:
        with pytest.raises(ValueError, match="completely or not at all"):
            _spec("table", ({"text": "a", "row": 1},), table_rows=1, table_columns=1)

    def test_explicit_orders_must_be_complete_permutation(self) -> None:
        with pytest.raises(ValueError, match="complete 1..N permutation"):
            _spec("formula", ({"text": "a", "order": 1}, {"text": "b", "order": 3}))

    def test_explicit_element_ids_must_cover_every_binding(self) -> None:
        with pytest.raises(ValueError, match="cover every binding"):
            _spec("formula", ({"text": "a", "element_id": "formula:legacy:001"}, {"text": "b"}))

    def test_marker_outside_ordered_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="ordered-list items"):
            _spec("formula", ({"text": "a", "marker": "一"},))

    def test_coordinates_outside_table_rejected(self) -> None:
        with pytest.raises(ValueError, match="only table cells"):
            _spec("formula", ({"text": "a", "row": 1, "column": 1, "row_span": 1, "column_span": 1},))

    def test_spec_seal_tamper_rejected(self) -> None:
        spec = _spec("formula", ({"text": "E=mc^2"},))
        raw = spec.model_dump(mode="json")
        raw["notes"] = "tampered human note"
        with pytest.raises(ValueError, match="hash mismatch"):
            ManualSelectionSpec.model_validate(raw)


class TestExtractionAssembly:
    def test_pipeline_builds_fixture_and_conserves_dispositions(self) -> None:
        spec = _spec("formula", ({"text": "E=mc^2"},))
        sources = (
            _source("formula"),
            _source("table", evidence_id="evidence:test:0002"),
        )
        batch = extract_reviewed_fixture(sources, (spec,), identity_period="x1")
        assert batch.fixture.reviewed_candidate_count == 1
        assert batch.fixture.queue_sha256 == batch.queue_sha256
        assert batch.manifest.decision_count == 1
        validate_queue_chain(batch.queue_sha256, (batch.manifest, *batch.manifest.decisions))
        reviewed, untouched = batch.queue_items
        assert reviewed.disposition == "reviewed_candidate"
        assert reviewed.last_decision_id == batch.manifest.decisions[0].decision_id
        assert untouched.disposition == "manual_review"  # 处置守恒：未圈选项不升格
        assert untouched.last_decision_id is None
        candidate = batch.fixture.candidates[0]
        assert candidate.typed_contract_sha256 == batch.manifest.decisions[0].typed_contract_sha256
        assert candidate.source_decision_id == batch.manifest.decisions[0].decision_id

    def test_edged_diagram_selection_passes_the_pipeline_p4b(self) -> None:
        # P4b：带边 diagram 圈选全链无拒（门放行布局边）；快照/授权面见装配测试。
        spec = _spec(
            "diagram",
            ({"text": "项目经理"}, {"text": "技术负责人"}, {"text": "施工员"}),
            diagram_edges=((1, 2), (2, 3)),
        )
        batch = extract_reviewed_fixture((_source("diagram"),), (spec,), identity_period="x1")
        assert batch.fixture.reviewed_candidate_count == 1
        contract = batch.fixture.candidates[0].typed_contract
        assert isinstance(contract, DiagramContract)
        assert len(contract.nodes) == 3 and len(contract.edges) == 2
        assert all(edge.layout_only for edge in contract.edges)

    def test_decision_passes_kernel_three_way_validation(self) -> None:
        spec = _spec("formula", ({"text": "E=mc^2"},))
        source = _source("formula")
        queue_items, chain = build_extraction_queue((source,), identity_period="x1")
        decision = build_extraction_decision(spec, source, queue_items[0], queue_sha256=chain, identity_period="x1")

        class _Ref:
            candidate_id = decision.candidate_id

        validate_human_decision(decision, queue_items[0], _Ref())
        assert decision.decision_id.startswith("decision:r84:human:x1-")

    def test_spec_without_matching_source_fails(self) -> None:
        spec = _spec("formula", ({"text": "E=mc^2"},), fact_id="fact:test:9999")
        with pytest.raises(ValueError, match="no matching extraction source"):
            extract_reviewed_fixture((_source("formula"),), (spec,), identity_period="x1")

    def test_source_claimed_twice_fails(self) -> None:
        spec_a = _spec("formula", ({"text": "E=mc^2"},))
        spec_b = _spec("formula", ({"text": "F=ma"},), selection_id="selection:test:formula-0002")
        with pytest.raises(ValueError, match="already claimed"):
            extract_reviewed_fixture((_source("formula"),), (spec_a, spec_b), identity_period="x1")

    def test_duplicate_source_projection_fails(self) -> None:
        with pytest.raises(ValueError, match="duplicate extraction source"):
            extract_reviewed_fixture((_source("formula"), _source("formula")), (), identity_period="x1")

    def test_decision_rejects_foreign_queue_item(self) -> None:
        spec = _spec("formula", ({"text": "E=mc^2"},))
        source = _source("formula")
        foreign = build_queue_item(_source("formula", record_id="record:test:0002"), identity_period="x1")
        with pytest.raises(ValueError, match="not derived from this extraction source"):
            build_extraction_decision(spec, source, foreign, queue_sha256="d" * 64, identity_period="x1")

    def test_mark_reviewed_rejects_foreign_decision(self) -> None:
        spec = _spec("formula", ({"text": "E=mc^2"},))
        source = _source("formula")
        queue_items, chain = build_extraction_queue((source,), identity_period="x1")
        decision = build_extraction_decision(spec, source, queue_items[0], queue_sha256=chain, identity_period="x1")
        foreign = build_queue_item(_source("formula", record_id="record:test:0002"), identity_period="x1")
        with pytest.raises(ValueError, match="crossed queue item identity"):
            mark_reviewed(foreign, decision)

    def test_identity_period_grammar_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="identity period"):
            build_queue_item(_source("formula"), identity_period="X1")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _contract_elements(contract: Any) -> tuple[Any, ...]:
    for field in ("nodes", "cells", "items", "tokens"):
        elements = getattr(contract, field, None)
        if elements:
            return tuple(elements)
    raise AssertionError("corpus contract has no elements")


def _element_order(element: Any) -> int:
    for field in ("reading_order", "order", "token_order"):
        value = getattr(element, field, None)
        if value is not None:
            return int(value)
    raise AssertionError("corpus element has no order field")


@pytest.fixture(scope="module")
def corpus_fixture() -> ReviewedTypedEvidenceFixture:
    return ReviewedTypedEvidenceFixture.model_validate(_read(R810_RUN / "reviewed-typed-evidence-fixture.json"))


@requires_legacy_corpus(_R810_REL)
class TestR810GoldenReplay:
    """r810 六候选（table×2 / ordered_list×3 / formula×1）逐字节重建对拍。"""

    def test_source_page_bindings_rebuild_byte_identical(self, corpus_fixture: ReviewedTypedEvidenceFixture) -> None:
        for candidate in corpus_fixture.candidates:
            for page in candidate.pages:
                rebuilt = make_source_page_binding(
                    page_ref_id=page.page_ref_id,
                    page=page.page,
                    source_file=page.source_file,
                    image_path=page.image_path,
                    image_sha256=page.image_sha256,
                    image_width=page.image_width,
                    image_height=page.image_height,
                    page_sha256=page.page_sha256,
                    source_manifest_sha256=page.source_manifest_sha256,
                )
                assert rebuilt.model_dump(mode="json") == page.model_dump(mode="json")

    def _replay_source_and_spec(self, candidate: Any) -> tuple[ExtractionSource, ManualSelectionSpec]:
        contract = candidate.typed_contract
        elements = _contract_elements(contract)
        page_refs = {element.provenance.page_ref_id for element in elements}
        assert len(page_refs) == 1, "corpus contract unexpectedly binds multiple source pages"
        source = make_extraction_source(
            subject_id=candidate.subject_id,
            record_id=candidate.record_id,
            fact_id=candidate.fact_id,
            evidence_id=candidate.evidence_id,
            presentation=candidate.presentation,
            source_manifest_sha256=candidate.source_manifest_sha256,
            anchor_ids=candidate.anchor_ids,
            pages=candidate.pages,
        )
        bindings = []
        for element in elements:
            binding: dict[str, Any] = {
                "source_role": ROLE_BY_PRESENTATION[candidate.presentation],
                "text": element.text,
                "element_id": element.element_id,
                "order": _element_order(element),
            }
            if candidate.presentation == "ordered_list":
                binding["marker"] = element.marker
            if candidate.presentation == "table":
                binding.update(
                    row=element.row, column=element.column, row_span=element.row_span, column_span=element.column_span
                )
            bindings.append(binding)
        payload: dict[str, Any] = {
            "selection_id": f"selection:replay:{candidate.candidate_id.rsplit(':', 1)[-1]}",
            "subject_id": candidate.subject_id,
            "record_id": candidate.record_id,
            "fact_id": candidate.fact_id,
            "evidence_id": candidate.evidence_id,
            "presentation": candidate.presentation,
            "page_ref_id": next(iter(page_refs)),
            "bindings": tuple(bindings),
            "excluded_noise_kinds": ("watermark",),
            "reviewer": "replay-harness",
            "notes": "golden replay of the r810 production corpus",
            "recorded_at": "2026-08-13T00:00:00Z",
        }
        if candidate.presentation == "table":
            payload.update(table_rows=contract.rows, table_columns=contract.columns)
        return source, make_manual_selection_spec(payload)

    def test_contracts_rebuild_byte_identical(self, corpus_fixture: ReviewedTypedEvidenceFixture) -> None:
        replayed = 0
        for candidate in corpus_fixture.candidates:
            source, spec = self._replay_source_and_spec(candidate)
            rebuilt = build_typed_contract(spec, source, identity_period="replay")
            # 逐字节重建：整体 dump（含 contract_sha256 内封印）与语料完全一致。
            assert rebuilt.model_dump(mode="json") == candidate.typed_contract.model_dump(mode="json")
            # 候选级 typed_contract_sha256 是对整个契约 dump 的哈希（含内封印字段）。
            assert canonical_hash(rebuilt.model_dump(mode="json")) == candidate.typed_contract_sha256
            replayed += 1
        assert replayed == corpus_fixture.reviewed_candidate_count == 6
        assert {item.presentation for item in corpus_fixture.candidates} == {"table", "ordered_list", "formula"}

    def test_candidates_rebuild_via_kernel_builder(self, corpus_fixture: ReviewedTypedEvidenceFixture) -> None:
        manifest_decisions = {
            item["decision_id"]: HumanDecision.model_validate(item)
            for item in _read(R810_RUN / "phase2r84-human-decision-manifest.json")["decisions"]
        }

        class _QueueRef:
            def __init__(self, queue_item_id: str) -> None:
                self.queue_item_id = queue_item_id

        for candidate in corpus_fixture.candidates:
            decision = manifest_decisions[candidate.source_decision_id]
            rebuilt = build_reviewed_candidate(_QueueRef(candidate.queue_item_id), candidate.candidate_id, decision)
            assert rebuilt.model_dump(mode="json") == candidate.model_dump(mode="json")
            assert rebuilt.candidate_sha256 == candidate.candidate_sha256
