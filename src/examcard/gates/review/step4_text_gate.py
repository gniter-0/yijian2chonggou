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
- **reflow 容忍（A2 金标集口径：纯换行 reflow＝软分隔＝pass）**：四段式配对——
  ① 精确配对（语义+标点 token 全等）；② reflow 修复：真值行 ↔ **相邻**未配对转写行
  拼接（拆行向），或转写行 ↔ 相邻未配对真值行拼接（并行向），语义 token 全等即配对
  （标点差记 warn）；③ 相似度贪心配对（真实差异行，产出 semantic/punct findings）；
  ④ 余量 → line_missing / line_extra；
- **行间顺序无关**：真值行与转写行按相似度贪心配对（表格卡行序视觉上可变）；
- **行内严格**：配对行内逐 token 比对——语义 token（CJK 字、拉丁字母数字串、比较/数学符
  ≤<>≥=±×∑ 与 %‰℃ 等单位记号）不一致 → hard_fail；仅标点 token（，。、；：？！""''（）
  及半角对应物等）差异 → warn；
- 覆盖率 = 配对成功的真值行 / 全部真值行；任何真值行落空（line_missing）→ hard_fail；
- 转写多余行 → warn（装饰性文字容忍为警告）；空转写 → hard_fail（empty_transcript）；
- 空白差异忽略；全角/半角**同类标点互替仍记 punct_diff**（保守：报 warn 留人裁）。

机拦人裁分层（A2 校准 R5，report/1.1）：门禁 hard_fail **≠ 自动废图**——两道零漏报支撑
「机器只负责拦截，处置权路由人工/重发道」：逐卡 ``disposition`` 按 verdict 定向
pass→auto_accept、warn→auto_archive_with_note（自动记档放行）、hard_fail→human_adjudication
（人工确认后定夺重发/豁免），映射由 validator 钉死防篡改。

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
    "DISPOSITION_BY_VERDICT",
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

STEP4_TEXT_GATE_VERSION = "step4-text-gate-report/1.1"

# 语义 token：CJK / 拉丁字母数字串 / 数值（含小数与百分比）/ 比较与数学符号（考点级，D2 符号谱系）。
_SEMANTIC_TOKEN = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf]"          # 单个 CJK 字
    r"|[A-Za-z0-9]+(?:\.[0-9]+)?"            # 拉丁/数字串（含小数）
    r"|[≤≥<>＜＞≦≧=±×÷∑Σ√∞≈≠~％%‰℃°]"          # 比较/数学/单位记号（含全角比较符）——错一个即考点级
)
# 标点 token：全半角常用标点（差异只记 warn）；写成 \u 转义防引号书写歧义。
_PUNCT_TOKEN = re.compile(
    r"[\uff0c\u3002\u3001\uff1b\uff1a\uff1f\uff01"      # ，。、；：？！
    r"\u201c\u201d\u2018\u2019\u300e\u300f\u300c\u300d"  # “”‘’『』「」
    r"\uff08\uff09\u3010\u3011"                          # （）【】
    r",.;:?!\"'()\[\]{}\-"
    r"\u2014\u2013\u2026\u00b7\\]"                       # —–…·
)
# 布局分隔记号（A2 校准 G1/R4：真值用「|」标注单元格边界、vision 转写常用「/」代——
# 图面上是网格线而非字符，两侧一律丢弃不参与比对；内容斜杠（km/h）两侧对称丢弃仍等价）。
_LAYOUT_TOKEN = re.compile(r"[|/∣｜／]")
# R1 显式等值字形折叠表（NFKC 之外无兼容分解的视觉同形对；只折字形不折语义，＞ vs ≥ 照旧 hard）。
_GLYPH_FOLD = str.maketrans({"∑": "Σ", "≦": "≤", "≧": "≥"})


FindingCode = Literal["line_missing", "line_extra", "semantic_diff", "punct_diff", "empty_transcript"]
Severity = Literal["hard_fail", "warn"]
Verdict = Literal["pass", "warn", "hard_fail"]
Disposition = Literal["auto_accept", "auto_archive_with_note", "human_adjudication"]

