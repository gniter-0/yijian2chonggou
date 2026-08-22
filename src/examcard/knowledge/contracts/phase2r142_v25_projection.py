"""Offline V25 response-to-Golden projection and coverage-layer reconciliation."""
from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Mapping

from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from ..learning_fixture import LearningReviewState
from .phase2r14_v25_live import V25LiveSampleContext
from ..schema import FactType
from . import (
    Step2ACoverageObligationKind,
    canonical_step2_sha256,
    normalized_compare_text,
)
from ..quality import QualityStatus, TitleQualityResult, assess_title_quality
from .v25_generic_contracts import (
    V25BSourceFamilyBinding,
    V25FactReviewState,
    V25Step2AContractProfile,
    V25Step2AGate,
    V25Step2AResultPayload,
    V25Step2BFactSummary,
    assess_v25_step2a_response,
    assess_v25b_step2b_response,
    build_v25b_source_fake_response,
    build_v25b_step2b_request,
)


PHASE: Final = "2R.1.4.2"
PROJECTION_VERSION = "v25_response_golden_projection_v1"
SCHEMA_VERSION: Final = "phase2r142_projection_schema_v1"
FROZEN_PHASE2R141_STORAGE = Path("data/semantic_llm_v2/gonglu_phase2r141_v25b_recovery_live_run1")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OFFLINE_RELAY = "https://offline.invalid/phase2r142"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _stable_id(prefix: str, payload: object) -> str:
    return f"{prefix}:{canonical_step2_sha256(payload)[:24]}"


def freeze_phase2r141_run1(*, repository_root: Path) -> dict[str, str]:
    """Hash every persisted probe/request/record/cache/journal/report artifact."""

    root = Path(repository_root).resolve()
    frozen_root = root / FROZEN_PHASE2R141_STORAGE
    if not frozen_root.is_dir():
        raise ValueError(f"Phase {PHASE} requires the frozen Phase 2R.1.4.1 run")
    paths = tuple(sorted(path for path in frozen_root.rglob("*") if path.is_file()))
    relative = {path.relative_to(root).as_posix(): _sha256_bytes(path.read_bytes()) for path in paths}
    required_fragments = (
        "/probes/",
        "/requests/",
        "/records/",
        "/cache/",
        "/telemetry/journal.jsonl",
        "/phase2r141_v25b_recovery_live_report.json",
    )
    for fragment in required_fragments:
        if not any(fragment in f"/{name}" for name in relative):
            raise ValueError(f"Phase {PHASE} frozen run is missing {fragment}")
    return relative


class V25ProjectionDisposition(StrEnum):
    PROJECTED = "projected"
    OUT_OF_SCOPE = "out_of_scope"
    DUPLICATE_SUBFACT = "duplicate_subfact"
    MANUAL_REVIEW = "manual_review"


class V25GoldenTargetState(StrEnum):
    PROJECTED = "projected"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    FAIL_CLOSED = "fail_closed"


class V25TargetCandidateAudit(StrictModel):
    proposal_index: int = Field(ge=0)
    selected_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    selected_parent_evidence_ids: tuple[str, ...] = Field(min_length=1)
    selected_source_table_ids: tuple[str, ...] = ()
    exact_source_match: bool
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_candidate(self) -> "V25TargetCandidateAudit":
        if self.exact_source_match != (not self.failure_codes):
            raise ValueError("target candidate exact flag must match its failure codes")
        return self


class V25GoldenTargetAudit(StrictModel):
    fact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    expected_fact_type: FactType
    canonical_title: str = Field(min_length=2, max_length=96)
    reviewed_primary_anchor_span_ids: tuple[str, ...] = ()
    reviewed_primary_evidence_ids: tuple[str, ...] = Field(min_length=1)
    reviewed_source_table_ids: tuple[str, ...] = ()
    required_assertion_term_sha256s: tuple[str, ...] = ()
    state: V25GoldenTargetState
    semantic_diagram_projection: bool = False
    candidate_proposal_indexes: tuple[int, ...] = ()
    candidate_audits: tuple[V25TargetCandidateAudit, ...] = ()
    projection_id: str | None = Field(default=None, pattern=r"^v25-canonical-projection:[0-9a-f]{24}$")
    failure_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_state(self) -> "V25GoldenTargetAudit":
        if len(self.candidate_audits) != len({item.proposal_index for item in self.candidate_audits}):
            raise ValueError("target candidate audits must be unique per proposal")
        if self.state == V25GoldenTargetState.PROJECTED:
            candidate_valid = (
                not self.semantic_diagram_projection and len(self.candidate_proposal_indexes) == 1
            ) or (
                self.semantic_diagram_projection and not self.candidate_proposal_indexes
            )
            if not candidate_valid or self.projection_id is None or self.failure_codes:
                raise ValueError("projected V25 target requires one exact proposal and no failures")
        elif self.projection_id is not None or not self.failure_codes:
            raise ValueError("non-projected V25 target must fail closed without a projection ID")
        return self


