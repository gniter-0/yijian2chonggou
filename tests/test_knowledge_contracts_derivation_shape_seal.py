"""封印：derivation 的「形状表」与提案字段声明自洽，且版本相关字段一律就地 isinstance 窄化。

Step 2A 的模型提案形状由请求版本一对一固定：V13 起每个请求版本对应一个专属模型类，
V13 之前是旧基类 `Step2AModelFactProposal`。这 12 个类都直接继承 `StrictModel`、彼此
没有继承关系，所以「哪些版本带哪个字段」只能靠外部登记表表达——`derivation.py` 里那
三张模块级 `Final` 类元组加一张版本→类的映射表就是这份登记。

cm30 之前，循环体不是这么写的：它先把 isinstance 的结果收进 `is_minimal` /
`is_single_role` / `is_deterministic_numeric` 三个**布尔旗子**，再靠旗子去判定能不能访问
某个字段。运行期没问题，类型上却是灾难——旗子把 isinstance 携带的类型信息抹成了 bool，
mypy 无法把旗子与 union 成员对应起来，于是循环体里 51 条 `union-attr` 全部涌出，被一条
`# type: ignore[arg-type]`（挂在形状校验的 isinstance 上、连带把 `proposal` 退化成 Any）
一并压住。那条 ignore 就是所谓「盖子」。cm30 按正解拆掉了它：版本表提到模块级 `Final`，
形状校验改成先判 None 再 isinstance，5 个版本相关字段各自就地 isinstance 窄化。

本封印看住拆完之后不许回退的四件事：

1. **三张类元组与提案类的字段声明动态相等**：元组不是手抄的常量，而是「声明了该字段的
   union 成员集合」的另一种写法；从 `model_fields` 反查对账，登记漏一个、多一个都失败。
2. **版本表与 `versions._REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS` 咬合**（去掉不带
   专属模型类的 V12），且键里的 `vNN` 与值的类名同号——将来加 V24 忘了登记会当场点名。
3. **6 个版本相关字段的每一次 `proposal.<字段>` 访问都在 isinstance 分支里**（AST 级检查，
   `if` / 三元都算，`else` 分支属于反向窄化也算），且这 6 个字段各自至少出现一次，
   免得代码改写后封印悄悄变成空断言。布尔旗子式访问会被这条直接判死。
4. **`derivation.py` 里不许再出现任何 `# type: ignore`**。逐点 ignore 的总量由
   tests/test_mypy_waiver_ratchet.py 的 `_IGNORE_CEILING` 管辖（cm30 已把 derivation
   那一条降掉），这里再钉一遍本文件的零豁免，让「盖子」没法原地长回来。
"""
from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path

from examcard.knowledge.contracts import derivation, versions

_SOURCE = Path(derivation.__file__)
_TARGET_FUNCTION = "derive_step2a_anchor_span_selection"

# 只有部分 union 成员声明这些字段，所以每次访问都必须先窄化。
_VERSION_DEPENDENT_FIELDS = (
    "secondary_anchor_span_ids",
    "derived_proposal_key",
    "proposal_key",
    "source_section_ids",
    "completeness_mode",
    "numeric_claims",
)
_FINAL_TABLES = (
    "_MINIMAL_MODEL_PROPOSAL_TYPES",
    "_DUAL_ROLE_ANCHOR_PROPOSAL_TYPES",
    "_MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES",
    "_MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION",
)
_CONTRACT_VERSION_NUMBER = re.compile(r"^step2a_contract_v(\d+)_")


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _target_function(tree: ast.Module) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == _TARGET_FUNCTION:
            return node
    raise AssertionError("derivation.py 里找不到 %s，本封印的定位锚点失效" % _TARGET_FUNCTION)


def _union_members(tree: ast.Module) -> tuple[type, ...]:
    """从 `proposals` 形参的注解里取出那 12 个提案类（注解是字符串，故走 AST 而非 get_type_hints）。"""
    function = _target_function(tree)
    annotations = [arg.annotation for arg in function.args.kwonlyargs if arg.arg == "proposals"]
    assert annotations and annotations[0] is not None, "proposals 形参丢了注解"
    names = {
        node.id
        for node in ast.walk(annotations[0])
        if isinstance(node, ast.Name) and node.id.startswith("Step2A")
    }
    return tuple(getattr(derivation, name) for name in sorted(names))


