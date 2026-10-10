"""Versioned material and policy contracts shared by the V2 pipeline.

Source fidelity and product inclusion are deliberately separate dimensions.
An exercise can be valid evidence while a ``core_only`` policy excludes it
from learning cards.
"""
from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


V2_SCHEMA_VERSION = "2.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SemanticRole(StrEnum):
    CORE = "core"
    WORKED_EXAMPLE = "worked_example"
    EXERCISE_QUESTION = "exercise_question"
    EXERCISE_OPTION = "exercise_option"
    EXERCISE_ANSWER = "exercise_answer"
    EXERCISE_EXPLANATION = "exercise_explanation"
    EXAM_TAG = "exam_tag"
    HEADING = "heading"
    CAPTION = "caption"
    HEADER_FOOTER = "header_footer"
    WATERMARK = "watermark"
    PAGE_NUMBER = "page_number"
    DECORATIVE = "decorative"
    UNKNOWN = "unknown"


NOISE_ROLES = frozenset(
    {
        SemanticRole.HEADER_FOOTER,
        SemanticRole.WATERMARK,
        SemanticRole.PAGE_NUMBER,
        SemanticRole.DECORATIVE,
    }
)


class PolicyDisposition(StrEnum):
    INCLUDED = "included"
    EXCLUDED_BY_POLICY = "excluded_by_policy"
    ARCHIVED = "archived"
    MANUAL_REVIEW = "manual_review"
    IGNORED_NOISE = "ignored_noise"


class CoverageRequirement(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NOT_REQUIRED = "not_required"


class PolicyDecisionState(StrEnum):
    UNCLASSIFIED = "unclassified"
    AUTO_CLASSIFIED = "auto_classified"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    MANUALLY_RESOLVED = "manually_resolved"


class PresentationTransformAction(StrEnum):
    """A deterministic, non-destructive change applied only to derived text."""

    REMOVE_MATCHES = "remove_matches"
    TRUNCATE_FROM_MATCH = "truncate_from_match"


class PresentationTransformRule(StrictModel):
    """Policy-owned render transform; never changes the source EvidenceUnit."""

    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    pattern: str = Field(min_length=1)
    action: PresentationTransformAction

    @model_validator(mode="after")
    def validate_pattern(self) -> "PresentationTransformRule":
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(f"invalid presentation transform pattern: {self.pattern}") from exc
        return self


class SourceKind(StrEnum):
    TEXTBOOK = "textbook"
    LECTURE_NOTE = "lecture_note"
    REGULATION = "regulation"
    STANDARD = "standard"
    QUESTION_BANK = "question_bank"
    PAST_EXAM = "past_exam"
    SLIDE_DECK = "slide_deck"
    OTHER = "other"


class DedupScope(StrEnum):
    WITHIN_DOCUMENT = "within_document"
    WITHIN_COLLECTION = "within_collection"
    CROSS_COLLECTION_CANDIDATE_ONLY = "cross_collection_candidate_only"


class EffectiveWindow(StrictModel):
    effective_from: str | None = None
    effective_to: str | None = None
    standard_id: str | None = None


class ContentPolicy(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    revision: int = Field(ge=1)
    display_name: str
    included_roles: frozenset[SemanticRole]
    excluded_roles: frozenset[SemanticRole] = frozenset()
    optional_roles: frozenset[SemanticRole] = frozenset()
    manual_review_roles: frozenset[SemanticRole] = frozenset()
    known_noise_patterns: tuple[str, ...] = ()
    presentation_transform_rules: tuple[PresentationTransformRule, ...] = ()
    allow_fact_promotion_from_excluded: bool = False
    description: str = ""

    @field_serializer("included_roles", "excluded_roles", "optional_roles", "manual_review_roles", when_used="json")
    def _role_set_json(self, value: frozenset[SemanticRole]) -> list[str]:
        # JSON order must not follow PYTHONHASHSEED: policy and profile fingerprints hash this JSON.
        return sorted(role.value for role in value)

    @model_validator(mode="after")
    def validate_role_sets(self) -> "ContentPolicy":
        groups = (
            self.included_roles,
            self.excluded_roles,
            self.optional_roles,
            self.manual_review_roles,
        )
        if not self.included_roles:
            raise ValueError("included_roles must not be empty")
        for left_index, left in enumerate(groups):
            for right in groups[left_index + 1 :]:
                overlap = left & right
                if overlap:
                    raise ValueError(f"semantic roles may not be in multiple policy sets: {sorted(overlap)}")
        if any(role in NOISE_ROLES for role in self.included_roles):
            raise ValueError("noise roles cannot be included in a content policy")
        for pattern in self.known_noise_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid known noise pattern: {pattern}") from exc
        rule_ids = [rule.rule_id for rule in self.presentation_transform_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("presentation transform rule IDs must be unique")
        return self


class MaterialProfile(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    revision: int = Field(ge=1)
    display_name: str
    collection_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._/-]*$")
    exam_family: str
    exam_level: str | None = None
    subject_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    subject_name: str
    source_kind: SourceKind
    source_language: str = "zh-CN"
    edition: str | None = None
    jurisdiction: str | None = None
    effective_window: EffectiveWindow = EffectiveWindow()
    content_policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    content_policy_revision: int = Field(ge=1)
    layout_profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    taxonomy_profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    validation_profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    card_profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    dedup_scope: DedupScope = DedupScope.WITHIN_COLLECTION


class PolicyDecision(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    evidence_id: str = Field(min_length=8)
    semantic_role: SemanticRole
    disposition: PolicyDisposition
    coverage_requirement: CoverageRequirement
    decision_state: PolicyDecisionState = PolicyDecisionState.AUTO_CLASSIFIED
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    policy_revision: int = Field(ge=1)
    reason: str = Field(min_length=3)
    reviewer: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "PolicyDecision":
        if self.semantic_role in NOISE_ROLES:
            if self.disposition != PolicyDisposition.IGNORED_NOISE:
                raise ValueError("noise evidence must use ignored_noise")
            if self.coverage_requirement != CoverageRequirement.NOT_REQUIRED:
                raise ValueError("noise evidence cannot require coverage")
        elif self.disposition == PolicyDisposition.IGNORED_NOISE:
            raise ValueError("only noise evidence may use ignored_noise")
        elif self.disposition == PolicyDisposition.INCLUDED:
            if self.coverage_requirement == CoverageRequirement.NOT_REQUIRED:
                raise ValueError("included evidence must be required or optional")
        elif self.coverage_requirement != CoverageRequirement.NOT_REQUIRED:
            raise ValueError("excluded, archived, and review evidence is not required by policy")
        if self.decision_state == PolicyDecisionState.MANUALLY_RESOLVED and not self.reviewer:
            raise ValueError("manual policy decisions require reviewer")
        return self
