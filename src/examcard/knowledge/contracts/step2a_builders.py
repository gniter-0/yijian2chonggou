"""Builders assembling Step 2A sections/bindings/requests from Evidence units."""
from __future__ import annotations
from collections.abc import Iterable, Mapping
from pathlib import Path

from ...evidence.schema import EvidenceKind, EvidenceUnit
from ..continuation.review import (
    ContinuationBindingReviewSidecar,
    load_continuation_review_sidecar,
    sha256_file,
    verify_continuation_review_source_pdf,
)

from .anchors import _TABLE_KINDS, EvidenceAnchorSpan
from .continuation import (
    ContinuationBinding,
    ContinuationBindingFailureCode,
    ContinuationBindingState,
    build_continuation_binding,
    build_reviewed_continuation_bindings,
)
from .enums import Step2DecoderConfig, Step2Stage
from .hashing import _OFFLINE_RELAY_BASE_URL, _canonical_sha256
from .obligations import derive_step2a_coverage_obligation_input
from .projection import ReviewedStructuralDiagramInput, Step2ASemanticProjection
from .request_helpers import derive_step2a_coverage_obligation_context_input
from .request_identity import Step2RequestIdentity, make_step2_request_identity
from .responses import step2a_result_payload_model
from .step2a_request import Step2ARequest, _step2a_payload
from .table_inputs import (
    SectionTextInput,
    Step2ASectionInput,
    StructuredTableInput,
    TableCellInput,
    TableContinuationRequestInput,
    TableRowInput,
    _make_section_id,
    derive_table_atomic_subfact_requirements,
)
from .versions import (
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION,
    COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION,
    CONTROLLED_DIAGRAM_ANCHOR_STEP2A_MODEL_INPUT_VERSION,
    CONTROLLED_DIAGRAM_ANCHOR_STEP2A_REQUEST_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION,
    DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    MULTIPAGE_STEP2A_REQUEST_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION,
    NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION,
    REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION,
    REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION,
    SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION,
    SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION,
    STEP2A_MODEL_INPUT_VERSION,
    STEP2A_REQUEST_VERSION,
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
)


