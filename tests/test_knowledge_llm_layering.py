"""Runtime import layering guard for the knowledge/llm_* family (blueprint section 8-3).

cm25 把 llm.py 拆成 7 个同级 llm_* 兄弟 + 原名门面（层序见
docs/prework/llm_split_blueprint.md 第 3 节）。兄弟之间只许引用层序在自己上方的模块；
门面反向引用全部 7 个，故门面不参与层序比较，单独断言它一个不漏。

本守卫与 test_knowledge_fact_engine_layering.py 同口径，把两个逃逸口一起堵上：
1. 新增 llm_*.py 却不进层序 —— 全覆盖断言当场失败；
2. 某个子模块被改成子包 llm_x/ —— 目录与文件一起清点，改包同样失败。第 2 条的教训来自
   contracts 家族：只用非递归 glob("*.py") 清点，模块变目录后守卫会静默失去管辖，
   而不是响亮失败。
"""
from __future__ import annotations

import ast
from pathlib import Path

import examcard.knowledge as knowledge

LAYER_ORDER = (
    # 层 0：脱敏落纸口与通用小工具，不引用任何兄弟
    "llm_support",
    # 层 1：journal 事件、评审载荷、被拒响应取证、会真联网的 transport
    "llm_journal",
    "llm_review",
    "llm_forensics",
    "llm_transport",
    # 层 2：Step 2A/2B 执行记录（引用评审与取证载荷）
    "llm_records",
    # 层 3：审计优先执行器（引用其余全部）
    "llm_adapter",
)

FACADE_NAME = "llm"
PACKAGE_DIR = Path(knowledge.__file__).resolve().parent


def _llm_members() -> set[str]:
    """清点 llm 家族成员：``llm.py`` / ``llm_*.py`` 文件与同名子包目录一并计入。"""
    members: set[str] = set()
    for path in PACKAGE_DIR.iterdir():
        is_member = path.stem == FACADE_NAME or path.name.startswith("llm_")
        if not is_member:
            continue
        if path.is_file() and path.suffix == ".py":
            members.add(path.stem)
        elif path.is_dir() and (path / "__init__.py").is_file():
            members.add(path.name)
    return members


def _guarded_node_ids(tree: ast.Module) -> set[int]:
    """`if TYPE_CHECKING:` 块内的全部节点 id：注解位引用不算运行期边。"""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_type_checking:
            for child in ast.walk(node):
                guarded.add(id(child))
    return guarded


def _sibling_imports(module_name: str) -> list[str]:
    """运行期引用到的 llm_* 兄弟；相对与绝对两种写法都算边。"""
    tree = ast.parse((PACKAGE_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
    guarded = _guarded_node_ids(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 1 and node.module.startswith("llm_"):
                found.append(node.module.split(".")[0])
            elif node.level == 0 and node.module.startswith("examcard.knowledge.llm_"):
                found.append(node.module.split(".")[2])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("examcard.knowledge.llm_"):
                    found.append(alias.name.split(".")[2])
    return found


def test_layer_order_covers_every_llm_member() -> None:
    members = _llm_members()
    expected = set(LAYER_ORDER) | {FACADE_NAME}
    assert members == expected, f"llm_* 家族与层序不一致: {members ^ expected}"


def test_every_layer_entry_is_a_module_file_not_a_package() -> None:
    """层序里的每一项都必须仍是单文件模块，否则 AST 扫描的管辖范围会悄悄缩小。"""
    not_files = [name for name in LAYER_ORDER if not (PACKAGE_DIR / f"{name}.py").is_file()]
    assert not not_files, f"层序条目已不是单文件模块: {not_files}"


def test_runtime_imports_have_no_back_edges() -> None:
    rank = {name: index for index, name in enumerate(LAYER_ORDER)}
    violations = []
    for module_name in LAYER_ORDER:
        for target in _sibling_imports(module_name):
            if target not in rank:
                violations.append(f"{module_name} -> {target} (unknown sibling)")
            elif rank[target] >= rank[module_name]:
                violations.append(f"{module_name} -> {target} (back edge)")
    assert not violations, f"layering violations: {violations}"


def test_facade_pulls_from_every_layer() -> None:
    """门面必须把 7 个兄弟都取到；漏一个就意味着有实现掉出了导入面。"""
    assert set(_sibling_imports(FACADE_NAME)) == set(LAYER_ORDER)
