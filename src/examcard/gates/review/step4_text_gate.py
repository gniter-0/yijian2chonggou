"""step4 词级/标点比对 + visible_texts 覆盖率自动门禁（改进 A1，独立后审计件）。

依据 b2-D1/D2（`docs/prework/P3_彩排b2_step4十六图人工评审_20260813.md`）：
- D1 型「术语单字符替换」（滑坡**地段**→滑坡**地质**）与数值/比较符错误 → **hard_fail**；
- D2 型「整条 expected_text 漏渲染」（『14≤B<18』）→ 覆盖率硬校验 → **hard_fail**；
- 标点级替换/增漏（『，』→『；』，#08-r2 实例）→ **warn**（人工终审语义：可带瑕疵接受）；
- 逐词逐标点全对 → **pass**。

形态（任务书钉死）：**standalone 后审计件**，不动 live_run.py 编排；真值＝发布 fixture 的
`expected_texts`（其 model validator 保证与 display_text_provenance 逐条互证）；转写面
**transport 注入式**——本模块只收 `transcribe: Callable[[bytes], str]`（图像字节→纯文本，
一行一个可见元素），live 视觉通道由 CLI 装配（本卡零 live 花费，校准归 A2 卡）。

匹配口径（评审人实践对齐，文档化决策）：
- **匹配单元 = 行**：真值 `expected_texts` 条目可含内嵌换行（一个可见元素跨多物理行），
  先按换行拆成真值行再配对；覆盖率分母 = 拆行后的真值行数（D2「整条元素漏渲染」
  ⇒ 其全部构成行落空，仍为 hard_fail）；
- **行间顺序无关**：真值行与转写行按相似度贪心配对（表格卡行序视觉上可变）；
- **行内严格**：配对行内逐 token 比对——语义 token（CJK 字、拉丁字母数字串、比较/数学符
  ≤<>≥=±×∑ 与 %‰℃ 等单位记号）不一致 → hard_fail；仅标点 token（，。、；：？！""''（）
  及半角对应物等）差异 → warn；
- 覆盖率 = 配对成功的真值行 / 全部真值行；任何真值行落空（line_missing）→ hard_fail；
- 转写多余行 → warn（装饰性文字容忍为警告）；空转写 → hard_fail（empty_transcript）；
- 空白差异忽略；全角/半角**同类标点互替仍记 punct_diff**（保守：报 warn 留人裁）。

自证哈希走 ``common.hashing``（新产出域；与旧契约法定哈希域不掺混——铁律 bc-b8b9aca6）。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Callable, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ...common.hashing import SHA256_HEX, canonical_hash, hash_model
from ...materials.schema import StrictModel

__all__ = (
    "STEP4_TEXT_GATE_VERSION",
    "TextGateFinding",
    "TextGateCardVerdict",
    "Step4TextGateReport",
    "tokenize_visible_text",
    "compare_line",
    "evaluate_card_transcript",
    "build_step4_text_gate_report",
    "extract_truths_from_release_fixture",
    "resolve_card_image",
)

STEP4_TEXT_GATE_VERSION = "step4-text-gate-report/1.0"

# 语义 token：CJK / 拉丁字母数字串 / 数值（含小数与百分比）/ 比较与数学符号（考点级，D2 符号谱系）。
_SEMANTIC_TOKEN = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf]"          # 单个 CJK 字
    r"|[A-Za-z0-9]+(?:\.[0-9]+)?"            # 拉丁/数字串（含小数）
    r"|[≤≥<>＜＞≦≧=±×÷∑Σ√∞≈≠~％%‰℃°]"          # 比较/数学/单位记号（含全角比较符）——错一个即考点级
)
# 标点 token：全半角常用标点（差异只记 warn）。
_PUNCT_TOKEN = re.compile(r"[，。、；：？！""''『』「」（）【】,.;:?!\"'()\[\]{}\-—–…·/\\|]")


FindingCode = Literal["line_missing", "line_extra", "semantic_diff", "punct_diff", "empty_transcript"]
Severity = Literal["hard_fail", "warn"]
Verdict = Literal["pass", "warn", "hard_fail"]


class TextGateFinding(StrictModel):
    code: FindingCode
    severity: Severity
    truth_line: str | None = None
    transcript_line: str | None = None
    detail: str = Field(min_length=1)


class TextGateCardVerdict(StrictModel):
    card_index: int = Field(ge=1)
    source_card_id: str = Field(min_length=1)
    render_presentation: str = Field(min_length=2)
    image_path: str = Field(min_length=1)
    image_sha256: str = Field(pattern=SHA256_HEX)
    transcript_sha256: str = Field(pattern=SHA256_HEX)
    verdict: Verdict
    coverage: float = Field(ge=0.0, le=1.0)
    truth_line_count: int = Field(ge=1)
    matched_line_count: int = Field(ge=0)
    findings: tuple[TextGateFinding, ...] = ()
    card_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_card(self) -> "TextGateCardVerdict":
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "card_sha256"}))
        if self.card_sha256 != expected:
            raise ValueError("text gate card verdict hash mismatch")
        return self


class Step4TextGateReport(StrictModel):
    schema_version: Literal["step4-text-gate-report/1.0"] = STEP4_TEXT_GATE_VERSION
    run_dir: str = Field(min_length=1)
    truth_fixture_path: str = Field(min_length=1)
    truth_fixture_sha256: str = Field(pattern=SHA256_HEX)
    transcriber_id: str = Field(min_length=1)
    cards: tuple[TextGateCardVerdict, ...] = Field(min_length=1)
    pass_count: int = Field(ge=0)
    warn_count: int = Field(ge=0)
    hard_fail_count: int = Field(ge=0)
    overall_verdict: Verdict
    report_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_report(self) -> "Step4TextGateReport":
        counts = {
            "pass": sum(1 for card in self.cards if card.verdict == "pass"),
            "warn": sum(1 for card in self.cards if card.verdict == "warn"),
            "hard_fail": sum(1 for card in self.cards if card.verdict == "hard_fail"),
        }
        if (self.pass_count, self.warn_count, self.hard_fail_count) != (
            counts["pass"],
            counts["warn"],
            counts["hard_fail"],
        ):
            raise ValueError("text gate report verdict counts mismatch")
        expected_overall: Verdict = (
            "hard_fail" if counts["hard_fail"] else ("warn" if counts["warn"] else "pass")
        )
        if self.overall_verdict != expected_overall:
            raise ValueError("text gate report overall verdict mismatch")
        if len({card.card_index for card in self.cards}) != len(self.cards):
            raise ValueError("text gate report card indexes must be unique")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "report_sha256"}))
        if self.report_sha256 != expected:
            raise ValueError("text gate report hash mismatch")
        return self


def extract_truths_from_release_fixture(fixture: Any) -> dict[str, tuple[str, ...]]:
    """发布 fixture → {source_card_id: expected_texts}（真值面唯一入口，CLI 与测试共用）。"""

    truths: dict[str, tuple[str, ...]] = {}
    for card in fixture.cards:
        truths[str(card.source_card_id)] = tuple(card.expected_texts)
    if len(truths) != len(fixture.cards):
        raise ValueError("release fixture source_card_id values must be unique")
    return truths


def resolve_card_image(images_dir: Any, card_index: int, source_card_id: str) -> Any:
    """run 目录出图命名约定 `step4-card-{index:02d}-{cardid前8位}[-rN].png`；有重发件取最高轮次。

    重发件（如 `-r2`）由收口流程以追加方式落盘（原图保留），其为**现行授权对象**——
    与 image_review_sheet 的 supersede 语义一致。
    """

    from pathlib import Path

    directory = Path(images_dir)
    short_id = source_card_id.rsplit(":", maxsplit=1)[-1][:8]
    base_name = f"step4-card-{card_index:02d}-{short_id}"
    candidates = sorted(directory.glob(f"{base_name}*.png"))
    if not candidates:
        raise FileNotFoundError(f"no rendered image for card {card_index} ({base_name}*.png) in {directory}")

    def round_key(path: Path) -> int:
        match = re.search(r"-r(\d+)$", path.stem)
        return int(match.group(1)) if match else 1

    return max(candidates, key=lambda path: (round_key(path), path.name))


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", "", line)


def tokenize_visible_text(line: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """一行 → (语义 token 序列, 标点 token 序列)；空白丢弃，未知字符归语义面（保守从严）。"""

    compact = _normalize_line(line)
    semantic: list[str] = []
    punct: list[str] = []
    cursor = 0
    while cursor < len(compact):
        match = _SEMANTIC_TOKEN.match(compact, cursor)
        if match is not None:
            semantic.append(match.group(0))
            cursor = match.end()
            continue
        match = _PUNCT_TOKEN.match(compact, cursor)
        if match is not None:
            punct.append(match.group(0))
            cursor = match.end()
            continue
        # 未知字符按语义 token 计（宁可 hard 也不放过考点级差异）。
        semantic.append(compact[cursor])
        cursor += 1
    return tuple(semantic), tuple(punct)


def compare_line(truth_line: str, transcript_line: str) -> TextGateFinding | None:
    """行内严格比对：语义差 → hard；仅标点差 → warn；全对 → None。"""

    truth_semantic, truth_punct = tokenize_visible_text(truth_line)
    got_semantic, got_punct = tokenize_visible_text(transcript_line)
    if truth_semantic != got_semantic:
        return TextGateFinding(
            code="semantic_diff",
            severity="hard_fail",
            truth_line=truth_line,
            transcript_line=transcript_line,
            detail=f"semantic tokens diverge: truth={list(truth_semantic)} transcript={list(got_semantic)}",
        )
    if truth_punct != got_punct:
        return TextGateFinding(
            code="punct_diff",
            severity="warn",
            truth_line=truth_line,
            transcript_line=transcript_line,
            detail=f"punctuation diverges: truth={list(truth_punct)} transcript={list(got_punct)}",
        )
    return None


def _pair_lines(truth_lines: Sequence[str], transcript_lines: Sequence[str]) -> tuple[dict[int, int], list[int]]:
    """行间顺序无关贪心配对：相似度全对排序取最优；返回 (truth→transcript 映射, 未配对 transcript 下标)。"""

    scored: list[tuple[float, int, int]] = []
    for t_index, truth in enumerate(truth_lines):
        truth_norm = unicodedata.normalize("NFKC", _normalize_line(truth))
        for g_index, got in enumerate(transcript_lines):
            got_norm = unicodedata.normalize("NFKC", _normalize_line(got))
            ratio = SequenceMatcher(None, truth_norm, got_norm).ratio()
            if ratio >= 0.5:
                scored.append((ratio, t_index, g_index))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    truth_to_transcript: dict[int, int] = {}
    used_transcript: set[int] = set()
    for _ratio, t_index, g_index in scored:
        if t_index in truth_to_transcript or g_index in used_transcript:
            continue
        truth_to_transcript[t_index] = g_index
        used_transcript.add(g_index)
    unmatched_transcript = [index for index in range(len(transcript_lines)) if index not in used_transcript]
    return truth_to_transcript, unmatched_transcript


def evaluate_card_transcript(*, truth_lines: Sequence[str], transcript: str) -> tuple[Verdict, float, int, tuple[TextGateFinding, ...]]:
    """一卡判定：返回 (verdict, coverage, matched_line_count, findings)。"""

    if not truth_lines:
        raise ValueError("truth_lines must not be empty")
    # 真值条目可含内嵌换行（一个元素跨多物理行）：拆成行级匹配单元（模块 docstring 口径）。
    truth_lines = [segment.strip() for element in truth_lines for segment in str(element).splitlines() if segment.strip()]
    if not truth_lines:
        raise ValueError("truth_lines must not be empty after line expansion")
    transcript_lines = [line.strip() for line in transcript.splitlines() if line.strip()]
    if not transcript_lines:
        finding = TextGateFinding(
            code="empty_transcript",
            severity="hard_fail",
            detail="transcript is empty; nothing rendered or transcriber failed",
        )
        return "hard_fail", 0.0, 0, (finding,)
    findings: list[TextGateFinding] = []
    pairing, unmatched_transcript = _pair_lines(truth_lines, transcript_lines)
    matched = 0
    for t_index, truth_line in enumerate(truth_lines):
        g_index = pairing.get(t_index)
        if g_index is None:
            findings.append(
                TextGateFinding(
                    code="line_missing",
                    severity="hard_fail",
                    truth_line=truth_line,
                    detail="expected visible text has no counterpart in transcript (coverage hard check, b2-D2)",
                )
            )
            continue
        matched += 1
        finding = compare_line(truth_line, transcript_lines[g_index])
        if finding is not None:
            findings.append(finding)
    for g_index in unmatched_transcript:
        findings.append(
            TextGateFinding(
                code="line_extra",
                severity="warn",
                transcript_line=transcript_lines[g_index],
                detail="transcript line has no counterpart in expected visible texts (decoration tolerated as warn)",
            )
        )
    coverage = matched / len(truth_lines)
    if any(item.severity == "hard_fail" for item in findings):
        verdict: Verdict = "hard_fail"
    elif findings:
        verdict = "warn"
    else:
        verdict = "pass"
    return verdict, coverage, matched, tuple(findings)


def build_step4_text_gate_report(
    *,
    run_dir: str,
    truth_fixture_path: str,
    truth_fixture_sha256: str,
    transcriber_id: str,
    cards: Sequence[Mapping[str, Any]],
    truths_by_card_id: Mapping[str, Sequence[str]],
    transcribe: Callable[[bytes], str],
    read_image: Callable[[str], bytes],
) -> Step4TextGateReport:
    """全卡评估并出自证报告。

    ``cards``：requests-manifest 条目（card_index/source_card_id/render_presentation +
    调用方补充的 image_path）；``truths_by_card_id``：source_card_id → expected_texts；
    ``read_image``：image_path → bytes（调用方定根，便于测试注入）；``transcribe``：注入转写面。
    """

    verdicts: list[TextGateCardVerdict] = []
    for entry in sorted(cards, key=lambda item: int(item["card_index"])):
        source_card_id = str(entry["source_card_id"])
        truth_lines = list(truths_by_card_id.get(source_card_id) or ())
        if not truth_lines:
            raise ValueError(f"no expected_texts truth for card {source_card_id}")
        image_path = str(entry["image_path"])
        image_bytes = read_image(image_path)
        transcript = transcribe(image_bytes)
        verdict, coverage, matched, findings = evaluate_card_transcript(
            truth_lines=truth_lines, transcript=transcript
        )
        payload = {
            "card_index": int(entry["card_index"]),
            "source_card_id": source_card_id,
            "render_presentation": str(entry["render_presentation"]),
            "image_path": image_path,
            "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
            "verdict": verdict,
            "coverage": round(coverage, 4),
            "truth_line_count": len(truth_lines),
            "matched_line_count": matched,
            "findings": tuple(findings),
        }
        verdicts.append(hash_model(TextGateCardVerdict, payload, "card_sha256"))  # type: ignore[arg-type]
    report_payload = {
        "run_dir": run_dir,
        "truth_fixture_path": truth_fixture_path,
        "truth_fixture_sha256": truth_fixture_sha256,
        "transcriber_id": transcriber_id,
        "cards": tuple(verdicts),
        "pass_count": sum(1 for item in verdicts if item.verdict == "pass"),
        "warn_count": sum(1 for item in verdicts if item.verdict == "warn"),
        "hard_fail_count": sum(1 for item in verdicts if item.verdict == "hard_fail"),
        "overall_verdict": (
            "hard_fail"
            if any(item.verdict == "hard_fail" for item in verdicts)
            else ("warn" if any(item.verdict == "warn" for item in verdicts) else "pass")
        ),
    }
    return hash_model(Step4TextGateReport, report_payload, "report_sha256")  # type: ignore[return-value]
