"""Facade completeness for examcard.pipeline.live_run (blueprint section 8-2).

cm26 把实现搬到同级 live_* 兄弟后，`live_run` 只剩再导出。本封印钉住五件事：

1. 拆前 50 个顶层符号（含常量与私有助手）逐一仍可从 `examcard.pipeline.live_run` 取到，
   且 `__all__` 与之精确相等——多一个少一个都算门面漂移；
2. 机器扫出的 28 个真实外部名（6 个 importer 文件）绑定的就是新归属模块里的**同一对象**；
3. 门面自己不定义实现，取到的每个函数/类都归属某个 live_* 兄弟；
4. **`_REPO_ROOT` 的 `parents[3]` 深度没被搬歪**：它锚的是 src/examcard/<pkg>/<mod>.py，
   同级搬迁本不改变深度，但这类「顺手改数字」是拆分里最典型的静默故障——直接断言它等于
   仓根，且仓根下 pyproject.toml 与缺省单价表都在；
5. **pydantic 模型在新模块里仍然建完**：全家都开了 `from __future__ import annotations`，
   字段注解是字符串，必须在 *定义模块* 的命名空间里可解析。搬迁最可能悄悄坏在这一点上——
   模型会以未完成状态存在，直到第一次实例化才炸。这里直接断言 `__pydantic_complete__`。
"""
from __future__ import annotations

import inspect
from importlib import import_module
from pathlib import Path

import examcard
import examcard.pipeline.live_run as live_run

# 拆前 live_run.py 的顶层符号表（AST 扫描，50 个）。
PRE_SPLIT_NAMES = (
    "DEFAULT_PRICING_RELATIVE",
    "ExamPaths",
    "LIVE_RUN_REPORT_VERSION",
    "LIVE_STAGES",
    "LIVE_STAGE_REPORT_VERSION",
    "LiveBudgetExceeded",
    "LiveRunBudget",
    "LiveRunConfig",
    "LiveRunReport",
    "LiveStageReport",
    "MissingUpstreamArtifact",
    "ResendPlan",
    "ResendRoundsExhausted",
    "RunState",
    "_REASON_MAX_LENGTH",
    "_REASON_PATTERN",
    "_REPO_ROOT",
    "_RUN_LABEL_MAX",
    "_STAGE_ORDER",
    "_STAGE_PREFLIGHT_KEYS",
    "_STATUS_TO_DETAIL",
    "_cache_state",
    "_channel_value",
    "_utc_now_compact",
    "_utc_now_iso",
    "budget_rehearsal_estimate",
    "build_admission_checkpoint",
    "build_live_run_report",
    "build_stage_report",
    "default_env_path",
    "derive_stage_call_caps",
    "emit_image_review_sheet",
    "evaluate_preflight_gate",
    "execute_stage_samples",
    "image_api_key",
    "image_base_url",
    "image_channel_fallback",
    "ingest_admission_decisions",
    "ingest_image_review_sheet",
    "load_live_pricing",
    "load_run_env",
    "load_step3_release_bypass",
    "load_typed_fixture_compilation",
    "materialize_live_run",
    "new_run_id",
    "orchestrate",
    "run_step3_stage",
    "run_step4_stage",
    "text_api_key",
    "text_base_url",
)

# 机器扫描的真实外部使用面：6 个 importer 文件（4 个 migration 脚本、2 个测试）只取这 28 个名字。
# 值 = 拆分后的新归属模块。live_stage_step4 一个外部名都没有：出图执行器只经 orchestrate 到达，
# 这也是它拆出来后唯一的调用路径。
EXTERNAL_OWNERS = {
    "DEFAULT_PRICING_RELATIVE": "live_support",
    "LiveBudgetExceeded": "live_budget",
    "LiveRunBudget": "live_budget",
    "load_live_pricing": "live_budget",
    "ExamPaths": "live_paths",
    "MissingUpstreamArtifact": "live_paths",
    "default_env_path": "live_env",
    "image_api_key": "live_env",
    "image_base_url": "live_env",
    "image_channel_fallback": "live_env",
    "load_run_env": "live_env",
    "text_api_key": "live_env",
    "text_base_url": "live_env",
    "ResendPlan": "live_resend",
    "ResendRoundsExhausted": "live_resend",
    "evaluate_preflight_gate": "live_preflight",
    "build_live_run_report": "live_reports",
    "build_stage_report": "live_reports",
    "load_typed_fixture_compilation": "live_stage_step3",
    "build_admission_checkpoint": "live_checkpoint",
    "emit_image_review_sheet": "live_checkpoint",
    "ingest_admission_decisions": "live_checkpoint",
    "ingest_image_review_sheet": "live_checkpoint",
    "LiveRunConfig": "live_config",
    "execute_stage_samples": "live_stage_samples",
    "budget_rehearsal_estimate": "live_rehearsal",
    "derive_stage_call_caps": "live_rehearsal",
    "orchestrate": "live_orchestrate",
}


