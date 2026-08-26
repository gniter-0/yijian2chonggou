"""cm35 切分产物：合并 / 修复 / 统一 reviewed 发布契约家族（层 4）。"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator
from ...materials.schema import StrictModel
from .admission_fixtures_constants import (EXPECTED_CARD_COUNT, EXPECTED_DISPLAY_TEXT_COUNT, EXPECTED_FACT_COUNT, EXPECTED_ORIGINAL_CARD_COUNT, EXPECTED_REMEDIATION_CARD_COUNT, EXPECTED_SEMANTIC_EDGE_COUNT, ExtractionMode, R2242_FIXTURE_VERSION, REVIEWED_AT_UTC, REVIEWER, SOURCE_PHASE2241_FIXTURE, SOURCE_PHASE224_FIXTURE, Step3ReviewedSourceTier, _ANCHOR_ID_PATTERN, _CARD_ID_PATTERN, _FACT_ID_PATTERN, _SHA256_PATTERN)


class Step3SourceOwnedSemanticEdgeAudit(StrictModel):
    fact_id: str = Field(pattern=_FACT_ID_PATTERN)
    source_node_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    basis: str = Field(min_length=12)
    confidence: float = Field(ge=0.0, le=1.0)
    figure_evidence_id: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)


class RemediationEvidenceBinding(StrictModel):
    evidence_id: str = Field(pattern=r"^ev:[0-9a-f]{24}$")
    selected_text: str = Field(min_length=1)
    anchor_span_ids: tuple[str, ...] = ()
    extraction_mode: ExtractionMode
    manual_visual_confirmation: bool = False
    review_basis: str = Field(min_length=24, max_length=1200)

    @model_validator(mode="after")
    def validate_binding(self) -> "RemediationEvidenceBinding":
        if len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("remediation anchor spans must be unique")
        if any(not re.fullmatch(_ANCHOR_ID_PATTERN, item) for item in self.anchor_span_ids):
            raise ValueError("remediation anchor span id is malformed")
        visual_modes = {ExtractionMode.CROP_CORRECTED, ExtractionMode.SUBSTRING_EXCLUDING_EXAM_TAG}
        if (self.extraction_mode in visual_modes) != self.manual_visual_confirmation:
            raise ValueError("visual extraction modes require explicit manual confirmation")
        if not self.anchor_span_ids and self.extraction_mode != ExtractionMode.SUBSTRING_EXCLUDING_EXAM_TAG:
            raise ValueError("only an explicitly confirmed exam-tag suffix may remain unanchored")
        return self


class RemediationTableCell(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    evidence_id: str = Field(pattern=r"^ev:[0-9a-f]{24}$")
    text: str = Field(min_length=1)
    extraction_mode: Literal["normalized_exact", "crop_corrected"] = "normalized_exact"
    manual_visual_confirmation: bool = False

    @model_validator(mode="after")
    def validate_cell(self) -> "RemediationTableCell":
        if (self.extraction_mode == "crop_corrected") != self.manual_visual_confirmation:
            raise ValueError("OCR-corrected table cells require explicit visual confirmation")
        return self


class RemediationTableGrid(StrictModel):
    table_evidence_id: str = Field(pattern=r"^ev:[0-9a-f]{24}$")
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    cell_count: int = Field(ge=1)
    cells: tuple[RemediationTableCell, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_grid(self) -> "RemediationTableGrid":
        if self.cell_count != len(self.cells):
            raise ValueError("table grid cell count must match cells")
        coordinates = {(item.row, item.column) for item in self.cells}
        if len(coordinates) != len(self.cells):
            raise ValueError("table grid coordinates must be unique")
        if any(item.row >= self.row_count or item.column >= self.column_count for item in self.cells):
            raise ValueError("table grid cell lies outside declared dimensions")
        if len({item.evidence_id for item in self.cells}) != len(self.cells):
            raise ValueError("table grid cell Evidence ids must be unique")
        return self


class Step3MergedSourceOwnedDisplayText(StrictModel):
    fact_id: str = Field(pattern=_FACT_ID_PATTERN)
    text: str = Field(min_length=1)
    source_kind: Literal[
        "reviewed_fact_assertion",
        "reviewed_semantic_node_label",
        "reviewed_remediation_assertion",
        "reviewed_remediation_ordered_list",
        "reviewed_remediation_table_grid",
        "inherited_reviewed_fact_assertion",
    ]
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    anchor_span_ids: tuple[str, ...] = ()
    source_table_ids: tuple[str, ...] = ()
    continuation_binding_ids: tuple[str, ...] = ()
    complete_table_grid_verified: bool
    semantic_sidecar_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    semantic_node_id: str | None = None
    source_manifest_sha256s: tuple[str, ...] = ()
    crop_sha256s: tuple[str, ...] = ()
    evidence_bindings: tuple[RemediationEvidenceBinding, ...] = ()
    table_grid: RemediationTableGrid | None = None
    remediation_review_basis: str | None = Field(default=None, min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_display(self) -> "Step3MergedSourceOwnedDisplayText":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("merged display Evidence ids must be unique")
        if len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("merged display anchor spans must be unique")
        if self.source_kind == "reviewed_semantic_node_label":
            if self.anchor_span_ids or not self.semantic_sidecar_sha256 or not self.semantic_node_id:
                raise ValueError("semantic display text requires sidecar/node provenance and no text anchor")
            if self.source_manifest_sha256s or self.crop_sha256s or self.evidence_bindings or self.table_grid:
                raise ValueError("Phase 2R.2.4 semantic display cannot carry remediation provenance")
        elif self.source_kind == "reviewed_fact_assertion":
            if not self.anchor_span_ids or self.semantic_sidecar_sha256 or self.semantic_node_id:
                raise ValueError("reviewed Fact display requires anchors and no semantic-node metadata")
            if self.source_manifest_sha256s or self.crop_sha256s or self.evidence_bindings or self.table_grid:
                raise ValueError("Phase 2R.2.4 Fact display cannot carry remediation provenance")
        else:
            if not self.anchor_span_ids or not self.source_manifest_sha256s or not self.remediation_review_basis:
                raise ValueError("remediation display requires anchors, source manifest and review basis")
            if self.semantic_sidecar_sha256 or self.semantic_node_id:
                raise ValueError("remediation display cannot synthesize semantic-node provenance")
            if self.source_kind == "reviewed_remediation_table_grid":
                if self.table_grid is None or not self.complete_table_grid_verified or not self.source_table_ids:
                    raise ValueError("remediation table display requires complete grid provenance")
            elif self.table_grid is not None:
                raise ValueError("non-table remediation display cannot carry a table grid")
        return self


class Step3MergedReviewedReleaseCard(StrictModel):
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_tier: Step3ReviewedSourceTier
    source_fixture_path: str = Field(min_length=1)
    source_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_reviewed_card_id: str = Field(min_length=24)
    source_card_id: str = Field(pattern=_CARD_ID_PATTERN)
    source_projection_id: str = Field(min_length=24)
    source_draft_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_step3_request_id: str | None = None
    source_step3_response_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    sample_id: str = Field(min_length=1)
    canonical_title: str = Field(min_length=2, max_length=64)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: str = Field(min_length=2, max_length=32)
    image_prompt: str = Field(min_length=24)
    expected_texts: tuple[str, ...] = Field(min_length=1)
    layout_description: str = Field(min_length=12)
    display_text_provenance: tuple[Step3MergedSourceOwnedDisplayText, ...] = Field(min_length=1)
    semantic_edge_provenance: tuple[Step3SourceOwnedSemanticEdgeAudit, ...] = ()
    review_state: Literal["reviewed"] = "reviewed"
    review_basis: str = Field(min_length=32, max_length=2000)

    @model_validator(mode="after")
    def validate_card(self) -> "Step3MergedReviewedReleaseCard":
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("merged Card Fact ids must be unique")
        if {item.fact_id for item in self.display_text_provenance} != set(self.fact_ids):
            raise ValueError("merged display provenance must cover every Card Fact")
        expected = tuple(item.text for item in self.display_text_provenance)
        if self.expected_texts != expected:
            raise ValueError("merged expected texts must exactly equal source-owned display texts")
        if not all(text in self.image_prompt for text in expected):
            raise ValueError("merged image prompt must contain every source-owned display text")
        if any(item.fact_id not in self.fact_ids for item in self.semantic_edge_provenance):
            raise ValueError("semantic edge must bind a Fact in the same Card")
        if self.source_tier == Step3ReviewedSourceTier.PHASE224:
            if not self.source_step3_request_id or not self.source_step3_response_sha256:
                raise ValueError("Phase 2R.2.4 Card must preserve real Step 3 request provenance")
        elif self.source_step3_request_id is not None or self.source_step3_response_sha256 is not None:
            raise ValueError("remediation Card must not claim a new real Step 3 request")
        return self


class Step3UnifiedReviewedReleaseFixture(StrictModel):
    schema_version: str = R2242_FIXTURE_VERSION
    fixture_id: str = "gonglu_phase2r2242_step3_reviewed_release"
    source_phase224_fixture_path: str = SOURCE_PHASE224_FIXTURE.as_posix()
    source_phase224_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_phase2241_fixture_path: str = SOURCE_PHASE2241_FIXTURE.as_posix()
    source_phase2241_fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    reviewed_card_count: int = EXPECTED_CARD_COUNT
    reviewed_fact_count: int = EXPECTED_FACT_COUNT
    source_owned_display_text_count: int = EXPECTED_DISPLAY_TEXT_COUNT
    semantic_edge_count: int = EXPECTED_SEMANTIC_EDGE_COUNT
    cards: tuple[Step3MergedReviewedReleaseCard, ...]
    reviewer: str = REVIEWER
    reviewed_at_utc: str = Field(default=REVIEWED_AT_UTC, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    review_basis: str = Field(min_length=48, max_length=2000)

    @model_validator(mode="after")
    def validate_fixture(self) -> "Step3UnifiedReviewedReleaseFixture":
        if self.schema_version != R2242_FIXTURE_VERSION:
            raise ValueError("unified fixture schema version must remain bound to Phase 2R.2.4.2")
        if (
            self.reviewed_card_count != EXPECTED_CARD_COUNT
            or self.reviewed_fact_count != EXPECTED_FACT_COUNT
            or self.source_owned_display_text_count != EXPECTED_DISPLAY_TEXT_COUNT
            or self.semantic_edge_count != EXPECTED_SEMANTIC_EDGE_COUNT
        ):
            raise ValueError("unified fixture declared totals must remain exact")
        indices = tuple(item.card_index for item in self.cards)
        if indices != tuple(range(1, EXPECTED_CARD_COUNT + 1)):
            raise ValueError("unified fixture Cards must be ordered at indices 1..16")
        fact_ids = [fact_id for card in self.cards for fact_id in card.fact_ids]
        if len(fact_ids) != EXPECTED_FACT_COUNT or len(fact_ids) != len(set(fact_ids)):
            raise ValueError("unified fixture must cover 28 Facts exactly once")
        if len(self.cards) != EXPECTED_CARD_COUNT or len({item.source_card_id for item in self.cards}) != EXPECTED_CARD_COUNT:
            raise ValueError("unified fixture must cover 16 source Cards exactly once")
        tier_counts = {tier: sum(item.source_tier == tier for item in self.cards) for tier in Step3ReviewedSourceTier}
        if tier_counts != {
            Step3ReviewedSourceTier.PHASE224: EXPECTED_ORIGINAL_CARD_COUNT,
            Step3ReviewedSourceTier.PHASE2241: EXPECTED_REMEDIATION_CARD_COUNT,
        }:
            raise ValueError("unified fixture must retain the 8+8 reviewed source tiers")
        display_count = sum(len(item.display_text_provenance) for item in self.cards)
        edge_count = sum(len(item.semantic_edge_provenance) for item in self.cards)
        if display_count != EXPECTED_DISPLAY_TEXT_COUNT or edge_count != EXPECTED_SEMANTIC_EDGE_COUNT:
            raise ValueError("unified fixture display-text/semantic-edge totals do not close")
        return self
