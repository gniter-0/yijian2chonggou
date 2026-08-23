"""封印：mypy 逐模块豁免清单只许缩短，不许加宽。

pyproject.toml 的 ``[[tool.mypy.overrides]]`` 头注释写着棘轮规则「只许缩短，不许加宽」，
但注释拦不住任何人。这个测试把该规则变成可执行闸门，锁住三件事：

1. 豁免清单与下方 _WAIVER_CEILING 逐条相等。新增模块、给某模块新增错误码，都会失败；
   退掉一条豁免同样会失败，直到作者在同一次提交里把 ceiling 一起降下来——「缩短」应当
   是显式动作，不该悄悄发生。
2. gates/ 与 compilers/ 永不出现在豁免清单里。这两个包是严格档，是判定逻辑所在，
   一旦允许豁免，最该被类型检查的地方就成了盲区。
3. 每条豁免只用 ``disable_error_code`` 精确关码，绝不用 ``ignore_errors`` 整模块放行。

清单为何还剩这些：七条全部是工具/上游限制或已实测的「盖子」，不是偷懒——
- call-arg（step2a_builders / admission_legacy_hash）：未启用 pydantic 插件，
  ``model_construct`` 在 mypy 眼里要求列全字段；
- call-overload（knowledge.llm）：openai SDK 的 create() 重载被运行期 stream 布尔打败；
- prop-decorator（knowledge.quality / knowledge.schema）：mypy 不支持 @property 之上再叠装饰器；
- arg-type（contracts.derivation）：见该文件 isinstance 处的长注释，按正解修掉会掀开约
  50 条 union-attr 真问题，需按请求版本逐个收窄才能收口；
- import-not-found（phase2r11_generic_five_golden）：``tools.`` 模块未随内核迁移过来。

mypy 自身的 warn_unused_configs 负责反向那一半：某条豁免变成死条目时它会报 unused section。
两者合起来，清单既不会悄悄变长，也不会留下过期条目。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

# 模块名 -> 该模块当前允许关闭的错误码集合。降低它，永远不要抬高它。
_WAIVER_CEILING: dict[str, frozenset[str]] = {
    "examcard.knowledge.contracts.derivation": frozenset({"arg-type"}),
    "examcard.knowledge.contracts.admission_legacy_hash": frozenset({"call-arg"}),
    "examcard.knowledge.contracts.step2a_builders": frozenset({"call-arg"}),
    "examcard.knowledge.contracts.phase2r11_generic_five_golden": frozenset({"import-not-found"}),
    "examcard.knowledge.llm": frozenset({"call-overload"}),
    "examcard.knowledge.quality": frozenset({"prop-decorator"}),
    "examcard.knowledge.schema": frozenset({"prop-decorator"}),
}

_STRICT_TIER_FORBIDDEN = ("examcard.gates", "examcard.compilers")


def _overrides() -> list[dict]:
    config = tomllib.loads(PYPROJECT.read_bytes().decode("utf-8"))
    return list(config["tool"]["mypy"]["overrides"])


def _waivers() -> list[dict]:
    return [entry for entry in _overrides() if "disable_error_code" in entry]


def _modules(entry: dict) -> list[str]:
    module = entry["module"]
    return [module] if isinstance(module, str) else list(module)


def test_waiver_inventory_matches_the_pinned_ceiling() -> None:
    """清单与 ceiling 逐条相等：加宽直接失败，缩短必须同一提交里改 ceiling。"""
    actual: dict[str, frozenset[str]] = {}
    for entry in _waivers():
        for module in _modules(entry):
            assert module not in actual, f"{module} 出现在多条豁免里，逐模块口径被拆散了"
            actual[module] = frozenset(entry["disable_error_code"])

    added = sorted(set(actual) - set(_WAIVER_CEILING))
    removed = sorted(set(_WAIVER_CEILING) - set(actual))
    assert not added, f"新增豁免模块 {added}：棘轮只许缩短，请改代码而不是加豁免"
    assert not removed, f"豁免模块 {removed} 已退掉，请同步删掉本文件 _WAIVER_CEILING 里的对应条目"

    for module, ceiling in _WAIVER_CEILING.items():
        widened = sorted(actual[module] - ceiling)
        narrowed = sorted(ceiling - actual[module])
        assert not widened, f"{module} 新增关码 {widened}：棘轮只许缩短"
        assert not narrowed, f"{module} 已少关 {narrowed}，请同步收紧本文件的 _WAIVER_CEILING"


def test_strict_tier_packages_never_appear_in_any_waiver() -> None:
    """gates/ 与 compilers/ 是判定逻辑所在，永不允许出现在豁免清单里。"""
    for entry in _waivers():
        for module in _modules(entry):
            for forbidden in _STRICT_TIER_FORBIDDEN:
                assert not module.startswith(forbidden), f"{module} 属于严格档，不得豁免"


def test_waivers_disable_named_codes_and_never_ignore_whole_modules() -> None:
    """每条豁免只精确关码；ignore_errors 会让整模块失去检查，一律禁止。"""
    for entry in _overrides():
        assert "ignore_errors" not in entry, f"{entry['module']} 使用了 ignore_errors，请改成 disable_error_code"
    for entry in _waivers():
        codes = entry["disable_error_code"]
        assert isinstance(codes, list) and codes, f"{entry['module']} 的 disable_error_code 必须是非空列表"