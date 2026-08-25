"""EOL/BOM 平权审计（仓内工具，cm33 从 `.test-tmp/` 迁入）。

本仓 `.gitattributes` 把所有路径标成 `-text`（字节权威）：golden/fixture/canonical
哈希锚定**工作区字节**，所以任何编辑都不许改变换行风格或引入 BOM。这份纪律原先只
靠一个 gitignore 掉的脚本 + 人记得手跑，`.test-tmp/` 一删就没牙了；cm33 起工具入仓，
且「全树跟踪文件零 BOM、每个文件行尾自洽」由 `tests/test_repo_byte_discipline_seal.py`
在 CI 里机器强制。本文件是那条封印的**唯一** profile 实现来源，别在封印里另写一份。

用法：
  python tools/eolaudit.py                 # 审计相对 HEAD 变更的文件 + 未跟踪文件（改完就跑这个）
  python tools/eolaudit.py --all-tracked   # 审计全树跟踪文件（与封印同一扫描面）
  python tools/eolaudit.py <path>...       # 只审计指定文件
退出码：0 = 无违规；1 = 有违规。
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

BOM = b"\xef\xbb\xbf"
# 兜底用：cm33 实测全树 485 个跟踪文件里一个二进制都没有（含 NUL 者为零），
# 但资产随时可能进来，所以按后缀 + NUL 双重跳过。
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf", ".zip", ".ico", ".woff", ".woff2"}


class Profile(NamedTuple):
    """一个文件的行尾体检结果。"""

    has_bom: bool
    crlf: int
    lone_lf: int

    @property
    def is_mixed(self) -> bool:
        """CRLF 与孤立 LF 同时出现——本仓视为违规（除封印里逐条钉住的既存豁免）。"""
        return bool(self.crlf and self.lone_lf)


def profile(data: bytes) -> Profile:
    """按字节算 (是否带 BOM, CRLF 行数, 孤立 LF 行数)。"""
    crlf = data.count(b"\r\n")
    return Profile(data.startswith(BOM), crlf, data.count(b"\n") - crlf)


def looks_binary(path: Path, data: bytes) -> bool:
    return path.suffix.lower() in BINARY_SUFFIXES or b"\x00" in data


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return result.stdout


def tracked_files(repo_root: Path | None = None) -> list[str]:
    """全树跟踪文件（`-z` 取名，避免 git 给非 ASCII 路径加引号转义）。"""
    args = ["-C", str(repo_root)] if repo_root is not None else []
    raw = subprocess.run(
        ["git", *args, "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return sorted(chunk.decode("utf-8") for chunk in raw.split(b"\0") if chunk)


def changed_or_untracked() -> list[str]:
    names: set[str] = set()
    for flag in ((), ("--untracked-files=all",)):
        for line in _git("status", "--porcelain", *flag).splitlines():
            entry = line[3:].strip().strip('"')
            if not entry or entry.endswith("/"):
                continue
            if " -> " in entry:
                entry = entry.split(" -> ", 1)[1]
            names.add(entry)
    return sorted(names)


def _head_bytes(path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"], capture_output=True, check=False
    )
    return None if result.returncode != 0 else result.stdout


def head_blobs(names: Sequence[str]) -> dict[str, bytes]:
    """一次子进程取回多个路径在 HEAD 里的字节（几百个文件逐个 `git show` 太慢）。

    封印按 **HEAD 字节**判定，不看工作区：一是「提交进去的字节是否干净」才是那条不变式，
    二是全量跑测试会把 7 个受版本管理的黄金件按 LF 重写，看工作区会让封印随用例执行顺序
    变脸。工作区那一档由本文件的默认模式（对比 HEAD）负责。
    """
    if not names:
        return {}
    request = "".join(f"HEAD:{name}\n" for name in names).encode("utf-8")
    out = subprocess.run(
        ["git", "cat-file", "--batch"], input=request, capture_output=True, check=True
    ).stdout
    blobs: dict[str, bytes] = {}
    pos = 0
    for name in names:
        end = out.index(b"\n", pos)
        header = out[pos:end].decode("utf-8", errors="replace").split(" ")
        pos = end + 1
        if header[-1] in {"missing", "ambiguous"}:
            continue
        size = int(header[2])
        blobs[name] = out[pos : pos + size]
        pos += size + 1  # 内容后面跟一个 \n
    return blobs



def _targets(argv: list[str]) -> list[str]:
    if argv == ["--all-tracked"]:
        return tracked_files()
    return argv or changed_or_untracked()


def main(argv: list[str]) -> int:
    violations: list[str] = []
    checked = 0
    for name in _targets(argv):
        path = Path(name)
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        data = path.read_bytes()
        if b"\x00" in data:
            continue
        checked += 1
        now = profile(data)
        head = _head_bytes(name)
        if head is None:
            status = "untracked"
            if now.has_bom:
                violations.append(f"{name}: 未跟踪文件带 BOM")
            if now.is_mixed:
                violations.append(
                    f"{name}: 未跟踪文件混用 CRLF({now.crlf}) 与孤立 LF({now.lone_lf})"
                )
        else:
            status = "tracked"
            was = profile(head)
            if now.has_bom != was.has_bom:
                violations.append(
                    f"{name}: BOM 与 HEAD 不一致 (HEAD={was.has_bom} 工作区={now.has_bom})"
                )
            if was.crlf and not was.lone_lf and now.lone_lf:
                violations.append(f"{name}: HEAD 是纯 CRLF，工作区引入了 {now.lone_lf} 处孤立 LF")
            if was.lone_lf and not was.crlf and now.crlf:
                violations.append(f"{name}: HEAD 是纯 LF，工作区引入了 {now.crlf} 处 CRLF")
        print(f"[{status}] {name}: bom={now.has_bom} crlf={now.crlf} lone_lf={now.lone_lf}")
    print(f"CHECKED={checked} VIOLATIONS={len(violations)}")
    for item in violations:
        print(f"VIOLATION {item}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
