"""Runtime import layering guard for the knowledge/fact_* family (blueprint section 8-3).

cm21 把 fact_engine.py 拆成 12 个同级 fact_* 兄弟 + 原名门面（层序见
docs/prework/fact_engine_split_blueprint.md 第 3 节）。兄弟之间只许引用层序在自己上方的模块；
门面反向引用全部 12 个，故门面不参与层序比较，单独断言它一个不漏。

本守卫刻意把两个逃逸口一起堵上：
1. 新增 fact_*.py 却不进层序 —— 全覆盖断言当场失败；
2. 某个子模块被改成子包 fact_x/ —— 目录与文件一起清点，改包同样失败。第 2 条是从
   test_knowledge_contracts_layering.py 的教训来的：只用非递归 glob("*.py") 清点，
   模块变目录后守卫会静默失去管辖，而不是响亮失败。
"""
from __future__ import annotations

import ast
from pathlib import Path

import examcard.knowledge as knowledge

LAYER_ORDER = (
    # 层 0：词表 / 内部数据类 / 仓根锚点与小工具，均不引用兄弟
    "fact_lexicon",
    "fact_types",
    "fact_support",
    # 层 1：策略裁决、侧车强校验、评审解读、证据渲染
    "fact_policy",
    "fact_sidecars",
    "fact_review",
    "fact_render",
    # 层 2：类型/完备度判定与图示绑定
    "fact_classify",
    "fact_diagram",
    # 层 3：事实与卡片装配
    "fact_build",
    # 层 4：去重审计与章节质量门禁
    "fact_quality",
    # 层 5：确定性运行入口（step2a 的自动前置）
    "fact_runner",
)

FACADE_NAME = "fact_engine"
PACKAGE_DIR = Path(knowledge.__file__).resolve().parent


def _fact_members() -> set[str]:
    """清点 fact_* 家族成员：.py 文件与同名子包目录一并计入。"""
    members: set[str] = set()
    for path in PACKAGE_DIR.iterdir():
        if not path.name.startswith("fact_"):
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
    """运行期引用到的 fact_* 兄弟；相对与绝对两种写法都算边。"""
    tree = ast.parse((PACKAGE_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
    guarded = _guarded_node_ids(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 1 and node.module.startswith("fact_"):
                found.append(node.module.split(".")[0])
            elif node.level == 0 and node.module.startswith("examcard.knowledge.fact_"):
                found.append(node.module.split(".")[2])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("examcard.knowledge.fact_"):
                    found.append(alias.name.split(".")[2])
    return found


def test_layer_order_covers_every_fact_member() -> None:
    members = _fact_members()
    expected = set(LAYER_ORDER) | {FACADE_NAME}
    assert members == expected, f"fact_* 家族与层序不一致: {members ^ expected}"


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
    """门面必须把 12 个兄弟都取到；漏一个就意味着有实现掉出了导入面。"""
    assert set(_sibling_imports(FACADE_NAME)) == set(LAYER_ORDER)