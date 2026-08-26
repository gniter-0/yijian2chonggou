"""cm35 切分产物：r319 试点图片 / 处置 / 交接契约家族（层 6）。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from ...materials.schema import StrictModel
from .admission_fixtures_constants import (EXPECTED_CARD_COUNT, HANDOFF_VERSION, SOURCE_R316_HANDOFF_POSIX, UNIFIED_FIXTURE_POSIX, _CARD, _SHA)


class Step4ReviewedImageReferenceR319(StrictModel):
    card_index: Literal[2, 3, 4, 6]
    source_card_id: str = Field(pattern=_CARD)
    fixture_id: str = Field(min_length=32)
    fixture_path: str = Field(min_length=10)
    fixture_sha256: str = Field(pattern=_SHA)
    image_path: str = Field(min_length=10)
    image_sha256: str = Field(pattern=_SHA)
    presentation: Literal["diagram", "ordered_list", "constraint", "table"]
    render_contract_kind: Literal[
        "diagram_connector_topology", "ordered_list_exact_eight_rows", "constraint_exact_five_blocks", "complete_table_grid"
    ]

    @model_validator(mode="after")
    def validate_reference(self) -> "Step4ReviewedImageReferenceR319":
        expected = {
            2: ("diagram", "diagram_connector_topology"),
            3: ("ordered_list", "ordered_list_exact_eight_rows"),
            4: ("constraint", "constraint_exact_five_blocks"),
            6: ("table", "complete_table_grid"),
        }
        if (self.presentation, self.render_contract_kind) != expected[self.card_index]:
            raise ValueError("R3.19 contract reference mismatch")
        return self


class Step4PilotCardDispositionR319(StrictModel):
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_card_id: str = Field(pattern=_CARD)
    sample_id: str = Field(min_length=1)
    presentation: str = Field(min_length=2, max_length=32)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    source_owned_display_text_count: int = Field(ge=1)
    semantic_edge_count: int = Field(ge=0)
    disposition: Literal["reviewed", "ungenerated"]
    reviewed_image_fixture_id: str | None = None
    reviewed_image_fixture_path: str | None = None
    reviewed_image_fixture_sha256: str | None = Field(default=None, pattern=_SHA)
    render_contract_kind: str | None = None
    render_contract_reused_from_other_card: Literal[False] = False
    generation_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_disposition(self) -> "Step4PilotCardDispositionR319":
        reviewed = self.card_index in {2, 3, 4, 6}
        refs = (
            self.reviewed_image_fixture_id,
            self.reviewed_image_fixture_path,
            self.reviewed_image_fixture_sha256,
            self.render_contract_kind,
        )
        if reviewed != (self.disposition == "reviewed"):
            raise ValueError("R3.19 reviewed inventory mismatch")
        if reviewed != all(item is not None for item in refs):
            raise ValueError("R3.19 fixture reference closure mismatch")
        if not reviewed and any(item is not None for item in refs):
            raise ValueError("R3.19 ungenerated Card cannot reference a fixture")
        return self


class Step4PilotHandoffRefresh(StrictModel):
    schema_version: Literal["step4_pilot_handoff_refresh_r319_v1"] = HANDOFF_VERSION
    manifest_id: str = Field(pattern=r"^step4-pilot-handoff-r319:[0-9a-f]{24}$")
    source_r316_handoff_path: Literal[
        "data/semantic_llm_v2/phase2r316_step4_card3_reviewed_image_fixture_run1/step4-pilot-handoff-refresh.json"
    ] = SOURCE_R316_HANDOFF_POSIX
    source_r316_handoff_sha256: str = Field(pattern=_SHA)
    source_step3_fixture_path: Literal[
        "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml"
    ] = UNIFIED_FIXTURE_POSIX
    source_step3_fixture_sha256: str = Field(pattern=_SHA)
    reviewed_image_fixtures: tuple[Step4ReviewedImageReferenceR319, ...] = Field(min_length=4, max_length=4)
    card_count: Literal[16] = 16
    reviewed_card_count: Literal[4] = 4
    ungenerated_card_count: Literal[12] = 12
    cards: tuple[Step4PilotCardDispositionR319, ...] = Field(min_length=16, max_length=16)
    card2_image_reviewed: Literal[True] = True
    card3_image_reviewed: Literal[True] = True
    card4_image_reviewed: Literal[True] = True
    card6_image_reviewed: Literal[True] = True
    other_cards_require_independent_contracts: Literal[True] = True
    ordered_list_contract_generalization_allowed: Literal[False] = False
    table_contract_generalization_allowed: Literal[False] = False
    diagram_contract_generalization_allowed: Literal[False] = False
    constraint_contract_generalization_allowed: Literal[False] = False
    batch_generation_authorized: Literal[False] = False
    real_pilot_authorized: Literal[False] = False
    real_http_call_budget: Literal[0] = 0

    @model_validator(mode="after")
    def validate_manifest(self) -> "Step4PilotHandoffRefresh":
        if tuple(item.card_index for item in self.cards) != tuple(range(1, 17)):
            raise ValueError("R3.19 Cards must be ordered 1..16")
        if tuple(item.card_index for item in self.cards if item.disposition == "reviewed") != (2, 3, 4, 6):
            raise ValueError("R3.19 reviewed inventory must be Card 2, 3, 4, 6")
        if tuple(item.card_index for item in self.reviewed_image_fixtures) != (2, 3, 4, 6):
            raise ValueError("R3.19 fixture references must be ordered 2, 3, 4, 6")
        if any(item.render_contract_reused_from_other_card or item.generation_authorized for item in self.cards):
            raise ValueError("R3.19 handoff cannot authorize or reuse contracts")
        return self