def _declaring(field: str, members: tuple[type, ...]) -> set[type]:
    return {member for member in members if field in member.model_fields}


def _isinstance_narrows_proposal(test: ast.expr) -> bool:
    for node in ast.walk(test):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "proposal"
        ):
            return True
    return False


def _scan(node: ast.AST, narrowed: bool, offenders: list[str], seen: set[str]) -> None:
    """递归下降，沿途记住「当前位置是否处在某个 isinstance(proposal, …) 分支内」。"""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "proposal"
        and node.attr in _VERSION_DEPENDENT_FIELDS
    ):
        seen.add(node.attr)
        if not narrowed:
            offenders.append("%s（第 %d 行）" % (node.attr, node.lineno))
    if isinstance(node, ast.If):
        # `if` 的条件本身不算「已窄化」；body 与 orelse 都算（else 分支是反向窄化，
        # mypy 同样能据此排除成员）。
        _scan(node.test, narrowed, offenders, seen)
        inner = narrowed or _isinstance_narrows_proposal(node.test)
        for child in (*node.body, *node.orelse):
            _scan(child, inner, offenders, seen)
        return
    if isinstance(node, ast.IfExp):
        _scan(node.test, narrowed, offenders, seen)
        inner = narrowed or _isinstance_narrows_proposal(node.test)
        _scan(node.body, inner, offenders, seen)
        _scan(node.orelse, inner, offenders, seen)
        return
    for child in ast.iter_child_nodes(node):
        _scan(child, narrowed, offenders, seen)


def test_version_table_matches_reviewed_multipage_versions() -> None:
    """版本表既不许漏登记，也不许错配：键对账 versions 里的集合，键里的 vNN 与类名同号。"""
    table = derivation._MINIMAL_MODEL_TYPE_BY_STEP2A_REQUEST_VERSION
    assert isinstance(table, Mapping), "版本表必须是模块级 Mapping（不是每轮循环重建的字典）"
    expected_keys = set(versions._REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS) - {
        versions.REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSION
    }
    assert set(table) == expected_keys, (
        "版本表与 versions._REVIEWED_MULTIPAGE_STEP2A_REQUEST_VERSIONS 不咬合"
        "（V12 不带专属模型类，故排除在外）：漏登记=%s 多登记=%s"
        % (sorted(expected_keys - set(table)), sorted(set(table) - expected_keys))
    )
    assert len(set(table.values())) == len(table), "一个提案类只能挂一个请求版本"
    assert set(table.values()) == set(derivation._MINIMAL_MODEL_PROPOSAL_TYPES), (
        "版本表的值集合必须与 _MINIMAL_MODEL_PROPOSAL_TYPES 相等，否则形状校验会放过某个类"
    )
    mismatched = {
        version: model.__name__
        for version, model in table.items()
        if (found := _CONTRACT_VERSION_NUMBER.match(version)) is None
        or model.__name__ != "Step2AV%sModelFactProposal" % found.group(1)
    }
    assert not mismatched, "请求版本与提案类版号不一致（错配比漏登记更难发现）：%s" % mismatched


def test_minimal_tuple_matches_derived_proposal_key_declarations() -> None:
    """_MINIMAL_MODEL_PROPOSAL_TYPES ⟺ 「有 derived_proposal_key、没有 proposal_key」的那批类。"""
    members = _union_members(_tree())
    minimal = set(derivation._MINIMAL_MODEL_PROPOSAL_TYPES)
    assert set(members) == minimal | {derivation.Step2AModelFactProposal}, (
        "12 元 union 的成员集合变了：三个旗子的等价改写依赖「union 恰好穷举」，"
        "新增成员必须同时登记进三张类元组，否则语义会悄悄漂移。实际=%s"
        % sorted(member.__name__ for member in members)
    )
    by_property = {member for member in members if hasattr(member, "derived_proposal_key")}
    assert by_property == minimal, (
        "_MINIMAL_MODEL_PROPOSAL_TYPES 与「声明了 derived_proposal_key」的类集合不一致："
        "多登记=%s 漏登记=%s"
        % (
            sorted(model.__name__ for model in minimal - by_property),
            sorted(model.__name__ for model in by_property - minimal),
        )
    )
    legacy = set(members) - minimal
    for field in ("proposal_key", "source_section_ids", "completeness_mode"):
        assert _declaring(field, members) == legacy, (
            "%s 应当只由 V13 之前的旧基类声明（循环体正是据此走 else 分支）：实际=%s"
            % (field, sorted(model.__name__ for model in _declaring(field, members)))
        )


