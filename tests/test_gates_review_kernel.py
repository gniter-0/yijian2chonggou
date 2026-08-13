"""gates/review 内核不变量：决策/清单/审计/fixture/队列（distill 设计 §4 清单）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from examcard.gates.review.audit import DecisionAudit, build_decision_audit
from examcard.gates.review.contracts import validate_typed_contract_binding
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
from examcard.knowledge.contracts.admission_fixtures import DiagramContract, DiagramEdge
from tests.fixtures.admission_kernel import (
    ANCHOR_IDS,
    EVIDENCE_ID,
    FACT_ID,
    RECORD_ID,
    SOURCE_MANIFEST_SHA256,
    SUBJECT_ID,
    KernelCandidate,
    make_diagram_contract,
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


class TestP4bEdgedDiagramGate:
    """P4b：r84 门放行布局边（r87 先例特判蒸馏）+ 边界 fail-closed 钉扎。"""

    @staticmethod
    def _diagram_decision(contract: DiagramContract):
        item = make_queue_item(presentation="diagram")
        return make_human_decision(item, contract=contract), item

    @staticmethod
    def _duck_decision(contract: object, item) -> SimpleNamespace:
        """鸭子决策载体：直探内核门（validate_typed_contract_binding 的 decision: Any 面）。"""
        return SimpleNamespace(
            typed_contract=contract,
            presentation="diagram",
            subject_id=SUBJECT_ID,
            record_id=RECORD_ID,
            fact_id=FACT_ID,
            evidence_id=EVIDENCE_ID,
            anchor_ids=ANCHOR_IDS,
            source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            pages=item.pages,
        )

    def test_layout_only_edged_contract_passes_the_gate(self) -> None:
        decision, item = self._diagram_decision(make_diagram_contract())
        validate_human_decision(decision, item, KernelCandidate())  # 无拒即证

    def test_edge_crossed_anchor_rejected(self) -> None:
        contract = make_diagram_contract(edge_provenance=make_provenance(anchor_ids=("anchor:test:0002",)))
        decision, item = self._diagram_decision(contract)
        with pytest.raises(ValueError, match="crossed anchor or source manifest"):
            validate_human_decision(decision, item, KernelCandidate())

    def test_edge_crossed_manifest_rejected(self) -> None:
        contract = make_diagram_contract(edge_provenance=make_provenance(source_manifest_sha256="f" * 64))
        decision, item = self._diagram_decision(contract)
        with pytest.raises(ValueError, match="crossed anchor or source manifest"):
            validate_human_decision(decision, item, KernelCandidate())

    def test_edge_crossed_page_rejected(self) -> None:
        contract = make_diagram_contract(edge_provenance=make_provenance(page_ref_id="page:test:0002"))
        decision, item = self._diagram_decision(contract)
        with pytest.raises(ValueError, match="crossed source page or image hash"):
            validate_human_decision(decision, item, KernelCandidate())

    def test_edge_crossed_fact_evidence_rejected(self) -> None:
        contract = make_diagram_contract(edge_provenance=make_provenance(fact_id="fact:test:0002"))
        decision, item = self._diagram_decision(contract)
        with pytest.raises(ValueError, match="crossed Fact/Evidence binding"):
            validate_human_decision(decision, item, KernelCandidate())

    def test_diagram_edge_layout_only_cannot_be_disarmed(self) -> None:
        # 语义轴钉扎：文本授权边不可表征（layout_only 是 Literal[True]，模型层即拒）。
        payload = make_diagram_contract().edges[0].model_dump(mode="json")
        payload["layout_only"] = False
        with pytest.raises(ValidationError, match="layout_only"):
            DiagramEdge.model_validate(payload)

    def test_flagless_non_edge_element_still_default_denied(self) -> None:
        # 卫兵钉（A 签追加②）：特判仅限 DiagramEdge——无六旗标的非边元素仍被
        # getattr 缺省拒，防特判分支日后被泛化。合法模型不可表征无旗标文本元素，
        # 故经 model_construct 绕过验证直探门本体。
        item = make_queue_item(presentation="diagram")
        stub = SimpleNamespace(provenance=make_provenance())
        contract = DiagramContract.model_construct(nodes=(stub,), edges=())
        with pytest.raises(ValueError, match="unauthorized derivation or noise flag"):
            validate_typed_contract_binding(self._duck_decision(contract, item), item)

    def test_bad_flag_text_element_still_denied(self) -> None:
        # 卫兵钉（A 签追加③）：坏旗标文本元素（watermark=True）仍拒——Literal 表征
        # 使其无法经验证构造，同法直探门的 getattr 扫描本体。
        item = make_queue_item(presentation="diagram")
        bad = SimpleNamespace(
            provenance=make_provenance(),
            human_confirmed=True,
            ocr_derived=False,
            assertion_derived=False,
            filename_derived=False,
            legacy_hint_derived=False,
            watermark=True,
        )
        contract = DiagramContract.model_construct(nodes=(bad,), edges=())
        with pytest.raises(ValueError, match="unauthorized derivation or noise flag"):
            validate_typed_contract_binding(self._duck_decision(contract, item), item)


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
