"""Reviewed, non-destructive bindings for one table continued on an adjacent page.

The normal Step 2A projection deliberately rejects a page-local table whose
grid ends at a page boundary.  This module provides a separate, reviewed
contract for that narrow case.  It never changes a ``PageEvidence`` manifest:
every retained table, row, cell, bbox, source manifest and anchor span is
rebound before a continued-table Fact can be accepted.
"""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import Field, model_validator

from ...evidence.layout import normalized_compare_text
from ...evidence.schema import BBox, EvidenceKind, EvidenceUnit, PageEvidence
from ...materials.schema import ContentPolicy, PolicyDisposition, StrictModel, V2_SCHEMA_VERSION

from ..fact_engine import decide_policy
from ..schema import FactType
from ..contracts import EvidenceAnchorSpan, canonical_step2_sha256, make_evidence_anchor_span_id


TABLE_CONTINUATION_BINDING_VERSION = "table_continuation_binding_v1"
TABLE_CONTINUATION_CARD_BACKFILL_VERSION = "table_continuation_card_backfill_v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TableContinuationReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class TableContinuationAnchorRole(StrEnum):
    TABLE_HEADING = "table_heading"
    COMPLETE_ROW = "complete_row"
    CONTINUED_SOURCE_FRAGMENT = "continued_source_fragment"
    CLOSURE_FRAGMENT = "closure_fragment"
    CONTINUED_ROW = "continued_row"


class TableContinuationFactGateState(StrEnum):
    COMPLETE = "complete"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    FACT_TYPE_MISMATCH = "fact_type_mismatch"
    ANCHOR_SET_MISMATCH = "anchor_set_mismatch"
    ASSERTION_MISMATCH = "assertion_mismatch"


