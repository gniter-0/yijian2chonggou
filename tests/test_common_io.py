"""common/io：原子落盘、读回与路径口径（全部写盘收口 tmp_path，T3）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
import yaml

from examcard.common.io import (
    append_jsonl,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_yaml,
    load_model,
    png_dimensions,
    read_json,
    relative,
    resolve,
    resolve_within,
    write_json,
    write_yaml,
)
from examcard.common.schema import StrictModel


class Record(StrictModel):
    schema_version: Literal["record_v1"] = "record_v1"
    title: str


def test_atomic_write_json_pretty_sorted_with_trailing_newline(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "value.json"
    atomic_write_json(target, {"b": 1, "a": "值"})
    text = target.read_text(encoding="utf-8")
    assert text == '{\n  "a": "值",\n  "b": 1\n}\n'


def test_atomic_write_json_serializes_models(tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    atomic_write_json(target, Record(title="t"))
    assert read_json(target) == {"schema_version": "record_v1", "title": "t"}


def test_write_json_is_the_same_frozen_implementation() -> None:
    assert write_json is atomic_write_json
    assert write_yaml is atomic_write_yaml


def test_atomic_write_bytes_overwrites_without_leftover_temp(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    atomic_write_bytes(target, b"first")
    atomic_write_bytes(target, b"second")
    assert target.read_bytes() == b"second"
    assert [item.name for item in tmp_path.iterdir()] == ["blob.bin"]


def test_atomic_write_yaml_roundtrip_preserves_unicode(tmp_path: Path) -> None:
    target = tmp_path / "value.yaml"
    atomic_write_yaml(target, {"标题": "值", "items": [1, 2]})
    assert "标题" in target.read_text(encoding="utf-8")
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == {"标题": "值", "items": [1, 2]}


def test_load_model_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    atomic_write_json(target, Record(title="t"))
    assert load_model(target, Record) == Record(title="t")


def test_append_jsonl_appends_one_canonical_line_per_record(tmp_path: Path) -> None:
    target = tmp_path / "journal.jsonl"
    append_jsonl(target, {"sequence": 1, "event": "开始"})
    append_jsonl(target, Record(title="t"))
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"event": "开始", "sequence": 1}
    assert json.loads(lines[1])["schema_version"] == "record_v1"


def test_resolve_keeps_absolute_and_joins_relative(tmp_path: Path) -> None:
    assert resolve(tmp_path, tmp_path / "abs.txt") == tmp_path / "abs.txt"
    assert resolve(tmp_path, "rel/file.txt") == tmp_path / "rel" / "file.txt"


def test_relative_returns_posix_inside_and_absolute_outside(tmp_path: Path) -> None:
    inside = tmp_path / "a" / "b.txt"
    assert relative(tmp_path, inside) == "a/b.txt"
    outside = tmp_path.parent / "elsewhere.txt"
    assert relative(tmp_path, outside) == outside.resolve().as_posix()


def test_resolve_within_allows_inside_and_blocks_escape(tmp_path: Path) -> None:
    assert resolve_within(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()
    with pytest.raises(ValueError):
        resolve_within(tmp_path, "../escape.txt")


def test_png_dimensions_reads_ihdr_width_height(tmp_path: Path) -> None:
    target = tmp_path / "page.png"
    header = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
    target.write_bytes(header)
    assert png_dimensions(target) == (640, 480)


def test_png_dimensions_rejects_non_png(tmp_path: Path) -> None:
    target = tmp_path / "fake.png"
    target.write_bytes(b"JFIF not a png padding padding")
    with pytest.raises(ValueError):
        png_dimensions(target)
    short = tmp_path / "short.png"
    short.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError):
        png_dimensions(short)
