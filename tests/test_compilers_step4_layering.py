"""Runtime import layering guard for the compilers/step4_contract_* family（蓝图 §8-3）。

cm34 把 step4_contract.py 拆成 6 个同级 step4_contract_* 兄弟 + 原名门面（层序见
docs/prework/step4_contract_split_blueprint.md 第 3 节）。兄弟之间只许引用层序在自己上方的模块；
门面反向引用全部 6 个，故门面不参与层序比较，单独断言它一个不漏。

族前缀取唯一的 `step4_contract_`（非 `step4_`）：`compilers/` 下另有与本次无关的
step4_echo_bridge.py / step4_image.py 共用 `step4_` 前缀，用 `step4_` 会把它们误纳、逃逸口失守。

本守卫与 test_knowledge_llm_layering.py 同口径，把两个逃逸口一起堵上：
1. 新增 step4_contract_*.py 却不进层序 —— 全覆盖断言当场失败；
2. 某个子模块被改成子包 step4_contract_x/ —— 目录与文件一起清点，改包同样失败。
"""
from __future__ import annotations

import ast
from pathlib import Path

import examcard.compilers as compilers

LAYER_ORDER = (
    # 层 0：版本串/类型标签/判定常量、r4 法定哈希三件套，均不引用任何兄弟
    "step4_contract_constants",
    "step4_contract_hashing",
    # 层 1：通用图卡契约模型（引用常量与哈希）
    "step4_contract_models_generic",
    # 层 2：Phase2R4 发布工件模型（经 GenericCardContract 触及通用层）
    "step4_contract_models_phase",
    # 层 3：fixture → 契约的确定性派生链（引用其上全部模型）
    "step4_contract_derive",
    # 层 4：离线闸门与回归矩阵（引用常量、模型、派生）
    "step4_contract_gate",
)

FACADE_NAME = "step4_contract"
FAMILY_PREFIX = "step4_contract_"
PACKAGE_DIR = Path(compilers.__file__).resolve().parent


def _family_members() -> set[str]:
    """清点 step4_contract 家族成员：门面文件、step4_contract_*.py 文件与同名子包目录一并计入。

    只认唯一前缀 step4_contract_，故 step4_echo_bridge / step4_image 这类共享 step4_ 的无关模块不纳入。
    """
    members: set[str] = set()
    for path in PACKAGE_DIR.iterdir():
        is_member = path.stem == FACADE_NAME or path.name.startswith(FAMILY_PREFIX)
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
    """运行期引用到的 step4_contract_* 兄弟；相对与绝对两种写法都算边。"""
    tree = ast.parse((PACKAGE_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
    guarded = _guarded_node_ids(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 1 and node.module.startswith(FAMILY_PREFIX):
                found.append(node.module.split(".")[0])
            elif node.level == 0 and node.module.startswith(f"examcard.compilers.{FAMILY_PREFIX}"):
                found.append(node.module.split(".")[2])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"examcard.compilers.{FAMILY_PREFIX}"):
                    found.append(alias.name.split(".")[2])
    return found


def test_layer_order_covers_every_family_member() -> None:
    members = _family_members()
    expected = set(LAYER_ORDER) | {FACADE_NAME}
    assert members == expected, f"step4_contract_* 家族与层序不一致: {members ^ expected}"


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
    """门面必须把 6 个兄弟都取到；漏一个就意味着有实现掉出了导入面。"""
    assert set(_sibling_imports(FACADE_NAME)) == set(LAYER_ORDER)

