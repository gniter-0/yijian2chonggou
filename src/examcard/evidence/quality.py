"""Structured quality summaries for one Evidence Engine run."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .schema import PageEvidence


def summarize_pages(pages: tuple[PageEvidence, ...], *, cache_hits: int, cache_misses: int, elapsed_seconds: float) -> dict[str, Any]:
    units = [unit for page in pages for unit in page.evidence_units]
    return {
        "page_count": len(pages),
        "evidence_count": len(units),
        "source_quality": dict(sorted(Counter(unit.source_quality.value for unit in units).items())),
        "evidence_kind": dict(sorted(Counter(unit.kind.value for unit in units).items())),
        "semantic_role": dict(sorted(Counter(unit.semantic_role.value for unit in units).items())),
        "must_cover_count": sum(1 for unit in units if unit.must_cover_by_source),
        "conflict_count": sum(len(page.source_conflicts) for page in pages),
        "visual_only_count": sum(1 for unit in units if unit.source_quality.value == "visual_only"),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
