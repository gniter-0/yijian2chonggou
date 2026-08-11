"""契约自证哈希：canonical JSON → sha256。

签名按 providers 稿 §6.1/§9.1 冻结（bc-888c6031 / bc-9d82a196）：
canonical_json / canonical_hash / sha256_bytes / sha256_text / file_hash / hash_model。
canonical 形态 = jsonable 归一 + sort_keys + separators=(",", ":") + ensure_ascii=False + UTF-8。

冻结哈希铁律（组公告 bc-b8b9aca6）：本模块仅供四桶**新代码**的契约自证；
旧契约法定哈希/指纹（canonical_step2_sha256、evidence identity._canonical/_digest、
stable_config_hash 等）逐字节随其所属包搬运，禁止改调本实现。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, TypeVar

from pydantic import BaseModel

SHA256_HEX = r"^[0-9a-f]{64}$"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_model(model_type: type[_ModelT], payload: Mapping[str, Any], field: str) -> _ModelT:
    """自证哈希封印：哈希投影 = model_dump(mode="json") 排除 {schema_version, field} 本身。"""
    document = dict(payload)
    document[field] = "0" * 64
    document[field] = canonical_hash(model_type.model_construct(**document).model_dump(mode="json", exclude={"schema_version", field}))
    return model_type.model_validate(document)
