"""step2b 事实摘要、来源族绑定、归组义务、卡片提案、结果/门禁/受阻预检（拆分层 3）。

八个 StrictModel。``V25Step2BFactSummary`` 把 Learning Golden 的评审态
（``V25FactReviewState``）与语义图桥一并收进摘要，但**评审态只喂离线预检层**，
不进 step2 模型载荷——这是原模块 docstring 里那条纪律的落点。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from ..schema import FactType

from . import CardPresentation, Step2RequestIdentity, Step2Stage, canonical_step2_sha256

from .v25_versions import (
    V25B_STEP2B_MODEL_INPUT_VERSION,
    V25B_STEP2B_PROMPT_VERSION,
    V25B_STEP2B_REQUEST_VERSION,
)
from .v25_support import _v25_id
from .v25_enums import V25BGroupingKind, V25BSourceFamilyKind, V25FactReviewState
from .v25_step2b_diagram import V25SemanticDiagramBridge
from .v25_step2b_prompt import v25b_step2b_request_payload


class V25Step2BFactSummary(StrictModel):
    """Reviewed Fact provenance used for generic V25B grouping only."""

    fact_id: str = Field(pattern=r"^fact:[0-9a-f]{24}$")
    fact_type: FactType
    canonical_title: str = Field(min_length=2, max_length=96)
    assertion_summary: str = Field(min_length=2, max_length=1000)
    primary_evidence_ids: tuple[str, ...] = Field(min_length=1)
    primary_anchor_span_ids: tuple[str, ...] = ()
    source_table_ids: tuple[str, ...] = ()
    source_topic_key: str = Field(pattern=r"^source-topic:[0-9a-f]{24}$")
    review_state: V25FactReviewState
    semantic_diagram_bridge: V25SemanticDiagramBridge | None = None

    @model_validator(mode="after")
    def validate_fact(self) -> "V25Step2BFactSummary":
        if self.canonical_title != self.canonical_title.strip() or self.assertion_summary != self.assertion_summary.strip():
            raise ValueError("V25B Fact text must be stripped")
        for label, values in (
            ("Evidence", self.primary_evidence_ids),
            ("anchor", self.primary_anchor_span_ids),
            ("source table", self.source_table_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"V25B Fact {label} IDs must be unique")
        if self.fact_type == FactType.DIAGRAM_RELATION:
            if self.semantic_diagram_bridge is None:
                raise ValueError("V25B semantic diagram Facts require a reviewed bridge")
            if self.primary_anchor_span_ids:
                raise ValueError("V25B semantic diagram Facts cannot invent textual anchor spans")
            if self.semantic_diagram_bridge.figure_evidence_id not in self.primary_evidence_ids:
                raise ValueError("V25B semantic diagram Fact must retain its figure Evidence")
        elif self.semantic_diagram_bridge is not None:
            raise ValueError("V25B non-diagram Facts cannot carry a semantic diagram bridge")
        elif not self.primary_anchor_span_ids:
            raise ValueError("V25B textual/table Facts require selected source anchor spans")
        return self


class V25BSourceFamilyBinding(StrictModel):
    """Human-reviewed relationship derived from Fact source Evidence, never Card Golden data."""

    family_id: str = Field(pattern=r"^v25b-source-family:[0-9a-f]{24}$")
    family_kind: V25BSourceFamilyKind
    member_fact_ids: tuple[str, ...] = Field(min_length=2)
    source_evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    source_manifest_sha256s: tuple[str, ...] = Field(min_length=1)
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_binding(self) -> "V25BSourceFamilyBinding":
        for label, values in (
            ("Fact", self.member_fact_ids),
            ("Evidence", self.source_evidence_ids),
            ("anchor", self.source_anchor_span_ids),
            ("manifest", self.source_manifest_sha256s),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"V25B source-family {label} IDs must be unique")
        payload = self.model_dump(mode="json", exclude={"family_id"})
        if self.family_id != _v25_id("v25b-source-family", payload):
            raise ValueError("V25B source-family ID must be deterministic")
        return self


class V25BGroupingObligation(StrictModel):
    obligation_id: str = Field(pattern=r"^v25b-obligation:[0-9a-f]{24}$")
    grouping_kind: V25BGroupingKind
    member_fact_ids: tuple[str, ...] = Field(min_length=1)
    source_table_id: str | None = None
    source_topic_key: str | None = Field(default=None, pattern=r"^source-topic:[0-9a-f]{24}$")
    required_presentation: CardPresentation

    @model_validator(mode="after")
    def validate_obligation(self) -> "V25BGroupingObligation":
        if len(self.member_fact_ids) != len(set(self.member_fact_ids)):
            raise ValueError("V25B grouping obligation Facts must be unique")
        if self.grouping_kind == V25BGroupingKind.TABLE_COMPOSITION:
            if self.source_table_id is None or self.source_topic_key is None:
                raise ValueError("V25B complete-table grouping must bind one source table and source topic")
        elif self.grouping_kind in {V25BGroupingKind.CLASSIFICATION_FAMILY, V25BGroupingKind.CONSTRAINT_FAMILY, V25BGroupingKind.ORDERED_FAMILY, V25BGroupingKind.THRESHOLD_FAMILY}:
            if self.source_topic_key is None:
                raise ValueError("V25B source rule family must bind a source topic")
        elif self.source_table_id is not None or self.source_topic_key is not None:
            raise ValueError("V25B standalone grouping cannot claim table or source-topic provenance")
        expected = {
            V25BGroupingKind.STANDALONE: None,
            V25BGroupingKind.TABLE_COMPOSITION: CardPresentation.TABLE,
            V25BGroupingKind.CLASSIFICATION_FAMILY: CardPresentation.TABLE,
            V25BGroupingKind.CONSTRAINT_FAMILY: CardPresentation.CONSTRAINT,
            V25BGroupingKind.ORDERED_FAMILY: CardPresentation.ORDERED_LIST,
            V25BGroupingKind.THRESHOLD_FAMILY: CardPresentation.THRESHOLD,
        }[self.grouping_kind]
        if expected is not None and self.required_presentation != expected:
            raise ValueError("V25B grouping obligation presentation is not source-derived")
        expected_id = _v25_id(
            "v25b-obligation",
            {
                "grouping_kind": self.grouping_kind.value,
                "member_fact_ids": self.member_fact_ids,
                "source_table_id": self.source_table_id,
                "source_topic_key": self.source_topic_key,
                "required_presentation": self.required_presentation.value,
            },
        )
        if self.obligation_id != expected_id:
            raise ValueError("V25B grouping obligation ID must be deterministic")
        return self


class V25BModelCardProposal(StrictModel):
    title: str = Field(min_length=2, max_length=96)
    primary_fact_ids: tuple[str, ...] = Field(min_length=1)
    presentation: CardPresentation
    split_reason: str = Field(min_length=6, max_length=400)

    @model_validator(mode="after")
    def validate_card(self) -> "V25BModelCardProposal":
        if self.title != self.title.strip() or self.split_reason != self.split_reason.strip():
            raise ValueError("V25B Card text must be stripped")
        if len(self.primary_fact_ids) != len(set(self.primary_fact_ids)):
            raise ValueError("V25B Card Fact IDs must be unique")
        return self


class V25BStep2BResultPayload(StrictModel):
    proposals: tuple[V25BModelCardProposal, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_payload(self) -> "V25BStep2BResultPayload":
        groups = [item.primary_fact_ids for item in self.proposals]
        if len(groups) != len(set(groups)):
            raise ValueError("V25B response cannot duplicate a primary Fact group")
        return self


class V25BStep2BRequest(StrictModel):
    """Generic V25B request. Draft Facts are rejected before model dispatch."""

    identity: Step2RequestIdentity
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    material_profile_id: str = Field(min_length=1)
    model_input_version: str = V25B_STEP2B_MODEL_INPUT_VERSION
    prompt_version: str = V25B_STEP2B_PROMPT_VERSION
    facts: tuple[V25Step2BFactSummary, ...] = Field(min_length=1)
    grouping_obligations: tuple[V25BGroupingObligation, ...] = Field(min_length=1)
    source_family_bindings: tuple[V25BSourceFamilyBinding, ...] = ()

    @model_validator(mode="after")
    def validate_request(self) -> "V25BStep2BRequest":
        if (
            self.identity.stage != Step2Stage.CARD_PLANNING
            or self.identity.request_version != V25B_STEP2B_REQUEST_VERSION
        ):
            raise ValueError("V25B Step 2B requires its own generic request identity")
        if self.model_input_version != V25B_STEP2B_MODEL_INPUT_VERSION:
            raise ValueError("V25B model-input version mismatch")
        if self.prompt_version != V25B_STEP2B_PROMPT_VERSION:
            raise ValueError("V25B prompt version mismatch")
        if any(item.review_state != V25FactReviewState.REVIEWED for item in self.facts):
            raise ValueError("V25B refuses unreviewed Facts before model dispatch")
        fact_ids = [item.fact_id for item in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("V25B Step 2B Facts must be unique")
        obligations = {item.obligation_id: item for item in self.grouping_obligations}
        if len(obligations) != len(self.grouping_obligations):
            raise ValueError("V25B grouping obligations must be unique")
        bindings = {item.family_id: item for item in self.source_family_bindings}
        if len(bindings) != len(self.source_family_bindings):
            raise ValueError("V25B source-family bindings must be unique")
        bound = [fact_id for item in self.source_family_bindings for fact_id in item.member_fact_ids]
        if len(bound) != len(set(bound)) or not set(bound) <= set(fact_ids):
            raise ValueError("V25B source-family bindings must own unique request-local Facts")
        owned = tuple(fact_id for item in self.grouping_obligations for fact_id in item.member_fact_ids)
        if set(owned) != set(fact_ids) or len(owned) != len(set(owned)):
            raise ValueError("V25B grouping obligations must own every reviewed Fact exactly once")
        payload = v25b_step2b_request_payload(self)
        if self.identity.payload_sha256 != canonical_step2_sha256(payload):
            raise ValueError("V25B request payload no longer matches its cache identity")
        return self


class V25BGroupingGate(StrictModel):
    request_id: str = Field(pattern=r"^step2b-request:[0-9a-f]{64}$")
    valid: bool
    failure_codes: tuple[str, ...] = ()
    exact_obligation_count: int = Field(ge=0)
    missing_obligation_count: int = Field(ge=0)
    split_obligation_count: int = Field(ge=0)
    merged_obligation_count: int = Field(ge=0)
    wrong_presentation_count: int = Field(ge=0)
    extra_card_count: int = Field(ge=0)
    exactly_once: bool
    basis: str = Field(min_length=24, max_length=1600)


class V25BBlockedPreflight(StrictModel):
    """Explicit fail-closed state when five-sample Golden Facts are still draft."""

    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_fact_count: int = Field(ge=0)
    draft_learning_review_count: int = Field(ge=1)
    state: str = "blocked_unreviewed_learning_target"
    failure_codes: tuple[str, ...] = ("unreviewed_learning_target",)
    network_calls: int = 0

    @model_validator(mode="after")
    def validate_block(self) -> "V25BBlockedPreflight":
        if self.network_calls != 0 or self.state != "blocked_unreviewed_learning_target":
            raise ValueError("V25B blocked preflight must be an offline unreviewed-target stop")
        return self
