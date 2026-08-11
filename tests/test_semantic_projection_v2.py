"""Regression coverage for deterministic, non-destructive Evidence anchor spans."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from examcard.evidence.schema import EvidenceKind, PageEvidence
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    Step2ASemanticProjection,
    Step2DecoderConfig,
    Step2ResponseFormat,
    build_step2a_request,
    condition_operators_in_text,
)
from examcard.knowledge.projection import build_step2a_semantic_projection
from examcard.materials.catalog import load_content_policy


PAGE_PATH = ROOT / "data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json"
POLICY_PATH = ROOT / "configs/v2/content_policies/core_only_v1.yaml"


def _page() -> PageEvidence:
    return PageEvidence.model_validate_json(PAGE_PATH.read_text(encoding="utf-8"))


def _build(*, manifest_path: Path = PAGE_PATH):
    return build_step2a_semantic_projection(
        sample_id="gonglu_24_table_formula",
        page=_page() if manifest_path == PAGE_PATH else PageEvidence.model_validate_json(manifest_path.read_text(encoding="utf-8")),
        source_manifest_path=manifest_path,
        source_manifest_reference="data/evidence_v2/golden/gonglu_phase0_v1/gonglu_24_table_formula/pages/page_0001.json",
        content_policy=load_content_policy(POLICY_PATH),
    )


def test_anchor_spans_are_deterministic_parent_bound_and_non_destructive():
    before = PAGE_PATH.read_bytes()
    first = _build()
    second = _build()
    units = {unit.evidence_id: unit for unit in _page().evidence_units}

    assert PAGE_PATH.read_bytes() == before
    assert first.projection == second.projection
    assert set(first.section.anchor_span_ids) == {span.anchor_span_id for span in first.projection.anchor_spans}
    assert len(first.projection.anchor_spans) == len(set(first.section.anchor_span_ids))

    for span in first.projection.anchor_spans:
        parent = units[span.parent_evidence_id]
        assert parent.kind == span.parent_evidence_kind
        assert parent.canonical_text[span.start : span.end] == span.canonical_text
        assert span.exact_text_sha256 == hashlib.sha256(span.canonical_text.encode("utf-8")).hexdigest()
        assert span.source_manifest_sha256 == first.projection.source_manifest_sha256
        assert span.content_policy_fingerprint_sha256 == first.projection.content_policy_fingerprint_sha256
        assert span.policy_decision.evidence_id == span.parent_evidence_id


def test_projection_keeps_complete_table_grid_and_splits_only_text_and_cells():
    built = _build()

    assert len(built.section.tables) == 1
    table = built.section.tables[0]
    assert table.row_count == 2
    assert table.column_count == 2
    assert {cell.evidence_id for row in table.rows for cell in row.cells}
    assert all(span.parent_evidence_kind not in {EvidenceKind.TABLE, EvidenceKind.TABLE_ROW} for span in built.projection.anchor_spans)
    assert all(record.status.value == "passed" for record in built.projection.table_grid_integrity if record.table_evidence_id == table.table_evidence_id)

    condition_spans = [
        span
        for span in built.projection.anchor_spans
        if span.parent_evidence_kind == EvidenceKind.TABLE_CELL and condition_operators_in_text(span.canonical_text)
    ]
    assert len(condition_spans) >= 2
    assert all("\n" not in span.canonical_text for span in condition_spans)


def test_projection_fails_closed_when_manifest_page_is_not_exact():
    with pytest.raises(ValueError, match="exactly match"):
        build_step2a_semantic_projection(
            sample_id="gonglu_24_table_formula",
            page=_page().model_copy(update={"page_num": 2}),
            source_manifest_path=PAGE_PATH,
            content_policy=load_content_policy(POLICY_PATH),
        )


def test_anchor_span_projection_changes_request_identity_and_rejects_tampering():
    built = _build()
    request = build_step2a_request(
        sample_id="gonglu_24_table_formula",
        material_profile_id="yijian_gonglu_2026",
        projection=built.projection,
        sections=(built.section,),
        model="deterministic-fake-v1",
        decoder=Step2DecoderConfig(max_tokens=1000, response_format=Step2ResponseFormat.JSON_SCHEMA_STRICT),
    )
    assert request.identity.request_version == "step2a_contract_v10_table_atomic_continuation"
    assert request.identity.projection_sha256 == built.projection.fingerprint_sha256

    first = built.projection.anchor_spans[0]
    tampered_span = first.model_copy(update={"canonical_text": first.canonical_text + "x"})
    with pytest.raises(ValueError, match="exact-text hash"):
        Step2ASemanticProjection.model_validate(
            built.projection.model_dump(mode="json")
            | {"anchor_spans": [tampered_span.model_dump(mode="json"), *[span.model_dump(mode="json") for span in built.projection.anchor_spans[1:]]]}
        )


def test_anchor_span_model_rejects_non_deterministic_id():
    built = _build()
    span = built.projection.anchor_spans[0]
    with pytest.raises(ValueError, match="ID does not match"):
        EvidenceAnchorSpan.model_validate(span.model_dump(mode="json") | {"anchor_span_id": "span:" + "0" * 24})


@pytest.mark.parametrize(
    "sample_id,page_num,empty_cell_ids",
    [
        ("gonglu_24_table_formula", 3, {"ev:dccbb7d93aae75475c0f874e"}),
        (
            "gonglu_62_cost_flow_incident",
            5,
            {
                "ev:29b0b28fe1bcb90d35ee2841",
                "ev:dc326cab052caa10c16a4b47",
                "ev:629d37314c840099c17f3a7d",
            },
        ),
    ],
)
def test_projection_preserves_empty_table_cells_without_inventing_anchor_spans(
    sample_id: str,
    page_num: int,
    empty_cell_ids: set[str],
):
    manifest_path = (
        ROOT
        / "data/evidence_v2/golden/gonglu_phase0_v1"
        / sample_id
        / "pages"
        / f"page_{page_num:04d}.json"
    )
    page = PageEvidence.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    built = build_step2a_semantic_projection(
        sample_id=sample_id,
        page=page,
        source_manifest_path=manifest_path,
        content_policy=load_content_policy(POLICY_PATH),
    )

    retained_cells = {
        cell.evidence_id: cell
        for table in built.section.tables
        for row in table.rows
        for cell in row.cells
    }
    assert empty_cell_ids <= set(retained_cells)
    assert all(retained_cells[evidence_id].canonical_text == "" for evidence_id in empty_cell_ids)
    assert all(not retained_cells[evidence_id].anchor_spans for evidence_id in empty_cell_ids)
    assert not empty_cell_ids & {span.parent_evidence_id for span in built.projection.anchor_spans}
