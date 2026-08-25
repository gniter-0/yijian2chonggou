"""扫描落盘产物里是否已经出现疑似凭据（仓内工具，cm33 从 `.test-tmp/` 迁入）。

判定用的是**项目自己**的 `_SECRET_PATTERN`（`examcard.providers.base`），所以工具与
运行期脱敏共享同一套模式：改了模式，这里立刻跟着改，不会两套规则各说各话。
只输出命中计数、行号与**脱敏后**的行，绝不打印原文匹配值。

用法：uv run python tools/leakscan.py <路径…>   # 目录按文本后缀递归
退出码：0 = 零命中；1 = 有命中；2 = 用法错误。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

from examcard.providers.base import _SECRET_PATTERN, sanitize_error_text

TEXT_SUFFIXES = {".log", ".jsonl", ".json", ".txt", ".md"}


def iter_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def main(argv: list[str]) -> int:
    targets = [Path(arg) for arg in argv]
    if not targets:
        print("usage: leakscan.py <path...>")
        return 2

    scanned = 0
    hit_files = 0
    hit_lines = 0
    for target in targets:
        for path in iter_files(target):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                print(f"[skip] {path}: {type(error).__name__}")
                continue
            scanned += 1
            hits = []
            for lineno, line in enumerate(text.splitlines(), start=1):
                matches = _SECRET_PATTERN.findall(line)
                if matches:
                    hits.append((lineno, len(matches), sanitize_error_text(line)))
            if hits:
                hit_files += 1
                hit_lines += len(hits)
                print(f"[HIT] {path} lines={len(hits)}")
                for lineno, count, masked in hits[:5]:
                    print(f"    L{lineno} matches={count} masked={masked[:160]}")
    print(f"SCANNED={scanned} HIT_FILES={hit_files} HIT_LINES={hit_lines}")
    return 1 if hit_files else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
