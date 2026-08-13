"""Runtime import layering guard for knowledge/contracts (blueprint section 8-3).

The 23 split modules form a strict DAG: a module may import only modules ordered
before it (blueprint section 3). Annotation-only references behind
``if TYPE_CHECKING:`` are exempt. The admission pair appended at the end follows
the same rule (admission_fixtures imports only admission_legacy_hash).

The V25 block (manifest v2 section 二/二-2: two current declarations plus the
five-module runtime closure lifted by RC1) is appended after the admission pair
in topological order. Facade attribute imports (``from . import X``) are not
sibling edges and are exempt by construction.
"""
from __future__ import annotations

import ast
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

PACKAGE_DIR = Path(contracts.__file__).resolve().parent


def runtime_sibling_imports(module_name: str) -> list[str]:
    """Sibling modules imported at runtime (TYPE_CHECKING blocks excluded)."""
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
    for node in ast.walk(tree):
        if id(node) in guarded or not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 1 and node.module:
            imports.append(node.module.split(".")[0])
    return imports


def test_layer_order_covers_every_module_file() -> None:
    files = {path.stem for path in PACKAGE_DIR.glob("*.py")} - {"__init__"}
    assert files == set(LAYER_ORDER), f"unaccounted contracts modules: {files ^ set(LAYER_ORDER)}"


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
