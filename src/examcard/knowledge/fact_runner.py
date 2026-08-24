"""样本级与注册表级的确定性运行入口（拆分层 5）。

run_golden_fact_pipeline 是 step2a 的自动前置，外部唯一稳定入口；
入口处的三道语义校验（注册表 reviewed、侧车配对、material_profile_id 一致）不变。
"""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from ..evidence.annotations import load_golden_review_sidecar
from ..evidence.golden import GoldenSample, load_golden_registry
from ..evidence.schema import EvidenceKind, PageEvidence
from ..materials.catalog import load_content_policy
from ..materials.schema import ContentPolicy, PolicyDisposition

from .diagram.sidecar import DiagramReviewEntry, load_diagram_review_sidecar, reviewed_entry_map
from .diagram.disposition import DiagramDisposition, is_released_disposition, load_diagram_disposition_sidecar
from .schema import CoverageDisposition, CoverageMap, CoverageRecord

from .fact_types import DiagramDispositionBinding, EvidenceContext, FactBundle, ReviewedConflict
from .fact_support import _write_json
from .fact_policy import _policy_decision_id, decide_policy
from .fact_sidecars import (
    _load_selected_pages,
    _validate_diagram_disposition_sidecar,
    _validate_diagram_review_sidecar,
)
from .fact_review import _reviewed_conflicts, _reviewed_override_groups
from .fact_render import _render_evidence_map
from .fact_diagram import _diagram_disposition_for_units, _reviewed_diagram_for_units
from .fact_build import _group_included_evidence, _make_card, _make_fact
from .fact_quality import _dedup_audit, _machine_assertions, _quality_gate


