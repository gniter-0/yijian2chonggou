"""Focused tests for the Step 2B -> Admission V2 production boundary."""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from examcard.evidence.schema import (
    EvidenceKind,
    EvidenceProvenance,
    EvidenceUnit,
    PageEvidence,
    PageQuality,
    SourceChannel,
    SourceQuality,
    SourceLocator,
)
from examcard.gates.review.decisions_v2 import HumanDecisionManifestV2, HumanDecisionV2
from examcard.gates.review.typed_evidence_export import ExplicitProductionAuthorization
from examcard.gates.review.step2b_admission_v2 import (
    AdmissionProjectionError,
    build_admission_queue_items_v2,
    make_source_page_binding,
    project_step2b_to_admission_v2,
)
from examcard.knowledge.contracts import CardPresentation, make_evidence_anchor_span_id
from examcard.knowledge.contracts.admission_v2 import ThresholdContract
from examcard.knowledge.contracts.admission_legacy_hash import canonical_hash, hash_model_r8x
from examcard.knowledge.contracts.v25_generic_contracts import (
    V25BModelCardProposal,
    V25BStep2BResultPayload,
    V25Step2BFactSummary,
    V25FactReviewState,
    build_v25_source_fake_response,
    build_v25b_source_fake_response,
    build_v25b_step2b_request,
)
from examcard.knowledge.schema import FactType
from examcard.materials.schema import (
    CoverageRequirement,
    PolicyDecision,
    PolicyDecisionState,
    PolicyDisposition,
    SemanticRole,
)
from examcard.pipeline.live_checkpoint import (
    build_admission_checkpoint_v2,
    ingest_admission_checkpoint_v2,
)
from examcard.pipeline.live_config import LiveRunConfig
from examcard.pipeline.live_orchestrate import orchestrate
from examcard.knowledge.llm_transport import TransportCompletion
from examcard.knowledge.contracts.phase2r14_v25_live import (
    build_live_sample_contexts,
    run_v25_live_smoke,
)
from migration import run_exam_pipeline
from tests.conftest import GOLDEN_EVIDENCE_DIR, requires_golden_assets


SOURCE_MANIFEST = "a" * 64
DOCUMENT_SHA = "b" * 64
POLICY_SHA = "c" * 64
EVIDENCE_ID = "ev:" + "d" * 24
FACT_ID = "fact:" + "e" * 24
TOPIC_ID = "source-topic:" + "f" * 24
SECTION_ID = "section:" + "1" * 24
DOCUMENT_ID = "doc:" + "2" * 24


def _png_bytes() -> bytes:
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "/w8AAgMBApWZ8WQAAAAASUVORK5CYII="
    )
    return image


