"""封印：仓内 .py 不许再钉某台机器的绝对路径，旧仓语料根的解析只有一处实现。

p0-2 把 tests/ 侧的机器本地路径拆成「环境变量 > 同级目录」解析，但 migration/ 下 25 个
脚本仍各自钉着同一条用户目录：换任何一台机器（含未来的 CI）跑它们，都只会以「找不到
文件」的形式失败，而错误里出现的还是使用者从未写过的那条路径。cm20 把这 25 处统一改走
examcard.utils.legacy_root，并让 tests/conftest.py 也复用同一处，不再自带第二份实现。

三条机械规则，全离线（规范 T4）：

1. src/examcard、migration、tests 全量 .py 不得出现盘符绝对路径（``C:\\Users\\…``）或
   POSIX 家目录路径（``/home/<人名>/``）——机器本地路径一旦入库就是硬编码复发。
2. 旧仓目录名只许由 legacy_root.py 以模块常量形式持有；任何文件把它当路径分量拼接
   （``… / "yijian2"``、``Path("…/yijian2")``）都算第二份解析顺序开始生长。散文里提到
   旧仓不算——注释与 docstring 不构成路径。
3. 解析器自身的三条口径：显式指定优先；指错即抛错，决不静默回落到同级猜测；
   must_exist=True 时缺语料抛出带修复指引的错，而默认求值不抛（模块级常量必须能在
   语料缺席时求值，否则离线用例会整文件报错，而不是按语料缺席跳过）。

覆盖面是诚实有限的：只认字面量。运行期用环境变量拼出来的路径拦不住；docs/ 下的历史
报告（P2/P3 prework 记录了当年语料挂在哪台机器）属史料，既不在射程内也不改。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import examcard
from examcard.utils.legacy_root import (
    LEGACY_ROOT_DIRNAME,
    LEGACY_ROOT_ENV,
    default_repo_root,
    resolve_legacy_root,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = Path(examcard.__file__).resolve().parent
SCAN_ROOTS = (SRC_ROOT, REPO_ROOT / "migration", REPO_ROOT / "tests")
RESOLVER_FILE = SRC_ROOT / "utils" / "legacy_root.py"

# 本文件自身排除在扫描之外：规则 1 的正则与规则 2 的探针必须写出被禁的形状，
# 否则封印会把自己判成违例。
_SELF = Path(__file__).resolve()
_DRIVE_ABS = re.compile(r"[A-Za-z]:[\\/]{1,2}(?:Users|home)[\\/]", re.IGNORECASE)
_POSIX_HOME = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/")


def _scanned_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        files.extend(
            path
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts and path.resolve() != _SELF
        )
    return tuple(sorted(files))


def _is_path_ctor(func: ast.expr) -> bool:
    return (isinstance(func, ast.Name) and func.id == "Path") or (
        isinstance(func, ast.Attribute) and func.attr == "Path"
    )


def _dirname_path_joins(tree: ast.Module) -> list[int]:
    """把旧仓目录名当路径分量用的行号（``/`` 拼接或 ``Path(...)`` 直构），去重后升序。"""
    lines: set[int] = set()
    for node in ast.walk(tree):
        operands: list[ast.expr] = []
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            operands = [node.left, node.right]
        elif isinstance(node, ast.Call) and _is_path_ctor(node.func):
            operands = list(node.args)
        for item in operands:
            if (
                isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and LEGACY_ROOT_DIRNAME in item.value
            ):
                lines.add(node.lineno)
    return sorted(lines)


def test_no_python_source_pins_a_machine_local_path() -> None:
    """规则 1：盘符绝对路径与家目录路径都不许入库。"""
    files = _scanned_files()
    offenders: list[str] = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _DRIVE_ABS.search(line) or _POSIX_HOME.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert len(files) >= 200, f"只扫到 {len(files)} 个 .py，锚点已失效，需重新定位"
    assert offenders == [], f"机器本地绝对路径回到仓里: {offenders}"


def test_only_the_resolver_owns_the_legacy_dirname() -> None:
    """规则 2：目录名由解析器一处持有，别处不许再拼出同一个语料根。"""
    owner = RESOLVER_FILE.read_text(encoding="utf-8")
    assert f'LEGACY_ROOT_DIRNAME = "{LEGACY_ROOT_DIRNAME}"' in owner, (
        "解析器不再以模块常量形式持有旧仓目录名，规则 2 的唯一出处已失效"
    )

    offenders: list[str] = []
    for path in _scanned_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        offenders.extend(
            f"{rel}:{lineno}"
            for lineno in _dirname_path_joins(ast.parse(path.read_text(encoding="utf-8")))
        )

    assert offenders == [], f"旧仓目录名被当路径分量另拼了一次: {offenders}"


def test_the_dirname_detector_actually_bites() -> None:
    """规则 2 的探测器不许是空转的：给它 cm20 之前的真实写法，必须命中。"""
    probe = ast.parse(
        # 第 1 行是 cm20 之前 tests/conftest.py 的写法，第 2 行是 migration 侧的写法。
        f'OLD = ROOT.parent / "{LEGACY_ROOT_DIRNAME}"\n'
        f'SCRIPTS = Path("{LEGACY_ROOT_DIRNAME}/scripts")\n'
        "SAFE = ROOT.parent / LEGACY_ROOT_DIRNAME\n"
    )

    assert _dirname_path_joins(probe) == [1, 2]


def test_the_machine_path_regexes_actually_bite() -> None:
    """规则 1 的两条正则不许是空转的：正反例各验一次。"""
    assert _DRIVE_ABS.search(r'OLD_ROOT = Path(r"C:\Users\someone\Desktop\getinfo\yijian2")')
    assert _DRIVE_ABS.search('OLD_ROOT = Path("C:/Users/someone/getinfo")')
    assert _POSIX_HOME.search('OLD_ROOT = Path("/home/someone/getinfo/yijian2")')
    assert not _DRIVE_ABS.search("REPO_ROOT = Path(__file__).resolve().parents[1]")
    assert not _POSIX_HOME.search('OUT = REPO_ROOT / "data" / "shadow_gonglu_65"')


def test_explicit_override_wins_over_the_sibling_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "elsewhere"
    corpus.mkdir()
    monkeypatch.setenv(LEGACY_ROOT_ENV, str(corpus))

    assert resolve_legacy_root(REPO_ROOT) == corpus
    assert resolve_legacy_root(REPO_ROOT, must_exist=True) == corpus


def test_override_pointing_nowhere_raises_instead_of_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指错即抛错：静默回落会把「我指错了」悄悄变成「语料不在本机」。"""
    monkeypatch.setenv(LEGACY_ROOT_ENV, str(tmp_path / "not-here"))

    with pytest.raises(RuntimeError, match=LEGACY_ROOT_ENV):
        resolve_legacy_root(REPO_ROOT)


def test_sibling_fallback_is_anchor_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(LEGACY_ROOT_ENV, raising=False)
    anchor = tmp_path / "exam-card-engine"
    anchor.mkdir()

    assert resolve_legacy_root(anchor) == tmp_path / LEGACY_ROOT_DIRNAME


def test_missing_corpus_stays_evaluable_unless_the_caller_demands_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认不抛，是为了让脚本的模块级常量在语料缺席时仍能求值；要报错得自己要。"""
    monkeypatch.delenv(LEGACY_ROOT_ENV, raising=False)
    anchor = tmp_path / "exam-card-engine"
    anchor.mkdir()

    assert not resolve_legacy_root(anchor).is_dir()

    with pytest.raises(RuntimeError, match=LEGACY_ROOT_ENV):
        resolve_legacy_root(anchor, must_exist=True)


def test_default_repo_root_counts_the_right_number_of_parents() -> None:
    """无 anchor 时的兜底锚点：legacy_root.py 上溯四级才是仓根，多一级少一级都错。"""
    assert default_repo_root() == SRC_ROOT.parents[1]