def build_step2a_section(
    *,
    units: Iterable[EvidenceUnit],
    anchor_spans: Iterable[EvidenceAnchorSpan],
    controlled_diagrams: Iterable[ReviewedStructuralDiagramInput] = (),
    context_only_evidence_ids: Iterable[str] = (),
    table_cell_span_overrides: Mapping[str, tuple[int, int]] | None = None,
    controlled_diagram_anchor_spans: Iterable[EvidenceAnchorSpan] = (),
) -> Step2ASectionInput:
    """Build one page-local 2A section without flattening table text."""
    ordered = tuple(sorted(units, key=lambda unit: unit.order))
    frozen_diagrams = tuple(controlled_diagrams)
    frozen_spans = tuple(anchor_spans)
    frozen_diagram_anchor_spans = tuple(controlled_diagram_anchor_spans)
    context_only_ids = frozenset(context_only_evidence_ids)
    span_overrides = dict(table_cell_span_overrides or {})
    if not ordered:
        raise ValueError("Step 2A sections require Evidence")
    page_numbers = {unit.locator.page_num for unit in ordered}
    if len(page_numbers) != 1:
        raise ValueError("Step 2A section builder requires one page at a time")
    page_num = next(iter(page_numbers))
    by_parent: dict[str, list[EvidenceUnit]] = {}
    for unit in ordered:
        if unit.parent_id:
            by_parent.setdefault(unit.parent_id, []).append(unit)
    spans_by_parent: dict[str, tuple[EvidenceAnchorSpan, ...]] = {}
    for span in frozen_spans:
        spans_by_parent[span.parent_evidence_id] = (*spans_by_parent.get(span.parent_evidence_id, ()), span)

    text_items = tuple(
        SectionTextInput(
            evidence_id=unit.evidence_id,
            page_num=page_num,
            kind=unit.kind,
            canonical_text=unit.canonical_text,
            anchor_spans=spans_by_parent.get(unit.evidence_id, ()),
            context_only=unit.evidence_id in context_only_ids,
        )
        for unit in ordered
        if unit.kind not in _TABLE_KINDS and unit.canonical_text
    )
    tables: list[StructuredTableInput] = []
    for table in (unit for unit in ordered if unit.kind == EvidenceKind.TABLE):
        rows: list[TableRowInput] = []
        for row in sorted(by_parent.get(table.evidence_id, []), key=lambda unit: unit.order):
            if row.kind != EvidenceKind.TABLE_ROW:
                continue
            cells = tuple(
                TableCellInput(
                    evidence_id=cell.evidence_id,
                    row_index=int(cell.metadata["row"]),
                    column_index=int(cell.metadata["column"]),
                    row_span=span_overrides.get(cell.evidence_id, (int(cell.metadata.get("row_span", 1)), int(cell.metadata.get("col_span", 1))))[0],
                    col_span=span_overrides.get(cell.evidence_id, (int(cell.metadata.get("row_span", 1)), int(cell.metadata.get("col_span", 1))))[1],
                    canonical_text=cell.canonical_text,
                    anchor_spans=spans_by_parent.get(cell.evidence_id, ()),
                )
                for cell in sorted(by_parent.get(row.evidence_id, []), key=lambda unit: unit.order)
                if cell.kind == EvidenceKind.TABLE_CELL
            )
            rows.append(
                TableRowInput(
                    evidence_id=row.evidence_id,
                    row_index=int(row.metadata["row"]),
                    cells=cells,
                )
            )
        if rows:
            provisional_table = StructuredTableInput(
                    table_evidence_id=table.evidence_id,
                    page_num=page_num,
                    row_count=int(table.metadata["rows"]),
                    column_count=int(table.metadata["columns"]),
                    rows=tuple(rows),
                )
            tables.append(
                provisional_table.model_copy(
                    update={"atomic_subfact_requirements": derive_table_atomic_subfact_requirements(provisional_table)}
                )
            )
        else:
            raise ValueError(f"table Evidence {table.evidence_id} has no structured row/cell inputs")
    all_ids = [item.evidence_id for item in text_items] + [table.table_evidence_id for table in tables]
    all_ids.extend(row.evidence_id for table in tables for row in table.rows)
    all_ids.extend(cell_id for table in tables for cell_id in table.cell_ids)
    all_ids.extend(evidence_id for diagram in frozen_diagrams for evidence_id in diagram.source_evidence_ids)
    headings = tuple(item.evidence_id for item in text_items if item.kind == EvidenceKind.HEADING)
    unknown_context = context_only_ids - {item.evidence_id for item in text_items}
    if unknown_context:
        raise ValueError("Step 2A context-only Evidence must remain direct textual inputs")
    return Step2ASectionInput(
        section_id=_make_section_id(page_num=page_num, evidence_ids=tuple(dict.fromkeys(all_ids))),
        page_num=page_num,
        heading_evidence_ids=headings,
        context_only_evidence_ids=tuple(item.evidence_id for item in text_items if item.context_only),
        text_evidence=text_items,
        tables=tuple(tables),
        controlled_diagrams=frozen_diagrams,
        controlled_diagram_anchor_spans=frozen_diagram_anchor_spans,
    )


def _default_manual_continuation_bindings(
    *,
    projection: Step2ASemanticProjection,
    additional_projections: tuple[Step2ASemanticProjection, ...],
    sections: tuple[Step2ASectionInput, ...],
) -> tuple[ContinuationBinding, ...]:
    """Retain unresolved source continuations explicitly when no closure is supplied."""

    projections = (projection, *additional_projections)
    bindings: list[ContinuationBinding] = []
    for source_projection in projections:
        for record in source_projection.continuation_records:
            source_sections = tuple(
                section
                for section in sections
                if section.page_num == source_projection.page_num
                and set(record.anchor_span_ids) <= set(section.anchor_span_ids)
            )
            if len(source_sections) != 1:
                raise ValueError("source continuation marker must belong to exactly one request section")
            adjacent_exists = any(item.page_num == record.required_adjacent_page_num for item in projections)
            candidate_exists = bool(additional_projections)
            failure = (
                ContinuationBindingFailureCode.AMBIGUOUS_CLOSURE
                if adjacent_exists
                else ContinuationBindingFailureCode.NON_ADJACENT_PAGE
                if candidate_exists
                else ContinuationBindingFailureCode.MISSING_ADJACENT_PAGE
            )
            bindings.append(
                build_continuation_binding(
                    continuation_id=record.continuation_id,
                    state=ContinuationBindingState.MANUAL_REVIEW,
                    source_projection_id=source_projection.projection_id,
                    source_manifest_sha256=source_projection.source_manifest_sha256,
                    source_page_num=source_projection.page_num,
                    source_section_id=source_sections[0].section_id,
                    source_anchor_span_ids=record.anchor_span_ids,
                    closure_basis="No reviewed adjacent-page closure binding is available for this source continuation.",
                    failure_code=failure,
                )
            )
    return tuple(bindings)


