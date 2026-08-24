"""AtomicFact 与 KnowledgeCard 的装配（拆分层 3）。"""
from __future__ import annotations

from ..evidence.schema import EvidenceKind, EvidenceUnit, SourceQuality
from ..materials.schema import ContentPolicy, PolicyDisposition

from .diagram.sidecar import DiagramReviewEntry
from .diagram.disposition import is_released_disposition
from .identity import make_card_raw_id, make_fact_id
from .schema import (
    AtomicFact,
    CardPlan,
    CompletenessMode,
    ContentQualityFlag,
    ContentSegment,
    EvidenceRef,
    FactType,
    GroundingStatus,
    KnowledgeCard,
    Renderability,
)

from .fact_lexicon import _CONSTRAINT_TERMS, _CONTAINER_KINDS, _DIAGRAM_PLACEHOLDER
from .fact_types import (
    DiagramAssessment,
    DiagramDispositionBinding,
    EvidenceContext,
    FactBundle,
    RenderedEvidence,
)
from .fact_support import _bbox_contains
from .fact_render import _content_units, _display_text, _group_text
from .fact_classify import _completeness_mode, _fact_type, _label, _number_claims
from .fact_diagram import _diagram_assessment, _reviewed_diagram_text


def _make_fact(
    *,
    units: tuple[EvidenceUnit, ...],
    rendered: dict[str, RenderedEvidence],
    override_text: str | None = None,
    diagram_review: DiagramReviewEntry | None = None,
    diagram_disposition: DiagramDispositionBinding | None = None,
) -> AtomicFact:
    if diagram_review is not None and diagram_disposition is not None:
        raise ValueError("a diagram fact cannot use both a structure review and a disposition")
    disposition_entry = diagram_disposition.entry if diagram_disposition is not None else None
    # 「这是一条已发布的图示处置」同时也是 disposition_entry 非空的担保。原先只用一个布尔量
    # 转述该担保，mypy 不跨条件收窄，三处 disposition_entry.X 各报一条 union-attr。改成把担保
    # 记在一个可空绑定上：released_entry 非空 ⇔ 原来的 released_visual 为真，取值逐点等价，
    # 而收窄这回是结构性的，不再靠注释维持。
    released_entry = disposition_entry if disposition_entry is not None and is_released_disposition(disposition_entry.disposition) else None
    released_visual = released_entry is not None
    text = (
        _reviewed_diagram_text(diagram_review)
        if diagram_review is not None
        else (released_entry.title if released_entry is not None else (override_text or _group_text(units, rendered)))
    )
    if text is None:
        raise ValueError("released visual disposition requires a display title")
    fact_type = FactType.VISUAL_REFERENCE if released_visual else _fact_type(units, text)
    assessment = (
        _diagram_assessment(units=units, rendered=rendered, review=diagram_review)
        if fact_type == FactType.DIAGRAM_RELATION
        else DiagramAssessment(renderability=Renderability.PUBLISHABLE)
    )
    if assessment.renderability == Renderability.MANUAL_REVIEW:
        text = _DIAGRAM_PLACEHOLDER
    mode = CompletenessMode.ATOMIC if fact_type == FactType.VISUAL_REFERENCE else _completeness_mode(fact_type)
    numbers = _number_claims(text=text, units=units, rendered=rendered)
    assertion_contract = {
        "assertion": text,
        "completeness_mode": mode.value,
        "numbers": [item.model_dump(mode="json") for item in numbers],
        "conditions": [term for term in _CONSTRAINT_TERMS if term in text],
    }
    return AtomicFact(
        fact_id=make_fact_id(
            fact_type=fact_type.value,
            primary_evidence_fingerprints=[unit.fingerprint for unit in units],
            assertion_contract=assertion_contract,
        ),
        fact_type=fact_type,
        label=(diagram_review.title if diagram_review is not None else (released_entry.title if released_entry is not None else _label(text))),
        assertion=text,
        completeness_mode=mode,
        primary_evidence_ids=tuple(unit.evidence_id for unit in units),
        conditions=tuple(term for term in _CONSTRAINT_TERMS if term in text),
        numbers=numbers,
        ordered_member_ids=tuple(unit.evidence_id for unit in units) if mode != CompletenessMode.ATOMIC else (),
        must_cover=any(unit.must_cover_by_source for unit in units),
        confidence=min(unit.confidence for unit in units),
        renderability=assessment.renderability,
        quality_flags=(
            (ContentQualityFlag.VISUAL_REFERENCE_ONLY,)
            if released_visual
            else assessment.quality_flags
        ),
        presentation_transforms=tuple(
            transform
            for unit in units
            for transform in rendered[unit.evidence_id].presentation_transforms
        ),
        diagram_review_id=assessment.review.review_id if assessment.review is not None else None,
        diagram_disposition_id=released_entry.disposition_id if released_entry is not None else None,
        diagram_disposition_trace=diagram_disposition.trace if released_visual and diagram_disposition is not None else None,
    )