def run_sample(
    *,
    sample: GoldenSample,
    pages: tuple[PageEvidence, ...],
    policy: ContentPolicy,
    conflicts: tuple[ReviewedConflict, ...],
    material_profile_id: str,
    diagram_reviews: dict[tuple[str, str], DiagramReviewEntry] | None = None,
    diagram_dispositions: dict[tuple[str, str], DiagramDispositionBinding] | None = None,
) -> dict[str, Any]:
    units = tuple(unit for page in pages for unit in page.evidence_units)
    if len({unit.evidence_id for unit in units}) != len(units):
        raise ValueError(f"{sample.sample_id}: duplicate evidence IDs across selected pages")
    decisions = tuple(decide_policy(unit=unit, policy=policy) for unit in units)
    contexts = tuple(EvidenceContext(unit=unit, decision=decision) for unit, decision in zip(units, decisions, strict=True))
    rendered = _render_evidence_map(units, policy)
    overrides = _reviewed_override_groups(contexts=contexts, conflicts=conflicts)
    grouped = _group_included_evidence(contexts=contexts, overrides=overrides)
    diagram_reviews = diagram_reviews or {}
    diagram_dispositions = diagram_dispositions or {}
    collected: list[FactBundle] = []
    audit_exclusions: dict[str, DiagramDispositionBinding] = {
        unit.evidence_id: binding
        for unit in units
        if unit.kind == EvidenceKind.FIGURE
        and (binding := diagram_dispositions.get((sample.sample_id, unit.evidence_id))) is not None
        and binding.entry.disposition == DiagramDisposition.NON_KP_VISUAL
    }
    for group, override_text in grouped:
        diagram_review = _reviewed_diagram_for_units(
            sample_id=sample.sample_id,
            units=group,
            reviews=diagram_reviews,
        )
        diagram_disposition = _diagram_disposition_for_units(
            sample_id=sample.sample_id,
            units=group,
            dispositions=diagram_dispositions,
        )
        if (
            diagram_disposition is not None
            and diagram_disposition.entry.disposition == DiagramDisposition.NON_KP_VISUAL
        ):
            audit_exclusions.update({unit.evidence_id: diagram_disposition for unit in group})
            continue
        collected.append(
            FactBundle(
                fact=_make_fact(
                    units=group,
                    rendered=rendered,
                    override_text=override_text,
                    diagram_review=diagram_review,
                    diagram_disposition=diagram_disposition,
                ),
                units=group,
                override_text=override_text,
                diagram_review=diagram_review,
                diagram_disposition=diagram_disposition,
            )
        )
    # 累加用的列表与最终的不可变元组分成两个名字：原先第二次赋值 `bundles = tuple(bundles)`
    # 会与首次绑定的 list[FactBundle] 冲突（assignment），而 bundles 声明成 list 之后又与
    # 下游要 tuple[FactBundle, ...] 的 _quality_gate 对不上（arg-type）。分名后两条一起消掉，
    # 下游用法一个字没改。
    bundles = tuple(collected)
    facts = tuple(bundle.fact for bundle in bundles)
    if len({fact.fact_id for fact in facts}) != len(facts):
        raise ValueError(f"{sample.sample_id}: duplicate fact IDs require explicit dedup review")
    plans_and_cards = tuple(
        _make_card(
            sample_id=sample.sample_id,
            bundle=bundle,
            material_profile_id=material_profile_id,
            policy=policy,
            rendered=rendered,
        )
        for bundle in bundles
    )
    plans = tuple(item[0] for item in plans_and_cards)
    cards = tuple(item[1] for item in plans_and_cards)
    owner_by_evidence = {
        evidence_id: card.raw_id
        for bundle, card in zip(bundles, cards, strict=True)
        for evidence_id in bundle.fact.primary_evidence_ids
    }
    records: list[CoverageRecord] = []
    for context in contexts:
        unit = context.unit
        decision = context.decision
        decision_id = _policy_decision_id(policy=policy, evidence_id=unit.evidence_id)
        audit_binding = audit_exclusions.get(unit.evidence_id)
        if audit_binding is not None:
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.EXCLUDED_BY_AUDIT,
                policy_decision_id=decision_id,
                reason=(
                    f"由图示处置审计 {audit_binding.entry.disposition_id} 标记为 "
                    "non_kp_visual；保留原始图像证据和高分辨率复核绑定，不生成 Fact/Card。"
                ),
            )
        elif decision.disposition == PolicyDisposition.INCLUDED:
            owner = owner_by_evidence.get(unit.evidence_id)
            if owner is None:
                raise ValueError(f"{sample.sample_id}: included evidence lacks primary fact {unit.evidence_id}")
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.PRIMARY_ASSIGNED,
                coverage_owner=owner,
                policy_decision_id=decision_id,
                reason="由唯一 primary fact/card 按 anchor span 主覆盖。",
            )
        elif decision.disposition == PolicyDisposition.EXCLUDED_BY_POLICY:
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.EXCLUDED_BY_POLICY,
                policy_decision_id=decision_id,
                reason="例题或答案作为有效 evidence 保留，但被当前 Content Policy 非破坏性分流。",
            )
        elif decision.disposition == PolicyDisposition.IGNORED_NOISE:
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.IGNORED_NOISE,
                policy_decision_id=decision_id,
                reason="已确认页面噪声或装饰。",
            )
        else:
            record = CoverageRecord(
                evidence_id=unit.evidence_id,
                disposition=CoverageDisposition.MANUAL_REVIEW,
                policy_decision_id=decision_id,
                reason="Content Policy 尚未允许自动消费该 evidence。",
            )
        records.append(record)
    coverage = CoverageMap(
        material_profile_id=material_profile_id,
        content_policy_id=policy.policy_id,
        content_policy_revision=policy.revision,
        records=tuple(records),
    )
    dedup = _dedup_audit(cards, facts)
    gate = _quality_gate(
        contexts=contexts,
        bundles=bundles,
        cards=cards,
        coverage=coverage,
        conflicts=conflicts,
        dedup=dedup,
        policy=policy,
        audit_excluded_evidence_ids=frozenset(audit_exclusions),
    )
    assertions = _machine_assertions(sample=sample, units=units, coverage=coverage, conflicts=conflicts, policy=policy)
    return {
        "sample_id": sample.sample_id,
        "pdf_sha256": sample.pdf_sha256,
        "selected_pages": list(sample.selected_pages),
        "facts": [fact.model_dump(mode="json") for fact in facts],
        "card_plans": [plan.model_dump(mode="json") for plan in plans],
        "cards": [card.model_dump(mode="json") for card in cards],
        "policy_decisions": [decision.model_dump(mode="json") for decision in decisions],
        "coverage": coverage.model_dump(mode="json"),
        "quality_gate": gate.model_dump(mode="json"),
        "dedup": dedup,
        "machine_assertions": list(assertions),
        "summary": {
            "evidence_count": len(units),
            "fact_count": len(facts),
            "card_count": len(cards),
            "policy_dispositions": dict(Counter(decision.disposition.value for decision in decisions)),
            "renderability": dict(Counter(card.renderability.value for card in cards)),
            "presentation_transform_count": sum(
                len(fact.presentation_transforms) for fact in facts
            ),
            "must_cover_count": gate.must_cover_total,
            "quality_gate_passed": gate.publishable,
            "machine_assertions_passed": all(item["passed"] for item in assertions),
        },
    }


