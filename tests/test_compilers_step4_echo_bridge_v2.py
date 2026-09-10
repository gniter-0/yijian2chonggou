"""Step 3 V3 → Echo Release V2 → Step 4 bridge tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from examcard.compilers.step4_echo_bridge_v2 import (
    build_echo_release_fixture_v2,
    derive_step4_bundle_echo_v2,
    emit_echo_step3_review_sheet_v2,
    load_step4_bundle_source_v2,
)
from examcard.compilers.step4_contract_derive import render_presentation_for_source
from examcard.gates.review.echo_step3_review_v2 import (
    ingest_echo_step3_review_v2,
    issue_echo_batch_handoff_v2,
)
from tests.fixtures.step3_v3 import approve_sheet, compile_v3, seed_v3_echo_run


def _issued_v2(tmp_path: Path):
    typed_path, fixture, file_sha, compilation = compile_v3(tmp_path)
    run_dir = seed_v3_echo_run(tmp_path, compilation)
    sheet_path = emit_echo_step3_review_sheet_v2(
        echo_run_dir=run_dir, typed_fixture_path=typed_path, out_dir=tmp_path
    )
    approve_sheet(sheet_path)
    outcome = ingest_echo_step3_review_v2(sheet_path=sheet_path)
    assert outcome["status"] == "released"
    release = build_echo_release_fixture_v2(
        typed_fixture_path=typed_path,
        echo_run_dir=run_dir,
        review_sheet_path=sheet_path,
        approved_rows=outcome["released"],
        source_echo_run_id="echo-run-v2",
    )
    handoff = issue_echo_batch_handoff_v2(
        release=release,
        authorized_by="b2-reviewer",
        authorization_evidence={"scope": "batch", "sheet": outcome["sheet_sha256"]},
        ingest_receipt=outcome["ingest_receipt"],
    )
    return typed_path, fixture, file_sha, run_dir, sheet_path, release, handoff


def test_v2_release_keeps_threshold_source_but_maps_only_renderer(tmp_path: Path) -> None:
    _typed_path, fixture, file_sha, _run_dir, _sheet, release, handoff = _issued_v2(tmp_path)
    card = release.cards[0]

    assert card.source_presentation == "threshold"
    assert card.render_presentation == "constraint"
    assert all(item.source_role == "threshold_token" for item in card.visible_texts)
    assert release.source_typed_fixture_file_sha256 == file_sha
    assert release.source_typed_fixture_sha256 == fixture.fixture_sha256
    assert handoff.source_typed_fixture_file_sha256 == file_sha
    assert handoff.source_typed_fixture_sha256 == fixture.fixture_sha256


def test_v2_bundle_uses_release_and_handoff_file_hashes(tmp_path: Path) -> None:
    typed_path, _fixture, _file_sha, _run_dir, _sheet, release, handoff = _issued_v2(tmp_path)
    release_path = tmp_path / "release-v2.json"
    handoff_path = tmp_path / "handoff-v2.json"
    release_path.write_text(json.dumps(release.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    handoff_path.write_text(json.dumps(handoff.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")

    bundle, view = load_step4_bundle_source_v2(
        release_fixture_path=release_path, handoff_path=handoff_path
    )
    assert bundle.source_release_sha256 == hashlib.sha256(release_path.read_bytes()).hexdigest()
    assert bundle.source_handoff_sha256 == hashlib.sha256(handoff_path.read_bytes()).hexdigest()
    assert bundle.source_handoff_sha256 != handoff.source_handoff_sha256
    assert view.fact_count == 1

    same_bundle, same_view = derive_step4_bundle_echo_v2(
        release,
        handoff,
        release_sha256=bundle.source_release_sha256,
        handoff_sha256=bundle.source_handoff_sha256,
    )
    assert same_bundle.bundle_sha256 == bundle.bundle_sha256
    assert same_view == view
    assert typed_path.is_file()


def test_v2_bundle_rejects_handoff_fixture_hash_crossing(tmp_path: Path) -> None:
    _typed_path, _fixture, _file_sha, _run_dir, _sheet, release, handoff = _issued_v2(tmp_path)
    tampered = handoff.model_copy(update={"source_typed_fixture_file_sha256": "0" * 64})
    with pytest.raises(ValueError, match="fixture file hash"):
        derive_step4_bundle_echo_v2(release, tampered, release_sha256="a" * 64, handoff_sha256="b" * 64)


def test_v2_release_rejects_echo_record_replaced_after_review(tmp_path: Path) -> None:
    typed_path, _fixture, _file_sha, run_dir, sheet_path, _release, _handoff = _issued_v2(tmp_path)
    card_dir = next((run_dir / "step3" / "cards").iterdir())
    response_path = card_dir / "attempt-1.response.raw.json"
    response_path.write_bytes(response_path.read_bytes() + b"tampered")
    outcome = ingest_echo_step3_review_v2(sheet_path=sheet_path)
    with pytest.raises(ValueError, match="response record changed"):
        build_echo_release_fixture_v2(
            typed_fixture_path=typed_path,
            echo_run_dir=run_dir,
            review_sheet_path=sheet_path,
            approved_rows=outcome["released"],
            source_echo_run_id="echo-run-v2-tampered",
        )


@pytest.mark.parametrize(
    ("source", "render"),
    [
        ("diagram", "diagram"),
        ("table", "table"),
        ("ordered_list", "ordered_list"),
        ("constraint", "constraint"),
        ("threshold", "constraint"),
        ("formula", "formula"),
    ],
)
def test_renderer_boundary_helper_is_the_only_threshold_projection(source: str, render: str) -> None:
    assert render_presentation_for_source(source) == render
    with pytest.raises(ValueError, match="unsupported source presentation"):
        render_presentation_for_source("unknown")
