"""examcard 测试全局隔离装置。条款编号（T1–T8）见 docs/engineering_skeleton.md 第 4 节。"""
from __future__ import annotations

import os
import shutil
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

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
# 机器（含未来的 CI）上整体静默消失——模块级 skipif 让 92 个用例无声蒸发，
# 却不报一个错。
#
# 解析顺序：EXAMCARD_LEGACY_ROOT 显式指定 > 仓库同级 ../yijian2。
# 显式指定却不存在 = 配置错误，立即抛错；未指定且不存在 = 允许跳过，但会在
# 会话摘要里响亮报数。EXAMCARD_REQUIRE_LEGACY_CORPUS=1 时升级为收集期失败（CI 用）。
# ---------------------------------------------------------------------------
LEGACY_ROOT_ENV = "EXAMCARD_LEGACY_ROOT"
REQUIRE_LEGACY_CORPUS_ENV = "EXAMCARD_REQUIRE_LEGACY_CORPUS"
_TRUTHY = {"1", "true", "yes", "on"}

# 被语料缺席挡掉的模块清单，供 pytest_terminal_summary 响亮报数。
_CORPUS_GATED: list[str] = []


def _resolve_legacy_root() -> Path:
    override = (os.environ.get(LEGACY_ROOT_ENV) or "").strip()
    if not override:
        return ROOT.parent / "yijian2"
    path = Path(override).expanduser()
    if not path.is_dir():
        raise RuntimeError(
            f"{LEGACY_ROOT_ENV}={override!r} 指向的目录不存在。"
            "显式指定即视为承诺语料就位，不做静默回落。"
        )
    return path


LEGACY_ROOT = _resolve_legacy_root()
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
    return pytest.mark.skipif(True, reason=f"旧仓金标语料不在本机（{detail}）")


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """语料缺席不许悄悄过去：在摘要里点名有多少回放模块被挡掉。"""
    if not _CORPUS_GATED:
        return
    terminalreporter.write_sep("=", "旧仓金标语料缺席", yellow=True)
    terminalreporter.write_line(
        f"{len(_CORPUS_GATED)} 个回放模块因语料不在本机被整体跳过；"
        f"这些用例本轮未提供任何保障。设 {LEGACY_ROOT_ENV} 指向 yijian2 仓可启用，"
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
