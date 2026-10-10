"""Frozenset fields must serialize to JSON independently of PYTHONHASHSEED.

Step 2A page snapshots, Evidence manifests and content-policy fingerprints are
hashed from JSON dumps.  A frozenset dumps in iteration order, which follows the
per-process string hash seed, so without sorting the same document got a
different request id in every process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from examcard.evidence.schema import EvidenceKind, EvidenceProvenance, SourceChannel, SourceQuality
from examcard.evidence.units import build_evidence_unit
from examcard.materials.schema import ContentPolicy, SemanticRole

_SRC = Path(__file__).resolve().parents[1] / "src"
_SEEDS = ("0", "1", "2", "3", "17", "4242")

_PROBE = r"""
import json
from examcard.evidence.schema import EvidenceKind, SourceChannel, SourceQuality
from examcard.evidence.units import build_evidence_unit
from examcard.materials.schema import ContentPolicy, SemanticRole

unit = build_evidence_unit(
    document_sha256="a" * 64,
    page_num=1,
    kind=EvidenceKind.TABLE_CELL,
    bbox=(0, 0, 10, 10),
    raw_text="cell",
    canonical_text="cell",
    semantic_role=SemanticRole.CORE,
    source_quality=SourceQuality.VERIFIED_AGREEMENT,
    confidence=1.0,
    order=0,
    source_channels=set(SourceChannel),
    engine_versions={"test": "1"},
    config_hash="test-config",
    native_block_index=0,
    generated_at_utc="2026-01-01T00:00:00Z",
    risk_tags={"watermark", "low_confidence", "merged_cell", "rotated", "ocr_conflict", "z", "a"},
)
policy = ContentPolicy(
    policy_id="probe",
    revision=1,
    display_name="probe",
    included_roles=frozenset({SemanticRole.CORE, SemanticRole.HEADING, SemanticRole.WORKED_EXAMPLE}),
    excluded_roles=frozenset({SemanticRole.EXERCISE_QUESTION, SemanticRole.EXERCISE_OPTION, SemanticRole.EXERCISE_ANSWER}),
)
print(unit.model_dump_json())
print(json.dumps(unit.model_dump(mode="json"), ensure_ascii=False))
print(policy.model_dump_json())
"""


def _probe(seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(_SRC), env.get("PYTHONPATH"))))
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout


def test_frozenset_json_is_identical_across_hash_seeds() -> None:
    outputs = {seed: _probe(seed) for seed in _SEEDS}

    assert len(set(outputs.values())) == 1, sorted(outputs)


def test_frozenset_json_is_sorted_and_round_trips() -> None:
    unit = build_evidence_unit(
        document_sha256="a" * 64,
        page_num=1,
        kind=EvidenceKind.PARAGRAPH,
        bbox=(0, 0, 10, 10),
        raw_text="text",
        canonical_text="text",
        semantic_role=SemanticRole.CORE,
        source_quality=SourceQuality.VERIFIED_AGREEMENT,
        confidence=1.0,
        order=0,
        source_channels={SourceChannel.VISUAL_OCR, SourceChannel.NATIVE_TEXT, SourceChannel.LAYOUT},
        engine_versions={"test": "1"},
        config_hash="test-config",
        native_block_index=0,
        generated_at_utc="2026-01-01T00:00:00Z",
        risk_tags={"watermark", "low_confidence", "merged_cell"},
    )
    dumped = json.loads(unit.model_dump_json())

    assert dumped["provenance"]["source_channels"] == ["layout", "native_text", "visual_ocr"]
    assert dumped["risk_tags"] == ["low_confidence", "merged_cell", "watermark"]
    assert type(unit).model_validate_json(unit.model_dump_json()) == unit
    # Python-mode dumps keep the set type; only JSON output is ordered.
    assert isinstance(unit.model_dump()["risk_tags"], frozenset)
    assert isinstance(unit.provenance.model_dump()["source_channels"], frozenset)


def test_content_policy_role_sets_dump_sorted() -> None:
    policy = ContentPolicy(
        policy_id="probe",
        revision=1,
        display_name="probe",
        included_roles=frozenset({SemanticRole.HEADING, SemanticRole.CORE}),
        manual_review_roles=frozenset({SemanticRole.EXERCISE_ANSWER, SemanticRole.EXAM_TAG}),
    )
    dumped = policy.model_dump(mode="json")

    assert dumped["included_roles"] == ["core", "heading"]
    assert dumped["manual_review_roles"] == ["exam_tag", "exercise_answer"]
    assert dumped["excluded_roles"] == []
    assert ContentPolicy.model_validate(dumped) == policy


def test_provenance_sorts_enum_members_by_value() -> None:
    provenance = EvidenceProvenance(
        source_channels=frozenset(SourceChannel),
        config_hash="c",
        generated_at_utc="2026-01-01T00:00:00Z",
    )

    assert provenance.model_dump(mode="json")["source_channels"] == sorted(item.value for item in SourceChannel)
