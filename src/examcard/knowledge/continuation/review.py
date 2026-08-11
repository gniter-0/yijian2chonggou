"""Reviewed continuation-marker dispositions and real-page learning fixtures.

The projection detector intentionally treats visible down-arrow markers as
potential page continuations.  This module supplies the narrow, reviewed
exception needed when a marker is an inline diagram/direction token instead.
It never changes the immutable Evidence manifest or its anchor spans.
"""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from ...evidence.layout import normalized_compare_text
from ...materials.schema import StrictModel, V2_SCHEMA_VERSION

from ..schema import CompletenessMode, FactType


CONTINUATION_REVIEW_SIDECAR_VERSION = "continuation_review_v1"
CONTINUATION_LEARNING_GOLDEN_VERSION = "continuation_learning_golden_v1"
CONTINUATION_TITLE_PROJECTION_VERSION = "continuation_title_projection_v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
# Kept local to avoid a continuation_review -> semantic_contracts import cycle.
# These contracts derive proposal identity locally rather than accepting a model key.
_LOCALLY_DERIVED_PROPOSAL_REQUEST_VERSIONS = frozenset(
    {
        "step2a_contract_v13_minimal_model_proposal",
        "step2a_contract_v14_numeric_expression_grounding",
        "step2a_contract_v15_single_role_anchor",
        "step2a_contract_v16_deterministic_numeric_claims",
        "step2a_contract_v17_table_continuation_integration",
        "step2a_contract_v18_coverage_obligation",
        "step2a_contract_v19_coverage_obligation_context",
        "step2a_contract_v20_stiffness_atomic_threshold_coverage",
        "step2a_contract_v21_composition_numeric_occurrence",
        "step2a_contract_v22_structural_index_occurrence",
        "step2a_contract_v23_source_owned_structural_projection",
    }
)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ContinuationReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class ContinuationMarkerDisposition(StrEnum):
    """Human disposition for a detector-created continuation record."""

    PAGE_CONTINUATION = "page_continuation"
    INLINE_DIRECTION_MARKER = "inline_direction_marker"
    UNRESOLVED = "unresolved"


class ContinuationMarkerReview(StrictModel):
    continuation_id: str = Field(pattern=r"^continuation:[0-9a-f]{24}$")
    disposition: ContinuationMarkerDisposition
    projection_id: str = Field(pattern=r"^step2a-projection:[0-9a-f]{24}$")
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    page_num: int = Field(ge=1)
    section_id: str = Field(pattern=r"^section:[0-9a-f]{24}$")
    anchor_span_ids: tuple[str, ...]
    marker_token_span_ids: tuple[str, ...]
    source_evidence_ids: tuple[str, ...]
    basis: str = Field(min_length=16, max_length=1000)

    @model_validator(mode="after")
    def validate_marker_review(self) -> "ContinuationMarkerReview":
        if not self.anchor_span_ids or len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("continuation marker reviews require unique anchor spans")
        if not self.marker_token_span_ids or len(self.marker_token_span_ids) != len(set(self.marker_token_span_ids)):
            raise ValueError("continuation marker reviews require unique marker-token spans")
        if not set(self.marker_token_span_ids) <= set(self.anchor_span_ids):
            raise ValueError("continuation marker token spans must belong to the detector record")
        if not self.source_evidence_ids or len(self.source_evidence_ids) != len(set(self.source_evidence_ids)):
            raise ValueError("continuation marker reviews require unique source Evidence IDs")
        if self.basis != self.basis.strip():
            raise ValueError("continuation marker review basis must be stripped")
        return self


def make_continuation_closure_review_id(
    *,
    continuation_id: str,
    source_projection_id: str,
    source_manifest_sha256: str,
    source_page_num: int,
    source_section_id: str,
    source_anchor_span_ids: tuple[str, ...],
    adjacent_projection_id: str,
    adjacent_manifest_sha256: str,
    adjacent_page_num: int,
    adjacent_section_id: str,
    closure_anchor_span_ids: tuple[str, ...],
    basis: str,
) -> str:
    digest = _canonical_sha256(
        {
            "continuation_id": continuation_id,
            "source_projection_id": source_projection_id,
            "source_manifest_sha256": source_manifest_sha256,
            "source_page_num": source_page_num,
            "source_section_id": source_section_id,
            "source_anchor_span_ids": source_anchor_span_ids,
            "adjacent_projection_id": adjacent_projection_id,
            "adjacent_manifest_sha256": adjacent_manifest_sha256,
            "adjacent_page_num": adjacent_page_num,
            "adjacent_section_id": adjacent_section_id,
            "closure_anchor_span_ids": closure_anchor_span_ids,
            "basis": basis,
        }
    )
    return f"continuation-review:{digest[:24]}"