class TableContinuationEvidenceBinding(StrictModel):
    """Exact immutable hierarchy for a table fragment on one source page."""

    page_num: int = Field(ge=1)
    source_manifest_path: str = Field(min_length=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    table_evidence_id: str
    table_bbox: BBox
    row_evidence_id: str
    row_bbox: BBox
    cell_evidence_id: str
    cell_bbox: BBox
    cell_canonical_text_sha256: str = Field(pattern=_SHA256_PATTERN)


class TableContinuationAnchorBinding(StrictModel):
    """One safe source span selected by the reviewed continuation contract."""

    role: TableContinuationAnchorRole
    page_num: int = Field(ge=1)
    source_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    parent_evidence_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    canonical_text: str = Field(min_length=1)
    exact_text_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_anchor(self) -> "TableContinuationAnchorBinding":
        if self.end <= self.start:
            raise ValueError("table continuation anchor span must be non-empty")
        if self.canonical_text != self.canonical_text.strip():
            raise ValueError("table continuation anchor text must be stripped")
        if self.exact_text_sha256 != hashlib.sha256(self.canonical_text.encode("utf-8")).hexdigest():
            raise ValueError("table continuation anchor exact-text hash does not match")
        return self


def make_table_continuation_binding_id(payload: object) -> str:
    return f"table-continuation:{_canonical_sha256(payload)[:24]}"


class TableContinuationBinding(StrictModel):
    """Versioned human-reviewed proof for an adjacent-page continued table."""

    schema_version: str = V2_SCHEMA_VERSION
    binding_version: str = TABLE_CONTINUATION_BINDING_VERSION
    binding_id: str = Field(pattern=r"^table-continuation:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_document_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_pdf_path: str = Field(min_length=1)
    source_pdf_sha256: str = Field(pattern=_SHA256_PATTERN)
    content_policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    content_policy_revision: int = Field(ge=1)
    content_policy_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    table_title: str = Field(min_length=2, max_length=64)
    state: TableContinuationReviewState
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    review_basis: str = Field(min_length=16, max_length=1200)
    evidence_bindings: tuple[TableContinuationEvidenceBinding, ...]
    anchor_bindings: tuple[TableContinuationAnchorBinding, ...]
    complete_table_anchor_span_ids: tuple[str, ...]
    item_three_source_anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    item_three_closure_anchor_span_id: str = Field(pattern=r"^span:[0-9a-f]{24}$")
    item_three_required_assertion: str = Field(min_length=8, max_length=500)

    @property
    def fingerprint_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json", exclude={"binding_id"}))

    @model_validator(mode="after")
    def validate_binding(self) -> "TableContinuationBinding":
        if self.binding_version != TABLE_CONTINUATION_BINDING_VERSION:
            raise ValueError("unsupported table continuation binding version")
        if self.source_pdf_sha256 != self.source_document_sha256:
            raise ValueError("table continuation source PDF hash must equal document hash")
        if self.table_title != self.table_title.strip() or self.review_basis != self.review_basis.strip():
            raise ValueError("table continuation text fields must be stripped")
        if self.state == TableContinuationReviewState.REVIEWED:
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed table continuation bindings require reviewer metadata")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft table continuation bindings cannot contain reviewer metadata")
        if len(self.evidence_bindings) < 3:
            raise ValueError("table continuation binding requires source header/body and adjacent table fragments")
        pages = tuple(sorted({item.page_num for item in self.evidence_bindings}))
        if len(pages) != 2 or pages[1] != pages[0] + 1:
            raise ValueError("table continuation binding requires adjacent source pages")
        cell_ids = [item.cell_evidence_id for item in self.evidence_bindings]
        if len(cell_ids) != len(set(cell_ids)):
            raise ValueError("table continuation binding requires distinct source and adjacent table cells")
        anchors = {item.anchor_span_id: item for item in self.anchor_bindings}
        if len(anchors) != len(self.anchor_bindings):
            raise ValueError("table continuation binding requires unique anchor spans")
        if not self.complete_table_anchor_span_ids or len(self.complete_table_anchor_span_ids) != len(set(self.complete_table_anchor_span_ids)):
            raise ValueError("table continuation binding requires unique complete-table anchor spans")
        if set(self.complete_table_anchor_span_ids) != set(anchors):
            raise ValueError("complete-table anchors must exactly equal the reviewed anchor binding set")
        source = anchors.get(self.item_three_source_anchor_span_id)
        closure = anchors.get(self.item_three_closure_anchor_span_id)
        if source is None or closure is None:
            raise ValueError("item three must bind source and closure anchor spans")
        if source.role != TableContinuationAnchorRole.CONTINUED_SOURCE_FRAGMENT:
            raise ValueError("item three source must be a continued source fragment")
        if closure.role != TableContinuationAnchorRole.CLOSURE_FRAGMENT:
            raise ValueError("item three closure must be a closure fragment")
        if source.page_num + 1 != closure.page_num:
            raise ValueError("item three closure must live on the immediate adjacent page")
        if self.item_three_required_assertion != self.item_three_required_assertion.strip():
            raise ValueError("item three required assertion must be stripped")
        expected_id = make_table_continuation_binding_id(
            self.model_dump(mode="json", exclude={"binding_id"})
        )
        if self.binding_id != expected_id:
            raise ValueError("table continuation binding ID does not match immutable content")
        return self


def load_table_continuation_binding(path: Path) -> TableContinuationBinding:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return TableContinuationBinding.model_validate(raw)


def _repository_path(*, root: Path, reference: str, label: str) -> Path:
    path = (root / reference).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"table continuation {label} escapes the repository root") from exc
    if not path.is_file():
        raise ValueError(f"table continuation {label} is missing")
    return path


def _unit_by_id(page: PageEvidence, evidence_id: str) -> EvidenceUnit:
    units = {unit.evidence_id: unit for unit in page.evidence_units}
    unit = units.get(evidence_id)
    if unit is None:
        raise ValueError(f"table continuation Evidence is missing: {evidence_id}")
    return unit


def _verify_evidence_binding(
    *, binding: TableContinuationEvidenceBinding, page: PageEvidence, document_sha256: str
) -> tuple[EvidenceUnit, EvidenceUnit, EvidenceUnit]:
    if page.page_num != binding.page_num:
        raise ValueError("table continuation page number does not match source manifest")
    table = _unit_by_id(page, binding.table_evidence_id)
    row = _unit_by_id(page, binding.row_evidence_id)
    cell = _unit_by_id(page, binding.cell_evidence_id)
    if table.kind != EvidenceKind.TABLE or row.kind != EvidenceKind.TABLE_ROW or cell.kind != EvidenceKind.TABLE_CELL:
        raise ValueError("table continuation hierarchy must bind table, row, and cell Evidence")
    if row.parent_id != table.evidence_id or cell.parent_id != row.evidence_id:
        raise ValueError("table continuation row/cell hierarchy does not match immutable Evidence")
    if table.locator.bbox != binding.table_bbox or row.locator.bbox != binding.row_bbox or cell.locator.bbox != binding.cell_bbox:
        raise ValueError("table continuation bbox does not match immutable Evidence")
    if hashlib.sha256(cell.canonical_text.encode("utf-8")).hexdigest() != binding.cell_canonical_text_sha256:
        raise ValueError("table continuation cell text hash does not match immutable Evidence")
    if any(unit.locator.document_sha256 != document_sha256 for unit in (table, row, cell)):
        raise ValueError("table continuation Evidence document hash does not match source PDF")
    return table, row, cell


