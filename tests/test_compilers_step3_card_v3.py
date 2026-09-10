"""Offline compiler tests for the threshold-capable Step 3 V3 contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from examcard.compilers.step3_card_v3 import compile_reviewed_fixture_v3
from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture
from tests.fixtures.compilers_synthetic import make_reviewed_typed_evidence_fixture
from tests.fixtures.step3_v3 import compile_v3, make_v2_fixture


def test_compiler_builds_a_valid_offline_threshold_batch(tmp_path: Path) -> None:
    _path, fixture, file_sha, compilation = compile_v3(tmp_path)

    assert compilation.gate.valid is True
    assert compilation.readiness.generic_step3_offline_ready is True
    assert compilation.batch.network_calls == 0
    assert compilation.report.network_calls == 0
    assert compilation.report.regression_caught_count == compilation.report.regression_count
    assert compilation.contracts[0].source_typed_fixture_file_sha256 == file_sha
    assert compilation.contracts[0].source_typed_fixture_sha256 == fixture.fixture_sha256
    assert compilation.prompt.source_typed_fixture_file_sha256 == file_sha
    assert compilation.prompt.source_typed_fixture_sha256 == fixture.fixture_sha256


def test_compiler_requires_v2_fixture_and_rejects_hash_interchange(tmp_path: Path) -> None:
    fixture = make_v2_fixture()
    with pytest.raises(ValueError, match="authority fixture hash"):
        compile_reviewed_fixture_v3(
            fixture,
            source_typed_fixture_file_sha256="a" * 64,
            source_typed_fixture_sha256="b" * 64,
        )

    v1_fixture: ReviewedTypedEvidenceFixture = make_reviewed_typed_evidence_fixture(("table",))
    with pytest.raises(TypeError, match="requires ReviewedTypedEvidenceFixtureV2"):
        compile_reviewed_fixture_v3(v1_fixture, source_typed_fixture_file_sha256="a" * 64)  # type: ignore[arg-type]


def test_compiler_regression_matrix_catches_threshold_alias_and_unknown_fields(tmp_path: Path) -> None:
    _path, _fixture, _file_sha, compilation = compile_v3(tmp_path)
    cases = {item.case_id: item for item in compilation.regressions}
    assert cases["unknown-field"].caught is True
    assert cases["threshold-layout-alias"].caught is True
    assert compilation.gate.failure_codes == ()