class ContinuationClosureReview(StrictModel):
    review_id: str = Field(pattern=r"^continuation-review:[0-9a-f]{24}$")
    continuation_id: str = Field(pattern=r"^continuation:[0-9a-f]{24}$")
    source_projection_id: str = Field(pattern=r"^step2a-projection:[0-9a-f]{24}$")
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_page_num: int = Field(ge=1)
    source_section_id: str = Field(pattern=r"^section:[0-9a-f]{24}$")
    source_anchor_span_ids: tuple[str, ...]
    adjacent_projection_id: str = Field(pattern=r"^step2a-projection:[0-9a-f]{24}$")
    adjacent_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    adjacent_page_num: int = Field(ge=1)
    adjacent_section_id: str = Field(pattern=r"^section:[0-9a-f]{24}$")
    closure_anchor_span_ids: tuple[str, ...]
    basis: str = Field(min_length=16, max_length=1000)

    @model_validator(mode="after")
    def validate_closure_review(self) -> "ContinuationClosureReview":
        if self.adjacent_page_num != self.source_page_num + 1:
            raise ValueError("continuation closure reviews require the immediate adjacent page")
        if self.source_projection_id == self.adjacent_projection_id:
            raise ValueError("continuation closure reviews cannot self-bind one projection")
        if not self.source_anchor_span_ids or len(self.source_anchor_span_ids) != len(set(self.source_anchor_span_ids)):
            raise ValueError("continuation closure reviews require unique source anchor spans")
        if not self.closure_anchor_span_ids or len(self.closure_anchor_span_ids) != len(set(self.closure_anchor_span_ids)):
            raise ValueError("continuation closure reviews require unique closure anchor spans")
        if self.basis != self.basis.strip():
            raise ValueError("continuation closure review basis must be stripped")
        expected_id = make_continuation_closure_review_id(
            continuation_id=self.continuation_id,
            source_projection_id=self.source_projection_id,
            source_manifest_sha256=self.source_manifest_sha256,
            source_page_num=self.source_page_num,
            source_section_id=self.source_section_id,
            source_anchor_span_ids=self.source_anchor_span_ids,
            adjacent_projection_id=self.adjacent_projection_id,
            adjacent_manifest_sha256=self.adjacent_manifest_sha256,
            adjacent_page_num=self.adjacent_page_num,
            adjacent_section_id=self.adjacent_section_id,
            closure_anchor_span_ids=self.closure_anchor_span_ids,
            basis=self.basis,
        )
        if self.review_id != expected_id:
            raise ValueError("continuation closure review ID does not match its immutable binding")
        return self


def make_continuation_review_sidecar_id(payload: object) -> str:
    return f"continuation-sidecar:{_canonical_sha256(payload)[:24]}"


