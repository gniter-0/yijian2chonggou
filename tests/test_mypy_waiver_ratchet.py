"""封印：mypy 的关码只许缩短，不许加宽（cm29 起棘轮降到「逐点 ignore」这一档）。

pyproject.toml 曾经带一份「逐模块 × 错误码」的豁免清单，把 263 条既有噪声精确挂账。
清单在 cm29 清零：admission_legacy_hash 那 2 条 call-arg 是真错（`default_factory()`
对 pydantic 的单参/零参 union 少传参），按正解改用 `FieldInfo.get_default(...)`；剩下
16 条工具侧限制改成**行尾逐点 `# type: ignore[码]`**，盲区从整模块缩到出错那一行。

于是本封印看住的不再是 pyproject 里的清单，而是四件事：

1. **零模块级豁免**：任何 ``disable_error_code`` 都不许再出现，``ignore_errors`` 一律禁止。
   想关码只能逐点写在出错行上——那里必须挨着一句为什么。
2. **逐点 ignore 清单与 _IGNORE_CEILING 逐条相等**：新增一条、给某文件多关一个码、
   多关一次，都会失败；删掉一条同样会失败，直到作者在同一次提交里把 ceiling 一起降下来
   ——「缩短」应当是显式动作，不该悄悄发生。
3. **全局 warn_unused_ignores 保持开启**：逐点 ignore 一旦因上游修好而失效，mypy 会当场
   报 unused-ignore 把它揪出来。这是「只许缩短」的反向那一半，替代了原先靠
   warn_unused_configs 发现死豁免条目的机制。
4. **gates/ 与 compilers/ 永不出现在任何关码条目里**（这两个包是判定逻辑所在，走严格档）。

清单为何还剩这 19 条：全是工具/上游限制，不是偷懒——
- ``prop-decorator``（knowledge/schema、knowledge/quality）：mypy 不支持 @property 之上
  再叠装饰器（python/mypy#1362），而 ``@computed_field`` + ``@property`` 是 pydantic 官方写法；
- ``call-arg``（contracts/step2a_builders 一行 10 条）：pydantic 的 mypy 插件给
  ``model_construct`` 合成了「必须列全字段」的签名，真实契约是部分赋值 + 跳过校验；
- ``import-not-found``（contracts/phase2r11_generic_five_golden）：``tools.`` 未随内核迁移，
  「调用即 ModuleNotFoundError」由 tests/test_phase2r11_generic_five_golden.py 封住；
- ``assignment``（contracts/admission_fixtures_typed 5 条）：Literal 字段默认值覆盖，同址有说明；
- 其余 ``return-value`` / ``misc`` / ``arg-type`` 各处：旧仓口径的窄化返回与协议缝，同址有说明。

cm30 拆掉了唯一一条「盖子」型条目 ``arg-type``（contracts/derivation）：那条 ignore 连带把
proposal 退化成 Any，压住了循环体里 51 条 union-attr。实测那 51 条只落在 5 个属性访问点上，
根因是三个布尔旗子把 isinstance 的类型信息抹成了 bool；改成模块级类元组 + 就地 isinstance
窄化后 mypy 零报错，ignore 随之删除，ceiling 从 20 条 / 11 个文件降到 19 条 / 10 个文件。
形状表的自洽由 tests/test_knowledge_contracts_derivation_shape_seal.py 单独看住。
"""
from __future__ import annotations

import re
import tokenize
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
SRC = ROOT / "src" / "examcard"

_IGNORE_PATTERN = re.compile(r"^#\s*type:\s*ignore\[([^\]]+)\]")
_BARE_PATTERN = re.compile(r"^#\s*type:\s*ignore(\s|$|#)")

_STRICT_TIER_FORBIDDEN = ("examcard.gates", "examcard.compilers")