def _source_case() -> dict[str, Any]:
    text = "来源阈值 300mm"
    exact_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    policy = PolicyDecision(
        evidence_id=EVIDENCE_ID,
        semantic_role=SemanticRole.CORE,
        disposition=PolicyDisposition.INCLUDED,
        coverage_requirement=CoverageRequirement.REQUIRED,
        decision_state=PolicyDecisionState.AUTO_CLASSIFIED,
        policy_id="core_only_v1",
        policy_revision=1,
        reason="synthetic authoritative source",
    )
    from examcard.knowledge.contracts.anchors import EvidenceAnchorSpan
    from examcard.knowledge.contracts.table_inputs import SectionTextInput, Step2ASectionInput

    anchor_id = make_evidence_anchor_span_id(
        parent_evidence_id=EVIDENCE_ID,
        start=0,
        end=len(text),
        exact_text_sha256=exact_sha,
        content_policy_fingerprint_sha256=POLICY_SHA,
        source_manifest_sha256=SOURCE_MANIFEST,
    )
    anchor = EvidenceAnchorSpan(
        anchor_span_id=anchor_id,
        parent_evidence_id=EVIDENCE_ID,
        parent_evidence_kind=EvidenceKind.PARAGRAPH,
        start=0,
        end=len(text),
        canonical_text=text,
        exact_text_sha256=exact_sha,
        content_policy_id="core_only_v1",
        content_policy_revision=1,
        content_policy_fingerprint_sha256=POLICY_SHA,
        policy_decision=policy,
        source_manifest_sha256=SOURCE_MANIFEST,
    )
    section = Step2ASectionInput(
        section_id=SECTION_ID,
        page_num=1,
        text_evidence=(
            SectionTextInput(
                evidence_id=EVIDENCE_ID,
                page_num=1,
                kind=EvidenceKind.PARAGRAPH,
                canonical_text=text,
                anchor_spans=(anchor,),
            ),
        ),
    )
    unit = EvidenceUnit(
        evidence_id=EVIDENCE_ID,
        fingerprint="evfp:" + "3" * 24,
        revision_id="evrev:" + "4" * 24,
        document_id=DOCUMENT_ID,
        kind=EvidenceKind.PARAGRAPH,
        locator=SourceLocator(document_sha256=DOCUMENT_SHA, page_num=1),
        raw_text=text,
        canonical_text=text,
        semantic_role=SemanticRole.CORE,
        source_quality=SourceQuality.NATIVE_ONLY,
        confidence=1.0,
        order=0,
        policy_decision=policy,
        provenance=EvidenceProvenance(
            source_channels=frozenset({SourceChannel.NATIVE_TEXT}),
            config_hash=POLICY_SHA,
            generated_at_utc="2026-09-11T00:00:00Z",
        ),
    )
    page = PageEvidence(
        page_id="page:test-threshold",
        page_num=1,
        width=1,
        height=1,
        evidence_units=(unit,),
        quality=PageQuality(native_text_coverage=1, ocr_alignment_rate=0, unresolved_regions=0, conflict_count=0),
    )
    fact = V25Step2BFactSummary(
        fact_id=FACT_ID,
        fact_type=FactType.THRESHOLD,
        canonical_title="阈值事实",
        assertion_summary="来源阈值用于验证 Admission V2 的精确绑定。",
        primary_evidence_ids=(EVIDENCE_ID,),
        primary_anchor_span_ids=(anchor_id,),
        source_topic_key=TOPIC_ID,
        review_state=V25FactReviewState.REVIEWED,
    )
    request = build_v25b_step2b_request(
        sample_id="threshold-sample",
        material_profile_id="test-profile",
        facts=(fact,),
        model="deterministic-test",
        relay_base_url="https://offline.invalid/v1",
    )
    response = V25BStep2BResultPayload(
        proposals=(
            V25BModelCardProposal(
                title="来源阈值卡",
                primary_fact_ids=(FACT_ID,),
                presentation=CardPresentation.THRESHOLD,
                split_reason="one source-derived threshold obligation",
            ),
        )
    )
    page_bytes = page.model_dump_json().encode("utf-8")
    image_bytes = _png_bytes()
    binding = make_source_page_binding(
        page_ref_id="p:1",
        page=1,
        source_file="sources/test.pdf",
        image_path="p.png",
        image_bytes=image_bytes,
        page_bytes=page_bytes,
        image_width=1,
        image_height=1,
        source_manifest_sha256=SOURCE_MANIFEST,
    )
    return {
        "request": request,
        "response": response,
        "pages": (page,),
        "sections": (section,),
        "binding": binding,
        "image_bytes": image_bytes,
        "page_bytes": page_bytes,
    }


def _projection(case: dict[str, Any]):
    return project_step2b_to_admission_v2(
        request=case["request"],
        response=case["response"],
        pages=case["pages"],
        page_bindings=(case["binding"],),
        sections=case["sections"],
    )


def _source_validator(**_kwargs: Any) -> None:
    """Test-only stand-in for the production Step2B source revalidation hook."""


def test_valid_v25b_response_projects_to_one_threshold_queue_item() -> None:
    case = _source_case()
    projection = _projection(case)

    assert projection.request_id == case["request"].identity.request_id
    assert projection.candidate_ids == (projection.queue_items[0].candidate_id,)
    assert projection.queue_items[0].presentation == "threshold"
    assert projection.queue_items[0].disposition == "manual_review"
    assert projection.contract_drafts_by_candidate[projection.candidate_ids[0]]["typed_contract"]["presentation"] == "threshold"
    assert build_admission_queue_items_v2(
        request=case["request"],
        response=case["response"],
        pages=case["pages"],
        page_bindings=(case["binding"],),
        sections=case["sections"],
    ) == projection.queue_items


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            V25BStep2BResultPayload(
                proposals=(
                    V25BModelCardProposal(
                        title="illegal merged card",
                        primary_fact_ids=(FACT_ID, "fact:" + "9" * 24),
                        presentation=CardPresentation.THRESHOLD,
                        split_reason="merged facts are not allowed",
                    ),
                )
            ),
            "multi-Fact",
        ),
    ],
)
def test_projection_rejects_unrepresentable_response(response: Any, message: str) -> None:
    case = _source_case()
    with pytest.raises(AdmissionProjectionError, match=message):
        project_step2b_to_admission_v2(
            request=case["request"],
            response=response,
            pages=case["pages"],
            page_bindings=(case["binding"],),
            sections=case["sections"],
        )


def test_projection_rejects_missing_or_crossed_page_binding() -> None:
    case = _source_case()
    with pytest.raises(AdmissionProjectionError, match="missing rendered source page"):
        project_step2b_to_admission_v2(
            request=case["request"],
            response=case["response"],
            pages=case["pages"],
            page_bindings=(),
            sections=case["sections"],
        )

    crossed = make_source_page_binding(
        page_ref_id=case["binding"].page_ref_id,
        page=1,
        source_file="sources/foreign.pdf",
        image_path="pages/foreign.png",
        image_bytes=case["image_bytes"],
        page_bytes=case["page_bytes"],
        image_width=1,
        image_height=1,
        source_manifest_sha256="9" * 64,
    )
    with pytest.raises(AdmissionProjectionError, match="missing rendered source page"):
        project_step2b_to_admission_v2(
            request=case["request"],
            response=case["response"],
            pages=case["pages"],
            page_bindings=(crossed,),
            sections=case["sections"],
        )