# R5 机拦人裁分层路由（verdict → 处置）：hard_fail 只拦截不废图，定向人工/重发道。
DISPOSITION_BY_VERDICT: Mapping[str, str] = {
    "pass": "auto_accept",
    "warn": "auto_archive_with_note",
    "hard_fail": "human_adjudication",
}


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
    disposition: Disposition
    coverage: float = Field(ge=0.0, le=1.0)
    truth_line_count: int = Field(ge=1)
    matched_line_count: int = Field(ge=0)
    findings: tuple[TextGateFinding, ...] = ()
    card_sha256: str = Field(pattern=SHA256_HEX)

    @model_validator(mode="after")
    def validate_card(self) -> "TextGateCardVerdict":
        if self.disposition != DISPOSITION_BY_VERDICT[self.verdict]:
            raise ValueError("text gate card disposition must follow verdict routing (R5)")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"schema_version", "card_sha256"}))
        if self.card_sha256 != expected:
            raise ValueError("text gate card verdict hash mismatch")
        return self


class Step4TextGateReport(StrictModel):
    schema_version: Literal["step4-text-gate-report/1.1"] = STEP4_TEXT_GATE_VERSION
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
    # A2 契约归一化：空白不敏感（#09 数字 kerning 先例）+ NFKC 全/半角等值形折叠
    # （＜≡<、（）≡()、，≡, 等宽度变体等同；，→； 这类**符号替换**折叠后仍相异 → 照记 punct_diff）
    # + R1 显式字形折叠（∑≡Σ、≦≡≤、≧≡≥——NFKC 无兼容分解的视觉同形对）。
    return unicodedata.normalize("NFKC", re.sub(r"\s+", "", line)).translate(_GLYPH_FOLD)


