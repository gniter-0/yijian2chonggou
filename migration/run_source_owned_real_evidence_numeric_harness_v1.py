"""Exercise source-owned numeric derivation against real PDF Evidence offline.

The harness owns only orchestration for this verification.  It ingests a PDF
with the existing Evidence Engine, keeps the resulting spans in memory, and
constructs one minimal typed source-owned proposal whose assertion contains a
locally observed quantitative token.  It never calls extraction, transport,
or a provider, and persists only the allow-listed summary.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

# The migration runner bootstraps the local source tree before importing the
# package, so its imports intentionally follow the guarded sys.path setup.
# ruff: noqa: E402
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from examcard.evidence.ingest import EvidenceEngine, EvidenceEngineConfig
from examcard.evidence.schema import DocumentEvidenceManifest, EvidenceUnit
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    SourceOwnedCanonicalProposal,
    SourceOwnedLocalDerivationResult,
    source_owned_derive_atomic_numeric_claims,
    source_owned_derive_table_atomic_numeric_claims,
)
from examcard.knowledge.contracts.enums import NumericTokenClass
from examcard.knowledge.contracts.numeric_tokens import classify_numeric_tokens
from examcard.knowledge.fact_policy import decide_policy
from examcard.knowledge.schema import FactType
from examcard.materials import load_content_policy, load_material_profile
from examcard.materials.schema import ContentPolicy, PolicyDisposition, StrictModel
from migration.run_source_owned_real_pilot_v1 import _make_anchor


type NumericDerivationStatus = Literal["derived", "no_quantitative_tokens", "failed_closed"]


DEFAULT_PROFILE_PATH = REPOSITORY_ROOT / "configs/v2/material_profiles/yijian_gonglu_2026.yaml"
DEFAULT_POLICY_PATH = REPOSITORY_ROOT / "configs/v2/content_policies/core_only_v1.yaml"
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "pilot_runs" / "source_owned" / "numeric_harness"


class SourceOwnedRealEvidenceNumericHarnessSummary(StrictModel):
    """Persisted result with no Evidence or provider content."""

    document_sha256: str
    pages: int
    evidence_units: int
    numeric_derivation_status: NumericDerivationStatus
    grounded_numeric_claim_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_summary(output_dir: Path, summary: SourceOwnedRealEvidenceNumericHarnessSummary) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / "summary.json.tmp"
    temporary.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_dir / "summary.json")


def _quantitative_values(text: str) -> tuple[str, ...]:
    return tuple(
        token.value
        for token in classify_numeric_tokens(text)
        if token.classification == NumericTokenClass.QUANTITATIVE_VALUE
    )


def _included_units(manifest: DocumentEvidenceManifest, *, policy: ContentPolicy) -> tuple[EvidenceUnit, ...]:
    return tuple(
        unit
        for page in manifest.pages
        for unit in page.evidence_units
        if unit.canonical_text.strip()
        and decide_policy(unit=unit, policy=policy).disposition == PolicyDisposition.INCLUDED
    )


def _select_numeric_input(
    manifest: DocumentEvidenceManifest,
    *,
    policy: ContentPolicy,
) -> tuple[EvidenceUnit, str | None] | None:
    units = _included_units(manifest, policy=policy)
    for unit in units:
        values = _quantitative_values(unit.canonical_text)
        if len(values) == 1:
            return unit, values[0]
    return (units[0], None) if units else None


def _proposal_for_anchor(anchor: EvidenceAnchorSpan, *, quantitative_value: str | None) -> SourceOwnedCanonicalProposal:
    assertion = (
        f"The locally observed value is {quantitative_value}."
        if quantitative_value is not None
        else "The locally observed statement is qualitative."
    )
    return SourceOwnedCanonicalProposal(
        proposal_key="proposal:real-evidence-numeric-harness",
        fact_type=FactType.DEFINITION,
        label="Real evidence numeric fact",
        assertion=assertion,
        primary_anchor_span_ids=(anchor.anchor_span_id,),
    )


def derive_source_owned_real_evidence_numeric_input(
    *,
    proposal: SourceOwnedCanonicalProposal,
    evidence_spans: Mapping[str, EvidenceAnchorSpan],
    table_atomic: bool = False,
) -> SourceOwnedLocalDerivationResult:
    """Dispatch an in-memory typed input to an existing local derivation lane."""

    if table_atomic:
        return source_owned_derive_table_atomic_numeric_claims(
            proposal=proposal,
            evidence_spans=evidence_spans,
        )
    return source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans=evidence_spans,
    )


def effective_numeric_derivation_status(result: SourceOwnedLocalDerivationResult) -> NumericDerivationStatus:
    """Expose one safe status for atomic and table-atomic verification."""

    if result.table_atomic_derivation_status in {"failed_closed", "unsupported_composition"}:
        return "failed_closed"
    return result.numeric_derivation_status


def _failed_summary(*, document_sha256: str) -> SourceOwnedRealEvidenceNumericHarnessSummary:
    return SourceOwnedRealEvidenceNumericHarnessSummary(
        document_sha256=document_sha256,
        pages=0,
        evidence_units=0,
        numeric_derivation_status="failed_closed",
        grounded_numeric_claim_count=0,
    )


def run_source_owned_real_evidence_numeric_harness(
    pdf_path: Path,
    *,
    output_dir: Path | None = None,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    policy_path: Path = DEFAULT_POLICY_PATH,
) -> SourceOwnedRealEvidenceNumericHarnessSummary:
    """Run numeric derivation over real Evidence without model or provider calls."""

    if os.environ.get("REAL_NETWORK", "0") != "0":
        raise ValueError("source-owned numeric harness requires REAL_NETWORK=0")
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise ValueError("source-owned numeric harness requires an existing PDF path")
    document_sha256 = _sha256_file(pdf_path)
    target = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_ROOT / pdf_path.stem

    policy = load_content_policy(policy_path)
    profile = load_material_profile(profile_path)
    if profile.content_policy_id != policy.policy_id or profile.content_policy_revision != policy.revision:
        raise ValueError("numeric harness material profile and content policy are inconsistent")

    engine = EvidenceEngine(
        profile=profile,
        cache_root=target / "cache",
        config=EvidenceEngineConfig(enable_visual_review=False),
        cache_enabled=False,
    )
    try:
        manifest, _report = engine.ingest_document(
            pdf_path,
            source_relative_path=pdf_path.name,
            output_dir=None,
        )
    except Exception:
        summary = _failed_summary(document_sha256=document_sha256)
        _write_summary(target, summary)
        return summary

    pages = len(manifest.pages)
    evidence_units = sum(len(page.evidence_units) for page in manifest.pages)
    selected = _select_numeric_input(manifest, policy=policy)
    if selected is None:
        summary = SourceOwnedRealEvidenceNumericHarnessSummary(
            document_sha256=manifest.source.sha256,
            pages=pages,
            evidence_units=evidence_units,
            numeric_derivation_status="no_quantitative_tokens",
            grounded_numeric_claim_count=0,
        )
        _write_summary(target, summary)
        return summary

    unit, quantitative_value = selected
    anchor = _make_anchor(unit, policy=policy)
    proposal = _proposal_for_anchor(anchor, quantitative_value=quantitative_value)
    result = derive_source_owned_real_evidence_numeric_input(
        proposal=proposal,
        evidence_spans={anchor.anchor_span_id: anchor},
    )
    summary = SourceOwnedRealEvidenceNumericHarnessSummary(
        document_sha256=manifest.source.sha256,
        pages=pages,
        evidence_units=evidence_units,
        numeric_derivation_status=effective_numeric_derivation_status(result),
        grounded_numeric_claim_count=len(result.grounded_numeric_claims),
    )
    _write_summary(target, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    summary = run_source_owned_real_evidence_numeric_harness(
        args.pdf_path,
        output_dir=args.output_dir,
    )
    print(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
