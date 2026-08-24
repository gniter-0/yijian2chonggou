"""Deterministic Phase 2 Fact/Card/Coverage runner for reviewed V2 evidence.

The runner intentionally consumes only immutable Step 1 snapshots plus the
reviewed sidecar. It does not call an LLM, OCR, Vision, or any V1 module.

cm21 拆分后本文件只做再导出：实现搬到同级 fact_* 兄弟模块（层序见
docs/prework/fact_engine_split_blueprint.md 第 3 节），导入路径
examcard.knowledge.fact_engine 一字不改。__all__ 连私有助手一并列出，
因为随迁测试确实从本模块 import 私有函数（如 _dedup_audit），
门面的职责是把原有名字面 100% 兜住，不是重新定义什么算公开。
"""
from __future__ import annotations

from .fact_lexicon import (
    _NUMBER_RE,
    _CONSTRAINT_TERMS,
    _CONTAINER_KINDS,
    _VISUAL_KINDS,
    _EMBEDDED_EXAMPLE_RE,
    _CJK_RE,
    _DIAGRAM_PLACEHOLDER,
)
from .fact_types import (
    ReviewedConflict,
    EvidenceContext,
    FactBundle,
    RenderedEvidence,
    DiagramAssessment,
    DiagramDispositionBinding,
)
from .fact_support import ROOT, _read_json, _write_json, _bbox_overlaps, _bbox_contains, _text_sha256
from .fact_policy import _policy_decision_id, _matches_known_noise, decide_policy
from .fact_sidecars import (
    _load_selected_pages,
    _validate_diagram_review_sidecar,
    _validate_diagram_disposition_sidecar,
)
from .fact_review import (
    _conflict_evidence_ids,
    _reviewed_conflicts,
    _conflict_is_resolved,
    _reviewed_override_groups,
)
from .fact_render import _render_evidence, _render_evidence_map, _display_text, _content_units, _group_text
from .fact_classify import _fact_type, _completeness_mode, _number_claims, _label
from .fact_diagram import (
    _is_readable_diagram_label,
    _reviewed_diagram_for_units,
    _diagram_disposition_for_units,
    _reviewed_diagram_text,
    _diagram_assessment,
)
from .fact_build import _make_fact, _group_included_evidence, _presentation, _make_card
from .fact_quality import _dedup_audit, _machine_assertions, _quality_gate
from .fact_runner import run_sample, run_golden_fact_pipeline


__all__ = [
    "DiagramAssessment",
    "DiagramDispositionBinding",
    "EvidenceContext",
    "FactBundle",
    "ROOT",
    "RenderedEvidence",
    "ReviewedConflict",
    "_CJK_RE",
    "_CONSTRAINT_TERMS",
    "_CONTAINER_KINDS",
    "_DIAGRAM_PLACEHOLDER",
    "_EMBEDDED_EXAMPLE_RE",
    "_NUMBER_RE",
    "_VISUAL_KINDS",
    "_bbox_contains",
    "_bbox_overlaps",
    "_completeness_mode",
    "_conflict_evidence_ids",
    "_conflict_is_resolved",
    "_content_units",
    "_dedup_audit",
    "_diagram_assessment",
    "_diagram_disposition_for_units",
    "_display_text",
    "_fact_type",
    "_group_included_evidence",
    "_group_text",
    "_is_readable_diagram_label",
    "_label",
    "_load_selected_pages",
    "_machine_assertions",
    "_make_card",
    "_make_fact",
    "_matches_known_noise",
    "_number_claims",
    "_policy_decision_id",
    "_presentation",
    "_quality_gate",
    "_read_json",
    "_render_evidence",
    "_render_evidence_map",
    "_reviewed_conflicts",
    "_reviewed_diagram_for_units",
    "_reviewed_diagram_text",
    "_reviewed_override_groups",
    "_text_sha256",
    "_validate_diagram_disposition_sidecar",
    "_validate_diagram_review_sidecar",
    "_write_json",
    "decide_policy",
    "run_golden_fact_pipeline",
    "run_sample",
]
