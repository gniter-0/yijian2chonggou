"""去重审计、机器断言与章节质量门禁（拆分层 4）。"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..evidence.golden import GoldenSample
from ..evidence.schema import EvidenceKind, EvidenceUnit, SourceQuality
from ..materials.schema import ContentPolicy, CoverageRequirement, PolicyDisposition

from .schema import (
    AtomicFact,
    ChapterQualityGate,
    CompletenessMode,
    CoverageDisposition,
    CoverageMap,
    FactType,
    KnowledgeCard,
    Renderability,
)

from .fact_lexicon import _EMBEDDED_EXAMPLE_RE
from .fact_types import EvidenceContext, FactBundle, ReviewedConflict
from .fact_review import _conflict_is_resolved


def _dedup_audit(cards: tuple[KnowledgeCard, ...], facts: tuple[AtomicFact, ...]) -> dict[str, Any]:
    fact_by_id = {fact.fact_id: fact for fact in facts}
    spans: dict[tuple[str, ...], list[KnowledgeCard]] = defaultdict(list)
    titles: dict[str, list[KnowledgeCard]] = defaultdict(list)
    for card in cards:
        fact = fact_by_id[card.content_segments[0].fact_ids[0]]
        spans[tuple(sorted(fact.primary_evidence_ids))].append(card)
        titles["".join(card.title.split())].append(card)
    exact_duplicates = [
        {"primary_evidence_ids": list(span), "card_raw_ids": [card.raw_id for card in group]}
        for span, group in spans.items()
        if len(group) > 1
    ]
    title_candidates = [
        {"title": title, "card_raw_ids": [card.raw_id for card in group], "decision": "candidate_only_distinct_anchor_span"}
        for title, group in titles.items()
        if len(group) > 1 and len({card.raw_id for card in group}) == len(group)
    ]
    return {
        "engine": "anchor-span-dedup-v1",
        "auto_merged": [],
        "exact_primary_span_duplicates": exact_duplicates,
        "title_similarity_candidates": title_candidates,
        "unresolved_candidates": 0,
    }


def _machine_assertions(
    *,
    sample: GoldenSample,
    units: tuple[EvidenceUnit, ...],
    coverage: CoverageMap,
    conflicts: tuple[ReviewedConflict, ...],
    policy: ContentPolicy,
) -> tuple[dict[str, Any], ...]:
    coverage_by_id = {record.evidence_id: record for record in coverage.records}
    results: list[dict[str, Any]] = []
    for assertion in sample.assertions:
        page_num = int(assertion.assertion_id.split("_", 1)[0].removeprefix("page"))
        page_units = [unit for unit in units if unit.locator.page_num == page_num]
        missing_kinds = [kind.value for kind in assertion.expected_evidence_kinds if not any(unit.kind == kind for unit in page_units)]
        match_results: list[dict[str, Any]] = []
        for expected in assertion.expected_matches:
            matches = [
                unit
                for unit in page_units
                if expected.text in unit.canonical_text
                and (expected.kind is None or unit.kind == expected.kind)
                and (expected.semantic_role is None or unit.semantic_role == expected.semantic_role)
                and (expected.must_cover is None or unit.must_cover_by_source == expected.must_cover)
            ]
            dispositions = [coverage_by_id[unit.evidence_id].disposition.value for unit in matches]
            expected_excluded = expected.semantic_role in policy.excluded_roles if expected.semantic_role else False
            passed = bool(matches) and (
                all(item == CoverageDisposition.EXCLUDED_BY_POLICY.value for item in dispositions)
                if expected_excluded
                else any(item == CoverageDisposition.PRIMARY_ASSIGNED.value for item in dispositions)
            )
            match_results.append(
                {
                    "text": expected.text,
                    "matched_evidence_ids": [unit.evidence_id for unit in matches],
                    "dispositions": dispositions,
                    "passed": passed,
                }
            )
        conflict_results = [
            {
                "native_text": native,
                "canonical_text": canonical,
                "passed": any(item.native_text == native and item.canonical_text == canonical for item in conflicts),
            }
            for native, canonical in assertion.expected_conflicts
        ]
        passed = not missing_kinds and all(item["passed"] for item in match_results + conflict_results)
        results.append(
            {
                "assertion_id": assertion.assertion_id,
                "passed": passed,
                "missing_evidence_kinds": missing_kinds,
                "expected_matches": match_results,
                "expected_conflicts": conflict_results,
            }
        )
    return tuple(results)


def _quality_gate(
    *,
    contexts: tuple[EvidenceContext, ...],
    bundles: tuple[FactBundle, ...],
    cards: tuple[KnowledgeCard, ...],
    coverage: CoverageMap,
    conflicts: tuple[ReviewedConflict, ...],
    dedup: dict[str, Any],
    policy: ContentPolicy,
    audit_excluded_evidence_ids: frozenset[str] = frozenset(),
) -> ChapterQualityGate:
    primary_ids = {evidence_id for bundle in bundles for evidence_id in bundle.fact.primary_evidence_ids}
    records = {record.evidence_id: record for record in coverage.records}
    required_ids = {
        context.unit.evidence_id
        for context in contexts
        if context.decision.disposition == PolicyDisposition.INCLUDED
        and context.decision.coverage_requirement == CoverageRequirement.REQUIRED
        and context.unit.evidence_id not in audit_excluded_evidence_ids
    }
    formula_ids = {
        context.unit.evidence_id
        for context in contexts
        if context.unit.kind == EvidenceKind.FORMULA
        and context.decision.disposition == PolicyDisposition.INCLUDED
        and context.unit.evidence_id not in audit_excluded_evidence_ids
    }
    list_ids = {
        context.unit.evidence_id
        for context in contexts
        if context.unit.kind == EvidenceKind.LIST_ITEM
        and context.decision.disposition == PolicyDisposition.INCLUDED
        and context.unit.evidence_id not in audit_excluded_evidence_ids
    }
    formula_fact_ids = {
        evidence_id
        for bundle in bundles
        if bundle.fact.completeness_mode == CompletenessMode.COMPLETE_FORMULA
        for evidence_id in bundle.fact.primary_evidence_ids
    }
    unresolved = [
        context.unit
        for context in contexts
        if context.unit.evidence_id not in audit_excluded_evidence_ids
        and (
            context.decision.disposition == PolicyDisposition.MANUAL_REVIEW
        or context.unit.source_quality == SourceQuality.UNRESOLVED
        or (context.unit.source_quality == SourceQuality.CONFLICT and not _conflict_is_resolved(context.unit, conflicts))
        )
    ]
    numeric_failures = sum(
        1
        for bundle in bundles
        for number in bundle.fact.numbers
        if number.raw not in bundle.fact.assertion or number.evidence_id not in bundle.fact.primary_evidence_ids
    )
    rendered_texts = tuple(bundle.fact.assertion for bundle in bundles) + tuple(card.content for card in cards)
    known_noise_patterns = tuple(policy.known_noise_patterns) + tuple(
        rule.pattern for rule in policy.presentation_transform_rules if rule.rule_id.startswith("watermark-")
    )
    known_noise_leaks = sum(
        any(re.search(pattern, text) for pattern in known_noise_patterns)
        for text in rendered_texts
    )
    embedded_example_leaks = sum(bool(_EMBEDDED_EXAMPLE_RE.search(text)) for text in rendered_texts)
    unrenderable_diagrams = sum(
        bundle.fact.fact_type == FactType.DIAGRAM_RELATION
        and bundle.fact.renderability == Renderability.MANUAL_REVIEW
        for bundle in bundles
    )
    return ChapterQualityGate(
        must_cover_total=len(required_ids),
        must_cover_primary_assigned=sum(
            records[evidence_id].disposition == CoverageDisposition.PRIMARY_ASSIGNED for evidence_id in required_ids
        ),
        unresolved_evidence=len(unresolved),
        fallback_content=0,
        numeric_failures=numeric_failures,
        enumeration_failures=len(list_ids - primary_ids),
        formula_failures=len(formula_ids - formula_fact_ids),
        unresolved_visual_only=sum(
            context.unit.source_quality == SourceQuality.VISUAL_ONLY
            and context.unit.must_cover_by_source
            and context.decision.disposition == PolicyDisposition.INCLUDED
            and context.unit.evidence_id not in primary_ids
            and context.unit.evidence_id not in audit_excluded_evidence_ids
            for context in contexts
        ),
        unresolved_dedup_candidates=int(dedup["unresolved_candidates"]),
        known_noise_leaks=known_noise_leaks,
        embedded_example_leaks=embedded_example_leaks,
        unrenderable_diagrams=unrenderable_diagrams,
        schema_valid=True,
        lineage_valid=True,
        adapter_valid=True,
    )