# 逐点 ignore 的上限：{相对 src/examcard 的路径: {错误码: 条数}}。
# 键是「文件 × 错误码」，值是这一对出现的次数（一条 `# type: ignore[a, b]` 记 a、b 各一次）。
# 注意 step2a_builders 那一条压住的是同一行上的 10 个 call-arg 报错——本清单钉的是
# 「关了几处」，不是「压住几条报错」，因为前者才是作者能主动加宽的那个量。
_IGNORE_CEILING: dict[str, dict[str, int]] = {
    "common/junit.py": {"return-value": 1},
    "gates/review/extraction.py": {"misc": 1, "return-value": 1},
    "gates/review/step4_text_gate.py": {"arg-type": 1},
    "knowledge/contracts/admission_fixtures_typed.py": {"assignment": 5},
    "knowledge/contracts/admission_v2.py": {"assignment": 1},
    "knowledge/contracts/phase2r11_generic_five_golden.py": {"import-not-found": 1},
    "knowledge/contracts/step2a_builders.py": {"call-arg": 1},
    "knowledge/quality.py": {"prop-decorator": 2},
    "knowledge/schema.py": {"prop-decorator": 2},
    "pipeline/live_orchestrate.py": {"arg-type": 3},
    "providers/envelope_profiles.py": {"return-value": 1},
}


def _load_config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _mypy_tables(config: dict) -> tuple[dict, list[dict]]:
    mypy = config["tool"]["mypy"]
    overrides = mypy.get("overrides", [])
    return mypy, list(overrides)


def _scan_pinpoint_ignores() -> tuple[dict[str, Counter[str]], list[str]]:
    """盘点 src/examcard 下的行尾逐点 ignore。

    口径与 mypy 一致：只有「同一物理行上、注释之前有代码」的 `# type: ignore[...]`
    才真正关码；整行注释（比如本仓多处散文里提到 `# type: ignore[assignment]`）不算。
    """
    inventory: dict[str, Counter[str]] = {}
    bare: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        with tokenize.open(path) as handle:
            lines = handle.readlines()
        with tokenize.open(path) as handle:
            for token in tokenize.generate_tokens(handle.readline):
                if token.type != tokenize.COMMENT:
                    continue
                row, col = token.start
                if not lines[row - 1][:col].strip():
                    continue
                rel = path.relative_to(SRC).as_posix()
                found = _IGNORE_PATTERN.match(token.string)
                if found:
                    for code in (item.strip() for item in found.group(1).split(",")):
                        inventory.setdefault(rel, Counter())[code] += 1
                elif _BARE_PATTERN.match(token.string):
                    bare.append(f"{rel}:{row}")
    return inventory, bare


def test_no_module_level_waiver_survives() -> None:
    """模块级豁免已在 cm29 清零，从此不许回来。

    `disable_error_code` 会让某个模块内该错误码的**全部**报错失明，加一条的成本近乎零、
    发现的成本却很高（要么全项目跑对账，要么等出事）。要关码就逐点关在出错行上，
    那一行旁边必须写清为什么——这条断言就是把那个便宜的口子焊死。
    """
    config = _load_config()
    mypy, overrides = _mypy_tables(config)
    assert "disable_error_code" not in mypy, "全局段不许出现 disable_error_code"
    offenders = {
        override.get("module"): sorted(override["disable_error_code"])
        for override in overrides
        if override.get("disable_error_code")
    }
    assert not offenders, (
        "pyproject 里又出现了模块级豁免：%s。"
        "cm29 已把清单清零，关码请改成出错行上的 `# type: ignore[码]` 并同步 _IGNORE_CEILING。"
        % offenders
    )
    banned = {
        override.get("module")
        for override in overrides
        if override.get("ignore_errors")
    }
    assert not banned, "ignore_errors 一律禁止（它比 disable_error_code 更宽）：%s" % banned


