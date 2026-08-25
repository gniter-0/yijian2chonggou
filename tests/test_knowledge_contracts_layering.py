"""Runtime import layering guard for knowledge/contracts (blueprint section 8-3).

The 23 split modules form a strict DAG: a module may import only modules ordered
before it (blueprint section 3). Annotation-only references behind
``if TYPE_CHECKING:`` are exempt. The admission pair appended at the end follows
the same rule (admission_fixtures imports only admission_legacy_hash).

The V25 block (manifest v2 section 二/二-2: two current declarations plus the
five-module runtime closure lifted by RC1) is appended after the admission pair
in topological order. Facade attribute imports (``from . import X``) are not
sibling edges and are exempt by construction.

cm27 把三个逃逸口一起堵上（拆 v25_generic_contracts.py 之前的必要前置）：
1. 清点面只用非递归 ``glob("*.py")``：谁把某个模块改成子包，守卫会静默失去管辖而不是响亮失败
   （教训来自 cm26 风险清单 R2）。现在文件与带 ``__init__.py`` 的同名目录一并清点，
   层序条目另有一条断言它仍是单文件模块。
2. 只认相对写法的兄弟导入：换成 ``from examcard.knowledge.contracts.x import`` 就能绕过层序。
   现在相对与绝对两种写法都算运行期边。
3. 字符串字面量的动态导入（``__import__("<包>.x", fromlist=[...])`` /
   ``importlib.import_module("<包>.x")``）在 AST 的 Import/ImportFrom 里根本不出现。现在登记进
   ``DYNAMIC_SIBLING_IMPORTS``，同样受层序管辖，并断言被取的属性在目标模块上仍可解析。
"""
from __future__ import annotations

import ast
from importlib import import_module
from pathlib import Path

import examcard.knowledge.contracts as contracts

LAYER_ORDER = (
    "versions",
    "hashing",
    "enums",
    "numeric_tokens",
    "prompts",
    "request_identity",
    "anchors",
    "continuation",
    "projection",
    "table_inputs",
    "obligations",
    "claims",
    "proposals",
    "responses",
    "occurrences",
    "request_helpers",
    "audits",
    "step2b",
    "step2a_request",
    "step2a_builders",
    "derivation",
    "validation",
    "testing",
    "admission_legacy_hash",
    "admission_fixtures",
    "echo_release",  # P8 补链（echo-batch-release/1.0 家族）：纯数据契约，仅向后引用 admission 对

    # V25 现役声明 + RC1 运行时闭包（manifest v2 §二/§二-2），拓扑序追加：
    "v25_generic_contracts",
    "phase2r11_generic_five_golden",
    "phase2r12_v25_golden_review",
    "phase2r13_v25_reconciliation",
    "phase2r14_v25_live",
    "phase2r142_v25_projection",
    "v25_teaching_gate_layers",
)

PACKAGE_NAME = "examcard.knowledge.contracts"
PACKAGE_DIR = Path(contracts.__file__).resolve().parent

# 字面量动态导入登记表：(importer, 目标兄弟, 被取的属性)。唯一一条来自
# phase2r14_v25_live._learning_fact_gate，它另有同目标的顶层静态 import，这处 ``__import__``
# 是历史写法。登记而不是删掉：删它属于改行为，本次只把这条隐形边纳入管辖。
DYNAMIC_SIBLING_IMPORTS: tuple[tuple[str, str, str], ...] = (
    ("phase2r14_v25_live", "v25_generic_contracts", "build_v25b_source_fake_response"),
)


def family_members() -> set[str]:
    """家族成员清点：``*.py`` 文件与带 ``__init__.py`` 的同名目录一并计入（``__init__`` 自己不算）。"""
    members: set[str] = set()
    for path in PACKAGE_DIR.iterdir():
        if path.is_file() and path.suffix == ".py":
            members.add(path.stem)
        elif path.is_dir() and (path / "__init__.py").is_file():
            members.add(path.name)
    return members - {"__init__"}


