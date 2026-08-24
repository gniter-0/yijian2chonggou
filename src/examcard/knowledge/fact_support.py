"""事实链的仓根锚点与通用小工具（拆分层 0）。

ROOT 是全 src 唯一一处自锚定（parents[3] → 仓根）；本模块与 fact_engine.py
同目录，锚点深度不变。另含 JSON 读写、bbox 几何判断与文本 sha256。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..evidence.schema import BBox


# 仓根锚点：旧仓 scripts/knowledge_v2/fact_engine.py 的 parents[2] 即仓根；
# 新仓 src 布局下需 parents[3]（src/examcard/knowledge/ → 仓根），承载 data/、configs/ 等仓库相对资产契约。
ROOT = Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _bbox_overlaps(left: BBox | None, right: BBox | None) -> bool:
    if left is None or right is None:
        return False
    return max(left.x0, right.x0) < min(left.x1, right.x1) and max(left.y0, right.y0) < min(left.y1, right.y1)


def _bbox_contains(outer: BBox | None, inner: BBox | None) -> bool:
    if outer is None or inner is None:
        return False
    center_x = (inner.x0 + inner.x1) / 2
    center_y = (inner.y0 + inner.y1) / 2
    return outer.x0 <= center_x <= outer.x1 and outer.y0 <= center_y <= outer.y1


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
