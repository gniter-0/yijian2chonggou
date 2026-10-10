"""Document-level Step 2A extraction and grounded Fact/Card entry point."""
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from ..evidence.schema import DocumentEvidenceManifest, EvidenceKind, PageEvidence
from ..materials.schema import ContentPolicy, PolicyDisposition

from .contracts import (
    Step2ARequest,
    Step2AFactProposal,
    Step2AExtractionPath,
    Step2AProductionSourceOwnedArbitraryPDFResultPayload,
    Step2DecoderConfig,
    Step2ResultState,
    Step2RequestIdentity,
    SourceOwnedCanonicalProposal,
    build_step2a_request,
    canonicalize_source_owned_model_proposals,
    select_step2a_extraction_path,
)
from .evidence_to_fact_v1 import GroundedFactCard, ground_step2a_proposals
from .llm_adapter import Step2OpenAIAdapter
from .llm_records import Step2AExecutionRecord
from .projection import build_step2a_semantic_projection
from .fact_policy import decide_policy


@dataclass(frozen=True)
class DocumentExtractionResult:
    manifest: DocumentEvidenceManifest
    requests: tuple[Step2ARequest, ...]
    records: tuple[Step2AExecutionRecord, ...]
    proposals: tuple[Step2AFactProposal, ...]


@dataclass(frozen=True)
class DocumentFactPipelineResult:
    extraction: DocumentExtractionResult
    grounded: tuple[GroundedFactCard, ...]

    @property
    def facts(self):
        return tuple(item.fact for item in self.grounded)

    @property
    def card_plans(self):
        return tuple(item.card_plan for item in self.grounded)

    @property
    def cards(self):
        return tuple(item.card for item in self.grounded)


def select_document_extraction_path(identity: Step2RequestIdentity) -> Step2AExtractionPath:
    """Select the document extraction lane from the immutable Step 2A identity."""

    return select_step2a_extraction_path(identity)


def source_owned_extract_document_proposals(
    *, response: Step2AProductionSourceOwnedArbitraryPDFResultPayload
) -> tuple[SourceOwnedCanonicalProposal, ...]:
    """Convert an already schema-validated source-owned payload at the seam.

    This entry point intentionally stops before numeric derivation, table
    occurrence derivation, grounding, or Evidence mutation.  The response
    type guard prevents a legacy V10/V21/V23 payload from entering this lane.
    """

    if not isinstance(response, Step2AProductionSourceOwnedArbitraryPDFResultPayload):
        raise ValueError(
            "source-owned extraction requires the production arbitrary-PDF response payload"
        )
    return canonicalize_source_owned_model_proposals(response.proposals)


_DIRECT_OR_TABLE_KINDS = frozenset(
    {
        EvidenceKind.HEADING,
        EvidenceKind.PARAGRAPH,
        EvidenceKind.LIST_GROUP,
        EvidenceKind.LIST_ITEM,
        EvidenceKind.FORMULA,
        EvidenceKind.CAPTION,
        EvidenceKind.TABLE,
        EvidenceKind.TABLE_ROW,
        EvidenceKind.TABLE_CELL,
    }
)


def _page_has_extractable_input(*, page: PageEvidence, policy: ContentPolicy) -> bool:
    return any(
        unit.kind in _DIRECT_OR_TABLE_KINDS
        and (
            bool(unit.canonical_text)
            or unit.kind in {EvidenceKind.TABLE, EvidenceKind.TABLE_ROW, EvidenceKind.TABLE_CELL}
        )
        and decide_policy(unit=unit, policy=policy).disposition == PolicyDisposition.INCLUDED
        for unit in page.evidence_units
    )


