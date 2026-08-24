"""Facade completeness for examcard.knowledge.fact_engine (blueprint section 8-2).

cm21 把实现搬到同级 fact_* 兄弟后，`fact_engine` 只剩再导出。本封印钉住三件事：

1. 拆前 52 个顶层符号（含私有助手）逐一仍可从 `examcard.knowledge.fact_engine` 取到，
   且 `__all__` 与之精确相等——多一个少一个都算门面漂移；
2. 机器扫出的 3 个真实外部名（8 个 importer 文件、无整模块导入）绑定的就是新归属模块里的同一对象；
3. `ROOT` 的锚点深度没被搬迁改坏：它是全 src 唯一一处自锚定，指向仓根。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import examcard.knowledge.fact_engine as fact_engine
from examcard.knowledge.fact_policy import decide_policy as policy_decide_policy
from examcard.knowledge.fact_quality import _dedup_audit as quality_dedup_audit
from examcard.knowledge.fact_runner import run_golden_fact_pipeline as runner_pipeline

# 拆前 fact_engine.py 的顶层符号表（AST 扫描，52 个，含常量与 dataclass）。
PRE_SPLIT_NAMES = (
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
)

# 机器扫描的真实外部使用面：8 个 importer 文件只取这 3 个名字。
EXTERNAL_NAMES = ("_dedup_audit", "decide_policy", "run_golden_fact_pipeline")


def test_pre_split_symbol_table_is_52_names() -> None:
    assert len(PRE_SPLIT_NAMES) == 52
    assert len(set(PRE_SPLIT_NAMES)) == 52


def test_every_pre_split_name_is_importable_from_the_facade() -> None:
    missing = [name for name in PRE_SPLIT_NAMES if not hasattr(fact_engine, name)]
    assert not missing, f"facade lost pre-split names: {missing}"


def test_all_equals_the_pre_split_symbol_table() -> None:
    exported = set(fact_engine.__all__)
    assert exported == set(PRE_SPLIT_NAMES), f"__all__ drifted: {exported ^ set(PRE_SPLIT_NAMES)}"


def test_all_entries_resolve() -> None:
    broken = [name for name in fact_engine.__all__ if not hasattr(fact_engine, name)]
    assert not broken, f"__all__ declares unresolvable names: {broken}"


def test_facade_defines_no_implementation_of_its_own() -> None:
    """门面只转口：每个函数/类都必须归属某个 fact_* 兄弟。

    只看函数与类：常量（Path / 已编译正则 / frozenset）的 `__module__` 取自它们的类型
    （pathlib、re），不是定义位置，据此判断会误伤。
    """
    stray = []
    for name in fact_engine.__all__:
        obj = getattr(fact_engine, name)
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        owner = obj.__module__
        if owner == "examcard.knowledge.fact_engine" or not owner.startswith("examcard.knowledge.fact_"):
            stray.append(f"{name} <- {owner}")
    assert not stray, f"facade 自己定义了实现或从家族外取名: {stray}"


def test_external_names_bind_their_new_owners() -> None:
    assert fact_engine.run_golden_fact_pipeline is runner_pipeline
    assert fact_engine.decide_policy is policy_decide_policy
    assert fact_engine._dedup_audit is quality_dedup_audit
    for name in EXTERNAL_NAMES:
        assert name in fact_engine.__all__


def test_root_anchor_still_points_at_the_repo_root() -> None:
    """fact_support.ROOT 用 parents[3] 自锚定；子模块与门面同目录，深度必须不变。"""
    assert fact_engine.ROOT == Path(__file__).resolve().parents[1]
    assert (fact_engine.ROOT / "pyproject.toml").is_file()