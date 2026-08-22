"""Reviewable teaching targets for the Phase 2R semantic-card pilot.

The fixture deliberately references stable Evidence text selectors instead of
editing Evidence IDs or canonical text. A target may stay ``draft`` while the
underlying Phase 0 Evidence remains reviewed and immutable.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from ..evidence.golden import GoldenSampleRegistry
from ..evidence.layout import normalized_compare_text
from ..evidence.schema import EvidenceKind, EvidenceUnit, PageEvidence
from ..materials.schema import StrictModel, V2_SCHEMA_VERSION

from .diagram.sidecar import DiagramReviewEntry, DiagramReviewState, load_diagram_review_sidecar
from .schema import FactType, ShortCardException
from .contracts import (
    CardPresentation,
    FactSummaryInput,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    ReviewedCanonicalTitleProjection,
    REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    STEP2A_REQUEST_VERSION,
    Step2ARequest,
    Step2AResponse,
    Step2BResponse,
    canonical_step2_sha256,
)


class LearningReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class LearningProposalDisposition(StrEnum):
    ACCEPTED_FACT = "accepted_fact"
    PARTIAL_CONTINUATION = "partial_continuation"
    DUPLICATE_SUBFACT = "duplicate_subfact"
    EXCLUDED = "excluded"


class LearningProposalAction(StrEnum):
    MAP_EXPECTED_FACT = "map_expected_fact"
    RETAIN_ATOMIC_SUBFACT = "retain_atomic_subfact"
    MANUAL_REVIEW = "manual_review"
    EXCLUDE = "exclude"


class LearningDispositionAuditState(StrEnum):
    APPLIED = "applied"
    OUT_OF_SCOPE = "out_of_scope"
    NOT_REVIEWED = "not_reviewed"
    REQUEST_MISMATCH = "request_mismatch"
    RESPONSE_HASH_MISMATCH = "response_hash_mismatch"
    PROPOSAL_MISSING = "proposal_missing"
    PROPOSAL_HASH_MISMATCH = "proposal_hash_mismatch"


class LearningReview(StrictModel):
    state: LearningReviewState = LearningReviewState.DRAFT
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    basis: str = "待人工复核"

    @model_validator(mode="after")
    def validate_review(self) -> "LearningReview":
        if self.state == LearningReviewState.REVIEWED:
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed learning targets require reviewer and reviewed_at_utc")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft learning targets cannot contain reviewer metadata")
        return self


class EvidenceAnchorSelector(StrictModel):
    text: str = Field(min_length=1)
    kind: EvidenceKind | None = None


class TableStructureExpectation(StrictModel):
    anchor: EvidenceAnchorSelector
    min_rows: int = Field(ge=1)
    min_columns: int = Field(ge=1)
    required_cell_texts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_table_anchor(self) -> "TableStructureExpectation":
        if self.anchor.kind not in {None, EvidenceKind.TABLE}:
            raise ValueError("table structure anchors must select table evidence")
        return self


class DiagramEdgeExpectation(StrictModel):
    source_label: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    target_label: str = Field(min_length=1)


class DiagramSemanticExpectation(StrictModel):
    review_id: str = Field(pattern=r"^diagram:[0-9a-f]{24}$")
    figure_evidence_id: str
    review_title: str = Field(min_length=2)
    required_nodes: tuple[str, ...] = ()
    required_edges: tuple[DiagramEdgeExpectation, ...] = ()
    review: LearningReview = LearningReview()


class LearningFactTarget(StrictModel):
    fact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    fact_type: FactType
    title: str = Field(min_length=2, max_length=32)
    title_aliases: tuple[str, ...] = ()
    primary_anchors: tuple[EvidenceAnchorSelector, ...]
    required_terms: tuple[str, ...] = ()
    table_structure: TableStructureExpectation | None = None
    allow_heading_only: bool = False
    review: LearningReview = LearningReview()

    @model_validator(mode="after")
    def validate_fact_target(self) -> "LearningFactTarget":
        if not self.primary_anchors:
            raise ValueError("learning fact targets require primary anchors")
        if self.table_structure is not None and self.fact_type != FactType.COMPOSITION:
            raise ValueError("table structure targets must use composition facts")
        normalized_titles = [_teaching_title_key(value) for value in (self.title, *self.title_aliases)]
        if len(normalized_titles) != len(set(normalized_titles)) or any(not value for value in normalized_titles):
            raise ValueError("learning Fact canonical title and aliases must be unique and non-empty")
        return self


class LearningProposalDispositionReview(StrictModel):
    page_num: int = Field(ge=1)
    source_request_id: str = Field(pattern=r"^step2a-request:[0-9a-f]{64}$")
    source_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_key: str = Field(min_length=1)
    proposal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: LearningProposalDisposition
    action: LearningProposalAction
    canonical_fact_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    basis: str = Field(min_length=12)
    review: LearningReview

    @model_validator(mode="after")
    def validate_disposition(self) -> "LearningProposalDispositionReview":
        expected = {
            LearningProposalDisposition.ACCEPTED_FACT: LearningProposalAction.MAP_EXPECTED_FACT,
            LearningProposalDisposition.PARTIAL_CONTINUATION: LearningProposalAction.MANUAL_REVIEW,
            LearningProposalDisposition.DUPLICATE_SUBFACT: LearningProposalAction.RETAIN_ATOMIC_SUBFACT,
            LearningProposalDisposition.EXCLUDED: LearningProposalAction.EXCLUDE,
        }[self.disposition]
        if self.action != expected:
            raise ValueError("learning proposal disposition/action pair is inconsistent")
        requires_fact = self.action in {
            LearningProposalAction.MAP_EXPECTED_FACT,
            LearningProposalAction.RETAIN_ATOMIC_SUBFACT,
        }
        if requires_fact != (self.canonical_fact_key is not None):
            raise ValueError("learning proposal action has an invalid canonical Fact binding")
        return self


class LearningCardTarget(StrictModel):
    card_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=2, max_length=32)
    primary_fact_keys: tuple[str, ...]
    presentation: CardPresentation
    split_reason: str = Field(min_length=6)
    short_card_exception: ShortCardException | None = None
    review: LearningReview = LearningReview()

    @model_validator(mode="after")
    def validate_card_target(self) -> "LearningCardTarget":
        if not self.primary_fact_keys or len(self.primary_fact_keys) != len(set(self.primary_fact_keys)):
            raise ValueError("learning cards require unique primary fact keys")
        return self


class LearningSampleTarget(StrictModel):
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    facts: tuple[LearningFactTarget, ...]
    cards: tuple[LearningCardTarget, ...]
    diagrams: tuple[DiagramSemanticExpectation, ...] = ()
    proposal_dispositions: tuple[LearningProposalDispositionReview, ...] = ()

    @model_validator(mode="after")
    def validate_sample_target(self) -> "LearningSampleTarget":
        fact_keys = [fact.fact_key for fact in self.facts]
        card_keys = [card.card_key for card in self.cards]
        if len(fact_keys) != len(set(fact_keys)) or len(card_keys) != len(set(card_keys)):
            raise ValueError("learning target keys must be unique within a sample")
        known = set(fact_keys)
        referenced = [fact_key for card in self.cards for fact_key in card.primary_fact_keys]
        if set(referenced) != known or len(referenced) != len(set(referenced)):
            raise ValueError("each learning fact target must belong to exactly one primary card")
        disposition_keys = [
            (item.page_num, item.source_request_id, item.proposal_key)
            for item in self.proposal_dispositions
        ]
        if len(disposition_keys) != len(set(disposition_keys)):
            raise ValueError("learning proposal dispositions must be unique per page and response")
        unknown_fact_keys = {
            item.canonical_fact_key
            for item in self.proposal_dispositions
            if item.canonical_fact_key is not None
        } - known
        if unknown_fact_keys:
            raise ValueError("learning proposal dispositions reference unknown canonical Facts")
        return self


class LearningGoldenFixture(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    registry_id: str
    material_profile_id: str
    samples: tuple[LearningSampleTarget, ...]

    @model_validator(mode="after")
    def validate_fixture(self) -> "LearningGoldenFixture":
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)) or len(self.samples) != 5:
            raise ValueError("the Phase 2R pilot fixture requires exactly five unique golden samples")
        return self

    @property
    def ready_for_phase2r1(self) -> bool:
        reviews: list[LearningReview] = []
        for sample in self.samples:
            reviews.extend(fact.review for fact in sample.facts)
            reviews.extend(card.review for card in sample.cards)
            reviews.extend(diagram.review for diagram in sample.diagrams)
            reviews.extend(item.review for item in sample.proposal_dispositions)
        return bool(reviews) and all(item.state == LearningReviewState.REVIEWED for item in reviews)

    @property
    def pending_review_entries(self) -> tuple[str, ...]:
        pending: list[str] = []
        for sample in self.samples:
            pending.extend(
                f"{sample.sample_id}:fact:{fact.fact_key}"
                for fact in sample.facts
                if fact.review.state != LearningReviewState.REVIEWED
            )
            pending.extend(
                f"{sample.sample_id}:card:{card.card_key}"
                for card in sample.cards
                if card.review.state != LearningReviewState.REVIEWED
            )
            pending.extend(
                f"{sample.sample_id}:diagram:{diagram.review_title}"
                for diagram in sample.diagrams
                if diagram.review.state != LearningReviewState.REVIEWED
            )
            pending.extend(
                f"{sample.sample_id}:proposal:{item.page_num}:{item.proposal_key}"
                for item in sample.proposal_dispositions
                if item.review.state != LearningReviewState.REVIEWED
            )
        return tuple(pending)


class LearningFactAlignment(StrictModel):
    fact_key: str
    expected_title: str
    accepted_title_aliases: tuple[str, ...] = ()
    expected_type: FactType
    match_state: str
    matched_proposal_key: str | None = None
    candidate_proposal_keys: tuple[str, ...] = ()
    actual_title: str | None = None
    actual_type: FactType | None = None
    title_drift: bool = False
    type_drift: bool = False


class LearningCardAlignment(StrictModel):
    card_key: str
    expected_title: str
    expected_presentation: CardPresentation
    expected_fact_keys: tuple[str, ...]
    match_state: str
    matched_proposal_key: str | None = None
    actual_title: str | None = None
    actual_presentation: str | None = None
    actual_primary_fact_ids: tuple[str, ...] = ()
    title_drift: bool = False
    grouping_drift: bool = False
    presentation_drift: bool = False


class LearningProposalDispositionAudit(StrictModel):
    proposal_key: str
    disposition: LearningProposalDisposition
    action: LearningProposalAction
    canonical_fact_key: str | None = None
    state: LearningDispositionAuditState
    basis: str


class LearningGoldenDriftReport(StrictModel):
    fixture_id: str
    sample_id: str
    page_num: int
    fact_alignments: tuple[LearningFactAlignment, ...]
    extra_fact_proposal_keys: tuple[str, ...]
    proposal_disposition_audits: tuple[LearningProposalDispositionAudit, ...] = ()
    manual_review_proposal_keys: tuple[str, ...] = ()
    excluded_proposal_keys: tuple[str, ...] = ()
    card_alignments: tuple[LearningCardAlignment, ...]
    extra_card_proposal_keys: tuple[str, ...]
    untyped_presentation_proposal_keys: tuple[str, ...]
    missing_fact_count: int = Field(ge=0)
    extra_fact_count: int = Field(ge=0)
    manual_review_fact_count: int = Field(default=0, ge=0)
    disposition_mismatch_count: int = Field(default=0, ge=0)
    fact_title_drift_count: int = Field(ge=0)
    fact_type_drift_count: int = Field(ge=0)
    missing_card_count: int = Field(ge=0)
    extra_card_count: int = Field(ge=0)
    card_title_drift_count: int = Field(ge=0)
    card_grouping_drift_count: int = Field(ge=0)
    presentation_drift_count: int = Field(ge=0)
    enforcement_state: str = "diagnostic_draft_fixture"


def _teaching_title_key(value: str) -> str:
    return "".join(char for char in normalized_compare_text(value) if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _reviewed_proposal_sha256(*, request: Step2ARequest, proposal) -> str:
    """Retain v8/v9 disposition bindings after additive canonical fields landed."""

    payload = proposal.model_dump(mode="json")
    if request.identity.request_version not in {
        STEP2A_REQUEST_VERSION,
        "step2a_contract_v11_multipage_continuation_binding",
        REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
        MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    }:
        payload.pop("completeness_mode", None)
    return canonical_step2_sha256(payload)


def load_learning_golden_fixture(path: Path) -> LearningGoldenFixture:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return LearningGoldenFixture.model_validate(raw)


def _load_selected_pages(*, evidence_root: Path, sample_id: str, selected_pages: Iterable[int]) -> tuple[PageEvidence, ...]:
    pages: list[PageEvidence] = []
    for page_num in selected_pages:
        path = evidence_root / sample_id / "pages" / f"page_{page_num:04d}.json"
        if not path.is_file():
            raise ValueError(f"{sample_id}: missing selected evidence page {page_num}")
        pages.append(PageEvidence.model_validate_json(path.read_text(encoding="utf-8")))
    return tuple(pages)


def _anchor_matches(unit: EvidenceUnit, anchor: EvidenceAnchorSelector) -> bool:
    if anchor.kind is not None and unit.kind != anchor.kind:
        return False
    needle = normalized_compare_text(anchor.text)
    return needle in normalized_compare_text(unit.raw_text) or needle in normalized_compare_text(unit.canonical_text)


def _table_descendant_ids(table: EvidenceUnit, units: tuple[EvidenceUnit, ...]) -> set[str]:
    ids = {table.evidence_id}
    while True:
        children = {unit.evidence_id for unit in units if unit.parent_id in ids}
        if children <= ids:
            return ids
        ids.update(children)


def _table_matches_expectation(
    expectation: TableStructureExpectation, units: tuple[EvidenceUnit, ...]
) -> tuple[bool, str | None]:
    tables = [unit for unit in units if unit.kind == EvidenceKind.TABLE and _anchor_matches(unit, expectation.anchor)]
    if not tables:
        return False, f"missing table anchor: {expectation.anchor.text}"
    for table in tables:
        rows = int(table.metadata.get("rows", 0))
        columns = int(table.metadata.get("columns", 0))
        if rows < expectation.min_rows or columns < expectation.min_columns:
            continue
        descendants = _table_descendant_ids(table, units)
        texts = "\n".join(
            unit.canonical_text
            for unit in units
            if unit.evidence_id in descendants
        )
        if all(normalized_compare_text(value) in normalized_compare_text(texts) for value in expectation.required_cell_texts):
            return True, None
    return False, f"table structure or required cells do not match: {expectation.anchor.text}"


def _diagram_matches(expectation: DiagramSemanticExpectation, entries: Iterable[DiagramReviewEntry]) -> bool:
    title = normalized_compare_text(expectation.review_title)
    for entry in entries:
        if entry.state != DiagramReviewState.REVIEWED or not entry.title:
            continue
        if entry.review_id != expectation.review_id or entry.figure_evidence_id != expectation.figure_evidence_id:
            continue
        if title not in normalized_compare_text(entry.title):
            continue
        labels = {normalized_compare_text(node.label): node.node_id for node in entry.nodes}
        if any(normalized_compare_text(label) not in labels for label in expectation.required_nodes):
            continue
        edge_values = {
            (
                normalized_compare_text(labels_by_id.get(edge.source_node_id, "")),
                normalized_compare_text(edge.relation),
                normalized_compare_text(labels_by_id.get(edge.target_node_id, "")),
            )
            for labels_by_id in ({node.node_id: node.label for node in entry.nodes},)
            for edge in entry.edges
        }
        wanted = {
            (
                normalized_compare_text(edge.source_label),
                normalized_compare_text(edge.relation),
                normalized_compare_text(edge.target_label),
            )
            for edge in expectation.required_edges
        }
        if wanted <= edge_values:
            return True
    return False


def validate_learning_fixture(
    *,
    fixture: LearningGoldenFixture,
    registry: GoldenSampleRegistry,
    evidence_root: Path,
    diagram_sidecar_path: Path | None = None,
) -> dict[str, Any]:
    """Validate draft teaching targets against immutable Evidence and reviewed diagrams."""
    if fixture.registry_id != registry.registry_id:
        raise ValueError("learning fixture registry_id does not match the golden registry")
    if fixture.material_profile_id != registry.material_profile_id:
        raise ValueError("learning fixture material profile does not match the golden registry")
    registry_by_id = {sample.sample_id: sample for sample in registry.samples}
    if set(registry_by_id) != {sample.sample_id for sample in fixture.samples}:
        raise ValueError("learning fixture samples must exactly match the golden registry")
    diagram_entries: tuple[DiagramReviewEntry, ...] = ()
    if diagram_sidecar_path is not None:
        diagram_entries = load_diagram_review_sidecar(diagram_sidecar_path).entries

    sample_reports: list[dict[str, Any]] = []
    for target in fixture.samples:
        registry_sample = registry_by_id[target.sample_id]
        pages = _load_selected_pages(
            evidence_root=evidence_root,
            sample_id=target.sample_id,
            selected_pages=registry_sample.selected_pages,
        )
        units = tuple(unit for page in pages for unit in page.evidence_units)
        errors: list[str] = []
        for fact in target.facts:
            anchored = [unit for anchor in fact.primary_anchors for unit in units if _anchor_matches(unit, anchor)]
            if not anchored:
                errors.append(f"{fact.fact_key}: no primary Evidence anchor resolved")
                continue
            source_text = "\n".join(unit.canonical_text for unit in anchored)
            missing_terms = [term for term in fact.required_terms if normalized_compare_text(term) not in normalized_compare_text(source_text)]
            if missing_terms:
                errors.append(f"{fact.fact_key}: required terms are not grounded: {missing_terms}")
            if fact.table_structure is not None:
                matched, reason = _table_matches_expectation(fact.table_structure, units)
                if not matched:
                    errors.append(f"{fact.fact_key}: {reason}")
        for diagram in target.diagrams:
            if not _diagram_matches(diagram, (entry for entry in diagram_entries if entry.sample_id == target.sample_id)):
                errors.append(f"diagram target not found or semantically incomplete: {diagram.review_title}")
        sample_reports.append(
            {
                "sample_id": target.sample_id,
                "fact_count": len(target.facts),
                "card_count": len(target.cards),
                "diagram_count": len(target.diagrams),
                "pending_review_entries": [item for item in fixture.pending_review_entries if item.startswith(f"{target.sample_id}:")],
                "errors": errors,
            }
        )
    return {
        "fixture_id": fixture.fixture_id,
        "ready_for_phase2r1": fixture.ready_for_phase2r1,
        "valid": not any(item["errors"] for item in sample_reports),
        "samples": sample_reports,
    }


def compare_learning_golden_page(
    *,
    fixture: LearningGoldenFixture,
    page: PageEvidence,
    request: Step2ARequest,
    step2a_response: Step2AResponse,
    fact_ids_by_proposal_key: Mapping[str, FactSummaryInput],
    step2b_response: Step2BResponse | None,
    source_response_sha256: str | None = None,
) -> LearningGoldenDriftReport:
    """Compare one page-local semantic run with the draft Learning Golden.

    The fixture remains draft, so drift is diagnostic and never promotes or
    mutates Evidence.  Matching is grounded in selected anchor-span text; no
    fuzzy title-only matching is used.
    """
    target = next((sample for sample in fixture.samples if sample.sample_id == request.sample_id), None)
    if target is None:
        raise ValueError("Learning Golden does not contain the request sample")
    if page.page_num != request.projection.page_num:
        raise ValueError("Learning Golden comparison page does not match the Step 2A request")
    proposals_by_key = {proposal.proposal_key: proposal for proposal in step2a_response.proposals}
    declared_dispositions = {
        item.proposal_key: item
        for item in target.proposal_dispositions
        if item.page_num == page.page_num and item.source_request_id == request.identity.request_id
    }
    active_dispositions: dict[str, LearningProposalDispositionReview] = {}
    disposition_audits: list[LearningProposalDispositionAudit] = []
    for item in (item for item in target.proposal_dispositions if item.page_num == page.page_num):
        state = LearningDispositionAuditState.APPLIED
        basis = item.basis
        proposal = proposals_by_key.get(item.proposal_key)
        if item.source_request_id != request.identity.request_id:
            # A response-bound review must never classify a new response, but its
            # historical existence must also not permanently fail a new run.
            state = LearningDispositionAuditState.OUT_OF_SCOPE
            basis = "disposition belongs to a different request and is retained only as historical audit evidence"
        elif item.review.state != LearningReviewState.REVIEWED:
            state = LearningDispositionAuditState.NOT_REVIEWED
            basis = "disposition classification has not been reviewed"
        elif source_response_sha256 != item.source_response_sha256:
            state = LearningDispositionAuditState.RESPONSE_HASH_MISMATCH
            basis = "disposition source response hash does not match the compared response"
        elif proposal is None:
            state = LearningDispositionAuditState.PROPOSAL_MISSING
            basis = "disposition-bound proposal is missing from the compared response"
        elif _reviewed_proposal_sha256(request=request, proposal=proposal) != item.proposal_sha256:
            state = LearningDispositionAuditState.PROPOSAL_HASH_MISMATCH
            basis = "disposition-bound proposal content hash no longer matches"
        else:
            active_dispositions[item.proposal_key] = item
        disposition_audits.append(
            LearningProposalDispositionAudit(
                proposal_key=item.proposal_key,
                disposition=item.disposition,
                action=item.action,
                canonical_fact_key=item.canonical_fact_key,
                state=state,
                basis=basis,
            )
        )
    spans = {
        span.anchor_span_id: span
        for section in request.sections
        for span in section.anchor_spans
    }
    expected_facts = tuple(
        fact
        for fact in target.facts
        if any(_anchor_matches(unit, anchor) for anchor in fact.primary_anchors for unit in page.evidence_units)
    )
    used_proposals: set[str] = set()
    matched_fact_ids: dict[str, str] = {}
    fact_alignments: list[LearningFactAlignment] = []
    for expected in expected_facts:
        candidates = []
        for proposal in step2a_response.proposals:
            disposition = active_dispositions.get(proposal.proposal_key)
            if proposal.proposal_key in declared_dispositions and disposition is None:
                continue
            if disposition is not None:
                if disposition.action in {LearningProposalAction.MANUAL_REVIEW, LearningProposalAction.EXCLUDE}:
                    continue
                if disposition.canonical_fact_key != expected.fact_key:
                    continue
            selected_text = "\n".join(
                spans[span_id].canonical_text
                for span_id in (*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids)
                if span_id in spans
            )
            grounded = normalized_compare_text(f"{selected_text}\n{proposal.assertion}")
            required = expected.required_terms or tuple(anchor.text for anchor in expected.primary_anchors)
            if all(normalized_compare_text(term) in grounded for term in required):
                candidates.append(proposal)
        available = [proposal for proposal in candidates if proposal.proposal_key not in used_proposals]
        compatible = [proposal for proposal in available if proposal.fact_type == expected.fact_type]
        if len(compatible) != 1:
            mismatch = available[0] if len(available) == 1 else None
            fact_alignments.append(
                LearningFactAlignment(
                    fact_key=expected.fact_key,
                    expected_title=expected.title,
                    accepted_title_aliases=expected.title_aliases,
                    expected_type=expected.fact_type,
                    match_state=(
                        "missing"
                        if not available
                        else "type_mismatch"
                        if len(available) == 1
                        else "ambiguous"
                    ),
                    candidate_proposal_keys=tuple(proposal.proposal_key for proposal in available),
                    actual_title=mismatch.label if mismatch is not None else None,
                    actual_type=mismatch.fact_type if mismatch is not None else None,
                    title_drift=(
                        _teaching_title_key(mismatch.label)
                        not in {_teaching_title_key(value) for value in (expected.title, *expected.title_aliases)}
                        if mismatch is not None
                        else False
                    ),
                    type_drift=mismatch is not None,
                )
            )
            continue
        actual = compatible[0]
        used_proposals.add(actual.proposal_key)
        summary = fact_ids_by_proposal_key.get(actual.proposal_key)
        if summary is None:
            raise ValueError("Learning Golden comparison is missing a canonical provisional Fact ID")
        matched_fact_ids[expected.fact_key] = summary.fact_id
        fact_alignments.append(
            LearningFactAlignment(
                fact_key=expected.fact_key,
                expected_title=expected.title,
                accepted_title_aliases=expected.title_aliases,
                expected_type=expected.fact_type,
                match_state="matched",
                matched_proposal_key=actual.proposal_key,
                candidate_proposal_keys=(actual.proposal_key,),
                actual_title=actual.label,
                actual_type=actual.fact_type,
                title_drift=_teaching_title_key(actual.label)
                not in {_teaching_title_key(value) for value in (expected.title, *expected.title_aliases)},
                type_drift=actual.fact_type != expected.fact_type,
            )
        )
    manual_review_keys: list[str] = []
    excluded_keys: list[str] = []
    extra_fact_keys: list[str] = []
    for proposal in step2a_response.proposals:
        if proposal.proposal_key in used_proposals:
            continue
        disposition = active_dispositions.get(proposal.proposal_key)
        if disposition is not None and disposition.action == LearningProposalAction.MANUAL_REVIEW:
            manual_review_keys.append(proposal.proposal_key)
        elif disposition is not None and disposition.action == LearningProposalAction.EXCLUDE:
            excluded_keys.append(proposal.proposal_key)
        else:
            extra_fact_keys.append(proposal.proposal_key)

    expected_fact_keys = {fact.fact_key for fact in expected_facts}
    expected_cards = tuple(
        card for card in target.cards if set(card.primary_fact_keys) & expected_fact_keys
    )
    used_cards: set[str] = set()
    card_alignments: list[LearningCardAlignment] = []
    actual_cards = step2b_response.proposals if step2b_response is not None else ()
    for expected_card in expected_cards:
        page_fact_keys = tuple(
            fact_key for fact_key in expected_card.primary_fact_keys if fact_key in expected_fact_keys
        )
        if any(fact_key not in matched_fact_ids for fact_key in page_fact_keys):
            expected_title = _teaching_title_key(expected_card.title)
            fallback = [
                card
                for card in actual_cards
                if card.proposal_key not in used_cards
                and card.presentation == expected_card.presentation
                and (
                    expected_title in _teaching_title_key(card.title)
                    or _teaching_title_key(card.title) in expected_title
                )
            ]
            if len(fallback) == 1:
                actual_card = fallback[0]
                used_cards.add(actual_card.proposal_key)
                card_alignments.append(
                    LearningCardAlignment(
                        card_key=expected_card.card_key,
                        expected_title=expected_card.title,
                        expected_presentation=expected_card.presentation,
                        expected_fact_keys=page_fact_keys,
                        match_state="fact_dependency_drift",
                        matched_proposal_key=actual_card.proposal_key,
                        actual_title=actual_card.title,
                        actual_presentation=actual_card.presentation,
                        actual_primary_fact_ids=actual_card.primary_fact_ids,
                        title_drift=_teaching_title_key(actual_card.title) != expected_title,
                        grouping_drift=True,
                        presentation_drift=actual_card.presentation != expected_card.presentation,
                    )
                )
                continue
            card_alignments.append(
                LearningCardAlignment(
                    card_key=expected_card.card_key,
                    expected_title=expected_card.title,
                    expected_presentation=expected_card.presentation,
                    expected_fact_keys=page_fact_keys,
                    match_state="missing_fact_dependency",
                )
            )
            continue
        wanted_ids = {matched_fact_ids[fact_key] for fact_key in page_fact_keys}
        card_candidates = [
            card
            for card in actual_cards
            if wanted_ids <= set(card.primary_fact_ids) and card.proposal_key not in used_cards
        ]
        if len(card_candidates) != 1:
            card_alignments.append(
                LearningCardAlignment(
                    card_key=expected_card.card_key,
                    expected_title=expected_card.title,
                    expected_presentation=expected_card.presentation,
                    expected_fact_keys=page_fact_keys,
                    match_state="missing" if not card_candidates else "ambiguous",
                )
            )
            continue
        actual_card = card_candidates[0]
        used_cards.add(actual_card.proposal_key)
        card_alignments.append(
            LearningCardAlignment(
                card_key=expected_card.card_key,
                expected_title=expected_card.title,
                expected_presentation=expected_card.presentation,
                expected_fact_keys=page_fact_keys,
                match_state="matched",
                matched_proposal_key=actual_card.proposal_key,
                actual_title=actual_card.title,
                actual_presentation=actual_card.presentation,
                actual_primary_fact_ids=actual_card.primary_fact_ids,
                title_drift=_teaching_title_key(actual_card.title) != _teaching_title_key(expected_card.title),
                grouping_drift=set(actual_card.primary_fact_ids) != wanted_ids,
                presentation_drift=actual_card.presentation != expected_card.presentation,
            )
        )
    extra_card_keys = tuple(card.proposal_key for card in actual_cards if card.proposal_key not in used_cards)
    allowed_presentations = {item.value for item in CardPresentation}
    untyped_presentations = tuple(
        card.proposal_key
        for card in actual_cards
        if str(card.presentation) not in allowed_presentations
    )
    presentation_drift_keys = {
        item.matched_proposal_key
        for item in card_alignments
        if item.presentation_drift and item.matched_proposal_key is not None
    } | set(untyped_presentations)
    return LearningGoldenDriftReport(
        fixture_id=fixture.fixture_id,
        sample_id=request.sample_id,
        page_num=page.page_num,
        fact_alignments=tuple(fact_alignments),
        extra_fact_proposal_keys=tuple(extra_fact_keys),
        proposal_disposition_audits=tuple(disposition_audits),
        manual_review_proposal_keys=tuple(manual_review_keys),
        excluded_proposal_keys=tuple(excluded_keys),
        card_alignments=tuple(card_alignments),
        extra_card_proposal_keys=extra_card_keys,
        untyped_presentation_proposal_keys=untyped_presentations,
        missing_fact_count=sum(item.match_state != "matched" for item in fact_alignments),
        extra_fact_count=len(extra_fact_keys),
        manual_review_fact_count=len(manual_review_keys),
        disposition_mismatch_count=sum(
            item.state
            in {
                LearningDispositionAuditState.RESPONSE_HASH_MISMATCH,
                LearningDispositionAuditState.PROPOSAL_MISSING,
                LearningDispositionAuditState.PROPOSAL_HASH_MISMATCH,
            }
            for item in disposition_audits
        ),
        fact_title_drift_count=sum(item.title_drift for item in fact_alignments),
        fact_type_drift_count=sum(item.type_drift for item in fact_alignments),
        missing_card_count=sum(item.match_state != "matched" for item in card_alignments),
        extra_card_count=len(extra_card_keys),
        card_title_drift_count=sum(item.title_drift for item in card_alignments),
        card_grouping_drift_count=sum(item.grouping_drift for item in card_alignments),
        presentation_drift_count=len(presentation_drift_keys),
    )


def build_reviewed_canonical_title_projections(
    *,
    fixture: LearningGoldenFixture,
    request: Step2ARequest,
    drift: LearningGoldenDriftReport,
    step2a_response: Step2AResponse,
) -> dict[str, ReviewedCanonicalTitleProjection]:
    """Project only reviewed, exact Golden titles into future Step 2B summaries.

    Aliases are comparison allowances only. A title is projected only after the
    current response independently matches a reviewed Golden Fact; a historical
    proposal disposition is never reused for a new request.
    """

    target = next((sample for sample in fixture.samples if sample.sample_id == request.sample_id), None)
    if target is None:
        raise ValueError("Learning Golden does not contain the request sample")
    targets = {fact.fact_key: fact for fact in target.facts}
    proposals = {proposal.proposal_key: proposal for proposal in step2a_response.proposals}
    projections: dict[str, ReviewedCanonicalTitleProjection] = {}
    for alignment in drift.fact_alignments:
        if alignment.match_state != "matched" or alignment.title_drift or alignment.matched_proposal_key is None:
            continue
        target_fact = targets[alignment.fact_key]
        if target_fact.review.state != LearningReviewState.REVIEWED:
            continue
        proposal = proposals[alignment.matched_proposal_key]
        projections[proposal.proposal_key] = ReviewedCanonicalTitleProjection(
            fixture_id=fixture.fixture_id,
            fact_key=target_fact.fact_key,
            canonical_title=target_fact.title,
            source_label=proposal.label,
            source_label_sha256=hashlib.sha256(proposal.label.encode("utf-8")).hexdigest(),
        )
    return projections


def resolve_reviewed_heading_only_allowance_span_ids(
    *,
    fixture: LearningGoldenFixture,
    sample_id: str,
    page: PageEvidence,
    sections: Iterable[object],
) -> tuple[str, ...]:
    """Derive heading-only allowances only from reviewed Golden assertions."""
    target = next((sample for sample in fixture.samples if sample.sample_id == sample_id), None)
    if target is None:
        raise ValueError("Learning Golden does not contain the requested sample")
    context_only_parent_ids: set[str] = set()
    spans_by_parent: dict[str, list[str]] = {}
    for section in sections:
        for item in getattr(section, "text_evidence", ()):
            if not getattr(item, "context_only", False):
                continue
            context_only_parent_ids.add(item.evidence_id)
            for span in item.anchor_spans:
                spans_by_parent.setdefault(item.evidence_id, []).append(span.anchor_span_id)
    allowances: list[str] = []
    for fact in target.facts:
        if not fact.allow_heading_only or fact.review.state != LearningReviewState.REVIEWED:
            continue
        matched_parent_ids = {
            unit.evidence_id
            for anchor in fact.primary_anchors
            for unit in page.evidence_units
            if _anchor_matches(unit, anchor)
        }
        for parent_id in sorted(matched_parent_ids & context_only_parent_ids):
            allowances.extend(spans_by_parent.get(parent_id, ()))
    return tuple(dict.fromkeys(allowances))
