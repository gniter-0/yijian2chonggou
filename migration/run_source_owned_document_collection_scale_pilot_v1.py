"""Measure source-owned throughput and failure isolation over a PDF collection.

This runner owns collection orchestration only.  Each PDF is delegated to the
existing real-PDF source-owned pilot in an ephemeral per-document directory;
the collection output directory receives only the allow-listed aggregate
summary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

# The migration runner bootstraps the local source tree before importing the
# package, so its imports intentionally follow the guarded sys.path setup.
# ruff: noqa: E402
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from examcard.materials.schema import StrictModel
from migration.run_source_owned_real_pilot_v1 import run_source_owned_real_pilot


type SourceOwnedCollectionDocumentStatus = Literal["success", "failed"]

DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "pilot_runs" / "source_owned" / "collections"


class SourceOwnedCollectionDocumentSummary(StrictModel):
    """Safe result for one collection member."""

    document_sha256: str
    pages: int
    status: SourceOwnedCollectionDocumentStatus


class SourceOwnedCollectionScaleSummary(StrictModel):
    """Allow-listed collection result with no source or provider content."""

    collection_id: str
    document_count: int
    total_pages: int
    total_evidence_units: int
    elapsed_seconds: float
    success_count: int
    failed_count: int
    knowledge_card_count: int
    documents: tuple[SourceOwnedCollectionDocumentSummary, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_members(collection_dir: Path) -> tuple[tuple[Path, str], ...]:
    members = sorted(
        (path for path in collection_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: (path.name.casefold(), path.name),
    )
    return tuple((path, _sha256_file(path)) for path in members)


def _collection_id(members: tuple[tuple[Path, str], ...]) -> str:
    identity = [
        {"name": path.name, "document_sha256": document_sha256}
        for path, document_sha256 in members
    ]
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "collection:" + hashlib.sha256(encoded).hexdigest()[:24]


def _write_summary(output_dir: Path, summary: SourceOwnedCollectionScaleSummary) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / "summary.json.tmp"
    temporary.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_dir / "summary.json")


def _failed_document(document_sha256: str) -> SourceOwnedCollectionDocumentSummary:
    return SourceOwnedCollectionDocumentSummary(
        document_sha256=document_sha256,
        pages=0,
        status="failed",
    )


def _summary(
    *,
    collection_id: str,
    documents: tuple[SourceOwnedCollectionDocumentSummary, ...],
    elapsed_seconds: float,
) -> SourceOwnedCollectionScaleSummary:
    return SourceOwnedCollectionScaleSummary(
        collection_id=collection_id,
        document_count=len(documents),
        total_pages=sum(document.pages for document in documents),
        total_evidence_units=0,
        elapsed_seconds=elapsed_seconds,
        success_count=sum(document.status == "success" for document in documents),
        failed_count=sum(document.status == "failed" for document in documents),
        knowledge_card_count=0,
        documents=documents,
    )


def run_source_owned_document_collection_scale_pilot(
    collection_dir: Path,
    *,
    output_dir: Path | None = None,
) -> SourceOwnedCollectionScaleSummary:
    """Run each PDF independently through the existing real-PDF pilot."""

    collection_dir = Path(collection_dir).resolve()
    if not collection_dir.is_dir():
        raise ValueError("source-owned collection pilot requires a PDF directory")
    target = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_ROOT / collection_dir.name
    members = _pdf_members(collection_dir)
    collection_id = _collection_id(members)
    started = time.perf_counter()
    results: list[SourceOwnedCollectionDocumentSummary] = []
    total_evidence_units = 0
    knowledge_card_count = 0

    with TemporaryDirectory(prefix="examcard-source-owned-collection-") as temporary_root:
        ephemeral_root = Path(temporary_root)
        for index, (pdf_path, document_sha256) in enumerate(members):
            try:
                # The real pilot writes its own safe summary.  Keep that
                # intermediate output ephemeral so the collection directory
                # cannot acquire per-document source artifacts.
                result = run_source_owned_real_pilot(
                    pdf_path,
                    output_dir=ephemeral_root / f"document-{index:06d}",
                )
                successful = result.materialization_status == "materialized"
                results.append(
                    SourceOwnedCollectionDocumentSummary(
                        document_sha256=document_sha256,
                        pages=result.pages,
                        status="success" if successful else "failed",
                    )
                )
                total_evidence_units += result.evidence_units
                knowledge_card_count += result.knowledge_card_count
            except Exception:
                results.append(_failed_document(document_sha256))

    elapsed_seconds = time.perf_counter() - started
    summary = _summary(
        collection_id=collection_id,
        documents=tuple(results),
        elapsed_seconds=elapsed_seconds,
    ).model_copy(
        update={
            "total_evidence_units": total_evidence_units,
            "knowledge_card_count": knowledge_card_count,
        }
    )
    _write_summary(target, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    summary = run_source_owned_document_collection_scale_pilot(
        args.collection_dir,
        output_dir=args.output_dir,
    )
    print(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
