"""Policy-filtered, immutable input projections for V2 Step 2A."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..evidence.schema import EvidenceKind, EvidenceUnit, PageEvidence
from ..materials.schema import ContentPolicy, PolicyDisposition

from .diagram.disposition import DiagramDisposition, DiagramDispositionSidecar
from .diagram.sidecar import (
    DiagramReviewSidecar,
    DiagramReviewState,
    DiagramSemanticContractVersion,
)
from .fact_engine import decide_policy
from .contracts import (
    EvidenceAnchorSpan,
    ContinuationRecord,
    PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION,
    ProjectionDecisionRecord,
    ProjectionSelection,
    ReviewedDiagramEdgeInput,
    ReviewedDiagramNodeInput,
    ReviewedStructuralDiagramInput,
    STEP2A_SEMANTIC_PROJECTION_VERSION,
    Step2ASemanticProjection,
    Step2ASectionInput,
    TableGridIntegrityRecord,
    TableGridIntegrityStatus,
    build_step2a_section,
    canonical_step2_sha256,
    make_evidence_anchor_span_id,
    make_continuation_record_id,
)
from .continuation.repair_sidecar import (
    TableRepairReviewSidecar,
    TableRepairReviewState,
    verify_reviewed_table_repair,
)


_TABLE_KINDS = frozenset({EvidenceKind.TABLE, EvidenceKind.TABLE_ROW, EvidenceKind.TABLE_CELL})
_DIRECT_TEXT_KINDS = frozenset(
    {
        EvidenceKind.HEADING,
        EvidenceKind.PARAGRAPH,
        EvidenceKind.LIST_GROUP,
        EvidenceKind.LIST_ITEM,
        EvidenceKind.FORMULA,
        EvidenceKind.CAPTION,
    }
)
_VISUAL_KINDS = frozenset({EvidenceKind.FIGURE, EvidenceKind.DIAGRAM_NODE, EvidenceKind.DIAGRAM_EDGE})
_SAFE_SPAN_TERMINATORS = frozenset("。！？!?；;\n\r")
_CONTINUATION_MARKERS = ("↓", "续下页", "下页续")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _selection_from_policy(disposition: PolicyDisposition) -> ProjectionSelection:
    if disposition == PolicyDisposition.EXCLUDED_BY_POLICY:
        return ProjectionSelection.EXCLUDED_BY_POLICY
    if disposition == PolicyDisposition.MANUAL_REVIEW:
        return ProjectionSelection.MANUAL_REVIEW
    return ProjectionSelection.EXCLUDED_BY_TYPE


def _bbox_center_within(*, inner: EvidenceUnit, outer: EvidenceUnit) -> bool:
    if inner.locator.bbox is None or outer.locator.bbox is None:
        return False
    center_x = (inner.locator.bbox.x0 + inner.locator.bbox.x1) / 2
    center_y = (inner.locator.bbox.y0 + inner.locator.bbox.y1) / 2
    return (
        outer.locator.bbox.x0 <= center_x <= outer.locator.bbox.x1
        and outer.locator.bbox.y0 <= center_y <= outer.locator.bbox.y1
    )


def _manifest_bound_page(*, source_manifest_path: Path, page: PageEvidence) -> PageEvidence:
    """Reject a caller-supplied page that is not exactly the hashed manifest."""
    manifest_page = PageEvidence.model_validate_json(source_manifest_path.read_bytes())
    if page.model_dump(mode="json") != manifest_page.model_dump(mode="json"):
        raise ValueError("supplied PageEvidence does not exactly match the source manifest")
    return manifest_page


def _safe_anchor_ranges(canonical_text: str) -> tuple[tuple[int, int], ...]:
    """Split only on source punctuation/newline boundaries, never OCR or an LLM.

    Boundaries are deliberately conservative: each returned range is an exact,
    non-overlapping source substring. A sentence without a terminator remains a
    single span, so the projection never invents clause structure.
    """

    ranges: list[tuple[int, int]] = []
    cursor = 0
    for index, char in enumerate(canonical_text):
        if char not in _SAFE_SPAN_TERMINATORS:
            continue
        start = cursor
        end = index + 1
        while start < end and canonical_text[start].isspace():
            start += 1
        while end > start and canonical_text[end - 1].isspace():
            end -= 1
        if start < end:
            ranges.append((start, end))
        cursor = index + 1
    start = cursor
    end = len(canonical_text)
    while start < end and canonical_text[start].isspace():
        start += 1
    while end > start and canonical_text[end - 1].isspace():
        end -= 1
    if start < end:
        ranges.append((start, end))
    if not ranges:
        raise ValueError("direct textual Evidence requires a non-empty deterministic anchor span")
    return tuple(ranges)


def _continuation_records(
    *,
    direct_units: tuple[EvidenceUnit, ...],
    anchor_spans: tuple[EvidenceAnchorSpan, ...],
    page_num: int,
    source_manifest_sha256: str,
) -> tuple[ContinuationRecord, ...]:
    """Record explicit page-end continuation markers without guessing closure."""

    spans_by_parent: dict[str, list[EvidenceAnchorSpan]] = {}
    for span in anchor_spans:
        spans_by_parent.setdefault(span.parent_evidence_id, []).append(span)
    marked_spans = tuple(
        span.anchor_span_id
        for unit in direct_units
        if unit.kind == EvidenceKind.LIST_ITEM and any(marker in unit.canonical_text for marker in _CONTINUATION_MARKERS)
        for span in spans_by_parent.get(unit.evidence_id, ())
    )
    if not marked_spans:
        return ()
    return (
        ContinuationRecord(
            continuation_id=make_continuation_record_id(
                page_num=page_num,
                source_manifest_sha256=source_manifest_sha256,
                anchor_span_ids=marked_spans,
            ),
            page_num=page_num,
            source_manifest_sha256=source_manifest_sha256,
            anchor_span_ids=marked_spans,
            required_adjacent_page_num=page_num + 1,
            basis="Explicit page-end continuation marker detected in list Evidence; page-local all-items enumeration is incomplete until the adjacent source page is bound and closed.",
        ),
    )


def _table_grid_integrity(
    *,
    table: EvidenceUnit,
    rows: list[EvidenceUnit],
    by_parent: dict[str, list[EvidenceUnit]],
    span_overrides: dict[str, tuple[int, int]] | None = None,
) -> tuple[TableGridIntegrityRecord, list[EvidenceUnit]]:
    """Validate declared row/column/span coverage without repairing Evidence."""
    members = [table, *rows]
    issues: set[str] = set()
    try:
        declared_rows = int(table.metadata.get("rows", 0))
        declared_columns = int(table.metadata.get("columns", 0))
    except (TypeError, ValueError):
        declared_rows = 0
        declared_columns = 0
        issues.add("invalid_declared_geometry")
    if declared_rows <= 0 or declared_columns <= 0:
        issues.add("invalid_declared_geometry")

    observed_row_indexes: set[int] = set()
    covered: set[tuple[int, int]] = set()
    observed_cells = 0
    for row in rows:
        cells = [cell for cell in by_parent.get(row.evidence_id, []) if cell.kind == EvidenceKind.TABLE_CELL]
        members.extend(cells)
        try:
            row_index = int(row.metadata["row"])
        except (KeyError, TypeError, ValueError):
            issues.add("invalid_row_index")
            row_index = None
        if row_index is not None:
            if row_index in observed_row_indexes:
                issues.add("duplicate_row_index")
            observed_row_indexes.add(row_index)
            if row_index < 0 or row_index >= declared_rows:
                issues.add("row_index_out_of_bounds")

        for cell in cells:
            observed_cells += 1
            try:
                cell_row = int(cell.metadata["row"])
                column_index = int(cell.metadata["column"])
                source_row_span = int(cell.metadata.get("row_span", 1))
                source_col_span = int(cell.metadata.get("col_span", 1))
                row_span, col_span = (span_overrides or {}).get(cell.evidence_id, (source_row_span, source_col_span))
            except (KeyError, TypeError, ValueError):
                issues.add("invalid_cell_geometry")
                continue
            if row_span <= 0 or col_span <= 0:
                issues.add("invalid_cell_span")
                continue
            if row_index is None or cell_row != row_index:
                issues.add("cell_row_parent_mismatch")
            for covered_row in range(cell_row, cell_row + row_span):
                for covered_column in range(column_index, column_index + col_span):
                    if covered_row < 0 or covered_column < 0 or covered_row >= declared_rows or covered_column >= declared_columns:
                        issues.add("cell_span_out_of_bounds")
                        continue
                    coordinate = (covered_row, covered_column)
                    if coordinate in covered:
                        issues.add("cell_span_overlap")
                    covered.add(coordinate)

    expected_rows = set(range(declared_rows))
    expected_coordinates = {(row_index, column_index) for row_index in expected_rows for column_index in range(declared_columns)}
    if observed_row_indexes != expected_rows:
        issues.add("row_coverage_hole")
    if covered != expected_coordinates:
        issues.add("grid_coverage_hole")
    status = TableGridIntegrityStatus.PASSED if not issues else TableGridIntegrityStatus.MANUAL_REVIEW
    return (
        TableGridIntegrityRecord(
            table_evidence_id=table.evidence_id,
            declared_row_count=max(declared_rows, 1),
            declared_column_count=max(declared_columns, 1),
            observed_row_count=len(rows),
            observed_cell_count=observed_cells,
            status=status,
            issue_codes=tuple(sorted(issues)),
        ),
        members,
    )


@dataclass(frozen=True)
class SemanticProjectionBuild:
    projection: Step2ASemanticProjection
    section: Step2ASectionInput


def _reviewed_diagram_input(entry) -> ReviewedStructuralDiagramInput:
    return ReviewedStructuralDiagramInput(
        review_id=entry.review_id,
        figure_evidence_id=entry.figure_evidence_id,
        title=entry.title or "",
        diagram_type=entry.diagram_type.value if entry.diagram_type else "",
        nodes=tuple(
            ReviewedDiagramNodeInput(
                node_id=node.node_id,
                label=node.label,
                evidence_ids=node.evidence_ids,
            )
            for node in entry.nodes
        ),
        edges=tuple(
            ReviewedDiagramEdgeInput(
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                relation=edge.relation,
                evidence_ids=edge.evidence_ids,
                basis=edge.basis or "",
                confidence=edge.confidence if edge.confidence is not None else -1,
            )
            for edge in entry.edges
        ),
    )


def build_step2a_semantic_projection(
    *,
    sample_id: str,
    page: PageEvidence,
    source_manifest_path: Path,
    source_manifest_reference: str | None = None,
    content_policy: ContentPolicy,
    diagram_review_sidecar: DiagramReviewSidecar | None = None,
    diagram_review_sidecar_path: Path | None = None,
    diagram_disposition_sidecar: DiagramDispositionSidecar | None = None,
    table_repair_sidecar: TableRepairReviewSidecar | None = None,
    table_repair_sidecar_path: Path | None = None,
    repository_root: Path | None = None,
    mint_controlled_diagram_anchor_spans: bool = False,
) -> SemanticProjectionBuild:
    """Create a non-destructive Step 2A view from one immutable Evidence page.

    The derived projection records a decision for every source Evidence unit,
    while the LLM receives only direct policy-approved textual/table content and
    reviewed ``semantic_v2`` diagram summaries.

    ``mint_controlled_diagram_anchor_spans`` opts in to the quarantined
    controlled-diagram node-label anchor lane. It stays off by default so every
    existing request family keeps building byte-stable payloads; only explicit
    admission-doctrine consumers (and a future request family that declares the
    lane) may enable it.
    """
    source_manifest_path = Path(source_manifest_path)
    if not source_manifest_path.is_file():
        raise ValueError(f"missing projection source manifest: {source_manifest_path}")
    source_manifest_reference = source_manifest_reference or source_manifest_path.as_posix()
    page = _manifest_bound_page(source_manifest_path=source_manifest_path, page=page)
    units = tuple(sorted(page.evidence_units, key=lambda unit: unit.order))
    if not units:
        raise ValueError("semantic projections require source Evidence")
    document_hashes = {unit.locator.document_sha256 for unit in units}
    if len(document_hashes) != 1:
        raise ValueError("semantic projection page must bind exactly one source document hash")
    by_id = {unit.evidence_id: unit for unit in units}
    by_parent: dict[str, list[EvidenceUnit]] = {}
    for unit in units:
        if unit.parent_id:
            by_parent.setdefault(unit.parent_id, []).append(unit)

    table_repair_overrides: dict[str, dict[str, tuple[int, int]]] = {}
    table_repair_sidecar_sha256: str | None = None
    reviewed_table_repair_ids: list[str] = []
    if table_repair_sidecar is not None:
        if table_repair_sidecar_path is None:
            raise ValueError("table repair sidecar path is required when a table repair is supplied")
        if table_repair_sidecar.sample_id != sample_id or table_repair_sidecar.page_num != page.page_num:
            raise ValueError("table repair sidecar does not bind this sample and page")
        if table_repair_sidecar.source_manifest_path != source_manifest_reference:
            raise ValueError("table repair sidecar source manifest path does not match the projection")
        if table_repair_sidecar.state == TableRepairReviewState.REVIEWED:
            overrides = verify_reviewed_table_repair(
                sidecar=table_repair_sidecar,
                page=page,
                source_manifest_path=source_manifest_path,
                repository_root=repository_root or Path.cwd(),
            )
            table_repair_overrides[table_repair_sidecar.table_evidence_id] = overrides
            reviewed_table_repair_ids.append(table_repair_sidecar.repair_id)
            table_repair_sidecar_sha256 = _file_sha256(Path(table_repair_sidecar_path))

    dispositions = {}
    if diagram_disposition_sidecar is not None:
        dispositions = {
            entry.figure_evidence_id: entry
            for entry in diagram_disposition_sidecar.entries
            if entry.sample_id == sample_id and entry.page_num == page.page_num
        }
    reviewed_entries = {}
    sidecar_sha256: str | None = None
    if diagram_review_sidecar is not None:
        if diagram_review_sidecar_path is None:
            raise ValueError("diagram review sidecar path is required when reviews are supplied")
        sidecar_sha256 = _file_sha256(Path(diagram_review_sidecar_path))
        reviewed_entries = {
            entry.figure_evidence_id: entry
            for entry in diagram_review_sidecar.entries
            if entry.sample_id == sample_id
            and entry.page_num == page.page_num
            and entry.state == DiagramReviewState.REVIEWED
            and entry.semantic_contract_version == DiagramSemanticContractVersion.SEMANTIC_V2
        }

    policy_decisions = {unit.evidence_id: decide_policy(unit=unit, policy=content_policy) for unit in units}
    selection: dict[str, ProjectionSelection] = {}
    additional_selections: dict[str, list[ProjectionSelection]] = {}
    reasons: dict[str, str] = {}
    direct_ids: set[str] = set()
    controlled_diagrams: list[ReviewedStructuralDiagramInput] = []
    table_grid_integrity: list[TableGridIntegrityRecord] = []
    applied_cell_span_overrides: dict[str, tuple[int, int]] = {}

    for unit in units:
        decision = policy_decisions[unit.evidence_id]
        if decision.disposition != PolicyDisposition.INCLUDED:
            selection[unit.evidence_id] = _selection_from_policy(decision.disposition)
            reasons[unit.evidence_id] = "Content Policy did not permit this Evidence as Step 2A input."
            continue
        if unit.kind in _DIRECT_TEXT_KINDS and unit.canonical_text:
            selection[unit.evidence_id] = ProjectionSelection.DIRECT_INPUT
            reasons[unit.evidence_id] = "Content Policy permitted this textual Evidence for direct Step 2A input."
            direct_ids.add(unit.evidence_id)
        elif unit.kind in _TABLE_KINDS:
            # Tables are admitted atomically below, after full row/cell checks.
            selection[unit.evidence_id] = ProjectionSelection.MANUAL_REVIEW
            reasons[unit.evidence_id] = "Awaiting full table hierarchy validation before Step 2A admission."
        elif unit.kind in _VISUAL_KINDS:
            selection[unit.evidence_id] = ProjectionSelection.EXCLUDED_BY_TYPE
            reasons[unit.evidence_id] = "Raw figures and OCR diagram fragments never enter Step 2A directly."
        else:
            selection[unit.evidence_id] = ProjectionSelection.EXCLUDED_BY_TYPE
            reasons[unit.evidence_id] = "Evidence kind is not eligible for direct Step 2A semantic input."

    for table in (unit for unit in units if unit.kind == EvidenceKind.TABLE):
        rows = [row for row in by_parent.get(table.evidence_id, []) if row.kind == EvidenceKind.TABLE_ROW]
        source_grid_record, table_members = _table_grid_integrity(table=table, rows=rows, by_parent=by_parent)
        # 换名而非复用上面的 overrides：那里是校验函数返回的非空字典，这里是 .get() 的
        # Optional，同名会让 mypy 认死首次绑定的非空类型。
        table_overrides = table_repair_overrides.get(table.evidence_id)
        if table_overrides is not None:
            repaired_grid_record, _ = _table_grid_integrity(
                table=table,
                rows=rows,
                by_parent=by_parent,
                span_overrides=table_overrides,
            )
            if repaired_grid_record.status != TableGridIntegrityStatus.PASSED:
                raise ValueError("reviewed table repair does not produce a complete structured grid")
            grid_record = source_grid_record.model_copy(
                update={
                    "status": TableGridIntegrityStatus.PASSED_WITH_REVIEWED_REPAIR,
                    "repair_id": table_repair_sidecar.repair_id if table_repair_sidecar is not None else None,
                }
            )
            applied_cell_span_overrides.update(overrides)
        else:
            grid_record = source_grid_record
        table_grid_integrity.append(grid_record)
        policy_permits_table = all(
            policy_decisions[item.evidence_id].disposition == PolicyDisposition.INCLUDED for item in table_members
        )
        if grid_record.status in {TableGridIntegrityStatus.PASSED, TableGridIntegrityStatus.PASSED_WITH_REVIEWED_REPAIR} and policy_permits_table:
            for item in table_members:
                selection[item.evidence_id] = ProjectionSelection.DIRECT_INPUT
                reasons[item.evidence_id] = "Complete policy-approved table hierarchy retained as structured Step 2A input."
                direct_ids.add(item.evidence_id)
            member_ids = {item.evidence_id for item in table_members}
            for candidate in units:
                if candidate.evidence_id in member_ids or selection[candidate.evidence_id] != ProjectionSelection.DIRECT_INPUT:
                    continue
                if _bbox_center_within(inner=candidate, outer=table):
                    selection[candidate.evidence_id] = ProjectionSelection.EXCLUDED_AS_TABLE_DUPLICATE
                    reasons[candidate.evidence_id] = "Text fragment lies inside a retained structured table and is excluded to prevent duplicate semantic input."
                    direct_ids.discard(candidate.evidence_id)
        elif grid_record.status == TableGridIntegrityStatus.MANUAL_REVIEW:
            for item in table_members:
                selection[item.evidence_id] = ProjectionSelection.MANUAL_REVIEW
                reasons[item.evidence_id] = "Table grid is incomplete or invalid; omitted fail-closed for manual review."
                direct_ids.discard(item.evidence_id)
            for candidate in units:
                if candidate.evidence_id in {item.evidence_id for item in table_members}:
                    continue
                if selection[candidate.evidence_id] != ProjectionSelection.DIRECT_INPUT:
                    continue
                if _bbox_center_within(inner=candidate, outer=table):
                    selection[candidate.evidence_id] = ProjectionSelection.MANUAL_REVIEW
                    reasons[candidate.evidence_id] = "Text fragment overlaps a table whose grid requires manual review; omitted fail-closed."
                    direct_ids.discard(candidate.evidence_id)

    for figure in (unit for unit in units if unit.kind == EvidenceKind.FIGURE):
        reviewed = reviewed_entries.get(figure.evidence_id)
        if reviewed is not None:
            diagram = _reviewed_diagram_input(reviewed)
            controlled_diagrams.append(diagram)
            for evidence_id in diagram.source_evidence_ids:
                if evidence_id not in by_id:
                    raise ValueError(f"reviewed diagram references Evidence outside the projected page: {evidence_id}")
                current = selection[evidence_id]
                if current == ProjectionSelection.DIRECT_INPUT:
                    if ProjectionSelection.CONTROLLED_DIAGRAM_SUMMARY not in additional_selections.setdefault(evidence_id, []):
                        additional_selections[evidence_id].append(ProjectionSelection.CONTROLLED_DIAGRAM_SUMMARY)
                    reasons[evidence_id] += " It is also retained as a reviewed structural-diagram anchor."
                elif current in {ProjectionSelection.EXCLUDED_BY_TYPE, ProjectionSelection.CONTROLLED_DIAGRAM_SUMMARY}:
                    selection[evidence_id] = ProjectionSelection.CONTROLLED_DIAGRAM_SUMMARY
                    reasons[evidence_id] = "Reviewed semantic_v2 structural diagram is admitted only as a controlled summary."
                else:
                    raise ValueError(
                        f"reviewed diagram anchor cannot bypass its current projection disposition: {evidence_id}={current.value}"
                    )
            continue
        disposition = dispositions.get(figure.evidence_id)
        if disposition is not None and disposition.disposition == DiagramDisposition.NON_KP_VISUAL:
            selection[figure.evidence_id] = ProjectionSelection.EXCLUDED_BY_AUDIT
            reasons[figure.evidence_id] = "Diagram disposition audit marked this figure non_kp_visual."
        elif disposition is not None:
            selection[figure.evidence_id] = ProjectionSelection.EXCLUDED_BY_TYPE
            reasons[figure.evidence_id] = "Reviewed photo/contextual visual remains an image reference, not Step 2A input."
        else:
            selection[figure.evidence_id] = ProjectionSelection.EXCLUDED_BY_TYPE
            reasons[figure.evidence_id] = "Figure lacks a reviewed semantic_v2 structural sidecar and is omitted fail-closed."
        for child in by_parent.get(figure.evidence_id, []):
            if child.kind in {EvidenceKind.DIAGRAM_NODE, EvidenceKind.DIAGRAM_EDGE}:
                selection[child.evidence_id] = selection[figure.evidence_id]
                reasons[child.evidence_id] = reasons[figure.evidence_id]

    records = tuple(
        ProjectionDecisionRecord(
            evidence_id=unit.evidence_id,
            source_kind=unit.kind,
            source_semantic_role=unit.semantic_role,
            policy_decision=policy_decisions[unit.evidence_id],
            selection=selection[unit.evidence_id],
            additional_selections=tuple(additional_selections.get(unit.evidence_id, ())),
            reason=reasons[unit.evidence_id],
        )
        for unit in units
    )
    direct_units = tuple(unit for unit in units if unit.evidence_id in direct_ids)
    heading_ids = {unit.evidence_id for unit in units if unit.kind == EvidenceKind.HEADING or unit.semantic_role.value == "heading"}
    context_only_ids: set[str] = set()
    for unit in direct_units:
        parent = by_id.get(unit.parent_id) if unit.parent_id else None
        heading_like_child = (
            unit.kind == EvidenceKind.PARAGRAPH
            and parent is not None
            and parent.evidence_id in heading_ids
            and len(unit.canonical_text.strip()) <= 80
            and not any(mark in unit.canonical_text for mark in "。！？!?；;：:")
        )
        if unit.evidence_id in heading_ids or heading_like_child:
            context_only_ids.add(unit.evidence_id)
    policy_fingerprint = canonical_step2_sha256(content_policy.model_dump(mode="python"))
    source_manifest_sha256 = _file_sha256(source_manifest_path)
    anchor_spans: list[EvidenceAnchorSpan] = []
    for unit in direct_units:
        if unit.kind not in (_DIRECT_TEXT_KINDS | {EvidenceKind.TABLE_CELL}):
            continue
        # Empty cells are structural grid members, not textual claims. Keep
        # their row/column occupancy in the table input without inventing an
        # anchor span for content that does not exist.
        if not unit.canonical_text.strip():
            continue
        for start, end in _safe_anchor_ranges(unit.canonical_text):
            exact_text = unit.canonical_text[start:end]
            exact_text_sha256 = hashlib.sha256(exact_text.encode("utf-8")).hexdigest()
            anchor_spans.append(
                EvidenceAnchorSpan(
                    anchor_span_id=make_evidence_anchor_span_id(
                        parent_evidence_id=unit.evidence_id,
                        start=start,
                        end=end,
                        exact_text_sha256=exact_text_sha256,
                        content_policy_fingerprint_sha256=policy_fingerprint,
                        source_manifest_sha256=source_manifest_sha256,
                    ),
                    parent_evidence_id=unit.evidence_id,
                    parent_evidence_kind=unit.kind,
                    start=start,
                    end=end,
                    canonical_text=exact_text,
                    exact_text_sha256=exact_text_sha256,
                    content_policy_id=content_policy.policy_id,
                    content_policy_revision=content_policy.revision,
                    content_policy_fingerprint_sha256=policy_fingerprint,
                    policy_decision=policy_decisions[unit.evidence_id],
                    source_manifest_sha256=source_manifest_sha256,
                )
            )
    # Controlled-diagram node-label lane (quarantined; never joins anchor_spans):
    # only policy-included census diagram_node units bound by a reviewed sidecar
    # node mint one whitespace-trimmed full-text span each, keep-first
    # deduplicated in deterministic diagram -> node -> evidence_id order.
    controlled_diagram_anchor_spans: list[EvidenceAnchorSpan] = []
    minted_node_evidence_ids: set[str] = set()
    for diagram in controlled_diagrams if mint_controlled_diagram_anchor_spans else ():
        for node in diagram.nodes:
            for evidence_id in node.evidence_ids:
                if evidence_id in minted_node_evidence_ids:
                    continue
                # 换名而非复用外层的 unit：那里是 for unit in units 绑定的非空 EvidenceUnit，
                # 这里是 by_id.get() 的 Optional，同名会让 mypy 认死非空类型。
                node_unit = by_id.get(evidence_id)
                if node_unit is None or node_unit.kind != EvidenceKind.DIAGRAM_NODE:
                    continue
                if policy_decisions[evidence_id].disposition != PolicyDisposition.INCLUDED:
                    continue
                label_text = node_unit.canonical_text
                start, end = 0, len(label_text)
                while start < end and label_text[start].isspace():
                    start += 1
                while end > start and label_text[end - 1].isspace():
                    end -= 1
                if start >= end:
                    continue
                minted_node_evidence_ids.add(evidence_id)
                exact_text = label_text[start:end]
                exact_text_sha256 = hashlib.sha256(exact_text.encode("utf-8")).hexdigest()
                controlled_diagram_anchor_spans.append(
                    EvidenceAnchorSpan(
                        anchor_span_id=make_evidence_anchor_span_id(
                            parent_evidence_id=node_unit.evidence_id,
                            start=start,
                            end=end,
                            exact_text_sha256=exact_text_sha256,
                            content_policy_fingerprint_sha256=policy_fingerprint,
                            source_manifest_sha256=source_manifest_sha256,
                        ),
                        parent_evidence_id=node_unit.evidence_id,
                        parent_evidence_kind=node_unit.kind,
                        start=start,
                        end=end,
                        canonical_text=exact_text,
                        exact_text_sha256=exact_text_sha256,
                        content_policy_id=content_policy.policy_id,
                        content_policy_revision=content_policy.revision,
                        content_policy_fingerprint_sha256=policy_fingerprint,
                        policy_decision=policy_decisions[node_unit.evidence_id],
                        source_manifest_sha256=source_manifest_sha256,
                    )
                )
    continuation_records = _continuation_records(
        direct_units=direct_units,
        anchor_spans=tuple(anchor_spans),
        page_num=page.page_num,
        source_manifest_sha256=source_manifest_sha256,
    )
    # Version literal is carried-lane conditional: plain builds keep the v5
    # literal so every frozen human-signed artifact that pins projection_id
    # (e.g. reviewed continuation sidecars) replays byte-identically; only a
    # payload that actually carries the quarantined lane claims v6.
    projection_payload = {
        "projection_version": (
            STEP2A_SEMANTIC_PROJECTION_VERSION
            if controlled_diagram_anchor_spans
            else PREVIOUS_STEP2A_SEMANTIC_PROJECTION_VERSION
        ),
        "sample_id": sample_id,
        "page_num": page.page_num,
        "source_manifest_path": source_manifest_reference,
        "source_manifest_sha256": source_manifest_sha256,
        "source_document_sha256": next(iter(document_hashes)),
        "content_policy_id": content_policy.policy_id,
        "content_policy_revision": content_policy.revision,
        "content_policy_fingerprint_sha256": policy_fingerprint,
        "decisions": [record.model_dump(mode="json") for record in records],
        "direct_evidence_ids": [unit.evidence_id for unit in direct_units],
        "context_only_evidence_ids": sorted(context_only_ids),
        "anchor_spans": [span.model_dump(mode="json") for span in anchor_spans],
        "table_grid_integrity": [record.model_dump(mode="json") for record in table_grid_integrity],
        "continuation_records": [record.model_dump(mode="json") for record in continuation_records],
        "reviewed_table_repair_ids": reviewed_table_repair_ids,
        "table_repair_sidecar_sha256": table_repair_sidecar_sha256,
        "controlled_diagrams": [diagram.model_dump(mode="json") for diagram in controlled_diagrams],
        "diagram_review_sidecar_sha256": sidecar_sha256,
    }
    if controlled_diagram_anchor_spans:
        # Mirror the model serializer: the key exists only when the lane minted.
        projection_payload["controlled_diagram_anchor_spans"] = [
            span.model_dump(mode="json") for span in controlled_diagram_anchor_spans
        ]
    fingerprint = canonical_step2_sha256(projection_payload)
    projection = Step2ASemanticProjection(
        projection_id=f"step2a-projection:{fingerprint[:24]}",
        **projection_payload,
    )
    section = build_step2a_section(
        units=direct_units,
        anchor_spans=tuple(anchor_spans),
        controlled_diagrams=tuple(controlled_diagrams),
        context_only_evidence_ids=context_only_ids,
        table_cell_span_overrides=applied_cell_span_overrides,
        controlled_diagram_anchor_spans=tuple(controlled_diagram_anchor_spans),
    )
    if set(section.evidence_ids) != (set(projection.direct_evidence_ids) | {evidence_id for diagram in projection.controlled_diagrams for evidence_id in diagram.source_evidence_ids}):
        raise ValueError("projected Step 2A section does not exactly match the semantic projection")
    if set(section.anchor_span_ids) != {span.anchor_span_id for span in projection.anchor_spans}:
        raise ValueError("projected Step 2A section does not exactly match the anchor-span projection")
    if set(section.controlled_diagram_anchor_span_ids) != {
        span.anchor_span_id for span in projection.controlled_diagram_anchor_spans
    }:
        raise ValueError("projected Step 2A section does not exactly match the controlled-diagram anchor lane")
    if set(section.context_only_evidence_ids) != set(projection.context_only_evidence_ids):
        raise ValueError("projected Step 2A section does not exactly match context-only Evidence")
    return SemanticProjectionBuild(projection=projection, section=section)