def _write_authority_inputs(run_root: Path, case: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    projection = _projection(case)
    ticket = build_admission_checkpoint_v2(
        run_root=run_root,
        projection=projection,
        page_artifacts={case["binding"].page_ref_id: (case["image_bytes"], case["page_bytes"])},
    )
    item = projection.queue_items[0]
    draft = projection.contract_drafts_by_candidate[item.candidate_id]
    contract = ThresholdContract.model_validate(draft["typed_contract"])
    decision = hash_model_r8x(
        HumanDecisionV2,
        {
            "decision_id": "decision:r84:human:test-threshold",
            "candidate_id": item.candidate_id,
            "queue_item_id": item.queue_item_id,
            "queue_sha256": ticket["queue_sha256"],
            "subject_id": item.subject_id,
            "record_id": item.record_id,
            "fact_id": item.fact_id,
            "evidence_id": item.evidence_id,
            "presentation": item.presentation,
            "source_manifest_sha256": item.source_manifest_sha256,
            "anchor_ids": item.anchor_ids,
            "pages": item.pages,
            "disposition": "reviewed_candidate",
            "reviewer": "human-tester",
            "reason": "explicit source-page threshold review",
            "source_text_confirmed": True,
            "anchor_confirmed": True,
            "source_page_confirmed": True,
            "image_hash_confirmed": True,
            "typed_contract_confirmed": True,
            "typed_contract": contract,
            "typed_contract_sha256": canonical_hash(contract.model_dump(mode="json")),
            "recorded_at": "2026-09-11T00:00:00Z",
            "browser_demo": False,
            "production_fixture_authorized": True,
        },
        "decision_sha256",
    )
    manifest = hash_model_r8x(
        HumanDecisionManifestV2,
        {
            "manifest_id": "manifest:r84:human:test-threshold",
            "queue_sha256": ticket["queue_sha256"],
            "decisions": (decision,),
            "decision_count": 1,
        },
        "manifest_sha256",
    )
    checkpoint = run_root / "checkpoints" / "admission"
    inbox = checkpoint / "inbox"
    (inbox / "human-decision-manifest-v2.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8"
    )
    authorization = ExplicitProductionAuthorization(
        reviewer="human-tester",
        reason="explicit source-page threshold review",
        recorded_at="2026-09-11T00:00:00Z",
        identity_period="threshold-test",
    )
    (inbox / "explicit-production-authorization-v2.json").write_text(
        json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8"
    )
    return checkpoint, ticket


def test_checkpoint_ingests_human_authority_and_emits_v2_fixture(tmp_path: Path) -> None:
    case = _source_case()
    checkpoint, ticket = _write_authority_inputs(tmp_path, case)

    result = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )

    assert result["status"] == "ingested"
    assert result["released_count"] == 1
    fixture = json.loads(Path(result["fixture_path"]).read_text(encoding="utf-8"))
    assert fixture["schema_version"] == "phase2r810_reviewed_typed_evidence_fixture_v2"
    assert fixture["candidates"][0]["typed_contract"]["presentation"] == "threshold"
    assert ticket["queue_sha256"] == result["audit"]["queue_sha256"]
    assert (checkpoint / "external-authority-audit-v2.json").is_file()


def test_checkpoint_refuses_page_and_fixture_tampering(tmp_path: Path) -> None:
    case = _source_case()
    checkpoint, ticket = _write_authority_inputs(tmp_path, case)
    image_path = checkpoint / case["binding"].image_path
    image_path.write_bytes(case["image_bytes"] + b"tampered")

    refused_page = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )
    assert refused_page["status"] == "refused"
    assert "image hash mismatch" in refused_page["detail"]

    image_path.write_bytes(case["image_bytes"])
    accepted = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )
    assert accepted["status"] == "ingested"
    fixture_path = Path(accepted["fixture_path"])
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["candidates"][0]["reviewer"] = "tampered"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

    refused_fixture = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        verify_only=True,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )
    assert refused_fixture["status"] == "refused"
    assert "does not match" in refused_fixture["detail"]


def test_checkpoint_refuses_missing_page_evidence_and_draft_file_drift(tmp_path: Path) -> None:
    case = _source_case()
    checkpoint, ticket = _write_authority_inputs(tmp_path, case)
    page_json = checkpoint / "pages" / "p_1.json"
    page_json.unlink()

    missing_page = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )
    assert missing_page["status"] == "refused"
    assert "source page evidence is missing" in missing_page["detail"]

    checkpoint, ticket = _write_authority_inputs(tmp_path / "draft-case", case)
    draft_path = next((checkpoint / "contract-drafts").glob("*.json"))
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["source_anchor_ids"] = ["tampered-anchor"]
    draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    drifted_draft = ingest_admission_checkpoint_v2(
        run_root=tmp_path / "draft-case",
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )
    assert drifted_draft["status"] == "refused"
    assert "draft file hash mismatch" in drifted_draft["detail"]


