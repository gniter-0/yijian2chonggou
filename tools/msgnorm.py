"""把提交信息文件规成「无 BOM + 纯 CRLF」并打印体检结果（仓内工具，cm33 从 `.test-tmp/` 迁入）。

为什么需要它：本仓 `.gitattributes` 是 `* -text`，提交信息文件也一样按字节原样进 git。
消息文件若混着 LF 或带上 BOM，提交历史里就会出现看不见的杂质。写完消息先过这一遍，
再 `git commit -F`。

用法：uv run python tools/msgnorm.py <消息文件>
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: msgnorm.py <path>")
        return 2
    path = Path(argv[0])
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    body = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")
    if body != path.read_bytes():
        path.write_bytes(body)
    crlf = body.count(b"\r\n")
    lone_lf = body.count(b"\n") - crlf
    subject = body.split(b"\r\n", 1)[0].decode("utf-8")
    print(f"bytes={len(body)} lines={crlf} lone_lf={lone_lf} had_bom={had_bom}")
    print(f"subject_bytes={len(subject.encode('utf-8'))} subject={subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