def test_pre_split_symbol_table_is_50_names() -> None:
    assert len(PRE_SPLIT_NAMES) == 50
    assert len(set(PRE_SPLIT_NAMES)) == 50


def test_every_pre_split_name_is_importable_from_the_facade() -> None:
    missing = [name for name in PRE_SPLIT_NAMES if not hasattr(live_run, name)]
    assert not missing, f"facade lost pre-split names: {missing}"


def test_all_equals_the_pre_split_symbol_table() -> None:
    exported = set(live_run.__all__)
    assert exported == set(PRE_SPLIT_NAMES), f"__all__ drifted: {exported ^ set(PRE_SPLIT_NAMES)}"


def test_all_entries_resolve() -> None:
    broken = [name for name in live_run.__all__ if not hasattr(live_run, name)]
    assert not broken, f"__all__ declares unresolvable names: {broken}"


def test_facade_defines_no_implementation_of_its_own() -> None:
    """门面只转口：每个函数/类都必须归属某个 live_* 兄弟。

    只看函数与类：常量（`LIVE_STAGES`、`_STAGE_ORDER`、`_REASON_PATTERN` 等）没有定义位置
    意义上的 `__module__`，据此判断会误伤。
    """
    stray = []
    for name in live_run.__all__:
        obj = getattr(live_run, name)
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        owner = obj.__module__
        if owner == "examcard.pipeline.live_run" or not owner.startswith("examcard.pipeline.live_"):
            stray.append(f"{name} <- {owner}")
    assert not stray, f"facade 自己定义了实现或从家族外取名: {stray}"


def test_external_names_bind_their_new_owners() -> None:
    """28 个外部名逐一用 `is` 比对新归属模块里的同一对象。

    归属模块里取不到名字时走哨兵而不是让 `getattr` 抛 AttributeError：归属表写错一格就中断，
    只会报出第一条坏项；哨兵让 28 条全部走完，一次给齐 mismatch 清单。
    """
    assert len(EXTERNAL_OWNERS) == 28
    missing = object()
    mismatched = []
    for name, module_name in sorted(EXTERNAL_OWNERS.items()):
        owner_module = import_module(f"examcard.pipeline.{module_name}")
        owned = getattr(owner_module, name, missing)
        if owned is missing:
            mismatched.append(f"{module_name} 里没有 {name}")
        elif getattr(live_run, name) is not owned:
            mismatched.append(f"{name} != {module_name}.{name}")
        if name not in live_run.__all__:
            mismatched.append(f"{name} 不在 __all__ 里")
    assert not mismatched, f"外部名与新归属不符: {mismatched}"


def test_external_face_touches_thirteen_of_the_fourteen_siblings() -> None:
    """外部使用面覆盖 13 个兄弟；缺的那个是 live_stage_step4（只经 orchestrate 到达）。"""
    touched = set(EXTERNAL_OWNERS.values())
    assert len(touched) == 13
    assert "live_stage_step4" not in touched


def test_repo_root_anchor_survived_the_move() -> None:
    """`parents[3]` 是按 src/examcard/<pkg>/<mod>.py 数的，同级搬迁不改深度——这里当场兑现。"""
    expected = Path(examcard.__file__).resolve().parents[2]
    assert live_run._REPO_ROOT == expected, f"_REPO_ROOT 不再指向仓根: {live_run._REPO_ROOT}"
    assert (live_run._REPO_ROOT / "pyproject.toml").is_file()
    assert (live_run._REPO_ROOT / live_run.DEFAULT_PRICING_RELATIVE).is_file()


def test_every_model_finished_building_in_its_new_module() -> None:
    """字段注解跨模块后仍必须可解析：未建完的模型要在这里当场断，而不是等到首次实例化。"""
    incomplete = []
    for name in live_run.__all__:
        obj = getattr(live_run, name)
        if not inspect.isclass(obj) or not hasattr(obj, "__pydantic_complete__"):
            continue
        if not obj.__pydantic_complete__:
            incomplete.append(f"{name} ({obj.__module__})")
    assert not incomplete, f"模型未建完，字段注解在新模块里解析不了: {incomplete}"
