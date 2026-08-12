"""w4 lane: post-hoc source-conflicts census for chapters 50-65 (two-pass amendment).

The prior w4 holder completed the zero-API first pass for every chapter in the
segment (chapter-report.json markers present, vision_calls_used = 0 everywhere)
but dropped before the bc-31b3117c amendment's extra census artifact existed.
The conflicts are fully persisted in each chapter's evidence/manifest.json, so
the census (format: shadow_source_conflicts_census_v1, as produced by the w2
lane) is derived offline here -- zero API calls, zero pipeline re-runs.

Per chapter (65 -> 50, reverse order per the segment rules):
  - skip when source-conflicts-census.json already exists (claim discipline);
  - read evidence/manifest.json pages[].source_conflicts + chapter-report.json;
  - severity via examcard.pipeline.shadow.classify_text_conflict;
  - reconcile counts.total against chapter-report metrics.source_conflict_count;
  - atomic_write_json (same byte format as the w2 lane's files).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from examcard.common.io import atomic_write_json, read_json
from examcard.pipeline.shadow import classify_text_conflict

ROOT = Path(__file__).resolve().parents[1] / "data" / "shadow_gonglu_65"
CHAPTERS = [f"{number:02d}" for number in range(65, 49, -1)]
LANE = "w4"


def build_census(chapter: str) -> dict:
    chapter_dir = ROOT / chapter
    report = read_json(chapter_dir / "chapter-report.json")
    manifest = read_json(chapter_dir / "evidence" / "manifest.json")

    conflicts = []
    counts = {"high_risk": 0, "normal": 0, "typography_only": 0, "total": 0}
    for page in manifest["pages"]:
        for record in page.get("source_conflicts", []) or []:
            severity = classify_text_conflict(record["native_text"], record["visual_text"])
            counts[severity] += 1
            counts["total"] += 1
            conflicts.append(
                {
                    "conflict_id": record["conflict_id"],
                    "locator": record["locator"],
                    "native_text": record["native_text"],
                    "page_num": page["page_num"],
                    "severity": severity,
                    "status": record["status"],
                    "visual_text": record["visual_text"],
                }
            )

    reported = report["metrics"]["source_conflict_count"]
    if counts["total"] != reported:
        raise SystemExit(f"chapter {chapter}: census total {counts['total']} != chapter-report {reported}")
    if report["metrics"]["vision_calls_used"] != 0:
        raise SystemExit(f"chapter {chapter}: unexpected vision usage in first pass")

    return {
        "census_version": "shadow_source_conflicts_census_v1",
        "chapter_prefix": chapter,
        "conflicts": conflicts,
        "counts": counts,
        "document_sha256": report["document_sha256"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lane": LANE,
        "manifest_id": report["manifest_id"],
        "page_count": report["metrics"]["page_count"],
        "pdf": report["pdf"],
    }


def main() -> None:
    written = skipped = 0
    totals = {"high_risk": 0, "normal": 0, "typography_only": 0, "total": 0}
    for chapter in CHAPTERS:
        census_path = ROOT / chapter / "source-conflicts-census.json"
        if census_path.is_file():
            print(f"{chapter}: census already present, skipped")
            skipped += 1
            continue
        census = build_census(chapter)
        atomic_write_json(census_path, census)
        written += 1
        for key in totals:
            totals[key] += census["counts"][key]
        print(
            f"{chapter}: census written  total={census['counts']['total']} "
            f"high_risk={census['counts']['high_risk']} typography_only={census['counts']['typography_only']}"
        )
    print(
        f"done: {written} written, {skipped} skipped; segment conflicts total={totals['total']} "
        f"high_risk={totals['high_risk']} normal={totals['normal']} typography_only={totals['typography_only']}"
    )


if __name__ == "__main__":
    main()