class ContinuationBindingReviewSidecar(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    sidecar_version: str = CONTINUATION_REVIEW_SIDECAR_VERSION
    sidecar_id: str = Field(pattern=r"^continuation-sidecar:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_pdf_path: str = Field(min_length=1)
    source_pdf_sha256: str = Field(pattern=_SHA256_PATTERN)
    state: ContinuationReviewState
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    review_basis: str = Field(min_length=16, max_length=1000)
    marker_reviews: tuple[ContinuationMarkerReview, ...]
    closure_reviews: tuple[ContinuationClosureReview, ...] = ()

    @property
    def fingerprint_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json", exclude={"sidecar_id"}))

    @model_validator(mode="after")
    def validate_sidecar(self) -> "ContinuationBindingReviewSidecar":
        if self.sidecar_version != CONTINUATION_REVIEW_SIDECAR_VERSION:
            raise ValueError("unsupported continuation review sidecar version")
        if self.source_pdf_sha256 != self.source_document_sha256:
            raise ValueError("continuation sidecar PDF hash must equal its source document hash")
        if self.review_basis != self.review_basis.strip():
            raise ValueError("continuation sidecar review basis must be stripped")
        if self.state == ContinuationReviewState.REVIEWED:
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed continuation sidecars require reviewer and reviewed_at_utc")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft continuation sidecars cannot contain reviewer metadata")
        marker_ids = [item.continuation_id for item in self.marker_reviews]
        if not marker_ids or len(marker_ids) != len(set(marker_ids)):
            raise ValueError("continuation sidecars require unique marker reviews")
        closure_ids = [item.review_id for item in self.closure_reviews]
        closure_sources = [item.continuation_id for item in self.closure_reviews]
        if len(closure_ids) != len(set(closure_ids)) or len(closure_sources) != len(set(closure_sources)):
            raise ValueError("continuation sidecars require unique closure reviews")
        dispositions = {item.continuation_id: item.disposition for item in self.marker_reviews}
        if any(dispositions.get(item.continuation_id) != ContinuationMarkerDisposition.PAGE_CONTINUATION for item in self.closure_reviews):
            raise ValueError("continuation closures require a reviewed page_continuation marker")
        expected_id = make_continuation_review_sidecar_id(
            self.model_dump(mode="json", exclude={"sidecar_id"})
        )
        if self.sidecar_id != expected_id:
            raise ValueError("continuation sidecar ID does not match its immutable content")
        return self


def load_continuation_review_sidecar(path: Path) -> ContinuationBindingReviewSidecar:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return ContinuationBindingReviewSidecar.model_validate(raw)


def verify_continuation_review_source_pdf(
    *, sidecar: ContinuationBindingReviewSidecar, repository_root: Path
) -> Path:
    """Fail closed unless the reviewed sidecar still binds the immutable PDF."""

    repository_root = Path(repository_root).resolve()
    source_pdf = (repository_root / sidecar.source_pdf_path).resolve()
    try:
        source_pdf.relative_to(repository_root)
    except ValueError as exc:
        raise ValueError("continuation sidecar PDF path escapes the repository root") from exc
    if not source_pdf.is_file():
        raise ValueError("continuation sidecar source PDF is missing")
    if sha256_file(source_pdf) != sidecar.source_pdf_sha256:
        raise ValueError("continuation sidecar source PDF hash does not match")
    return source_pdf


class ContinuationGoldenPageBinding(StrictModel):
    page_num: int = Field(ge=1)
    source_manifest_path: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)


class ContinuationAllItemsGoldenTarget(StrictModel):
    fact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    canonical_title: str = Field(min_length=2, max_length=64)
    fact_type: FactType = FactType.ENUMERATION
    completeness_mode: CompletenessMode = CompletenessMode.ALL_ITEMS
    required_evidence_ids: tuple[str, ...]
    required_anchor_span_ids: tuple[str, ...]
    required_assertion_terms: tuple[str, ...]

    @model_validator(mode="after")
    def validate_target(self) -> "ContinuationAllItemsGoldenTarget":
        if not self.required_evidence_ids or len(self.required_evidence_ids) != len(set(self.required_evidence_ids)):
            raise ValueError("continuation Golden target requires unique Evidence IDs")
        if not self.required_anchor_span_ids or len(self.required_anchor_span_ids) != len(set(self.required_anchor_span_ids)):
            raise ValueError("continuation Golden target requires unique anchor spans")
        if not self.required_assertion_terms or len(self.required_assertion_terms) != len(set(self.required_assertion_terms)):
            raise ValueError("continuation Golden target requires unique assertion terms")
        return self


class ContinuationLearningGoldenFixture(StrictModel):
    schema_version: str = V2_SCHEMA_VERSION
    fixture_version: str = CONTINUATION_LEARNING_GOLDEN_VERSION
    fixture_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    continuation_review_sidecar_id: str = Field(pattern=r"^continuation-sidecar:[0-9a-f]{24}$")
    continuation_review_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)
    pages: tuple[ContinuationGoldenPageBinding, ...]
    target: ContinuationAllItemsGoldenTarget
    state: ContinuationReviewState
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    review_basis: str = Field(min_length=16, max_length=1000)

    @model_validator(mode="after")
    def validate_fixture(self) -> "ContinuationLearningGoldenFixture":
        if self.fixture_version != CONTINUATION_LEARNING_GOLDEN_VERSION:
            raise ValueError("unsupported continuation Learning Golden version")
        if len(self.pages) != 2 or self.pages[1].page_num != self.pages[0].page_num + 1:
            raise ValueError("continuation Learning Golden requires exactly two adjacent pages")
        if self.review_basis != self.review_basis.strip():
            raise ValueError("continuation Learning Golden basis must be stripped")
        if self.state == ContinuationReviewState.REVIEWED:
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed continuation Learning Golden requires reviewer and reviewed_at_utc")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft continuation Learning Golden cannot contain reviewer metadata")
        return self