def _group_included_evidence(
    *, contexts: tuple[EvidenceContext, ...], overrides: tuple[tuple[tuple[str, ...], str], ...]
) -> tuple[tuple[tuple[EvidenceUnit, ...], str | None], ...]:
    included = [context.unit for context in contexts if context.decision.disposition == PolicyDisposition.INCLUDED]
    by_id = {unit.evidence_id: unit for unit in included}
    consumed: set[str] = set()
    groups: list[tuple[tuple[EvidenceUnit, ...], str | None]] = []

    for ids, override_text in overrides:
        units = tuple(by_id[evidence_id] for evidence_id in ids)
        consumed.update(ids)
        groups.append((units, override_text))

    for container in sorted((unit for unit in included if unit.kind in _CONTAINER_KINDS), key=lambda item: (item.locator.page_num, item.order)):
        if container.evidence_id in consumed:
            continue
        members = [
            unit
            for unit in included
            if unit.evidence_id not in consumed
            and unit.locator.page_num == container.locator.page_num
            and (_bbox_contains(container.locator.bbox, unit.locator.bbox) or unit.evidence_id == container.evidence_id)
        ]
        if not members:
            members = [container]
        members.sort(key=lambda item: item.order)
        consumed.update(unit.evidence_id for unit in members)
        groups.append((tuple(members), None))

    current: list[EvidenceUnit] = []
    current_page: int | None = None
    current_size = 0

    def flush() -> None:
        nonlocal current, current_size
        if current:
            groups.append((tuple(current), None))
        current = []
        current_size = 0

    for unit in sorted((item for item in included if item.evidence_id not in consumed), key=lambda item: (item.locator.page_num, item.order)):
        unit_size = len(_display_text(unit))
        if current_page is not None and unit.locator.page_num != current_page:
            flush()
        if unit.kind == EvidenceKind.HEADING and current:
            flush()
        if current and current_size + unit_size > 500 and unit.kind not in {EvidenceKind.LIST_ITEM, EvidenceKind.FORMULA}:
            flush()
        current.append(unit)
        current_page = unit.locator.page_num
        current_size += unit_size
    flush()
    return tuple(groups)


def _presentation(fact_type: FactType) -> str:
    return {
        FactType.FORMULA: "formula",
        FactType.ENUMERATION: "ordered_list",
        FactType.COMPOSITION: "table",
        FactType.DIAGRAM_RELATION: "diagram",
        FactType.VISUAL_REFERENCE: "visual_reference",
        FactType.EXCEPTION: "constraint",
        FactType.THRESHOLD: "threshold",
    }.get(fact_type, "text")


