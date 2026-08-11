"""Explicit state transitions for V2 decisions and chapter publication."""
from __future__ import annotations

from enum import StrEnum

from ..materials.schema import PolicyDecisionState

from .schema import ChapterQualityGate


class ChapterState(StrEnum):
    PROCESSING = "processing"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    PUBLISHABLE = "publishable"
    PUBLISHED = "published"


_CHAPTER_TRANSITIONS = {
    ChapterState.PROCESSING: {
        ChapterState.DEGRADED,
        ChapterState.BLOCKED,
        ChapterState.PUBLISHABLE,
    },
    ChapterState.DEGRADED: {
        ChapterState.PROCESSING,
        ChapterState.BLOCKED,
        ChapterState.PUBLISHABLE,
    },
    ChapterState.BLOCKED: {ChapterState.PROCESSING, ChapterState.DEGRADED},
    ChapterState.PUBLISHABLE: {
        ChapterState.PROCESSING,
        ChapterState.BLOCKED,
        ChapterState.PUBLISHED,
    },
    ChapterState.PUBLISHED: {ChapterState.PROCESSING},
}

_POLICY_TRANSITIONS = {
    PolicyDecisionState.UNCLASSIFIED: {
        PolicyDecisionState.AUTO_CLASSIFIED,
        PolicyDecisionState.NEEDS_MANUAL_REVIEW,
    },
    PolicyDecisionState.AUTO_CLASSIFIED: {PolicyDecisionState.NEEDS_MANUAL_REVIEW},
    PolicyDecisionState.NEEDS_MANUAL_REVIEW: {PolicyDecisionState.MANUALLY_RESOLVED},
    PolicyDecisionState.MANUALLY_RESOLVED: {PolicyDecisionState.NEEDS_MANUAL_REVIEW},
}


def assert_chapter_transition(
    previous: ChapterState,
    target: ChapterState,
    *,
    quality_gate: ChapterQualityGate | None = None,
) -> None:
    if target not in _CHAPTER_TRANSITIONS[previous]:
        raise ValueError(f"invalid chapter transition: {previous} -> {target}")
    if target in {ChapterState.PUBLISHABLE, ChapterState.PUBLISHED}:
        if quality_gate is None or not quality_gate.publishable:
            raise ValueError("publishable/published transitions require a passing chapter quality gate")


def assert_policy_transition(previous: PolicyDecisionState, target: PolicyDecisionState) -> None:
    if target not in _POLICY_TRANSITIONS[previous]:
        raise ValueError(f"invalid policy transition: {previous} -> {target}")
