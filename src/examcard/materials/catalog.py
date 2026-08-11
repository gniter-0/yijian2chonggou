"""Load versioned V2 profiles without coupling them to the V1 subject map."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .schema import ContentPolicy, MaterialProfile


def _load_mapping(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return raw


def load_content_policy(path: Path) -> ContentPolicy:
    return ContentPolicy.model_validate(_load_mapping(path))


def load_material_profile(path: Path) -> MaterialProfile:
    return MaterialProfile.model_validate(_load_mapping(path))
