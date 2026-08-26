"""cm35 切分产物：admission 契约家族的版本常量、正则、噪声标记与两个 StrEnum（层 0，无兄弟依赖）。"""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Final


HASH_PATTERN = r"^[0-9a-f]{64}$"


ID_PATTERN = r"^[a-z0-9][a-z0-9._:-]{2,255}$"


NOISE_MARKERS = (
    "水印",
    "二维码",
    "扫码",
    "微信",
    "QQ",
    "联系电话",
    "联系地址",
    "环球网校",
    "嗨学网",
    "荣胜教育",
    "www.",
)


SOURCE_PAGE_VERSION: Final = "phase2r84_source_page_binding_v1"


PROVENANCE_VERSION: Final = "phase2r84_typed_element_provenance_v1"


TYPED_TEXT_VERSION: Final = "phase2r84_typed_text_base_v1"


NODE_VERSION: Final = "phase2r84_diagram_node_v1"


EDGE_VERSION: Final = "phase2r84_diagram_edge_v1"


DIAGRAM_VERSION: Final = "phase2r84_diagram_contract_v1"


TABLE_CELL_VERSION: Final = "phase2r84_table_cell_v1"


TABLE_VERSION: Final = "phase2r84_table_contract_v1"


LIST_ITEM_VERSION: Final = "phase2r84_ordered_list_item_v1"


LIST_VERSION: Final = "phase2r84_ordered_list_contract_v1"


CONSTRAINT_TOKEN_VERSION: Final = "phase2r84_constraint_token_v1"


CONSTRAINT_VERSION: Final = "phase2r84_constraint_contract_v1"


FORMULA_TOKEN_VERSION: Final = "phase2r84_formula_token_v1"


FORMULA_VERSION: Final = "phase2r84_formula_contract_v1"


REVIEWED_CANDIDATE_VERSION: Final = "phase2r84_reviewed_typed_evidence_candidate_v1"


R810_FIXTURE_VERSION: Final = "phase2r810_reviewed_typed_evidence_fixture_v1"


CONTRACT_VERSION: Final = "phase2r9_step3_card_contract_v1"


DRAFT_VERSION: Final = "phase2r9_step3_deterministic_draft_v1"


HASH = r"^[0-9a-f]{64}$"


ID = r"^[a-z0-9][a-z0-9._:-]{2,255}$"


SOURCE_CANDIDATE = r"^candidate:[a-z0-9][a-z0-9._:-]{2,255}$"


CONTRACT_ID = r"^step3-card-contract:[0-9a-f]{24}$"


FORBIDDEN_TEXT = ("watermark", "水印", "evidence-only", "evidence_only", "brand mark")


DRAFT_CARD_VERSION: Final = "phase2r98_deterministic_draft_card_v1"


RELEASE_VERSION: Final = "phase2r98_deterministic_release_bundle_v1"


DECISION_VERSION: Final = "phase2r10_step3_production_human_decision_v1"


REVIEWED_CARD_VERSION: Final = "phase2r10_reviewed_step3_card_v1"


R10_FIXTURE_VERSION: Final = "phase2r10_reviewed_step3_fixture_v1"


R103_FIXTURE_VERSION: Final = "phase2r103_reviewed_step3_fixture_v1"


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


_FACT_ID_PATTERN = r"^fact:[0-9a-f]{24}$"


_CARD_ID_PATTERN = r"^v25-release-card:[0-9a-f]{24}$"


_ANCHOR_ID_PATTERN = r"^span:[0-9a-f]{24}$"


R2242_FIXTURE_VERSION = "phase2r2242_step3_reviewed_release_fixture_v1"


UNIFIED_FIXTURE_POSIX: Final = (
    "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml"
)


UNIFIED_FIXTURE = Path(UNIFIED_FIXTURE_POSIX)


SOURCE_PHASE224_FIXTURE = Path(
    "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r224_step3_reviewed_release.yaml"
)


SOURCE_PHASE2241_FIXTURE = Path(
    "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2241_reviewed_source_text_remediation.yaml"
)


REVIEWER = "codex_operator_phase2r2242_step3_release_merge_audit"


REVIEWED_AT_UTC = "2026-07-30T23:00:00Z"


EXPECTED_FACT_COUNT: Final = 28


EXPECTED_CARD_COUNT: Final = 16


EXPECTED_DISPLAY_TEXT_COUNT: Final = 52


EXPECTED_SEMANTIC_EDGE_COUNT: Final = 24


EXPECTED_ORIGINAL_CARD_COUNT = 8


EXPECTED_REMEDIATION_CARD_COUNT = 8


R104_PHASE: Final = "2R.10.4"


R104_FREEZE_VERSION: Final = "phase2r104_prior_freeze_v1"


R104_GATE_CHECK_VERSION: Final = "phase2r104_gate_check_v1"


R104_GATE_VERSION: Final = "phase2r104_offline_gate_v1"


R104_ACCEPTANCE_VERSION: Final = "phase2r104_acceptance_report_v1"


HANDOFF_VERSION: Final = "step4_pilot_handoff_refresh_r319_v1"


SOURCE_R316_HANDOFF_POSIX: Final = "data/semantic_llm_v2/phase2r316_step4_card3_reviewed_image_fixture_run1/step4-pilot-handoff-refresh.json"


SOURCE_R316_HANDOFF = Path(SOURCE_R316_HANDOFF_POSIX)


SOURCE_R316_RUN = SOURCE_R316_HANDOFF.parent


_SHA = r"^[0-9a-f]{64}$"


_CARD = r"^v25-release-card:[0-9a-f]{24}$"


class ExtractionMode(StrEnum):
    EXACT = "exact"
    NORMALIZED_EXACT = "normalized_exact"
    CROP_CORRECTED = "crop_corrected"
    SUBSTRING_EXCLUDING_EXAM_TAG = "substring_excluding_exam_tag"


class Step3ReviewedSourceTier(StrEnum):
    PHASE224 = "phase2r224_reviewed"
    PHASE2241 = "phase2r2241_remediation"