def runtime_sibling_imports(module_name: str) -> list[str]:
    """Sibling modules imported at runtime (TYPE_CHECKING blocks excluded).

    相对与绝对两种写法都算边；``from . import X`` 取的是门面属性而非兄弟模块，按构造豁免。
    """
    tree = ast.parse((PACKAGE_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_type_checking:
                for child in ast.walk(node):
                    guarded.add(id(child))
    imports: list[str] = []
    prefix = f"{PACKAGE_NAME}."
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 1:
                imports.append(node.module.split(".")[0])
            elif node.level == 0 and node.module.startswith(prefix):
                imports.append(node.module[len(prefix):].split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefix):
                    imports.append(alias.name[len(prefix):].split(".")[0])
    return imports


def dynamic_sibling_imports(module_name: str) -> list[tuple[str, str, str]]:
    """扫出 ``__import__("<包>.x", fromlist=[...])`` / ``import_module("<包>.x")`` 这类字面量边。

    没有 fromlist 时属性位留空字符串。非字面量的模块名扫不出来，也不该扫——那种写法应当先落成
    字面量再登记，而不是让守卫去猜。
    """
    tree = ast.parse((PACKAGE_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
    prefix = f"{PACKAGE_NAME}."
    found: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if called not in {"__import__", "import_module"} or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        if not first.value.startswith(prefix):
            continue
        sibling = first.value[len(prefix):].split(".")[0]
        attributes = [
            element.value
            for keyword in node.keywords
            if keyword.arg == "fromlist" and isinstance(keyword.value, (ast.List, ast.Tuple))
            for element in keyword.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        found.extend((module_name, sibling, attribute) for attribute in attributes or [""])
    return found


def test_layer_order_covers_every_module_file() -> None:
    members = family_members()
    assert members == set(LAYER_ORDER), f"unaccounted contracts modules: {members ^ set(LAYER_ORDER)}"


def test_every_layer_entry_is_a_module_file_not_a_package() -> None:
    """层序里的每一项都必须仍是单文件模块，否则 AST 扫描的管辖范围会悄悄缩小。"""
    not_files = [name for name in LAYER_ORDER if not (PACKAGE_DIR / f"{name}.py").is_file()]
    assert not not_files, f"层序条目已不是单文件模块: {not_files}"


def test_runtime_imports_have_no_back_edges() -> None:
    rank = {name: index for index, name in enumerate(LAYER_ORDER)}
    violations = []
    for module_name in LAYER_ORDER:
        for target in runtime_sibling_imports(module_name):
            if target not in rank:
                violations.append(f"{module_name} -> {target} (unknown sibling)")
            elif rank[target] >= rank[module_name]:
                violations.append(f"{module_name} -> {target} (back edge)")
    assert not violations, f"layering violations: {violations}"


def test_dynamic_string_imports_match_the_registry() -> None:
    """字面量动态导入必须与登记表逐条相等：新增一处却不登记、或登记了却已删掉，都当场失败。"""
    found = sorted(item for module_name in LAYER_ORDER for item in dynamic_sibling_imports(module_name))
    expected = sorted(DYNAMIC_SIBLING_IMPORTS)
    assert found == expected, f"动态字符串导入与登记表不符: found={found} expected={expected}"


def test_dynamic_string_imports_are_layered_and_resolvable() -> None:
    """登记的动态边同样受层序管辖，且被取的属性在目标模块上确实取得到。

    后半句是给日后拆分留的桩：目标模块若被拆成门面，属性必须仍从门面取得到，否则这处
    ``__import__`` 会在运行期才炸——那时离改动已经很远了。
    """
    rank = {name: index for index, name in enumerate(LAYER_ORDER)}
    problems = []
    for importer, target, attribute in DYNAMIC_SIBLING_IMPORTS:
        if target not in rank:
            problems.append(f"{importer} -> {target} (unknown sibling)")
            continue
        if rank[target] >= rank[importer]:
            problems.append(f"{importer} -> {target} (back edge)")
        if attribute and not hasattr(import_module(f"{PACKAGE_NAME}.{target}"), attribute):
            problems.append(f"{target} 上取不到 {attribute}")
    assert not problems, f"动态字符串导入的层序或取值不成立: {problems}"
