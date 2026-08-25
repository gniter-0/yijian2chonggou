"""语料闸门记账封印（cm32）：让「摘要报的条数」与「真被挡掉的条数」不许再分叉。

cm32 实测到的病灶：语料缺席时全仓被挡掉 32 条，而 conftest 的摘要只报 21 条。差的 11 条来自
`tests/test_gates_review_replay.py` 里 3 处**裸** `pytest.mark.skipif(not X.is_dir(), …)`——绕过
`requires_legacy_corpus`，于是两件事同时失效：

1. 不进 `_CORPUS_GATED` 记账，摘要少报，「本轮有多少保障没提供」这句话就成了假数字；
2. 不受 `EXAMCARD_REQUIRE_LEGACY_CORPUS=1` 管辖。严格模式的全部意义是「语料缺席就当场失败，
   不许静默跳过」，可它看不见裸 skipif——CI 上开了严格模式，这 3 组仍会静默蒸发。

所以本封印钉的不是风格，而是「语料闸门只有一个入口」这条不变式：凡以旧仓语料路径为条件的跳过，
必须经 `requires_legacy_corpus`。每条断言都自带防空转（扫描面为空、清单为空、扫不到 skipif 一律
当场失败），否则测试文件改名或目录搬迁会让它悄悄变成一句空话。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests import conftest as tests_conftest

TESTS_DIR = Path(__file__).resolve().parent

# 语料闸门的完整清单：文件 → `requires_legacy_corpus(...)` 调用处数。
# 只许经审阅变更——新增一处语料依赖就要在这里露头，不许悄悄长出来。
GATE_CENSUS: dict[str, int] = {
    "test_compilers_step3_response_v2.py": 1,
    "test_gates_review_authorization_assembly.py": 1,
    "test_gates_review_authorization_replay.py": 1,
    "test_gates_review_extraction.py": 1,
    "test_gates_review_replay.py": 4,
}

# 派生名的源头：这两个名字取到的都是旧仓语料下的路径。
_LEGACY_SOURCES = {"legacy_path", "LEGACY_ROOT"}


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py") if p.is_file())


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_bytes().decode("utf-8"), filename=str(path))


def _legacy_derived_names(tree: ast.Module) -> set[str]:
    """模块内取值自 legacy_path(...) / LEGACY_ROOT 的名字（含一层间接赋值）。"""
    derived = set(_LEGACY_SOURCES)
    # 赋值可能前后颠倒（先用后赋不合法，但多层间接需要迭代到不动点）
    for _ in range(4):
        grown = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            used = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            if used & derived and target.id not in derived:
                derived.add(target.id)
                grown = True
        if not grown:
            break
    return derived


def _skipif_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "skipif":
                calls.append(node)
    return calls


def _gate_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "requires_legacy_corpus"
    ]


def test_scan_surface_is_not_empty() -> None:
    """防空转第一层：扫描面必须真的扫到东西（目录搬迁 / 改名会让下面几条变空话）。"""
    modules = _test_modules()
    assert len(modules) >= 80, f"tests/ 下只扫到 {len(modules)} 个 test_*.py，扫描面可疑"
    total_skipif = sum(len(_skipif_calls(_parse(p))) for p in modules)
    assert total_skipif >= 15, f"全 tests/ 只扫到 {total_skipif} 处 skipif，扫描器可疑"


def test_no_bare_skipif_on_legacy_corpus_paths() -> None:
    """语料闸门只有一个入口：以旧仓语料路径为条件的跳过必须走 requires_legacy_corpus。"""
    offenders: list[str] = []
    checked_names = 0
    for path in _test_modules():
        tree = _parse(path)
        derived = _legacy_derived_names(tree)
        if derived == _LEGACY_SOURCES:
            continue
        checked_names += len(derived - _LEGACY_SOURCES)
        for call in _skipif_calls(tree):
            if not call.args:
                continue
            hit = sorted(
                {n.id for n in ast.walk(call.args[0]) if isinstance(n, ast.Name)} & derived
            )
            if hit:
                offenders.append(f"{path.name}:{call.lineno} 条件里出现 {', '.join(hit)}")
    assert checked_names >= 4, f"只认出 {checked_names} 个语料派生名，防空转不通过"
    assert not offenders, (
        "裸 skipif 绕过了记账与严格模式（改走 requires_legacy_corpus）：\n  "
        + "\n  ".join(offenders)
    )


def test_gate_census_is_pinned() -> None:
    """闸门清单钉死：处数与分布只许经审阅变更。"""
    actual = {
        path.name: len(_gate_calls(_parse(path)))
        for path in _test_modules()
        if _gate_calls(_parse(path))
    }
    assert actual == GATE_CENSUS, f"语料闸门分布变了：实测 {actual}"
    assert sum(GATE_CENSUS.values()) == 8


def test_skip_reason_shares_the_counting_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """摘要按 _SKIP_REASON_PREFIX 数条数，所以生成 reason 的那一处必须用同一个常量。"""
    # 这条只看跳过路径，与严格模式无关：显式清掉环境变量，否则 CI（EXAMCARD_REQUIRE_LEGACY_CORPUS=1）
    # 上会走抛错分支，测试就成了「随运行模式变脸」的那种。
    monkeypatch.delenv(tests_conftest.REQUIRE_LEGACY_CORPUS_ENV, raising=False)
    saved = list(tests_conftest._CORPUS_GATED)
    try:
        mark = tests_conftest.requires_legacy_corpus("__cm32_absent_subtree__")
    finally:
        tests_conftest._CORPUS_GATED[:] = saved
    assert mark.args == (True,)
    assert mark.kwargs["reason"].startswith(tests_conftest._SKIP_REASON_PREFIX)


def test_strict_mode_governs_every_accounted_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """EXAMCARD_REQUIRE_LEGACY_CORPUS=1 的全部意义：缺席当场失败，不许静默跳过。"""
    monkeypatch.setenv(tests_conftest.REQUIRE_LEGACY_CORPUS_ENV, "1")
    saved = list(tests_conftest._CORPUS_GATED)
    try:
        with pytest.raises(RuntimeError, match="旧仓金标语料不可用"):
            tests_conftest.requires_legacy_corpus("__cm32_absent_subtree__")
        # 记账清单要在**还原之前**取样，否则这条断言恒真（等于没写）
        after = list(tests_conftest._CORPUS_GATED)
    finally:
        tests_conftest._CORPUS_GATED[:] = saved
    assert after == saved, "抛错路径不该往记账清单里塞东西"


def test_summary_counts_are_computed_not_hardcoded() -> None:
    """摘要里的条数必须现算：写死的数字迟早变成假话（cm32 订正的正是这种数字）。"""
    tree = _parse(Path(tests_conftest.__file__))
    summary = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "pytest_terminal_summary"
    )
    literals = [
        node.value
        for node in ast.walk(summary)
        if isinstance(node, ast.Constant) and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ]
    assert literals == [1], f"摘要里出现了写死的整数 {literals}——条数必须按跳过报告现算"
    assert any(
        isinstance(node, ast.Attribute) and node.attr == "stats" for node in ast.walk(summary)
    ), "摘要没有读 terminalreporter.stats，说明它不是在现算"



