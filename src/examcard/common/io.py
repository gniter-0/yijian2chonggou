"""原子落盘与路径口径（写盘助手不参与任何指纹计算，见 compilers 稿 §2.1）。

批复面（compilers 稿 §2.2，总指挥 2026-08-11 直裁）：
atomic_write_json / write_json / read_json / write_yaml / resolve / relative；
规范源 r84/r810（最全）+ r16/r224 的 atomic_write_yaml + r92 的 atomic_write_bytes。
write_json / write_yaml 是历史双名（r9 write_json 与 r15/r152/r16 _atomic_write_json 等）
并入单一原子实现后的别名。签名落地即冻（bc-9d82a196）。
"""
from __future__ import annotations

import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from .hashing import jsonable

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, json.dumps(jsonable(value), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


write_json = atomic_write_json


def atomic_write_yaml(path: Path, value: Any) -> None:
    atomic_write_bytes(path, yaml.safe_dump(jsonable(value), allow_unicode=True, sort_keys=False).encode("utf-8"))


write_yaml = atomic_write_yaml


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_model(path: Path, model_type: type[_ModelT]) -> _ModelT:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True) + "\n")


def resolve(root: Path, path_value: str | Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else root / path


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve_within(root: Path, relative_path: str | Path) -> Path:
    """根内路径守卫：解析后必须仍位于 root 之下，否则 ValueError。"""
    path = (root / relative_path).resolve()
    path.relative_to(root.resolve())
    return path


def png_dimensions(path: Path) -> tuple[int, int]:
    """PNG IHDR 宽高（stdlib 解析，r71/r72/r8/r81/r57 五份重复的归一）。"""
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG file: {path}")
    return struct.unpack(">II", data[16:24])