def _verified_anchor_span(
    *,
    anchor: TableContinuationAnchorBinding,
    unit: EvidenceUnit,
    policy: ContentPolicy,
) -> EvidenceAnchorSpan:
    if unit.kind != EvidenceKind.TABLE_CELL:
        raise ValueError("table continuation anchors must bind table-cell Evidence")
    if unit.evidence_id != anchor.parent_evidence_id:
        raise ValueError("table continuation anchor parent Evidence does not match its cell binding")
    if anchor.end > len(unit.canonical_text) or unit.canonical_text[anchor.start : anchor.end] != anchor.canonical_text:
        raise ValueError("table continuation anchor range does not match immutable cell text")
    decision = decide_policy(unit=unit, policy=policy)
    if decision.disposition != PolicyDisposition.INCLUDED:
        raise ValueError("table continuation anchor is excluded by the current Content Policy")
    exact_text_sha256 = hashlib.sha256(anchor.canonical_text.encode("utf-8")).hexdigest()
    expected_id = make_evidence_anchor_span_id(
        parent_evidence_id=unit.evidence_id,
        start=anchor.start,
        end=anchor.end,
        exact_text_sha256=exact_text_sha256,
        content_policy_fingerprint_sha256=canonical_step2_sha256(policy.model_dump(mode="python")),
        source_manifest_sha256=anchor.source_manifest_sha256,
    )
    if anchor.anchor_span_id != expected_id:
        raise ValueError("table continuation anchor ID does not match immutable source span")
    return EvidenceAnchorSpan(
        anchor_span_id=anchor.anchor_span_id,
        parent_evidence_id=unit.evidence_id,
        parent_evidence_kind=unit.kind,
        start=anchor.start,
        end=anchor.end,
        canonical_text=anchor.canonical_text,
        exact_text_sha256=exact_text_sha256,
        content_policy_id=policy.policy_id,
        content_policy_revision=policy.revision,
        content_policy_fingerprint_sha256=canonical_step2_sha256(policy.model_dump(mode="python")),
        policy_decision=decision,
        source_manifest_sha256=anchor.source_manifest_sha256,
    )


def verify_table_continuation_binding(
    *, binding: TableContinuationBinding, repository_root: Path, content_policy: ContentPolicy
) -> tuple[EvidenceAnchorSpan, ...]:
    """Rebind every source asset and span before consuming a reviewed sidecar."""

    if binding.content_policy_id != content_policy.policy_id or binding.content_policy_revision != content_policy.revision:
        raise ValueError("table continuation Content Policy ID/revision does not match")
    policy_fingerprint = canonical_step2_sha256(content_policy.model_dump(mode="python"))
    if binding.content_policy_fingerprint_sha256 != policy_fingerprint:
        raise ValueError("table continuation Content Policy fingerprint does not match")
    source_pdf = _repository_path(root=repository_root, reference=binding.source_pdf_path, label="source PDF")
    if sha256_file(source_pdf) != binding.source_pdf_sha256:
        raise ValueError("table continuation source PDF hash does not match")

    pages_by_manifest: dict[str, PageEvidence] = {}
    cells_by_id: dict[str, EvidenceUnit] = {}
    for evidence_binding in binding.evidence_bindings:
        source_manifest = _repository_path(
            root=repository_root,
            reference=evidence_binding.source_manifest_path,
            label="source manifest",
        )
        actual_sha256 = sha256_file(source_manifest)
        if actual_sha256 != evidence_binding.source_manifest_sha256:
            raise ValueError("table continuation source manifest hash does not match")
        page = PageEvidence.model_validate_json(source_manifest.read_bytes())
        _, _, cell = _verify_evidence_binding(
            binding=evidence_binding,
            page=page,
            document_sha256=binding.source_document_sha256,
        )
        pages_by_manifest[evidence_binding.source_manifest_sha256] = page
        cells_by_id[cell.evidence_id] = cell

    verified: list[EvidenceAnchorSpan] = []
    for anchor in binding.anchor_bindings:
        anchor_page = pages_by_manifest.get(anchor.source_manifest_sha256)
        if anchor_page is None or anchor_page.page_num != anchor.page_num:
            raise ValueError("table continuation anchor does not bind a declared source manifest/page")
        anchor_cell = cells_by_id.get(anchor.parent_evidence_id)
        if anchor_cell is None:
            raise ValueError("table continuation anchor parent is not one of the reviewed cells")
        verified.append(_verified_anchor_span(anchor=anchor, unit=anchor_cell, policy=content_policy))
    return tuple(verified)


