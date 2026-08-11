"""gates/review 内核不变量：决策/清单/审计/fixture/队列（distill 设计 §4 清单）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from examcard.gates.review.audit import DecisionAudit, build_decision_audit
from examcard.gates.review.decisions import (
    HumanDecision,
    HumanDecisionManifest,
    empty_human_manifest,
    validate_human_decision,
)
from examcard.gates.review.dispositions import (
    assert_disposition_conserved,
    count_dispositions,
    derive_admission_basis,
    effective_disposition,
)
from examcard.gates.review.fixtures import ReviewedFixture, build_reviewed_candidate, build_reviewed_fixture
from examcard.gates.review.queue import queue_sha256, validate_queue_chain
from tests.fixtures.admission_kernel import (
    KernelCandidate,
    make_formula_contract,
    make_human_decision,
    make_manifest,
    make_provenance,
    make_queue_item,
    make_source_page,
)

QUEUE_SHA = "d" * 64


def test_effective_disposition_downgrades_browser_reviewed_only() -> None:
    assert effective_disposition("reviewed_candidate", browser_demo=True) == "manual_review"
    assert effective_disposition("blocked", browser_demo=True) == "blocked"
    assert effective_disposition("reviewed_candidate", browser_demo=False) == "reviewed_candidate"


def test_admission_basis_derivation() -> None:
    assert derive_admission_basis(None, browser_demo=False) == "no_production_human_decision"
    assert derive_admission_basis("reviewed_candidate", browser_demo=True) == "browser_demo_not_authority"
    assert derive_admission_basis("blocked", browser_demo=False) == "explicit_human_blocked"


def test_disposition_conservation_blocks_silent_upgrade() -> None:
    assert_disposition_conserved({"a": "manual_review"}, {"a": "manual_review"})
    assert_disposition_conserved({"a": "reviewed_candidate"}, {"a": "manual_review"})
    with pytest.raises(ValueError, match="conservation"):
        assert_disposition_conserved({"a": "manual_review"}, {"a": "reviewed_candidate"})
    with pytest.raises(ValueError, match="conservation"):
        assert_disposition_conserved({"a": "blocked"}, {"a": "not_applicable"})


def test_count_dispositions_rejects_unknown_state() -> None:
    assert count_dispositions(["blocked", "blocked"])["blocked"] == 2
    with pytest.raises(ValueError):
        count_dispositions(["approved"])


def test_queue_item_rejects_crossed_page_manifest() -> None:
    with pytest.raises(ValidationError, match="crossed source manifest"):
        make_queue_item(pages=(make_source_page(source_manifest_sha256="e" * 64),))


def test_queue_sha256_is_order_sensitive_and_deterministic() -> None:
    first = make_source_page()
    second = make_source_page(page_ref_id="page:test:0002", page=2)
    assert queue_sha256([first, second]) == queue_sha256([first, second])
    assert queue_sha256([first, second]) != queue_sha256([second, first])


def test_reviewed_decision_requires_each_confirmation() -> None:
    for flag in (
        "source_text_confirmed",
        "anchor_confirmed",
        "source_page_confirmed",
        "image_hash_confirmed",
        "typed_contract_confirmed",
        "production_fixture_authorized",
    ):
        with pytest.raises(ValidationError, match="five confirmations"):
            make_human_decision(**{flag: False})


def test_reviewed_decision_requires_complete_contract() -> None:
    with pytest.raises(ValidationError):
        make_human_decision(typed_contract=None, typed_contract_sha256=None)


def test_non_reviewed_decision_cannot_carry_authorized_contract() -> None:
    with pytest.raises(ValidationError, match="cannot carry"):
        make_human_decision(disposition="blocked", contract=make_formula_contract())


def test_browser_demo_cannot_pass_authority_model() -> None:
    with pytest.raises(ValidationError):
        make_human_decision(browser_demo=True)


def test_decision_identity_prefix_is_enforced() -> None:
    with pytest.raises(ValidationError, match="fresh identity"):
        make_human_decision(decision_id="decision:r83:ui:test-0001")


def test_decision_self_hash_tamper_is_rejected() -> None:
    decision = make_human_decision()
    payload = decision.model_dump(mode="json")
    payload["reason"] = "tampered reason text"
    with pytest.raises(ValidationError, match="hash mismatch"):
        HumanDecision.model_validate(payload)


def test_manifest_rejects_duplicate_and_crossed_decisions() -> None:
    decision = make_human_decision()
    with pytest.raises(ValidationError, match="unique"):
        make_manifest((decision, decision))
    with pytest.raises(ValidationError, match="crossed queue hash"):
        make_manifest((decision,), queue_sha256="e" * 64)


def test_empty_manifest_and_queue_chain() -> None:
    manifest = empty_human_manifest(QUEUE_SHA)
    assert manifest.decision_count == 0
    validate_queue_chain(QUEUE_SHA, [manifest])
    with pytest.raises(ValueError, match="queue hash chain"):
        validate_queue_chain("e" * 64, [manifest])


def test_strict_ingestion_binding_validators() -> None:
    item = make_queue_item()
    decision = make_human_decision(item)
    validate_human_decision(decision, item, KernelCandidate())
    with pytest.raises(ValueError, match="candidate identity"):
        validate_human_decision(decision, item, KernelCandidate("candidate:test:0002"))
    crossed_item = make_queue_item(fact_id="fact:test:0002")
    with pytest.raises(ValueError, match="crossed source identity"):
        validate_human_decision(decision, crossed_item, KernelCandidate())


def test_contract_binding_rejects_crossed_provenance() -> None:
    # 越界契约能通过模型自校验（哈希一致），但严格摄取的三方绑定校验必须拒绝。
    item = make_queue_item()
    crossed_contract = make_formula_contract(make_provenance(fact_id="fact:test:0002"))
    decision = make_human_decision(item, contract=crossed_contract)
    with pytest.raises(ValueError, match="crossed Fact/Evidence"):
        validate_human_decision(decision, item, KernelCandidate())


def test_audit_builder_derives_basis_and_downgrade() -> None:
    item = make_queue_item()
    pending = build_decision_audit(candidate_id="candidate:test:0001", queue_item=item)
    assert pending.admission_basis == "no_production_human_decision"
    assert pending.effective_disposition == "manual_review"
    decision = make_human_decision(item)
    browser = build_decision_audit(candidate_id="candidate:test:0001", queue_item=item, decision=decision, browser_demo=True)
    assert browser.effective_disposition == "manual_review"
    assert browser.admission_basis == "browser_demo_not_authority"
    explicit = build_decision_audit(candidate_id="candidate:test:0001", queue_item=item, decision=decision)
    assert explicit.effective_disposition == "reviewed_candidate"
    assert explicit.admission_basis == "explicit_human_reviewed_candidate"


def test_audit_model_blocks_browser_authorization() -> None:
    item = make_queue_item()
    decision = make_human_decision(item)
    audit = build_decision_audit(candidate_id="candidate:test:0001", queue_item=item, decision=decision)
    payload = audit.model_dump(mode="json")
    payload["browser_demo"] = True
    with pytest.raises(ValidationError, match="browser demo cannot authorize"):
        DecisionAudit.model_validate(payload)


def test_reviewed_fixture_roundtrip_and_forbidden_authorizations() -> None:
    item = make_queue_item()
    decision = make_human_decision(item)
    candidate = build_reviewed_candidate(item, "candidate:test:0001", decision)
    fixture = build_reviewed_fixture(QUEUE_SHA, (candidate,))
    assert fixture.fixture_authorized is True
    assert fixture.reviewed_candidate_count == 1
    payload = fixture.model_dump(mode="json")
    payload["cards_created"] = True
    with pytest.raises(ValidationError):
        ReviewedFixture.model_validate(payload)
    empty = build_reviewed_fixture(QUEUE_SHA, ())
    assert empty.fixture_authorized is False


def test_reviewed_fixture_rejects_duplicate_candidates() -> None:
    item = make_queue_item()
    decision = make_human_decision(item)
    candidate = build_reviewed_candidate(item, "candidate:test:0001", decision)
    with pytest.raises(ValidationError, match="unique"):
        build_reviewed_fixture(QUEUE_SHA, (candidate, candidate))


def test_non_reviewed_decision_cannot_build_candidate() -> None:
    item = make_queue_item()
    blocked = make_human_decision(item, disposition="blocked")
    with pytest.raises(ValueError, match="only reviewed decisions"):
        build_reviewed_candidate(item, "candidate:test:0001", blocked)
