"""providers 依赖方向守护：providers 只可 import common+utils（布局终裁 bc-888c6031）。

与 test_common_dependency_guard（common/utils 叶包纪律）互补，合起来覆盖终裁的
全部依赖方向规则：common/utils 为叶包 ← providers ← 业务包。
业务包方向（可 import 三者）无需守护——它是许可而非禁令。
"""
from __future__ import annotations

import ast
from pathlib import Path

import examcard.providers

PROVIDERS_DIR = Path(examcard.providers.__file__).resolve().parent
ALLOWED_INTERNAL = {"common", "utils", "providers"}


def _module_files() -> tuple[Path, ...]:
    return tuple(sorted(path for path in PROVIDERS_DIR.rglob("*.py") if "__pycache__" not in path.parts))


def test_providers_package_not_empty() -> None:
    assert len(_module_files()) >= 3


def test_providers_only_imports_common_and_utils_internally() -> None:
    violations: list[str] = []
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "examcard" and (len(parts) < 2 or parts[1] not in ALLOWED_INTERNAL):
                        violations.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                parts = (node.module or "").split(".")
                if parts[0] == "examcard" and (len(parts) < 2 or parts[1] not in ALLOWED_INTERNAL):
                    violations.append(f"{path.name}: from {node.module} import …")
    assert not violations, f"providers 违反依赖方向（只可 import common+utils）: {violations}"


def test_providers_relative_imports_stay_inside_package() -> None:
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        depth = len(path.relative_to(PROVIDERS_DIR).parts)  # 1 = 包根模块
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                assert node.level <= depth, (
                    f"{path.name} 相对导入越出 providers 包（level={node.level}）——"
                    "跨包引用必须用绝对 examcard.common/examcard.utils"
                )