class TableContinuationFactProposal(StrictModel):
    """Minimal deterministic-fake Step 2A proposal used only by this offline gate."""

    proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    fact_type: FactType
    label: str = Field(min_length=2, max_length=64)
    assertion: str = Field(min_length=2, max_length=500)
    selected_anchor_span_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_proposal(self) -> "TableContinuationFactProposal":
        if self.label != self.label.strip() or self.assertion != self.assertion.strip():
            raise ValueError("table continuation proposal text must be stripped")
        if not self.selected_anchor_span_ids or len(self.selected_anchor_span_ids) != len(set(self.selected_anchor_span_ids)):
            raise ValueError("table continuation proposals require unique selected anchor spans")
        return self


class TableContinuationFactResponse(StrictModel):
    """Strict offline fake-LLM response shape for continued-table validation."""

    proposals: tuple[TableContinuationFactProposal, ...]

    @model_validator(mode="after")
    def validate_response(self) -> "TableContinuationFactResponse":
        proposal_keys = [item.proposal_key for item in self.proposals]
        if len(proposal_keys) != len(set(proposal_keys)):
            raise ValueError("table continuation fake-LLM proposals must have unique keys")
        return self


class TableContinuationFactGate(StrictModel):
    binding_id: str = Field(pattern=r"^table-continuation:[0-9a-f]{24}$")
    complete_table_state: TableContinuationFactGateState
    complete_table_proposal_key: str | None = None
    item_three_state: TableContinuationFactGateState
    item_three_proposal_key: str | None = None
    valid: bool
    basis: str = Field(min_length=16, max_length=1000)


def _candidate_state(
    *,
    candidates: tuple[TableContinuationFactProposal, ...],
    expected_type: FactType,
    required_anchor_span_ids: set[str],
    required_assertion: str | None = None,
) -> tuple[TableContinuationFactGateState, str | None]:
    if not candidates:
        return TableContinuationFactGateState.MISSING, None
    typed = tuple(item for item in candidates if item.fact_type == expected_type)
    if not typed:
        return TableContinuationFactGateState.FACT_TYPE_MISMATCH, None
    exact_anchor = tuple(item for item in typed if set(item.selected_anchor_span_ids) == required_anchor_span_ids)
    if not exact_anchor:
        return TableContinuationFactGateState.ANCHOR_SET_MISMATCH, None
    if required_assertion is not None:
        exact_assertion = tuple(
            item
            for item in exact_anchor
            if normalized_compare_text(item.assertion) == normalized_compare_text(required_assertion)
        )
        if not exact_assertion:
            return TableContinuationFactGateState.ASSERTION_MISMATCH, None
        exact_anchor = exact_assertion
    if len(exact_anchor) != 1:
        return TableContinuationFactGateState.AMBIGUOUS, None
    return TableContinuationFactGateState.COMPLETE, exact_anchor[0].proposal_key


def assess_table_continuation_facts(
    *, binding: TableContinuationBinding, proposals: Iterable[TableContinuationFactProposal]
) -> TableContinuationFactGate:
    """Require one whole-table composition and one exact continued item-③ threshold Fact."""

    frozen = tuple(proposals)
    complete_state, complete_key = _candidate_state(
        candidates=frozen,
        expected_type=FactType.COMPOSITION,
        required_anchor_span_ids=set(binding.complete_table_anchor_span_ids),
    )
    item_three_state, item_three_key = _candidate_state(
        candidates=frozen,
        expected_type=FactType.THRESHOLD,
        required_anchor_span_ids={
            binding.item_three_source_anchor_span_id,
            binding.item_three_closure_anchor_span_id,
        },
        required_assertion=binding.item_three_required_assertion,
    )
    valid = complete_state == TableContinuationFactGateState.COMPLETE and item_three_state == TableContinuationFactGateState.COMPLETE
    return TableContinuationFactGate(
        binding_id=binding.binding_id,
        complete_table_state=complete_state,
        complete_table_proposal_key=complete_key,
        item_three_state=item_three_state,
        item_three_proposal_key=item_three_key,
        valid=valid,
        basis=(
            "The complete-table composition selects every reviewed source/closure span and item ③ selects only its exact cross-page source and 1/400 closure spans."
            if valid
            else "A continued-table Fact is not publishable until both the whole-table and item-③ exact anchor contracts pass."
        ),
    )


