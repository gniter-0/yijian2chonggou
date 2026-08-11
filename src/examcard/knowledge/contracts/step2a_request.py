"""Step2ARequest (the giant validated request model) plus its payload serializer."""
from __future__ import annotations
from collections.abc import Iterable
from typing import Any

from pydantic import Field, model_validator

from ...materials.schema import StrictModel
from ..continuation.review import (
    ContinuationBindingReviewSidecar,
    ContinuationMarkerDisposition,
    ContinuationReviewState,
)

from .continuation import ContinuationBinding, ContinuationBindingState
from .enums import Step2Stage
from .hashing import _canonical_sha256
from .obligations import (
    Step2ACoverageObligationContextInput,
    Step2ACoverageObligationInput,
    derive_step2a_coverage_obligation_input,
)
from .projection import Step2ASemanticProjection
from .request_helpers import derive_step2a_coverage_obligation_context_input
from .request_identity import _SHA256_PATTERN, Step2RequestIdentity
from .responses import step2a_result_payload_model
from .table_inputs import Step2ASectionInput, TableContinuationRequestInput
from .versions import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    LEGACY_STEP2A_MODEL_INPUT_VERSION,
    LEGACY_STEP2A_REQUEST_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    MULTIPAGE_STEP2A_REQUEST_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
    PREVIOUS_STEP2A_REQUEST_VERSION,
    REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_MODEL_INPUT_VERSION,
    STEP2A_REQUEST_VERSION,
    STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,
    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
    TABLE_CONTINUATION_STEP2A_MODEL_INPUT_VERSION,
    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_MODEL_INPUT_VERSION,
    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_MODEL_INPUT_VERSION,
    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_MODEL_INPUT_VERSION,
    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
    _MULTIPAGE_STEP2A_REQUEST_VERSIONS,
    _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS,
)


