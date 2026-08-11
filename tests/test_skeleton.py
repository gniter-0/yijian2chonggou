"""骨架冒烟：包可安装可导入、子包齐全、隔离装置按规范生效。"""
from __future__ import annotations

import importlib
import pkgutil
import socket
from pathlib import Path

import pytest

import examcard

EXPECTED_SUBPACKAGES = {
    "materials",
    "evidence",
    "knowledge",
    "compilers",
    "gates",
    "pipeline",
    "webui",
    # 总指挥终裁锁定 10 包：providers 顶层；common=冻结底座 与 utils=非底座杂项 双包并存
    "providers",
    "common",
    "utils",
}

NESTED_PACKAGES = (
    "examcard.knowledge.contracts",
    "examcard.knowledge.continuation",
    "examcard.knowledge.diagram",
    "examcard.knowledge.planner",
    "examcard.gates.review",
    "examcard.gates.release",
)


def test_version_present() -> None:
    assert examcard.__version__ == "0.1.0"


def test_top_level_subpackages_complete_and_importable() -> None:
    found = {m.name for m in pkgutil.iter_modules(examcard.__path__)}
    missing = EXPECTED_SUBPACKAGES - found
    assert not missing, f"缺少约定子包: {sorted(missing)}"
    for name in sorted(EXPECTED_SUBPACKAGES):
        importlib.import_module(f"examcard.{name}")


def test_nested_packages_importable() -> None:
    for dotted in NESTED_PACKAGES:
        importlib.import_module(dotted)


def test_t4_network_guard_blocks_external_connect() -> None:
    # 203.0.113.1 为 TEST-NET-3 保留地址；守卫在真实拨号前即抛错，用例不产生网络流量。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError, match="禁网"):
            sock.connect(("203.0.113.1", 443))


def test_t3_repo_tmp_path_under_artifact_root(repo_tmp_path: Path) -> None:
    assert repo_tmp_path.parent.name == ".test-tmp"
    probe = repo_tmp_path / "probe.txt"
    probe.write_text("ok", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "ok"
