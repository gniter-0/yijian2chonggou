"""One-shot builder for knowledge/contracts/admission_fixtures.py + admission_legacy_hash.py.

Cross-bucket pure-data admission contracts (ruling: plan A, skeleton section 3),
extracted verbatim from the legacy phase modules:

  phase2r84   typed-evidence kernel family (SourcePageBinding .. ReviewedCandidate)
  phase2r810  ReviewedTypedEvidenceFixture
  phase2r9    Step3CardContract family (+ FakeArtifact deterministic draft)
  phase2r98   DeterministicDraftCard
  phase2r10   Step3HumanDecision / ReviewedStep3Card / ReviewedStep3Fixture
  phase2r22   Step3SourceOwnedSemanticEdgeAudit
  phase2r2241 ExtractionMode / Remediation* provenance models
  phase2r2242 Step3Merged* / Step3UnifiedReviewedReleaseFixture (the r2242 trio)
  phase2r319  Step4PilotHandoffRefreshR319 handoff family

Class bodies are byte-exact slices. The only edits are Python identifier renames
for three colliding FIXTURE_VERSION constants and dropping one `r84.` module
qualifier -- string values, field names, schema_version literals and self-hash
protocols are untouched, so every persisted hash stays valid (bc-b8b9aca6).

admission_legacy_hash.py carries the legal admission-family hash implementation
byte-for-byte from phase2r84 (the admission kernel authority per the gates-review
survey); r810 duplicates it verbatim and the r9/r10 variants are output-equivalent
on model_dump payloads, which verify_admission_fixtures.py proves on the real
frozen artifacts.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

OLD_KV2 = Path(r"c:\Users\liushaocheng\Desktop\getinfo\yijian2\scripts\knowledge_v2")
CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "src" / "examcard" / "knowledge" / "contracts"


def collect_segments(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    lines = source.split("\n")
    entries: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Expr)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            if node.decorator_list:
                start = min(start, min(d.lineno for d in node.decorator_list))
            entries.append((node.name, start))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            entries.append((node.targets[0].id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            entries.append((node.target.id, node.lineno))
        elif isinstance(node, (ast.If, ast.Try)):
            continue
        else:
            raise SystemExit(f"unsupported top-level node {type(node).__name__} at line {node.lineno}")
    segments: dict[str, str] = {}
    for index, (name, start) in enumerate(entries):
        end = entries[index + 1][1] - 1 if index + 1 < len(entries) else len(lines)
        chunk = lines[start - 1 : end]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        segments[name] = "\n".join(chunk)
    return segments


def load(filename: str) -> dict[str, str]:
    return collect_segments((OLD_KV2 / filename).read_text(encoding="utf-8"))


def edited(segment: str, replacements: list[tuple[str, str, int]]) -> str:
    for before, after, expected in replacements:
        if segment.count(before) != expected:
            raise SystemExit(f"expected {expected} occurrence(s) of {before!r}, found {segment.count(before)}")
        segment = segment.replace(before, after)
    return segment


def main() -> None:
    r84 = load("phase2r84_selected_batch_human_decision_ingestion.py")
    r810 = load("phase2r810_r89_reviewed_admission_authorization.py")
    r9 = load("phase2r9_generic_step3_card_compiler.py")
    r98 = load("phase2r98_generic_step3_deterministic_release_closure.py")
    r10 = load("phase2r10_generic_step3_human_review_admission.py")
    r102 = load("phase2r102_production_step3_human_decision_session.py")
    r103 = load("phase2r103_step3_production_raw_decision_ingestion.py")
    r104 = load("phase2r104_step3_explicit_production_authorization.py")
    r22 = load("phase2r22_step3_teaching_reconciliation.py")
    r2241 = load("phase2r2241_step3_source_text_remediation.py")
    r2242 = load("phase2r2242_step3_reviewed_release_merge.py")
    r319 = load("phase2r319_step4_card4_reviewed_image_fixture.py")

    # ------------------------------------------------------------------
    # admission_legacy_hash.py -- BOTH legal hash variants coexist verbatim
    # (gates-review survey: r8x vs r9/r10x are distinct lineages; iron rule
    # bc-b8b9aca6). Builder helpers gain distinguishing identifiers only.
    # ------------------------------------------------------------------
    hash_doc = (
        '"""Legal legacy hash variants for the admission fixture family (both lineages).\n'
        "\n"
        "r8x lineage  (phase2r84, admission kernel; phase2r810 duplicates it verbatim):\n"
        "  canonical_json / canonical_hash / text_hash / hash_model_r8x -- the builder\n"
        "  does NOT exclude schema_version from the self-hash payload.\n"
        "r9/r10x lineage (phase2r102 bytes; phase2r10 imports canonical_hash from\n"
        "  phase2r9 and phase2r103/r104 call r102.canonical_hash):\n"
        "  _jsonable / canonical_json_r10x / canonical_hash_r10x / hash_model_r10x /\n"
        "  hash_model_r102 -- builders exclude {schema_version, field}.\n"
        "\n"
        "Function bodies are byte-exact slices; only builder def-lines and the internal\n"
        "canonical_hash binding inside the two r10x builders gained explicit _r10x names\n"
        "(identifier-level, hash-invisible). Output equivalence of the lineages on\n"
        "model_dump payloads is proven by migration/verify_admission_fixtures.py against\n"
        "the frozen production artifacts. Never fold into examcard.common.hashing.\n"
        '"""\n'
        "from __future__ import annotations\n"
        "\n"
        "import hashlib\n"
        "import json\n"
        "from collections.abc import Mapping\n"
        "from typing import Any\n"
        "\n"
        "from ...materials.schema import StrictModel\n"
    )
    hash_bodies = [
        "# --- r8x lineage (phase2r84, verbatim) ---",
        r84["canonical_json"],
        r84["canonical_hash"],
        r84["text_hash"],
        edited(r84["_hash_model"], [("def _hash_model(", "def hash_model_r8x(", 1)]),
        "# --- r9 lineage (phase2r9 canonical_json traversal, verbatim body; def-line",
        "# disambiguated -- its canonical_hash/text_hash bodies are byte-identical to",
        "# the r84 ones above, so the shared names cover them) ---",
        edited(
            r9["canonical_json"],
            [
                ("def canonical_json(", "def canonical_json_r9x(", 1),
                ("json.loads(canonical_json(item)", "json.loads(canonical_json_r9x(item)", 2),
            ],
        ),
        "# --- r10x lineage (phase2r102 / phase2r10 bytes; builder names disambiguated) ---",
        r102["_jsonable"],
        edited(r10["canonical_json"], [("def canonical_json(", "def canonical_json_r10x(", 1)]),
        edited(r102["canonical_hash"], [("def canonical_hash(", "def canonical_hash_r10x(", 1)]),
        edited(
            r102["_hash_model"],
            [
                ("def _hash_model(", "def hash_model_r102(", 1),
                ("canonical_hash({key", "canonical_hash_r10x({key", 1),
            ],
        ),
        edited(
            r10["_hash_model"],
            [
                ("def _hash_model(", "def hash_model_r10x(", 1),
                ("canonical_hash(draft", "canonical_hash_r10x(draft", 1),
            ],
        ),
    ]
    hash_content = hash_doc + "\n\n" + "\n\n\n".join(hash_bodies) + "\n"
    (CONTRACTS_DIR / "admission_legacy_hash.py").write_bytes(hash_content.encode("utf-8"))
    print(f"wrote admission_legacy_hash.py ({len(hash_content)} chars)")

    # ------------------------------------------------------------------
    # admission_fixtures.py
    # ------------------------------------------------------------------
    doc = (
        '"""Cross-bucket pure-data admission contracts (ruling: plan A).\n'
        "\n"
        "Verbatim extractions from the legacy phase modules (r84/r810/r9/r98/r10/r22/\n"
        "r2241/r2242/r319). schema_version literals, field shapes and self-hash\n"
        "protocols are untouched; only three colliding FIXTURE_VERSION constant\n"
        "identifiers gained phase prefixes and one r84. module qualifier was dropped\n"
        "(identifier-level edits, invisible to serialization and hashing).\n"
        '"""\n'
        "from __future__ import annotations\n"
        "\n"
        "import re\n"
        "from enum import StrEnum\n"
        "from pathlib import Path\n"
        "from typing import Annotated, Literal\n"
        "\n"
        "from pydantic import Field, model_validator\n"
        "\n"
        "from ...materials.schema import StrictModel\n"
        "\n"
        "from .admission_legacy_hash import canonical_hash, canonical_hash_r10x, text_hash\n"
    )

    constants = "\n".join(
        [
            "# --- phase2r84 typed-evidence kernel ---",
            r84["HASH_PATTERN"],
            r84["ID_PATTERN"],
            r84["NOISE_MARKERS"],
            r84["SOURCE_PAGE_VERSION"],
            r84["PROVENANCE_VERSION"],
            r84["TYPED_TEXT_VERSION"],
            r84["NODE_VERSION"],
            r84["EDGE_VERSION"],
            r84["DIAGRAM_VERSION"],
            r84["TABLE_CELL_VERSION"],
            r84["TABLE_VERSION"],
            r84["LIST_ITEM_VERSION"],
            r84["LIST_VERSION"],
            r84["CONSTRAINT_TOKEN_VERSION"],
            r84["CONSTRAINT_VERSION"],
            r84["FORMULA_TOKEN_VERSION"],
            r84["FORMULA_VERSION"],
            r84["REVIEWED_CANDIDATE_VERSION"],
            "",
            "# --- phase2r810 (FIXTURE_VERSION renamed with phase prefix) ---",
            edited(r810["FIXTURE_VERSION"], [("FIXTURE_VERSION", "R810_FIXTURE_VERSION", 1)]),
            "",
            "# --- phase2r9 Step3 card-contract family ---",
            r9["CONTRACT_VERSION"],
            r9["DRAFT_VERSION"],
            r9["HASH"],
            r9["ID"],
            r9["SOURCE_CANDIDATE"],
            r9["CONTRACT_ID"],
            r9["FORBIDDEN_TEXT"],
            "",
            "# --- phase2r98 deterministic draft / release bundle ---",
            r98["DRAFT_CARD_VERSION"],
            r98["RELEASE_VERSION"],
            "",
            "# --- phase2r10 reviewed Step 3 admission (FIXTURE_VERSION renamed) ---",
            r10["DECISION_VERSION"],
            r10["REVIEWED_CARD_VERSION"],
            edited(r10["FIXTURE_VERSION"], [("FIXTURE_VERSION", "R10_FIXTURE_VERSION", 1)]),
            "",
            "# --- phase2r103 empty refusal-form fixture (FIXTURE_VERSION renamed) ---",
            edited(r103["FIXTURE_VERSION"], [("FIXTURE_VERSION", "R103_FIXTURE_VERSION", 1)]),
            "",
            "# --- shared release-fixture patterns (phase2r22/r2241/r2242 values coincide) ---",
            r2242["_SHA256_PATTERN"],
            r2242["_FACT_ID_PATTERN"],
            r2242["_CARD_ID_PATTERN"],
            r2241["_ANCHOR_ID_PATTERN"],
            "",
            "# --- phase2r2242 unified release fixture (FIXTURE_VERSION renamed; source",
            "# fixture paths inlined with the exact legacy values) ---",
            edited(r2242["FIXTURE_VERSION"], [("FIXTURE_VERSION", "R2242_FIXTURE_VERSION", 1)]),
            r2242["UNIFIED_FIXTURE"],
            'SOURCE_PHASE224_FIXTURE = Path(\n    "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r224_step3_reviewed_release.yaml"\n)',
            'SOURCE_PHASE2241_FIXTURE = Path(\n    "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2241_reviewed_source_text_remediation.yaml"\n)',
            r2242["REVIEWER"],
            r2242["REVIEWED_AT_UTC"],
            r2242["EXPECTED_FACT_COUNT"],
            r2242["EXPECTED_CARD_COUNT"],
            r2242["EXPECTED_DISPLAY_TEXT_COUNT"],
            r2242["EXPECTED_SEMANTIC_EDGE_COUNT"],
            r2242["EXPECTED_ORIGINAL_CARD_COUNT"],
            r2242["EXPECTED_REMEDIATION_CARD_COUNT"],
            "",
            "# --- phase2r104 admission run-evidence family (constants gain R104_ prefixes;",
            "# string values untouched) ---",
            edited(r104["PHASE"], [("PHASE", "R104_PHASE", 1)]),
            edited(r104["FREEZE_VERSION"], [("FREEZE_VERSION", "R104_FREEZE_VERSION", 1)]),
            edited(r104["GATE_CHECK_VERSION"], [("GATE_CHECK_VERSION", "R104_GATE_CHECK_VERSION", 1)]),
            edited(r104["GATE_VERSION"], [("GATE_VERSION", "R104_GATE_VERSION", 1)]),
            edited(r104["ACCEPTANCE_VERSION"], [("ACCEPTANCE_VERSION", "R104_ACCEPTANCE_VERSION", 1)]),
            "",
            "# --- phase2r319 Step 4 pilot handoff ---",
            r319["HANDOFF_VERSION"],
            r319["SOURCE_R316_RUN"],
            r319["SOURCE_R316_HANDOFF"],
            r319["_SHA"],
            r319["_CARD"],
        ]
    )

    bodies = [
        # phase2r84 family (verbatim)
        r84["SourcePageBinding"],
        r84["TypedElementProvenance"],
        r84["TypedTextBase"],
        r84["DiagramNode"],
        r84["DiagramEdge"],
        r84["DiagramContract"],
        r84["TableCell"],
        r84["TableContract"],
        r84["OrderedListItem"],
        r84["OrderedListContract"],
        r84["ConstraintToken"],
        r84["ConstraintContract"],
        r84["FormulaToken"],
        r84["FormulaContract"],
        r84["TypedContract"],
        r84["ReviewedCandidate"],
        # phase2r810 fixture (two identifier-level edits)
        edited(
            r810["ReviewedTypedEvidenceFixture"],
            [
                ("Literal[FIXTURE_VERSION] = FIXTURE_VERSION", "Literal[R810_FIXTURE_VERSION] = R810_FIXTURE_VERSION", 1),
                ("r84.ReviewedCandidate", "ReviewedCandidate", 1),
            ],
        ),
        # phase2r9 Step3 card-contract family. Lineage audit trail (gates-review
        # ruling): the r9/r10x-hashed families call canonical_hash_r10x EXPLICITLY
        # below (legacy sources called the unqualified r9-lineage canonical_hash /
        # r102.canonical_hash); the r8x families above keep the unqualified name.
        r9["SourceProvenance"],
        r9["DisplayElement"],
        r9["TableCellRef"],
        r9["ListItemRef"],
        r9["TokenRef"],
        r9["DiagramNodeRef"],
        r9["DiagramEdgeRef"],
        r9["LayoutContract"],
        r9["GeometryConstraints"],
        edited(r9["Step3CardContract"], [("canonical_hash(", "canonical_hash_r10x(", 2)]),
        edited(r9["FakeArtifact"], [("canonical_hash(", "canonical_hash_r10x(", 1)]),
        r9["DraftBundle"],
        # phase2r98 (verbatim; DeterministicReleaseBundle is cross-bucket-consumed by
        # the gates/review r10 queue, ruled into admission_fixtures by the
        # compilers/release-gate + gates-review joint decision)
        r98["DeterministicDraftCard"],
        edited(r98["DeterministicReleaseBundle"], [("canonical_hash(", "canonical_hash_r10x(", 1)]),
        # phase2r10 (fixture version identifier renamed; r10x lineage made explicit)
        edited(r10["Step3HumanDecision"], [("canonical_hash(", "canonical_hash_r10x(", 1)]),
        edited(r10["ReviewedStep3Card"], [("canonical_hash(", "canonical_hash_r10x(", 1)]),
        edited(
            r10["ReviewedStep3Fixture"],
            [
                ("Literal[FIXTURE_VERSION] = FIXTURE_VERSION", "Literal[R10_FIXTURE_VERSION] = R10_FIXTURE_VERSION", 1),
                ("canonical_hash(", "canonical_hash_r10x(", 1),
            ],
        ),
        # phase2r103 empty refusal-form fixture (class renamed with the R103 suffix per
        # gates-review ruling -- schema_version literal keeps the phase2r103_* lineage)
        edited(
            r103["ReviewedStep3Fixture"],
            [
                ("class ReviewedStep3Fixture(StrictModel):", "class ReviewedStep3FixtureR103(StrictModel):", 1),
                ("Literal[FIXTURE_VERSION] = FIXTURE_VERSION", "Literal[R103_FIXTURE_VERSION] = R103_FIXTURE_VERSION", 1),
                ('def validate_fixture(self) -> "ReviewedStep3Fixture":', 'def validate_fixture(self) -> "ReviewedStep3FixtureR103":', 1),
                ("r102.canonical_hash(", "canonical_hash_r10x(", 1),
            ],
        ),
        # phase2r22 (verbatim)
        r22["Step3SourceOwnedSemanticEdgeAudit"],
        # phase2r2241 (verbatim)
        r2241["ExtractionMode"],
        r2241["RemediationEvidenceBinding"],
        r2241["RemediationTableCell"],
        r2241["RemediationTableGrid"],
        # phase2r2242 trio (fixture version identifier renamed)
        r2242["Step3ReviewedSourceTier"],
        r2242["Step3MergedSourceOwnedDisplayText"],
        r2242["Step3MergedReviewedReleaseCard"],
        edited(
            r2242["Step3UnifiedReviewedReleaseFixture"],
            [
                ("schema_version: str = FIXTURE_VERSION", "schema_version: str = R2242_FIXTURE_VERSION", 1),
                ("if self.schema_version != FIXTURE_VERSION:", "if self.schema_version != R2242_FIXTURE_VERSION:", 1),
            ],
        ),
        # phase2r104 run-evidence family (r102. qualifier rebound to the r10x lineage;
        # version-constant identifiers gain R104_ prefixes)
        edited(
            r104["FrozenArtifact"],
            [("Literal[FREEZE_VERSION] = FREEZE_VERSION", "Literal[R104_FREEZE_VERSION] = R104_FREEZE_VERSION", 1)],
        ),
        edited(
            r104["PriorFreeze"],
            [
                ("Literal[FREEZE_VERSION] = FREEZE_VERSION", "Literal[R104_FREEZE_VERSION] = R104_FREEZE_VERSION", 1),
                ("Literal[PHASE] = PHASE", "Literal[R104_PHASE] = R104_PHASE", 1),
                ("r102.canonical_hash(", "canonical_hash_r10x(", 2),
            ],
        ),
        edited(
            r104["GateCheck"],
            [("Literal[GATE_CHECK_VERSION] = GATE_CHECK_VERSION", "Literal[R104_GATE_CHECK_VERSION] = R104_GATE_CHECK_VERSION", 1)],
        ),
        edited(
            r104["OfflineGate"],
            [
                ("Literal[GATE_VERSION] = GATE_VERSION", "Literal[R104_GATE_VERSION] = R104_GATE_VERSION", 1),
                ("Literal[PHASE] = PHASE", "Literal[R104_PHASE] = R104_PHASE", 1),
                ("r102.canonical_hash(", "canonical_hash_r10x(", 1),
            ],
        ),
        edited(
            r104["JUnitReceipt"],
            [("Literal[ACCEPTANCE_VERSION] = ACCEPTANCE_VERSION", "Literal[R104_ACCEPTANCE_VERSION] = R104_ACCEPTANCE_VERSION", 1)],
        ),
        edited(
            r104["AcceptanceReport"],
            [
                ("Literal[ACCEPTANCE_VERSION] = ACCEPTANCE_VERSION", "Literal[R104_ACCEPTANCE_VERSION] = R104_ACCEPTANCE_VERSION", 1),
                ("Literal[PHASE] = PHASE", "Literal[R104_PHASE] = R104_PHASE", 1),
                ("r102.canonical_hash(", "canonical_hash_r10x(", 1),
            ],
        ),
        # phase2r319 handoff family (handoff class drops the R319 suffix per the
        # gates-review authority list; class names do not participate in hashing)
        r319["Step4ReviewedImageReferenceR319"],
        r319["Step4PilotCardDispositionR319"],
        edited(
            r319["Step4PilotHandoffRefreshR319"],
            [("Step4PilotHandoffRefreshR319", "Step4PilotHandoffRefresh", 2)],
        ),
    ]

    content = doc + "\n\n" + constants + "\n\n\n" + "\n\n\n".join(bodies) + "\n"
    (CONTRACTS_DIR / "admission_fixtures.py").write_bytes(content.encode("utf-8"))
    print(f"wrote admission_fixtures.py ({len(content)} chars, {len(bodies)} models)")
    for name in ("admission_fixtures.py", "admission_legacy_hash.py"):
        digest = hashlib.sha256((CONTRACTS_DIR / name).read_bytes()).hexdigest()
        print(f"  {name} sha256 {digest[:16]}...")


if __name__ == "__main__":
    main()
