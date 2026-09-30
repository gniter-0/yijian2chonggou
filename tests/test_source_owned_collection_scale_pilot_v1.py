"""Offline tests for source-owned PDF collection orchestration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import examcard.knowledge.extraction_v1 as extraction_module
import migration.run_source_owned_document_collection_scale_pilot_v1 as collection_module
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from migration.run_source_owned_real_pilot_v1 import SourceOwnedRealPilotSummary
from migration.run_source_owned_document_collection_scale_pilot_v1 import (
    SourceOwnedCollectionScaleSummary,
    run_source_owned_document_collection_scale_pilot,
)


def _make_pdf(path: Path, *, text: str = "collection evidence") -> Path:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 24 48 Td ({escaped}) Tj ET".encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    document = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_offset = len(document)
    document += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    document += b"0000000000 65535 f \n"
    document += b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    document += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(document)
    return path


def _make_collection(directory: Path, count: int) -> tuple[Path, ...]:
    directory.mkdir()
    return tuple(_make_pdf(directory / f"document-{index:03d}.pdf", text=f"evidence {index}") for index in range(count))


def _summary_for(pdf_path: Path, *, materialized: bool = True) -> SourceOwnedRealPilotSummary:
    return SourceOwnedRealPilotSummary(
        document_sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        pages=1,
        evidence_units=1 if materialized else 0,
        extraction_status="extracted" if materialized else "failed_closed",
        anchor_selection_status="selected" if materialized else "failed_closed",
        derivation_status="no_quantitative_tokens" if materialized else "failed_closed",
        canonical_fact_status="assembled" if materialized else "failed_closed",
        admission_status="admitted" if materialized else "rejected",
        bridge_status="bridged" if materialized else "failed_closed",
        materialization_status="materialized" if materialized else "failed_closed",
        knowledge_card_count=1 if materialized else 0,
    )


def test_ten_pdf_collection_runs_each_pdf_through_the_offline_pipeline(tmp_path: Path) -> None:
    collection = tmp_path / "ten-pdfs"
    pdfs = _make_collection(collection, 10)

    summary = run_source_owned_document_collection_scale_pilot(
        collection,
        output_dir=tmp_path / "ten-output",
    )

    assert isinstance(summary, SourceOwnedCollectionScaleSummary)
    assert summary.document_count == 10
    assert summary.total_pages == 10
    assert summary.total_evidence_units >= 10
    assert summary.success_count == 10
    assert summary.failed_count == 0
    assert summary.knowledge_card_count == 10
    assert [document.status for document in summary.documents] == ["success"] * 10
    assert [document.document_sha256 for document in summary.documents] == [
        hashlib.sha256(pdf.read_bytes()).hexdigest() for pdf in pdfs
    ]


def test_fifty_pdf_simulated_collection_is_deterministically_delegated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = tmp_path / "fifty-pdfs"
    pdfs = _make_collection(collection, 50)
    calls: list[Path] = []

    def fake_real_pilot(pdf_path: Path, *, output_dir: Path | None = None) -> SourceOwnedRealPilotSummary:
        assert output_dir is not None
        calls.append(pdf_path)
        return _summary_for(pdf_path)

    monkeypatch.setattr(collection_module, "run_source_owned_real_pilot", fake_real_pilot)
    summary = run_source_owned_document_collection_scale_pilot(
        collection,
        output_dir=tmp_path / "fifty-output",
    )

    assert calls == list(pdfs)
    assert summary.document_count == 50
    assert summary.success_count == 50
    assert summary.failed_count == 0
    assert summary.total_pages == 50
    assert summary.total_evidence_units == 50
    assert summary.knowledge_card_count == 50


def test_one_pdf_exception_does_not_stop_remaining_documents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = tmp_path / "exception-isolation"
    pdfs = _make_collection(collection, 3)
    calls: list[Path] = []

    def fake_real_pilot(pdf_path: Path, *, output_dir: Path | None = None) -> SourceOwnedRealPilotSummary:
        calls.append(pdf_path)
        if pdf_path.name == "document-001.pdf":
            raise RuntimeError("simulated document failure")
        return _summary_for(pdf_path)

    monkeypatch.setattr(collection_module, "run_source_owned_real_pilot", fake_real_pilot)
    summary = run_source_owned_document_collection_scale_pilot(
        collection,
        output_dir=tmp_path / "exception-output",
    )

    assert calls == list(pdfs)
    assert summary.document_count == 3
    assert summary.success_count == 2
    assert summary.failed_count == 1
    assert summary.knowledge_card_count == 2
    assert [document.status for document in summary.documents] == ["success", "failed", "success"]
    assert summary.documents[1].pages == 0


def test_partial_success_result_is_counted_without_cross_document_merging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = tmp_path / "partial-success"
    _make_collection(collection, 3)

    def fake_real_pilot(pdf_path: Path, *, output_dir: Path | None = None) -> SourceOwnedRealPilotSummary:
        return _summary_for(pdf_path, materialized=pdf_path.name != "document-001.pdf")

    monkeypatch.setattr(collection_module, "run_source_owned_real_pilot", fake_real_pilot)
    summary = run_source_owned_document_collection_scale_pilot(
        collection,
        output_dir=tmp_path / "partial-output",
    )

    assert summary.success_count == 2
    assert summary.failed_count == 1
    assert summary.total_pages == 3
    assert summary.total_evidence_units == 2
    assert summary.knowledge_card_count == 2
    assert [document.status for document in summary.documents] == ["success", "failed", "success"]


def test_duplicate_hash_documents_remain_independent_members(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = tmp_path / "duplicates"
    collection.mkdir()
    first = _make_pdf(collection / "a.pdf", text="same bytes")
    second = collection / "b.pdf"
    second.write_bytes(first.read_bytes())
    calls: list[Path] = []

    def fake_real_pilot(pdf_path: Path, *, output_dir: Path | None = None) -> SourceOwnedRealPilotSummary:
        calls.append(pdf_path)
        return _summary_for(pdf_path)

    monkeypatch.setattr(collection_module, "run_source_owned_real_pilot", fake_real_pilot)
    summary = run_source_owned_document_collection_scale_pilot(
        collection,
        output_dir=tmp_path / "duplicate-output",
    )

    expected_hash = hashlib.sha256(first.read_bytes()).hexdigest()
    assert calls == [first.resolve(), second.resolve()]
    assert summary.document_count == 2
    assert summary.success_count == 2
    assert summary.knowledge_card_count == 2
    assert [document.document_sha256 for document in summary.documents] == [expected_hash, expected_hash]


def test_collection_id_and_summary_are_stable_and_allow_listed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = tmp_path / "secure-collection"
    _make_collection(collection, 2)

    def fake_real_pilot(pdf_path: Path, *, output_dir: Path | None = None) -> SourceOwnedRealPilotSummary:
        return _summary_for(pdf_path)

    monkeypatch.setattr(collection_module, "run_source_owned_real_pilot", fake_real_pilot)
    first_output = tmp_path / "secure-output-1"
    second_output = tmp_path / "secure-output-2"
    first = run_source_owned_document_collection_scale_pilot(collection, output_dir=first_output)
    second = run_source_owned_document_collection_scale_pilot(collection, output_dir=second_output)

    assert first.collection_id == second.collection_id
    payload = json.loads((first_output / "summary.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "collection_id",
        "document_count",
        "total_pages",
        "total_evidence_units",
        "elapsed_seconds",
        "success_count",
        "failed_count",
        "knowledge_card_count",
        "documents",
    }
    assert set(payload["documents"][0]) == {"document_sha256", "pages", "status"}
    serialized = json.dumps(payload, sort_keys=True).lower()
    for forbidden in (
        "raw_text",
        "canonical_text",
        "prompt",
        "response",
        "provider",
        "secret",
        "credential",
        "token_value",
        "evidence_content",
    ):
        assert forbidden not in serialized
    assert sorted(path.name for path in first_output.iterdir()) == ["summary.json"]


def test_collection_path_isolated_from_legacy_extraction_and_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = tmp_path / "isolation"
    _make_collection(collection, 1)

    def forbidden_legacy(**_: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    def forbidden_provider(*_: object, **__: object) -> None:
        raise AssertionError("provider path was entered")

    monkeypatch.setattr(extraction_module, "extract_document_proposals", forbidden_legacy)
    monkeypatch.setattr(Step2OpenAIAdapter, "complete_step2a", forbidden_provider)
    summary = run_source_owned_document_collection_scale_pilot(
        collection,
        output_dir=tmp_path / "isolation-output",
    )

    assert summary.success_count == 1
    assert summary.failed_count == 0
    assert summary.knowledge_card_count == 1