def test_global_warn_unused_ignores_is_enabled() -> None:
    """棘轮反向的那一半：失效的逐点 ignore 必须当场被 mypy 点名。

    逐点 ignore 成了本仓唯一的关码手段，若这个开关关掉，上游修好后的过期 ignore
    就会永久留在代码里——清单看着没加宽，实际盲区却在悄悄发霉。
    """
    config = _load_config()
    mypy, overrides = _mypy_tables(config)
    assert mypy.get("warn_unused_ignores") is True, (
        "[tool.mypy] 必须保持 warn_unused_ignores = true"
    )
    disabled = [
        override.get("module")
        for override in overrides
        if override.get("warn_unused_ignores") is False
    ]
    assert not disabled, "不许在 override 里把 warn_unused_ignores 关掉：%s" % disabled


def test_pinpoint_ignores_match_ceiling_exactly() -> None:
    """逐点 ignore 清单与 _IGNORE_CEILING 逐条相等：新增即失败，缩短也要显式改。"""
    inventory, _ = _scan_pinpoint_ignores()
    actual = {rel: dict(codes) for rel, codes in inventory.items()}
    added = {
        rel: {
            code: count
            for code, count in codes.items()
            if count > _IGNORE_CEILING.get(rel, {}).get(code, 0)
        }
        for rel, codes in actual.items()
    }
    added = {rel: codes for rel, codes in added.items() if codes}
    assert not added, (
        "新增或加宽了逐点 ignore：%s。"
        "先按正解修类型；确实是工具限制才在出错行旁写明原因并同步本封印的 _IGNORE_CEILING。"
        % added
    )
    assert actual == _IGNORE_CEILING, (
        "逐点 ignore 清单与 ceiling 不一致（少了就把 ceiling 一起降下来，"
        "「缩短」应当是显式动作）：实际=%s 期望=%s" % (actual, _IGNORE_CEILING)
    )


def test_no_bare_type_ignore_in_src() -> None:
    """裸 `# type: ignore` 关掉的是该行所有错误码，等于局部的 ignore_errors。"""
    _, bare = _scan_pinpoint_ignores()
    assert not bare, "发现不带错误码的裸 ignore，请补上具体错误码：%s" % bare


def _modules(override: dict) -> list[str]:
    module = override.get("module")
    if isinstance(module, str):
        return [module]
    return list(module or [])


def test_strict_tier_never_appears_in_any_waiver() -> None:
    """gates/ 与 compilers/ 是判定逻辑所在，永远走严格档，且永不出现在关码条目里。"""
    config = _load_config()
    _, overrides = _mypy_tables(config)
    for override in overrides:
        for name in _modules(override):
            if not name.startswith(_STRICT_TIER_FORBIDDEN):
                continue
            for key in ("disable_error_code", "ignore_errors"):
                assert key not in override, (
                    "严格档模块 %s 不许带 %s：这两个包一旦失明，闸门就形同虚设" % (name, key)
                )
    covered = {
        name
        for override in overrides
        if override.get("disallow_untyped_defs")
        for name in _modules(override)
    }
    missing = [
        prefix for prefix in _STRICT_TIER_FORBIDDEN
        if not any(name.startswith(prefix) for name in covered)
    ]
    assert not missing, "严格档 override 丢了这些包：%s" % missing


def test_pinpoint_ignores_stay_out_of_the_strict_tier_judgement_code() -> None:
    """严格档里的逐点 ignore 也钉在 ceiling 里，且只允许出现在已记账的那几处。

    gates/review 下现存 3 处（extraction 的 misc/return-value、step4_text_gate 的 arg-type）
    是旧仓口径的窄化返回与协议缝，同址有说明；compilers/ 一处都不许有。
    """
    inventory, _ = _scan_pinpoint_ignores()
    in_compilers = sorted(rel for rel in inventory if rel.startswith("compilers/"))
    assert not in_compilers, "compilers/ 里不许出现任何逐点 ignore：%s" % in_compilers
    in_gates = {rel: dict(codes) for rel, codes in inventory.items()
                if rel.startswith("gates/")}
    expected = {rel: codes for rel, codes in _IGNORE_CEILING.items()
                if rel.startswith("gates/")}
    assert in_gates == expected, (
        "gates/ 下的逐点 ignore 变了（这是判定逻辑，加一处都要在提交里说清）："
        "实际=%s 期望=%s" % (in_gates, expected)
    )
