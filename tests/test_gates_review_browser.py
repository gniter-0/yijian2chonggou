"""浏览器界不可权威：raw 草稿信封 / 分类降格 / 进度快照。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from examcard.gates.review.browser import (
    PENDING_DECISION_HASH,
    PENDING_MANIFEST_HASH,
    Progress,
    RawReviewDecision,
    RawReviewDraftManifest,
    build_progress,
    classify_browser_decision,
    normalize_raw_draft,
    summarize_browser_input,
)
from examcard.gates.review.decisions import HumanDecision
from tests.fixtures.admission_kernel import make_human_decision

QUEUE_SHA = "d" * 64


def _raw_decision(**overrides: object) -> RawReviewDecision:
    payload: dict[str, object] = {
        "draft_id": "draft:ui:0001",
        "candidate_id": "candidate:test:0001",
        "queue_item_id": "queue:test:0001",
        "queue_sha256": QUEUE_SHA,
        "disposition": "manual_review",
        "reviewer": "tester",
        "reason": "浏览器界初审记录",
        "recorded_at": "2026-08-12T00:00:00Z",
    }
    payload.update(overrides)
    return RawReviewDecision.model_validate(payload)


def test_raw_decision_hash_is_structurally_pending() -> None:
    decision = _raw_decision()
    assert decision.decision_sha256 == PENDING_DECISION_HASH
    with pytest.raises(ValidationError):
        _raw_decision(decision_sha256="0" * 64)
    with pytest.raises(ValidationError):
        _raw_decision(browser_demo=False)


def test_raw_export_shape_cannot_validate_as_authority_model() -> None:
    decision = _raw_decision()
    payload = decision.model_dump(mode="json")
    with pytest.raises(ValidationError):
        HumanDecision.model_validate(payload)


def test_raw_manifest_rejects_duplicates_and_crossed_queue() -> None:
    first = _raw_decision()
    base = {"manifest_id": "manifest:ui:0001", "queue_sha256": QUEUE_SHA, "decisions": (first,), "decision_count": 1}
    manifest = RawReviewDraftManifest.model_validate(base)
    assert manifest.manifest_sha256 == PENDING_MANIFEST_HASH
    duplicate = {**base, "decisions": (first, first), "decision_count": 2}
    with pytest.raises(ValidationError, match="unique"):
        RawReviewDraftManifest.model_validate(duplicate)
    crossed = {**base, "queue_sha256": "e" * 64}
    with pytest.raises(ValidationError, match="crossed queue hash"):
        RawReviewDraftManifest.model_validate(crossed)


def test_classify_browser_decision_downgrades_reviewed() -> None:
    reviewed = _raw_decision(
        disposition="reviewed_candidate",
        source_text_confirmed=True,
        anchor_confirmed=True,
        source_page_confirmed=True,
        image_hash_confirmed=True,
        typed_contract_confirmed=True,
    )
    assert classify_browser_decision(reviewed) == ("manual_review", "browser_demo_reviewed_candidate_blocked")
    assert classify_browser_decision(_raw_decision(disposition="blocked"))[0] == "blocked"


def test_classify_browser_decision_requires_confirmations() -> None:
    incomplete = _raw_decision(disposition="reviewed_candidate", source_text_confirmed=True)
    with pytest.raises(ValueError, match="five confirmations"):
        classify_browser_decision(incomplete)


def test_normalize_raw_draft_rebinds_and_fails_closed() -> None:
    raw_manifest = RawReviewDraftManifest.model_validate(
        {"manifest_id": "manifest:ui:0001", "queue_sha256": QUEUE_SHA, "decisions": (_raw_decision(),), "decision_count": 1}
    )
    built = normalize_raw_draft(
        raw_manifest,
        {"candidate:test:0001": object()},
        expected_queue_sha256=QUEUE_SHA,
        decision_builder=lambda raw, candidate: make_human_decision(disposition=raw.disposition),
    )
    assert len(built) == 1 and built[0].disposition == "manual_review"
    with pytest.raises(ValueError, match="unknown candidate"):
        normalize_raw_draft(raw_manifest, {}, expected_queue_sha256=QUEUE_SHA, decision_builder=lambda raw, candidate: raw)
    with pytest.raises(ValueError, match="crossed expected queue hash"):
        normalize_raw_draft(raw_manifest, {}, expected_queue_sha256="e" * 64, decision_builder=lambda raw, candidate: raw)


def test_summarize_browser_input_rejects_all_demo_for_production() -> None:
    summary = summarize_browser_input([_raw_decision(), _raw_decision(draft_id="draft:ui:0002")])
    assert summary["browser_demo_count"] == 2
    assert summary["rejected_for_production_count"] == 2
    assert summary["production_authorized_count"] == 0


def test_progress_counts_are_sealed_and_consistent() -> None:
    progress = build_progress(
        QUEUE_SHA,
        [
            {
                "candidate_id": "candidate:test:0001",
                "queue_item_id": "queue:test:0001",
                "subject_id": "subject.gonglu",
                "presentation": "formula",
                "disposition": "manual_review",
                "explicit_decision": True,
                "draft_id": "draft:ui:0001",
                "reviewer": "tester",
            }
        ],
    )
    assert progress.manual_review_count == 1
    payload = progress.model_dump(mode="json")
    payload["reviewed_candidate_count"] = 1
    with pytest.raises(ValidationError, match="counts mismatch"):
        Progress.model_validate(payload)
