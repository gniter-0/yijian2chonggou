"""教学质量评审在 Step 2 记录里的落纸载荷（拆分层 1）。"""
from __future__ import annotations

from enum import StrEnum
from pydantic import Field

from ..materials.schema import StrictModel

from .quality import CardGranularityResult, HeadingOnlyFactResult, TeachingQualityGate, TitleQualityResult


class SemanticReviewState(StrEnum):
    DRAFT = "draft"


class Step2TeachingReview(StrictModel):
    state: SemanticReviewState = SemanticReviewState.DRAFT
    gate: TeachingQualityGate
    title_results: tuple[TitleQualityResult, ...] = ()
    granularity_results: tuple[CardGranularityResult, ...] = ()
    heading_only_fact_results: tuple[HeadingOnlyFactResult, ...] = ()
    basis: str = Field(min_length=1)
