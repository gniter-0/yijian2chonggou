"""Runtime import layering guard for the pipeline/live_* family (blueprint section 8-3).

cm26 把 live_run.py 拆成 14 个同级 live_* 兄弟 + 原名门面（层序见
docs/prework/live_run_split_blueprint.md 第 3 节）。兄弟之间只许引用层序在自己上方的模块；
门面反向引用全部 14 个，故门面不参与层序比较，单独断言它一个不漏。

本守卫与 test_knowledge_llm_layering.py 同口径，把两个逃逸口一起堵上：
1. 新增 live_*.py 却不进层序 —— 全覆盖断言当场失败；
2. 某个子模块被改成子包 live_x/ —— 目录与文件一起清点，改包同样失败。第 2 条的教训来自
   contracts 家族：只用非递归 glob("*.py") 清点，模块变目录后守卫会静默失去管辖，
   而不是响亮失败。

pipeline/ 下另有 run.py / shadow.py / shadow_wiring.py 三个非本家族模块：清点按 ``live_``
前缀取，因此它们天然在管辖外，而将来任何新的 live_*.py 都会被第 1 条抓住。
"""
from __future__ import annotations

import ast
from pathlib import Path

import examcard.pipeline as pipeline

LAYER_ORDER = (
    # 层 0：常量底座与时间/run_id 助手，不引用任何兄弟
    "live_support",
    # 层 0：另两个自足件——单价表与预算护栏、exams/ 目录契约解析
    "live_budget",
    "live_paths",
    # 层 1：.env 双通道取值、重发身份、预检矩阵、报告模型
    "live_env",
    "live_resend",
    "live_preflight",
    "live_reports",
    # 层 1：三个 stage 执行器（step3/step4 只用到层 0 的预算与路径）
    "live_stage_step3",
    "live_stage_step4",
    "live_checkpoint",
    # 层 2：运行配置与状态机、step2 样本执行、预算预演与报告物化
    "live_config",
    "live_stage_samples",
    "live_rehearsal",
    # 层 3：总编排（引用其余全部）
    "live_orchestrate",
)

FACADE_NAME = "live_run"
PACKAGE_DIR = Path(pipeline.__file__).resolve().parent


def _live_members() -> set[str]:
    """清点 live 家族成员：``live_*.py`` 文件与同名子包目录一并计入（门面自己也在其中）。"""
    members: set[str] = set()
    for path in PACKAGE_DIR.iterdir():
        if not path.name.startswith("live_"):
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
    """运行期引用到的 live_* 兄弟；相对与绝对两种写法都算边。"""
    tree = ast.parse((PACKAGE_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
    guarded = _guarded_node_ids(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 1 and node.module.startswith("live_"):
                found.append(node.module.split(".")[0])
            elif node.level == 0 and node.module.startswith("examcard.pipeline.live_"):
                found.append(node.module.split(".")[2])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("examcard.pipeline.live_"):
                    found.append(alias.name.split(".")[2])
    return found


def test_layer_order_covers_every_live_member() -> None:
    members = _live_members()
    expected = set(LAYER_ORDER) | {FACADE_NAME}
    assert members == expected, f"live_* 家族与层序不一致: {members ^ expected}"


def test_layer_order_has_fourteen_distinct_siblings() -> None:
    assert len(LAYER_ORDER) == 14
    assert len(set(LAYER_ORDER)) == 14
    assert FACADE_NAME not in LAYER_ORDER


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


def test_no_sibling_imports_the_facade() -> None:
    """兄弟不许回头 import 门面：那会造出 live_run -> 兄弟 -> live_run 的环。"""
    offenders = []
    for module_name in LAYER_ORDER:
        source = (PACKAGE_DIR / f"{module_name}.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                target = node.module.split(".")[-1]
                if target == FACADE_NAME:
                    offenders.append(module_name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith(f".{FACADE_NAME}"):
                        offenders.append(module_name)
    assert not offenders, f"兄弟回头引用了门面: {sorted(set(offenders))}"


def test_facade_pulls_from_every_layer() -> None:
    """门面必须把 14 个兄弟都取到；漏一个就意味着有实现掉出了导入面。"""
    assert set(_sibling_imports(FACADE_NAME)) == set(LAYER_ORDER)
