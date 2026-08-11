"""Continuation records/bindings: page-end markers and adjacent-page closures."""
from __future__ import annotations
from collections.abc import Iterable
from enum import StrEnum

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from ..continuation.review import ContinuationBindingReviewSidecar, ContinuationMarkerDisposition

from .hashing import _canonical_sha256
from .request_identity import _SHA256_PATTERN


class TableGridIntegrityStatus(StrEnum):
    PASSED = "passed"
    PASSED_WITH_REVIEWED_REPAIR = "passed_with_reviewed_repair"
    MANUAL_REVIEW = "manual_review"


class ContinuationState(StrEnum):
    """Page-local continuation state; page-only input never closes a chain."""

    PARTIAL_CONTINUATION = "partial_continuation"


class ContinuationBindingState(StrEnum):
    """Whether a continued source list has a reviewable closed adjacent-page binding."""

    CLOSED = "closed"
    MANUAL_REVIEW = "manual_review"


class ContinuationBindingFailureCode(StrEnum):
    """Why an explicit continuation cannot unlock an all-items Fact."""

    MISSING_ADJACENT_PAGE = "missing_adjacent_page"
    NON_ADJACENT_PAGE = "non_adjacent_page"
    OUT_OF_SECTION = "out_of_section"
    AMBIGUOUS_CLOSURE = "ambiguous_closure"
    SPAN_OVERREACH = "span_overreach"


def make_continuation_record_id(
    *,
    page_num: int,
    source_manifest_sha256: str,
    anchor_span_ids: Iterable[str],
) -> str:
    digest = _canonical_sha256(
        {
            "page_num": page_num,
            "source_manifest_sha256": source_manifest_sha256,
            "anchor_span_ids": tuple(anchor_span_ids),
        }
    )
    return f"continuation:{digest[:24]}"


