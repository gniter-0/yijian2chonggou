"""驱动侧转换器入口测试（gates/review/typed_evidence_export，T4b 权威签名件）。

全离线（规范 T4）：放行集装配、候选序=released 序、四哈希内部推导、
compile_reviewed_fixture 直通（硬约束 1）、fail-closed 边界全覆盖。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from examcard.compilers.step3_card import compile_reviewed_fixture
from examcard.gates.review.extraction import (
    ROLE_BY_PRESENTATION,
    extract_reviewed_fixture,
    make_extraction_source,
    make_manual_selection_spec,
)
from examcard.gates.review.typed_evidence_export import (
    ExplicitProductionAuthorization,
    export_reviewed_typed_evidence_fixture,
)
from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture

from tests.fixtures.admission_kernel import (
    ANCHOR_IDS,
    EVIDENCE_ID,
    FACT_ID,
    RECORD_ID,
    SOURCE_MANIFEST_SHA256,
    SUBJECT_ID,
    make_human_decision,
    make_queue_item,
    make_source_page,
)


def _authorization(**overrides: Any) -> ExplicitProductionAuthorization:
    payload: dict[str, Any] = {
        "reviewer": "human-operator",
        "reason": "explicit production authorization at checkpoint one",
        "recorded_at": "2026-08-13T00:00:00Z",
    }
    payload.update(overrides)
    return ExplicitProductionAuthorization.model_validate(payload)


def _released_setup(presentations: tuple[tuple[str, str], ...] = (("formula", "E=mc^2"),)):
    """经提取管线制备 released 决策 + 冻结队列映射（fact_id 区分多样本）。"""
    sources = []
    specs = []
    for index, (presentation, text) in enumerate(presentations, start=1):
        fact_id = f"fact:test:{index:04d}"
        sources.append(
            make_extraction_source(
                subject_id=SUBJECT_ID,
                record_id=RECORD_ID,
                fact_id=fact_id,
                evidence_id=EVIDENCE_ID,
                presentation=presentation,
                source_manifest_sha256=SOURCE_MANIFEST_SHA256,
                anchor_ids=ANCHOR_IDS,
                pages=(make_source_page(),),
            )
        )
        binding: dict[str, Any] = {"source_role": ROLE_BY_PRESENTATION[presentation], "text": text}
        if presentation == "ordered_list":
            binding["marker"] = "（一）"  # step3 编译道 ListItemRef 要求 marker 非空（生产语料一贯有标号）
        spec_payload: dict[str, Any] = {
            "selection_id": f"selection:test:export-{index:04d}",
            "subject_id": SUBJECT_ID,
            "record_id": RECORD_ID,
            "fact_id": fact_id,
            "evidence_id": EVIDENCE_ID,
            "presentation": presentation,
            "page_ref_id": "page:test:0001",
            "bindings": (binding,),
            "excluded_noise_kinds": ("watermark",),
            "reviewer": "tester",
            "notes": "explicit human source-page selection",
            "recorded_at": "2026-08-13T00:00:00Z",
        }
        if presentation == "table":
            spec_payload.update(table_rows=1, table_columns=1)
        specs.append(make_manual_selection_spec(spec_payload))
    batch = extract_reviewed_fixture(tuple(sources), tuple(specs), identity_period="x1")
    queue_by_id = {item.queue_item_id: item for item in batch.queue_items}
    queue_by_candidate = {
        candidate.candidate_id: queue_by_id[candidate.queue_item_id] for candidate in batch.fixture.candidates
    }
    return batch, tuple(batch.manifest.decisions), queue_by_candidate


class TestExportEntry:
    def test_export_builds_fixture_that_compiles_as_is(self) -> None:
        # compile 的回归矩阵按 r810 语料形状预期 table/ordered_list/formula 三族在场，
        # 故直通验证用三族放行集（与生产语料同形）。
        _batch, released, queue_map = _released_setup(
            (("formula", "E=mc^2"), ("table", "限值 3.5m"), ("ordered_list", "先勘察后设计"))
        )
        fixture = export_reviewed_typed_evidence_fixture(
            released=released,
            queue_items_by_candidate=queue_map,
            queue_sha256=released[0].queue_sha256,
            authorization=_authorization(),
        )
        assert isinstance(fixture, ReviewedTypedEvidenceFixture)
        # 驱动落盘口径：canonical json 字节的 sha256 作 source_fixture_sha256。
        payload = json.dumps(fixture.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode("utf-8")
        compilation = compile_reviewed_fixture(fixture, source_fixture_sha256=hashlib.sha256(payload).hexdigest())
        assert compilation.gate.valid
        assert len(compilation.contracts) == 3
        assert {contract.presentation for contract in compilation.contracts} == {"formula", "table", "ordered_list"}

    def test_candidate_order_follows_released_order(self) -> None:
        _batch, released, queue_map = _released_setup((("formula", "E=mc^2"), ("constraint", "净高不小于 5m")))
        reversed_released = tuple(reversed(released))
        fixture = export_reviewed_typed_evidence_fixture(
            released=reversed_released,
            queue_items_by_candidate=queue_map,
            queue_sha256=released[0].queue_sha256,
            authorization=_authorization(),
        )
        assert [item.candidate_id for item in fixture.candidates] == [
            decision.candidate_id for decision in reversed_released
        ]

    def test_chain_hashes_are_internally_derived_and_fresh(self) -> None:
        _batch, released, queue_map = _released_setup()
        fixture = export_reviewed_typed_evidence_fixture(
            released=released,
            queue_items_by_candidate=queue_map,
            queue_sha256=released[0].queue_sha256,
            authorization=_authorization(identity_period="t4b"),
        )
        chain = {
            fixture.r89_admission_bundle_sha256,
            fixture.authorization_manifest_sha256,
            fixture.exported_r84_manifest_sha256,
            fixture.r84_fixture_sha256,
        }
        assert len(chain) == 4  # 四哈希各自独立推导，互不相同
        # 终态候选绑定 r810 导出的新鲜 r84 决策身份（内核冻结的 r810- 派生前缀），
        # 而非 released 原身份；identity_period 作用于 r810/r89 槽位身份。
        assert fixture.candidates[0].source_decision_id.startswith("decision:r84:human:r810-")
        assert fixture.candidates[0].source_decision_id != released[0].decision_id
        assert fixture.candidates[0].typed_contract_sha256 == released[0].typed_contract_sha256

    def test_empty_released_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="released set is empty"):
            export_reviewed_typed_evidence_fixture(
                released=(),
                queue_items_by_candidate={},
                queue_sha256="d" * 64,
                authorization=_authorization(),
            )

    def test_non_reviewed_decision_fails_closed(self) -> None:
        decision = make_human_decision(disposition="manual_review")
        queue_map = {decision.candidate_id: make_queue_item()}
        with pytest.raises(ValueError, match="not a reviewed candidate"):
            export_reviewed_typed_evidence_fixture(
                released=(decision,),
                queue_items_by_candidate=queue_map,
                queue_sha256=decision.queue_sha256,
                authorization=_authorization(),
            )

    def test_queue_hash_crossing_fails_closed(self) -> None:
        _batch, released, queue_map = _released_setup()
        with pytest.raises(ValueError, match="crossed the queue hash chain"):
            export_reviewed_typed_evidence_fixture(
                released=released,
                queue_items_by_candidate=queue_map,
                queue_sha256="e" * 64,
                authorization=_authorization(),
            )

    def test_missing_queue_item_fails_closed(self) -> None:
        _batch, released, _queue_map = _released_setup()
        with pytest.raises(ValueError, match="no frozen queue item"):
            export_reviewed_typed_evidence_fixture(
                released=released,
                queue_items_by_candidate={},
                queue_sha256=released[0].queue_sha256,
                authorization=_authorization(),
            )

    def test_duplicate_candidate_binding_fails_closed(self) -> None:
        _batch, released, queue_map = _released_setup()
        with pytest.raises(ValueError, match="binds one candidate twice"):
            export_reviewed_typed_evidence_fixture(
                released=(released[0], released[0]),
                queue_items_by_candidate=queue_map,
                queue_sha256=released[0].queue_sha256,
                authorization=_authorization(),
            )

    def test_cross_source_manifest_fails_closed(self) -> None:
        _batch, released, queue_map = _released_setup()
        foreign_queue = {
            candidate_id: make_queue_item(
                queue_item_id=item.queue_item_id,
                fact_id=item.fact_id,
                presentation=item.presentation,
                source_manifest_sha256="f" * 64,
                pages=(make_source_page(source_manifest_sha256="f" * 64),),
            )
            for candidate_id, item in queue_map.items()
        }
        with pytest.raises(ValueError, match="crossed source contract"):
            export_reviewed_typed_evidence_fixture(
                released=released,
                queue_items_by_candidate=foreign_queue,
                queue_sha256=released[0].queue_sha256,
                authorization=_authorization(),
            )

    def test_identity_period_grammar_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            _authorization(identity_period="T4B")
