"""Agent-as-model Step 2 lane: export page work packets, check, then dry-run.

This CLI lets a coding agent (or a human operator) play the Step 2A model by
writing one JSON response file per page.  It never talks to a provider:

* ``export`` ingests the PDF through the normal Evidence engine once, freezes
  the manifest (Evidence carries timestamps, so a fresh ingest would change
  every request id), derives the
  exact Step 2A requests the runner would derive for the
  ``agent-file:<label>`` model, and writes one work packet per request
  (model-visible input, prompt, response schema, worksheet, page image and a
  response template with the binding fields prefilled).
* ``check`` replays every response through the unmodified
  ``Step2OpenAIAdapter`` in a throwaway storage root, then dry-runs the full
  runner in another throwaway root.  Nothing durable is written.
* ``run`` repeats the check and only then dry-runs ``run_exam_project_v1``
  with a durable adapter storage root keyed by the digest of the response set,
  so editing a response can never be shadowed by a cached record.  It also
  exports one image work packet per card under ``agent_images/``.
* ``images`` repeats ``run`` and, once the agent has written every
  ``agent_images/responses/prompt-<sha256>.png``, executes the runner for real
  (``dry_run=False``) with ``AgentFileImageCallerV1``: every PNG still passes the
  unchanged image envelope gate and is written to ``result/images/``.

Provenance stays honest: the request identity carries the ``agent-file:``
model label, completions carry ``x-examcard-transport: agent_file_v1``, agent
images carry zero usage and ``created=0`` (no provider was called) and no
response is ever treated as a review or an approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pymupdf
from pydantic import ValidationError

NEW_ROOT = Path(__file__).resolve().parents[1]
if str(NEW_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.evidence.ingest import EvidenceEngine  # noqa: E402
from examcard.evidence.schema import DocumentEvidenceManifest  # noqa: E402
from examcard.knowledge.agent_file_transport import (  # noqa: E402
    AGENT_STEP2_MODEL_PREFIX,
    AGENT_STEP2_RESPONSE_SCHEMA_VERSION,
    AGENT_STEP2_TRANSPORT_ID,
    AgentFileStep2Transport,
    AgentStep2ResponseBindingError,
    AgentStep2ResponseMissing,
    agent_step2_model_payload,
    agent_step2_response_path,
    load_agent_step2_response,
    verify_agent_step2_response_binding,
)
from examcard.compilers.step4_knowledge_card_bridge_v1 import build_knowledge_card_image_request_v1  # noqa: E402
from examcard.knowledge.contracts import (  # noqa: E402
    Step2AFactProposal,
    Step2ARequest,
    Step2DecoderConfig,
    Step2ResponseFormat,
    Step2ResultState,
    default_step2_prompt_template,
    step2a_result_payload_model,
)
from examcard.knowledge.evidence_to_fact_v1 import ground_step2a_proposals  # noqa: E402
from examcard.knowledge.extraction_v1 import iter_document_step2a_requests, run_document_fact_pipeline  # noqa: E402
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter  # noqa: E402
from examcard.materials.catalog import load_content_policy, load_material_profile  # noqa: E402
from examcard.pipeline.exam_project_ai_runtime_v1 import (  # noqa: E402
    build_exam_project_dependencies_v1,
)
from examcard.pipeline.exam_project_runner_v1 import (  # noqa: E402
    ExamProjectRunError,
    ExamProjectRunResultV1,
    run_exam_project_v1,
)
from examcard.projects.exam_subject_profile_v1 import ExamSubjectProfileV1  # noqa: E402
from examcard.providers.agent_file_image_caller_v1 import (  # noqa: E402
    AGENT_IMAGE_TRANSPORT_ID,
    AgentFileImageCallerV1,
    agent_image_prompt_sha256,
    agent_image_response_path,
)
from examcard.providers.base import sanitized_error_summary  # noqa: E402
from examcard.providers.envelope_profiles import build_image_profile  # noqa: E402

__all__ = [
    "AGENT_IMAGE_QUEUE_VERSION",
    "AGENT_STEP2_QUEUE_VERSION",
    "AGENT_STEP2_RUN_SUMMARY_VERSION",
    "DEFAULT_AGENT_MODEL",
    "AgentStep2Workspace",
    "PageCheck",
    "PreparedAgentDocument",
    "agent_decoder_config",
    "build_agent_profile",
    "FrozenEvidenceMismatch",
    "check_agent_responses",
    "export_agent_packets",
    "freeze_agent_evidence",
    "main",
    "prepare_agent_document",
    "response_set_digest",
    "export_image_packets",
    "run_agent_document",
    "run_agent_images",
]

DEFAULT_AGENT_MODEL: Final = f"{AGENT_STEP2_MODEL_PREFIX}pdf-page-review"
AGENT_STEP2_QUEUE_VERSION: Final = "examcard_agent_step2_queue_v1"
AGENT_STEP2_RUN_SUMMARY_VERSION: Final = "examcard_agent_step2_run_summary_v1"
AGENT_IMAGE_QUEUE_VERSION: Final = "examcard_agent_image_queue_v1"
_DEFAULT_MATERIAL_PROFILE: Final = NEW_ROOT / "configs" / "v2" / "material_profiles" / "yijian_gonglu_2026.yaml"
_DEFAULT_CONTENT_POLICY: Final = NEW_ROOT / "configs" / "v2" / "content_policies" / "core_only_v1.yaml"
_PAGE_IMAGE_DPI: Final = 150
_TEMPLATE_AGENT_LABEL: Final = "FILL-ME"
_HASH_SEED_ENV: Final = "PYTHONHASHSEED"


# --------------------------------------------------------------------------- layout


@dataclass(frozen=True, slots=True)
class AgentStep2Workspace:
    """All paths of one agent run; everything lives under ``run_root``."""

    run_root: Path

    @property
    def agent_root(self) -> Path:
        return self.run_root / "agent_step2"

    @property
    def requests_dir(self) -> Path:
        return self.agent_root / "requests"

    @property
    def responses_dir(self) -> Path:
        return self.agent_root / "responses"

    @property
    def queue_path(self) -> Path:
        return self.agent_root / "queue.json"

    @property
    def manifest_path(self) -> Path:
        return self.agent_root / "evidence_manifest.json"

    @property
    def evidence_report_path(self) -> Path:
        return self.agent_root / "evidence_report.json"

    @property
    def evidence_cache(self) -> Path:
        return self.run_root / "evidence-cache"

    @property
    def result_dir(self) -> Path:
        return self.run_root / "result"

    def step2_storage(self, digest: str) -> Path:
        return self.run_root / "step2" / digest[:16]

    @property
    def image_root(self) -> Path:
        return self.run_root / "agent_images"

    @property
    def image_requests_dir(self) -> Path:
        return self.image_root / "requests"

    @property
    def image_responses_dir(self) -> Path:
        return self.image_root / "responses"

    @property
    def image_queue_path(self) -> Path:
        return self.image_root / "queue.json"

    def packet_dir(self, request: Step2ARequest) -> Path:
        return self.requests_dir / f"p{request.projection.page_num:04d}"

    def response_path(self, request: Step2ARequest) -> Path:
        return agent_step2_response_path(self.responses_dir, request.identity.request_id)


# --------------------------------------------------------------------------- composition


def build_agent_profile(
    *,
    model: str = DEFAULT_AGENT_MODEL,
    material_profile_path: Path = _DEFAULT_MATERIAL_PROFILE,
    content_policy_path: Path = _DEFAULT_CONTENT_POLICY,
) -> ExamSubjectProfileV1:
    """Same profile as the API pilot except for the honest agent model label."""

    if not model.startswith(AGENT_STEP2_MODEL_PREFIX) or len(model) <= len(AGENT_STEP2_MODEL_PREFIX):
        raise ValueError("agent Step 2 model label must look like 'agent-file:<label>'")
    return ExamSubjectProfileV1(
        material_profile=load_material_profile(material_profile_path),
        content_policy=load_content_policy(content_policy_path),
        step2_model=model,
        image_profile=build_image_profile(),
    )


def agent_decoder_config() -> Step2DecoderConfig:
    return Step2DecoderConfig(response_format=Step2ResponseFormat.JSON_OBJECT)


class _PrecomputedEvidence:
    """Evidence ingestor that hands the runner the manifest we already built."""

    def __init__(self, manifest: DocumentEvidenceManifest, report: dict[str, object]) -> None:
        self._manifest = manifest
        self._report = report

    def ingest_document(
        self,
        pdf_path: Path,
        *,
        source_relative_path: str | None = None,
        output_dir: Path | None = None,
    ) -> tuple[DocumentEvidenceManifest, dict[str, object]]:
        del pdf_path, source_relative_path, output_dir
        return self._manifest, dict(self._report)


@dataclass(frozen=True, slots=True)
class PreparedAgentDocument:
    pdf_path: Path
    profile: ExamSubjectProfileV1
    manifest: DocumentEvidenceManifest
    evidence_report: dict[str, object]
    requests: tuple[Step2ARequest, ...]
    source_relative_path: str | None
    sample_id: str | None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class FrozenEvidenceMismatch(ValueError):
    """The frozen manifest does not belong to the PDF given on the command line."""


def freeze_agent_evidence(
    *,
    pdf_path: Path,
    workspace: AgentStep2Workspace,
    profile: ExamSubjectProfileV1,
    source_relative_path: str | None = None,
    evidence_engine: Any | None = None,
) -> None:
    """Ingest once and freeze the manifest.

    Evidence carries ``generated_at_utc`` timestamps, so every fresh ingest has
    a new manifest hash and therefore new Step 2A request ids.  Freezing the
    manifest is what lets export, check and run (separate processes, possibly
    days apart) agree on the exact same requests.
    """

    engine = evidence_engine or EvidenceEngine(
        profile=profile.material_profile,
        cache_root=workspace.evidence_cache,
        config=profile.evidence_config,
    )
    manifest, report = engine.ingest_document(
        Path(pdf_path),
        source_relative_path=source_relative_path,
        output_dir=None,
    )
    _write_text(workspace.manifest_path, manifest.model_dump_json())
    _write_json(workspace.evidence_report_path, dict(report))


def prepare_agent_document(
    *,
    pdf_path: Path,
    workspace: AgentStep2Workspace,
    profile: ExamSubjectProfileV1,
    source_relative_path: str | None = None,
    sample_id: str | None = None,
) -> PreparedAgentDocument:
    """Derive exactly the Step 2A requests the runner will issue from the frozen manifest."""

    manifest = DocumentEvidenceManifest.model_validate_json(workspace.manifest_path.read_text(encoding="utf-8"))
    if manifest.source.sha256 != _sha256_file(pdf_path):
        raise FrozenEvidenceMismatch("frozen evidence manifest was built from a different PDF")
    report = json.loads(workspace.evidence_report_path.read_text(encoding="utf-8"))
    requests = tuple(
        iter_document_step2a_requests(
            manifest=manifest,
            content_policy=profile.content_policy,
            model=profile.step2_model,
            material_profile_id=profile.material_profile.profile_id,
            sample_id=sample_id,
            decoder=agent_decoder_config(),
        )
    )
    return PreparedAgentDocument(
        pdf_path=Path(pdf_path),
        profile=profile,
        manifest=manifest,
        evidence_report=dict(report),
        requests=requests,
        source_relative_path=source_relative_path,
        sample_id=sample_id,
    )


def _prompt(request: Step2ARequest) -> str:
    return default_step2_prompt_template(
        request.identity.stage,
        request_version=request.identity.request_version,
    )


def _response_schema(request: Step2ARequest) -> dict[str, Any]:
    return step2a_result_payload_model(request.identity.request_version).model_json_schema()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------- export


def _response_template(request: Step2ARequest) -> dict[str, Any]:
    identity = request.identity
    return {
        "schema_version": AGENT_STEP2_RESPONSE_SCHEMA_VERSION,
        "request_id": identity.request_id,
        "model": identity.model,
        "prompt_sha256": identity.prompt_sha256,
        "response_schema_sha256": identity.response_schema_sha256,
        "projection_sha256": identity.projection_sha256,
        "author": {
            "agent_label": _TEMPLATE_AGENT_LABEL,
            "page_image_reviewed": False,
            "notes": "",
        },
        "payload": {"proposals": []},
    }


_GROUNDING_RULES: Final = (
    "## Grounding rules (checked after the Step 2A contract)",
    "",
    "Grounding derives `fact_type`/`completeness_mode` from ALL selected Evidence (primary and secondary)",
    "and rejects any proposal whose declared pair differs. First matching rule wins:",
    "",
    "1. any figure/diagram node/diagram edge -> `diagram_relation` / `full_diagram_subgraph`",
    "2. formula Evidence or a `=` in the text -> `formula` / `complete_formula`",
    "3. any table/table row/table cell -> `composition` / `full_table_row` (select every cell span of the table).",
    "   Exception: a `threshold`/`condition` proposal with mode `atomic` whose primary and secondary spans are",
    "   all table Evidence keeps its type - put the rule cell in primary and header cells in secondary.",
    "4. every unit is a list item -> `enumeration` / `all_items` (do not add the paragraph lead-in line)",
    "5. a constraint term (不得 不应 严禁 除外 至少 不少于 不超过) -> `exception` / `atomic`",
    "6. a digit -> `threshold` / `atomic`; otherwise `definition` / `atomic`",
    "",
    "Also: `proposal_key` must be unique across the whole document (prefix the page, e.g. `proposal:p3-...`);",
    "a table cell may be primary in one composition and in several atomic proposals, any other shared",
    "primary Evidence is rejected. `check` names each rejected proposal when the runner fails.",
)


def _span_lines(spans: Sequence[dict[str, Any]], indent: str) -> Iterator[str]:
    for span in spans:
        text = str(span.get("text", "")).replace("\n", " ")
        yield f"{indent}- `{span.get('anchor_span_id')}` {text}"


def _worksheet(request: Step2ARequest, model_input: dict[str, Any]) -> str:
    identity = request.identity
    lines = [
        f"# Step 2A worksheet - page {request.projection.page_num}",
        "",
        "Authoritative input is `model_input.json`; this file is a readable rendering of it.",
        "Rules are in `prompt.md`; the payload must validate against `response_schema.json`.",
        "Copy `response_template.json` to the path in `request_identity.json` -> `response_path`,",
        "fill `author` honestly and put the proposals under `payload`.",
        "",
        f"- request_id: `{identity.request_id}`",
        f"- request_version: `{identity.request_version}`",
        f"- model: `{identity.model}`",
        "",
        *_GROUNDING_RULES,
        "",
    ]
    heading_only = model_input.get("heading_only_allowed_anchor_span_ids") or []
    if heading_only:
        lines.append("Heading-only spans allowed: " + ", ".join(f"`{item}`" for item in heading_only))
        lines.append("")
    for record in model_input.get("continuation_records") or []:
        lines.append(
            f"Continuation `{record.get('continuation_id')}` state={record.get('state')} "
            f"adjacent_page={record.get('required_adjacent_page_num')} spans={record.get('anchor_span_ids')}"
        )
    for section in model_input.get("sections") or []:
        lines.append(f"## Section `{section.get('section_id')}` (page {section.get('page_num')})")
        lines.append("")
        for item in section.get("text_evidence") or []:
            tag = " (context only)" if item.get("context_only") else ""
            lines.append(f"- {item.get('kind')}{tag}")
            lines.extend(_span_lines(item.get("anchor_spans") or [], "  "))
        for table in section.get("tables") or []:
            lines.append("")
            lines.append(
                f"### Table `{table.get('table_evidence_id')}` "
                f"{table.get('row_count')}x{table.get('column_count')}"
            )
            for requirement in table.get("atomic_subfact_requirements") or []:
                lines.append(
                    f"- REQUIRED `{requirement.get('requirement_id')}` "
                    f"{requirement.get('expected_fact_type')} on `{requirement.get('required_anchor_span_id')}`: "
                    f"{requirement.get('required_assertion')}"
                )
            for row in table.get("rows") or []:
                lines.append(f"- row {row.get('row_index')}")
                for cell in row.get("cells") or []:
                    lines.append(f"  - cell ({cell.get('row_index')},{cell.get('column_index')})")
                    lines.extend(_span_lines(cell.get("anchor_spans") or [], "    "))
        for diagram in section.get("controlled_diagrams") or []:
            lines.append("")
            lines.append(f"### Diagram `{diagram.get('review_id')}` {diagram.get('title')}")
            for node in diagram.get("nodes") or []:
                lines.append(f"- node `{node.get('node_id')}` {node.get('label')} spans={node.get('anchor_span_ids')}")
            for edge in diagram.get("edges") or []:
                lines.append(
                    f"- edge {edge.get('source_node_id')} -> {edge.get('target_node_id')} "
                    f"{edge.get('relation')} spans={edge.get('anchor_span_ids')}"
                )
        lines.append("")
    return "\n".join(lines)


def _render_page_png(pdf_path: Path, page_num: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as document:
        document[page_num - 1].get_pixmap(dpi=_PAGE_IMAGE_DPI, alpha=False).save(path)


def export_agent_packets(
    prepared: PreparedAgentDocument,
    workspace: AgentStep2Workspace,
    *,
    render_pages: bool = True,
) -> dict[str, Any]:
    """Write one work packet per request plus ``queue.json``; never touches responses."""

    workspace.responses_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for request in prepared.requests:
        packet = workspace.packet_dir(request)
        model_input = agent_step2_model_payload(request)
        response_path = workspace.response_path(request)
        _write_json(packet / "model_input.json", model_input)
        _write_text(packet / "prompt.md", _prompt(request))
        _write_json(packet / "response_schema.json", _response_schema(request))
        _write_json(
            packet / "request_identity.json",
            {
                "identity": request.identity.model_dump(mode="json"),
                "page_num": request.projection.page_num,
                "response_path": response_path.relative_to(workspace.run_root).as_posix(),
            },
        )
        _write_json(packet / "response_template.json", _response_template(request))
        _write_text(packet / "worksheet.md", _worksheet(request, model_input))
        if render_pages:
            _render_page_png(prepared.pdf_path, request.projection.page_num, packet / "page.png")
        entries.append(
            {
                "page_num": request.projection.page_num,
                "request_id": request.identity.request_id,
                "packet_dir": packet.relative_to(workspace.run_root).as_posix(),
                "response_path": response_path.relative_to(workspace.run_root).as_posix(),
                "response_present": response_path.is_file(),
            }
        )
    queue = {
        "schema_version": AGENT_STEP2_QUEUE_VERSION,
        "pdf_name": prepared.pdf_path.name,
        "pdf_sha256": prepared.manifest.source.sha256,
        "page_count": prepared.manifest.source.page_count,
        "model": prepared.profile.step2_model,
        "profile_sha256": prepared.profile.canonical_sha256(),
        "source_relative_path": prepared.source_relative_path,
        "sample_id": prepared.sample_id,
        "transport": AGENT_STEP2_TRANSPORT_ID,
        "python_hash_seed": os.environ.get(_HASH_SEED_ENV),
        "requests": entries,
        "orphan_responses": _orphan_responses(prepared, workspace),
    }
    _write_json(workspace.queue_path, queue)
    return queue


def _orphan_responses(prepared: PreparedAgentDocument, workspace: AgentStep2Workspace) -> list[str]:
    expected = {workspace.response_path(request).name for request in prepared.requests}
    if not workspace.responses_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in workspace.responses_dir.glob("*.response.json")
        if path.name not in expected
    )


# --------------------------------------------------------------------------- check


@dataclass(frozen=True, slots=True)
class PageCheck:
    page_num: int
    request_id: str
    status: str
    response_sha256: str | None = None
    proposal_count: int = 0
    detail: tuple[str, ...] = ()
    forensics: dict[str, Any] | None = None
    proposals: tuple[Step2AFactProposal, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    def as_json(self) -> dict[str, Any]:
        return {
            "page_num": self.page_num,
            "request_id": self.request_id,
            "status": self.status,
            "response_sha256": self.response_sha256,
            "proposal_count": self.proposal_count,
            "detail": list(self.detail),
            "forensics": self.forensics,
        }


def _validation_details(error: ValidationError) -> tuple[str, ...]:
    return tuple(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors(include_url=False, include_input=False)
    )


def _check_one(request: Step2ARequest, workspace: AgentStep2Workspace) -> PageCheck:
    page_num = request.projection.page_num
    request_id = request.identity.request_id
    path = workspace.response_path(request)
    try:
        envelope, raw_sha256 = load_agent_step2_response(path)
    except AgentStep2ResponseMissing:
        return PageCheck(page_num, request_id, "missing")
    except ValidationError as error:
        return PageCheck(page_num, request_id, "invalid_envelope", detail=_validation_details(error))
    except ValueError as error:
        return PageCheck(page_num, request_id, "invalid_envelope", detail=(sanitized_error_summary(error),))
    if envelope.author.agent_label == _TEMPLATE_AGENT_LABEL:
        return PageCheck(page_num, request_id, "invalid_envelope", raw_sha256, detail=("author.agent_label is still the template placeholder",))
    try:
        verify_agent_step2_response_binding(
            envelope,
            request=request,
            response_schema=_response_schema(request),
            prompt=_prompt(request),
        )
    except AgentStep2ResponseBindingError as error:
        return PageCheck(page_num, request_id, "binding_mismatch", raw_sha256, detail=(sanitized_error_summary(error),))
    with tempfile.TemporaryDirectory(prefix="agent-step2-check-") as scratch:
        adapter = Step2OpenAIAdapter(
            storage_root=Path(scratch),
            transport=AgentFileStep2Transport(response_root=workspace.responses_dir),
            idempotency_supported=False,
        )
        record = adapter.complete_step2a(request)
    response = record.response
    if response.state == Step2ResultState.SUCCEEDED:
        return PageCheck(
            page_num,
            request_id,
            "accepted",
            raw_sha256,
            proposal_count=len(response.proposals),
            proposals=tuple(response.proposals),
        )
    detail: list[str] = []
    if response.failure is not None:
        detail.append(f"{response.failure.state.value}: {response.failure.message}")
    detail.extend(record.contract_report.errors)
    forensics = (
        record.rejected_response_forensics.model_dump(mode="json")
        if record.rejected_response_forensics is not None
        else None
    )
    return PageCheck(page_num, request_id, "rejected", raw_sha256, detail=tuple(detail), forensics=forensics)


def check_agent_responses(prepared: PreparedAgentDocument, workspace: AgentStep2Workspace) -> tuple[PageCheck, ...]:
    """Contract-check every response in throwaway adapter storage."""

    return tuple(_check_one(request, workspace) for request in prepared.requests)


def response_set_digest(checks: Sequence[PageCheck]) -> str:
    """Digest binding the exact set of response bytes a run consumed."""

    lines = sorted(f"{item.request_id}={item.response_sha256}" for item in checks)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- run


def _dry_run(
    prepared: PreparedAgentDocument,
    workspace: AgentStep2Workspace,
    *,
    storage_root: Path,
    image_caller: AgentFileImageCallerV1 | None = None,
) -> ExamProjectRunResultV1:
    """Run the unchanged runner; it is a dry run unless an agent image caller is given."""

    dependencies = build_exam_project_dependencies_v1(
        storage_root=storage_root,
        transport=AgentFileStep2Transport(response_root=workspace.responses_dir),
        idempotency_supported=False,
        image_caller=image_caller,
        evidence_engine=_PrecomputedEvidence(prepared.manifest, prepared.evidence_report),
    )
    return run_exam_project_v1(
        pdf_path=prepared.pdf_path,
        profile=prepared.profile,
        output_root=workspace.result_dir,
        cache_root=workspace.evidence_cache,
        dependencies=dependencies,
        dry_run=image_caller is None,
        source_relative_path=prepared.source_relative_path,
        sample_id=prepared.sample_id,
        step2_decoder_config=agent_decoder_config(),
    )


def _run_failure(error: ExamProjectRunError) -> dict[str, Any]:
    diagnostic = getattr(error, "diagnostic", None)
    return {"code": error.code, "diagnostic": dict(diagnostic) if diagnostic else None}


def _cards_by_page(result: ExamProjectRunResultV1) -> dict[str, int]:
    counts: dict[str, int] = {}
    for request in result.requests:
        for page in request.source_pages:
            counts[str(page)] = counts.get(str(page), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: int(item[0])))


def diagnose_grounding(prepared: PreparedAgentDocument, checks: Sequence[PageCheck]) -> list[dict[str, Any]]:
    """Name every proposal grounding rejects, in the runner's document order.

    Each proposal is grounded together with the proposals accepted before it, so
    ownership conflicts are attributed to the later proposal and one bad proposal
    does not hide the next.
    """

    accepted: list[Step2AFactProposal] = []
    findings: list[dict[str, Any]] = []
    for check in checks:
        for proposal in check.proposals:
            try:
                ground_step2a_proposals(
                    manifest=prepared.manifest,
                    proposals=(*accepted, proposal),
                    content_policy=prepared.profile.content_policy,
                    material_profile_id=prepared.profile.material_profile.profile_id,
                    sample_id=prepared.sample_id,
                )
            except ValueError as error:
                findings.append(
                    {
                        "page_num": check.page_num,
                        "proposal_key": proposal.proposal_key,
                        "problem": sanitized_error_summary(error),
                    }
                )
            else:
                accepted.append(proposal)
    return findings


def diagnose_cards(prepared: PreparedAgentDocument, workspace: AgentStep2Workspace) -> list[dict[str, Any]]:
    """Name every card the Step 4 bridge refuses to compile."""

    with tempfile.TemporaryDirectory(prefix="agent-step2-diagnose-") as scratch:
        adapter = Step2OpenAIAdapter(
            storage_root=Path(scratch),
            transport=AgentFileStep2Transport(response_root=workspace.responses_dir),
            idempotency_supported=False,
        )
        pipeline = run_document_fact_pipeline(
            manifest=prepared.manifest,
            content_policy=prepared.profile.content_policy,
            adapter=adapter,
            model=prepared.profile.step2_model,
            material_profile_id=prepared.profile.material_profile.profile_id,
            sample_id=prepared.sample_id,
            decoder=agent_decoder_config(),
        )
    findings: list[dict[str, Any]] = []
    for card in pipeline.cards:
        try:
            build_knowledge_card_image_request_v1(card, profile=prepared.profile.image_profile)
        except Exception as error:  # the runner also treats any compile error as card_compilation_failed
            findings.append(
                {
                    "page_nums": list(card.source_pages),
                    "card_title": card.title,
                    "problem": sanitized_error_summary(error),
                }
            )
    return findings


def _diagnose(
    code: str,
    prepared: PreparedAgentDocument,
    workspace: AgentStep2Workspace,
    checks: Sequence[PageCheck],
) -> list[dict[str, Any]] | None:
    try:
        if code == "knowledge_extraction_failed":
            return diagnose_grounding(prepared, checks)
        if code == "card_compilation_failed":
            return diagnose_cards(prepared, workspace)
    except (ValueError, ExamProjectRunError) as error:
        return [{"problem": "diagnosis itself failed: " + sanitized_error_summary(error)}]
    return None


def overlay_warnings(prepared: PreparedAgentDocument, facts: Sequence[Any]) -> list[dict[str, Any]]:
    """List Facts that cite Evidence still sharing glyphs with the page watermark.

    The Evidence layer removes watermark glyphs it can explain geometrically and tags
    the remainder ``overlay_overlap``; such Facts deserve a human look before release.
    """
    tagged = {
        unit.evidence_id: (page.page_num, unit.raw_text)
        for page in prepared.manifest.pages
        for unit in page.evidence_units
        if "overlay_overlap" in unit.risk_tags
    }
    findings: list[dict[str, Any]] = []
    for fact in facts:
        cited = [*fact.primary_evidence_ids, *fact.secondary_evidence_ids]
        cited += [number.evidence_id for number in fact.numbers if number.evidence_id]
        for evidence_id in dict.fromkeys(cited):
            if evidence_id in tagged:
                page_num, text = tagged[evidence_id]
                findings.append(
                    {"fact_label": fact.label, "page_num": page_num, "evidence_id": evidence_id, "evidence_text": text[:60]}
                )
    return findings


def run_agent_document(
    prepared: PreparedAgentDocument,
    workspace: AgentStep2Workspace,
    *,
    durable: bool,
) -> dict[str, Any]:
    """Check every page, then dry-run the runner (throwaway or durable storage)."""

    checks = check_agent_responses(prepared, workspace)
    summary: dict[str, Any] = {
        "schema_version": AGENT_STEP2_RUN_SUMMARY_VERSION,
        "pdf_name": prepared.pdf_path.name,
        "pdf_sha256": prepared.manifest.source.sha256,
        "model": prepared.profile.step2_model,
        "transport": AGENT_STEP2_TRANSPORT_ID,
        "profile_sha256": prepared.profile.canonical_sha256(),
        "human_approved": False,
        "durable": durable,
        "python_hash_seed": os.environ.get(_HASH_SEED_ENV),
        "pages": [item.as_json() for item in checks],
        "status": "pages_not_ready",
    }
    if not checks or not all(item.accepted for item in checks):
        return summary
    digest = response_set_digest(checks)
    summary["response_set_sha256"] = digest
    try:
        if durable:
            storage = workspace.step2_storage(digest)
            summary["step2_storage"] = storage.relative_to(workspace.run_root).as_posix()
            result = _dry_run(prepared, workspace, storage_root=storage)
        else:
            with tempfile.TemporaryDirectory(prefix="agent-step2-run-") as scratch:
                result = _dry_run(prepared, workspace, storage_root=Path(scratch))
    except ExamProjectRunError as error:
        summary["status"] = "runner_failed"
        summary["runner_failure"] = _run_failure(error)
        diagnosis = _diagnose(error.code, prepared, workspace, checks)
        if diagnosis is not None:
            summary["diagnosis"] = diagnosis
        return summary
    summary["status"] = "completed"
    summary["fact_count"] = len(result.fact_pipeline.facts)
    summary["card_count"] = len(result.cards)
    summary["image_request_count"] = len(result.requests)
    summary["cards_by_page"] = _cards_by_page(result)
    summary["overlay_warnings"] = overlay_warnings(prepared, result.fact_pipeline.facts)
    if durable:
        _write_json(workspace.result_dir / "cards.json", [card.model_dump(mode="json") for card in result.cards])
        _write_json(workspace.result_dir / "facts.json", [fact.model_dump(mode="json") for fact in result.fact_pipeline.facts])
        _write_json(workspace.result_dir / "image_requests.json", [request.model_dump(mode="json") for request in result.requests])
        queue = export_image_packets(result, workspace)
        summary["image_packets"] = len(queue["requests"])
        summary["images_missing"] = sum(1 for entry in queue["requests"] if not entry["present"])
    return summary


def export_image_packets(result: ExamProjectRunResultV1, workspace: AgentStep2Workspace) -> dict[str, Any]:
    """Write one image work packet per card and a queue naming the PNG each one needs.

    The PNG name is the sha256 of the exact request prompt (see
    ``AgentFileImageCallerV1``), so a packet can only be answered for the card
    content it was exported from.
    """

    workspace.image_responses_dir.mkdir(parents=True, exist_ok=True)
    first_request = result.projections[0].http_request if result.projections else None
    entries: list[dict[str, Any]] = []
    for request, projection in zip(result.requests, result.projections, strict=True):
        prompt_sha256 = agent_image_prompt_sha256(request.prompt_text)
        response = agent_image_response_path(workspace.image_responses_dir, prompt_sha256)
        name = f"card-{request.raw_id.removeprefix('kp:')}"
        request_json = request.model_dump(mode="json")
        packet = {
            "schema_version": AGENT_IMAGE_QUEUE_VERSION,
            "transport": AGENT_IMAGE_TRANSPORT_ID,
            "response_path": response.relative_to(workspace.run_root).as_posix(),
            "http_request": projection.http_request.model_dump(mode="json"),
            "request": request_json,
        }
        _write_json(workspace.image_requests_dir / f"{name}.json", packet)
        entries.append(
            {
                "raw_id": request.raw_id,
                "title": request.title,
                "presentation": request_json["presentation"],
                "source_pages": list(request.source_pages),
                "prompt_sha256": prompt_sha256,
                "packet": f"requests/{name}.json",
                "response_file": response.name,
                "present": response.is_file(),
            }
        )
    queue: dict[str, Any] = {
        "schema_version": AGENT_IMAGE_QUEUE_VERSION,
        "transport": AGENT_IMAGE_TRANSPORT_ID,
        "size": first_request.size if first_request is not None else None,
        "requests": entries,
    }
    _write_json(workspace.image_queue_path, queue)
    return queue


def run_agent_images(prepared: PreparedAgentDocument, workspace: AgentStep2Workspace) -> dict[str, Any]:
    """``run`` plus a real (non-dry) runner execution fed by agent-written PNGs."""

    summary = run_agent_document(prepared, workspace, durable=True)
    summary["image_transport"] = AGENT_IMAGE_TRANSPORT_ID
    if summary["status"] != "completed":
        return summary
    queue = json.loads(workspace.image_queue_path.read_text(encoding="utf-8"))
    missing = [entry for entry in queue["requests"] if not entry["present"]]
    if missing:
        summary["status"] = "images_missing"
        summary["missing_images"] = [
            {"raw_id": entry["raw_id"], "title": entry["title"], "response_file": entry["response_file"]}
            for entry in missing
        ]
        return summary
    caller = AgentFileImageCallerV1(
        response_root=workspace.image_responses_dir,
        profile=prepared.profile.image_profile,
    )
    try:
        result = _dry_run(
            prepared,
            workspace,
            storage_root=workspace.step2_storage(summary["response_set_sha256"]),
            image_caller=caller,
        )
    except ExamProjectRunError as error:
        summary["status"] = "runner_failed"
        summary["runner_failure"] = _run_failure(error)
        return summary
    summary["status"] = "completed"
    summary["dry_run"] = result.dry_run
    summary["images"] = [
        {
            "raw_id": image.raw_id,
            "source_pages": list(image.source_pages),
            "output_path": image.output_path.relative_to(workspace.run_root).as_posix() if image.output_path else None,
            "image_sha256": image.image_sha256,
        }
        for image in result.images
    ]
    _write_json(
        workspace.result_dir / "images.json",
        {"transport": AGENT_IMAGE_TRANSPORT_ID, "human_approved": False, "images": summary["images"]},
    )
    return summary


# --------------------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent-as-model Step 2A lane (no provider calls).")
    parser.add_argument("command", choices=("export", "check", "run", "images"))
    parser.add_argument("--pdf", required=True, type=Path, help="explicit PDF input path")
    parser.add_argument("--run-root", required=True, type=Path, help="isolated run directory")
    parser.add_argument("--model", default=DEFAULT_AGENT_MODEL, help="agent-file:<label> model label")
    parser.add_argument("--source-relative-path", default=None)
    parser.add_argument("--sample-id", default=None)
    parser.add_argument("--no-page-images", action="store_true", help="export: skip page.png rendering")
    parser.add_argument(
        "--refreeze",
        action="store_true",
        help="export: re-ingest and replace the frozen manifest (invalidates every existing response)",
    )
    return parser


def _warn_on_request_drift(prepared: PreparedAgentDocument, workspace: AgentStep2Workspace) -> None:
    """Warn when the live request ids no longer match the last export.

    Evidence JSON is serialized independently of ``PYTHONHASHSEED`` (sorted
    frozensets), so ids should only move when the frozen manifest, the model
    label, the profile or the contract code changed.
    """

    if not workspace.queue_path.is_file():
        return
    queue = json.loads(workspace.queue_path.read_text(encoding="utf-8"))
    exported = {entry.get("page_num"): entry.get("request_id") for entry in queue.get("requests", [])}
    current = {request.projection.page_num: request.identity.request_id for request in prepared.requests}
    if exported == current:
        return
    drifted = sorted(page for page in exported.keys() | current.keys() if exported.get(page) != current.get(page))
    print(
        "warning: request ids differ from the last export for page(s) "
        + ", ".join(str(page) for page in drifted)
        + "; re-run export and rewrite the affected responses",
        file=sys.stderr,
    )


def _print_checks(summary: dict[str, Any]) -> None:
    for page in summary["pages"]:
        line = f"page {page['page_num']:>3}: {page['status']}"
        if page["status"] == "accepted":
            line += f" ({page['proposal_count']} proposals)"
        print(line)
        for detail in page["detail"]:
            print(f"    - {detail}")


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    pdf = Path(args.pdf).resolve()
    if not pdf.is_file():
        print("agent step2: --pdf must be an existing file", file=sys.stderr)
        return 2
    workspace = AgentStep2Workspace(Path(args.run_root).resolve())
    try:
        profile = build_agent_profile(model=args.model)
    except ValueError as error:
        print(f"agent step2: {sanitized_error_summary(error)}", file=sys.stderr)
        return 2
    if args.command == "export" and (args.refreeze or not workspace.manifest_path.is_file()):
        freeze_agent_evidence(
            pdf_path=pdf,
            workspace=workspace,
            profile=profile,
            source_relative_path=args.source_relative_path,
        )
    if not workspace.manifest_path.is_file():
        print("agent step2: no frozen evidence manifest; run export first", file=sys.stderr)
        return 2
    try:
        prepared = prepare_agent_document(
            pdf_path=pdf,
            workspace=workspace,
            profile=profile,
            source_relative_path=args.source_relative_path,
            sample_id=args.sample_id,
        )
    except FrozenEvidenceMismatch:
        print("agent step2: frozen evidence manifest belongs to a different PDF", file=sys.stderr)
        return 2
    if args.command == "export":
        queue = export_agent_packets(prepared, workspace, render_pages=not args.no_page_images)
        print(f"exported {len(queue['requests'])} request packet(s) to {workspace.requests_dir}")
        print(f"write responses into {workspace.responses_dir}")
        for name in queue["orphan_responses"]:
            print(f"warning: response file matches no current request: {name}")
        return 0
    _warn_on_request_drift(prepared, workspace)
    if args.command == "images":
        summary = run_agent_images(prepared, workspace)
    else:
        summary = run_agent_document(prepared, workspace, durable=args.command == "run")
    _print_checks(summary)
    _write_json(workspace.agent_root / f"last_{args.command}.json", summary)
    for warning in summary.get("overlay_warnings") or []:
        print(
            f"warning: page {warning['page_num']} fact {warning['fact_label']!r} cites Evidence that still "
            f"overlaps the watermark: {warning['evidence_text']!r}"
        )
    if summary["status"] == "completed":
        print(
            f"{args.command}: completed - {summary['fact_count']} fact(s), "
            f"{summary['card_count']} card(s); cards by page {summary['cards_by_page']}"
        )
        if "images" in summary:
            print(f"images: wrote {len(summary['images'])} PNG(s) to {workspace.result_dir / 'images'}")
        elif summary.get("image_packets"):
            print(
                f"images: {summary['image_packets']} packet(s) in {workspace.image_requests_dir}, "
                f"{summary['images_missing']} PNG(s) still missing in {workspace.image_responses_dir}"
            )
        return 0
    if summary["status"] == "images_missing":
        print(f"{args.command}: {len(summary['missing_images'])} agent image(s) missing", file=sys.stderr)
        for entry in summary["missing_images"]:
            print(f"    - {entry['raw_id']} {entry['title']}: {entry['response_file']}", file=sys.stderr)
        return 1
    if summary["status"] == "runner_failed":
        print(f"{args.command}: runner failed - {summary['runner_failure']['code']}", file=sys.stderr)
        for finding in summary.get("diagnosis") or []:
            where = finding.get("proposal_key") or finding.get("card_title") or ""
            page = finding.get("page_num", finding.get("page_nums", ""))
            print(f"    - page {page} {where}: {finding['problem']}", file=sys.stderr)
        if summary.get("diagnosis") == []:
            print("    - diagnosis found no single failing proposal or card", file=sys.stderr)
    else:
        print(f"{args.command}: not every page is accepted yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
