"""compilers.step4_contract 单测（蓝本：旧仓 test_phase2r4_...py）。

可移植片段：token 抽取、有序列表/表格派生、可见文本哈希保真、禁键/禁文扫描与
fail-closed 模型校验。16 卡全束派生/门禁/回归依赖 r2242 真实统一 fixture，
归 migration/verify 双跑对照（设计 §6.2/§9.1），不进常驻 CI。
"""
from __future__ import annotations

import hashlib

import pytest

from examcard.compilers.step4_contract import (
    DEFAULT_FAKE_FILENAME_PREFIX,
    ExpectedCounts,
    GenericOrderedListContract,
    GenericOrderedListItem,
    GenericTableContract,
    GenericTokenBinding,
    GenericVisibleText,
    MARKERS,
    _canonical_hash,
    _extract_tokens,
    _no_forbidden_text,
    _walk_mappings,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_extract_tokens_is_deterministic_and_deduplicated() -> None:
    tokens = _extract_tokens("Q=V×A 且 Q=V×A 不小于 300mm ≥ 0.5")
    assert tokens == ("Q", "=", "V", "×", "A", "300mm", "≥", "0.5")


def test_token_binding_hash_is_fail_closed() -> None:
    tokens = _extract_tokens("300mm ≥ 0.5")
    binding = GenericTokenBinding(
        fact_id="fact:" + "a" * 24,
        text_index=0,
        source_text_sha256=_sha("300mm ≥ 0.5"),
        tokens=tokens,
        tokens_sha256=_canonical_hash(tokens),
    )
    assert binding.tokens == tokens
    with pytest.raises(Exception):
        GenericTokenBinding(
            fact_id="fact:" + "a" * 24,
            text_index=0,
            source_text_sha256=_sha("300mm ≥ 0.5"),
            tokens=tokens,
            tokens_sha256="0" * 64,
        )


def test_visible_text_hash_must_match_source() -> None:
    with pytest.raises(Exception, match="visible text hash"):
        GenericVisibleText(
            fact_id="fact:" + "b" * 24,
            visible_text="正确文本",
            source_kind="reviewed_fact_assertion",
            evidence_ids=("evidence:1",),
            anchor_span_ids=("span:1",),
            complete_table_grid_verified=False,
            source_text_sha256="0" * 64,
            source_display_contract_sha256="1" * 64,
        )


def test_ordered_list_markers_must_stay_complete() -> None:
    items = tuple(
        GenericOrderedListItem(ordinal=index, marker=MARKERS[index - 1], text=f"条目{index}", source_item_sha256=_sha(f"item{index}"))
        for index in (1, 2)
    )
    contract = GenericOrderedListContract(
        fact_id="fact:" + "c" * 24,
        source_text_sha256=_sha("全文"),
        items=items,
        source_list_contract_sha256=_sha("contract"),
    )
    assert len(contract.items) == 2
    swapped = tuple(
        GenericOrderedListItem(ordinal=index, marker=MARKERS[2 - index], text=f"条目{index}", source_item_sha256=_sha(f"item{index}"))
        for index in (1, 2)
    )
    with pytest.raises(Exception, match="markers"):
        GenericOrderedListContract(
            fact_id="fact:" + "c" * 24,
            source_text_sha256=_sha("全文"),
            items=swapped,
            source_list_contract_sha256=_sha("contract"),
        )


def test_source_text_only_table_cannot_invent_grid() -> None:
    with pytest.raises(Exception, match="invent grid"):
        GenericTableContract(
            fact_id="fact:" + "d" * 24,
            source_table_ids=("table:1",),
            source_text_sha256=_sha("表格全文"),
            table_state="source_text_only",
            row_count=2,
        )


def test_forbidden_text_scan_catches_watermark() -> None:
    class _Stub:
        def model_dump(self, mode: str) -> dict:
            return {"visible": "包含 水印 的文本"}

    assert not _no_forbidden_text(_Stub(), _Stub())  # type: ignore[arg-type]


def test_walk_mappings_visits_nested_structures() -> None:
    payload = {"a": [{"b": {"c": 1}}, {"d": 2}]}
    keys = {key for mapping in _walk_mappings(payload) for key in mapping}
    assert keys == {"a", "b", "c", "d"}


def test_expected_counts_defaults_pin_gonglu_v1() -> None:
    expected = ExpectedCounts()
    assert (expected.card_count, expected.fact_count, expected.display_text_count, expected.semantic_edge_count) == (16, 28, 52, 24)
    assert expected.prior_reviewed_indexes == (2, 3, 4, 6)
    assert DEFAULT_FAKE_FILENAME_PREFIX == "gonglu-step4-card-"