def tokenize_visible_text(line: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """一行 → (语义 token 序列, 标点 token 序列)；空白丢弃，未知字符归语义面（保守从严）。"""

    compact = _normalize_line(line)
    semantic: list[str] = []
    punct: list[str] = []
    cursor = 0
    while cursor < len(compact):
        match = _LAYOUT_TOKEN.match(compact, cursor)
        if match is not None:
            cursor = match.end()  # 布局分隔记号：丢弃不参与比对
            continue
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


_REFLOW_MAX_SPAN = 4  # 一个可见元素被 reflow 拆开时允许的最大相邻行数


def _similarity_pairs(
    truth_indexes: Sequence[int],
    transcript_indexes: Sequence[int],
    truth_lines: Sequence[str],
    transcript_lines: Sequence[str],
) -> dict[int, int]:
    """行间顺序无关贪心配对（相似度 ≥0.5 全对排序取最优）；输入为未配对下标集。"""

    scored: list[tuple[float, int, int]] = []
    for t_index in truth_indexes:
        truth_norm = unicodedata.normalize("NFKC", _normalize_line(truth_lines[t_index]))
        for g_index in transcript_indexes:
            got_norm = unicodedata.normalize("NFKC", _normalize_line(transcript_lines[g_index]))
            ratio = SequenceMatcher(None, truth_norm, got_norm).ratio()
            if ratio >= 0.5:
                scored.append((ratio, t_index, g_index))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    pairs: dict[int, int] = {}
    used: set[int] = set()
    for _ratio, t_index, g_index in scored:
        if t_index in pairs or g_index in used:
            continue
        pairs[t_index] = g_index
        used.add(g_index)
    return pairs


def _consecutive_unmatched_runs(indexes: Sequence[int], unmatched: set[int], span: int) -> list[tuple[int, ...]]:
    """在原始行序上取「全部未配对」的连续窗口（长度 2..span）。"""

    runs: list[tuple[int, ...]] = []
    ordered = [index for index in indexes if index in unmatched]
    for start_pos, start in enumerate(ordered):
        window = [start]
        cursor = start
        for follower in ordered[start_pos + 1 :]:
            if follower != cursor + 1:
                break
            window.append(follower)
            cursor = follower
            if 2 <= len(window) <= span:
                runs.append(tuple(window))
            if len(window) >= span:
                break
    return runs


def evaluate_card_transcript(*, truth_lines: Sequence[str], transcript: str) -> tuple[Verdict, float, int, tuple[TextGateFinding, ...]]:
    """一卡判定：返回 (verdict, coverage, matched_line_count, findings)。"""

    if not truth_lines:
        raise ValueError("truth_lines must not be empty")
    # 真值条目可含内嵌换行（一个元素跨多物理行）：拆成行级匹配单元（模块 docstring 口径），
    # 保留行→元素归属（元素级 token 流对账需要，②′段）。
    truth_elements = [str(element) for element in truth_lines]
    expanded: list[str] = []
    element_of: list[int] = []
    for element_index, element in enumerate(truth_elements):
        for segment in element.splitlines():
            segment = segment.strip()
            if segment:
                expanded.append(segment)
                element_of.append(element_index)
    truth_lines = expanded
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
    truth_tokens = [tokenize_visible_text(line) for line in truth_lines]
    got_tokens = [tokenize_visible_text(line) for line in transcript_lines]

    # 表格行标签合并等价（A2 #07 口径：重复行标签渲染为合并单元格＝语义等价重组＝pass）：
    # 同元素内「LABEL | CONTENT」行的 LABEL 若与此前行重复，生成去标签备选形参与精确配对。
    label_pattern = re.compile(r"^([^|]{1,24}?)\s*\|\s*(.+)$")
    alt_tokens: list[tuple[tuple[str, ...], tuple[str, ...]] | None] = []
    seen_labels: dict[int, set[str]] = {}
    for line, element_index in zip(truth_lines, element_of):
        match = label_pattern.match(line)
        alternate = None
        if match is not None:
            label = match.group(1).strip()
            seen = seen_labels.setdefault(element_index, set())
            if label in seen:
                alternate = tokenize_visible_text(match.group(2).strip())
            seen.add(label)
        alt_tokens.append(alternate)

    open_truth = set(range(len(truth_lines)))
    open_transcript = set(range(len(transcript_lines)))
    matched = 0

    # ── ① 精确配对（语义+标点 token 全等；行序无关；重复行标签可去标签匹配）────
    got_by_tokens: dict[tuple[tuple[str, ...], tuple[str, ...]], list[int]] = {}
    for g_index in sorted(open_transcript):
        got_by_tokens.setdefault(got_tokens[g_index], []).append(g_index)
    for t_index in sorted(open_truth):
        bucket = got_by_tokens.get(truth_tokens[t_index])
        if not bucket and alt_tokens[t_index] is not None:
            bucket = got_by_tokens.get(alt_tokens[t_index])
        if bucket:
            g_index = bucket.pop(0)
            open_truth.discard(t_index)
            open_transcript.discard(g_index)
            matched += 1

    # ── ② reflow 修复（A2 口径：纯换行 reflow＝软分隔）────────────────────
    def _concat(tokens: Sequence[tuple[tuple[str, ...], tuple[str, ...]]], indexes: Sequence[int]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        semantic: list[str] = []
        punct: list[str] = []
        for index in indexes:
            semantic.extend(tokens[index][0])
            punct.extend(tokens[index][1])
        return tuple(semantic), tuple(punct)

    # 拆行向：真值行 ↔ 相邻未配对转写行拼接。
    for t_index in sorted(open_truth):
        candidate = None
        for run in _consecutive_unmatched_runs(range(len(transcript_lines)), open_transcript, _REFLOW_MAX_SPAN):
            semantic, punct = _concat(got_tokens, run)
            if semantic == truth_tokens[t_index][0]:
                candidate = (run, punct)
                break
        if candidate is None:
            continue
        run, punct = candidate
        open_truth.discard(t_index)
        open_transcript.difference_update(run)
        matched += 1
        if punct != truth_tokens[t_index][1]:
            findings.append(
                TextGateFinding(
                    code="punct_diff",
                    severity="warn",
                    truth_line=truth_lines[t_index],
                    transcript_line=" / ".join(transcript_lines[index] for index in run),
                    detail="reflow-joined lines match semantically but punctuation diverges",
                )
            )

    # 并行向：转写行 ↔ 相邻未配对真值行拼接。
    for g_index in sorted(open_transcript):
        candidate = None
        for run in _consecutive_unmatched_runs(range(len(truth_lines)), open_truth, _REFLOW_MAX_SPAN):
            semantic, punct = _concat(truth_tokens, run)
            if semantic == got_tokens[g_index][0]:
                candidate = (run, punct)
                break
        if candidate is None:
            continue
        run, punct = candidate
        open_transcript.discard(g_index)
        open_truth.difference_update(run)
        matched += len(run)
        if punct != got_tokens[g_index][1]:
            findings.append(
                TextGateFinding(
                    code="punct_diff",
                    severity="warn",
                    truth_line=" / ".join(truth_lines[index] for index in run),
                    transcript_line=transcript_lines[g_index],
                    detail="merged truth lines match semantically but punctuation diverges",
                )
            )

    # ── ②′ 元素级 token 流对账（跨换行点重排 + 内部缺陷共存的卡，#01-r1 形态）──
    # 元素的构成行经 ①② 仍未全配对时：对「该元素未配对行拼接」与「相邻未配对转写行
    # 拼接窗口」做相似度选窗（≥0.6），再在语义 token 流上跑 SequenceMatcher——
    # replace/delete/insert 逐段产出 semantic_diff（hard），语义全等仅标点异则 punct_diff（warn）。
    element_open: dict[int, list[int]] = {}
    for t_index in sorted(open_truth):
        element_open.setdefault(element_of[t_index], []).append(t_index)
    for element_index in sorted(element_open):
        pending = element_open[element_index]

        def _variant(use_alt: bool) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
            texts: list[str] = []
            semantic: list[str] = []
            punct: list[str] = []
            for index in pending:
                if use_alt and alt_tokens[index] is not None:
                    stripped = label_pattern.match(truth_lines[index])
                    assert stripped is not None
                    texts.append(stripped.group(2).strip())
                    tokens = alt_tokens[index]
                else:
                    texts.append(truth_lines[index])
                    tokens = truth_tokens[index]
                semantic.extend(tokens[0])
                punct.extend(tokens[1])
            return "".join(texts), tuple(semantic), tuple(punct)

        # 重复行标签可去标签参与元素级对账（真转写「标签独立成行/合并单元格+自由折行」形态，
        # A2 校准 R3 剩余项）：全形与去标签形两个候选取窗口相似度更高者。
        variants = [_variant(False)]
        if any(alt_tokens[index] is not None for index in pending):
            variants.append(_variant(True))
        best: tuple[float, tuple[int, ...], int] | None = None
        ordered_open = sorted(open_transcript)
        for variant_index, (truth_concat, _sem, _pun) in enumerate(variants):
            truth_norm = unicodedata.normalize("NFKC", _normalize_line(truth_concat))
            for start_pos in range(len(ordered_open)):
                window: list[int] = []
                cursor: int | None = None
                for follower in ordered_open[start_pos:]:
                    if cursor is not None and follower != cursor + 1:
                        break
                    window.append(follower)
                    cursor = follower
                    got_concat = "".join(transcript_lines[index] for index in window)
                    got_norm = unicodedata.normalize("NFKC", _normalize_line(got_concat))
                    ratio = SequenceMatcher(None, truth_norm, got_norm).ratio()
                    if ratio >= 0.6 and (best is None or ratio > best[0]):
                        best = (ratio, tuple(window), variant_index)
        if best is None:
            continue
        _ratio, window_indexes, variant_index = best
        truth_concat, truth_semantic, truth_punct = variants[variant_index]
        got_semantic, got_punct = _concat(got_tokens, window_indexes)
        matcher = SequenceMatcher(None, truth_semantic, got_semantic)
        semantic_findings = 0
        for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
            if opcode == "equal":
                continue
            semantic_findings += 1
            findings.append(
                TextGateFinding(
                    code="semantic_diff",
                    severity="hard_fail",
                    truth_line="".join(truth_semantic[i1:i2]) or None,
                    transcript_line="".join(got_semantic[j1:j2]) or None,
                    detail=(
                        f"element-level token diff ({opcode}) inside truth element {element_index + 1}: "
                        f"truth segment {list(truth_semantic[i1:i2])} vs transcript {list(got_semantic[j1:j2])}"
                    ),
                )
            )
        if semantic_findings == 0 and truth_punct != got_punct:
            findings.append(
                TextGateFinding(
                    code="punct_diff",
                    severity="warn",
                    truth_line=truth_concat[:80],
                    transcript_line="".join(transcript_lines[index] for index in window_indexes)[:80],
                    detail="element-level punctuation diverges (relined element, semantic tokens equal)",
                )
            )
        elif semantic_findings > 0 and truth_punct != got_punct:
            findings.append(
                TextGateFinding(
                    code="punct_diff",
                    severity="warn",
                    truth_line=truth_concat[:80],
                    transcript_line="".join(transcript_lines[index] for index in window_indexes)[:80],
                    detail="element-level punctuation also diverges alongside semantic diffs",
                )
            )
        open_truth.difference_update(pending)
        open_transcript.difference_update(window_indexes)
        matched += len(pending)

    # ── ③ 相似度贪心配对（真实差异行）──────────────────────────────────
    pairing = _similarity_pairs(sorted(open_truth), sorted(open_transcript), truth_lines, transcript_lines)
    for t_index, g_index in pairing.items():
        open_truth.discard(t_index)
        open_transcript.discard(g_index)
        matched += 1
        finding = compare_line(truth_lines[t_index], transcript_lines[g_index])
        if finding is not None:
            findings.append(finding)

    # ── ④ 余量 ────────────────────────────────────────────────────────
    for t_index in sorted(open_truth):
        findings.append(
            TextGateFinding(
                code="line_missing",
                severity="hard_fail",
                truth_line=truth_lines[t_index],
                detail="expected visible text has no counterpart in transcript (coverage hard check, b2-D2)",
            )
        )
    for g_index in sorted(open_transcript):
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
            "disposition": DISPOSITION_BY_VERDICT[verdict],
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