class Step2ARequest(StrictModel):
    identity: Step2RequestIdentity
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    material_profile_id: str = Field(min_length=1)
    model_input_version: str = STEP2A_MODEL_INPUT_VERSION
    projection: Step2ASemanticProjection
    additional_projections: tuple[Step2ASemanticProjection, ...] = ()
    sections: tuple[Step2ASectionInput, ...]
    continuation_bindings: tuple[ContinuationBinding, ...] = ()
    continuation_review_sidecar: ContinuationBindingReviewSidecar | None = None
    continuation_review_sidecar_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    table_continuation_inputs: tuple[TableContinuationRequestInput, ...] = ()
    coverage_obligation_input: Step2ACoverageObligationInput | None = None
    coverage_obligation_context_input: Step2ACoverageObligationContextInput | None = None
    heading_only_allowed_anchor_span_ids: tuple[str, ...] = ()
    manual_reissue_reason: str | None = None

    @property
    def source_projections(self) -> tuple[Step2ASemanticProjection, ...]:
        return (self.projection, *self.additional_projections)

    @model_validator(mode="after")
    def validate_request(self) -> "Step2ARequest":
        section_ids = [section.section_id for section in self.sections]
        if self.identity.stage != Step2Stage.FACT_PLANNING or self.identity.request_version not in {
            LEGACY_STEP2A_REQUEST_VERSION,
            PREVIOUS_STEP2A_REQUEST_VERSION,
            STEP2A_REQUEST_VERSION,
            MULTIPAGE_STEP2A_REQUEST_VERSION,
            REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
            MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
            NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
            SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
            DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
            TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
            TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
            COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
            STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
            SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
        }:
            raise ValueError("Step 2A request uses an incompatible identity contract")
        expected_model_input_versions = {
            LEGACY_STEP2A_REQUEST_VERSION: LEGACY_STEP2A_MODEL_INPUT_VERSION,
            PREVIOUS_STEP2A_REQUEST_VERSION: LEGACY_STEP2A_MODEL_INPUT_VERSION,
            STEP2A_REQUEST_VERSION: STEP2A_MODEL_INPUT_VERSION,
            MULTIPAGE_STEP2A_REQUEST_VERSION: MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
            REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION: REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
            MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION: MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
            NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION: NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION,
            SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION: SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION,
            DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION: DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION,
            TABLE_CONTINUATION_STEP2A_REQUEST_VERSION: TABLE_CONTINUATION_STEP2A_MODEL_INPUT_VERSION,
            TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION: TABLE_COVERAGE_OBLIGATION_STEP2A_MODEL_INPUT_VERSION,
            TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION: TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_MODEL_INPUT_VERSION,
            TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION: TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_MODEL_INPUT_VERSION,
            COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION: COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,
            STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION: STRUCTURAL_INDEX_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,
            SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION: SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION,
        }
        if self.model_input_version != expected_model_input_versions[self.identity.request_version]:
            raise ValueError("Step 2A request uses an incompatible model-input projection")
        if self.identity.request_version in {
            MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
            NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
            SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
            DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
            TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
            TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
            COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
            STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
            SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
        }:
            expected_response_schema_sha256 = _canonical_sha256(
                step2a_result_payload_model(self.identity.request_version).model_json_schema()
            )
            if self.identity.response_schema_sha256 != expected_response_schema_sha256:
                raise ValueError("minimal Step 2A request identity uses an incompatible model response schema")
        if self.manual_reissue_reason != self.identity.manual_reissue_reason:
            raise ValueError("Step 2A manual reissue context must match its identity")
        if self.identity.request_version == LEGACY_STEP2A_REQUEST_VERSION and self.manual_reissue_reason is not None:
            raise ValueError("legacy Step 2A requests cannot carry manual reissue context")
        if not self.sections or len(section_ids) != len(set(section_ids)):
            raise ValueError("Step 2A requests require unique sections")
        projections = self.source_projections
        projection_ids = [item.projection_id for item in projections]
        projection_pages = [item.page_num for item in projections]
        projection_manifests = [item.source_manifest_sha256 for item in projections]
        if len(projection_ids) != len(set(projection_ids)):
            raise ValueError("Step 2A multi-page requests require unique source projections")
        if len(projection_pages) != len(set(projection_pages)):
            raise ValueError("Step 2A multi-page requests require unique source page numbers")
        if len(projection_manifests) != len(set(projection_manifests)):
            raise ValueError("Step 2A multi-page requests require unique source manifests")
        if any(
            item.source_document_sha256 != self.projection.source_document_sha256
            or item.content_policy_fingerprint_sha256 != self.projection.content_policy_fingerprint_sha256
            for item in self.additional_projections
        ):
            raise ValueError("Step 2A multi-page projections must share one document and Content Policy")
        if self.identity.request_version in _MULTIPAGE_STEP2A_REQUEST_VERSIONS:
            if not self.continuation_bindings:
                raise ValueError("multi-page Step 2A requests require continuation bindings")
            if any(item.state == ContinuationBindingState.CLOSED for item in self.continuation_bindings) and not self.additional_projections:
                raise ValueError("closed continuation bindings require an adjacent source projection")
            if self.identity.request_version in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS:
                if self.continuation_review_sidecar is None or self.continuation_review_sidecar_sha256 is None:
                    raise ValueError("V12 Step 2A requests require a reviewed continuation sidecar and file hash")
                if self.continuation_review_sidecar.state != ContinuationReviewState.REVIEWED:
                    raise ValueError("V12 Step 2A requests require a reviewed continuation sidecar")
            elif self.continuation_review_sidecar is not None or self.continuation_review_sidecar_sha256 is not None:
                raise ValueError("V11 Step 2A requests cannot carry a reviewed continuation sidecar")
        elif self.additional_projections or self.continuation_bindings:
            raise ValueError("pre-V11 Step 2A requests cannot carry multi-page continuation fields")
        elif self.continuation_review_sidecar is not None or self.continuation_review_sidecar_sha256 is not None:
            raise ValueError("pre-V12 Step 2A requests cannot carry a reviewed continuation sidecar")
        if {section.page_num for section in self.sections} != set(projection_pages):
            raise ValueError("Step 2A request sections must bind every projected source page")
        projected_evidence_ids = {
            evidence_id
            for item in projections
            for evidence_id in (
                set(item.direct_evidence_ids)
                | {evidence_id for diagram in item.controlled_diagrams for evidence_id in diagram.source_evidence_ids}
            )
        }
        request_evidence_ids = {evidence_id for section in self.sections for evidence_id in section.evidence_ids}
        if request_evidence_ids - projected_evidence_ids:
            raise ValueError("Step 2A request cannot include Evidence outside its semantic projection")
        section_span_ids = {span_id for section in self.sections for span_id in section.anchor_span_ids}
        projection_span_ids = {span.anchor_span_id for item in projections for span in item.anchor_spans}
        if section_span_ids != projection_span_ids:
            raise ValueError("Step 2A request anchor spans must exactly match its semantic projection")
        controlled_input_ids = [item.input_id for item in self.table_continuation_inputs]
        if len(controlled_input_ids) != len(set(controlled_input_ids)):
            raise ValueError("controlled table continuation request inputs must be unique")
        if self.identity.request_version in {
            TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
            TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
            COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
            STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
            SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
        }:
            if len(self.table_continuation_inputs) != 1:
                raise ValueError("V17+ Step 2A requests require exactly one reviewed controlled table continuation input")
            controlled = self.table_continuation_inputs[0]
            if (
                self.identity.request_version in {
                    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
                }
                and controlled.input_version != STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION
            ):
                raise ValueError("V20/V21 Step 2A requests require the V2 stiffness atomic-threshold controlled table input")
            if (
                self.identity.request_version not in {
                    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
                }
                and controlled.input_version == STIFFNESS_ATOMIC_THRESHOLD_TABLE_CONTINUATION_REQUEST_INPUT_VERSION
            ):
                raise ValueError("V17-V19 Step 2A requests cannot reuse the V20 controlled table input")
            if (
                controlled.sample_id != self.sample_id
                or controlled.source_document_sha256 != self.projection.source_document_sha256
                or controlled.content_policy_id != self.projection.content_policy_id
                or controlled.content_policy_revision != self.projection.content_policy_revision
                or controlled.content_policy_fingerprint_sha256 != self.projection.content_policy_fingerprint_sha256
            ):
                raise ValueError("controlled table continuation input must bind the request sample, document, and Content Policy")
            ordinary_section_ids = set(section_ids)
            if ordinary_section_ids & set(controlled.controlled_section_ids):
                raise ValueError("controlled table source sections must remain distinct from ordinary request sections")
            controlled_span_ids = {span.anchor_span_id for span in controlled.anchor_spans}
            if controlled_span_ids & projection_span_ids:
                raise ValueError("controlled table anchors must be disjoint from ordinary semantic projections")
            controlled_pages = {item.page_num: item for item in controlled.source_pages}
            first_page = controlled.source_pages[0]
            second_page = controlled.source_pages[1]
            projection_by_page = {item.page_num: item for item in projections}
            if (
                projection_by_page.get(first_page.page_num) is None
                or projection_by_page[first_page.page_num].source_manifest_sha256 != first_page.source_manifest_sha256
                or second_page.page_num in projection_by_page
            ):
                raise ValueError("V17 admits the later page only through the controlled table continuation input")
            if set(controlled_pages) != {first_page.page_num, second_page.page_num}:
                raise ValueError("controlled table continuation source pages must be unique")
        elif self.table_continuation_inputs:
            raise ValueError("pre-V17 Step 2A requests cannot carry controlled table continuation inputs")
        if self.identity.request_version in {
            TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
            TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
            TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
            COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
            STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
            SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
        }:
            expected_obligations = derive_step2a_coverage_obligation_input(
                source_document_sha256=self.projection.source_document_sha256,
                content_policy_id=self.projection.content_policy_id,
                content_policy_revision=self.projection.content_policy_revision,
                content_policy_fingerprint_sha256=self.projection.content_policy_fingerprint_sha256,
                sections=self.sections,
                continuation_bindings=self.continuation_bindings,
                table_continuation_inputs=self.table_continuation_inputs,
            )
            if self.coverage_obligation_input is None:
                raise ValueError("V18-V21 Step 2A requests require a deterministic coverage-obligation input")
            if self.coverage_obligation_input != expected_obligations:
                raise ValueError("V18-V21 coverage obligations must be derived exactly from request tables and closed continuations")
            if self.identity.request_version in {
                TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
                TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
                SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
            }:
                if self.coverage_obligation_context_input is None:
                    raise ValueError("V19-V21 Step 2A requests require source-derived coverage-obligation context")
                if self.coverage_obligation_context_input != derive_step2a_coverage_obligation_context_input(request=self):
                    raise ValueError("V19-V21 coverage-obligation context must be derived exactly from request-local source spans")
            elif self.coverage_obligation_context_input is not None:
                raise ValueError("V18 requests cannot carry V19/V20 coverage-obligation context")
        elif self.coverage_obligation_input is not None or self.coverage_obligation_context_input is not None:
            raise ValueError("pre-V18 Step 2A requests cannot carry coverage-obligation inputs")
        context_only_span_ids = {
            span.anchor_span_id
            for section in self.sections
            for item in section.text_evidence
            if item.context_only
            for span in item.anchor_spans
        }
        if len(self.heading_only_allowed_anchor_span_ids) != len(set(self.heading_only_allowed_anchor_span_ids)):
            raise ValueError("Step 2A heading-only allowances must be unique")
        if not set(self.heading_only_allowed_anchor_span_ids) <= context_only_span_ids:
            raise ValueError("Step 2A heading-only allowances must bind context-only anchor spans")
        continuation_records = {
            record.continuation_id: (record, item)
            for item in projections
            for record in item.continuation_records
        }
        binding_ids = [item.binding_id for item in self.continuation_bindings]
        source_continuation_ids = [item.continuation_id for item in self.continuation_bindings]
        if len(binding_ids) != len(set(binding_ids)) or len(source_continuation_ids) != len(set(source_continuation_ids)):
            raise ValueError("multi-page continuation bindings must be unique by binding and source continuation")
        projections_by_id = {item.projection_id: item for item in projections}
        sections_by_id = {item.section_id: item for item in self.sections}
        spans_by_projection = {item.projection_id: {span.anchor_span_id for span in item.anchor_spans} for item in projections}
        spans_by_id = {
            span.anchor_span_id: span
            for projection in projections
            for span in projection.anchor_spans
        }

        def controlled_table_page_rebind(projection: Step2ASemanticProjection) -> bool:
            return (
                self.identity.request_version
                in {
                TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
                TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
                TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
                TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
            }
                and any(
                    source_page.page_num == projection.page_num
                    and source_page.source_manifest_sha256 == projection.source_manifest_sha256
                    for controlled in self.table_continuation_inputs
                    for source_page in controlled.source_pages
                )
            )

        reviewed_marker_dispositions: dict[str, ContinuationMarkerDisposition] = {}
        reviewed_closures = {}
        if self.identity.request_version in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS:
            sidecar = self.continuation_review_sidecar
            assert sidecar is not None
            if sidecar.sample_id != self.sample_id or sidecar.source_document_sha256 != self.projection.source_document_sha256:
                raise ValueError("reviewed continuation sidecar does not bind the Step 2A sample and source document")
            marker_reviews = {item.continuation_id: item for item in sidecar.marker_reviews}
            if set(marker_reviews) != set(continuation_records):
                raise ValueError("reviewed continuation sidecar must classify every request continuation record exactly once")
            for continuation_id, (record, source_projection) in continuation_records.items():
                review = marker_reviews[continuation_id]
                source_section = sections_by_id.get(review.section_id)
                if source_section is None and controlled_table_page_rebind(source_projection):
                    candidates = tuple(
                        section
                        for section in self.sections
                        if section.page_num == source_projection.page_num
                        and set(review.anchor_span_ids) <= set(section.anchor_span_ids)
                    )
                    source_section = candidates[0] if len(candidates) == 1 else None
                expected_evidence_ids = tuple(
                    dict.fromkeys(spans_by_id[span_id].parent_evidence_id for span_id in record.anchor_span_ids)
                )
                if (
                    (review.projection_id != source_projection.projection_id and not controlled_table_page_rebind(source_projection))
                    or review.source_manifest_sha256 != source_projection.source_manifest_sha256
                    or review.page_num != source_projection.page_num
                    or review.anchor_span_ids != record.anchor_span_ids
                    or not set(review.marker_token_span_ids) <= set(record.anchor_span_ids)
                    or any(
                        not any(marker in spans_by_id[span_id].canonical_text for marker in ("↓", "续下页", "下页续"))
                        for span_id in review.marker_token_span_ids
                    )
                    or review.source_evidence_ids != expected_evidence_ids
                    or source_section is None
                    or source_section.page_num != source_projection.page_num
                    or not set(review.anchor_span_ids) <= set(source_section.anchor_span_ids)
                ):
                    raise ValueError("reviewed continuation marker sidecar binding does not match the request projection")
                reviewed_marker_dispositions[continuation_id] = review.disposition
            reviewed_closures = {item.continuation_id: item for item in sidecar.closure_reviews}
            for continuation_id, review in reviewed_closures.items():
                record, source_projection = continuation_records[continuation_id]
                adjacent_projection = projections_by_id.get(review.adjacent_projection_id)
                adjacent_section = sections_by_id.get(review.adjacent_section_id)
                if adjacent_projection is None and self.identity.request_version in {
                    TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
                    TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
                    TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
                    TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                    STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
                }:
                    candidates = tuple(
                        projection
                        for projection in projections
                        if projection.page_num == review.adjacent_page_num
                        and projection.source_manifest_sha256 == review.adjacent_manifest_sha256
                    )
                    adjacent_projection = candidates[0] if len(candidates) == 1 else None
                if adjacent_section is None and adjacent_projection is not None and controlled_table_page_rebind(adjacent_projection):
                    candidates = tuple(
                        section
                        for section in self.sections
                        if section.page_num == adjacent_projection.page_num
                        and set(review.closure_anchor_span_ids) <= set(section.anchor_span_ids)
                    )
                    adjacent_section = candidates[0] if len(candidates) == 1 else None
                if (
                    (review.source_projection_id != source_projection.projection_id and not controlled_table_page_rebind(source_projection))
                    or review.source_manifest_sha256 != source_projection.source_manifest_sha256
                    or review.source_page_num != source_projection.page_num
                    or review.source_anchor_span_ids != record.anchor_span_ids
                    or adjacent_projection is None
                    or adjacent_section is None
                    or (review.adjacent_projection_id != adjacent_projection.projection_id and not controlled_table_page_rebind(adjacent_projection))
                    or review.adjacent_manifest_sha256 != adjacent_projection.source_manifest_sha256
                    or review.adjacent_page_num != adjacent_projection.page_num
                    or adjacent_section.page_num != adjacent_projection.page_num
                    or review.adjacent_page_num != record.required_adjacent_page_num
                    or not set(review.closure_anchor_span_ids) <= spans_by_projection[adjacent_projection.projection_id]
                    or not set(review.closure_anchor_span_ids) <= set(adjacent_section.anchor_span_ids)
                ):
                    raise ValueError("reviewed continuation closure sidecar binding does not match the request projection")
        if self.identity.request_version == MULTIPAGE_STEP2A_REQUEST_VERSION:
            expected_binding_continuations = set(continuation_records)
        elif self.identity.request_version in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS:
            expected_binding_continuations = {
                continuation_id
                for continuation_id, disposition in reviewed_marker_dispositions.items()
                if disposition != ContinuationMarkerDisposition.INLINE_DIRECTION_MARKER
            }
        else:
            expected_binding_continuations = set()
        if self.identity.request_version in _MULTIPAGE_STEP2A_REQUEST_VERSIONS and set(source_continuation_ids) != expected_binding_continuations:
            raise ValueError("multi-page Step 2A requests require one binding for every non-inline continuation record")
        for binding in self.continuation_bindings:
            source = continuation_records.get(binding.continuation_id)
            if source is None:
                raise ValueError("continuation binding references an unknown source continuation record")
            record, source_projection = source
            if (
                binding.source_projection_id != source_projection.projection_id
                or binding.source_manifest_sha256 != source_projection.source_manifest_sha256
                or binding.source_page_num != source_projection.page_num
                or binding.source_anchor_span_ids != record.anchor_span_ids
            ):
                raise ValueError("continuation binding must exactly bind its source projection and marker spans")
            source_section = sections_by_id.get(binding.source_section_id)
            if source_section is None or source_section.page_num != source_projection.page_num:
                raise ValueError("continuation binding source section is absent from the source projection page")
            if not set(binding.source_anchor_span_ids) <= set(source_section.anchor_span_ids):
                raise ValueError("continuation binding source spans must belong to its declared source section")
            if self.identity.request_version in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS:
                disposition = reviewed_marker_dispositions[binding.continuation_id]
                if disposition == ContinuationMarkerDisposition.INLINE_DIRECTION_MARKER:
                    raise ValueError("reviewed inline direction markers cannot retain continuation bindings")
                if disposition == ContinuationMarkerDisposition.UNRESOLVED and binding.state == ContinuationBindingState.CLOSED:
                    raise ValueError("unresolved reviewed continuation markers cannot close an all-items Fact")
            if binding.state != ContinuationBindingState.CLOSED:
                continue
            if self.identity.request_version in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS:
                closure_review = reviewed_closures.get(binding.continuation_id)
                if closure_review is None:
                    raise ValueError("closed V12 continuation bindings require an exact reviewed closure sidecar entry")
                permits_controlled_adjacent_rebind = (
                    self.identity.request_version
                    in {
                        TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
                        TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
                        TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
                        TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                        STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                        SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
                    }
                    and binding.adjacent_projection_id is not None
                    and binding.adjacent_projection_id in projections_by_id
                    and controlled_table_page_rebind(projections_by_id[binding.adjacent_projection_id])
                )
                if (
                    binding.source_projection_id != closure_review.source_projection_id
                    or binding.source_manifest_sha256 != closure_review.source_manifest_sha256
                    or binding.source_page_num != closure_review.source_page_num
                    or binding.source_section_id != closure_review.source_section_id
                    or binding.source_anchor_span_ids != closure_review.source_anchor_span_ids
                    or binding.adjacent_manifest_sha256 != closure_review.adjacent_manifest_sha256
                    or binding.adjacent_page_num != closure_review.adjacent_page_num
                    or binding.closure_anchor_span_ids != closure_review.closure_anchor_span_ids
                    or binding.closure_basis != closure_review.basis
                    or (
                        not permits_controlled_adjacent_rebind
                        and (
                            binding.adjacent_projection_id != closure_review.adjacent_projection_id
                            or binding.adjacent_section_id != closure_review.adjacent_section_id
                        )
                    )
                ):
                    raise ValueError("closed V12 continuation binding does not exactly match its reviewed closure sidecar entry")
            adjacent_projection = projections_by_id.get(binding.adjacent_projection_id or "")
            adjacent_section = sections_by_id.get(binding.adjacent_section_id or "")
            if adjacent_projection is None or adjacent_section is None:
                raise ValueError("closed continuation bindings require the declared adjacent projection and section")
            if (
                binding.adjacent_manifest_sha256 != adjacent_projection.source_manifest_sha256
                or binding.adjacent_page_num != adjacent_projection.page_num
                or adjacent_section.page_num != adjacent_projection.page_num
                or adjacent_projection.page_num != record.required_adjacent_page_num
            ):
                raise ValueError("closed continuation bindings require the immediate adjacent source page")
            if adjacent_projection.projection_id == source_projection.projection_id:
                raise ValueError("closed continuation bindings cannot self-bind a source page")
            if adjacent_projection.continuation_records and self.identity.request_version not in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS:
                raise ValueError("closed continuation bindings require an adjacent page with no unresolved continuation marker")
            if self.identity.request_version in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS and any(
                reviewed_marker_dispositions.get(record.continuation_id)
                != ContinuationMarkerDisposition.INLINE_DIRECTION_MARKER
                for record in adjacent_projection.continuation_records
            ):
                raise ValueError("closed V12 continuation bindings require every adjacent marker to be reviewed inline")
            if not set(binding.closure_anchor_span_ids) <= spans_by_projection[adjacent_projection.projection_id] or not set(binding.closure_anchor_span_ids) <= set(adjacent_section.anchor_span_ids):
                raise ValueError("closed continuation binding closure spans must belong to the declared adjacent section")
        if (
            self.identity.projection_id != self.projection.projection_id
            or self.identity.projection_sha256 != self.projection.fingerprint_sha256
            or self.identity.source_manifest_sha256 != self.projection.source_manifest_sha256
            or self.identity.content_policy_fingerprint_sha256 != self.projection.content_policy_fingerprint_sha256
        ):
            raise ValueError("Step 2A request identity does not bind its semantic projection")
        payload = _step2a_payload(
            sample_id=self.sample_id,
            material_profile_id=self.material_profile_id,
            model_input_version=self.model_input_version,
            projection=self.projection,
            additional_projections=self.additional_projections,
            sections=self.sections,
            continuation_bindings=self.continuation_bindings,
            heading_only_allowed_anchor_span_ids=self.heading_only_allowed_anchor_span_ids,
            manual_reissue_reason=self.manual_reissue_reason,
            include_manual_reissue_reason=self.identity.request_version != LEGACY_STEP2A_REQUEST_VERSION,
            include_phase2r054_fields=self.identity.request_version
            in {STEP2A_REQUEST_VERSION, *_MULTIPAGE_STEP2A_REQUEST_VERSIONS},
            include_multipage_fields=self.identity.request_version
            in _MULTIPAGE_STEP2A_REQUEST_VERSIONS,
            continuation_review_sidecar=self.continuation_review_sidecar,
            continuation_review_sidecar_sha256=self.continuation_review_sidecar_sha256,
            table_continuation_inputs=self.table_continuation_inputs,
            coverage_obligation_input=self.coverage_obligation_input,
            coverage_obligation_context_input=self.coverage_obligation_context_input,
            include_reviewed_continuation_fields=self.identity.request_version in _REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS,
            include_table_continuation_fields=self.identity.request_version
            in {
                TABLE_CONTINUATION_STEP2A_REQUEST_VERSION,
                TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
                TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
                TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
            },
            include_coverage_obligation_fields=self.identity.request_version
            == TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION,
            include_coverage_obligation_context_fields=self.identity.request_version
            in {
                TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION,
                TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION,
                COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
                STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION,
                SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
            },
        )
        if self.identity.payload_sha256 != _canonical_sha256(payload):
            raise ValueError("Step 2A request payload no longer matches its cache identity")
        return self


