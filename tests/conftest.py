"""examcard 测试全局隔离装置。条款编号（T1–T8）见 docs/engineering_skeleton.md 第 4 节。"""
from __future__ import annotations

import os
import shutil
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from examcard.utils.legacy_root import LEGACY_ROOT_ENV, resolve_legacy_root

ROOT = Path(__file__).resolve().parents[1]
# T3：必须仓库相对的测试产物统一收口到单一稳定父目录。
# 在根目录随意建删顶层路径会与 pytest 的文件系统扫描竞态（Windows 上曾致收集期 stat 已删目录）。
ARTIFACT_ROOT = ROOT / ".test-tmp"

# T7：matplotlib 无头渲染，避免 GUI 后端在 CI/无显示环境崩溃。
os.environ.setdefault("MPLBACKEND", "Agg")

# ---------------------------------------------------------------------------
# 旧仓（yijian2）金标语料的机器无关定位。
#
# 语料体量 >120MB，按 engineering_skeleton.md 第 3 节不入 git，只能挂在本机。
# 但路径决不允许硬编码到某台机器的用户目录：那会让依赖它的回放模块在任何其他
# 机器（含未来的 CI）上静默消失——skipif 让整批回放用例无声蒸发，却不报一个错。
# 但别把「模块整体消失」当成事实：cm32 实测 8 处闸门散在 5 个模块里，只挡掉 32 条，
# 那 5 个模块共 92 条，其余 60 条不依赖语料、照常跑并照常保障。摘要里的条数一律
# 按跳过报告现算，不写常数——写死的数字迟早变成一句假话（cm31 的 fitz/cv2 存根豁免
# 就是这样过期的）。
#
# 8 处里有 3 处是 cm32 收归进来的：`test_gates_review_replay.py` 原先用裸
# `pytest.mark.skipif(not X.is_dir(), …)` 挡 r810/r104/r11101 三条链，绕过本函数的代价有两条——
# 不进 _CORPUS_GATED（摘要少报 11 条，实测报 21 而真值 32），也不受 REQUIRE 严格模式管辖
# （CI 上开了严格模式，这 3 组仍会静默蒸发）。所以「语料闸门只有一个入口」这条不变式由
# tests/test_corpus_gate_accounting_seal.py 看住：裸 skipif 一出现就当场失败。
#
# 解析顺序（EXAMCARD_LEGACY_ROOT 显式指定 > 本仓同级 ../yijian2）与「指错即抛错、
# 决不静默回落」的口径都由 examcard.utils.legacy_root 一处提供：cm20 起 migration
# 下 25 个脚本共用同一处，两份副本各自漂移的余地被消掉。这里只叠加测试特有的一层：
# 未指定且语料不在本机 = 允许跳过，但在会话摘要里响亮报数；
# EXAMCARD_REQUIRE_LEGACY_CORPUS=1 时升级为收集期失败（CI 用）。
# ---------------------------------------------------------------------------
REQUIRE_LEGACY_CORPUS_ENV = "EXAMCARD_REQUIRE_LEGACY_CORPUS"
_TRUTHY = {"1", "true", "yes", "on"}

# 跳过原因的前缀：requires_legacy_corpus 写它，pytest_terminal_summary 按它数条数。
# 两处共用一个常量，改文案不会让计数悄悄归零。
_SKIP_REASON_PREFIX = "旧仓金标语料不在本机"

# 被语料缺席挡掉的闸门清单，供 pytest_terminal_summary 响亮报数。一处闸门可能只覆盖
# 一个类或一个函数（本仓 5 处里只有 1 处是模块级 pytestmark），所以这份清单的长度是
# 「闸门处数」而不是「用例数」。
_CORPUS_GATED: list[str] = []

LEGACY_ROOT = resolve_legacy_root(ROOT)
LEGACY_CORPUS_PRESENT = LEGACY_ROOT.is_dir()


def legacy_path(relative: str) -> Path:
    """取旧仓语料下的相对路径（relative 用 POSIX 分隔符书写）。"""
    return LEGACY_ROOT / relative


def requires_legacy_corpus(*relatives: str) -> pytest.MarkDecorator:
    """语料齐备则不跳；缺席则跳过并记账；严格模式下直接抛错阻止静默跳过。"""
    missing = tuple(item for item in relatives if not legacy_path(item).is_dir())
    if not missing:
        return pytest.mark.skipif(False, reason="旧仓金标语料就位")

    detail = f"{LEGACY_ROOT} 下缺少 {', '.join(missing)}"
    if (os.environ.get(REQUIRE_LEGACY_CORPUS_ENV) or "").strip().lower() in _TRUTHY:
        raise RuntimeError(
            f"{REQUIRE_LEGACY_CORPUS_ENV} 已开启，但旧仓金标语料不可用（{detail}）。"
            f"请用 {LEGACY_ROOT_ENV} 指向 yijian2 仓，或关闭严格模式。"
        )
    _CORPUS_GATED.append(detail)
    return pytest.mark.skipif(True, reason=f"{_SKIP_REASON_PREFIX}（{detail}）")


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """语料缺席不许悄悄过去：在摘要里报出闸门处数与**现算**的被挡用例数。"""
    if not _CORPUS_GATED:
        return
    gated = sum(
        1
        for report in terminalreporter.stats.get("skipped", [])
        if _SKIP_REASON_PREFIX in str(getattr(report, "longrepr", ""))
    )
    terminalreporter.write_sep("=", "旧仓金标语料缺席", yellow=True)
    terminalreporter.write_line(
        f"{len(_CORPUS_GATED)} 处语料闸门被触发，挡掉 {gated} 条用例——这 {gated} 条本轮"
        f"未提供任何保障。闸门可能只覆盖一个类或一个函数，同模块内不依赖语料的用例照常"
        f"跑，所以这不是「模块整体消失」。设 {LEGACY_ROOT_ENV} 指向 yijian2 仓可启用，"
        f"或设 {REQUIRE_LEGACY_CORPUS_ENV}=1 让缺席直接失败。"
    )

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture(autouse=True)
def _no_network_by_default(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """T4：未标 @pytest.mark.live 的用例发起非回环外联时立即失败（拦截在拨号之前）。"""
    if request.node.get_closest_marker("live") is not None:
        return

    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, (bytes, bytearray)):
            host = host.decode(errors="replace")
        if isinstance(host, str) and (host in _LOOPBACK_HOSTS or host.startswith("127.")):
            return real_connect(self, address, *args, **kwargs)
        if self.family == getattr(socket, "AF_UNIX", object()):
            return real_connect(self, address, *args, **kwargs)
        raise RuntimeError(
            f"测试默认禁网（规范 T4）：拦截到对 {address!r} 的外联。"
            "确需真实外呼的用例请标 @pytest.mark.live 并通过 `uv run pytest -m live` 显式运行。"
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def repo_tmp_path() -> Iterator[Path]:
    """T3：需要仓库相对路径契约的产物，用本 fixture 取 .test-tmp/ 下的独立子目录。"""
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="artifacts_", dir=ARTIFACT_ROOT))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
