"""step4 词级/标点/覆盖率门禁离线单测（改进 A1；全部伪转写、零外呼）。

三档口径与实例对齐 b2 评审实录（P3_彩排b2_step4十六图人工评审_20260813.md）：
D1 术语单字符替换 → hard_fail；D2 整条漏渲染 → hard_fail；#08-r2 标点替换 → warn。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from examcard.gates.review.step4_text_gate import (
    Step4TextGateReport,
    build_step4_text_gate_report,
    compare_line,
    evaluate_card_transcript,
    extract_truths_from_release_fixture,
    resolve_card_image,
    tokenize_visible_text,
)

_REAL_REPO = Path(__file__).resolve().parents[1]
_R2242 = _REAL_REPO / "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml"


# ---------------------------------------------------------------------------
# 分词与行内比对
# ---------------------------------------------------------------------------


def test_tokenize_splits_semantic_and_punctuation() -> None:
    semantic, punct = tokenize_visible_text("隧道跨度 14≤B<18，Ⅰ级 BQ＞550；")
    assert "14" in semantic and "18" in semantic and "≤" in semantic and "<" in semantic
    assert "隧" in semantic and "道" in semantic
    # NFKC 归一化后全角标点折叠为半角等值形（A2 契约③）。
    assert punct == (",", ";")
    # ＞（全角比较符）折叠为 > 后按语义面计，不落标点面。
    assert ">" in semantic and "＞" not in punct and ">" not in punct


def test_fullwidth_halfwidth_equivalence_is_pass() -> None:
    # A2 契约③：全/半角等值形等同（＜/<、括号/冒号宽度、逗号宽度）。
    assert compare_line("14＜B≤18（米）：说明，完", "14<B≤18(米):说明,完") is None
    # 等值形折叠后**符号替换**仍相异：，→； 照记 warn。
    finding = compare_line("甲，乙", "甲；乙")
    assert finding is not None and finding.severity == "warn"


def test_duplicate_rendered_truth_line_is_warn_injective_coverage() -> None:
    # A2 契约④：覆盖单射——真值单元恰配一次，重复渲染的第二份记 line_extra warn。
    truth = ("重复出现的行",)
    transcript = "重复出现的行\n重复出现的行"
    verdict, coverage, matched, findings = evaluate_card_transcript(truth_lines=truth, transcript=transcript)
    assert (verdict, coverage, matched) == ("warn", 1.0, 1)
    assert [item.code for item in findings] == ["line_extra"]


def test_compare_line_term_single_char_diff_is_hard_fail() -> None:
    # b2-D1 实例：滑坡地段路基 → 滑坡地质路基（考点术语单字符错）。
    finding = compare_line("滑坡地段路基", "滑坡地质路基")
    assert finding is not None
    assert finding.code == "semantic_diff"
    assert finding.severity == "hard_fail"


def test_compare_line_number_and_operator_diff_is_hard_fail() -> None:
    assert compare_line("Ⅰ级 BQ＞550", "Ⅰ级 BQ≥550").severity == "hard_fail"
    assert compare_line("预压 1.05～1.1 倍", "预压 1.5～1.1 倍").severity == "hard_fail"


def test_compare_line_punctuation_only_diff_is_warn() -> None:
    # #08-r2 实例：Ⅳ级行『，』渲为『；』——术语数值零损，warn。
    finding = compare_line("较坚硬岩，较完整", "较坚硬岩；较完整")
    assert finding is not None
    assert finding.code == "punct_diff"
    assert finding.severity == "warn"


def test_compare_line_exact_match_returns_none() -> None:
    assert compare_line("项目预算总成本=∑（标后预算清单单价×清单工程量）", "项目预算总成本=∑（标后预算清单单价×清单工程量）") is None
    # 空白差异忽略。
    assert compare_line("40m 的桥梁工程", "40m的桥梁工程") is None


# ---------------------------------------------------------------------------
# 卡级判定（覆盖率 + 三档 + 边界）
# ---------------------------------------------------------------------------

_TRUTH = ("隧道围岩分级", "14≤B<18 时按跨度折减", "Ⅰ级 BQ＞550")


def test_card_pass_is_line_order_independent() -> None:
    transcript = "\n".join(reversed(_TRUTH))
    verdict, coverage, matched, findings = evaluate_card_transcript(truth_lines=_TRUTH, transcript=transcript)
    assert (verdict, coverage, matched, findings) == ("pass", 1.0, 3, ())


def test_card_missing_truth_line_is_hard_fail_with_coverage_drop() -> None:
    # b2-D2 实例：『14≤B<18』整条漏渲染。
    transcript = "隧道围岩分级\nⅠ级 BQ＞550"
    verdict, coverage, matched, findings = evaluate_card_transcript(truth_lines=_TRUTH, transcript=transcript)
    assert verdict == "hard_fail"
    assert matched == 2 and coverage == pytest.approx(2 / 3)
    assert any(item.code == "line_missing" and "14≤B<18" in (item.truth_line or "") for item in findings)


def test_card_extra_transcript_line_is_warn() -> None:
    transcript = "\n".join(_TRUTH) + "\n装饰性页脚文字"
    verdict, coverage, _matched, findings = evaluate_card_transcript(truth_lines=_TRUTH, transcript=transcript)
    assert verdict == "warn"
    assert coverage == 1.0
    assert any(item.code == "line_extra" for item in findings)


def test_card_empty_transcript_is_hard_fail() -> None:
    verdict, coverage, matched, findings = evaluate_card_transcript(truth_lines=_TRUTH, transcript="  \n \n")
    assert (verdict, coverage, matched) == ("hard_fail", 0.0, 0)
    assert findings[0].code == "empty_transcript"


def test_card_punct_diff_only_is_warn_with_full_coverage() -> None:
    transcript = "隧道围岩分级\n14≤B<18 时按跨度折减\nⅠ级 BQ＞550；"
    verdict, coverage, _matched, findings = evaluate_card_transcript(truth_lines=_TRUTH, transcript=transcript)
    assert (verdict, coverage) == ("warn", 1.0)
    assert all(item.severity == "warn" for item in findings)


def test_reflow_split_truth_element_across_adjacent_transcript_lines_is_pass() -> None:
    # A2 口径：纯换行 reflow＝软分隔＝pass（拆行向）。
    truth = ("特殊路基处治原则：①软土地段路堤②冻土地段路堤", "另一独立元素")
    transcript = "特殊路基处治原则：①软土地段路堤\n②冻土地段路堤\n另一独立元素"
    verdict, coverage, matched, findings = evaluate_card_transcript(truth_lines=truth, transcript=transcript)
    assert (verdict, coverage, matched, findings) == ("pass", 1.0, 2, ())


def test_reflow_merge_truth_lines_into_one_transcript_line_is_pass() -> None:
    # 并行向：两真值行被并进一转写行。
    truth = ("行一内容甲", "行二内容乙", "行三内容丙")
    transcript = "行一内容甲行二内容乙\n行三内容丙"
    verdict, coverage, matched, findings = evaluate_card_transcript(truth_lines=truth, transcript=transcript)
    assert (verdict, coverage, matched, findings) == ("pass", 1.0, 3, ())


def test_reflow_with_punct_diff_is_warn() -> None:
    truth = ("处治原则：①换填，②抛石挤淤", "另一独立元素")
    transcript = "处治原则：①换填；\n②抛石挤淤\n另一独立元素"
    verdict, coverage, _matched, findings = evaluate_card_transcript(truth_lines=truth, transcript=transcript)
    assert (verdict, coverage) == ("warn", 1.0)
    assert [item.code for item in findings] == ["punct_diff"]


def test_reflow_with_semantic_typo_stays_hard_fail() -> None:
    # reflow 修复只接受语义 token 全等；拆行且带术语错 → 仍 hard。
    truth = ("滑坡地段路基处治要点甲乙丙丁", "另一独立元素")
    transcript = "滑坡地质路基处治要点甲乙\n丙丁\n另一独立元素"
    verdict, _coverage, _matched, findings = evaluate_card_transcript(truth_lines=truth, transcript=transcript)
    assert verdict == "hard_fail"
    assert any(item.severity == "hard_fail" for item in findings)


def test_card_multiline_truth_element_expands_to_line_units() -> None:
    # 真值条目内嵌换行（一个元素跨多物理行，r2242 实况形态）：拆行后逐行配对。
    truth = ("特殊路基分类：\n①软土地段路堤\n②冻土地段路堤", "处治原则")
    transcript = "处治原则\n特殊路基分类：\n①软土地段路堤\n②冻土地段路堤"
    verdict, coverage, matched, findings = evaluate_card_transcript(truth_lines=truth, transcript=transcript)
    assert (verdict, coverage, matched, findings) == ("pass", 1.0, 4, ())
    # 元素的其中一行漏渲 → line_missing hard_fail（D2 语义在行单元上保持）。
    transcript_missing = "处治原则\n特殊路基分类：\n①软土地段路堤"
    verdict2, coverage2, _m, findings2 = evaluate_card_transcript(truth_lines=truth, transcript=transcript_missing)
    assert verdict2 == "hard_fail"
    assert coverage2 == pytest.approx(3 / 4)
    assert any(item.code == "line_missing" for item in findings2)


# ---------------------------------------------------------------------------
# 报告构建（自证哈希 + 汇总 + 防篡改）
# ---------------------------------------------------------------------------


def _fake_cards(tmp_path: Path) -> tuple[list[dict], dict[str, tuple[str, ...]], dict[str, str]]:
    truths = {
        "card:gate:one": ("行一", "行二"),
        "card:gate:two": ("较坚硬岩，较完整",),
        "card:gate:three": ("整行真值",),
    }
    transcripts = {
        "card:gate:one": "行二\n行一",                 # pass（顺序无关）
        "card:gate:two": "较坚硬岩；较完整",            # warn（标点）
        "card:gate:three": "别的内容",                  # hard（语义/漏行）
    }
    cards = []
    for index, (card_id, _lines) in enumerate(truths.items(), start=1):
        image = tmp_path / f"card-{index}.png"
        image.write_bytes(f"png-bytes-{index}".encode("utf-8"))
        cards.append(
            {
                "card_index": index,
                "source_card_id": card_id,
                "render_presentation": "table",
                "image_path": str(image),
            }
        )
    return cards, truths, transcripts


def test_report_builds_counts_overall_and_self_hash(tmp_path: Path) -> None:
    cards, truths, transcripts = _fake_cards(tmp_path)
    by_bytes = {f"png-bytes-{index}".encode("utf-8"): transcripts[cards[index - 1]["source_card_id"]] for index in (1, 2, 3)}
    report = build_step4_text_gate_report(
        run_dir="exams/fake/data/live_runs/run-x",
        truth_fixture_path="scripts/tests/fixtures/v2/release_fixtures/fake.yaml",
        truth_fixture_sha256=hashlib.sha256(b"fixture").hexdigest(),
        transcriber_id="fake-transport",
        cards=cards,
        truths_by_card_id=truths,
        transcribe=lambda image_bytes: by_bytes[bytes(image_bytes)],
        read_image=lambda path: Path(path).read_bytes(),
    )
    assert (report.pass_count, report.warn_count, report.hard_fail_count) == (1, 1, 1)
    assert report.overall_verdict == "hard_fail"
    assert report.cards[0].verdict == "pass" and report.cards[1].verdict == "warn"
    assert report.cards[2].verdict == "hard_fail"
    # R5 机拦人裁分层：disposition 按 verdict 定向（hard_fail 路由人工，非自动废图）。
    assert [card.disposition for card in report.cards] == [
        "auto_accept",
        "auto_archive_with_note",
        "human_adjudication",
    ]
    # 防篡改：改一处判级即哈希失配。
    payload = report.model_dump(mode="json")
    payload["pass_count"] = 2
    payload["warn_count"] = 0
    with pytest.raises(Exception):
        Step4TextGateReport.model_validate(payload)
    # 序列化-回读闭环。
    assert Step4TextGateReport.model_validate(json.loads(json.dumps(report.model_dump(mode="json")))).report_sha256 == report.report_sha256


def test_card_disposition_must_follow_verdict_routing() -> None:
    # R5 映射被 validator 钉死：hard_fail 卡标成 auto_accept（即便自证哈希算对）也必须被拒。
    from examcard.common.hashing import hash_model
    from examcard.gates.review.step4_text_gate import TextGateCardVerdict

    payload = {
        "card_index": 1,
        "source_card_id": "card:gate:one",
        "render_presentation": "table",
        "image_path": "images/step4-card-01-deadbeef.png",
        "image_sha256": hashlib.sha256(b"image").hexdigest(),
        "transcript_sha256": hashlib.sha256(b"transcript").hexdigest(),
        "verdict": "hard_fail",
        "disposition": "auto_accept",
        "coverage": 0.5,
        "truth_line_count": 2,
        "matched_line_count": 1,
        "findings": (),
    }
    with pytest.raises(ValueError, match="disposition must follow verdict routing"):
        hash_model(TextGateCardVerdict, payload, "card_sha256")
    payload["disposition"] = "human_adjudication"
    card = hash_model(TextGateCardVerdict, payload, "card_sha256")
    assert card.disposition == "human_adjudication"


def test_report_refuses_missing_truth(tmp_path: Path) -> None:
    cards, truths, _ = _fake_cards(tmp_path)
    del truths["card:gate:two"]
    with pytest.raises(ValueError, match="no expected_texts truth"):
        build_step4_text_gate_report(
            run_dir="run",
            truth_fixture_path="f.yaml",
            truth_fixture_sha256=hashlib.sha256(b"f").hexdigest(),
            transcriber_id="fake",
            cards=cards,
            truths_by_card_id=truths,
            transcribe=lambda _b: "x",
            read_image=lambda path: Path(path).read_bytes(),
        )


# ---------------------------------------------------------------------------
# 适配面：真实 r2242 fixture 真值抽取 + 出图命名解析（含 -r2 重发件优先）
# ---------------------------------------------------------------------------


def test_extract_truths_from_real_r2242_fixture() -> None:
    fixture_payload = yaml.safe_load(_R2242.read_text(encoding="utf-8"))
    from examcard.knowledge.contracts.admission_fixtures import Step3UnifiedReviewedReleaseFixture

    fixture = Step3UnifiedReviewedReleaseFixture.model_validate(fixture_payload)
    truths = extract_truths_from_release_fixture(fixture)
    assert len(truths) == 16
    assert all(lines for lines in truths.values())
    sample = truths[fixture.cards[0].source_card_id]
    assert all(isinstance(line, str) and line for line in sample)


def test_merged_row_label_is_semantically_equivalent_pass() -> None:
    # A2 #07 口径：同元素内重复行标签渲染为合并单元格＝语义等价重组＝pass。
    truth = ("模板 | 条款甲内容。\n模板 | 条款乙内容。\n支架 | 条款丙内容。",)
    transcript = "模板 | 条款甲内容。\n条款乙内容。\n支架 | 条款丙内容。"
    verdict, coverage, matched, findings = evaluate_card_transcript(truth_lines=truth, transcript=transcript)
    assert (verdict, coverage, matched, findings) == ("pass", 1.0, 3, ())
    # 首次出现的标签不可去（非重复不合并）：缺首标签行按内容仍可相似度配对，但不是精确等价。
    transcript_faithful = "模板 | 条款甲内容。\n模板 | 条款乙内容。\n支架 | 条款丙内容。"
    verdict2, _c, _m, findings2 = evaluate_card_transcript(truth_lines=truth, transcript=transcript_faithful)
    assert (verdict2, findings2) == ("pass", ())


def test_a2_gold_set_verdicts_reproduced_end_to_end() -> None:
    """A2 金标集消费回归（tests/fixtures/step4_word_gate/b2_review_gold.json，95b18b0）：
    逐卡 gate(human_transcript) == expected_verdict；kind→findings 码映射抽查；分布对账。"""

    gold_path = _REAL_REPO / "tests/fixtures/step4_word_gate/b2_review_gold.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    assert gold["schema"] == "step4-word-gate-gold/1.0"
    assert len(gold["cards"]) == 18
    kind_to_codes = {
        "term_error": "semantic_diff",
        "number_error": "semantic_diff",
        "line_missing": "line_missing",
        "punct_sub": "punct_diff",
    }
    verdict_counts = {"pass": 0, "warn": 0, "hard_fail": 0}
    for card in gold["cards"]:
        transcript = "\n".join(card["human_transcript"])
        verdict, coverage, _matched, findings = evaluate_card_transcript(
            truth_lines=tuple(card["truth_visible_texts"]), transcript=transcript
        )
        label = f"card {card['card_index']}-{card['render']}"
        assert verdict == card["expected_verdict"], (
            f"{label}: gate={verdict} expected={card['expected_verdict']} findings={[f.code for f in findings]}"
        )
        verdict_counts[verdict] += 1
        expected_codes = {
            kind_to_codes[item["kind"]]
            for item in card.get("expected_findings", ())
            if item.get("kind") in kind_to_codes
        }
        got_codes = {item.code for item in findings}
        assert expected_codes <= got_codes, f"{label}: expected finding codes {expected_codes} not all present in {got_codes}"
        if card["expected_verdict"] == "pass":
            assert coverage == 1.0, f"{label}: pass card must have full coverage"
    distribution = gold.get("expected_verdict_distribution") or {}
    if distribution:
        assert verdict_counts == {key: int(value) for key, value in distribution.items()}


def test_a2_live_vision_transcripts_replay_matches_human_verdicts() -> None:
    """真 vision 转写复放回归（A2 live 校准实录 fixture，d90afa9）：
    18 卡逐卡 gate(真转写) == 人工 expected_verdict——含逐单元格转写、罗马数字字母化、
    ∑/Σ 归一、| → / 分隔替换、自由折行等全部真实转写形态（零网络复放）。"""

    gold = json.loads((_REAL_REPO / "tests/fixtures/step4_word_gate/b2_review_gold.json").read_text(encoding="utf-8"))
    live = json.loads(
        (_REAL_REPO / "tests/fixtures/step4_word_gate/b2_live_vision_transcripts_20260813.json").read_text(encoding="utf-8")
    )
    by_key = {(entry["card_index"], entry.get("render", "r1")): entry for entry in live["transcripts"]}
    for card in gold["cards"]:
        entry = by_key[(card["card_index"], card["render"])]
        transcript = entry["transcript"]
        transcript = "\n".join(transcript) if isinstance(transcript, list) else str(transcript)
        verdict, _coverage, _matched, findings = evaluate_card_transcript(
            truth_lines=tuple(card["truth_visible_texts"]), transcript=transcript
        )
        assert verdict == card["expected_verdict"], (
            f"card {card['card_index']}-{card['render']} (live transcript): gate={verdict} "
            f"expected={card['expected_verdict']} findings={[f.code for f in findings]}"
        )


def test_resolve_card_image_prefers_highest_resend_round(tmp_path: Path) -> None:
    base = tmp_path / "step4-card-01-1011b240.png"
    base.write_bytes(b"round1")
    assert resolve_card_image(tmp_path, 1, "v25-release-card:1011b240d15e12f3f7084096") == base
    r2 = tmp_path / "step4-card-01-1011b240-r2.png"
    r2.write_bytes(b"round2")
    assert resolve_card_image(tmp_path, 1, "v25-release-card:1011b240d15e12f3f7084096") == r2
    with pytest.raises(FileNotFoundError):
        resolve_card_image(tmp_path, 2, "v25-release-card:c2712f4fcfbb9407a3140d57")
