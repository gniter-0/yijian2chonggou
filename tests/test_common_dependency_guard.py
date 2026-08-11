"""common 依赖方向守护：叶包纪律 + freeze 的无 glob/无 git 章程 + 模块集锁定。

章程来源：布局终裁 bc-888c6031、扩容终裁 bc-9d82a196、engineering_skeleton.md §3。
common 只许 import 标准库、pydantic、yaml 与 common 自身（相对导入）；
不得 import 任何 examcard 内部包；freeze.py 不得引用 subprocess/git/glob 机制。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import examcard.common

COMMON_DIR = Path(examcard.common.__file__).resolve().parent
EXPECTED_MODULES = {"__init__.py", "hashing.py", "gates.py", "io.py", "freeze.py", "junit.py", "schema.py"}
ALLOWED_EXTERNAL = {"pydantic", "yaml"}


def _module_files() -> tuple[Path, ...]:
    return tuple(sorted(COMMON_DIR.glob("*.py")))


def _imports(path: Path) -> tuple[tuple[str, int], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level))
    return tuple(found)


def test_module_set_is_locked_to_the_six_frozen_pieces() -> None:
    assert {item.name for item in _module_files()} == EXPECTED_MODULES


def test_common_never_imports_internal_packages() -> None:
    for path in _module_files():
        for name, level in _imports(path):
            if level > 0:
                assert level == 1, f"{path.name} 相对导入越出 common: level={level}"
                target = name.split(".")[0] if name else ""
                assert not target or f"{target}.py" in EXPECTED_MODULES, f"{path.name} 相对导入未知模块 {name}"
                continue
            top = name.split(".")[0]
            assert top != "examcard", f"{path.name} 违反叶包纪律 import 了内部包 {name}"
            assert top in ALLOWED_EXTERNAL or top in sys.stdlib_module_names, (
                f"{path.name} 引入未获批准的第三方依赖 {name}"
            )


def test_freeze_has_no_git_subprocess_or_glob_machinery() -> None:
    source = (COMMON_DIR / "freeze.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert not imported & {"subprocess", "git", "glob", "fnmatch"}
    call_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not call_names & {"glob", "rglob"}, "freeze 不得内置路径 glob 机制"
    assert "phase2" not in source, "freeze 不得携带任何 phase 路径集"


def test_common_never_touches_subprocess_or_network_modules() -> None:
    forbidden = {"subprocess", "socket", "httpx", "requests", "urllib"}
    for path in _module_files():
        for name, level in _imports(path):
            if level == 0:
                assert name.split(".")[0] not in forbidden, f"{path.name} 引入了 {name}"