def run_golden_fact_pipeline(
    *, registry_path: Path,
    sidecar_path: Path,
    content_policy_path: Path,
    evidence_root: Path,
    output_root: Path | None = None,
    diagram_review_sidecar_path: Path | None = None,
    diagram_disposition_sidecar_path: Path | None = None,
) -> dict[str, Any]:
    registry = load_golden_registry(registry_path)
    sidecar = load_golden_review_sidecar(sidecar_path)
    policy = load_content_policy(content_policy_path)
    if not registry.ready_for_phase1:
        raise ValueError("Phase 2 requires every golden sample to be reviewed")
    if sidecar.state.value != "reviewed" or sidecar.registry_id != registry.registry_id:
        raise ValueError("Phase 2 requires the reviewed sidecar for this golden registry")
    if registry.material_profile_id != sidecar.material_profile_id:
        raise ValueError("golden registry and sidecar material profiles do not match")
    diagram_sidecar = (
        load_diagram_review_sidecar(diagram_review_sidecar_path)
        if diagram_review_sidecar_path is not None
        else None
    )
    if diagram_sidecar is not None:
        _validate_diagram_review_sidecar(sidecar=diagram_sidecar, registry=registry, evidence_root=evidence_root)
    diagram_reviews = reviewed_entry_map(diagram_sidecar) if diagram_sidecar is not None else {}
    diagram_disposition_sidecar = (
        load_diagram_disposition_sidecar(diagram_disposition_sidecar_path)
        if diagram_disposition_sidecar_path is not None
        else None
    )
    if diagram_disposition_sidecar is not None:
        if diagram_sidecar is None or diagram_review_sidecar_path is None:
            raise ValueError("diagram disposition sidecar requires its bound diagram review sidecar")
        diagram_dispositions = _validate_diagram_disposition_sidecar(
            sidecar=diagram_disposition_sidecar,
            diagram_sidecar=diagram_sidecar,
            diagram_sidecar_sha256=hashlib.sha256(diagram_review_sidecar_path.read_bytes()).hexdigest(),
            registry=registry,
        )
    else:
        diagram_dispositions = {}
    samples: list[dict[str, Any]] = []
    for sample in registry.samples:
        pages = _load_selected_pages(sample=sample, evidence_root=evidence_root)
        conflicts = _reviewed_conflicts(sidecar=sidecar, sample_id=sample.sample_id, pages=pages)
        result = run_sample(
            sample=sample,
            pages=pages,
            policy=policy,
            conflicts=conflicts,
            material_profile_id=registry.material_profile_id,
            diagram_reviews=diagram_reviews,
            diagram_dispositions=diagram_dispositions,
        )
        samples.append(result)
        if output_root is not None:
            _write_json(output_root / sample.sample_id / "fact_run.json", result)
    summary = {
        "schema_version": "2.0",
        "stage": "phase2_fact_card_coverage",
        "registry_id": registry.registry_id,
        "sidecar_id": sidecar.sidecar_id,
        "annotation_revision": sidecar.annotation_revision,
        "material_profile_id": registry.material_profile_id,
        "content_policy_id": policy.policy_id,
        "content_policy_revision": policy.revision,
        "diagram_review_sidecar_id": diagram_sidecar.sidecar_id if diagram_sidecar is not None else None,
        "diagram_review_annotation_revision": diagram_sidecar.annotation_revision if diagram_sidecar is not None else None,
        "diagram_review_entries": len(diagram_sidecar.entries) if diagram_sidecar is not None else 0,
        "diagram_reviewed_entries": len(diagram_reviews),
        "diagram_disposition_sidecar_id": (
            diagram_disposition_sidecar.sidecar_id if diagram_disposition_sidecar is not None else None
        ),
        "diagram_disposition_annotation_revision": (
            diagram_disposition_sidecar.annotation_revision if diagram_disposition_sidecar is not None else None
        ),
        "diagram_disposition_entries": len(diagram_dispositions),
        "diagram_disposition_released_entries": sum(
            is_released_disposition(binding.entry.disposition) for binding in diagram_dispositions.values()
        ),
        "sample_count": len(samples),
        "selected_page_count": sum(len(sample.selected_pages) for sample in registry.samples),
        "evidence_count": sum(sample["summary"]["evidence_count"] for sample in samples),
        "fact_count": sum(sample["summary"]["fact_count"] for sample in samples),
        "card_count": sum(sample["summary"]["card_count"] for sample in samples),
        "must_cover_count": sum(sample["summary"]["must_cover_count"] for sample in samples),
        "quality_gates_passed": all(sample["summary"]["quality_gate_passed"] for sample in samples),
        "machine_assertions_passed": all(sample["summary"]["machine_assertions_passed"] for sample in samples),
        "samples": samples,
    }
    if output_root is not None:
        _write_json(output_root / "golden_fact_run_report.json", summary)
    return summary