@contextmanager
def _page_source_paths(
    *,
    manifest: DocumentEvidenceManifest,
    supplied: Mapping[int, Path] | None,
) -> Iterator[Mapping[int, Path]]:
    page_numbers = {page.page_num for page in manifest.pages}
    if supplied is not None:
        provided_paths = {page_num: Path(path) for page_num, path in supplied.items()}
        if page_numbers - set(provided_paths):
            raise ValueError("page manifest paths must cover every document page")
        yield provided_paths
        return

    # The projection contract is intentionally file-bound.  For callers that
    # already hold a validated in-memory DocumentEvidenceManifest, these are
    # ephemeral page snapshots, not a new persisted evidence format.
    with TemporaryDirectory(prefix="examcard-step2a-") as temporary:
        root = Path(temporary)
        temporary_paths: dict[int, Path] = {}
        for page in manifest.pages:
            path = root / f"page_{page.page_num:04d}.json"
            path.write_text(
                json.dumps(page.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary_paths[page.page_num] = path
        yield temporary_paths


def iter_document_step2a_requests(
    *,
    manifest: DocumentEvidenceManifest,
    content_policy: ContentPolicy,
    model: str,
    material_profile_id: str | None = None,
    sample_id: str | None = None,
    page_manifest_paths: Mapping[int, Path] | None = None,
    source_manifest_references: Mapping[int, str] | None = None,
    decoder: Step2DecoderConfig | None = None,
    prompt_template: str | None = None,
) -> Iterator[Step2ARequest]:
    """Yield the immutable Step 2A request for each eligible page, in page order.

    This is the single request-building path: ``extract_document_proposals``
    consumes it for provider execution, and offline tooling (for example the
    agent file-exchange export) consumes it without executing anything.
    """

    profile_id = material_profile_id or manifest.material_profile_id
    if profile_id != manifest.material_profile_id:
        raise ValueError("material profile does not match the Evidence manifest")
    stable_sample_id = sample_id or manifest.document_id.removeprefix("doc:")
    with _page_source_paths(manifest=manifest, supplied=page_manifest_paths) as page_paths:
        for page in manifest.pages:
            if not _page_has_extractable_input(page=page, policy=content_policy):
                continue
            reference = (
                source_manifest_references.get(page.page_num)
                if source_manifest_references is not None
                else None
            ) or f"manifest:{manifest.manifest_id}:page:{page.page_num}"
            built = build_step2a_semantic_projection(
                sample_id=stable_sample_id,
                page=page,
                source_manifest_path=page_paths[page.page_num],
                source_manifest_reference=reference,
                content_policy=content_policy,
            )
            yield build_step2a_request(
                sample_id=stable_sample_id,
                material_profile_id=profile_id,
                projection=built.projection,
                sections=(built.section,),
                model=model,
                decoder=decoder,
                prompt_template=prompt_template,
            )


def extract_document_proposals(
    *,
    manifest: DocumentEvidenceManifest,
    content_policy: ContentPolicy,
    adapter: Step2OpenAIAdapter,
    model: str,
    material_profile_id: str | None = None,
    sample_id: str | None = None,
    page_manifest_paths: Mapping[int, Path] | None = None,
    source_manifest_references: Mapping[int, str] | None = None,
    decoder: Step2DecoderConfig | None = None,
    prompt_template: str | None = None,
) -> DocumentExtractionResult:
    """Run the existing typed Step2A adapter once for each eligible page."""

    requests: list[Step2ARequest] = []
    records: list[Step2AExecutionRecord] = []
    proposals: list[Step2AFactProposal] = []
    for request in iter_document_step2a_requests(
        manifest=manifest,
        content_policy=content_policy,
        model=model,
        material_profile_id=material_profile_id,
        sample_id=sample_id,
        page_manifest_paths=page_manifest_paths,
        source_manifest_references=source_manifest_references,
        decoder=decoder,
        prompt_template=prompt_template,
    ):
        record = adapter.complete_step2a(request)
        if record.response.state != Step2ResultState.SUCCEEDED:
            state = record.response.failure.state.value if record.response.failure is not None else "failed"
            raise ValueError(f"Step 2A extraction failed for page {request.projection.page_num}: {state}")
        requests.append(request)
        records.append(record)
        proposals.extend(record.response.proposals)
    return DocumentExtractionResult(
        manifest=manifest,
        requests=tuple(requests),
        records=tuple(records),
        proposals=tuple(proposals),
    )


def run_document_fact_pipeline(
    *,
    manifest: DocumentEvidenceManifest,
    content_policy: ContentPolicy,
    adapter: Step2OpenAIAdapter,
    model: str,
    material_profile_id: str | None = None,
    sample_id: str | None = None,
    page_manifest_paths: Mapping[int, Path] | None = None,
    source_manifest_references: Mapping[int, str] | None = None,
    decoder: Step2DecoderConfig | None = None,
    prompt_template: str | None = None,
) -> DocumentFactPipelineResult:
    """Extract typed proposals, then ground them into Facts and Cards."""

    extraction = extract_document_proposals(
        manifest=manifest,
        content_policy=content_policy,
        adapter=adapter,
        model=model,
        material_profile_id=material_profile_id,
        sample_id=sample_id,
        page_manifest_paths=page_manifest_paths,
        source_manifest_references=source_manifest_references,
        decoder=decoder,
        prompt_template=prompt_template,
    )
    grounded = ground_step2a_proposals(
        manifest=manifest,
        proposals=extraction.proposals,
        content_policy=content_policy,
        material_profile_id=material_profile_id,
        sample_id=sample_id,
    )
    return DocumentFactPipelineResult(extraction=extraction, grounded=grounded)


__all__ = [
    "DocumentExtractionResult",
    "DocumentFactPipelineResult",
    "select_document_extraction_path",
    "source_owned_extract_document_proposals",
    "iter_document_step2a_requests",
    "extract_document_proposals",
    "run_document_fact_pipeline",
]