class ContinuationLearningGoldenAlignment(StrictModel):
    fixture_id: str
    sample_id: str
    proposal_key: str | None = None
    valid: bool
    failure_codes: tuple[str, ...] = ()
    matched_evidence_ids: tuple[str, ...] = ()
    matched_anchor_span_ids: tuple[str, ...] = ()
    title_projection: "ContinuationCanonicalTitleProjection | None" = None
    basis: str


class ContinuationCanonicalTitleProjectionState(StrEnum):
    """Terminal disposition for a reviewed continuation-title projection."""

    PROJECTED = "projected"
    OUT_OF_SCOPE = "out_of_scope"
    FAIL_CLOSED = "fail_closed"


class ContinuationCanonicalTitleProjection(StrictModel):
    """Auditable local title authority for one exact continuation Golden match.

    The model label is evidence about the response only. It never becomes a
    canonical title by fuzzy matching or an implicit alias; projection is
    granted solely by the reviewed fixture's exact source constraints.
    """

    schema_version: str = CONTINUATION_TITLE_PROJECTION_VERSION
    projection_id: str = Field(pattern=r"^continuation-title-projection:[0-9a-f]{24}$")
    projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    state: ContinuationCanonicalTitleProjectionState
    fixture_id: str
    fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    fact_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    request_id: str
    request_version: str
    source_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    proposal_key: str | None = Field(default=None, pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    model_label: str | None = Field(default=None, min_length=2, max_length=64)
    model_label_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    canonical_title: str | None = Field(default=None, min_length=2, max_length=64)
    matched_evidence_ids: tuple[str, ...] = ()
    matched_anchor_span_ids: tuple[str, ...] = ()
    continuation_binding_ids: tuple[str, ...] = ()
    failure_codes: tuple[str, ...] = ()
    basis: str = Field(min_length=1, max_length=1000)

    def _fingerprint_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "fixture_id": self.fixture_id,
            "fixture_sha256": self.fixture_sha256,
            "fact_key": self.fact_key,
            "request_id": self.request_id,
            "request_version": self.request_version,
            "source_response_sha256": self.source_response_sha256,
            "proposal_key": self.proposal_key,
            "model_label": self.model_label,
            "model_label_sha256": self.model_label_sha256,
            "canonical_title": self.canonical_title,
            "matched_evidence_ids": self.matched_evidence_ids,
            "matched_anchor_span_ids": self.matched_anchor_span_ids,
            "continuation_binding_ids": self.continuation_binding_ids,
            "failure_codes": self.failure_codes,
            "basis": self.basis,
        }

    @model_validator(mode="after")
    def validate_projection(self) -> "ContinuationCanonicalTitleProjection":
        if self.schema_version != CONTINUATION_TITLE_PROJECTION_VERSION:
            raise ValueError("unsupported continuation canonical title projection version")
        if bool(self.model_label) != bool(self.model_label_sha256):
            raise ValueError("continuation title projection model label/hash must be present together")
        if self.model_label is not None:
            if self.model_label != self.model_label.strip():
                raise ValueError("continuation title projection model label must be stripped")
            if self.model_label_sha256 != hashlib.sha256(self.model_label.encode("utf-8")).hexdigest():
                raise ValueError("continuation title projection model label hash does not match")
        if len(self.matched_evidence_ids) != len(set(self.matched_evidence_ids)):
            raise ValueError("continuation title projection matched Evidence IDs must be unique")
        if len(self.matched_anchor_span_ids) != len(set(self.matched_anchor_span_ids)):
            raise ValueError("continuation title projection matched anchor span IDs must be unique")
        if len(self.continuation_binding_ids) != len(set(self.continuation_binding_ids)):
            raise ValueError("continuation title projection continuation binding IDs must be unique")
        if len(self.failure_codes) != len(set(self.failure_codes)):
            raise ValueError("continuation title projection failure codes must be unique")
        if self.state == ContinuationCanonicalTitleProjectionState.PROJECTED:
            if (
                self.proposal_key is None
                or self.model_label is None
                or self.canonical_title is None
                or not self.matched_evidence_ids
                or not self.matched_anchor_span_ids
                or not self.continuation_binding_ids
                or self.failure_codes
            ):
                raise ValueError("projected continuation title requires exact candidate, source proof, and no failures")
            if self.canonical_title != self.canonical_title.strip():
                raise ValueError("continuation title projection canonical title must be stripped")
        elif self.canonical_title is not None or not self.failure_codes:
            raise ValueError("non-projected continuation title audits must fail closed without a canonical title")
        digest = _canonical_sha256(self._fingerprint_payload())
        if self.projection_sha256 != digest or self.projection_id != f"continuation-title-projection:{digest[:24]}":
            raise ValueError("continuation title projection ID/hash does not match its immutable audit payload")
        return self


