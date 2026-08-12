"""Offline census.json derivation for already-ingested chapters (w4 segment 50-65).

The official first-pass runner (shadow_wiring.make_census_runner, bc-1fa74e06)
skips only on census.json presence and otherwise RE-INGESTS the whole chapter.
Chapters 50-65 already carry complete zero-API evidence (chapter-report.json +
evidence/manifest.json), so census.json can be derived read-only from the
persisted manifest using the SAME production functions (_conflict_records +
high_risk_review_hints); the engine is deterministic, so the payload equals
what a re-ingest would emit -- minus ~45 minutes of recomputation.

Default is a dry run (prints per-chapter summaries, writes nothing). Pass
--write to materialize census.json for chapters that lack it; existing
census.json files are never touched.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from examcard.common.io import atomic_write_json, load_model, read_json
from examcard.evidence.schema import DocumentEvidenceManifest
from examcard.pipeline.shadow_wiring import CENSUS_REPORT_NAME, _conflict_records, high_risk_review_hints

ROOT = Path(__file__).resolve().parents[1] / "data" / "shadow_gonglu_65"
CHAPTERS = [f"{number:02d}" for number in range(65, 49, -1)]


def derive_payload(chapter: str) -> dict:
    chapter_dir = ROOT / chapter
    report = read_json(chapter_dir / "chapter-report.json")
    manifest = load_model(chapter_dir / "evidence" / "manifest.json", DocumentEvidenceManifest)
    records = _conflict_records(manifest)
    high_risk = sum(1 for record in records if record["severity"] == "high_risk")
    if len(records) != report["metrics"]["source_conflict_count"]:
        raise SystemExit(f"chapter {chapter}: conflict count drift vs chapter-report")
    return {
        "chapter_prefix": chapter,
        "pdf": report["pdf"],
        "document_sha256": manifest.source.sha256,
        "manifest_id": manifest.manifest_id,
        "pass": "census",
        "page_count": len(manifest.pages),
        "evidence_unit_count": sum(len(page.evidence_units) for page in manifest.pages),
        "source_conflict_count": len(records),
        "high_risk_conflict_count": high_risk,
        "high_risk_hint_count": len(high_risk_review_hints(records)),
        "conflicts": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="materialize census.json (default: dry run)")
    args = parser.parse_args()

    derived = skipped = 0
    for chapter in CHAPTERS:
        census_path = ROOT / chapter / CENSUS_REPORT_NAME
        if census_path.is_file():
            print(f"{chapter}: census.json already present, skipped")
            skipped += 1
            continue
        payload = derive_payload(chapter)
        summary = (
            f"conflicts={payload['source_conflict_count']} high_risk={payload['high_risk_conflict_count']} "
            f"hints={payload['high_risk_hint_count']} pages={payload['page_count']}"
        )
        if args.write:
            atomic_write_json(census_path, payload)
            print(f"{chapter}: census.json WRITTEN  {summary}")
        else:
            print(f"{chapter}: DRY RUN (would write)  {summary}")
        derived += 1
    mode = "written" if args.write else "derivable"
    print(f"done: {derived} {mode}, {skipped} already present")


if __name__ == "__main__":
    main()
