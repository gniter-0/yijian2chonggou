"""Phase2R4 发布工件模型（拆分层 2）。

FrozenArtifact/FreezeAudit/ContractBundle/ModelInput/PromptContract/Request/
FakeArtifact(+Manifest)/CacheRecord/JournalRecord/BatchEntry/BatchManifest/
OfflineGate/RegressionResult/Report 十五个 StrictModel。ContractBundle 与
ModelInput 的字段注解引用 GenericCardContract，故运行期 import models_generic。
"""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from examcard.materials.schema import StrictModel
from examcard.knowledge.contracts.admission_fixtures import (
    EXPECTED_CARD_COUNT,
    EXPECTED_DISPLAY_TEXT_COUNT,
    EXPECTED_FACT_COUNT,
    EXPECTED_SEMANTIC_EDGE_COUNT,
)

from .step4_contract_constants import (
    BATCH_VERSION,
    CACHE_VERSION,
    CONTRACT_BUNDLE_VERSION,
    FAKE_ARTIFACT_VERSION,
    FROZEN_ARTIFACT_COUNT,
    GATE_VERSION,
    HISTORICAL_IDENTITY_COUNT,
    JOURNAL_VERSION,
    MODEL_INPUT_VERSION,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    PHASE,
    PROMPT_VERSION,
    REGRESSION_VERSION,
    RENDER_PRESENTATIONS,
    REPORT_VERSION,
    REQUEST_VERSION,
    _CARD,
    _FAKE_FILENAME,
    _JOB,
    _MODEL_INPUT,
    _REQUEST,
    _SHA,
)
from .step4_contract_models_generic import GenericCardContract


class Phase2R4FrozenArtifact(StrictModel):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA)


class Phase2R4FreezeAudit(StrictModel):
    schema_version: Literal["phase2r4_freeze_audit_v1"] = "phase2r4_freeze_audit_v1"
    valid: bool
    artifact_count: Literal[501] = FROZEN_ARTIFACT_COUNT
    artifacts: tuple[Phase2R4FrozenArtifact, ...] = Field(
        min_length=FROZEN_ARTIFACT_COUNT, max_length=FROZEN_ARTIFACT_COUNT
    )
    aggregate_sha256: str = Field(pattern=_SHA)
    historical_identity_count: Literal[19] = HISTORICAL_IDENTITY_COUNT
    historical_identity_set_sha256: str = Field(pattern=_SHA)
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_audit(self) -> "Phase2R4FreezeAudit":
        exact = len(self.artifacts) == self.artifact_count
        unique = len({item.path for item in self.artifacts}) == self.artifact_count
        if self.valid != (exact and unique and not self.failure_codes):
            raise ValueError("R4 freeze validity mismatch")
        return self


class Phase2R4ContractBundle(StrictModel):
    schema_version: Literal["step4_generic_contract_bundle_v1"] = CONTRACT_BUNDLE_VERSION
    source_unified_fixture_sha256: str = Field(pattern=_SHA)
    source_r319_handoff_sha256: str = Field(pattern=_SHA)
    cards: tuple[GenericCardContract, ...] = Field(min_length=EXPECTED_CARD_COUNT, max_length=EXPECTED_CARD_COUNT)
    card_count: Literal[16] = EXPECTED_CARD_COUNT
    fact_count: Literal[28] = EXPECTED_FACT_COUNT
    visible_text_count: Literal[52] = EXPECTED_DISPLAY_TEXT_COUNT
    semantic_edge_count: Literal[24] = EXPECTED_SEMANTIC_EDGE_COUNT


