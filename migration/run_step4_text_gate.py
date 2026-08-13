"""step4 词级/标点/覆盖率门禁 CLI 薄壳（改进 A1；核心在 gates/review/step4_text_gate.py）。

独立后审计件：输入一个 step4 run 目录（`step4/requests-manifest.json` + `step4/images/`）
与该 run 消费的发布 fixture（真值 expected_texts），输出门禁报告 JSON（逐卡+汇总，自证哈希）。

转写面两种模式（互斥，二选一）：
- ``--transcripts-json <path>``：**零网络**。人工/既有转写件，格式
  {"transcripts": [{"card_index": 1, "image_sha256": "<64hex，可选但给了就强校验>",
  "transcript": "一行一个可见元素"}]}——A2 金标集直接喂此口。
- ``--allow-live``：视觉读图转写（chat completions + image_url data URI，读 .env 文本通道；
  形状对齐 evidence/vision.py 的 OpenAI 兼容口径）。**本卡零 live 花费纪律：该道由 A2 卡
  负责实测校准，本仓测试全部走伪转写。**

退出码：0=pass 或 warn（warn 记录在报告，不拦截）；1=存在 hard_fail 或输入缺件。
机拦人裁（R5，report/1.1）：exit 1 语义是「机器拦截、路由人工」而非自动废图——报告逐卡
``disposition`` 给出定向（pass→auto_accept、warn→auto_archive_with_note、
hard_fail→human_adjudication），stdout 汇总行附 human_adjudication_cards 清单。

运行（exam-card-engine 仓根）：
  uv run python migration/run_step4_text_gate.py \
      --run-dir exams/gonglu_golden5_rehearsal/data/live_runs/20260812-rehearsal-b2-9lhvgu4b \
      --release-fixture scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml \
      --transcripts-json <人工转写件.json>
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

NEW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.gates.review.step4_text_gate import (  # noqa: E402
    build_step4_text_gate_report,
    extract_truths_from_release_fixture,
    resolve_card_image,
)
from examcard.knowledge.contracts.admission_fixtures import Step3UnifiedReviewedReleaseFixture  # noqa: E402
from examcard.pipeline.shadow_wiring import load_env_file  # noqa: E402

# R4（A2 校准建议）：转写行约定入 prompt——表格卡整行转写为一行、单元格间以 " | " 连接，
# 从源头把转写形态对齐真值行约定（逐单元格一行是 live 道 G2 碎行的主因）。prompt 一旦变更
# 必须同步 bump 版本号：transcriber_id 携带之（A2 提醒——转写实录缓存/复放按版本区分谱系）。
TRANSCRIBE_PROMPT_VERSION = "prompt-v2"
TRANSCRIBE_PROMPT = (
    "Transcribe every piece of visible text in this exam knowledge-card image verbatim, "
    "one visible element per line, preserving all punctuation, comparison operators and units "
    "exactly as rendered. For table cards transcribe each table row as one single line in "
    "reading order, joining the row's cell contents with ' | '; never split one table row "
    "across multiple output lines. Output only the transcription lines with no commentary."
)


def _live_transcriber_id(model: str) -> str:
    """live 道 transcriber_id＝模型+prompt 版本（R4：转写形态随 prompt 变，谱系必须可区分）。"""

    return f"live:{model}:{TRANSCRIBE_PROMPT_VERSION}"


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def _json_transcriber(transcripts_path: Path, cards: list[dict]) -> "callable":
    document = json.loads(transcripts_path.read_text(encoding="utf-8"))
    entries = document.get("transcripts")
    if not isinstance(entries, list) or not entries:
        raise SystemExit(f"--transcripts-json has no transcripts array: {transcripts_path}")
    by_sha: dict[str, str] = {}
    by_index: dict[int, str] = {}
    for entry in entries:
        transcript = entry.get("transcript")
        if not isinstance(transcript, str):
            raise SystemExit(f"transcript entry missing text: {entry!r}")
        if entry.get("image_sha256"):
            by_sha[str(entry["image_sha256"])] = transcript
        if entry.get("card_index") is not None:
            by_index[int(entry["card_index"])] = transcript
    index_by_bytes: dict[str, int] = {}
    for card in cards:
        blob = Path(card["image_path"]).read_bytes()
        index_by_bytes[hashlib.sha256(blob).hexdigest()] = int(card["card_index"])

    def transcribe(image_bytes: bytes) -> str:
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest in by_sha:
            return by_sha[digest]
        card_index = index_by_bytes.get(digest)
        if card_index is not None and card_index in by_index:
            return by_index[card_index]
        raise SystemExit(f"no transcript bound for image sha256 {digest[:16]}… (card {card_index})")

    return transcribe


def _live_transcriber(env_path: Path) -> tuple["callable", str]:
    # live 视觉转写道——**SSE 流式**（生产配方：2chat 首字节窗口以流式穿越，T2/T3 实证；
    # 20260813 P8 彩排实测非流式被网关 http=524 掐断后改流式，transcriber_id 携 +sse 谱系）。
    from openai import OpenAI

    env = load_env_file(env_path)
    missing = [key for key in ("API_BASE_URL", "API_KEY", "TEXT_MODEL") if not env.get(key)]
    if missing:
        raise SystemExit(f"{env_path} is missing required keys for live transcription: {missing}")
    model = env["TEXT_MODEL"]
    client = OpenAI(
        base_url=env["API_BASE_URL"].rstrip("/"),
        api_key=env["API_KEY"],
        timeout=600,
        max_retries=0,
        default_headers={"User-Agent": env["OPENAI_USER_AGENT"]} if env.get("OPENAI_USER_AGENT") else None,
    )

    def transcribe(image_bytes: bytes) -> str:
        # Responses API 路：A2 校准实录（provider_id=resp_…，3-18s/卡）证实 2chat 视觉
        # 输入走 /responses 快速返回；chat/completions+image（含流式）实测被 120s 源站
        # 窗口掐断（http=524，20260813 P8 彩排两次取证）。
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": TRANSCRIBE_PROMPT},
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii"),
                        },
                    ],
                }
            ],
            max_output_tokens=4000,
            temperature=0,
        )
        content = (getattr(response, "output_text", None) or "").strip()
        if not content:
            raise SystemExit("live transcription returned empty output")
        return content

    return transcribe, _live_transcriber_id(model) + "+responses"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--release-fixture", type=Path, required=True)
    parser.add_argument(
        "--release-family",
        choices=("pilot", "echo"),
        default="pilot",
        help="truth source family: pilot = r2242 YAML (expected_texts); echo = echo-batch-release/1.0 json (P8 bridge)",
    )
    parser.add_argument("--transcripts-json", type=Path, default=None)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=NEW_ROOT / ".env")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if bool(args.transcripts_json) == bool(args.allow_live):
        parser.error("choose exactly one of --transcripts-json or --allow-live")

    run_dir = args.run_dir.resolve()
    manifest_path = run_dir / "step4" / "requests-manifest.json"
    images_dir = run_dir / "step4" / "images"
    if not manifest_path.is_file() or not images_dir.is_dir():
        raise SystemExit(f"run dir lacks step4 artifacts (requests-manifest.json / images/): {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture_bytes = args.release_fixture.read_bytes()
    if args.release_family == "echo":
        from examcard.gates.review.step4_text_gate import extract_truths_from_echo_release
        from examcard.knowledge.contracts.echo_release import load_echo_release_fixture

        truths = extract_truths_from_echo_release(load_echo_release_fixture(args.release_fixture))
    else:
        import yaml

        fixture = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(fixture_bytes.decode("utf-8")))
        truths = extract_truths_from_release_fixture(fixture)

    cards: list[dict] = []
    for entry in manifest:
        image_path = resolve_card_image(images_dir, int(entry["card_index"]), str(entry["source_card_id"]))
        cards.append(
            {
                "card_index": int(entry["card_index"]),
                "source_card_id": str(entry["source_card_id"]),
                "render_presentation": str(entry["render_presentation"]),
                "image_path": str(image_path),
            }
        )

    if args.transcripts_json is not None:
        transcribe = _json_transcriber(args.transcripts_json, cards)
        transcriber_id = f"transcripts-json:{hashlib.sha256(args.transcripts_json.read_bytes()).hexdigest()[:16]}"
    else:
        transcribe, transcriber_id = _live_transcriber(args.env_file)

    report = build_step4_text_gate_report(
        run_dir=str(run_dir),
        truth_fixture_path=str(args.release_fixture),
        truth_fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
        transcriber_id=transcriber_id,
        cards=cards,
        truths_by_card_id=truths,
        transcribe=transcribe,
        read_image=lambda path: Path(path).read_bytes(),
    )
    out_path = args.out if args.out is not None else run_dir / "step4" / "text-gate-report.json"
    _atomic_write_json(out_path, report.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "report": str(out_path),
                "cards": len(report.cards),
                "pass": report.pass_count,
                "warn": report.warn_count,
                "hard_fail": report.hard_fail_count,
                "overall_verdict": report.overall_verdict,
                # R5 机拦人裁分层：hard_fail 卡定向人工/重发道（非自动废图），warn 卡自动记档。
                "human_adjudication_cards": [
                    card.card_index for card in report.cards if card.disposition == "human_adjudication"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.overall_verdict in {"pass", "warn"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