def build_step2a_request(
    *,
    sample_id: str,
    material_profile_id: str,
    projection: Step2ASemanticProjection,
    sections: Iterable[Step2ASectionInput],
    model: str,
    relay_base_url: str = _OFFLINE_RELAY_BASE_URL,
    prompt_template: str | None = None,
    decoder: Step2DecoderConfig | None = None,
    heading_only_allowed_anchor_span_ids: Iterable[str] = (),
    manual_reissue_reason: str | None = None,
    additional_projections: Iterable[Step2ASemanticProjection] = (),
    continuation_bindings: Iterable[ContinuationBinding] = (),
    continuation_review_sidecar: ContinuationBindingReviewSidecar | None = None,
    continuation_review_sidecar_path: Path | None = None,
    continuation_repository_root: Path | None = None,
    table_continuation_inputs: Iterable[TableContinuationRequestInput] = (),
    use_minimal_model_contract: bool = False,
    use_numeric_expression_grounding_contract: bool = False,
    use_single_role_anchor_contract: bool = False,
    use_deterministic_numeric_claim_contract: bool = False,
    use_table_continuation_contract: bool = False,
    use_table_coverage_obligation_contract: bool = False,
    use_table_coverage_obligation_context_contract: bool = False,
    use_table_stiffness_atomic_threshold_coverage_contract: bool = False,
    use_composition_numeric_occurrence_contract: bool = False,
    use_structural_index_occurrence_contract: bool = False,
    use_source_owned_structural_projection_contract: bool = False,
) -> Step2ARequest:
    frozen_sections = tuple(sections)
    frozen_heading_only_allowances = tuple(heading_only_allowed_anchor_span_ids)
    frozen_additional_projections = tuple(additional_projections)
    supplied_bindings = tuple(continuation_bindings)
    frozen_table_continuation_inputs = tuple(table_continuation_inputs)
    is_multipage = bool(frozen_additional_projections or supplied_bindings)
    has_controlled_diagram_anchors = any(section.controlled_diagram_anchor_spans for section in frozen_sections)
    if has_controlled_diagram_anchors and (
        is_multipage
        or continuation_review_sidecar is not None
        or continuation_review_sidecar_path is not None
        or frozen_table_continuation_inputs
        or use_minimal_model_contract
        or use_numeric_expression_grounding_contract
        or use_single_role_anchor_contract
        or use_deterministic_numeric_claim_contract
        or use_table_continuation_contract
        or use_table_coverage_obligation_contract
        or use_table_coverage_obligation_context_contract
        or use_table_stiffness_atomic_threshold_coverage_contract
        or use_composition_numeric_occurrence_contract
        or use_structural_index_occurrence_contract
        or use_source_owned_structural_projection_contract
    ):
        # Fail-closed variant guard (checkpoint-A rule 8): only the single-page
        # V24 base contract admits the quarantined controlled-diagram lane.
        raise ValueError("controlled-diagram node anchors are admitted only by the single-page V24 base contract")
    if continuation_review_sidecar is None and continuation_review_sidecar_path is not None:
        continuation_review_sidecar = load_continuation_review_sidecar(continuation_review_sidecar_path)
    if continuation_review_sidecar is not None and continuation_review_sidecar_path is None:
        raise ValueError("reviewed continuation sidecars require their source file path for hash binding")
    if continuation_review_sidecar is not None and continuation_repository_root is None:
        raise ValueError("reviewed continuation sidecars require a repository root for source PDF verification")
    if continuation_review_sidecar is not None and continuation_review_sidecar_path is not None:
        file_sidecar = load_continuation_review_sidecar(continuation_review_sidecar_path)
        if file_sidecar != continuation_review_sidecar:
            raise ValueError("reviewed continuation sidecar object does not match its source file")
        verify_continuation_review_source_pdf(
            sidecar=continuation_review_sidecar,
            repository_root=continuation_repository_root,
        )
    if continuation_review_sidecar_path is not None and not is_multipage:
        raise ValueError("reviewed continuation sidecars require a multi-page Step 2A request")
    if (
        use_minimal_model_contract
        or use_numeric_expression_grounding_contract
        or use_single_role_anchor_contract
        or use_deterministic_numeric_claim_contract
        or use_table_continuation_contract
        or use_table_coverage_obligation_contract
        or use_table_coverage_obligation_context_contract
        or use_table_stiffness_atomic_threshold_coverage_contract
        or use_composition_numeric_occurrence_contract
        or use_structural_index_occurrence_contract
        or use_source_owned_structural_projection_contract
    ) and continuation_review_sidecar is None:
        raise ValueError("minimal model contracts currently require a reviewed multi-page continuation request")
    continuation_review_sidecar_sha256 = (
        sha256_file(continuation_review_sidecar_path)
        if continuation_review_sidecar_path is not None
        else None
    )
    frozen_bindings = (
        supplied_bindings
        if supplied_bindings
        else build_reviewed_continuation_bindings(continuation_review_sidecar)
        if continuation_review_sidecar is not None
        else _default_manual_continuation_bindings(
            projection=projection,
            additional_projections=frozen_additional_projections,
            sections=frozen_sections,
        )
        if is_multipage
        else ()
    )
    request_version = (
        CONTROLLED_DIAGRAM_ANCHOR_STEP2A_REQUEST_VERSION
        if has_controlled_diagram_anchors
        else SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_REQUEST_VERSION
        if use_source_owned_structural_projection_contract
        else STRUCTURAL_INDEX_OCCURRENCE_STEP2A_REQUEST_VERSION
        if use_structural_index_occurrence_contract
        else
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_REQUEST_VERSION
        if use_composition_numeric_occurrence_contract
        else
        TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_REQUEST_VERSION
        if use_table_stiffness_atomic_threshold_coverage_contract
        else
        TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_REQUEST_VERSION
        if use_table_coverage_obligation_context_contract
        else
        TABLE_COVERAGE_OBLIGATION_STEP2A_REQUEST_VERSION
        if use_table_coverage_obligation_contract
        else
        TABLE_CONTINUATION_STEP2A_REQUEST_VERSION
        if use_table_continuation_contract
        else
        DETERMINISTIC_NUMERIC_CLAIM_STEP2A_REQUEST_VERSION
        if use_deterministic_numeric_claim_contract
        else
        SINGLE_ROLE_ANCHOR_STEP2A_REQUEST_VERSION
        if use_single_role_anchor_contract
        else
        NUMERIC_EXPRESSION_GROUNDING_STEP2A_REQUEST_VERSION
        if use_numeric_expression_grounding_contract
        else
        MINIMAL_REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION
        if use_minimal_model_contract
        else
        REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION
        if continuation_review_sidecar is not None
        else MULTIPAGE_STEP2A_REQUEST_VERSION
        if is_multipage
        else STEP2A_REQUEST_VERSION
    )
    model_input_version = (
        CONTROLLED_DIAGRAM_ANCHOR_STEP2A_MODEL_INPUT_VERSION
        if has_controlled_diagram_anchors
        else SOURCE_OWNED_STRUCTURAL_PROJECTION_STEP2A_MODEL_INPUT_VERSION
        if use_source_owned_structural_projection_contract
        else STRUCTURAL_INDEX_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION
        if use_structural_index_occurrence_contract
        else
        COMPOSITION_NUMERIC_OCCURRENCE_STEP2A_MODEL_INPUT_VERSION
        if use_composition_numeric_occurrence_contract
        else
        TABLE_STIFFNESS_ATOMIC_THRESHOLD_COVERAGE_STEP2A_MODEL_INPUT_VERSION
        if use_table_stiffness_atomic_threshold_coverage_contract
        else
        TABLE_COVERAGE_OBLIGATION_CONTEXT_STEP2A_MODEL_INPUT_VERSION
        if use_table_coverage_obligation_context_contract
        else
        TABLE_COVERAGE_OBLIGATION_STEP2A_MODEL_INPUT_VERSION
        if use_table_coverage_obligation_contract
        else
        TABLE_CONTINUATION_STEP2A_MODEL_INPUT_VERSION
        if use_table_continuation_contract
        else
        DETERMINISTIC_NUMERIC_CLAIM_STEP2A_MODEL_INPUT_VERSION
        if use_deterministic_numeric_claim_contract
        else
        SINGLE_ROLE_ANCHOR_STEP2A_MODEL_INPUT_VERSION
        if use_single_role_anchor_contract
        else
        NUMERIC_EXPRESSION_GROUNDING_STEP2A_MODEL_INPUT_VERSION
        if use_numeric_expression_grounding_contract
        else
        MINIMAL_REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION
        if use_minimal_model_contract
        else
        REVIEWED_MULTIPAGE_STEP2A_MODEL_INPUT_VERSION
        if continuation_review_sidecar is not None
        else MULTIPAGE_STEP2A_MODEL_INPUT_VERSION
        if is_multipage
        else STEP2A_MODEL_INPUT_VERSION
    )
    coverage_obligation_input = (
        derive_step2a_coverage_obligation_input(
            source_document_sha256=projection.source_document_sha256,
            content_policy_id=projection.content_policy_id,
            content_policy_revision=projection.content_policy_revision,
            content_policy_fingerprint_sha256=projection.content_policy_fingerprint_sha256,
            sections=frozen_sections,
            continuation_bindings=frozen_bindings,
            table_continuation_inputs=frozen_table_continuation_inputs,
        )
        if (
            use_table_coverage_obligation_contract
            or use_table_coverage_obligation_context_contract
            or use_table_stiffness_atomic_threshold_coverage_contract
            or use_composition_numeric_occurrence_contract
            or use_structural_index_occurrence_contract
            or use_source_owned_structural_projection_contract
        )
        else None
    )
    coverage_obligation_context_input = (
        derive_step2a_coverage_obligation_context_input(
            request=Step2ARequest.model_construct(
                identity=Step2RequestIdentity.model_construct(stage=Step2Stage.FACT_PLANNING),
                sample_id=sample_id,
                material_profile_id=material_profile_id,
                projection=projection,
                additional_projections=frozen_additional_projections,
                sections=frozen_sections,
                continuation_bindings=frozen_bindings,
                table_continuation_inputs=frozen_table_continuation_inputs,
                coverage_obligation_input=coverage_obligation_input,
            )
        )
        if (
            use_table_coverage_obligation_context_contract
            or use_table_stiffness_atomic_threshold_coverage_contract
            or use_composition_numeric_occurrence_contract
            or use_structural_index_occurrence_contract
            or use_source_owned_structural_projection_contract
        )
        else None
    )
    payload = _step2a_payload(
        sample_id=sample_id,
        material_profile_id=material_profile_id,
        model_input_version=model_input_version,
        projection=projection,
        sections=frozen_sections,
        additional_projections=frozen_additional_projections,
        continuation_bindings=frozen_bindings,
        continuation_review_sidecar=continuation_review_sidecar,
        continuation_review_sidecar_sha256=continuation_review_sidecar_sha256,
        table_continuation_inputs=frozen_table_continuation_inputs,
        coverage_obligation_input=coverage_obligation_input,
        coverage_obligation_context_input=coverage_obligation_context_input,
        heading_only_allowed_anchor_span_ids=frozen_heading_only_allowances,
        manual_reissue_reason=manual_reissue_reason,
        include_multipage_fields=is_multipage,
        include_reviewed_continuation_fields=continuation_review_sidecar is not None,
        include_table_continuation_fields=(
            use_table_continuation_contract
            or use_table_coverage_obligation_contract
            or use_table_coverage_obligation_context_contract
            or use_table_stiffness_atomic_threshold_coverage_contract
            or use_composition_numeric_occurrence_contract
            or use_structural_index_occurrence_contract
            or use_source_owned_structural_projection_contract
        ),
        include_coverage_obligation_fields=use_table_coverage_obligation_contract,
        include_coverage_obligation_context_fields=(
            use_table_coverage_obligation_context_contract
            or use_table_stiffness_atomic_threshold_coverage_contract
            or use_composition_numeric_occurrence_contract
            or use_structural_index_occurrence_contract
            or use_source_owned_structural_projection_contract
        ),
    )
    identity = make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=request_version,
        model=model,
        payload_sha256=_canonical_sha256(payload),
        relay_base_url=relay_base_url,
        prompt_template=prompt_template,
        response_schema=step2a_result_payload_model(request_version).model_json_schema(),
        decoder=decoder,
        projection_id=projection.projection_id,
        projection_sha256=projection.fingerprint_sha256,
        source_manifest_sha256=projection.source_manifest_sha256,
        content_policy_fingerprint_sha256=projection.content_policy_fingerprint_sha256,
        manual_reissue_reason=manual_reissue_reason,
    )
    return Step2ARequest(
        identity=identity,
        sample_id=sample_id,
        material_profile_id=material_profile_id,
        model_input_version=model_input_version,
        projection=projection,
        additional_projections=frozen_additional_projections,
        sections=frozen_sections,
        continuation_bindings=frozen_bindings,
        continuation_review_sidecar=continuation_review_sidecar,
        continuation_review_sidecar_sha256=continuation_review_sidecar_sha256,
        table_continuation_inputs=frozen_table_continuation_inputs,
        coverage_obligation_input=coverage_obligation_input,
        coverage_obligation_context_input=coverage_obligation_context_input,
        heading_only_allowed_anchor_span_ids=frozen_heading_only_allowances,
        manual_reissue_reason=manual_reissue_reason,
    )
