"""step2a 取材义务模型族：锚点、覆盖义务、原子/文本/清单三类来源义务、续写绑定（拆分层 1）。

七个 StrictModel 都带 ``model_validator`` 自证：断言里的 sha256 必须与规范化文本逐字节对上，
对不上即拒收——**宁可拒开，不得发明**。``normalize_v25_source_assertion`` 是文本与清单两类
义务共用的归一化口径，与它们同住才不会出现「两处归一化各走一套」。
"""
from __future__ import annotations

import hashlib
import re
from pydantic import Field, model_validator

from ...evidence.schema import EvidenceKind
from ...materials.schema import StrictModel
from ..schema import FactType

from . import NumericTokenClass, Step2ACoverageObligationKind, normalized_compare_text

from .v25_support import _SHA256_PATTERN, _v25_id
from .v25_enums import V25AtomicConditionOperator


class V25AnchorContext(StrictModel):
    """One exact source anchor made visible to an obligation, never Golden data."""

    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    parent_evidence_id: str = Field(min_length=1)
    parent_evidence_kind: EvidenceKind
    canonical_text: str = Field(min_length=1)
    exact_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> "V25AnchorContext":
        if self.exact_text_sha256 != hashlib.sha256(self.canonical_text.encode("utf-8")).hexdigest():
            raise ValueError("V25 anchor context text hash must match the exact source text")
        return self


