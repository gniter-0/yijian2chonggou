"""Typed Echo Release V2 contract tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from examcard.knowledge.contracts.echo_release_v2 import (
    EchoBatchReleaseCardV2,
    EchoStep3ReviewSheetV2,
    review_sheet_binding_hash,
)
from tests.fixtures.step3_v3 import approve_sheet, compile_v3, seed_v3_echo_run
from examcard.compilers.step4_echo_bridge_v2 import emit_echo_step3_review_sheet_v2


def test_review_sheet_v2_binds_file_and_authority_hashes(tmp_path: Path) -> None:
    typed_path, fixture, file_sha, compilation = compile_v3(tmp_path)
    run_dir = seed_v3_echo_run(tmp_path, compilation)
    sheet_path = emit_echo_step3_review_sheet_v2(
        echo_run_dir=run_dir, typed_fixture_path=typed_path, out_dir=tmp_path
    )
    sheet = EchoStep3ReviewSheetV2.model_validate_json(sheet_path.read_text(encoding="utf-8"))

    assert sheet.source_typed_fixture_file_sha256 == file_sha
    assert sheet.source_typed_fixture_sha256 == fixture.fixture_sha256
    assert sheet.source_typed_fixture_file_sha256 != sheet.source_typed_fixture_sha256
    assert sheet.cards[0].presentation == "threshold"
    assert sheet.cards[0].source_typed_fixture_sha256 == fixture.fixture_sha256

    approve_sheet(sheet_path)
    backfilled = EchoStep3ReviewSheetV2.model_validate_json(sheet_path.read_text(encoding="utf-8"))
    assert backfilled.sheet_sha256 == sheet.sheet_sha256
    assert review_sheet_binding_hash(backfilled) == sheet.sheet_sha256


def test_release_card_rejects_threshold_as_renderer_presentation() -> None:
    with pytest.raises(ValueError, match="projection mismatch|Input should"):
        EchoBatchReleaseCardV2.model_validate(
            {
                "card_index": 1,
                "source_card_id": "step3-card-contract:" + "a" * 24,
                "source_candidate_id": "candidate:test:1",
                "sample_id": "subject:test:1",
                "source_presentation": "threshold",
                "render_presentation": "threshold",
                "fact_ids": ("fact:test:1",),
                "visible_texts": (
                    {
                        "element_id": "element:test:1",
                        "visible_text": "阈值",
                        "fact_id": "fact:test:1",
                        "source_order": 1,
                        "source_role": "threshold_token",
                        "text_sha256": "1" * 64,
                        "provenance": {
                            "subject_id": "subject:test:1",
                            "record_id": "record:test:1",
                            "fact_id": "fact:test:1",
                            "evidence_id": "evidence:test:1",
                            "anchor_ids": ("anchor:test:1",),
                            "page_ref_id": "page:test:1",
                            "page": 1,
                            "image_sha256": "2" * 64,
                            "source_manifest_sha256": "3" * 64,
                        },
                    },
                ),
                "source_typed_fixture_file_sha256": "a" * 64,
                "source_typed_fixture_sha256": "b" * 64,
                "source_contract_sha256": "c" * 64,
                "echo_record_sha256": "d" * 64,
                "review_signoff_sha256": "e" * 64,
                "reviewer": "b2-reviewer",
                "card_sha256": "f" * 64,
            }
        )
