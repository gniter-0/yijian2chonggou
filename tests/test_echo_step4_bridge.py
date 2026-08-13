"""P8 补链离线测试（规范 = echo_step4_bridge_design.md §6 离线组；零外呼，全合成语料）。

覆盖：新模型互锁/防篡改/防注入、评审文件契约往返（undecided/伪造负例/released）、
授权签发、echo derive 互证、装载接缝双分支、>16 卡证伪 Literal 上限、
prompt 构建器 duck 契约实证（path-load 真 pilot 模块调 source_prompt_v2）、
text-gate echo 真值适配。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from examcard.common.hashing import canonical_hash, hash_model
from examcard.compilers.step3_card import compile_reviewed_fixture
from examcard.compilers.step4_echo_bridge import (
    ECHO_REVIEW_SHEET_NAME,
    EchoStep4Bundle,
    build_echo_release_fixture,
    derive_step4_bundle_echo,
    emit_echo_step3_review_sheet,
    load_step4_bundle_source,
)
from examcard.gates.review.echo_step3_review import (
    ingest_echo_step3_review,
    issue_echo_batch_handoff,
)
from examcard.gates.review.step4_text_gate import extract_truths_from_echo_release
from examcard.knowledge.contracts.echo_release import (
    EchoBatchReleaseCard,
    EchoBatchReviewedReleaseFixture,
    EchoBatchStep4Handoff,
    EchoReleaseVisibleText,
    load_echo_release_fixture,
)
from tests.fixtures.compilers_synthetic import _hex, make_reviewed_typed_evidence_fixture

_REAL_REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 合成语料装配（typed fixture → 假 echo run → 评审底稿）
# ---------------------------------------------------------------------------


def _seed_typed_fixture(root: Path) -> tuple[Path, str]:
    fixture = make_reviewed_typed_evidence_fixture()
    path = root / "typed-fixture.json"
    path.write_text(json.dumps(fixture.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    import hashlib

    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_echo_run(root: Path, typed_fixture_path: Path) -> Path:
    import hashlib

    typed_sha = hashlib.sha256(typed_fixture_path.read_bytes()).hexdigest()
    from examcard.knowledge.contracts.admission_fixtures import ReviewedTypedEvidenceFixture

    fixture = ReviewedTypedEvidenceFixture.model_validate_json(typed_fixture_path.read_text(encoding="utf-8"))
    compilation = compile_reviewed_fixture(fixture, source_fixture_sha256=typed_sha)
    run_dir = root / "echo-run"
    for sequence, contract in enumerate(compilation.contracts, start=1):
        card_dir = run_dir / "step3" / "cards" / f"{sequence:02d}_{contract.contract_id.rsplit(':', 1)[-1]}"
        card_dir.mkdir(parents=True, exist_ok=True)
        (card_dir / "attempt-1.response.raw.json").write_bytes(
            json.dumps({"echo": contract.contract_id}).encode("utf-8")
        )
        (card_dir / "attempt-1.gate.json").write_text(json.dumps({"valid": True}), encoding="utf-8")
    return run_dir


def _backfill(sheet_path: Path, *, approve_all: bool = False) -> None:
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    for row in sheet["cards"]:
        if approve_all or row["presentation"] in {"table", "constraint", "formula", "diagram"}:
            row["disposition"] = "approved"
            row["reviewer"] = "b2-reviewer"
            row["basis"] = "逐卡对照 typed 契约复核通过"
            row["b2_review"] = {"crop_sha256": _hex(f"crop:{row['card_seq']}"), "checked_at": "2026-08-13T04:00:00Z"}
        else:
            row["disposition"] = "rejected"
            row["reviewer"] = "b2-reviewer"
            row["basis"] = "结构缺陷，回重发道"
            row["b2_review"] = {"crop_sha256": _hex(f"crop:{row['card_seq']}")}
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")


def _issued(tmp_path: Path) -> tuple[EchoBatchReviewedReleaseFixture, EchoBatchStep4Handoff, Path, Path]:
    typed_path, _sha = _seed_typed_fixture(tmp_path)
    run_dir = _seed_echo_run(tmp_path, typed_path)
    sheet_path = emit_echo_step3_review_sheet(echo_run_dir=run_dir, typed_fixture_path=typed_path, out_dir=tmp_path)
    _backfill(sheet_path, approve_all=True)
    outcome = ingest_echo_step3_review(sheet_path=sheet_path)
    assert outcome["status"] == "released"
    release = build_echo_release_fixture(
        typed_fixture_path=typed_path,
        echo_run_dir=run_dir,
        review_sheet_path=sheet_path,
        approved_rows=outcome["released"],
        source_echo_run_id="test-echo-run",
    )
    handoff = issue_echo_batch_handoff(
        release=release, authorized_by="b2-reviewer", authorization_evidence={"scope": "batch", "sheet": "ok"}
    )
    return release, handoff, typed_path, run_dir


# ---------------------------------------------------------------------------
# 评审文件契约（§3.1）
# ---------------------------------------------------------------------------


def test_review_sheet_roundtrip_waiting_then_released(tmp_path: Path) -> None:
    typed_path, _ = _seed_typed_fixture(tmp_path)
    run_dir = _seed_echo_run(tmp_path, typed_path)
    sheet_path = emit_echo_step3_review_sheet(echo_run_dir=run_dir, typed_fixture_path=typed_path, out_dir=tmp_path)
    assert sheet_path.name == ECHO_REVIEW_SHEET_NAME
    waiting = ingest_echo_step3_review(sheet_path=sheet_path)
    assert waiting["status"] == "waiting_human"
    _backfill(sheet_path, approve_all=True)
    released = ingest_echo_step3_review(sheet_path=sheet_path)
    assert released["status"] == "released"
    assert len(released["released"]) == 5
    assert all(row["review_signoff_sha256"] for row in released["released"])


def test_review_sheet_forgery_guard_refuses_missing_evidence_block(tmp_path: Path) -> None:
    typed_path, _ = _seed_typed_fixture(tmp_path)
    run_dir = _seed_echo_run(tmp_path, typed_path)
    sheet_path = emit_echo_step3_review_sheet(echo_run_dir=run_dir, typed_fixture_path=typed_path, out_dir=tmp_path)
    _backfill(sheet_path, approve_all=True)
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"][0]["b2_review"] = None  # 冒签形态：只有 reviewer 字符串
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    outcome = ingest_echo_step3_review(sheet_path=sheet_path)
    assert outcome["status"] == "refused"
    assert "b2_review" in outcome["detail"]


def test_review_sheet_placeholder_reviewer_refused(tmp_path: Path) -> None:
    typed_path, _ = _seed_typed_fixture(tmp_path)
    run_dir = _seed_echo_run(tmp_path, typed_path)
    sheet_path = emit_echo_step3_review_sheet(echo_run_dir=run_dir, typed_fixture_path=typed_path, out_dir=tmp_path)
    _backfill(sheet_path, approve_all=True)
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet["cards"][0]["reviewer"] = "<待签名>"
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    assert ingest_echo_step3_review(sheet_path=sheet_path)["status"] == "refused"


# ---------------------------------------------------------------------------
# release / handoff 模型与构建器（§3.2/§3.3）
# ---------------------------------------------------------------------------


def test_release_builder_lineage_and_projections(tmp_path: Path) -> None:
    release, _handoff, typed_path, run_dir = _issued(tmp_path)
    import hashlib

    assert release.card_count == 5
    assert release.source_typed_fixture_sha256 == hashlib.sha256(typed_path.read_bytes()).hexdigest()
    assert tuple(card.card_index for card in release.cards) == (1, 2, 3, 4, 5)
    by_presentation = {card.render_presentation: card for card in release.cards}
    diagram = by_presentation["diagram"]
    assert diagram.diagram_node_ids and diagram.semantic_layout_edges
    labelled = {item.semantic_node_id for item in diagram.visible_texts if item.semantic_node_id}
    assert set(diagram.diagram_node_ids) <= labelled
    table = by_presentation["table"]
    assert table.table_contracts and table.table_contracts[0].table_state == "reviewed_grid"
    assert all(cell.row >= 0 and cell.column >= 0 for cell in table.table_contracts[0].cells)
    # echo record 谱系：与盘上实录逐字节一致
    for card in release.cards:
        record = run_dir / "step3" / "cards" / f"{card.card_index:02d}_{card.source_card_id.rsplit(':', 1)[-1]}" / "attempt-1.response.raw.json"
        assert card.echo_record_sha256 == hashlib.sha256(record.read_bytes()).hexdigest()


def test_release_fixture_tamper_detection(tmp_path: Path) -> None:
    release, _h, _t, _r = _issued(tmp_path)
    payload = release.model_dump(mode="json")
    payload["cards"][0]["reviewer"] = "someone-else"
    with pytest.raises(Exception):
        EchoBatchReviewedReleaseFixture.model_validate(payload)


def test_handoff_issuance_guards(tmp_path: Path) -> None:
    release, handoff, _t, _r = _issued(tmp_path)
    assert handoff.batch_id == release.batch_id
    assert handoff.source_release_sha256 == release.fixture_sha256
    assert all(item.disposition == "pending" for item in handoff.cards)
    with pytest.raises(ValueError, match="evidence block"):
        issue_echo_batch_handoff(release=release, authorized_by="b2-reviewer", authorization_evidence={})
    with pytest.raises(ValueError, match="signature"):
        issue_echo_batch_handoff(release=release, authorized_by="<unsigned>", authorization_evidence={"ok": 1})


def test_seventeen_card_batch_expressible() -> None:
    """>16 卡在类型层可表达（对照 r319 Literal[16] 钉扎——P8 的核心证伪）。"""

    cards = []
    for index in range(1, 18):
        payload = {
            "card_index": index,
            "source_card_id": f"step3-card-contract:{index:024x}",
            "source_candidate_id": f"candidate:test:{index}",
            "sample_id": "subject:test",
            "source_presentation": "constraint",
            "render_presentation": "constraint",
            "fact_ids": (f"fact:test:{index}",),
            "visible_texts": (
                EchoReleaseVisibleText(
                    element_id=f"element:test:{index}",
                    visible_text=f"条款{index}",
                    fact_id=f"fact:test:{index}",
                    source_order=1,
                    semantic_node_id=None,
                ),
            ),
            "diagram_node_ids": (),
            "semantic_layout_edges": (),
            "table_contracts": (),
            "source_typed_fixture_sha256": _hex("typed"),
            "source_contract_sha256": _hex(f"contract:{index}"),
            "echo_record_sha256": _hex(f"record:{index}"),
            "review_signoff_sha256": _hex(f"signoff:{index}"),
            "reviewer": "b2-reviewer",
        }
        cards.append(hash_model(EchoBatchReleaseCard, payload, "card_sha256"))
    fixture_payload = {
        "batch_id": f"echo-batch:{'a' * 24}",
        "source_typed_fixture_path": "tmp/typed.json",
        "source_typed_fixture_sha256": _hex("typed"),
        "source_echo_run_id": "run-17",
        "review_sheet_sha256": _hex("sheet"),
        "card_count": 17,
        "cards": tuple(cards),
    }
    fixture = hash_model(EchoBatchReviewedReleaseFixture, fixture_payload, "fixture_sha256")
    assert fixture.card_count == 17


# ---------------------------------------------------------------------------
# derive 与装载接缝（§3.4）
# ---------------------------------------------------------------------------


def test_derive_echo_bundle_interlocks(tmp_path: Path) -> None:
    release, handoff, _t, _r = _issued(tmp_path)
    bundle, view = derive_step4_bundle_echo(
        release, handoff, release_sha256=_hex("release-bytes"), handoff_sha256=_hex("handoff-bytes")
    )
    assert isinstance(bundle, EchoStep4Bundle)
    assert len(bundle.cards) == 5 and bundle.prior_reviewed_indexes == ()
    assert view.fact_count == 5
    tampered = handoff.model_dump(mode="json")
    tampered["source_release_sha256"] = _hex("other")
    tampered["handoff_sha256"] = canonical_hash({k: v for k, v in tampered.items() if k not in {"schema_version", "handoff_sha256"}})
    bad_handoff = EchoBatchStep4Handoff.model_validate(tampered)
    with pytest.raises(ValueError, match="sha mismatch"):
        derive_step4_bundle_echo(release, bad_handoff, release_sha256=_hex("x"), handoff_sha256=_hex("y"))


def test_load_step4_bundle_source_echo_branch(tmp_path: Path) -> None:
    release, handoff, _t, _r = _issued(tmp_path)
    release_path = tmp_path / "echo-batch-release.json"
    handoff_path = tmp_path / "echo-batch-handoff.json"
    release_path.write_text(json.dumps(release.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    handoff_path.write_text(json.dumps(handoff.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    bundle, view = load_step4_bundle_source(
        source="echo-batch", release_fixture_path=release_path, handoff_path=handoff_path
    )
    assert len(bundle.cards) == 5 and view.fact_count == 5
    reloaded = load_echo_release_fixture(release_path)
    assert reloaded.fixture_sha256 == release.fixture_sha256


def test_prompt_builder_duck_contract_with_real_pilot_module(tmp_path: Path) -> None:
    """echo 卡直接喂真 pilot prompt 构建器（§3.4 判据的实证：无需触碰 pilot 代码）。"""

    release, _handoff, _t, _r = _issued(tmp_path)
    module_path = _REAL_REPO / "migration" / "run_pilot_gonglu5_step4.py"
    spec = importlib.util.spec_from_file_location("step4_pilot_under_test", module_path)
    assert spec is not None and spec.loader is not None
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    by_presentation = {card.render_presentation: card for card in release.cards}
    diagram_prompt = pilot.source_prompt_v2(by_presentation["diagram"])
    assert "NODES" in diagram_prompt and "EDGES" in diagram_prompt
    table_prompt = pilot.source_prompt_v2(by_presentation["table"])
    assert "TABLE_GRID_BEGIN" in table_prompt
    for presentation in ("constraint", "formula", "ordered_list"):
        assert pilot.source_prompt_v2(by_presentation[presentation]).strip()


# ---------------------------------------------------------------------------
# 词级门禁 echo 真值适配（§3.5）
# ---------------------------------------------------------------------------


def test_text_gate_truths_from_echo_release(tmp_path: Path) -> None:
    release, _handoff, _t, _r = _issued(tmp_path)
    truths = extract_truths_from_echo_release(release)
    assert len(truths) == 5
    table_card = next(card for card in release.cards if card.render_presentation == "table")
    assert truths[table_card.source_card_id] == tuple(item.visible_text for item in table_card.visible_texts)