def _fixture_sha256(fixture: ContinuationLearningGoldenFixture) -> str:
    return _canonical_sha256(fixture.model_dump(mode="json"))


def _proposal_response_sha256(proposals: Any) -> str:
    return _canonical_sha256([proposal.model_dump(mode="json") for proposal in proposals])


def _make_continuation_title_projection(
    *,
    fixture: ContinuationLearningGoldenFixture,
    request: Any,
    source_response_sha256: str,
    state: ContinuationCanonicalTitleProjectionState,
    proposal: Any | None,
    matched_evidence_ids: tuple[str, ...],
    matched_anchor_span_ids: tuple[str, ...],
    continuation_binding_ids: tuple[str, ...],
    failure_codes: tuple[str, ...],
    basis: str,
) -> ContinuationCanonicalTitleProjection:
    model_label = proposal.label if proposal is not None else None
    model_label_sha256 = hashlib.sha256(model_label.encode("utf-8")).hexdigest() if model_label is not None else None
    payload = {
        "schema_version": CONTINUATION_TITLE_PROJECTION_VERSION,
        "state": state.value,
        "fixture_id": fixture.fixture_id,
        "fixture_sha256": _fixture_sha256(fixture),
        "fact_key": fixture.target.fact_key,
        "request_id": request.identity.request_id,
        "request_version": request.identity.request_version,
        "source_response_sha256": source_response_sha256,
        "proposal_key": proposal.proposal_key if proposal is not None else None,
        "model_label": model_label,
        "model_label_sha256": model_label_sha256,
        "canonical_title": fixture.target.canonical_title if state == ContinuationCanonicalTitleProjectionState.PROJECTED else None,
        "matched_evidence_ids": matched_evidence_ids,
        "matched_anchor_span_ids": matched_anchor_span_ids,
        "continuation_binding_ids": continuation_binding_ids,
        "failure_codes": failure_codes,
        "basis": basis,
    }
    digest = _canonical_sha256(payload)
    return ContinuationCanonicalTitleProjection(
        projection_id=f"continuation-title-projection:{digest[:24]}",
        projection_sha256=digest,
        **payload,
    )