def test_checkpoint_requires_source_validator(tmp_path: Path) -> None:
    case = _source_case()
    _checkpoint, ticket = _write_authority_inputs(tmp_path, case)

    refused = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=None,
    )

    assert refused["status"] == "refused"
    assert "source validator is required" in refused["detail"]


def test_checkpoint_refuses_replaced_queue_before_new_authority_identity(tmp_path: Path) -> None:
    case = _source_case()
    _checkpoint, ticket = _write_authority_inputs(tmp_path, case)

    replacement = _source_case()
    replacement_proposal = replacement["response"].proposals[0].model_copy(update={"title": "替换后的来源阈值卡"})
    replacement["response"] = replacement["response"].model_copy(update={"proposals": (replacement_proposal,)})
    replacement_projection = _projection(replacement)
    replacement_ticket = build_admission_checkpoint_v2(
        run_root=tmp_path,
        projection=replacement_projection,
        page_artifacts={replacement["binding"].page_ref_id: (replacement["image_bytes"], replacement["page_bytes"])},
    )
    assert replacement_ticket["queue_file_sha256"] != ticket["queue_file_sha256"]

    refused = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )

    assert refused["status"] == "refused"
    assert "queue file identity drifted" in refused["detail"]


def test_checkpoint_requires_all_candidates_to_be_reviewed(tmp_path: Path) -> None:
    case = _source_case()
    checkpoint, ticket = _write_authority_inputs(tmp_path, case)
    manifest_path = checkpoint / "inbox" / "human-decision-manifest-v2.json"
    manifest = HumanDecisionManifestV2.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    source = manifest.decisions[0]
    manual = hash_model_r8x(
        HumanDecisionV2,
        {
            **source.model_dump(mode="json"),
            "decision_id": "decision:r84:human:manual-review",
            "disposition": "manual_review",
            "source_text_confirmed": False,
            "anchor_confirmed": False,
            "source_page_confirmed": False,
            "image_hash_confirmed": False,
            "typed_contract_confirmed": False,
            "typed_contract": None,
            "typed_contract_sha256": None,
            "production_fixture_authorized": False,
        },
        "decision_sha256",
    )
    waiting_manifest = hash_model_r8x(
        HumanDecisionManifestV2,
        {
            "manifest_id": "manifest:r84:human:manual-review",
            "queue_sha256": ticket["queue_sha256"],
            "decisions": (manual,),
            "decision_count": 1,
        },
        "manifest_sha256",
    )
    manifest_path.write_text(json.dumps(waiting_manifest.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")

    waiting = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )
    assert waiting["status"] == "waiting_human"
    assert not (checkpoint / "reviewed-typed-evidence-fixture-v2.json").exists()

    blocked = hash_model_r8x(
        HumanDecisionV2,
        {
            **manual.model_dump(mode="json"),
            "decision_id": "decision:r84:human:blocked",
            "disposition": "blocked",
        },
        "decision_sha256",
    )
    blocked_manifest = hash_model_r8x(
        HumanDecisionManifestV2,
        {
            "manifest_id": "manifest:r84:human:blocked",
            "queue_sha256": ticket["queue_sha256"],
            "decisions": (blocked,),
            "decision_count": 1,
        },
        "manifest_sha256",
    )
    manifest_path.write_text(json.dumps(blocked_manifest.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    refused = ingest_admission_checkpoint_v2(
        run_root=tmp_path,
        expected_queue_file_sha256=ticket["queue_file_sha256"],
        source_validator=_source_validator,
    )
    assert refused["status"] == "refused"
    assert "terminal refusal disposition" in refused["detail"]


def test_ordered_list_injection_is_not_an_admission_authority() -> None:
    case = _source_case()
    fact = V25Step2BFactSummary.model_validate(
        {**case["request"].facts[0].model_dump(mode="json"), "fact_type": "enumeration"}
    )
    request = build_v25b_step2b_request(
        sample_id=case["request"].sample_id,
        material_profile_id=case["request"].material_profile_id,
        facts=(fact,),
        model="deterministic-test",
        relay_base_url="https://offline.invalid/v1",
    )
    response = V25BStep2BResultPayload(
        proposals=(
            V25BModelCardProposal(
                title="来源顺序卡",
                primary_fact_ids=(FACT_ID,),
                presentation=CardPresentation.ORDERED_LIST,
                split_reason="marker injection must not authorize ordered lists",
            ),
        )
    )
    with pytest.raises(AdmissionProjectionError, match="ordered_list is not admitted"):
        project_step2b_to_admission_v2(
            request=request,
            response=response,
            pages=case["pages"],
            page_bindings=(case["binding"],),
            sections=case["sections"],
            source_list_markers={EVIDENCE_ID: "1."},
        )


class _AdapterTransport:
    def __init__(self, completions: list[str]) -> None:
        self.completions = list(completions)
        self.network_call_count = 0

    def probe_json_schema(self, *, schema_name: str, **_kwargs: Any) -> TransportCompletion:
        self.network_call_count += 1
        if schema_name == "v25_step2a_strict_probe":
            payload = {
                "proposals": [
                    {
                        "fact_type": "definition",
                        "label": "V25形状探测",
                        "assertion": "仅用于严格响应形状探测。",
                        "primary_anchor_span_ids": ["span:" + "0" * 24],
                    }
                ]
            }
        else:
            payload = {
                "proposals": [
                    {
                        "title": "V25B形状探测",
                        "primary_fact_ids": ["fact:" + "0" * 24],
                        "presentation": "text",
                        "split_reason": "仅用于严格响应形状探测。",
                    }
                ]
            }
        return TransportCompletion(content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")), http_status=200)

    def complete_payload(self, **_kwargs: Any) -> TransportCompletion:
        self.network_call_count += 1
        return TransportCompletion(content=self.completions.pop(0), provider_request_id="adapter-test", http_status=200)


@requires_golden_assets(GOLDEN_EVIDENCE_DIR)
def test_production_v25_adapter_accepts_legal_step2b_responses(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    contexts = build_live_sample_contexts(
        repository_root=repo_root,
        model="deterministic-v25-test",
        relay_base_url="https://offline.invalid/v1",
        manual_reissue_reason="admission-v2-adapter",
        sample_ids=("gonglu_24_table_formula", "gonglu_41_diagram_mixed"),
    )
    completions = [
        json.dumps(build_v25_source_fake_response(request=context.step2a_request).model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        for context in contexts
    ]
    ordered: list[str] = []
    for context, step2a in zip(contexts, completions, strict=True):
        ordered.extend(
            (
                step2a,
                json.dumps(build_v25b_source_fake_response(request=context.step2b_request).model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
            )
        )
    transport = _AdapterTransport(ordered)

    report, exit_code = run_v25_live_smoke(
        repository_root=repo_root,
        storage_root=tmp_path / "adapter",
        transport=transport,
        model="deterministic-v25-test",
        relay_base_url="https://offline.invalid/v1",
        manual_reissue_reason="admission-v2-adapter",
        allow_live=True,
    )

    assert exit_code == 0
    assert report["accepted"] is True
    assert report["network_calls"] == 6
    assert report["frozen_phase2r13_unchanged"] is True
    assert all(item["step2b_gates"]["source_grouping"]["valid"] is True for item in report["samples"])


def _seed_orchestration_workspace(root: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    for relative in (
        "exams/gonglu_golden5_rehearsal/fixtures/golden_samples/gonglu_golden5_rehearsal_phase0.yaml",
        "configs/v2/material_profiles/yijian_gonglu_2026.yaml",
        "configs/v2/content_policies/core_only_v1.yaml",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)
    annotation = root / "exams/gonglu_golden5_rehearsal/fixtures/golden_annotations/gonglu_golden5_rehearsal_phase0_v1.yaml"
    annotation.parent.mkdir(parents=True, exist_ok=True)
    annotation.write_text("synthetic: true\n", encoding="utf-8")
    (root / "exams/gonglu_golden5_rehearsal/data/evidence_v2/golden/gonglu_golden5_rehearsal_phase0_v1").mkdir(
        parents=True, exist_ok=True
    )


def test_same_run_admission_v2_is_the_only_step3_v3_source(request: pytest.FixtureRequest) -> None:
    short_root = Path(tempfile.mkdtemp(prefix="ec2c-", dir=tempfile.gettempdir()))
    request.addfinalizer(lambda: shutil.rmtree(short_root, ignore_errors=True))
    _seed_orchestration_workspace(short_root)
    case = _source_case()
    projection = _projection(case)
    external_fixture = short_root / "external-typed-fixture-that-must-not-be-read.json"
    external_fixture.write_text("not a V2 fixture", encoding="utf-8")
    records: dict[str, dict[str, Any]] = {}
    source_validation_calls: list[tuple[str, ...]] = []

    def context_builder(*, sample_ids: tuple[str, ...], reason: str):
        assert sample_ids == ("threshold-sample",)
        return (
            type(
                "Context",
                (),
                {
                    "sample_id": "threshold-sample",
                    "step2a_request": case["request"],
                    "step2b_request": case["request"],
                    "reason": reason,
                },
            )(),
        )

    def stage_executor(*, storage_root: Path, request: Any, stage: str, **_kwargs: Any) -> dict[str, Any]:
        response = case["response"].model_dump(mode="json") if stage == "step2b" else {"proposals": []}
        record = {
            "state": "success",
            "request_id": request.identity.request_id,
            "stage": stage,
            "response": response,
            "http_status": 200,
            "latency_ms": 1.0,
        }
        records[stage] = record
        return record

    def chat_strict(**_kwargs: Any) -> dict[str, Any]:
        from examcard.compilers.step3_card_v3 import compile_reviewed_fixture_v3
        from tests.fixtures.step3_v3 import valid_v3_response_content
        from examcard.knowledge.contracts.admission_v2 import ReviewedTypedEvidenceFixtureV2

        fixture_path = current_run_root / "checkpoints" / "admission" / "reviewed-typed-evidence-fixture-v2.json"
        fixture_bytes = fixture_path.read_bytes()
        fixture = ReviewedTypedEvidenceFixtureV2.model_validate(json.loads(fixture_bytes))
        compilation = compile_reviewed_fixture_v3(
            fixture,
            source_typed_fixture_file_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
        )
        return {"content": valid_v3_response_content(compilation), "http_status": 200, "latency_ms": 1.0}

    def admission_source_validator(**kwargs: Any) -> None:
        queue_doc = kwargs["queue_doc"]
        assert queue_doc["source_provenance"]
        assert queue_doc["contract_draft_files"]
        source_validation_calls.append(tuple(item.candidate_id for item in kwargs["queue_items"]))

    config = LiveRunConfig(
        exam_id="gonglu_golden5_rehearsal",
        stages=("step2a", "step2b", "step3"),
        mode="live",
            run_label="x",
        budget_usd=10.0,
        max_attempts=2,
        step3_contract="v3",
        step3_typed_fixture=external_fixture,
        skip_preflight=True,
        repo_root=short_root,
    )
    providers: dict[str, Any] = {
        "pricing": {"step2a_call": 0.1, "step2b_call": 0.1, "step3_card_call": 0.2},
        "sample_ids": ("threshold-sample",),
        "step3_card_count": 1,
        "context_builder": context_builder,
        "stage_executor": stage_executor,
        "gate_evaluator": lambda _stage, _context, _record: {"valid": True},
        "admission_projection_builder": lambda **_kwargs: (projection,),
        "admission_page_artifacts": {
            case["binding"].page_ref_id: (case["image_bytes"], case["page_bytes"])
        },
        "admission_source_validator": admission_source_validator,
        "chat_strict": chat_strict,
    }

    first_report, first_exit = orchestrate(config, providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_admission")
    current_run_root = short_root / "exams" / "gonglu_golden5_rehearsal" / "data" / "live_runs" / first_report.run_id
    _write_authority_inputs(current_run_root, case)

    second_report, second_exit = orchestrate(replace(config, resume_run_id=first_report.run_id), providers)

    assert (second_exit, second_report.stop_reason) == (0, "completed")
    assert {stage.stage: stage.status for stage in second_report.stages}["step3"] == "completed"
    assert second_report.network_calls == 3
    assert current_run_root.joinpath("checkpoints/admission/reviewed-typed-evidence-fixture-v2.json").is_file()
    assert second_report.run_id == first_report.run_id
    assert records["step2b"]["response"]["proposals"][0]["presentation"] == "threshold"
    assert len(source_validation_calls) == 1


def _same_run_validator_scenario(request: pytest.FixtureRequest, source_validator: Any, mutate: Any = None) -> tuple[Path, list[str]]:
    short_root = Path(tempfile.mkdtemp(prefix="ec2c-", dir=tempfile.gettempdir()))
    request.addfinalizer(lambda: shutil.rmtree(short_root, ignore_errors=True))
    _seed_orchestration_workspace(short_root)
    case = _source_case()
    projection = _projection(case)
    external_fixture = short_root / "external-typed-fixture-that-must-not-be-read.json"
    external_fixture.write_text("not a V2 fixture", encoding="utf-8")
    step3_calls: list[str] = []

    def context_builder(*, sample_ids: tuple[str, ...], reason: str):
        assert sample_ids == ("threshold-sample",)
        return (
            type(
                "Context",
                (),
                {
                    "sample_id": "threshold-sample",
                    "step2a_request": case["request"],
                    "step2b_request": case["request"],
                    "reason": reason,
                },
            )(),
        )

    def stage_executor(*, request: Any, stage: str, **_kwargs: Any) -> dict[str, Any]:
        response = case["response"].model_dump(mode="json") if stage == "step2b" else {"proposals": []}
        return {
            "state": "success",
            "request_id": request.identity.request_id,
            "stage": stage,
            "response": response,
            "http_status": 200,
            "latency_ms": 1.0,
        }

    def chat_strict(**_kwargs: Any) -> dict[str, Any]:
        step3_calls.append("step3")
        raise AssertionError("Step3 provider must not be called")

    config = LiveRunConfig(
        exam_id="gonglu_golden5_rehearsal",
        stages=("step2a", "step2b", "step3"),
        mode="live",
        run_label="x",
        budget_usd=10.0,
        max_attempts=2,
        step3_contract="v3",
        step3_typed_fixture=external_fixture,
        skip_preflight=True,
        repo_root=short_root,
    )
    providers: dict[str, Any] = {
        "pricing": {"step2a_call": 0.1, "step2b_call": 0.1, "step3_card_call": 0.2},
        "sample_ids": ("threshold-sample",),
        "step3_card_count": 1,
        "context_builder": context_builder,
        "stage_executor": stage_executor,
        "gate_evaluator": lambda _stage, _context, _record: {"valid": True},
        "admission_projection_builder": lambda **_kwargs: (projection,),
        "admission_page_artifacts": {
            case["binding"].page_ref_id: (case["image_bytes"], case["page_bytes"])
        },
        "admission_source_validator": source_validator,
        "chat_strict": chat_strict,
    }

    first_report, first_exit = orchestrate(config, providers)
    assert (first_exit, first_report.stop_reason) == (2, "waiting_human_admission")
    run_root = short_root / "exams" / "gonglu_golden5_rehearsal" / "data" / "live_runs" / first_report.run_id
    _write_authority_inputs(run_root, case)
    if mutate is not None:
        mutate(run_root)

    second_report, second_exit = orchestrate(replace(config, resume_run_id=first_report.run_id), providers)
    assert (second_exit, second_report.stop_reason) == (1, "gate_refused_admission")
    assert not step3_calls
    return run_root, step3_calls


def test_same_run_missing_source_validator_refuses_before_step3(request: pytest.FixtureRequest) -> None:
    run_root, _step3_calls = _same_run_validator_scenario(request, None)

    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["admission"]["status"] == "refused"
    assert not (run_root / "checkpoints" / "admission" / "reviewed-typed-evidence-fixture-v2.json").exists()


def test_same_run_source_validator_failure_refuses_before_step3(request: pytest.FixtureRequest) -> None:
    def rejecting_validator(**_kwargs: Any) -> None:
        raise ValueError("synthetic source revalidation failure")

    run_root, _step3_calls = _same_run_validator_scenario(request, rejecting_validator)

    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["admission"]["status"] == "refused"
    assert not (run_root / "checkpoints" / "admission" / "reviewed-typed-evidence-fixture-v2.json").exists()


def test_same_run_orchestrator_uses_emitted_queue_identity_before_ingest(request: pytest.FixtureRequest) -> None:
    def mutate_queue(run_root: Path) -> None:
        queue_path = run_root / "checkpoints" / "admission" / "queue-v2.json"
        queue_path.write_bytes(queue_path.read_bytes() + b"\n")

    run_root, _step3_calls = _same_run_validator_scenario(request, _source_validator, mutate_queue)

    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["admission"]["status"] == "refused"
    assert not (run_root / "checkpoints" / "admission" / "reviewed-typed-evidence-fixture-v2.json").exists()


@requires_golden_assets(GOLDEN_EVIDENCE_DIR)
def test_production_admission_providers_bind_successful_step2b_retry_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production CLI providers must reopen the exact successful offline retry source."""

    env_file = tmp_path / "offline.env"
    env_file.write_text("TEXT_MODEL=deterministic-v25-test\nIMAGE_MODEL=deterministic-image\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_orchestrate(config: Any, providers: Any) -> tuple[Any, int]:
        captured["config"] = config
        captured["providers"] = providers
        return (
            SimpleNamespace(
                run_id="captured-offline-run",
                mode=config.mode,
                stages=(),
                stop_reason="captured",
                network_calls=0,
                usd_estimate=0.0,
            ),
            0,
        )

    monkeypatch.setattr(run_exam_pipeline, "orchestrate", fake_orchestrate)
    monkeypatch.setattr(
        run_exam_pipeline,
        "_resolve_channel",
        lambda _env_path, _channel: {
            "base_url": "https://offline.invalid/v1",
            "api_key": "offline-test-key",
            "globals": {},
        },
    )
    monkeypatch.setattr(
        run_exam_pipeline,
        "_load_preflight",
        lambda _args, _env_path, _stages: {"network_calls": 0, "requirement_conclusions": {}},
    )
    from examcard.knowledge.contracts import phase2r14_v25_live as live_contracts

    real_context_builder = live_contracts.build_live_sample_contexts

    def narrow_contexts(
        *,
        repository_root: Path,
        model: str,
        relay_base_url: str,
        manual_reissue_reason: str,
        sample_ids: tuple[str, ...],
    ):
        contexts = real_context_builder(
            repository_root=repository_root,
            model=model,
            relay_base_url=relay_base_url,
            manual_reissue_reason=manual_reissue_reason,
            sample_ids=sample_ids,
        )
        narrowed = []
        for context in contexts:
            fact_key, fact = next(
                (item for item in context.facts_by_key.items() if item[1].fact_type.value == "condition"),
                next(iter(context.facts_by_key.items())),
            )
            request = build_v25b_step2b_request(
                sample_id=context.sample_id,
                material_profile_id=context.step2b_request.material_profile_id,
                facts=(fact,),
                model=model,
                relay_base_url=relay_base_url,
            )
            response = build_v25b_source_fake_response(request=request)
            fixture_sample = SimpleNamespace(
                cards=(
                    SimpleNamespace(
                        primary_fact_keys=(fact_key,),
                        presentation=response.proposals[0].presentation,
                    ),
                )
            )
            narrowed.append(
                replace(
                    context,
                    step2b_request=request,
                    fixture_sample=fixture_sample,
                    facts_by_key={fact_key: fact},
                )
            )
        return tuple(narrowed)

    monkeypatch.setattr(live_contracts, "build_live_sample_contexts", narrow_contexts)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "gonglu_golden5_rehearsal",
            "--stages",
            "step2a,step2b,step3",
            "--allow-live",
            "--budget-usd",
            "10",
            "--run-label",
            "production-provider-r2",
            "--only-samples",
            "gonglu_24_table_formula",
            "--env-file",
            str(env_file),
            "--step3-contract",
            "v3",
            "--skip-preflight",
            "--max-attempts",
            "2",
        ],
    )

    assert run_exam_pipeline.main() == 0
    config = captured["config"]
    providers = captured["providers"]
    assert callable(providers["admission_projection_builder"])
    assert callable(providers["admission_source_validator"])
    assert config.max_attempts == 2

    sample_id = "gonglu_24_table_formula"
    base_reason = config.base_reason("step2b")
    retry_reason = f"{base_reason}-r2"
    context_builder = providers["context_builder"]
    failed_context = context_builder(sample_ids=(sample_id,), reason=base_reason)[0]
    successful_context = context_builder(sample_ids=(sample_id,), reason=retry_reason)[0]
    request = successful_context.step2b_request
    request_id = request.identity.request_id
    assert request_id == successful_context.step2b_request.identity.request_id
    outcome = {
        "samples": [
            {
                "sample_id": sample_id,
                "outcome": "success",
                "attempts": [
                    {"state": "failed", "reason": base_reason, "request_id": failed_context.step2b_request.identity.request_id},
                    {"state": "success", "reason": retry_reason, "request_id": request_id},
                ],
            }
        ]
    }
    assert outcome["samples"][0]["attempts"][1]["reason"].endswith("-r2")

    from examcard.knowledge.contracts.phase2r14_v25_live import _stage_request_payload, canonical_step2_sha256

    model_input, prompt, response_schema = _stage_request_payload(request, "step2b")
    request_doc = {
        "request": request.model_dump(mode="json", exclude_computed_fields=True),
        "model_input": model_input,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "response_schema_sha256": canonical_step2_sha256(response_schema),
    }
    response = build_v25b_source_fake_response(request=request)
    record = {
        "state": "success",
        "request_id": request_id,
        "stage": "step2b",
        "response": response.model_dump(mode="json"),
        "response_sha256": hashlib.sha256(
            json.dumps(response.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "http_status": 200,
        "provider_request_id": "offline-provider-success-r2",
        "latency_ms": 1.0,
        "schema_valid": True,
        "forensics": None,
    }
    run_root = tmp_path / "live-run"
    request_path = run_root / "step2b" / "requests" / f"{request_id.replace(':', '_')}.json"
    record_path = run_root / "step2b" / "records" / f"{request_id.replace(':', '_')}.json"
    cache_path = run_root / "step2b" / "cache" / f"{request.identity.cache_key.replace(':', '_')}.json"
    request_path.parent.mkdir(parents=True)
    record_path.parent.mkdir(parents=True)
    cache_path.parent.mkdir(parents=True)
    request_path.write_text(json.dumps(request_doc, ensure_ascii=False), encoding="utf-8")
    record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    cache = {
        "request_id": request_id,
        "state": "success",
        "record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
    }
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    projections = providers["admission_projection_builder"](run_root=run_root, outcome=outcome)
    assert len(projections) == 1
    source_rows = providers["admission_source_provenance"]
    assert len(source_rows) == 1
    row = source_rows[0]
    assert row["successful_attempt_reason"] == retry_reason
    assert row["request_id"] == request_id
    assert row["request_path"] == "step2b/requests/" + request_path.name
    assert row["record_path"] == "step2b/records/" + record_path.name
    assert row["cache_path"] == "step2b/cache/" + cache_path.name
    assert row["request_file_sha256"] == hashlib.sha256(request_path.read_bytes()).hexdigest()
    assert row["record_file_sha256"] == hashlib.sha256(record_path.read_bytes()).hexdigest()
    assert row["cache_file_sha256"] == hashlib.sha256(cache_path.read_bytes()).hexdigest()
    assert row["cache_request_id"] == request_id
    assert row["cache_record_sha256"] == cache["record_sha256"]

    providers["admission_source_validator"](
        run_root=run_root,
        queue_doc={"source_provenance": source_rows},
        queue_items=tuple(projections[0].queue_items),
    )

    request_path.write_bytes(request_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Step2B source provenance drifted"):
        providers["admission_source_validator"](
            run_root=run_root,
            queue_doc={"source_provenance": source_rows},
            queue_items=tuple(projections[0].queue_items),
        )
