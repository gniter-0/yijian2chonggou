"""Deterministic local cache for V2 evidence artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)


def stable_config_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ArtifactCache:
    """File cache with atomic writes. Cache keys are already versioned upstream."""

    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        safe_key = key.replace(":", "_")
        return self.root / safe_key[:2] / f"{safe_key}.json"

    def get_model(self, key: str, model: type[ModelT]) -> ModelT | None:
        if not self.enabled:
            self.misses += 1
            return None
        path = self._path(key)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            value = model.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put_model(self, key: str, value: BaseModel) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = value.model_dump_json(indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(rendered)
            temp_path = Path(handle.name)
        try:
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