def assess_continuation_learning_golden(
    *,
    fixture: ContinuationLearningGoldenFixture,
    request: Any,
    proposals: Any,
    source_response_sha256: str | None = None,
) -> ContinuationLearningGoldenAlignment:
    """Build a fail-closed canonical-title projection for a reviewed page pair.

    A title projection has no fuzzy label matcher. The raw model label is kept
    in the audit but grants no authority. Only an exact reviewed target match
    can project the canonical title into a later Step 2B summary.
    """

    frozen_proposals = tuple(proposals)
    scope_failures: list[str] = []
    if request.sample_id != fixture.sample_id:
        scope_failures.append("sample_id_mismatch")
    if (
        request.continuation_review_sidecar is None
        or request.continuation_review_sidecar.sidecar_id != fixture.continuation_review_sidecar_id
        or request.continuation_review_sidecar_sha256 != fixture.continuation_review_sidecar_sha256
    ):
        scope_failures.append("continuation_sidecar_mismatch")
    projections = {item.page_num: item for item in request.source_projections}
    for binding in fixture.pages:
        projection = projections.get(binding.page_num)
        if projection is None:
            scope_failures.append(f"missing_page_{binding.page_num}")
        elif (
            projection.source_manifest_sha256 != binding.source_manifest_sha256
            or projection.source_manifest_path != binding.source_manifest_path
            or projection.source_document_sha256 != fixture.source_document_sha256
        ):
            scope_failures.append(f"manifest_mismatch_page_{binding.page_num}")
    if getattr(request.identity, "request_version", "") in _LOCALLY_DERIVED_PROPOSAL_REQUEST_VERSIONS:
        matches = [item for item in frozen_proposals if item.fact_type == fixture.target.fact_type]
    else:
        matches = [item for item in frozen_proposals if item.proposal_key == f"proposal:{fixture.target.fact_key}"]
    proposal_failures: list[str] = []
    if len(matches) != 1:
        proposal_failures.append("proposal_missing_or_ambiguous")
        proposal = None
    else:
        proposal = matches[0]
    if proposal is not None:
        if proposal.fact_type != fixture.target.fact_type:
            proposal_failures.append("fact_type_mismatch")
        if proposal.completeness_mode != fixture.target.completeness_mode:
            proposal_failures.append("completeness_mode_mismatch")
        if proposal.label != proposal.label.strip():
            proposal_failures.append("model_label_not_stripped")
        selected_evidence_ids = set((*proposal.primary_evidence_ids, *proposal.secondary_evidence_ids))
        selected_span_ids = set((*proposal.primary_anchor_span_ids, *proposal.secondary_anchor_span_ids))
        required_evidence_ids = set(fixture.target.required_evidence_ids)
        required_anchor_span_ids = set(fixture.target.required_anchor_span_ids)
        if not required_evidence_ids <= selected_evidence_ids:
            proposal_failures.append("required_evidence_missing")
        if selected_evidence_ids - required_evidence_ids:
            proposal_failures.append("unexpected_evidence_selected")
        if not required_anchor_span_ids <= selected_span_ids:
            proposal_failures.append("required_anchor_span_missing")
        if selected_span_ids - required_anchor_span_ids:
            proposal_failures.append("unexpected_anchor_span_selected")
        normalized_assertion = normalized_compare_text(proposal.assertion)
        if any(normalized_compare_text(term) not in normalized_assertion for term in fixture.target.required_assertion_terms):
            proposal_failures.append("assertion_term_missing")
        closed_bindings = tuple(
            binding
            for binding in request.continuation_bindings
            if getattr(binding.state, "value", binding.state) == "closed"
            and binding.source_section_id in proposal.source_section_ids
            and binding.adjacent_section_id in proposal.source_section_ids
            and set((*binding.source_anchor_span_ids, *binding.closure_anchor_span_ids)) <= selected_span_ids
        )
        if len(closed_bindings) == 0:
            proposal_failures.append("continuation_binding_missing")
        elif len(closed_bindings) > 1:
            proposal_failures.append("continuation_binding_ambiguous")
    else:
        selected_evidence_ids = set()
        selected_span_ids = set()
        closed_bindings = ()
    failures = tuple(dict.fromkeys((*scope_failures, *proposal_failures)))
    matched_evidence_ids = tuple(sorted(selected_evidence_ids & set(fixture.target.required_evidence_ids)))
    matched_anchor_span_ids = tuple(sorted(selected_span_ids & set(fixture.target.required_anchor_span_ids)))
    continuation_binding_ids = tuple(binding.binding_id for binding in closed_bindings)
    projection_state = (
        ContinuationCanonicalTitleProjectionState.OUT_OF_SCOPE
        if scope_failures
        else ContinuationCanonicalTitleProjectionState.FAIL_CLOSED
        if proposal_failures
        else ContinuationCanonicalTitleProjectionState.PROJECTED
    )
    projection_basis = (
        "reviewed continuation Golden exact-match projection: type, completeness, assertion terms, selected Evidence, anchor spans, and closed binding all match; model label is retained only as audited source text."
        if projection_state == ContinuationCanonicalTitleProjectionState.PROJECTED
        else "continuation canonical title projection not granted: " + ", ".join(failures)
    )
    title_projection = _make_continuation_title_projection(
        fixture=fixture,
        request=request,
        source_response_sha256=source_response_sha256 or _proposal_response_sha256(frozen_proposals),
        state=projection_state,
        proposal=proposal,
        matched_evidence_ids=matched_evidence_ids,
        matched_anchor_span_ids=matched_anchor_span_ids,
        continuation_binding_ids=continuation_binding_ids,
        failure_codes=failures,
        basis=projection_basis,
    )
    return ContinuationLearningGoldenAlignment(
        fixture_id=fixture.fixture_id,
        sample_id=fixture.sample_id,
        proposal_key=proposal.proposal_key if proposal is not None else None,
        valid=projection_state == ContinuationCanonicalTitleProjectionState.PROJECTED,
        failure_codes=failures,
        matched_evidence_ids=matched_evidence_ids,
        matched_anchor_span_ids=matched_anchor_span_ids,
        title_projection=title_projection,
        basis=(
            "reviewed real-page continuation target projects its canonical title from exact source-bound Fact constraints"
            if projection_state == ContinuationCanonicalTitleProjectionState.PROJECTED
            else "continuation Learning Golden rejected the proposal: " + ", ".join(failures)
        ),
    )
