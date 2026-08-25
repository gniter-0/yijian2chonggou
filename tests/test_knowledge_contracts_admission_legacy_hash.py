"""封印：admission 家族两条旧口径哈希的「填默认值」环节（cm29 唯一一处偏离逐字切片）。

``admission_legacy_hash`` 的函数体是旧仓字节级切片，唯一的例外是 ``hash_model_r8x`` /
``hash_model_r102`` 里那行补默认值的语句：原文是
``field.default_factory() if field.default_factory else field.default``——零参调用。
pydantic v2 把 ``default_factory`` 标注成
``Callable[[], Any] | Callable[[dict[str, Any]], Any] | None``，也就是允许**单参**工厂
（``Field(default_factory=lambda data: ...)``，参数是已校验字段）。旧写法遇到单参工厂会在
运行时 ``TypeError``，mypy 报的那 2 条 ``call-arg`` 是真错、不是插件假阳性。cm29 改成
pydantic 自己的取值口 ``FieldInfo.get_default(call_default_factory=True, validated_data=...)``。

本封印钉四件事：

1. **等值**：对普通默认值与零参工厂，新写法与旧写法取到的值相等且同型——所以 canonical
   载荷不变、旧口径哈希不变（cm29 曾对全仓 500 个模型 / 1428 个默认值字段逐个比过，
   这里保留可复跑的最小样本）。
2. **修的是真问题**：单参工厂上，旧写法确实抛 ``TypeError``，新写法给出正确值。
3. **哈希口径未漂**：两条血脉在同一份载荷上的摘要钉成字面量；r8x 把 ``schema_version``
   计入自哈希载荷，r10x 血脉把它排除——这个差异正是两条血脉必须并存的原因。
4. **源码不许回退**：模块里不许再出现零参 ``default_factory()`` 调用。
"""
from __future__ import annotations

import re
import tokenize
from pathlib import Path
from typing import Any

import pytest
from pydantic import Field

from examcard.knowledge.contracts import admission_legacy_hash as legacy
from examcard.materials.schema import StrictModel

MODULE = Path(legacy.__file__)


class _Plain(StrictModel):
    """普通默认值 + 零参工厂：两种写法必须完全一致。"""

    name: str
    schema_version: str = "2.0"
    note: str = "-"
    tags: tuple[str, ...] = Field(default_factory=tuple)
    digest: str = ""


class _Contextual(StrictModel):
    """单参工厂：旧写法在这里运行时炸，新写法按 pydantic 语义拿到值。"""

    name: str
    mirror: str = Field(default_factory=lambda data: str(data["name"]).upper())
    schema_version: str = "2.0"
    digest: str = ""


def _legacy_expression(field: Any) -> Any:
    """cm29 之前那行的原样复刻，仅用于等值对照。"""
    return field.default_factory() if field.default_factory else field.default


@pytest.mark.parametrize("field_name", ["schema_version", "note", "tags", "digest"])
def test_get_default_matches_the_old_zero_arg_expression(field_name: str) -> None:
    """普通默认值与零参工厂：新旧两种取值写法相等且同型（哈希因此不变）。"""
    field = _Plain.model_fields[field_name]
    new = field.get_default(call_default_factory=True, validated_data={})
    old = _legacy_expression(field)
    assert new == old
    assert type(new) is type(old)


def test_one_argument_factory_breaks_the_old_expression() -> None:
    """单参工厂：旧写法抛 TypeError，这就是那 2 条 call-arg 报的真问题。"""
    field = _Contextual.model_fields["mirror"]
    with pytest.raises(TypeError):
        _legacy_expression(field)
    filled = field.get_default(call_default_factory=True, validated_data={"name": "gonglu"})
    assert filled == "GONGLU"


def test_builders_fill_defaults_including_a_contextual_factory() -> None:
    """两条血脉的 builder 都能补上单参工厂字段，且不覆盖载荷里已给的值。"""
    for build in (legacy.hash_model_r8x, legacy.hash_model_r102):
        model = build(_Contextual, {"name": "gonglu"}, "digest")
        assert model.mirror == "GONGLU"
        assert model.schema_version == "2.0"
        given = build(_Contextual, {"name": "gonglu", "mirror": "kept"}, "digest")
        assert given.mirror == "kept"


def _code_without_strings() -> str:
    """取模块的「可执行部分」：剔掉字符串字面量与注释。

    必须剔掉——本模块的 docstring 与本文件的说明里都原样引用了旧写法
    ``field.default_factory()``，若直接扫全文，散文会把回退闸误触。
    """
    chunks: list[str] = []
    with tokenize.open(MODULE) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            chunks.append(token.string)
    return " ".join(chunks)


def test_zero_arg_default_factory_call_never_returns_to_the_module() -> None:
    """源码级回退闸：不许再出现零参 `default_factory()` 调用。"""
    code = _code_without_strings()
    assert not re.search(r"default_factory\s*\(\s*\)", code), (
        "又出现了零参 default_factory() 调用：单参工厂会在运行时 TypeError，"
        "请改用 FieldInfo.get_default(call_default_factory=True, validated_data=...)"
    )
    calls = re.findall(r"get_default\s*\(\s*call_default_factory\s*=\s*True", code)
    assert len(calls) == 2, (
        "hash_model_r8x / hash_model_r102 各有一处补默认值，必须都走 get_default，"
        "实测 %d 处" % len(calls)
    )


# 同一份载荷在两条血脉下的摘要，钉成字面量：口径一旦漂移（排除集合、分隔符、
# 排序、编码任一处），这两个值立刻变。数值由 cm29 在 pydantic 2.13.4 上实测得到。
_R8X_DIGEST = "229116be29f68de62efb8e32e912a231a0e049dfc59001e075bf36cd5de90c7c"
_R10X_DIGEST = "8b9494aeb3bc099bb188ad8c6718d533a3f6df17200306746b351e8ba65cde78"


def test_pinned_digests_for_both_lineages() -> None:
    payload = {"name": "gonglu"}
    assert legacy.hash_model_r8x(_Plain, dict(payload), "digest").digest == _R8X_DIGEST
    assert legacy.hash_model_r102(_Plain, dict(payload), "digest").digest == _R10X_DIGEST
    # r10x 的第三个 builder 走 model_construct + model_dump，落点与 r102 一致。
    assert legacy.hash_model_r10x(_Plain, dict(payload), "digest").digest == _R10X_DIGEST


def test_lineages_differ_exactly_on_schema_version() -> None:
    """r8x 把 schema_version 计入自哈希载荷，r10x 血脉排除它——两条血脉不可互换。"""
    base = {"name": "gonglu"}
    bumped = {"name": "gonglu", "schema_version": "2.1"}
    assert (legacy.hash_model_r8x(_Plain, dict(bumped), "digest").digest
            != legacy.hash_model_r8x(_Plain, dict(base), "digest").digest)
    assert (legacy.hash_model_r102(_Plain, dict(bumped), "digest").digest
            == legacy.hash_model_r102(_Plain, dict(base), "digest").digest)
