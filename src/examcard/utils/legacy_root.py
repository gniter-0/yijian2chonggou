"""旧仓（yijian2）金标语料根的机器无关定位（utils 杂项，叶包，不 import 任何 examcard 内部包）。

为什么需要一个模块来干这件事：语料体量 >120MB，按 engineering_skeleton.md 第 3 节不入
git，只能挂在本机。但把它写成某台机器的用户目录字面量，代价是 migration 下的实跑/对照
工具在任何别的机器（含未来的 CI）上都只能以「找不到文件」的形式失败，而错误里出现的还
是使用者从未写过的那条路径。p0-2 已在 tests/conftest.py 拆掉同一处债；本模块把同一套
解析顺序收成单一出处，供 migration 脚本与测试共用，避免两份副本各自漂移。

解析顺序：EXAMCARD_LEGACY_ROOT 显式指定 > 本仓同级目录 ../yijian2。

两条硬规矩：
1. 显式指定却不存在 = 配置错误，立即抛错，决不静默回落到同级猜测——那会把「我指错了」
   悄悄变成「语料不在本机」。
2. 未指定且同级不存在时，默认仍照常返回候选路径而不抛错（must_exist=False）。模块级
   常量必须能在语料缺席时求值：tests 里有若干离线用例按文件路径装载 migration 模块
   （relay_preflight、run_pilot_gonglu5_step4 等），import 期抛错会让整个测试文件报错
   而不是跳过。确实非语料不可的脚本自己传 must_exist=True 换取一条可执行的报错。
"""
from __future__ import annotations

import os
from pathlib import Path

LEGACY_ROOT_ENV = "EXAMCARD_LEGACY_ROOT"
LEGACY_ROOT_DIRNAME = "yijian2"


def default_repo_root() -> Path:
    """本模块所在仓库的根目录（src/examcard/utils/legacy_root.py 上溯四级）。

    只在调用方确实拿不到自己的锚点时用。migration 脚本与 conftest 都应显式传 anchor：
    包若被非 editable 装进 site-packages，这里的上溯就会落到无关目录。
    """
    return Path(__file__).resolve().parents[3]


def _override_root() -> Path | None:
    raw = (os.environ.get(LEGACY_ROOT_ENV) or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise RuntimeError(
            f"{LEGACY_ROOT_ENV}={raw!r} 指向的目录不存在。"
            "显式指定即视为承诺语料就位，不做静默回落。"
        )
    return path


def resolve_legacy_root(anchor: Path | None = None, *, must_exist: bool = False) -> Path:
    """返回旧仓语料根。anchor 为与 yijian2 平级的本仓根目录（migration 脚本传自己算出的仓根）。"""
    override = _override_root()
    if override is not None:
        return override
    base = default_repo_root() if anchor is None else Path(anchor).resolve()
    root = base.parent / LEGACY_ROOT_DIRNAME
    if must_exist and not root.is_dir():
        raise RuntimeError(
            f"旧仓金标语料根不可用：{root} 不存在。"
            f"请把 {LEGACY_ROOT_ENV} 指向 yijian2 仓，或把该仓放在本仓同级目录。"
        )
    return root