class Phase2R4ModelInput(StrictModel):
    schema_version: Literal["step4_generic_model_input_v1"] = MODEL_INPUT_VERSION
    model_input_id: str = Field(pattern=_MODEL_INPUT)
    source_unified_fixture_sha256: str = Field(pattern=_SHA)
    source_r319_handoff_sha256: str = Field(pattern=_SHA)
    card_count: Literal[16] = EXPECTED_CARD_COUNT
    fact_count: Literal[28] = EXPECTED_FACT_COUNT
    visible_text_count: Literal[52] = EXPECTED_DISPLAY_TEXT_COUNT
    semantic_edge_count: Literal[24] = EXPECTED_SEMANTIC_EDGE_COUNT
    cards: tuple[GenericCardContract, ...] = Field(min_length=EXPECTED_CARD_COUNT, max_length=EXPECTED_CARD_COUNT)

    @model_validator(mode="after")
    def validate_input(self) -> "Phase2R4ModelInput":
        if tuple(item.card_index for item in self.cards) != tuple(range(1, EXPECTED_CARD_COUNT + 1)):
            raise ValueError("model input Cards must remain ordered 1..16")
        facts = [fact_id for card in self.cards for fact_id in card.fact_ids]
        if len(facts) != EXPECTED_FACT_COUNT or len(facts) != len(set(facts)):
            raise ValueError("model input must cover 28 Facts exactly once")
        if len({card.source_card_id for card in self.cards}) != EXPECTED_CARD_COUNT:
            raise ValueError("model input must cover 16 Cards exactly once")
        if sum(len(card.visible_texts) for card in self.cards) != EXPECTED_DISPLAY_TEXT_COUNT:
            raise ValueError("model input must cover 52 source-owned display texts")
        if sum(len(card.semantic_layout_edges) for card in self.cards) != EXPECTED_SEMANTIC_EDGE_COUNT:
            raise ValueError("model input must carry 24 semantic layout edges")
        return self


class Phase2R4PromptContract(StrictModel):
    schema_version: Literal["step4_generic_prompt_contract_v1"] = PROMPT_VERSION
    instructions: tuple[str, ...] = (
        "Render only the supplied source-owned visible_text values verbatim.",
        "Use semantic layout edges for placement only; never render their relation values as visible text.",
        "Preserve the supplied table, ordered-list, diagram, constraint, and formula contracts exactly.",
        "Use a stable 1536x1024 canvas and do not infer source content or add visible text.",
        "Do not use review metadata or presentation text as a content source.",
    )
    supported_render_presentations: tuple[str, ...] = RENDER_PRESENTATIONS
    source_text_only: Literal[True] = True
    relation_values_layout_only: Literal[True] = True
    additional_visible_text_allowed: Literal[False] = False
    real_generation_authorized: Literal[False] = False


class Phase2R4Request(StrictModel):
    schema_version: Literal["step4_generic_offline_request_v1"] = REQUEST_VERSION
    request_id: str = Field(pattern=_REQUEST)
    backend: Literal["deterministic_fake_image_backend_r4_v1"] = "deterministic_fake_image_backend_r4_v1"
    model_input: Phase2R4ModelInput
    prompt_contract: Phase2R4PromptContract
    cache_version: Literal["step4_generic_cache_v1"] = CACHE_VERSION
    journal_version: Literal["step4_generic_journal_v1"] = JOURNAL_VERSION
    real_http_call_budget: Literal[0] = 0
    real_image_generation_authorized: Literal[False] = False
    batch_generation_authorized: Literal[False] = False


