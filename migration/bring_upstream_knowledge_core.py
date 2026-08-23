"""Verbatim placement of the three knowledge-core upstream modules contracts/ needs.

Scope note: knowledge/schema.py, knowledge/identity.py and knowledge/continuation/review.py
belong to the knowledge-core migration task, not the contracts split. They are placed
here verbatim (only the import prefix mapping from skeleton section 6-1 applied) purely
so the contracts package can be smoke-imported and hash/schema parity can run.
Files that already exist are left untouched so the owning member's work wins.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from examcard.utils.legacy_root import resolve_legacy_root  # noqa: E402

OLD_ROOT = resolve_legacy_root(REPO_ROOT) / "scripts" / "knowledge_v2"
NEW_KNOWLEDGE = REPO_ROOT / "src" / "examcard" / "knowledge"

PLAN = [
    (
        OLD_ROOT / "schema.py",
        NEW_KNOWLEDGE / "schema.py",
        [
            (
                "from evidence_v2.schema import BBox",
                "from ..evidence.schema import BBox",
            ),
            (
                "from materials_v2.schema import StrictModel, V2_SCHEMA_VERSION",
                "from ..materials.schema import StrictModel, V2_SCHEMA_VERSION",
            ),
        ],
    ),
    (
        OLD_ROOT / "identity.py",
        NEW_KNOWLEDGE / "identity.py",
        [],
    ),
    (
        OLD_ROOT / "continuation_review.py",
        NEW_KNOWLEDGE / "continuation" / "review.py",
        [
            (
                "from evidence_v2.layout import normalized_compare_text",
                "from ...evidence.layout import normalized_compare_text",
            ),
            (
                "from materials_v2.schema import StrictModel, V2_SCHEMA_VERSION",
                "from ...materials.schema import StrictModel, V2_SCHEMA_VERSION",
            ),
            (
                "from .schema import CompletenessMode, FactType",
                "from ..schema import CompletenessMode, FactType",
            ),
        ],
    ),
]


def main() -> None:
    for old_path, new_path, replacements in PLAN:
        if new_path.exists():
            print(f"skip (already exists): {new_path}")
            continue
        text = old_path.read_bytes().decode("utf-8")
        for before, after in replacements:
            if text.count(before) != 1:
                raise SystemExit(f"{old_path.name}: expected exactly one occurrence of {before!r}")
            text = text.replace(before, after)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(text.encode("utf-8"))
        print(
            f"wrote {new_path} (source sha256 {hashlib.sha256(old_path.read_bytes()).hexdigest()[:16]}..., "
            f"{len(replacements)} import lines rewritten)"
        )


if __name__ == "__main__":
    main()
