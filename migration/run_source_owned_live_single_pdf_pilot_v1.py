"""Run one PDF through the source-owned live provider boundary.

The runner is orchestration only.  It receives an explicit source-owned live
transport so tests can inject a fake without ever opening a network socket;
the production transport can be supplied by a later operator entry point.
Evidence ingestion is in-memory with persistence disabled, and the only
optional artifact written by this module is the allow-listed summary.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

# Migration runners are executable from the repository root or directly from
# the migration directory.
# ruff: noqa: E402
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from examcard.evidence.ingest import EvidenceEngine, EvidenceEngineConfig
from examcard.evidence.schema import EvidenceUnit
from examcard.knowledge.contracts import (
    EvidenceAnchorSpan,
    SourceOwnedKnowledgeCardMaterializationInput,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    build_source_owned_provider_request,
    source_owned_admit_card_candidate,
    source_owned_assemble_canonical_fact,
    source_owned_bridge_to_knowledge_card,
    source_owned_derive_atomic_numeric_claims,
    source_owned_fact_to_card_candidate,
    source_owned_materialize_knowledge_card,
    source_owned_select_anchor_spans,
    Step2DecoderConfig,
    Step2ResponseFormat,
)
from examcard.knowledge.extraction_v1 import source_owned_extract_document_proposals
from examcard.knowledge.schema import EvidenceRef
from examcard.knowledge.source_owned_live_transport import (
    SourceOwnedLiveProviderDispatchResult,
    SourceOwnedLiveProviderTransport,
    SourceOwnedLiveTransportDiagnostic,
    dispatch_source_owned_live_provider_request,
)
from examcard.materials import load_content_policy, load_material_profile
from examcard.materials.schema import ContentPolicy, StrictModel
from examcard.pipeline.live_env import load_run_env, text_api_key, text_base_url
from examcard.knowledge.source_owned_live_transport import (
    OpenAICompatibleSourceOwnedLiveTransport,
)

from migration.run_source_owned_real_pilot_v1 import (
    DEFAULT_POLICY_PATH,
    DEFAULT_PROFILE_PATH,
    _make_anchor,
    _select_local_unit,
)


type ResponseValidationStatus = Literal["validated", "rejected"]
type AnchorSelectionStatus = Literal[
    "selected",
    "no_matching_anchor",
    "ambiguous_anchor",
    "failed_closed",
]
type DerivationStatus = Literal["derived", "no_quantitative_tokens", "failed_closed"]
type MaterializationStatus = Literal["materialized", "failed_closed"]
type SourceOwnedLiveDiagnosticSink = Callable[[SourceOwnedLiveProviderDispatchResult], None]

DEFAULT_MODEL = "source-owned-live-model"
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "pilot_runs" / "source_owned" / "live_single_pdf"
SOURCE_OWNED_LIVE_TIMEOUT_SECONDS = 600.0


class SourceOwnedLiveOperatorConfigurationError(ValueError):
    """Safe operator-edge configuration failure without secret-bearing detail."""

    def __init__(self, safe_error_category: str) -> None:
        self.safe_error_category = safe_error_category
        super().__init__("source-owned live operator configuration failed")


class SourceOwnedLiveSinglePdfPilotSummary(StrictModel):
    """The complete persisted output contract for one live-boundary pilot."""

    document_sha256: str
    pages: int
    evidence_units: int
    provider_call_count: int
    response_validation_status: ResponseValidationStatus
    proposal_count: int
    anchor_selection_status: AnchorSelectionStatus
    derivation_status: DerivationStatus
    grounded_numeric_claim_count: int
    materialization_status: MaterializationStatus


def _resolve_source_owned_live_model(
    explicit_model: str | None,
    env: Mapping[str, str],
) -> str:
    model = (explicit_model or env.get("TEXT_MODEL") or "").strip()
    if not model:
        raise SourceOwnedLiveOperatorConfigurationError("model_missing")
    return model


def _source_owned_decoder_config_from_env(
    env: Mapping[str, str],
) -> Step2DecoderConfig:
    """Parse the provider response mode without changing local validation."""

    raw_mode = (env.get("STEP2_RESPONSE_MODE") or "").strip().upper()
    if not raw_mode or raw_mode == "STRICT_SCHEMA":
        response_format = Step2ResponseFormat.JSON_SCHEMA_STRICT
    elif raw_mode == "JSON_OBJECT":
        response_format = Step2ResponseFormat.JSON_OBJECT
    else:
        raise SourceOwnedLiveOperatorConfigurationError("response_mode_invalid")
    return Step2DecoderConfig(response_format=response_format)


def _load_source_owned_live_runtime_env(
    env_file: Path | str | None = None,
) -> dict[str, str]:
    """Load operator configuration without exposing filesystem errors."""

    try:
        return load_run_env(env_file)
    except FileNotFoundError as exc:
        raise SourceOwnedLiveOperatorConfigurationError("run_env_missing") from exc


def _build_default_source_owned_live_transport(
    *,
    env_file: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> OpenAICompatibleSourceOwnedLiveTransport:
    """Build only the dedicated source-owned live transport at the CLI edge."""

    try:
        runtime_env = (
            dict(env)
            if env is not None
            else _load_source_owned_live_runtime_env(env_file)
        )
        base_url = text_base_url(runtime_env)
        api_key = text_api_key(runtime_env)
        user_agent = (runtime_env.get("OPENAI_USER_AGENT") or "").strip()
        default_headers = {"User-Agent": user_agent} if user_agent else None
        return OpenAICompatibleSourceOwnedLiveTransport(
            base_url=base_url,
            api_key=api_key,
            timeout=SOURCE_OWNED_LIVE_TIMEOUT_SECONDS,
            default_headers=default_headers,
        )
    except SourceOwnedLiveOperatorConfigurationError:
        raise
    except ValueError as exc:
        message = str(exc)
        category = "base_url_missing" if "text channel" in message and "API_BASE_URL" in message else "api_key_missing"
        raise SourceOwnedLiveOperatorConfigurationError(category) from exc
    except Exception as exc:
        raise SourceOwnedLiveOperatorConfigurationError("transport_construction_failed") from exc


def _write_safe_dispatch_diagnostic(
    result: SourceOwnedLiveProviderDispatchResult,
) -> None:
    """Write only typed, non-content dispatch metadata to the operator sink."""

    fields = [
        f"stage={result.stage}",
        f"safe_error_category={result.safe_error_category}",
        f"provider_call_count={result.provider_call_count}",
    ]
    if result.response_sha256 is not None:
        fields.append(f"response_sha256={result.response_sha256}")
    if result.response_length > 0:
        fields.append(f"response_length={result.response_length}")
    if result.finish_reason:
        safe_finish_reason = "".join(
            character for character in result.finish_reason if character.isalnum() or character in "._:-"
        )
        if safe_finish_reason:
            fields.append(f"finish_reason={safe_finish_reason}")
    if result.decoder_diagnostic is not None:
        diagnostic = result.decoder_diagnostic
        fields.extend(
            [
                f"decoder_diagnostic_category={diagnostic.category}",
                f"decoder_validation_error_count={diagnostic.validation_error_count}",
                f"decoder_missing_field_count={diagnostic.missing_field_count}",
                f"decoder_extra_forbidden_field_count={diagnostic.extra_forbidden_field_count}",
            ]
        )
        if diagnostic.distinct_error_types:
            fields.append(
                "decoder_distinct_error_types="
                + ",".join(diagnostic.distinct_error_types)
            )
    if result.transport_diagnostic is not None:
        diagnostic: SourceOwnedLiveTransportDiagnostic = result.transport_diagnostic
        fields.append(f"transport_diagnostic_category={diagnostic.category}")
        if diagnostic.http_status_code is not None:
            fields.append(f"transport_http_status_code={diagnostic.http_status_code}")
    print(" ".join(fields), file=sys.stderr)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_summary(output_dir: Path, summary: SourceOwnedLiveSinglePdfPilotSummary) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / "summary.json.tmp"
    temporary.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_dir / "summary.json")


def _summary(
    *,
    document_sha256: str,
    pages: int,
    evidence_units: int,
    provider_call_count: int = 0,
    response_validation_status: ResponseValidationStatus = "rejected",
    proposal_count: int = 0,
    anchor_selection_status: AnchorSelectionStatus = "failed_closed",
    derivation_status: DerivationStatus = "failed_closed",
    grounded_numeric_claim_count: int = 0,
    materialization_status: MaterializationStatus = "failed_closed",
) -> SourceOwnedLiveSinglePdfPilotSummary:
    return SourceOwnedLiveSinglePdfPilotSummary(
        document_sha256=document_sha256,
        pages=pages,
        evidence_units=evidence_units,
        provider_call_count=provider_call_count,
        response_validation_status=response_validation_status,
        proposal_count=proposal_count,
        anchor_selection_status=anchor_selection_status,
        derivation_status=derivation_status,
        grounded_numeric_claim_count=grounded_numeric_claim_count,
        materialization_status=materialization_status,
    )


def _evidence_model_input(
    *,
    document_sha256: str,
    anchor: EvidenceAnchorSpan,
) -> dict[str, object]:
    """Build transient model input from the locally selected Evidence span."""

    return {
        "document_sha256": document_sha256,
        "anchor_span_ids": [anchor.anchor_span_id],
        "evidence_anchor_spans": [
            {
                "anchor_span_id": anchor.anchor_span_id,
                "parent_evidence_id": anchor.parent_evidence_id,
                "canonical_text": anchor.canonical_text,
            }
        ],
    }


def _evidence_ref(unit: EvidenceUnit) -> EvidenceRef:
    return EvidenceRef(evidence_id=unit.evidence_id, page_num=unit.locator.page_num)


def _ingest(
    *,
    pdf_path: Path,
    target: Path,
    profile_path: Path,
    policy_path: Path,
) -> tuple[object, ContentPolicy] | None:
    """Ingest one PDF without cache or raw artifact persistence."""

    policy = load_content_policy(policy_path)
    profile = load_material_profile(profile_path)
    if profile.content_policy_id != policy.policy_id or profile.content_policy_revision != policy.revision:
        return None
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
        return None
    return manifest, policy


def _failed_after_ingestion(
    *,
    manifest: object,
    provider_call_count: int,
    response_validation_status: ResponseValidationStatus = "rejected",
    proposal_count: int = 0,
    anchor_selection_status: AnchorSelectionStatus = "failed_closed",
    derivation_status: DerivationStatus = "failed_closed",
    grounded_numeric_claim_count: int = 0,
) -> SourceOwnedLiveSinglePdfPilotSummary:
    pages = getattr(manifest, "pages", ())
    source = getattr(manifest, "source", None)
    document_sha256 = getattr(source, "sha256", "")
    evidence_units = sum(len(page.evidence_units) for page in pages)
    return _summary(
        document_sha256=document_sha256,
        pages=len(pages),
        evidence_units=evidence_units,
        provider_call_count=provider_call_count,
        response_validation_status=response_validation_status,
        proposal_count=proposal_count,
        anchor_selection_status=anchor_selection_status,
        derivation_status=derivation_status,
        grounded_numeric_claim_count=grounded_numeric_claim_count,
    )


def _run_source_owned_stages(
    *,
    manifest: object,
    policy: ContentPolicy,
    model: str,
    decoder: Step2DecoderConfig,
    transport: SourceOwnedLiveProviderTransport | None,
    diagnostic_sink: SourceOwnedLiveDiagnosticSink | None,
) -> SourceOwnedLiveSinglePdfPilotSummary:
    pages = tuple(getattr(manifest, "pages", ()))
    source = getattr(manifest, "source", None)
    document_sha256 = getattr(source, "sha256", "")
    evidence_unit_count = sum(len(page.evidence_units) for page in pages)
    unit = _select_local_unit(pages, policy=policy)
    if unit is None:
        return _summary(
            document_sha256=document_sha256,
            pages=len(pages),
            evidence_units=evidence_unit_count,
        )

    anchor = _make_anchor(unit, policy=policy)
    request = build_source_owned_provider_request(
        model=model,
        model_input=_evidence_model_input(
            document_sha256=document_sha256,
            anchor=anchor,
        ),
        decoder=decoder,
    )
    dispatched: SourceOwnedLiveProviderDispatchResult = dispatch_source_owned_live_provider_request(
        request=request,
        transport=transport,
    )
    if dispatched.status != "succeeded" or dispatched.response is None:
        if diagnostic_sink is not None:
            diagnostic_sink(dispatched)
        return _summary(
            document_sha256=document_sha256,
            pages=len(pages),
            evidence_units=evidence_unit_count,
            provider_call_count=dispatched.provider_call_count,
        )

    response: Step2AProductionSourceOwnedArbitraryPDFResultPayload = dispatched.response
    proposals = source_owned_extract_document_proposals(response=response)
    if not proposals:
        return _summary(
            document_sha256=document_sha256,
            pages=len(pages),
            evidence_units=evidence_unit_count,
            provider_call_count=dispatched.provider_call_count,
            response_validation_status="validated",
        )

    proposal = proposals[0]
    evidence_spans = {anchor.anchor_span_id: anchor}
    selection = source_owned_select_anchor_spans(
        proposal=proposal,
        candidate_anchor_spans=evidence_spans,
    )
    derivation = source_owned_derive_atomic_numeric_claims(
        proposal=proposal,
        evidence_spans=evidence_spans,
    )
    fact = source_owned_assemble_canonical_fact(
        proposal=proposal,
        anchor_selection=selection,
        derivation_result=derivation,
    )
    candidate = source_owned_fact_to_card_candidate(fact)
    admission = source_owned_admit_card_candidate(candidate)
    bridge = source_owned_bridge_to_knowledge_card(candidate)
    materialization = source_owned_materialize_knowledge_card(
        SourceOwnedKnowledgeCardMaterializationInput(
            bridge_result=bridge,
            candidate=candidate,
            canonical_fact=fact,
            evidence_refs=(_evidence_ref(unit),),
        )
    )
    # Keep the admission call explicit in this wiring layer.  The bridge also
    # re-checks admission, but a non-admitted candidate must never be counted
    # as a materialized card here.
    if admission.status != "admitted":
        materialization_status: MaterializationStatus = "failed_closed"
    else:
        materialization_status = materialization.status
    return _summary(
        document_sha256=document_sha256,
        pages=len(pages),
        evidence_units=evidence_unit_count,
        provider_call_count=dispatched.provider_call_count,
        response_validation_status="validated",
        proposal_count=len(proposals),
        anchor_selection_status=selection.anchor_selection_status,
        derivation_status=derivation.numeric_derivation_status,
        grounded_numeric_claim_count=len(derivation.grounded_numeric_claims),
        materialization_status=materialization_status,
    )


def run_source_owned_live_single_pdf_pilot(
    pdf_path: Path,
    *,
    transport: SourceOwnedLiveProviderTransport | None,
    model: str = DEFAULT_MODEL,
    decoder: Step2DecoderConfig | None = None,
    diagnostic_sink: SourceOwnedLiveDiagnosticSink | None = None,
    output_dir: Path | None = None,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    policy_path: Path = DEFAULT_POLICY_PATH,
) -> SourceOwnedLiveSinglePdfPilotSummary:
    """Run one PDF through the source-owned live boundary only.

    ``transport`` is explicit dependency injection.  Omitting it fails closed
    before a provider call; the function never constructs a legacy adapter or
    a fallback transport.
    """

    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise ValueError("source-owned live pilot requires an existing PDF path")
    if not model.strip():
        raise ValueError("source-owned live pilot requires a model")

    document_sha256 = _sha256_file(pdf_path)
    effective_decoder = decoder or Step2DecoderConfig()
    target = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_ROOT / pdf_path.stem
    ingested = _ingest(
        pdf_path=pdf_path,
        target=target,
        profile_path=profile_path,
        policy_path=policy_path,
    )
    if ingested is None:
        summary = _summary(
            document_sha256=document_sha256,
            pages=0,
            evidence_units=0,
        )
        _write_summary(target, summary)
        return summary

    manifest, policy = ingested
    try:
        summary = _run_source_owned_stages(
            manifest=manifest,
            policy=policy,
            model=model,
            decoder=effective_decoder,
            transport=transport,
            diagnostic_sink=diagnostic_sink,
        )
    except Exception:
        # Do not expose stage input, provider content, or exception text.  The
        # typed result is the only persisted output and stays fail-closed.
        summary = _failed_after_ingestion(manifest=manifest, provider_call_count=0)
    _write_summary(target, summary)
    return summary


def _operator_failure_summary(
    *,
    pdf_path: Path,
    output_dir: Path | None,
) -> SourceOwnedLiveSinglePdfPilotSummary:
    document_sha256 = _sha256_file(pdf_path) if pdf_path.is_file() else ""
    target = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_ROOT / pdf_path.stem
    summary = _summary(document_sha256=document_sha256, pages=0, evidence_units=0)
    _write_summary(target, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        runtime_env = _load_source_owned_live_runtime_env(args.env_file)
        model = _resolve_source_owned_live_model(args.model, runtime_env)
        decoder = _source_owned_decoder_config_from_env(runtime_env)
        transport = _build_default_source_owned_live_transport(env=runtime_env)
    except SourceOwnedLiveOperatorConfigurationError as exc:
        summary = _operator_failure_summary(
            pdf_path=args.pdf_path,
            output_dir=args.output_dir,
        )
        print(summary.model_dump_json())
        print(
            f"stage=operator safe_error_category={exc.safe_error_category}",
            file=sys.stderr,
        )
        return 2

    summary = run_source_owned_live_single_pdf_pilot(
        args.pdf_path,
        transport=transport,
        model=model,
        decoder=decoder,
        diagnostic_sink=_write_safe_dispatch_diagnostic,
        output_dir=args.output_dir,
    )
    print(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
