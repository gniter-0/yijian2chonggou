"""common/hashing：canonical 形态、文件哈希与 hash_model 自证封印。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from examcard.common.hashing import (
    SHA256_HEX,
    canonical_hash,
    canonical_json,
    file_hash,
    hash_model,
    jsonable,
    sha256_bytes,
    sha256_text,
)
from examcard.common.schema import StrictModel


class Sealed(StrictModel):
    schema_version: Literal["sealed_v1"] = "sealed_v1"
    name: str
    payload_sha256: str = Field(pattern=SHA256_HEX)


def test_canonical_json_is_key_order_invariant() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": (1, 2), "b": 1})


def test_canonical_json_compact_sorted_utf8() -> None:
    assert canonical_json({"乙": "值", "甲": 1}) == '{"乙":"值","甲":1}'.encode("utf-8")


def test_canonical_hash_matches_direct_sha256() -> None:
    value = {"key": ["值", 3]}
    expected = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert canonical_hash(value) == expected


def test_jsonable_unwraps_models_and_containers() -> None:
    sealed = hash_model(Sealed, {"name": "n"}, "payload_sha256")
    assert jsonable({"item": sealed})["item"]["name"] == "n"
    assert jsonable((1, {"a": (2,)})) == [1, {"a": [2]}]


def test_model_and_equivalent_mapping_hash_identically() -> None:
    sealed = hash_model(Sealed, {"name": "n"}, "payload_sha256")
    assert canonical_hash(sealed) == canonical_hash(sealed.model_dump(mode="json"))


def test_sha256_text_matches_sha256_bytes() -> None:
    assert sha256_text("值") == sha256_bytes("值".encode("utf-8"))


def test_file_hash_matches_direct_digest(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"x" * (1024 * 1024 + 17))
    assert file_hash(target) == hashlib.sha256(target.read_bytes()).hexdigest()


def test_hash_model_seal_excludes_version_and_seal_field() -> None:
    sealed = hash_model(Sealed, {"name": "n"}, "payload_sha256")
    expected = canonical_hash(sealed.model_dump(mode="json", exclude={"schema_version", "payload_sha256"}))
    assert sealed.payload_sha256 == expected


def test_hash_model_is_deterministic() -> None:
    first = hash_model(Sealed, {"name": "n"}, "payload_sha256")
    second = hash_model(Sealed, {"name": "n"}, "payload_sha256")
    assert first == second