class Phase2R4FakeArtifact(StrictModel):
    schema_version: Literal["step4_generic_fake_artifact_manifest_v1"] = FAKE_ARTIFACT_VERSION
    artifact_id: str = Field(pattern=r"^step4-generic-fake-artifact:[0-9a-f]{24}$")
    job_id: str = Field(pattern=_JOB)
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_card_id: str = Field(pattern=_CARD)
    presentation: Literal["table", "diagram", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    rendered_visible_texts: tuple[str, ...] = Field(min_length=1)
    rendered_visible_text_sha256s: tuple[str, ...] = Field(min_length=1)
    rendered_table_cell_sha256s: tuple[str, ...] = ()
    rendered_list_item_sha256s: tuple[str, ...] = ()
    rendered_semantic_edge_sha256s: tuple[str, ...] = ()
    rendered_token_sha256s: tuple[str, ...] = ()
    output_filename: str = Field(pattern=_FAKE_FILENAME)
    width: Literal[1536] = OUTPUT_WIDTH
    height: Literal[1024] = OUTPUT_HEIGHT
    simulated_image_sha256: str = Field(pattern=_SHA)
    artifact_contract_sha256: str = Field(pattern=_SHA)
    materialized: Literal[False] = False
    real_image: Literal[False] = False


class Phase2R4FakeArtifactManifest(StrictModel):
    schema_version: Literal["step4_generic_fake_artifact_manifest_v1"] = FAKE_ARTIFACT_VERSION
    request_id: str = Field(pattern=_REQUEST)
    request_sha256: str = Field(pattern=_SHA)
    artifacts: tuple[Phase2R4FakeArtifact, ...] = Field(min_length=EXPECTED_CARD_COUNT, max_length=EXPECTED_CARD_COUNT)
    artifact_count: Literal[16] = EXPECTED_CARD_COUNT
    materialized_file_count: Literal[0] = 0
    real_image_count: Literal[0] = 0
    aggregate_sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def validate_manifest(self) -> "Phase2R4FakeArtifactManifest":
        if tuple(item.card_index for item in self.artifacts) != tuple(range(1, EXPECTED_CARD_COUNT + 1)):
            raise ValueError("fake artifacts must remain ordered 1..16")
        if len({item.output_filename for item in self.artifacts}) != EXPECTED_CARD_COUNT:
            raise ValueError("fake artifact filenames must be unique")
        return self


class Phase2R4CacheRecord(StrictModel):
    schema_version: Literal["step4_generic_cache_v1"] = CACHE_VERSION
    cache_key: str = Field(pattern=_SHA)
    request_id: str = Field(pattern=_REQUEST)
    request_sha256: str = Field(pattern=_SHA)
    artifact_manifest_sha256: str = Field(pattern=_SHA)
    cache_hit: Literal[False] = False
    external_cache_reads: Literal[0] = 0
    external_cache_writes: Literal[0] = 0


class Phase2R4JournalRecord(StrictModel):
    schema_version: Literal["step4_generic_journal_v1"] = JOURNAL_VERSION
    event: Literal["generic_offline_batch_compilation_completed"] = "generic_offline_batch_compilation_completed"
    request_id: str = Field(pattern=_REQUEST)
    request_sha256: str = Field(pattern=_SHA)
    gate_sha256: str = Field(pattern=_SHA)
    card_count: Literal[16] = EXPECTED_CARD_COUNT
    fact_count: Literal[28] = EXPECTED_FACT_COUNT
    network_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0


class Phase2R4BatchEntry(StrictModel):
    card_index: int = Field(ge=1, le=EXPECTED_CARD_COUNT)
    source_card_id: str = Field(pattern=_CARD)
    sample_id: str = Field(min_length=1)
    source_presentation: str = Field(min_length=2, max_length=32)
    render_presentation: Literal["table", "diagram", "ordered_list", "constraint", "formula"]
    fact_ids: tuple[str, ...] = Field(min_length=1)
    source_owned_display_text_count: int = Field(ge=1)
    semantic_edge_count: int = Field(ge=0)
    contract_sha256: str = Field(pattern=_SHA)
    model_input_sha256: str = Field(pattern=_SHA)
    disposition: Literal["prior_reviewed_reference", "offline_compiled"]
    offline_gate_valid: Literal[True] = True
    generation_authorized: Literal[False] = False


class Phase2R4BatchManifest(StrictModel):
    schema_version: Literal["step4_generic_batch_manifest_v1"] = BATCH_VERSION
    manifest_id: str = Field(pattern=r"^step4-generic-batch:[0-9a-f]{24}$")
    source_unified_fixture_sha256: str = Field(pattern=_SHA)
    source_r319_handoff_sha256: str = Field(pattern=_SHA)
    cards: tuple[Phase2R4BatchEntry, ...] = Field(min_length=EXPECTED_CARD_COUNT, max_length=EXPECTED_CARD_COUNT)
    card_count: Literal[16] = EXPECTED_CARD_COUNT
    prior_reviewed_card_count: Literal[4] = 4
    offline_compiled_card_count: Literal[12] = 12
    batch_generation_authorized: Literal[False] = False
    real_http_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0

    @model_validator(mode="after")
    def validate_batch(self) -> "Phase2R4BatchManifest":
        if tuple(item.card_index for item in self.cards) != tuple(range(1, EXPECTED_CARD_COUNT + 1)):
            raise ValueError("batch manifest Cards must remain ordered 1..16")
        if tuple(item.card_index for item in self.cards if item.disposition == "prior_reviewed_reference") != (2, 3, 4, 6):
            raise ValueError("batch manifest prior reviewed references changed")
        if sum(item.disposition == "offline_compiled" for item in self.cards) != 12:
            raise ValueError("batch manifest must compile exactly 12 ungenerated Cards")
        if any(item.generation_authorized for item in self.cards):
            raise ValueError("batch manifest cannot authorize generation")
        return self


class Phase2R4OfflineGate(StrictModel):
    schema_version: Literal["step4_generic_batch_offline_gate_v1"] = GATE_VERSION
    valid: bool
    freeze_valid: bool
    source_fixture_valid: bool
    source_r319_handoff_valid: bool
    source_hashes_exact: bool
    card_exactly_once: bool
    fact_exactly_once: bool
    display_text_count_exact: bool
    semantic_edge_count_exact: bool
    presentation_dispatch_exact: bool
    table_contracts_complete: bool
    ordered_list_contracts_complete: bool
    diagram_contracts_complete: bool
    constraint_contracts_complete: bool
    formula_contracts_complete: bool
    source_provenance_exact: bool
    display_text_verbatim: bool
    relation_layout_only: bool
    fake_artifacts_exact: bool
    batch_manifest_exact: bool
    no_cross_sample_grouping: bool
    no_forbidden_model_keys: bool
    no_forbidden_text: bool
    no_unknown_fields: bool
    no_materialized_files: bool
    no_real_calls: bool
    generic_contract_reusable: bool
    generic_step4_offline_ready: bool
    batch_generation_authorized: Literal[False] = False
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_gate(self) -> "Phase2R4OfflineGate":
        checks = self.model_dump(
            exclude={
                "schema_version",
                "valid",
                "generic_step4_offline_ready",
                "batch_generation_authorized",
                "failure_codes",
            }
        )
        expected = all(checks.values()) and not self.failure_codes
        if self.valid != expected or self.generic_step4_offline_ready != expected:
            raise ValueError("R4 generic offline readiness must match every Gate")
        return self


class Phase2R4RegressionResult(StrictModel):
    schema_version: Literal["step4_generic_regression_result_v1"] = REGRESSION_VERSION
    case_id: str = Field(min_length=2)
    expected_failure_codes: tuple[str, ...] = Field(min_length=1)
    observed_failure_codes: tuple[str, ...] = Field(min_length=1)
    caught: bool

    @model_validator(mode="after")
    def validate_caught(self) -> "Phase2R4RegressionResult":
        if self.caught != set(self.expected_failure_codes).issubset(self.observed_failure_codes):
            raise ValueError("R4 regression result does not close its expected failure")
        return self


class Phase2R4Report(StrictModel):
    schema_version: Literal["phase2r4_step4_generic_report_v1"] = REPORT_VERSION
    phase: Literal["2R.4"] = PHASE
    generated_at_utc: str = Field(min_length=20)
    freeze_audit: Phase2R4FreezeAudit
    contract_bundle_sha256: str = Field(pattern=_SHA)
    model_input_sha256: str = Field(pattern=_SHA)
    request_sha256: str = Field(pattern=_SHA)
    fake_artifact_manifest_sha256: str = Field(pattern=_SHA)
    batch_manifest_sha256: str = Field(pattern=_SHA)
    gate: Phase2R4OfflineGate
    regressions: tuple[Phase2R4RegressionResult, ...] = Field(min_length=15, max_length=15)
    all_regressions_caught: bool
    deterministic_fake_backend_calls: Literal[1] = 1
    network_calls: Literal[0] = 0
    relay_calls: Literal[0] = 0
    image_api_calls: Literal[0] = 0
    real_images_generated: Literal[0] = 0
    source_mutations: Literal[0] = 0
    generic_step4_offline_ready: bool
    batch_generation_authorized: Literal[False] = False
    next_required_action: str = Field(min_length=40)

    @model_validator(mode="after")
    def validate_report(self) -> "Phase2R4Report":
        if self.all_regressions_caught != all(item.caught for item in self.regressions):
            raise ValueError("R4 regression summary mismatch")
        if self.generic_step4_offline_ready != self.gate.generic_step4_offline_ready:
            raise ValueError("R4 report readiness mismatch")
        return self
