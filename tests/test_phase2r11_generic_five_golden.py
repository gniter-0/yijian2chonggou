"""封印 phase2r11_generic_five_golden 的两半：活的契约面 vs 未移植的入口。

这个模块被 tests/test_knowledge_contracts_layering.py 列为 V25 契约闭包成员，但它内部
分成两半，混在一起很容易被误用：

- 活的一半：``V25GoldenReviewKind`` / ``V25GoldenReviewQueue`` / ``_review_queue``，
  被 ``phase2r12_v25_golden_review`` 顶层 import，是真在跑的东西。
- 死的一半：``build_phase2r11_five_golden_report``，全仓零调用点，且第一条函数内 import
  就抛 ModuleNotFoundError——``tools.verify_phase2r1_phase2r0_freeze`` 留在旧仓
  ``yijian2/scripts/tools/``，未随内核迁移。

pyproject 里给这处留了一条 import-not-found 豁免。豁免的正当性以前只写在注释里，本文件
把它变成被测事实：调用它必然抛 ModuleNotFoundError；同时守住「不许有人新接调用点」，
因为接上去只会在运行期炸，而不是在类型检查期。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from examcard.knowledge.contracts.phase2r11_generic_five_golden import (
    V25GoldenReviewKind,
    V25GoldenReviewQueue,
    _review_queue,
    build_phase2r11_five_golden_report,
)

ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINT_NAME = "build_phase2r11_five_golden_report"
MISSING_LEGACY_MODULE = "tools.verify_phase2r1_phase2r0_freeze"

# 允许出现该名字的两处：定义它的模块本身，和本封印测试。
_ALLOWED = {
    Path("src/examcard/knowledge/contracts/phase2r11_generic_five_golden.py"),
    Path("tests/test_phase2r11_generic_five_golden.py"),
}


def test_live_contract_surface_stays_importable() -> None:
    """phase2r12 依赖的三个名字必须一直在：这半边是活的，不能被当成死代码清掉。"""
    assert V25GoldenReviewKind is not None
    assert V25GoldenReviewQueue is not None
    assert callable(_review_queue)


def test_unported_entry_point_raises_module_not_found(tmp_path: Path) -> None:
    """调用未移植入口必然抛 ModuleNotFoundError，且缺的正是旧仓那个 tools 模块。"""
    with pytest.raises(ModuleNotFoundError) as excinfo:
        build_phase2r11_five_golden_report(
            repository_root=tmp_path, generated_at_utc="2026-01-01T00:00:00Z"
        )
    assert excinfo.value.name is not None
    assert excinfo.value.name.split(".")[0] == "tools", (
        f"预期缺失 {MISSING_LEGACY_MODULE}，实际缺失 {excinfo.value.name}——"
        "若旧仓那个模块已被移植进来，请同时删掉 pyproject 里的 import-not-found 豁免"
    )


def test_unported_entry_point_has_no_call_sites() -> None:
    """守住零调用点：接上去只会在运行期炸，类型检查期看不出来（那条豁免正是为此存在）。"""
    offenders: list[str] = []
    for folder in ("src", "tests", "migration"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            relative = path.relative_to(ROOT)
            if Path(*relative.parts) in _ALLOWED:
                continue
            if ENTRY_POINT_NAME in path.read_text(encoding="utf-8"):
                offenders.append(relative.as_posix())
    assert not offenders, (
        f"{ENTRY_POINT_NAME} 在本仓无法运行，却出现在 {offenders}。"
        f"要用它得先把 {MISSING_LEGACY_MODULE} 和旧仓语料一起移植过来。"
    )