class V25CoverageObligationContext(StrictModel):
    """Auditable source text for one generic structural obligation."""

    context_id: str = Field(pattern=r"^v25-coverage-context:[0-9a-f]{24}$")
    obligation_id: str = Field(pattern=r"^coverage-obligation:[0-9a-f]{24}$")
    kind: Step2ACoverageObligationKind
    expected_fact_type: FactType
    required_primary_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    requires_all_items: bool
    table_evidence_id: str | None = None
    table_row_count: int | None = Field(default=None, ge=1)
    table_column_count: int | None = Field(default=None, ge=1)
    anchors: tuple[V25AnchorContext, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_context(self) -> "V25CoverageObligationContext":
        anchor_ids = tuple(item.anchor_span_id for item in self.anchors)
        if anchor_ids != self.required_primary_anchor_span_ids:
            raise ValueError("V25 obligation context anchors must exactly match its required source spans")
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("V25 obligation context anchors must be unique")
        if self.kind == Step2ACoverageObligationKind.COMPLETE_TABLE_COMPOSITION:
            if self.table_evidence_id is None or self.table_row_count is None or self.table_column_count is None:
                raise ValueError("V25 complete-table context requires source table geometry")
        elif self.kind == Step2ACoverageObligationKind.ATOMIC_TABLE_RULE:
            if self.table_evidence_id is None or self.table_row_count is not None or self.table_column_count is not None:
                raise ValueError("V25 atomic-table context requires an exact source table only")
        elif self.table_evidence_id is not None or self.table_row_count is not None or self.table_column_count is not None:
            raise ValueError("V25 closed continuation context cannot claim table geometry")
        payload = self.model_dump(mode="json", exclude={"context_id"})
        if self.context_id != _v25_id("v25-coverage-context", payload):
            raise ValueError("V25 coverage context ID must be deterministic")
        return self


class V25AtomicNumericExpression(StrictModel):
    """One source-owned quantitative expression used by an atomic obligation."""

    value: str = Field(min_length=1)
    classification: NumericTokenClass

    @model_validator(mode="after")
    def validate_expression(self) -> "V25AtomicNumericExpression":
        if self.value != self.value.strip() or self.classification != NumericTokenClass.QUANTITATIVE_VALUE:
            raise ValueError("V25 atomic numeric expressions must be complete quantitative source values")
        return self


class V25AtomicSourceObligation(StrictModel):
    """A complete atomic rule sourced from one or more deterministic spans.

    The optional second span is a punctuation-safe continuation fragment. It is
    retained as a source anchor, never used to rewrite the immutable table cell.
    """

    obligation_id: str = Field(pattern=r"^v25-atomic-obligation:[0-9a-f]{24}$")
    table_evidence_id: str
    expected_fact_type: FactType
    required_primary_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    anchors: tuple[V25AnchorContext, ...] = Field(min_length=1)
    required_source_assertion: str = Field(min_length=2)
    required_source_assertion_sha256: str = Field(pattern=_SHA256_PATTERN)
    numeric_expressions: tuple[V25AtomicNumericExpression, ...] = ()
    required_condition_operators: tuple[V25AtomicConditionOperator, ...] = ()
    continuation_binding_ids: tuple[str, ...] = ()
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_obligation(self) -> "V25AtomicSourceObligation":
        anchor_ids = tuple(item.anchor_span_id for item in self.anchors)
        if anchor_ids != self.required_primary_anchor_span_ids or len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("V25 atomic source anchors must exactly match the required model-selected spans")
        if self.required_source_assertion != self.required_source_assertion.strip():
            raise ValueError("V25 atomic source assertion must be stripped")
        if self.required_source_assertion_sha256 != hashlib.sha256(
            self.required_source_assertion.encode("utf-8")
        ).hexdigest():
            raise ValueError("V25 atomic source assertion hash must match the exact basis")
        joined_source = "\n".join(item.canonical_text for item in self.anchors)
        if normalized_compare_text(joined_source) != normalized_compare_text(self.required_source_assertion):
            raise ValueError("V25 atomic source assertion must exactly join its ordered source spans")
        if len(self.continuation_binding_ids) != len(set(self.continuation_binding_ids)):
            raise ValueError("V25 atomic continuation bindings must be unique")
        source_text = normalized_compare_text(self.required_source_assertion)
        if any(normalized_compare_text(item.value) not in source_text for item in self.numeric_expressions):
            raise ValueError("V25 atomic numeric expressions must remain in the complete source assertion")
        source_operators = {
            operator for operator in V25AtomicConditionOperator if operator.value in self.required_source_assertion
        }
        if not set(self.required_condition_operators) <= source_operators:
            raise ValueError("V25 atomic source assertion must retain every required condition operator")
        payload = self.model_dump(mode="json", exclude={"obligation_id"})
        if self.obligation_id != _v25_id("v25-atomic-obligation", payload):
            raise ValueError("V25 atomic source obligation ID must be deterministic")
        return self


def normalize_v25_source_assertion(value: str) -> str:
    """Normalize a structurally unambiguous OCR replacement glyph.

    ``14��B<18`` is a damaged rendering of the source interval operator. The
    transformation is admitted only between a numeric lower bound and a Latin
    comparison variable, so it cannot create arbitrary source text.
    """

    return re.sub(r"(?<=\d)\ufffd+(?=[A-Za-z])", "≤", value)


class V25TextSourceObligation(StrictModel):
    """A complete source-owned text/formula rule used by the diagram preflight.

    This is intentionally separate from table-only atomic obligations: the
    source may be a formula/list span while the reviewed diagram is projected
    locally from ``semantic_v2`` provenance rather than a fabricated text
    anchor.
    """

    obligation_id: str = Field(pattern=r"^v25-text-source-obligation:[0-9a-f]{24}$")
    expected_fact_type: FactType
    required_primary_anchor_span_ids: tuple[str, ...] = Field(min_length=1)
    anchors: tuple[V25AnchorContext, ...] = Field(min_length=1)
    required_source_assertion: str = Field(min_length=2)
    required_source_assertion_sha256: str = Field(pattern=_SHA256_PATTERN)
    model_assertion: str = Field(min_length=2)
    model_assertion_sha256: str = Field(pattern=_SHA256_PATTERN)
    numeric_expressions: tuple[V25AtomicNumericExpression, ...] = ()
    required_condition_operators: tuple[V25AtomicConditionOperator, ...] = ()
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_obligation(self) -> "V25TextSourceObligation":
        anchor_ids = tuple(item.anchor_span_id for item in self.anchors)
        if anchor_ids != self.required_primary_anchor_span_ids or len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("V25 text source anchors must exactly match the required model-selected spans")
        if self.required_source_assertion != self.required_source_assertion.strip():
            raise ValueError("V25 text source assertion must be stripped")
        if self.required_source_assertion_sha256 != hashlib.sha256(
            self.required_source_assertion.encode("utf-8")
        ).hexdigest():
            raise ValueError("V25 text source assertion hash must match the exact basis")
        if self.model_assertion != normalize_v25_source_assertion(self.required_source_assertion):
            raise ValueError("V25 text source model assertion must use only deterministic source normalization")
        if self.model_assertion_sha256 != hashlib.sha256(self.model_assertion.encode("utf-8")).hexdigest():
            raise ValueError("V25 text source model assertion hash must match its normalized source text")
        joined_source = "\n".join(item.canonical_text for item in self.anchors)
        if normalized_compare_text(joined_source) != normalized_compare_text(self.required_source_assertion):
            raise ValueError("V25 text source assertion must exactly join its ordered source spans")
        source_text = normalized_compare_text(self.model_assertion)
        if any(normalized_compare_text(item.value) not in source_text for item in self.numeric_expressions):
            raise ValueError("V25 text source numeric expressions must remain in the complete source assertion")
        source_operators = {
            operator for operator in V25AtomicConditionOperator if operator.value in self.required_source_assertion
        }
        if not set(self.required_condition_operators) <= source_operators:
            raise ValueError("V25 text source assertion must retain every required condition operator")
        payload = self.model_dump(mode="json", exclude={"obligation_id"})
        if self.obligation_id != _v25_id("v25-text-source-obligation", payload):
            raise ValueError("V25 text source obligation ID must be deterministic")
        return self


class V25ListSourceObligation(StrictModel):
    """A complete source-owned ordered-list Fact with exact endpoint anchors.

    This obligation describes list provenance only. It deliberately contains no
    Learning Golden title, target assertion, Card grouping, or presentation.
    ``local_only`` is explicit when no reviewed cross-page continuation exists.
    """

    obligation_id: str = Field(pattern=r"^v25-list-source-obligation:[0-9a-f]{24}$")
    expected_fact_type: FactType
    source_table_id: str = Field(min_length=1)
    source_cell_evidence_id: str = Field(min_length=1)
    source_row: int = Field(ge=0)
    source_column: int = Field(ge=0)
    required_primary_anchor_span_ids: tuple[str, ...] = Field(min_length=2)
    ordered_span_positions: tuple[int, ...] = Field(min_length=2)
    anchors: tuple[V25AnchorContext, ...] = Field(min_length=2)
    required_source_assertion: str = Field(min_length=2)
    required_source_assertion_sha256: str = Field(pattern=_SHA256_PATTERN)
    model_assertion: str = Field(min_length=2)
    model_assertion_sha256: str = Field(pattern=_SHA256_PATTERN)
    continuation_state: str = Field(pattern=r"^(local_only|reviewed_cross_page)$")
    continuation_binding_ids: tuple[str, ...] = ()
    basis: str = Field(min_length=24, max_length=1600)

    @model_validator(mode="after")
    def validate_obligation(self) -> "V25ListSourceObligation":
        anchor_ids = tuple(item.anchor_span_id for item in self.anchors)
        if self.expected_fact_type != FactType.ENUMERATION:
            raise ValueError("V25 list-source obligations must require enumeration Facts")
        if anchor_ids != self.required_primary_anchor_span_ids or len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("V25 list-source anchors must exactly match the required model-selected spans")
        if len(self.ordered_span_positions) != len(anchor_ids) or tuple(sorted(self.ordered_span_positions)) != self.ordered_span_positions:
            raise ValueError("V25 list-source anchor positions must be unique and strictly ordered")
        if len(self.ordered_span_positions) != len(set(self.ordered_span_positions)):
            raise ValueError("V25 list-source anchor positions must be unique")
        if any(item.parent_evidence_id != self.source_cell_evidence_id for item in self.anchors):
            raise ValueError("V25 list-source anchors must share the declared source cell Evidence")
        if len({item.source_manifest_sha256 for item in self.anchors}) != 1:
            raise ValueError("V25 list-source anchors must share one source manifest")
        if self.required_source_assertion != self.required_source_assertion.strip():
            raise ValueError("V25 list-source assertion must be stripped")
        if self.required_source_assertion_sha256 != hashlib.sha256(
            self.required_source_assertion.encode("utf-8")
        ).hexdigest():
            raise ValueError("V25 list-source assertion hash must match the exact basis")
        if self.model_assertion != normalize_v25_source_assertion(self.required_source_assertion):
            raise ValueError("V25 list-source model assertion must use only deterministic source normalization")
        if self.model_assertion_sha256 != hashlib.sha256(self.model_assertion.encode("utf-8")).hexdigest():
            raise ValueError("V25 list-source model assertion hash must match its normalized source text")
        joined_source = "\n".join(item.canonical_text for item in self.anchors)
        if normalized_compare_text(joined_source) != normalized_compare_text(self.required_source_assertion):
            raise ValueError("V25 list-source assertion must exactly join its ordered source spans")
        if self.continuation_state == "local_only" and self.continuation_binding_ids:
            raise ValueError("V25 local-only list obligations cannot claim continuation bindings")
        if self.continuation_state == "reviewed_cross_page" and not self.continuation_binding_ids:
            raise ValueError("V25 cross-page list obligations require reviewed bindings")
        if len(self.continuation_binding_ids) != len(set(self.continuation_binding_ids)):
            raise ValueError("V25 list-source continuation bindings must be unique")
        payload = self.model_dump(mode="json", exclude={"obligation_id"})
        if self.obligation_id != _v25_id("v25-list-source-obligation", payload):
            raise ValueError("V25 list-source obligation ID must be deterministic")
        return self


class V25ReviewedContinuationBinding(StrictModel):
    """A reviewed multi-page binding admitted into a generic V25 request."""

    binding_id: str = Field(pattern=r"^[a-z][a-z0-9._:-]*$")
    source_manifest_sha256s: tuple[str, ...] = Field(min_length=2)
    page_nums: tuple[int, ...] = Field(min_length=2)
    anchor_span_ids: tuple[str, ...] = Field(min_length=2)
    review_sidecar_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_binding(self) -> "V25ReviewedContinuationBinding":
        if len(self.source_manifest_sha256s) != len(set(self.source_manifest_sha256s)):
            raise ValueError("V25 reviewed continuation manifests must be unique")
        if len(self.page_nums) != len(set(self.page_nums)) or tuple(sorted(self.page_nums)) != self.page_nums:
            raise ValueError("V25 reviewed continuation pages must be unique and ordered")
        if any(right - left != 1 for left, right in zip(self.page_nums, self.page_nums[1:])):
            raise ValueError("V25 reviewed continuation pages must be adjacent")
        if len(self.anchor_span_ids) != len(set(self.anchor_span_ids)):
            raise ValueError("V25 reviewed continuation anchors must be unique")
        return self
