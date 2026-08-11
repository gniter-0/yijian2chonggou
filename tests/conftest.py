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