def _make_card(
    *,
    sample_id: str,
    bundle: FactBundle,
    material_profile_id: str,
    policy: ContentPolicy,
    rendered: dict[str, RenderedEvidence],
) -> tuple[CardPlan, KnowledgeCard]:
    fact = bundle.fact
    raw_id = make_card_raw_id(
        material_profile_id=material_profile_id,
        content_policy_id=policy.policy_id,
        primary_fact_ids=[fact.fact_id],
    )
    plan = CardPlan(
        raw_id=raw_id,
        title=fact.label,
        primary_fact_ids=(fact.fact_id,),
        kp_type="visual_reference" if fact.fact_type == FactType.VISUAL_REFERENCE else "content",
        presentation=_presentation(fact.fact_type),
        split_reason="按唯一 primary evidence anchor span 分组；不与相似标题跨 span 合并。",
    )
    if bundle.diagram_review is not None:
        # 形状 B：这处赋值是个 1 元组，会被推成定长 tuple[ContentSegment]，下面 else 支路用
        # 生成器构造的 tuple[ContentSegment, ...] 就报 assignment。显式注解一次即可，四条支路
        # 的取值一个没动。
        segments: tuple[ContentSegment, ...] = (
            ContentSegment(
                text=fact.assertion,
                fact_ids=(fact.fact_id,),
                evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                renderer="reviewed_diagram",
                presentation_transforms=fact.presentation_transforms,
            ),
        )
    elif bundle.diagram_disposition is not None and is_released_disposition(bundle.diagram_disposition.entry.disposition):
        segments = (
            ContentSegment(
                text=fact.assertion,
                fact_ids=(fact.fact_id,),
                evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                renderer="visual_reference",
                presentation_transforms=fact.presentation_transforms,
            ),
        )
    elif fact.renderability == Renderability.MANUAL_REVIEW:
        segments = (
            ContentSegment(
                text=fact.assertion,
                fact_ids=(fact.fact_id,),
                evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                renderer="manual_review",
                presentation_transforms=fact.presentation_transforms,
            ),
        )
    elif bundle.override_text is not None:
        segments = (
            ContentSegment(
                text=bundle.override_text,
                fact_ids=(fact.fact_id,),
                evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                renderer="reviewed_conflict",
                presentation_transforms=fact.presentation_transforms,
            ),
        )
    else:
        content_units = _content_units(bundle.units, rendered)
        segments = tuple(
            ContentSegment(
                text=_display_text(unit, rendered),
                fact_ids=(fact.fact_id,),
                evidence_ids=(unit.evidence_id,),
                renderer=_presentation(fact.fact_type),
                presentation_transforms=rendered[unit.evidence_id].presentation_transforms,
            )
            for unit in content_units
        )
        if not segments:
            segments = (
                ContentSegment(
                    text=fact.assertion,
                    fact_ids=(fact.fact_id,),
                    evidence_ids=tuple(unit.evidence_id for unit in bundle.units),
                    renderer="structural_anchor",
                    presentation_transforms=fact.presentation_transforms,
                ),
            )
    refs = tuple(
        EvidenceRef(
            evidence_id=unit.evidence_id,
            page_num=unit.locator.page_num,
            bbox=unit.locator.bbox,
            visual_only=unit.source_quality == SourceQuality.VISUAL_ONLY,
        )
        for unit in bundle.units
    )
    card = KnowledgeCard(
        id=f"{sample_id}:{raw_id}",
        raw_id=raw_id,
        title=fact.label,
        content="\n".join(segment.text for segment in segments),
        content_segments=segments,
        evidence_refs=refs,
        grounding_status=(
            GroundingStatus.MANUAL_VERIFIED
            if bundle.diagram_review is not None or bundle.diagram_disposition is not None and is_released_disposition(bundle.diagram_disposition.entry.disposition)
            else (GroundingStatus.UNRESOLVED if fact.renderability == Renderability.MANUAL_REVIEW else GroundingStatus.VERIFIED)
        ),
        renderability=fact.renderability,
        quality_flags=fact.quality_flags,
        key_words=tuple(sorted({fact.fact_type.value, *fact.conditions})),
        diagram_type=(
            bundle.diagram_review.diagram_type
            if bundle.diagram_review is not None
            else (bundle.diagram_disposition.entry.disposition.value if bundle.diagram_disposition is not None and is_released_disposition(bundle.diagram_disposition.entry.disposition) else ("flow" if fact.fact_type == FactType.DIAGRAM_RELATION else ""))
        ),
        diagram_elements=(
            tuple(node.label for node in bundle.diagram_review.nodes)
            if bundle.diagram_review is not None
            else ()
        ),
        annotations=(
            tuple(
                f"{next(node.label for node in bundle.diagram_review.nodes if node.node_id == edge.source_node_id)}"
                f"{edge.relation}"
                f"{next(node.label for node in bundle.diagram_review.nodes if node.node_id == edge.target_node_id)}"
                for edge in bundle.diagram_review.edges
            )
            if bundle.diagram_review is not None
            else ()
        ),
        diagram_review_id=fact.diagram_review_id,
        diagram_disposition_id=fact.diagram_disposition_id,
        diagram_disposition_trace=fact.diagram_disposition_trace,
        source_pages=tuple(sorted({unit.locator.page_num for unit in bundle.units})),
        kp_type=plan.kp_type,
    )
    return plan, card