def _step2a_payload(
    *,
    sample_id: str,
    material_profile_id: str,
    model_input_version: str,
    projection: Step2ASemanticProjection,
    sections: Iterable[Step2ASectionInput],
    additional_projections: Iterable[Step2ASemanticProjection] = (),
    continuation_bindings: Iterable[ContinuationBinding] = (),
    continuation_review_sidecar: ContinuationBindingReviewSidecar | None = None,
    continuation_review_sidecar_sha256: str | None = None,
    table_continuation_inputs: Iterable[TableContinuationRequestInput] = (),
    coverage_obligation_input: Step2ACoverageObligationInput | None = None,
    coverage_obligation_context_input: Step2ACoverageObligationContextInput | None = None,
    heading_only_allowed_anchor_span_ids: Iterable[str] = (),
    manual_reissue_reason: str | None = None,
    include_manual_reissue_reason: bool = True,
    include_phase2r054_fields: bool = True,
    include_multipage_fields: bool = False,
    include_reviewed_continuation_fields: bool = False,
    include_table_continuation_fields: bool = False,
    include_coverage_obligation_fields: bool = False,
    include_coverage_obligation_context_fields: bool = False,
) -> dict[str, Any]:
    projection_payload = projection.model_dump(mode="json")
    additional_projection_payloads = [item.model_dump(mode="json") for item in additional_projections]
    section_payloads = [section.model_dump(mode="json") for section in sections]
    if not include_phase2r054_fields:
        projection_payload.pop("continuation_records", None)
        for section_payload in section_payloads:
            for table_payload in section_payload.get("tables", []):
                table_payload.pop("atomic_subfact_requirements", None)
    payload = {
        "sample_id": sample_id,
        "material_profile_id": material_profile_id,
        "model_input_version": model_input_version,
        "projection": projection_payload,
        "sections": section_payloads,
        "heading_only_allowed_anchor_span_ids": list(heading_only_allowed_anchor_span_ids),
    }
    if include_manual_reissue_reason:
        payload["manual_reissue_reason"] = manual_reissue_reason
    if include_multipage_fields:
        payload["additional_projections"] = additional_projection_payloads
        payload["continuation_bindings"] = [item.model_dump(mode="json") for item in continuation_bindings]
    if include_reviewed_continuation_fields:
        if continuation_review_sidecar is None or continuation_review_sidecar_sha256 is None:
            raise ValueError("reviewed continuation request payload requires sidecar and file hash")
        payload["continuation_review_sidecar"] = continuation_review_sidecar.model_dump(mode="json")
        payload["continuation_review_sidecar_sha256"] = continuation_review_sidecar_sha256
    if include_table_continuation_fields:
        payload["table_continuation_inputs"] = [
            item.model_dump(mode="json") for item in table_continuation_inputs
        ]
    if include_coverage_obligation_fields:
        if coverage_obligation_input is None:
            raise ValueError("coverage-obligation request payload requires its deterministic input")
        payload["coverage_obligation_input"] = coverage_obligation_input.model_dump(mode="json")
    if include_coverage_obligation_context_fields:
        if coverage_obligation_context_input is None:
            raise ValueError("coverage-obligation context request payload requires its deterministic input")
        payload["coverage_obligation_context_input"] = coverage_obligation_context_input.model_dump(mode="json")
    return payload
