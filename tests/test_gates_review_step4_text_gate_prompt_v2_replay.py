"""prompt-v2×Responses 路真 vision 转写复放回归（改进2-④ live 校准实录；零网络）。

独立成文件（不并入 test_gates_review_step4_text_gate.py）：改进2-③ 同期在改配对算法，
写面分离。两件事被钉住：
1. 复放判定：18 条金标逐卡 gate(prompt-v2 真转写)——17 条与人工 expected_verdict 一致；
   唯一例外 #07-r1 钉为 hard_fail（该转写抖动性漏掉『支架 | （6）…』整行，图面确有——
   机判 hard_fail 是门禁对其输入的正确拦截；此钉扎同时是防漏放卫兵：任何门禁改动
   把「转写缺整行内容」放行成 pass 即为 false-pass 回归）。配对算法改进（如 ③ 的
   R2 全序对齐）只会消解误报向分歧，不触动本钉扎面。
2. 版本谱系纪律（R4/A2）：fixture 锁 prompt-v2+responses 谱系；当 runner 仍处
   prompt-v2 时，prompt 文本不得漂移（改 prompt 必须 bump 版本号并另立新一轮校准
   fixture，本 fixture 永久保留为 v2 谱系复放件）。

校准实测记录：docs/prework/P3_改进2-4_promptv2_live校准_20260813.md（门禁 commit
8f9d5a2 下 v2 道 17/18、hard_fail 召回 2/2、零误放行；#07 探针复测归因抖动）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from examcard.gates.review.step4_text_gate import evaluate_card_transcript

_REAL_REPO = Path(__file__).resolve().parents[1]
_GOLD = _REAL_REPO / "tests/fixtures/step4_word_gate/b2_review_gold.json"
_FIXTURE = _REAL_REPO / "tests/fixtures/step4_word_gate/b2_live_vision_transcripts_prompt_v2_20260813.json"

# 校准时点唯一分歧（安全向）：#07-r1 转写实录漏行 → 机判 hard_fail 正确；详见模块 docstring。
_MACHINE_VERDICT_OVERRIDES = {(7, "r1"): "hard_fail"}


def _load_cli_module():
    module_path = _REAL_REPO / "migration" / "run_step4_text_gate.py"
    spec = importlib.util.spec_from_file_location("run_step4_text_gate_for_v2_replay", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prompt_v2_live_transcripts_replay_verdicts() -> None:
    gold = json.loads(_GOLD.read_text(encoding="utf-8"))
    live = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    by_key = {(entry["card_index"], entry.get("render", "r1")): entry for entry in live["transcripts"]}
    assert len(live["transcripts"]) == 18
    hard_recall = 0
    for card in gold["cards"]:
        key = (card["card_index"], card["render"])
        transcript = str(by_key[key]["transcript"])
        verdict, _coverage, _matched, findings = evaluate_card_transcript(
            truth_lines=tuple(card["truth_visible_texts"]), transcript=transcript
        )
        expected = _MACHINE_VERDICT_OVERRIDES.get(key, card["expected_verdict"])
        assert verdict == expected, (
            f"card {key[0]:02d}-{key[1]} (prompt-v2 live transcript): gate={verdict} "
            f"expected={expected} findings={[item.code for item in findings]}"
        )
        # 安全不变量（两道零漏报纪律）：人工 hard_fail 卡绝不得被机判放行。
        if card["expected_verdict"] == "hard_fail":
            assert verdict == "hard_fail"
            hard_recall += 1
    assert hard_recall == 2


def test_prompt_v2_fixture_lineage_and_binding() -> None:
    gold_bytes = _GOLD.read_bytes()
    live = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    provenance = live["provenance"]
    assert live["schema"] == "step4-word-gate-live-transcripts/1.0"
    # 金标绑定：转写实录与其标注集逐字节互锁。
    assert provenance["gold_set_sha256"] == hashlib.sha256(gold_bytes).hexdigest()
    gold = json.loads(gold_bytes.decode("utf-8"))
    gold_keys = {(card["card_index"], card["render"]): card["image_sha256"] for card in gold["cards"]}
    live_keys = {
        (entry["card_index"], entry.get("render", "r1")): entry["image_sha256"] for entry in live["transcripts"]
    }
    assert live_keys == gold_keys
    # 谱系纪律（R4）：本 fixture 永久锁 prompt-v2+responses。
    assert provenance["prompt_version"] == "prompt-v2"
    assert provenance["transcriber_id"] == f"live:{provenance['model']}:prompt-v2+responses"
    cli = _load_cli_module()
    if cli.TRANSCRIBE_PROMPT_VERSION == "prompt-v2":
        # runner 仍处 v2 期：prompt 文本漂移而不 bump 版本号＝违反 R4 纪律，此处拦下。
        prompt_sha = hashlib.sha256(cli.TRANSCRIBE_PROMPT.encode("utf-8")).hexdigest()[:16]
        assert provenance["prompt_sha256_16"] == prompt_sha, (
            "TRANSCRIBE_PROMPT text drifted while version stays prompt-v2 - bump "
            "TRANSCRIBE_PROMPT_VERSION and run a fresh calibration round instead"
        )
