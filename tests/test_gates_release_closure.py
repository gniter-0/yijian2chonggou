"""gates.release.closure 单测（蓝本：旧仓 test_phase2r98_...py；R97/R96 盘面用例留旧仓）。"""
from __future__ import annotations

import pytest

from examcard.compilers.step3_card import compile_reviewed_fixture
from examcard.gates.release.closure import (
    AuthorityPolicy,
    DeterministicRelease,
    ExternalCheck,
    NonAuthoritativeModelCandidate,
    build_deterministic_release,
    submit_non_authoritative_candidate,
)
from tests.fixtures.compilers_synthetic import make_reviewed_typed_evidence_fixture

SOURCE_SHA = "4" * 64


@pytest.fixture(scope="module")
def release() -> DeterministicRelease:
    compilation = compile_reviewed_fixture(make_reviewed_typed_evidence_fixture(), source_fixture_sha256=SOURCE_SHA)
    return build_deterministic_release(
        compilation,
        source_fixture_path_label="tests/synthetic/reviewed-typed-evidence-fixture.json",
        expected_card_count=5,
    )


def test_bundle_binds_r9_draft_and_source(release: DeterministicRelease) -> None:
    assert release.bundle.card_count == 5
    assert release.bundle.source_fixture_sha256 == SOURCE_SHA
    assert release.bundle.draft_authority == "local_deterministic_compiler"
    assert release.bundle.reviewed_fixture_created is False
    assert release.bundle.step4_authorized is False


def test_offline_gate_valid_with_injected_checks(release: DeterministicRelease) -> None:
    assert release.gate.valid
    assert release.gate.failure_codes == ()
    assert release.gate.batch_generation_authorized is False


def test_injected_external_check_failure_fails_gate() -> None:
    compilation = compile_reviewed_fixture(make_reviewed_typed_evidence_fixture(), source_fixture_sha256=SOURCE_SHA)
    result = build_deterministic_release(
        compilation,
        source_fixture_path_label="tests/synthetic/reviewed-typed-evidence-fixture.json",
        r97_freeze_check=ExternalCheck(False, "frozen artifact drifted"),
        expected_card_count=5,
    )
    assert not result.gate.valid
    assert "r97-freeze" in result.gate.failure_codes


def test_regressions_are_thirteen_and_fail_closed(release: DeterministicRelease) -> None:
    assert len(release.regressions) == 13
    assert all(item.caught for item in release.regressions)


def test_candidate_isolation(release: DeterministicRelease) -> None:
    audit = submit_non_authoritative_candidate(
        NonAuthoritativeModelCandidate(candidate_sha256="7" * 64, proposed_payload_sha256="8" * 64),
        release.bundle,
    )
    assert audit.disposition == "ignored_non_authoritative"
    assert audit.draft_mutated is False
    with pytest.raises(ValueError, match="cross-sample"):
        submit_non_authoritative_candidate(
            NonAuthoritativeModelCandidate(
                candidate_sha256="9" * 64,
                proposed_payload_sha256="a" * 64,
                claimed_contract_ids=("step3-card-contract:ffffffffffffffffffffffff",),
            ),
            release.bundle,
        )


def test_authority_policy_cannot_escalate() -> None:
    with pytest.raises(Exception):
        AuthorityPolicy.model_validate({**AuthorityPolicy().model_dump(mode="json"), "model_can_mutate_draft": True})