def test_dual_role_tuple_matches_secondary_anchor_declarations() -> None:
    """V15 起只剩一个穷举的 selected 角色，secondary 字段就该只在旧基类与 V13/V14 上。"""
    members = _union_members(_tree())
    declared = _declaring("secondary_anchor_span_ids", members)
    assert set(derivation._DUAL_ROLE_ANCHOR_PROPOSAL_TYPES) == declared, (
        "_DUAL_ROLE_ANCHOR_PROPOSAL_TYPES 与声明 secondary_anchor_span_ids 的类集合不一致："
        "登记表=%s 实际声明=%s"
        % (
            sorted(model.__name__ for model in derivation._DUAL_ROLE_ANCHOR_PROPOSAL_TYPES),
            sorted(model.__name__ for model in declared),
        )
    )


def test_numeric_claim_tuple_matches_numeric_claims_declarations() -> None:
    """V16 起数值声明改成本地确定性派生，模型不再声明 numeric_claims。"""
    members = _union_members(_tree())
    declared = _declaring("numeric_claims", members)
    assert set(derivation._MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES) == declared, (
        "_MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES 与声明 numeric_claims 的类集合不一致："
        "登记表=%s 实际声明=%s"
        % (
            sorted(model.__name__ for model in derivation._MODEL_NUMERIC_CLAIM_PROPOSAL_TYPES),
            sorted(model.__name__ for model in declared),
        )
    )


def test_shape_tables_stay_module_level_final() -> None:
    """四张表必须是模块级 `Final` 赋值：mypy 只对 Final 的类元组做 isinstance 窄化。

    一旦谁把它们挪进函数体、或去掉 `Final`，窄化就会失效，51 条 union-attr 会重新涌出，
    紧接着最省事的「修法」就是再糊一条 ignore ——盖子于是长回来。这条断言把入口堵住。
    """
    tree = _tree()
    annotated = {
        node.target.id: node
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    missing = [name for name in _FINAL_TABLES if name not in annotated]
    assert not missing, "这些形状表不再是模块级带注解赋值（可能被挪进函数体了）：%s" % missing
    not_final = [
        name
        for name in _FINAL_TABLES
        if "Final" not in ast.unparse(annotated[name].annotation)
    ]
    assert not not_final, "这些形状表的注解丢了 Final，mypy 会因此不再窄化：%s" % not_final


def test_version_dependent_fields_are_always_isinstance_narrowed() -> None:
    """6 个版本相关字段的每次访问都得在 isinstance 分支里——布尔旗子式访问一律判死。"""
    function = _target_function(_tree())
    offenders: list[str] = []
    seen: set[str] = set()
    _scan(function, False, offenders, seen)
    assert not offenders, (
        "%s 里出现了未经 isinstance 窄化的版本相关字段访问：%s。"
        "请就地写 `isinstance(proposal, _…_TYPES)`，不要先收进布尔旗子再判——"
        "旗子会把类型信息抹成 bool，mypy 从此对整个循环体失明（cm30 拆掉的正是这种盖子）。"
        % (_TARGET_FUNCTION, offenders)
    )
    assert seen == set(_VERSION_DEPENDENT_FIELDS), (
        "本封印的检查面与代码不同步了：这些字段在 %s 里一次都没出现，断言已变成空转=%s"
        % (_TARGET_FUNCTION, sorted(set(_VERSION_DEPENDENT_FIELDS) - seen))
    )


def test_derivation_carries_no_type_ignore() -> None:
    """derivation.py 零逐点 ignore：cm30 删掉的那条 arg-type 不许以任何形式回来。"""
    lines = [
        "%d: %s" % (number, text.strip())
        for number, text in enumerate(_SOURCE.read_text(encoding="utf-8").splitlines(), start=1)
        if "type: ignore" in text
    ]
    assert not lines, (
        "derivation.py 又出现了 type: ignore：%s。"
        "这里的类型问题已经在 cm30 按正解解决过一次，再关码等于把盖子盖回去；"
        "确有工具限制请先在提交里说清，并同步 tests/test_mypy_waiver_ratchet.py 的 _IGNORE_CEILING。"
        % lines
    )