class TableContinuationCardBackfillFact(StrictModel):
    source_draft_proposal_key: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    expected_fact_type: FactType
    gate_role: str = Field(pattern=r"^(complete_table|item_three_threshold)$")


class TableContinuationCardBackfillCard(StrictModel):
    card_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    expected_title: str = Field(min_length=2, max_length=64)
    expected_presentation: str = Field(pattern=r"^(table|threshold)$")
    required_source_draft_proposal_keys: tuple[str, ...]


def make_table_continuation_card_backfill_id(payload: object) -> str:
    return f"table-card-backfill:{_canonical_sha256(payload)[:24]}"


class TableContinuationCardGoldenBackfill(StrictModel):
    """A reviewed overlay for draft-only Card Golden entries; source YAML remains immutable."""

    schema_version: str = V2_SCHEMA_VERSION
    backfill_version: str = TABLE_CONTINUATION_CARD_BACKFILL_VERSION
    backfill_id: str = Field(pattern=r"^table-card-backfill:[0-9a-f]{24}$")
    sample_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    state: TableContinuationReviewState
    reviewer: str | None = None
    reviewed_at_utc: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    review_basis: str = Field(min_length=16, max_length=1200)
    source_card_golden_path: str = Field(min_length=1)
    source_card_golden_sha256: str = Field(pattern=_SHA256_PATTERN)
    table_continuation_binding_id: str = Field(pattern=r"^table-continuation:[0-9a-f]{24}$")
    table_continuation_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    facts: tuple[TableContinuationCardBackfillFact, ...]
    cards: tuple[TableContinuationCardBackfillCard, ...]

    @model_validator(mode="after")
    def validate_backfill(self) -> "TableContinuationCardGoldenBackfill":
        if self.backfill_version != TABLE_CONTINUATION_CARD_BACKFILL_VERSION:
            raise ValueError("unsupported table continuation Card backfill version")
        if self.state == TableContinuationReviewState.REVIEWED:
            if not self.reviewer or not self.reviewed_at_utc:
                raise ValueError("reviewed table Card backfills require reviewer metadata")
        elif self.reviewer is not None or self.reviewed_at_utc is not None:
            raise ValueError("draft table Card backfills cannot contain reviewer metadata")
        if self.review_basis != self.review_basis.strip():
            raise ValueError("table Card backfill review basis must be stripped")
        fact_keys = [item.source_draft_proposal_key for item in self.facts]
        card_keys = [item.card_key for item in self.cards]
        if len(fact_keys) != 2 or len(fact_keys) != len(set(fact_keys)):
            raise ValueError("table Card backfill requires exactly two unique draft Fact entries")
        if len(card_keys) != 2 or len(card_keys) != len(set(card_keys)):
            raise ValueError("table Card backfill requires exactly two unique draft Card entries")
        expected_id = make_table_continuation_card_backfill_id(
            self.model_dump(mode="json", exclude={"backfill_id"})
        )
        if self.backfill_id != expected_id:
            raise ValueError("table Card backfill ID does not match immutable content")
        return self


def load_table_continuation_card_backfill(path: Path) -> TableContinuationCardGoldenBackfill:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return TableContinuationCardGoldenBackfill.model_validate(raw)


class TableContinuationCardBackfillGate(StrictModel):
    backfill_id: str = Field(pattern=r"^table-card-backfill:[0-9a-f]{24}$")
    binding_id: str = Field(pattern=r"^table-continuation:[0-9a-f]{24}$")
    complete_table_fact_ready: bool
    item_three_threshold_fact_ready: bool
    stiffness_table_card_ready: bool
    deflection_limits_card_ready: bool
    valid: bool
    basis: str = Field(min_length=16, max_length=1000)