class V25ProposalDispositionAudit(StrictModel):
    proposal_index: int = Field(ge=0)
    proposal_sha256: str = Field(pattern=_SHA256_PATTERN)
    model_label: str = Field(min_length=2, max_length=96)
    model_label_sha256: str = Field(pattern=_SHA256_PATTERN)
    fact_type: FactType
    primary_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    primary_evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_table_ids: tuple[str, ...] = ()
    source_manifest_sha256s: tuple[str, ...] = Field(min_length=1)
    coverage_obligation_ids: tuple[str, ...] = ()
    disposition: V25ProjectionDisposition
    fact_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    canonical_title: str | None = Field(default=None, min_length=2, max_length=96)
    projection_id: str | None = Field(default=None, pattern=r"^v25-canonical-projection:[0-9a-f]{24}$")
    basis_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_disposition(self) -> "V25ProposalDispositionAudit":
        projected = self.disposition == V25ProjectionDisposition.PROJECTED
        if projected != bool(self.fact_key and self.canonical_title and self.projection_id):
            raise ValueError("only projected proposal audits may carry Golden identity")
        if self.model_label_sha256 != _sha256_text(self.model_label):
            raise ValueError("proposal audit model-label hash mismatch")
        return self


class V25CanonicalTitleProjection(StrictModel):
    projection_id: str = Field(pattern=r"^v25-canonical-projection:[0-9a-f]{24}$")
    projection_version: str = PROJECTION_VERSION
    fact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    canonical_title: str = Field(min_length=2, max_length=96)
    proposal_index: int | None = Field(default=None, ge=0)
    proposal_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    model_label: str | None = Field(default=None, min_length=2, max_length=96)
    model_label_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    request_id: str = Field(pattern=r"^step2a-request:[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    coverage_obligation_ids: tuple[str, ...] = ()
    complete_table_grid_verified: bool
    continuation_binding_ids: tuple[str, ...] = ()
    semantic_diagram_bridge_id: str | None = Field(
        default=None, pattern=r"^v25-semantic-diagram-bridge:[0-9a-f]{24}$"
    )
    fact_summary: V25Step2BFactSummary
    basis: str = Field(min_length=32, max_length=1600)

    @model_validator(mode="after")
    def validate_projection(self) -> "V25CanonicalTitleProjection":
        if self.fact_summary.canonical_title != self.canonical_title:
            raise ValueError("canonical projection title must match its Step 2B FactSummary")
        if self.fact_summary.review_state != V25FactReviewState.REVIEWED:
            raise ValueError("canonical projection must originate from a reviewed target")
        if self.model_label is None:
            if self.proposal_index is not None or self.proposal_sha256 is not None or self.model_label_sha256 is not None:
                raise ValueError("semantic-diagram projection cannot carry model proposal fields")
            if self.semantic_diagram_bridge_id is None:
                raise ValueError("proposal-free projection requires a reviewed semantic diagram bridge")
        elif (
            self.proposal_index is None
            or self.proposal_sha256 is None
            or self.model_label_sha256 != _sha256_text(self.model_label)
            or self.semantic_diagram_bridge_id is not None
        ):
            raise ValueError("text/table projection requires complete model-label audit and no diagram bridge")
        payload = self.model_dump(mode="json", exclude={"projection_id"})
        if self.projection_id != _stable_id("v25-canonical-projection", payload):
            raise ValueError("canonical projection ID must be deterministic")
        return self


class V25TeachingTitleSeparationAudit(StrictModel):
    model_label_results: tuple[TitleQualityResult, ...]
    canonical_title_results: tuple[TitleQualityResult, ...]
    model_label_failure_count: int = Field(ge=0)
    canonical_title_failure_count: int = Field(ge=0)
    canonical_title_manual_review_count: int = Field(ge=0)
    card_facing_title_gate_valid: bool
    basis: str = Field(min_length=32, max_length=1600)


class V25ProjectedStep2BBridgeAudit(StrictModel):
    eligible: bool
    expected_fact_count: int = Field(ge=0)
    projected_fact_count: int = Field(ge=0)
    missing_fact_keys: tuple[str, ...] = ()
    extra_fact_keys: tuple[str, ...] = ()
    request_id: str | None = Field(default=None, pattern=r"^step2b-request:[0-9a-f]{64}$")
    exactly_once: bool
    source_grouping_valid: bool
    card_golden_grouping_valid: bool
    basis: str = Field(min_length=32, max_length=1600)


class V25ResponseGoldenProjectionBundle(StrictModel):
    schema_version: str = SCHEMA_VERSION
    phase: str = PHASE
    projection_version: str = PROJECTION_VERSION
    sample_id: str
    request_id: str = Field(pattern=r"^step2a-request:[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    fixture_id: str
    fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    frozen_phase2r141_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_gate: V25Step2AGate
    continuation_binding_valid: bool
    target_audits: tuple[V25GoldenTargetAudit, ...]
    proposal_audits: tuple[V25ProposalDispositionAudit, ...]
    projections: tuple[V25CanonicalTitleProjection, ...]
    teaching_title_audit: V25TeachingTitleSeparationAudit
    step2b_bridge_audit: V25ProjectedStep2BBridgeAudit
    response_remains_draft: bool = True
    valid: bool

    @model_validator(mode="after")
    def validate_bundle(self) -> "V25ResponseGoldenProjectionBundle":
        if len(self.proposal_audits) != len({item.proposal_index for item in self.proposal_audits}):
            raise ValueError("V25 proposal audits must be exactly-once")
        if len(self.projections) != len({item.fact_key for item in self.projections}):
            raise ValueError("V25 canonical projections must be unique per reviewed target")
        projected_ids = {item.projection_id for item in self.projections}
        audit_ids = {
            item.projection_id
            for item in self.proposal_audits
            if item.disposition == V25ProjectionDisposition.PROJECTED
        }
        diagram_ids = {
            item.projection_id
            for item in self.projections
            if item.semantic_diagram_bridge_id is not None
        }
        if projected_ids != audit_ids | diagram_ids:
            raise ValueError("projection bundle proposal and diagram audit ownership mismatch")
        expected_valid = (
            self.source_gate.valid
            and self.continuation_binding_valid
            and all(item.state == V25GoldenTargetState.PROJECTED for item in self.target_audits)
            and self.teaching_title_audit.card_facing_title_gate_valid
            and self.step2b_bridge_audit.eligible
        )
        if self.valid != expected_valid:
            raise ValueError("projection bundle valid flag must match every fail-closed layer")
        return self


def _span_and_table_maps(context: V25LiveSampleContext):
    spans = {}
    table_by_span: dict[str, str] = {}
    tables = {}
    for section in context.step2a_request.sections:
        for span in section.anchor_spans:
            spans[span.anchor_span_id] = span
        for table in section.tables:
            tables[table.table_evidence_id] = table
            for row in table.rows:
                for cell in row.cells:
                    for span in cell.anchor_spans:
                        table_by_span[span.anchor_span_id] = table.table_evidence_id
    return spans, table_by_span, tables


def _target_audit_fields(context: V25LiveSampleContext, target) -> dict[str, object]:
    summary = context.facts_by_key[target.fact_key]
    return {
        "reviewed_primary_anchor_span_ids": summary.primary_anchor_span_ids,
        "reviewed_primary_evidence_ids": summary.primary_evidence_ids,
        "reviewed_source_table_ids": summary.source_table_ids,
        "required_assertion_term_sha256s": tuple(_sha256_text(item) for item in target.required_terms),
    }


def _proposal_obligations(context: V25LiveSampleContext, proposal) -> tuple[Any, ...]:
    selected = frozenset(proposal.primary_anchor_span_ids)
    structural = tuple(
        obligation
        for obligation in context.step2a_request.coverage_obligations
        if obligation.expected_fact_type == proposal.fact_type
        and frozenset(obligation.required_primary_anchor_span_ids) == selected
    )
    atomic = tuple(
        obligation
        for obligation in context.step2a_request.atomic_source_obligations
        if obligation.expected_fact_type == proposal.fact_type
        and frozenset(obligation.required_primary_anchor_span_ids) == selected
    )
    text = tuple(
        obligation
        for obligation in getattr(context.step2a_request, "text_source_obligations", ())
        if obligation.expected_fact_type == proposal.fact_type
        and frozenset(obligation.required_primary_anchor_span_ids) == selected
    )
    source_lists = tuple(
        obligation
        for obligation in getattr(context.step2a_request, "list_source_obligations", ())
        if obligation.expected_fact_type == proposal.fact_type
        and frozenset(obligation.required_primary_anchor_span_ids) == selected
    )
    return (*structural, *atomic, *text, *source_lists)


def _continuation_binding_ids(context: V25LiveSampleContext, selected: set[str]) -> tuple[tuple[str, ...], bool]:
    bound: list[str] = []
    valid = True
    for binding in context.step2a_request.reviewed_continuation_bindings:
        binding_anchors = set(binding.anchor_span_ids)
        if selected & binding_anchors:
            bound.append(binding.binding_id)
            valid = valid and binding_anchors <= selected
    return tuple(bound), valid


def assess_request_continuation_bindings(
    context: V25LiveSampleContext, *, required_binding_ids: tuple[str, ...] | None = None
) -> bool:
    selectable = {
        span.anchor_span_id
        for section in context.step2a_request.sections
        for span in section.anchor_spans
    }
    manifests = set(context.step2a_request.source_manifest_sha256s)
    actual_binding_ids = tuple(binding.binding_id for binding in context.step2a_request.reviewed_continuation_bindings)
    expected_binding_ids = actual_binding_ids if required_binding_ids is None else required_binding_ids
    return actual_binding_ids == expected_binding_ids and all(
        set(binding.anchor_span_ids) <= selectable
        and set(binding.source_manifest_sha256s) <= manifests
        and tuple(right - left for left, right in zip(binding.page_nums, binding.page_nums[1:]))
        == (1,) * (len(binding.page_nums) - 1)
        for binding in context.step2a_request.reviewed_continuation_bindings
    )


def _table_target_match(*, target, expected_summary, proposal, obligations, spans, table_by_span, tables):
    failures: list[str] = []
    selected = set(proposal.primary_anchor_span_ids)
    source_tables = tuple(dict.fromkeys(table_by_span[item] for item in proposal.primary_anchor_span_ids if item in table_by_span))
    if target.table_structure is None:
        if selected != set(expected_summary.primary_anchor_span_ids):
            failures.append("anchor_set_mismatch")
        if set(expected_summary.primary_evidence_ids) != {spans[item].parent_evidence_id for item in selected if item in spans}:
            failures.append("parent_evidence_mismatch")
        return failures, source_tables, False
    complete = [
        item
        for item in obligations
        if getattr(item, "kind", None) == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION
        and item.table_evidence_id in expected_summary.source_table_ids
    ]
    if len(complete) != 1:
        failures.append("complete_table_obligation_mismatch")
        return failures, source_tables, False
    table = tables.get(complete[0].table_evidence_id)
    if table is None:
        failures.append("source_table_missing")
        return failures, source_tables, False
    full_grid = {
        span.anchor_span_id
        for row in table.rows
        for cell in row.cells
        for span in cell.anchor_spans
    }
    if selected != full_grid:
        failures.append("complete_grid_anchor_mismatch")
    expectation = target.table_structure
    if table.row_count < expectation.min_rows or table.column_count < expectation.min_columns:
        failures.append("table_geometry_mismatch")
    table_text = normalized_compare_text("\n".join(cell.canonical_text for row in table.rows for cell in row.cells))
    if any(normalized_compare_text(value) not in table_text for value in expectation.required_cell_texts):
        failures.append("required_table_cell_missing")
    if not set(expected_summary.primary_anchor_span_ids) <= selected:
        failures.append("reviewed_table_anchor_missing")
    if set(expected_summary.source_table_ids) != {table.table_evidence_id}:
        failures.append("reviewed_table_provenance_mismatch")
    return failures, source_tables, not failures


def _match_target(*, context, target, proposal, spans, table_by_span, tables):
    expected_summary = context.facts_by_key[target.fact_key]
    obligations = _proposal_obligations(context, proposal)
    failures: list[str] = []
    if not obligations:
        failures.append("coverage_obligation_missing")
    if proposal.fact_type != target.fact_type:
        failures.append("fact_type_mismatch")
    selected = set(proposal.primary_anchor_span_ids)
    if any(anchor_id not in spans for anchor_id in selected):
        failures.append("unknown_anchor")
    table_failures, source_tables, complete_grid = _table_target_match(
        target=target,
        expected_summary=expected_summary,
        proposal=proposal,
        obligations=obligations,
        spans=spans,
        table_by_span=table_by_span,
        tables=tables,
    )
    failures.extend(table_failures)
    grounded = normalized_compare_text(
        "\n".join((proposal.assertion, *(spans[item].canonical_text for item in proposal.primary_anchor_span_ids if item in spans)))
    )
    if any(normalized_compare_text(term) not in grounded for term in target.required_terms):
        failures.append("required_assertion_terms_missing")
    binding_ids, continuation_valid = _continuation_binding_ids(context, selected)
    if not continuation_valid:
        failures.append("continuation_binding_incomplete")
    expected_anchors = set(expected_summary.primary_anchor_span_ids)
    overlap = bool(selected & expected_anchors) or any(
        normalized_compare_text(term) in grounded for term in target.required_terms
    )
    return tuple(sorted(set(failures))), source_tables, complete_grid, binding_ids, overlap


def _projection_from_proposal(
    *, context, target, proposal, proposal_index, response_sha256, obligations, source_tables, complete_grid, binding_ids, spans
):
    selected = proposal.primary_anchor_span_ids
    evidence_ids = tuple(dict.fromkeys(spans[item].parent_evidence_id for item in selected))
    # 待定项（审计遗留）：算出的清单指纹并未喂进下方 _stable_id，而紧邻的
    # evidence_ids 是喂了的，疑似抽包时漏接一个溯源成分。补接会改变
    # topic_key/fact_id 的派生值并牵动全部金标，须由负责人裁定后连同金标一并改，
    # 故此处保留计算并显式标注，不做静默删除。
    manifests = tuple(dict.fromkeys(spans[item].source_manifest_sha256 for item in selected))  # noqa: F841
    proposal_sha256 = canonical_step2_sha256(proposal.model_dump(mode="json"))
    topic_key = _stable_id(
        "source-topic",
        {"sample_id": context.sample_id, "evidence_ids": evidence_ids, "anchor_ids": selected, "tables": source_tables},
    )
    fact_id = _stable_id(
        "fact",
        {
            "request_id": context.step2a_request.identity.request_id,
            "response_sha256": response_sha256,
            "fact_key": target.fact_key,
            "proposal_sha256": proposal_sha256,
        },
    )
    fact_summary = V25Step2BFactSummary(
        fact_id=fact_id,
        fact_type=target.fact_type,
        canonical_title=target.title,
        assertion_summary=proposal.assertion,
        primary_evidence_ids=evidence_ids,
        primary_anchor_span_ids=selected,
        source_table_ids=source_tables,
        source_topic_key=topic_key,
        review_state=V25FactReviewState.REVIEWED,
    )
    payload = {
        "projection_version": PROJECTION_VERSION,
        "fact_key": target.fact_key,
        "canonical_title": target.title,
        "proposal_index": proposal_index,
        "proposal_sha256": proposal_sha256,
        "model_label": proposal.label,
        "model_label_sha256": _sha256_text(proposal.label),
        "request_id": context.step2a_request.identity.request_id,
        "response_sha256": response_sha256,
        "coverage_obligation_ids": tuple(item.obligation_id for item in obligations),
        "complete_table_grid_verified": complete_grid,
        "continuation_binding_ids": binding_ids,
        "semantic_diagram_bridge_id": None,
        "fact_summary": fact_summary.model_dump(mode="json"),
        "basis": "Reviewed canonical title projected only after exact Fact type, required assertion terms, selected anchor, parent Evidence, table provenance, and continuation checks; model label remains audit-only.",
    }
    return V25CanonicalTitleProjection(
        projection_id=_stable_id("v25-canonical-projection", payload),
        **payload,
    )


def _projection_from_diagram(*, context, target, response_sha256):
    summary = context.facts_by_key[target.fact_key]
    bridge = summary.semantic_diagram_bridge
    if bridge is None:
        return None
    payload = {
        "projection_version": PROJECTION_VERSION,
        "fact_key": target.fact_key,
        "canonical_title": target.title,
        "proposal_index": None,
        "proposal_sha256": None,
        "model_label": None,
        "model_label_sha256": None,
        "request_id": context.step2a_request.identity.request_id,
        "response_sha256": response_sha256,
        "coverage_obligation_ids": (),
        "complete_table_grid_verified": False,
        "continuation_binding_ids": (),
        "semantic_diagram_bridge_id": bridge.bridge_id,
        "fact_summary": summary.model_dump(mode="json"),
        "basis": "Reviewed semantic_v2 sidecar supplies the diagram FactSummary without inventing a text anchor or consuming an LLM diagram proposal.",
    }
    return V25CanonicalTitleProjection(
        projection_id=_stable_id("v25-canonical-projection", payload),
        **payload,
    )


def _rebind_source_families(context: V25LiveSampleContext, facts_by_key: Mapping[str, V25Step2BFactSummary]):
    old_id_to_key = {fact.fact_id: key for key, fact in context.facts_by_key.items()}
    bindings = []
    for old in context.step2b_request.source_family_bindings:
        keys = tuple(old_id_to_key[item] for item in old.member_fact_ids)
        payload = {
            "family_kind": old.family_kind.value,
            "member_fact_ids": tuple(facts_by_key[key].fact_id for key in keys),
            "source_evidence_ids": old.source_evidence_ids,
            "source_anchor_span_ids": old.source_anchor_span_ids,
            "source_manifest_sha256s": old.source_manifest_sha256s,
            "basis": old.basis,
        }
        bindings.append(
            V25BSourceFamilyBinding(
                family_id=_stable_id("v25b-source-family", payload),
                **payload,
            )
        )
    return tuple(bindings)


def assess_projected_step2b_bridge(
    *, context: V25LiveSampleContext, facts_by_key: Mapping[str, V25Step2BFactSummary]
) -> V25ProjectedStep2BBridgeAudit:
    expected = tuple(item.fact_key for item in context.fixture_sample.facts)
    missing = tuple(key for key in expected if key not in facts_by_key)
    extra = tuple(sorted(set(facts_by_key) - set(expected)))
    if missing or extra:
        return V25ProjectedStep2BBridgeAudit(
            eligible=False,
            expected_fact_count=len(expected),
            projected_fact_count=len(facts_by_key),
            missing_fact_keys=missing,
            extra_fact_keys=extra,
            exactly_once=False,
            source_grouping_valid=False,
            card_golden_grouping_valid=False,
            basis="Step 2B remains blocked until every reviewed target has exactly one projected FactSummary and no out-of-scope Fact is present.",
        )
    ordered = tuple(facts_by_key[key] for key in expected)
    bindings = _rebind_source_families(context, facts_by_key)
    request = build_v25b_step2b_request(
        sample_id=context.sample_id,
        material_profile_id=context.step2b_request.material_profile_id,
        facts=ordered,
        source_family_bindings=bindings,
        model="deterministic-fake-llm",
        relay_base_url=_OFFLINE_RELAY,
    )
    response = build_v25b_source_fake_response(request=request)
    gate = assess_v25b_step2b_response(request=request, response=response)
    expected_groups = {
        (tuple(facts_by_key[key].fact_id for key in card.primary_fact_keys), card.presentation.value)
        for card in context.fixture_sample.cards
    }
    observed_groups = {(tuple(card.primary_fact_ids), card.presentation.value) for card in response.proposals}
    card_valid = expected_groups == observed_groups
    return V25ProjectedStep2BBridgeAudit(
        eligible=gate.valid and gate.exactly_once and card_valid,
        expected_fact_count=len(expected),
        projected_fact_count=len(facts_by_key),
        request_id=request.identity.request_id,
        exactly_once=gate.exactly_once,
        source_grouping_valid=gate.valid,
        card_golden_grouping_valid=card_valid,
        basis="Offline V25B request contains only exact projected FactSummaries; source-derived grouping and reviewed Card comparison both require exactly-once ownership.",
    )


def build_v25_response_golden_projection(
    *,
    context: V25LiveSampleContext,
    response: V25Step2AResultPayload,
    response_sha256: str,
    fixture_sha256: str,
    frozen_phase2r141_manifest_sha256: str,
    required_continuation_binding_ids: tuple[str, ...] | None = None,
    fixture_id: str = "gonglu_phase2r12_v25_reviewed",
) -> V25ResponseGoldenProjectionBundle:
    """Project exact reviewed titles without modifying the persisted response."""

    spans, table_by_span, tables = _span_and_table_maps(context)
    source_gate = assess_v25_step2a_response(request=context.step2a_request, response=response)
    continuation_valid = assess_request_continuation_bindings(
        context, required_binding_ids=required_continuation_binding_ids
    )
    target_audits: list[V25GoldenTargetAudit] = []
    projections: list[V25CanonicalTitleProjection] = []
    exact_owner: dict[int, V25CanonicalTitleProjection] = {}
    overlap_failures: dict[int, set[str]] = {index: set() for index in range(len(response.proposals))}
    for target in context.fixture_sample.facts:
        target_fields = _target_audit_fields(context, target)
        if target.review.state != LearningReviewState.REVIEWED:
            target_audits.append(
                V25GoldenTargetAudit(
                    fact_key=target.fact_key,
                    expected_fact_type=target.fact_type,
                    canonical_title=target.title,
                    **target_fields,
                    state=V25GoldenTargetState.FAIL_CLOSED,
                    failure_codes=("target_not_reviewed",),
                )
            )
            continue
        if target.fact_type == FactType.DIAGRAM_RELATION:
            projection = _projection_from_diagram(context=context, target=target, response_sha256=response_sha256)
            if projection is None:
                target_audits.append(
                    V25GoldenTargetAudit(
                        fact_key=target.fact_key,
                        expected_fact_type=target.fact_type,
                        canonical_title=target.title,
                        **target_fields,
                        state=V25GoldenTargetState.FAIL_CLOSED,
                        failure_codes=("semantic_v2_bridge_missing",),
                    )
                )
            else:
                projections.append(projection)
                target_audits.append(
                    V25GoldenTargetAudit(
                        fact_key=target.fact_key,
                        expected_fact_type=target.fact_type,
                        canonical_title=target.title,
                        **target_fields,
                        state=V25GoldenTargetState.PROJECTED,
                    candidate_proposal_indexes=(),
                    semantic_diagram_projection=True,
                    projection_id=projection.projection_id,
                    )
                )
            continue
        candidates = []
        candidate_details = {}
        candidate_audits: list[V25TargetCandidateAudit] = []
        for index, proposal in enumerate(response.proposals):
            failures, source_tables, complete_grid, binding_ids, overlap = _match_target(
                context=context,
                target=target,
                proposal=proposal,
                spans=spans,
                table_by_span=table_by_span,
                tables=tables,
            )
            if overlap:
                overlap_failures[index].update(f"{target.fact_key}:{item}" for item in failures or ("candidate",))
                candidate_audits.append(
                    V25TargetCandidateAudit(
                        proposal_index=index,
                        selected_anchor_span_ids=proposal.primary_anchor_span_ids,
                        selected_parent_evidence_ids=tuple(
                            dict.fromkeys(spans[item].parent_evidence_id for item in proposal.primary_anchor_span_ids)
                        ),
                        selected_source_table_ids=source_tables,
                        exact_source_match=not failures,
                        failure_codes=failures,
                    )
                )
            if not failures:
                candidates.append(index)
                candidate_details[index] = (source_tables, complete_grid, binding_ids)
        if len(candidates) == 1 and candidates[0] not in exact_owner:
            index = candidates[0]
            proposal = response.proposals[index]
            obligations = _proposal_obligations(context, proposal)
            source_tables, complete_grid, binding_ids = candidate_details[index]
            projection = _projection_from_proposal(
                context=context,
                target=target,
                proposal=proposal,
                proposal_index=index,
                response_sha256=response_sha256,
                obligations=obligations,
                source_tables=source_tables,
                complete_grid=complete_grid,
                binding_ids=binding_ids,
                spans=spans,
            )
            projections.append(projection)
            exact_owner[index] = projection
            target_audits.append(
                V25GoldenTargetAudit(
                    fact_key=target.fact_key,
                    expected_fact_type=target.fact_type,
                    canonical_title=target.title,
                    **target_fields,
                    state=V25GoldenTargetState.PROJECTED,
                    candidate_proposal_indexes=(index,),
                    candidate_audits=tuple(candidate_audits),
                    projection_id=projection.projection_id,
                )
            )
        elif candidates:
            target_audits.append(
                V25GoldenTargetAudit(
                    fact_key=target.fact_key,
                    expected_fact_type=target.fact_type,
                    canonical_title=target.title,
                    **target_fields,
                    state=V25GoldenTargetState.AMBIGUOUS,
                    candidate_proposal_indexes=tuple(candidates),
                    candidate_audits=tuple(candidate_audits),
                    failure_codes=("multiple_exact_candidates",),
                )
            )
        else:
            target_audits.append(
                V25GoldenTargetAudit(
                    fact_key=target.fact_key,
                    expected_fact_type=target.fact_type,
                    canonical_title=target.title,
                    **target_fields,
                    state=V25GoldenTargetState.MISSING,
                    candidate_audits=tuple(candidate_audits),
                    failure_codes=("no_exact_source_bound_candidate",),
                )
            )

    projected_table_ids = {
        table_id
        for projection in projections
        if projection.fact_summary.fact_type == FactType.COMPOSITION
        for table_id in projection.fact_summary.source_table_ids
    }
    reviewed_atomic_anchor_ids = {
        anchor_id
        for target in context.fixture_sample.facts
        if target.fact_type not in {FactType.COMPOSITION, FactType.DIAGRAM_RELATION}
        for anchor_id in context.facts_by_key[target.fact_key].primary_anchor_span_ids
    }
    proposal_audits = []
    for index, proposal in enumerate(response.proposals):
        selected = proposal.primary_anchor_span_ids
        evidence_ids = tuple(dict.fromkeys(spans[item].parent_evidence_id for item in selected))
        manifests = tuple(dict.fromkeys(spans[item].source_manifest_sha256 for item in selected))
        source_tables = tuple(dict.fromkeys(table_by_span[item] for item in selected if item in table_by_span))
        obligations = _proposal_obligations(context, proposal)
        owner = exact_owner.get(index)
        if owner is not None:
            disposition = V25ProjectionDisposition.PROJECTED
            # 显式注解：首次赋值是单元素字面量，不注解会被推成定长 tuple[str]，
            # 下面分支再赋变长 tuple 就冲突。
            basis_codes: tuple[str, ...] = ("exact_reviewed_target_projection",)
        elif set(selected) & reviewed_atomic_anchor_ids:
            disposition = V25ProjectionDisposition.MANUAL_REVIEW
            basis_codes = tuple(sorted(overlap_failures[index])) or ("reviewed_atomic_anchor_not_projected",)
        elif set(source_tables) & projected_table_ids and proposal.fact_type != FactType.COMPOSITION:
            disposition = V25ProjectionDisposition.DUPLICATE_SUBFACT
            basis_codes = ("source_table_atomic_subfact_outside_reviewed_target",)
        else:
            disposition = V25ProjectionDisposition.OUT_OF_SCOPE
            basis_codes = ("source_grounded_obligation_outside_reviewed_learning_target",)
        proposal_audits.append(
            V25ProposalDispositionAudit(
                proposal_index=index,
                proposal_sha256=canonical_step2_sha256(proposal.model_dump(mode="json")),
                model_label=proposal.label,
                model_label_sha256=_sha256_text(proposal.label),
                fact_type=proposal.fact_type,
                primary_anchor_span_ids=selected,
                primary_evidence_ids=evidence_ids,
                source_table_ids=source_tables,
                source_manifest_sha256s=manifests,
                coverage_obligation_ids=tuple(item.obligation_id for item in obligations),
                disposition=disposition,
                fact_key=owner.fact_key if owner else None,
                canonical_title=owner.canonical_title if owner else None,
                projection_id=owner.projection_id if owner else None,
                basis_codes=basis_codes,
            )
        )

    model_titles = tuple(
        assess_title_quality(
            title=proposal.label,
            evidence_texts=(spans[item].canonical_text for item in proposal.primary_anchor_span_ids),
        )
        for proposal in response.proposals
    )

    def canonical_evidence_texts(projection: V25CanonicalTitleProjection) -> tuple[str, ...]:
        fact = projection.fact_summary
        values = [
            spans[item].canonical_text
            for item in fact.primary_anchor_span_ids
            if item in spans
        ]
        values.extend(
            cell.canonical_text
            for table_id in fact.source_table_ids
            if table_id in tables
            for row in tables[table_id].rows
            for cell in row.cells
        )
        if fact.semantic_diagram_bridge is not None:
            values.append(fact.semantic_diagram_bridge.semantic_title)
            values.extend(node.label for node in fact.semantic_diagram_bridge.nodes)
            values.extend(edge.relation for edge in fact.semantic_diagram_bridge.edges)
        if context.step2a_request.contract_profile == V25Step2AContractProfile.STANDARD_CANONICAL_PRECHECK:
            selected = set(fact.primary_anchor_span_ids)
            source_sections = [
                section
                for section in context.step2a_request.sections
                if selected & {span.anchor_span_id for span in section.anchor_spans}
            ]
            # This is title-quality context only.  It neither changes the selected
            # Fact anchors nor becomes a source of model-selected spans.
            values.extend(
                evidence.canonical_text
                for section in source_sections
                for evidence in section.text_evidence
            )
        if not values:
            values.append(fact.assertion_summary)
        return tuple(values)

    canonical_titles = tuple(
        assess_title_quality(
            title=projection.canonical_title,
            evidence_texts=canonical_evidence_texts(projection),
        )
        for projection in projections
    )
    canonical_failures = sum(item.status == QualityStatus.FAIL for item in canonical_titles)
    canonical_manual = sum(item.status == QualityStatus.MANUAL_REVIEW for item in canonical_titles)
    teaching = V25TeachingTitleSeparationAudit(
        model_label_results=model_titles,
        canonical_title_results=canonical_titles,
        model_label_failure_count=sum(item.status == QualityStatus.FAIL for item in model_titles),
        canonical_title_failure_count=canonical_failures,
        canonical_title_manual_review_count=canonical_manual,
        card_facing_title_gate_valid=not canonical_failures and not canonical_manual,
        basis="Model labels remain forensic diagnostics and cannot become Card titles. Only exact reviewed projections are evaluated as Card-facing canonical titles.",
    )
    projected_by_key = {item.fact_key: item.fact_summary for item in projections}
    bridge = assess_projected_step2b_bridge(context=context, facts_by_key=projected_by_key)
    return V25ResponseGoldenProjectionBundle(
        sample_id=context.sample_id,
        request_id=context.step2a_request.identity.request_id,
        response_sha256=response_sha256,
        fixture_id=fixture_id,
        fixture_sha256=fixture_sha256,
        frozen_phase2r141_manifest_sha256=frozen_phase2r141_manifest_sha256,
        source_gate=source_gate,
        continuation_binding_valid=continuation_valid,
        target_audits=tuple(target_audits),
        proposal_audits=tuple(proposal_audits),
        projections=tuple(projections),
        teaching_title_audit=teaching,
        step2b_bridge_audit=bridge,
        valid=(
            source_gate.valid
            and continuation_valid
            and all(item.state == V25GoldenTargetState.PROJECTED for item in target_audits)
            and teaching.card_facing_title_gate_valid
            and bridge.eligible
        ),
    )
