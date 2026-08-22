"""Bounded live V25/V25B validation with draft-only, source-bound records.

The runner deliberately keeps the V25 Step 2A output as a draft.  A successful
Step 2B call consumes only the separately reviewed V25 Fact bridge; this avoids
turning a successful model response into an implicit review promotion.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Final, Mapping, Protocol

import yaml

from ...evidence.golden import load_golden_registry
from ...evidence.schema import PageEvidence
from ...materials.catalog import load_content_policy

from ..diagram.disposition import load_diagram_disposition_sidecar
from ..diagram.sidecar import load_diagram_review_sidecar
from ..learning_fixture import LearningReviewState, load_learning_golden_fixture
from .phase2r12_v25_golden_review import (
    _SOURCE_DISPOSITION_SIDECAR,
    _TARGET_REVIEWED_FIXTURE,
    _TARGET_SEMANTIC_SIDECAR,
)
from .phase2r13_v25_reconciliation import (
    _SOURCE_GROUPING_PATH,
    _TABLE_REPAIR_PATHS,
    _WATERMARK_REVIEW_PATH,
    _bindings_for_sample,
    _builds_for_sample,
    _continuation_binding,
    _fact_summaries,
    _rendered_table,
    _unresolved_watermarks,
)
from . import canonical_step2_sha256, fingerprint_step2_relay, normalized_compare_text
from ..llm import TransportCompletion
from ..quality import (
    assess_diagram_semantics,
    assess_heading_only_fact,
    assess_table_structure,
    assess_title_quality,
    build_teaching_quality_gate,
)
from .v25_generic_contracts import (
    V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION,
    V25_STEP2A_JOURNAL_VERSION,
    V25B_STEP2B_JOURNAL_VERSION,
    V25BStep2BResultPayload,
    V25Step2AResultPayload,
    assess_v25_step2a_response,
    assess_v25b_step2b_response,
    build_v25_step2a_request,
    build_v25b_step2b_request,
    v25_step2a_prompt_template,
    v25_step2a_request_payload,
    v25b_step2b_prompt_template,
    v25b_step2b_request_payload,
)


PHASE: Final = "2R.1.4.0"
NETWORK_CALL_BUDGET = 6
SAMPLE_IDS = ("gonglu_24_table_formula", "gonglu_41_diagram_mixed")
REPORT_NAME = "phase2r14_v25_live_smoke_report.json"
FROZEN_RELATIVE_PATHS = (
    "docs/v2/Phase2R.1.3_V25源派生编组与图示桥接验收报告_20260729.md",
    "docs/v2/artifacts/phase2r13_v25_reconciliation_offline_report_20260729.json",
    "configs/v2/card_grouping_review_sidecars/gonglu_phase2r13_v25_source_grouping.reviewed.yaml",
    "configs/v2/watermark_review_sidecars/gonglu_phase2r13_spatial_watermark.reviewed.yaml",
    "configs/v2/diagram_review_sidecars/gonglu_phase2r12_v25_semantic_reviewed.yaml",
    "scripts/tests/fixtures/v2/learning_golden/gonglu_phase2r12_v25_reviewed.yaml",
)


class V25LiveTransport(Protocol):
    network_call_count: int

    def probe_json_schema(
        self,
        *,
        model: str,
        schema_name: str,
        response_schema: Mapping[str, Any],
        user_content: str,
        max_tokens: int = 400,
    ) -> TransportCompletion: ...

    def complete_payload(
        self,
        *,
        identity: Any,
        model_payload: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        prompt: str,
        use_idempotency_key: bool,
    ) -> TransportCompletion: ...


@dataclass(frozen=True)
class V25LiveSampleContext:
    sample_id: str
    step2a_request: Any
    step2b_request: Any
    fixture_sample: Any
    pages: tuple[PageEvidence, ...]
    diagram_sidecar: Any
    watermark_sidecar: Any
    manifest_hashes: dict[tuple[str, int], str]
    facts_by_key: dict[str, Any]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _storage_name(value: str) -> str:
    """Map an auditable key to a Windows-safe filename without changing the key."""

    return value.replace(":", "_")


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_journal(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _next_journal_cursor(path: Path) -> int:
    """Return the next monotonic cursor without altering an existing journal."""

    if not path.is_file():
        return 1
    highest = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        cursor = event.get("event_cursor")
        if not isinstance(cursor, int) or cursor < 1:
            raise ValueError(f"Phase {PHASE} journal has an invalid event cursor")
        highest = max(highest, cursor)
    return highest + 1


def _safe_error(error: BaseException) -> dict[str, object]:
    status = getattr(error, "status_code", None)
    return {
        "error_type": type(error).__name__,
        "http_status": status if isinstance(status, int) and 100 <= status <= 599 else None,
    }


def freeze_phase2r13_inputs(*, repository_root: Path) -> dict[str, str]:
    root = Path(repository_root).resolve()
    frozen: dict[str, str] = {}
    for relative in FROZEN_RELATIVE_PATHS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"Phase {PHASE} frozen input is missing: {relative}")
        frozen[relative] = _sha256_bytes(path.read_bytes())
    return frozen


def build_live_sample_contexts(
    *,
    repository_root: Path,
    model: str,
    relay_base_url: str,
    manual_reissue_reason: str,
    sample_ids: tuple[str, ...] = SAMPLE_IDS,
) -> tuple[V25LiveSampleContext, ...]:
    """Build selected live requests without mutating reviewed V25 artifacts."""

    root = Path(repository_root).resolve()
    registry = load_golden_registry(root / "scripts/tests/fixtures/v2/golden_samples/gonglu_phase0.yaml")
    policy = load_content_policy(root / "configs/v2/content_policies/core_only_v1.yaml")
    fixture = load_learning_golden_fixture(root / _TARGET_REVIEWED_FIXTURE)
    diagram_path = root / _TARGET_SEMANTIC_SIDECAR
    diagram_sidecar = load_diagram_review_sidecar(diagram_path)
    semantic_sidecar_sha256 = _sha256_bytes(diagram_path.read_bytes())
    disposition = load_diagram_disposition_sidecar(root / _SOURCE_DISPOSITION_SIDECAR)
    continuation = _continuation_binding(repository_root=root)
    repairs_by_page = {key: root / value for key, value in _TABLE_REPAIR_PATHS.items()}
    grouping_sidecar = yaml.safe_load((root / _SOURCE_GROUPING_PATH).read_text(encoding="utf-8"))
    from .phase2r13_v25_reconciliation import V25SourceGroupingReviewSidecar, V25WatermarkReviewSidecar

    grouping = V25SourceGroupingReviewSidecar.model_validate(grouping_sidecar)
    watermarks = V25WatermarkReviewSidecar.model_validate(
        yaml.safe_load((root / _WATERMARK_REVIEW_PATH).read_text(encoding="utf-8"))
    )
    contexts: list[V25LiveSampleContext] = []
    if not sample_ids or len(sample_ids) != len(set(sample_ids)):
        raise ValueError("Phase 2R.1.4 live sample IDs must be non-empty and unique")
    for sample_id in sample_ids:
        sample = next((item for item in registry.samples if item.sample_id == sample_id), None)
        fixture_sample = next((item for item in fixture.samples if item.sample_id == sample_id), None)
        if sample is None or fixture_sample is None:
            raise ValueError(f"Phase {PHASE} sample is missing from the reviewed V25 registry: {sample_id}")
        builds, paths = _builds_for_sample(
            repository_root=root,
            sample=sample,
            policy=policy,
            diagram_sidecar=diagram_sidecar,
            diagram_path=diagram_path,
            disposition=disposition,
            repairs_by_page=repairs_by_page,
        )
        step2a = build_v25_step2a_request(
            sample_id=sample.sample_id,
            material_profile_id=registry.material_profile_id,
            source_document_sha256=builds[0].projection.source_document_sha256,
            source_manifest_sha256s=tuple(item.projection.source_manifest_sha256 for item in builds),
            projection_sha256s=tuple(item.projection.fingerprint_sha256 for item in builds),
            content_policy_id=policy.policy_id,
            content_policy_revision=policy.revision,
            content_policy_fingerprint_sha256=builds[0].projection.content_policy_fingerprint_sha256,
            sections=tuple(item.section for item in builds),
            model=model,
            relay_base_url=relay_base_url,
            manual_reissue_reason=f"{manual_reissue_reason}-step2a-{sample.sample_id}",
            reviewed_continuation_bindings=(continuation,) if sample.sample_id == "gonglu_24_table_formula" else (),
            reviewed_table_repair_ids=tuple(
                item for build in builds for item in build.projection.reviewed_table_repair_ids
            ),
            table_repair_sidecar_sha256s=tuple(
                item for build in builds for item in (build.projection.table_repair_sidecar_sha256,) if item
            ),
        )
        fact_pairs, manifest_by_span = _fact_summaries(
            request=step2a,
            fixture_sample=fixture_sample,
            builds=builds,
            diagram_sidecar=diagram_sidecar,
            semantic_sidecar_sha256=semantic_sidecar_sha256,
        )
        facts_by_key = dict(fact_pairs)
        bindings = _bindings_for_sample(sample_id=sample.sample_id, sidecar=grouping, facts_by_key=facts_by_key)
        step2b = build_v25b_step2b_request(
            sample_id=sample.sample_id,
            material_profile_id=registry.material_profile_id,
            facts=tuple(item for _, item in fact_pairs),
            source_family_bindings=bindings,
            model=model,
            relay_base_url=relay_base_url,
        )
        pages = tuple(PageEvidence.model_validate_json(path.read_bytes()) for path in paths)
        manifest_hashes = {
            (sample.sample_id, build.projection.page_num): build.projection.source_manifest_sha256
            for build in builds
        }
        contexts.append(
            V25LiveSampleContext(
                sample_id=sample.sample_id,
                step2a_request=step2a,
                step2b_request=step2b,
                fixture_sample=fixture_sample,
                pages=pages,
                diagram_sidecar=diagram_sidecar,
                watermark_sidecar=watermarks,
                manifest_hashes=manifest_hashes,
                facts_by_key=facts_by_key,
            )
        )
    identities = [
        item.identity.request_id
        for context in contexts
        for item in (context.step2a_request, context.step2b_request)
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("Phase 2R.1.4 live request identities must be unique")
    return tuple(contexts)


def _learning_fact_gate(context: V25LiveSampleContext) -> dict[str, object]:
    facts = tuple(context.facts_by_key.values())
    reviewed = all(target.review.state == LearningReviewState.REVIEWED for target in context.fixture_sample.facts)
    fact_keys_match = set(context.facts_by_key) == {target.fact_key for target in context.fixture_sample.facts}
    diagram_targets = [target for target in context.fixture_sample.facts if target.fact_type.value == "diagram_relation"]
    diagram_bridges = [fact.semantic_diagram_bridge for fact in facts if fact.semantic_diagram_bridge is not None]
    diagram_bridge_valid = len(diagram_targets) == len(diagram_bridges) and all(
        bridge is not None and bridge.figure_evidence_id in next(
            fact.primary_evidence_ids for fact in facts if fact.semantic_diagram_bridge == bridge
        )
        for bridge in diagram_bridges
    )
    evidence_only = [
        entry
        for entry in context.diagram_sidecar.entries
        if entry.sample_id == context.sample_id
        and entry.review_id not in {bridge.review_id for bridge in diagram_bridges if bridge is not None}
    ]
    evidence_only_excluded = all(
        entry.figure_evidence_id not in {item for fact in facts for item in fact.primary_evidence_ids}
        for entry in evidence_only
    )
    fake = __import__(
        "examcard.knowledge.contracts.v25_generic_contracts", fromlist=["build_v25b_source_fake_response"]
    )
    source_cards = fake.build_v25b_source_fake_response(request=context.step2b_request)
    expected = {
        (tuple(context.facts_by_key[key].fact_id for key in card.primary_fact_keys), card.presentation.value)
        for card in context.fixture_sample.cards
    }
    observed = {(tuple(card.primary_fact_ids), card.presentation.value) for card in source_cards.proposals}
    return {
        "valid": reviewed and fact_keys_match and diagram_bridge_valid and evidence_only_excluded and expected == observed,
        "reviewed_fact_count": len(context.fixture_sample.facts),
        "reviewed_card_count": len(context.fixture_sample.cards),
        "diagram_bridge_count": len(diagram_bridges),
        "evidence_only_diagram_count": len(evidence_only),
        "fact_keys_match": fact_keys_match,
        "diagram_bridge_valid": diagram_bridge_valid,
        "evidence_only_diagram_excluded": evidence_only_excluded,
        "source_grouping_matches_reviewed_card_groups": expected == observed,
    }


def _learning_title_key(value: str) -> str:
    return "".join(char for char in normalized_compare_text(value) if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def assess_v25_response_learning_golden(
    *, context: V25LiveSampleContext, response: V25Step2AResultPayload
) -> dict[str, object]:
    """Compare a live V25 response to reviewed targets without feeding them to the model.

    Source-derived obligations may legitimately produce additional Facts outside
    the reviewed teaching target.  They are retained as out-of-scope audit
    items, but a target itself must have exactly one source-bound proposal with
    an exact canonical title or reviewed alias.
    """

    spans = {span.anchor_span_id: span for section in context.step2a_request.sections for span in section.anchor_spans}
    used: set[int] = set()
    missing: list[str] = []
    ambiguous: list[str] = []
    title_drift: list[str] = []
    matched: list[str] = []
    reviewed = all(target.review.state == LearningReviewState.REVIEWED for target in context.fixture_sample.facts)
    for target in context.fixture_sample.facts:
        candidates: list[int] = []
        for index, proposal in enumerate(response.proposals):
            if index in used or proposal.fact_type != target.fact_type:
                continue
            selected = tuple(spans[anchor_id] for anchor_id in proposal.primary_anchor_span_ids)
            anchors_match = all(
                any(
                    (selector.kind is None or span.parent_evidence_kind == selector.kind)
                    and _learning_title_key(selector.text) in _learning_title_key(span.canonical_text)
                    for span in selected
                )
                for selector in target.primary_anchors
            )
            grounded_text = "\n".join((proposal.assertion, *(span.canonical_text for span in selected)))
            terms_match = all(
                _learning_title_key(term) in _learning_title_key(grounded_text) for term in target.required_terms
            )
            if anchors_match and terms_match:
                candidates.append(index)
        if len(candidates) != 1:
            (missing if not candidates else ambiguous).append(target.fact_key)
            continue
        candidate = candidates[0]
        used.add(candidate)
        labels = {_learning_title_key(value) for value in (target.title, *target.title_aliases)}
        if _learning_title_key(response.proposals[candidate].label) not in labels:
            title_drift.append(target.fact_key)
            continue
        matched.append(target.fact_key)
    out_of_scope = tuple(
        f"proposal:{index}"
        for index in range(len(response.proposals))
        if index not in used
    )
    return {
        "valid": reviewed and not missing and not ambiguous and not title_drift,
        "reviewed_target_count": len(context.fixture_sample.facts),
        "matched_target_count": len(matched),
        "missing_target_fact_keys": tuple(missing),
        "ambiguous_target_fact_keys": tuple(ambiguous),
        "title_drift_fact_keys": tuple(title_drift),
        "out_of_scope_proposal_keys": out_of_scope,
        "out_of_scope_proposal_count": len(out_of_scope),
        "basis": "Each reviewed target must map exactly once by type, selected source anchor, required terms, and exact canonical title or reviewed alias; additional source-derived proposals remain auditable out_of_scope rather than being used as a Golden answer.",
    }


def _teaching_gate(context: V25LiveSampleContext, response: V25Step2AResultPayload):
    spans = {
        span.anchor_span_id: span
        for section in context.step2a_request.sections
        for item in section.text_evidence
        for span in item.anchor_spans
    }
    spans.update(
        {
            span.anchor_span_id: span
            for section in context.step2a_request.sections
            for table in section.tables
            for row in table.rows
            for cell in row.cells
            for span in cell.anchor_spans
        }
    )
    context_only = {
        span.anchor_span_id
        for section in context.step2a_request.sections
        for item in section.text_evidence
        if item.context_only
        for span in item.anchor_spans
    }
    unresolved = _unresolved_watermarks(
        sample_id=context.sample_id,
        pages=context.pages,
        manifest_hashes=context.manifest_hashes,
        sidecar=context.watermark_sidecar,
    )
    wanted = {item.review_id for item in context.fixture_sample.diagrams}
    diagrams = [
        assess_diagram_semantics(item)
        for item in context.diagram_sidecar.entries
        if item.sample_id == context.sample_id and item.review_id in wanted
    ]
    return build_teaching_quality_gate(
        titles=[
            assess_title_quality(
                title=item.label,
                evidence_texts=(spans[anchor_id].canonical_text for anchor_id in item.primary_anchor_span_ids),
            )
            for item in response.proposals
        ],
        tables=[
            assess_table_structure(source=table, rendered=_rendered_table(table))
            for section in context.step2a_request.sections
            for table in section.tables
        ],
        heading_only_facts=[
            assess_heading_only_fact(
                proposal_key=f"v25-live-proposal:{index}",
                selected_anchor_span_ids=item.primary_anchor_span_ids,
                context_only_anchor_span_ids=context_only,
            )
            for index, item in enumerate(response.proposals)
        ],
        diagrams=diagrams,
        watermark_candidates=unresolved,
    )


def _card_golden_gate(context: V25LiveSampleContext, response: V25BStep2BResultPayload) -> dict[str, object]:
    expected = {
        (tuple(context.facts_by_key[key].fact_id for key in card.primary_fact_keys), card.presentation.value)
        for card in context.fixture_sample.cards
    }
    observed = {(tuple(card.primary_fact_ids), card.presentation.value) for card in response.proposals}
    return {
        "valid": expected == observed,
        "expected_card_count": len(expected),
        "observed_card_count": len(observed),
        "missing_group_count": len(expected - observed),
        "extra_group_count": len(observed - expected),
    }


def _stage_request_payload(request: Any, stage: str) -> tuple[dict[str, object], str, dict[str, object]]:
    if stage == "step2a":
        return (
            v25_step2a_request_payload(request),
            v25_step2a_prompt_template(contract_profile=request.contract_profile),
            V25Step2AResultPayload.model_json_schema(),
        )
    return (
        v25b_step2b_request_payload(request),
        v25b_step2b_prompt_template(),
        V25BStep2BResultPayload.model_json_schema(),
    )


def _execute_stage(*, storage_root: Path, transport: V25LiveTransport, request: Any, stage: str) -> dict[str, object]:
    model_payload, prompt, response_schema = _stage_request_payload(request, stage)
    request_id = request.identity.request_id
    request_path = storage_root / "requests" / f"{_storage_name(request_id)}.json"
    record_path = storage_root / "records" / f"{_storage_name(request_id)}.json"
    cache_path = storage_root / "cache" / f"{_storage_name(request.identity.cache_key)}.json"
    journal_path = storage_root / "telemetry" / "journal.jsonl"
    journal_version = (
        V25_ATOMIC_SOURCE_STEP2A_JOURNAL_VERSION
        if stage == "step2a" and request.contract_profile.value == "v25_atomic_source_contract_v2"
        else V25_STEP2A_JOURNAL_VERSION
        if stage == "step2a"
        else V25B_STEP2B_JOURNAL_VERSION
    )
    if request_path.exists() or record_path.exists() or cache_path.exists():
        raise ValueError(f"Phase {PHASE} refuses to reuse an existing request identity: {request_id}")
    _atomic_write_json(
        request_path,
        {
            "request": request.model_dump(mode="json", exclude_computed_fields=True),
            "model_input": model_payload,
            "prompt_sha256": _sha256_text(prompt),
            "response_schema_sha256": canonical_step2_sha256(response_schema),
        },
    )
    started_cursor = _next_journal_cursor(journal_path)
    started = {
        "event_cursor": started_cursor,
        "event_type": "request_started",
        "timestamp_utc": _utc_now(),
        "stage": stage,
        "request_id": request_id,
        "request_sha256": request.identity.payload_sha256,
        "response_schema_sha256": request.identity.response_schema_sha256,
        "journal_version": journal_version,
    }
    _append_journal(journal_path, started)
    completion: TransportCompletion | None = None
    started_at = perf_counter()
    try:
        completion = transport.complete_payload(
            identity=request.identity,
            model_payload=model_payload,
            response_schema=response_schema,
            prompt=prompt,
            use_idempotency_key=False,
        )
        parsed_raw = json.loads(completion.content)
        response = V25Step2AResultPayload.model_validate(parsed_raw) if stage == "step2a" else V25BStep2BResultPayload.model_validate(parsed_raw)
        if not isinstance(parsed_raw, dict) or set(parsed_raw) != {"proposals"}:
            raise ValueError("V25 live response must contain only the proposals top-level field")
        record = {
            "state": "success",
            "request_id": request_id,
            "stage": stage,
            "response": response.model_dump(mode="json"),
            "response_sha256": _sha256_text(completion.content),
            "http_status": completion.http_status,
            "provider_request_id": completion.provider_request_id,
            "latency_ms": round((perf_counter() - started_at) * 1000, 2),
            "schema_valid": True,
            "forensics": None,
        }
    except Exception as error:
        record = {
            "state": "unknown_outcome" if completion is None else "failed",
            "request_id": request_id,
            "stage": stage,
            "response": None,
            "response_sha256": _sha256_text(completion.content) if completion is not None else None,
            "http_status": completion.http_status if completion is not None else _safe_error(error)["http_status"],
            "provider_request_id": completion.provider_request_id if completion is not None else None,
            "latency_ms": round((perf_counter() - started_at) * 1000, 2),
            "schema_valid": False,
            "forensics": _safe_error(error),
        }
    _atomic_write_json(record_path, record)
    _atomic_write_json(
        cache_path,
        {
            "request_id": request_id,
            "state": record["state"],
            "record_sha256": _sha256_bytes(record_path.read_bytes()),
        },
    )
    _append_journal(
        journal_path,
        {
            "event_cursor": started_cursor + 1,
            "event_type": "terminal",
            "timestamp_utc": _utc_now(),
            "stage": stage,
            "request_id": request_id,
            "outcome": record["state"],
            "http_status": record["http_status"],
            "provider_request_id": record["provider_request_id"],
            "response_sha256": record["response_sha256"],
            "forensics": record["forensics"],
            "journal_version": journal_version,
        },
    )
    return {
        **record,
        "request_identity": request.identity.model_dump(mode="json"),
        "request_path": request_path.as_posix(),
        "record_path": record_path.as_posix(),
        "cache_path": cache_path.as_posix(),
        "journal_path": journal_path.as_posix(),
        "idempotency_supported": False,
        "idempotency_header_sent": False,
        "automatic_retry_enabled": False,
        "payload_utf8_bytes": _json_bytes(model_payload),
        "journal_version": journal_version,
    }


def _probe_payload(stage: str) -> dict[str, object]:
    if stage == "step2a":
        return {
            "proposals": [{"fact_type": "definition", "label": "V25形状探测", "assertion": "仅用于严格响应形状探测。", "primary_anchor_span_ids": ["span:000000000000000000000000"]}]
        }
    return {
        "proposals": [{"title": "V25B形状探测", "primary_fact_ids": ["fact:000000000000000000000000"], "presentation": "text", "split_reason": "仅用于严格响应形状探测。"}]
    }


def _strict_probe(
    *, transport: V25LiveTransport, model: str, relay_base_url: str, stage: str, phase: str = PHASE
) -> dict[str, object]:
    response_schema = V25Step2AResultPayload.model_json_schema() if stage == "step2a" else V25BStep2BResultPayload.model_json_schema()
    expected = _probe_payload(stage)
    prompt = (
        "Return exactly this JSON object. Do not add fields, prose, explanations, or markdown.\n"
        + json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    )
    identity = canonical_step2_sha256(
        {
            "phase": phase,
            "stage": f"{stage}_strict_response_shape_probe",
            "relay_fingerprint_sha256": fingerprint_step2_relay(relay_base_url),
            "model": model,
            "prompt": prompt,
            "response_schema": response_schema,
        }
    )
    base = {
        "request_identity": f"{stage}-v25-shape-probe:{identity}",
        "stage": stage,
        "model": model,
        "relay_fingerprint_sha256": fingerprint_step2_relay(relay_base_url),
        "prompt_sha256": _sha256_text(prompt),
        "prompt_utf8_bytes": len(prompt.encode("utf-8")),
        "response_schema_sha256": canonical_step2_sha256(response_schema),
        "response_schema_utf8_bytes": _json_bytes(response_schema),
        "response_format": "json_schema_strict",
    }
    started = perf_counter()
    completion: TransportCompletion | None = None
    try:
        completion = transport.probe_json_schema(
            model=model,
            schema_name=f"v25_{stage}_strict_probe",
            response_schema=response_schema,
            user_content=prompt,
            max_tokens=320,
        )
        raw = json.loads(completion.content)
        payload = V25Step2AResultPayload.model_validate(raw) if stage == "step2a" else V25BStep2BResultPayload.model_validate(raw)
        if not isinstance(raw, dict) or set(raw) != {"proposals"}:
            raise ValueError("V25 strict probe response must contain only proposals")
        return {
            **base,
            "outcome": "success",
            "schema_valid": True,
            "proposal_count": len(payload.proposals),
            "http_status": completion.http_status,
            "provider_request_id": completion.provider_request_id,
            "response_sha256": _sha256_text(completion.content),
            "latency_ms": round((perf_counter() - started) * 1000, 2),
        }
    except Exception as error:
        return {
            **base,
            "outcome": "unknown_outcome" if completion is None else "invalid_response",
            "schema_valid": False,
            "proposal_count": 0,
            "http_status": completion.http_status if completion is not None else _safe_error(error)["http_status"],
            "provider_request_id": completion.provider_request_id if completion is not None else None,
            "response_sha256": _sha256_text(completion.content) if completion is not None else None,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "forensics": _safe_error(error),
        }


def run_v25_live_smoke(
    *,
    repository_root: Path,
    storage_root: Path,
    transport: V25LiveTransport | None,
    model: str,
    relay_base_url: str,
    manual_reissue_reason: str,
    allow_live: bool,
) -> tuple[dict[str, object], int]:
    """Run at most two probes, two Step 2A calls, and two dependent Step 2B calls."""

    root = Path(repository_root).resolve()
    storage = Path(storage_root).resolve()
    if storage.exists() and any(storage.iterdir()):
        raise ValueError(f"Phase {PHASE} storage root must be new and empty: {storage}")
    frozen_before = freeze_phase2r13_inputs(repository_root=root)
    contexts = build_live_sample_contexts(
        repository_root=root,
        model=model,
        relay_base_url=relay_base_url,
        manual_reissue_reason=manual_reissue_reason,
    )
    # probes 与 samples 单独具名：报告整体是 dict[str, object]（要原样落成 JSON），而从
    # report["probes"] 取回来的静态类型只能是 object，于是下标赋值报 index、append 报
    # attr-defined。具名局部量把精确类型留在原处，字典里放的是同一个对象引用，落盘结果
    # 逐字节不变。
    probes: dict[str, object] = {"step2a": None, "step2b": None}
    samples: list[dict[str, object]] = []
    report: dict[str, object] = {
        "phase": PHASE,
        "mode": "live" if allow_live else "dry_run",
        "model": model,
        "relay_fingerprint_sha256": fingerprint_step2_relay(relay_base_url),
        "network_call_budget": NETWORK_CALL_BUDGET,
        "manual_reissue_reason": manual_reissue_reason,
        "frozen_phase2r13_before": frozen_before,
        "probes": probes,
        "samples": samples,
        "review_promotion": "disabled; all live Step 2A and Step 2B output remains draft",
        "source_mutations": 0,
        "accepted": False,
        "stop_reason": None,
    }

    def finish(exit_code: int) -> tuple[dict[str, object], int]:
        frozen_after = freeze_phase2r13_inputs(repository_root=root)
        report["frozen_phase2r13_unchanged"] = frozen_after == frozen_before
        report["source_mutations"] = 0
        _atomic_write_json(storage / REPORT_NAME, report)
        return report, exit_code

    _atomic_write_json(storage / REPORT_NAME, report)
    if not allow_live:
        report.update(
            {
                "network_calls": 0,
                "stop_reason": "dry_run_complete",
                "frozen_phase2r13_unchanged": freeze_phase2r13_inputs(repository_root=root) == frozen_before,
            }
        )
        return finish(0)
    if transport is None:
        raise ValueError("Phase 2R.1.4 live mode requires an OpenAI-compatible transport")
    step2a_probe = _strict_probe(transport=transport, model=model, relay_base_url=relay_base_url, stage="step2a")
    probes["step2a"] = step2a_probe
    if step2a_probe["outcome"] != "success":
        report.update({"network_calls": transport.network_call_count, "stop_reason": "step2a_schema_probe_failed"})
        return finish(1)
    step2b_probe = _strict_probe(transport=transport, model=model, relay_base_url=relay_base_url, stage="step2b")
    probes["step2b"] = step2b_probe
    if step2b_probe["outcome"] != "success":
        report.update({"network_calls": transport.network_call_count, "stop_reason": "step2b_schema_probe_failed"})
        return finish(1)
    for context in contexts:
        step2a = _execute_stage(storage_root=storage, transport=transport, request=context.step2a_request, stage="step2a")
        sample_report: dict[str, object] = {"sample_id": context.sample_id, "step2a": step2a, "step2b": None}
        samples.append(sample_report)
        if step2a["state"] != "success":
            report.update({"network_calls": transport.network_call_count, "stop_reason": f"{context.sample_id}_step2a_{step2a['state']}"})
            return finish(1)
        response_a = V25Step2AResultPayload.model_validate(step2a["response"])
        source_gate = assess_v25_step2a_response(request=context.step2a_request, response=response_a)
        teaching_gate = _teaching_gate(context, response_a)
        learning_gate = _learning_fact_gate(context)
        table_or_bridge_gate = {
            "valid": (
                bool(context.step2a_request.reviewed_continuation_bindings)
                if context.sample_id == "gonglu_24_table_formula"
                else any(fact.semantic_diagram_bridge is not None for fact in context.step2b_request.facts)
            ),
            "complete_table_obligation_count": sum(
                item.kind.value == "complete_table_composition" for item in context.step2a_request.coverage_obligations
            ),
            "reviewed_continuation_binding_count": len(context.step2a_request.reviewed_continuation_bindings),
            "semantic_diagram_fact_bridge_count": sum(
                fact.semantic_diagram_bridge is not None for fact in context.step2b_request.facts
            ),
            "evidence_only_diagram_excluded": context.sample_id != "gonglu_24_table_formula" or all(
                fact.semantic_diagram_bridge is None for fact in context.step2b_request.facts
            ),
        }
        sample_report["step2a_gates"] = {
            "source_contract": source_gate.model_dump(mode="json"),
            "teaching": teaching_gate.model_dump(mode="json"),
            "learning_golden": learning_gate,
            "table_or_diagram_bridge": table_or_bridge_gate,
        }
        if not (source_gate.valid and teaching_gate.publishable and learning_gate["valid"] and table_or_bridge_gate["valid"]):
            report.update({"network_calls": transport.network_call_count, "stop_reason": f"{context.sample_id}_step2a_gate_failed"})
            return finish(1)
        step2b = _execute_stage(storage_root=storage, transport=transport, request=context.step2b_request, stage="step2b")
        sample_report["step2b"] = step2b
        if step2b["state"] != "success":
            report.update({"network_calls": transport.network_call_count, "stop_reason": f"{context.sample_id}_step2b_{step2b['state']}"})
            return finish(1)
        response_b = V25BStep2BResultPayload.model_validate(step2b["response"])
        grouping_gate = assess_v25b_step2b_response(request=context.step2b_request, response=response_b)
        card_golden = _card_golden_gate(context, response_b)
        compositions = {fact.fact_id for fact in context.step2b_request.facts if fact.fact_type.value == "composition"}
        composition_cards = [set(card.primary_fact_ids) for card in response_b.proposals if set(card.primary_fact_ids) & compositions]
        atomic_separation = all(len(card & compositions) == 1 for card in composition_cards)
        sample_report["step2b_gates"] = {
            "source_grouping": grouping_gate.model_dump(mode="json"),
            "learning_card": card_golden,
            "complete_table_cards_separate_from_atomic_rules": atomic_separation,
            "facts_exactly_once": grouping_gate.exactly_once,
        }
        if not (grouping_gate.valid and card_golden["valid"] and atomic_separation):
            report.update({"network_calls": transport.network_call_count, "stop_reason": f"{context.sample_id}_step2b_gate_failed"})
            return finish(1)
        if transport.network_call_count > NETWORK_CALL_BUDGET:
            raise RuntimeError(f"Phase {PHASE} network call budget exceeded")
    frozen_after = freeze_phase2r13_inputs(repository_root=root)
    report.update(
        {
            "network_calls": transport.network_call_count,
            "frozen_phase2r13_unchanged": frozen_after == frozen_before,
            "source_mutations": 0,
            "accepted": transport.network_call_count <= NETWORK_CALL_BUDGET and frozen_after == frozen_before,
            "stop_reason": None,
        }
    )
    return finish(0 if report["accepted"] else 1)