def verify_table_continuation_card_backfill(
    *,
    backfill: TableContinuationCardGoldenBackfill,
    binding: TableContinuationBinding,
    repository_root: Path,
) -> None:
    """Rebind an immutable draft Card Golden fixture without changing it."""

    if backfill.sample_id != binding.sample_id:
        raise ValueError("table Card backfill sample does not match table continuation binding")
    if backfill.table_continuation_binding_id != binding.binding_id or backfill.table_continuation_binding_sha256 != binding.fingerprint_sha256:
        raise ValueError("table Card backfill binding ID/hash does not match")
    fixture_path = _repository_path(
        root=repository_root,
        reference=backfill.source_card_golden_path,
        label="source Card Golden fixture",
    )
    if sha256_file(fixture_path) != backfill.source_card_golden_sha256:
        raise ValueError("table Card backfill source Card Golden hash does not match")
    # Import locally to avoid a module-level dependency cycle.
    from ..step2b_card_golden import CardGoldenReviewState, ContinuationCardGoldenFixture

    fixture = ContinuationCardGoldenFixture.model_validate(yaml.safe_load(fixture_path.read_text(encoding="utf-8")))
    if fixture.sample_id != backfill.sample_id:
        raise ValueError("table Card backfill source fixture sample does not match")
    facts = {item.proposal_key: item for item in fixture.facts}
    cards = {item.card_key: item for item in fixture.cards}
    for fact_entry in backfill.facts:
        source_fact = facts.get(fact_entry.source_draft_proposal_key)
        if source_fact is None or source_fact.review.state != CardGoldenReviewState.DRAFT:
            raise ValueError("table Card backfill may only target an existing draft Fact Golden entry")
    for card_entry in backfill.cards:
        source_card = cards.get(card_entry.card_key)
        if source_card is None or source_card.review.state != CardGoldenReviewState.DRAFT:
            raise ValueError("table Card backfill may only target an existing draft Card Golden entry")
        if (
            source_card.title != card_entry.expected_title
            or source_card.presentation.value != card_entry.expected_presentation
        ):
            raise ValueError("table Card backfill title/presentation does not match source draft Card Golden")
        if tuple(source_card.primary_proposal_keys) != card_entry.required_source_draft_proposal_keys:
            raise ValueError("table Card backfill Fact grouping does not match source draft Card Golden")


def assess_table_continuation_card_backfill(
    *,
    backfill: TableContinuationCardGoldenBackfill,
    binding: TableContinuationBinding,
    fact_gate: TableContinuationFactGate,
) -> TableContinuationCardBackfillGate:
    """Check that the reviewed overlay can recover only the two blocked Card targets.

    The source Card Golden YAML is intentionally not mutated.  This gate proves
    that a future fresh Step 2A/2B request may use the reviewed overlay only
    after it regenerates its own request/response provenance.
    """

    facts_by_role = {item.gate_role: item for item in backfill.facts}
    cards_by_key = {item.card_key: item for item in backfill.cards}
    complete_fact = facts_by_role.get("complete_table")
    item_three_fact = facts_by_role.get("item_three_threshold")
    complete_ready = (
        binding.state == TableContinuationReviewState.REVIEWED
        and backfill.state == TableContinuationReviewState.REVIEWED
        and fact_gate.complete_table_state == TableContinuationFactGateState.COMPLETE
        and complete_fact is not None
        and complete_fact.expected_fact_type == FactType.COMPOSITION
    )
    item_three_ready = (
        binding.state == TableContinuationReviewState.REVIEWED
        and backfill.state == TableContinuationReviewState.REVIEWED
        and fact_gate.item_three_state == TableContinuationFactGateState.COMPLETE
        and item_three_fact is not None
        and item_three_fact.expected_fact_type == FactType.THRESHOLD
    )
    stiffness = cards_by_key.get("formwork.stiffness-table")
    deflection = cards_by_key.get("formwork.deflection-limits")
    stiffness_ready = complete_ready and stiffness is not None and stiffness.expected_presentation == "table"
    deflection_ready = item_three_ready and deflection is not None and deflection.expected_presentation == "threshold"
    valid = complete_ready and item_three_ready and stiffness_ready and deflection_ready
    return TableContinuationCardBackfillGate(
        backfill_id=backfill.backfill_id,
        binding_id=binding.binding_id,
        complete_table_fact_ready=complete_ready,
        item_three_threshold_fact_ready=item_three_ready,
        stiffness_table_card_ready=stiffness_ready,
        deflection_limits_card_ready=deflection_ready,
        valid=valid,
        basis=(
            "The reviewed overlay restores only the previously draft stiffness-table and deflection-limit targets after exact cross-page Fact grounding."
            if valid
            else "Card Golden recovery remains blocked until the reviewed binding and both exact Fact gates pass."
        ),
    )