class ContinuationRecord(StrictModel):
    """A deterministic page-end marker that blocks an all-items enumeration."""

    continuation_id: str = Field(pattern=r"^continuation:[0-9a-f]{24}$")
    state: ContinuationState = ContinuationState.PARTIAL_CONTINUATION
    page_num: int = Field(ge=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    anchor_span_ids: tuple[str, ...]
    required_adjacent_page_num: int = Field(ge=1)
    basis: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def validate_record(self) -> "ContinuationRecord":
        if self.required_adjacent_page_num != self.page_num + 1:
            raise ValueError("continuation records must require the immediate adjacent page")
        if not self.anchor_span_ids or len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("continuation records require unique source anchor spans")
        expected_id = make_continuation_record_id(
            page_num=self.page_num,
            source_manifest_sha256=self.source_manifest_sha256,
            anchor_span_ids=self.anchor_span_ids,
        )
        if self.continuation_id != expected_id:
            raise ValueError("continuation record ID does not match its immutable binding")
        return self


def make_continuation_binding_id(
    *,
    continuation_id: str,
    source_projection_id: str,
    source_manifest_sha256: str,
    source_page_num: int,
    source_section_id: str,
    source_anchor_span_ids: Iterable[str],
    state: ContinuationBindingState,
    adjacent_projection_id: str | None,
    adjacent_manifest_sha256: str | None,
    adjacent_page_num: int | None,
    adjacent_section_id: str | None,
    closure_anchor_span_ids: Iterable[str],
    closure_basis: str,
    failure_code: ContinuationBindingFailureCode | None,
) -> str:
    """Create a stable ID for a source-bound continuation disposition."""

    digest = _canonical_sha256(
        {
            "continuation_id": continuation_id,
            "source_projection_id": source_projection_id,
            "source_manifest_sha256": source_manifest_sha256,
            "source_page_num": source_page_num,
            "source_section_id": source_section_id,
            "source_anchor_span_ids": tuple(source_anchor_span_ids),
            "state": state.value,
            "adjacent_projection_id": adjacent_projection_id,
            "adjacent_manifest_sha256": adjacent_manifest_sha256,
            "adjacent_page_num": adjacent_page_num,
            "adjacent_section_id": adjacent_section_id,
            "closure_anchor_span_ids": tuple(closure_anchor_span_ids),
            "closure_basis": closure_basis,
            "failure_code": failure_code.value if failure_code is not None else None,
        }
    )
    return f"continuation-binding:{digest[:24]}"


class ContinuationBinding(StrictModel):
    """A non-destructive link from a page-end marker to an adjacent source page.

    A closed binding is an input-side proof only. A model still has to select
    the source and closure anchor spans before its ``all_items`` Fact may pass.
    Manual bindings retain missing or ambiguous cases without granting that
    publication permission.
    """

    binding_id: str = Field(pattern=r"^continuation-binding:[0-9a-f]{24}$")
    continuation_id: str = Field(pattern=r"^continuation:[0-9a-f]{24}$")
    state: ContinuationBindingState
    source_projection_id: str = Field(pattern=r"^step2a-projection:[0-9a-f]{24}$")
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_page_num: int = Field(ge=1)
    source_section_id: str = Field(pattern=r"^section:[0-9a-f]{24}$")
    source_anchor_span_ids: tuple[str, ...]
    adjacent_projection_id: str | None = Field(default=None, pattern=r"^step2a-projection:[0-9a-f]{24}$")
    adjacent_manifest_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    adjacent_page_num: int | None = Field(default=None, ge=1)
    adjacent_section_id: str | None = Field(default=None, pattern=r"^section:[0-9a-f]{24}$")
    closure_anchor_span_ids: tuple[str, ...] = ()
    closure_basis: str = Field(min_length=8, max_length=500)
    failure_code: ContinuationBindingFailureCode | None = None

    @model_validator(mode="after")
    def validate_binding(self) -> "ContinuationBinding":
        source_span_ids = self.source_anchor_span_ids
        closure_span_ids = self.closure_anchor_span_ids
        if not source_span_ids or len(source_span_ids) != len(set(source_span_ids)):
            raise ValueError("continuation bindings require unique source anchor spans")
        if len(closure_span_ids) != len(set(closure_span_ids)):
            raise ValueError("continuation bindings require unique closure anchor spans")
        if self.closure_basis != self.closure_basis.strip():
            raise ValueError("continuation binding closure basis must be stripped")
        adjacent_values = (
            self.adjacent_projection_id,
            self.adjacent_manifest_sha256,
            self.adjacent_page_num,
            self.adjacent_section_id,
        )
        if self.state == ContinuationBindingState.CLOSED:
            if any(value is None for value in adjacent_values) or not closure_span_ids:
                raise ValueError("closed continuation bindings require adjacent source, section, and closure spans")
            if self.failure_code is not None:
                raise ValueError("closed continuation bindings cannot carry a failure code")
        else:
            if self.failure_code is None:
                raise ValueError("manual-review continuation bindings require a failure code")
        expected_id = make_continuation_binding_id(
            continuation_id=self.continuation_id,
            source_projection_id=self.source_projection_id,
            source_manifest_sha256=self.source_manifest_sha256,
            source_page_num=self.source_page_num,
            source_section_id=self.source_section_id,
            source_anchor_span_ids=source_span_ids,
            state=self.state,
            adjacent_projection_id=self.adjacent_projection_id,
            adjacent_manifest_sha256=self.adjacent_manifest_sha256,
            adjacent_page_num=self.adjacent_page_num,
            adjacent_section_id=self.adjacent_section_id,
            closure_anchor_span_ids=closure_span_ids,
            closure_basis=self.closure_basis,
            failure_code=self.failure_code,
        )
        if self.binding_id != expected_id:
            raise ValueError("continuation binding ID does not match its immutable binding")
        return self


def build_continuation_binding(
    *,
    continuation_id: str,
    state: ContinuationBindingState,
    source_projection_id: str,
    source_manifest_sha256: str,
    source_page_num: int,
    source_section_id: str,
    source_anchor_span_ids: Iterable[str],
    closure_basis: str,
    adjacent_projection_id: str | None = None,
    adjacent_manifest_sha256: str | None = None,
    adjacent_page_num: int | None = None,
    adjacent_section_id: str | None = None,
    closure_anchor_span_ids: Iterable[str] = (),
    failure_code: ContinuationBindingFailureCode | None = None,
) -> ContinuationBinding:
    """Create a deterministic continuation binding without mutating Evidence."""

    frozen_source_spans = tuple(source_anchor_span_ids)
    frozen_closure_spans = tuple(closure_anchor_span_ids)
    return ContinuationBinding(
        binding_id=make_continuation_binding_id(
            continuation_id=continuation_id,
            source_projection_id=source_projection_id,
            source_manifest_sha256=source_manifest_sha256,
            source_page_num=source_page_num,
            source_section_id=source_section_id,
            source_anchor_span_ids=frozen_source_spans,
            state=state,
            adjacent_projection_id=adjacent_projection_id,
            adjacent_manifest_sha256=adjacent_manifest_sha256,
            adjacent_page_num=adjacent_page_num,
            adjacent_section_id=adjacent_section_id,
            closure_anchor_span_ids=frozen_closure_spans,
            closure_basis=closure_basis,
            failure_code=failure_code,
        ),
        continuation_id=continuation_id,
        state=state,
        source_projection_id=source_projection_id,
        source_manifest_sha256=source_manifest_sha256,
        source_page_num=source_page_num,
        source_section_id=source_section_id,
        source_anchor_span_ids=frozen_source_spans,
        adjacent_projection_id=adjacent_projection_id,
        adjacent_manifest_sha256=adjacent_manifest_sha256,
        adjacent_page_num=adjacent_page_num,
        adjacent_section_id=adjacent_section_id,
        closure_anchor_span_ids=frozen_closure_spans,
        closure_basis=closure_basis,
        failure_code=failure_code,
    )


def build_reviewed_continuation_bindings(
    sidecar: ContinuationBindingReviewSidecar,
) -> tuple[ContinuationBinding, ...]:
    """Project reviewed marker dispositions into V12 continuation bindings."""

    closures = {item.continuation_id: item for item in sidecar.closure_reviews}
    bindings: list[ContinuationBinding] = []
    for marker in sidecar.marker_reviews:
        if marker.disposition == ContinuationMarkerDisposition.INLINE_DIRECTION_MARKER:
            continue
        closure = closures.get(marker.continuation_id)
        if closure is not None:
            bindings.append(
                build_continuation_binding(
                    continuation_id=closure.continuation_id,
                    state=ContinuationBindingState.CLOSED,
                    source_projection_id=closure.source_projection_id,
                    source_manifest_sha256=closure.source_manifest_sha256,
                    source_page_num=closure.source_page_num,
                    source_section_id=closure.source_section_id,
                    source_anchor_span_ids=closure.source_anchor_span_ids,
                    adjacent_projection_id=closure.adjacent_projection_id,
                    adjacent_manifest_sha256=closure.adjacent_manifest_sha256,
                    adjacent_page_num=closure.adjacent_page_num,
                    adjacent_section_id=closure.adjacent_section_id,
                    closure_anchor_span_ids=closure.closure_anchor_span_ids,
                    closure_basis=closure.basis,
                )
            )
            continue
        bindings.append(
            build_continuation_binding(
                continuation_id=marker.continuation_id,
                state=ContinuationBindingState.MANUAL_REVIEW,
                source_projection_id=marker.projection_id,
                source_manifest_sha256=marker.source_manifest_sha256,
                source_page_num=marker.page_num,
                source_section_id=marker.section_id,
                source_anchor_span_ids=marker.anchor_span_ids,
                closure_basis=marker.basis,
                failure_code=ContinuationBindingFailureCode.AMBIGUOUS_CLOSURE,
            )
        )
    return tuple(bindings)


class TableGridIntegrityRecord(StrictModel):
    """Non-destructive assessment of one declared Evidence table grid."""

    table_evidence_id: str
    declared_row_count: int = Field(ge=1)
    declared_column_count: int = Field(ge=1)
    observed_row_count: int = Field(ge=0)
    observed_cell_count: int = Field(ge=0)
    status: TableGridIntegrityStatus
    issue_codes: tuple[str, ...] = ()
    repair_id: str | None = Field(default=None, pattern=r"^table-repair:[0-9a-f]{24}$")

    @model_validator(mode="after")
    def validate_status(self) -> "TableGridIntegrityRecord":
        if len(self.issue_codes) != len(set(self.issue_codes)):
            raise ValueError("table grid integrity issue codes must be unique")
        if self.status == TableGridIntegrityStatus.PASSED and (self.issue_codes or self.repair_id is not None):
            raise ValueError("passed table grids cannot contain integrity issue codes")
        if self.status == TableGridIntegrityStatus.MANUAL_REVIEW and not self.issue_codes:
            raise ValueError("manual-review table grids require integrity issue codes")
        if self.status == TableGridIntegrityStatus.MANUAL_REVIEW and self.repair_id is not None:
            raise ValueError("manual-review table grids cannot claim a reviewed repair")
        if self.status == TableGridIntegrityStatus.PASSED_WITH_REVIEWED_REPAIR and (not self.issue_codes or self.repair_id is None):
            raise ValueError("reviewed table repairs require original issue codes and a repair ID")
        return self
