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
- **reflow 容忍（A2 金标集口径：纯换行 reflow＝软分隔＝pass；R2 全序对齐，改进2-③）**：
  三段式配对——① 精确配对（语义+标点 token 全等）；② **全序 LCS 对齐**（A2 校准 R2）：
  未配对转写行按原始行序**全量**拼接成有序 token 流，逐真值元素（其未配对行拼接；
  重复行标签有去标签备选形，两形取优）做 LCS 子序列匹配——无相邻窗口、无跨度上限，
  行怎么碎/怎么并均不依赖窗口启发式；LCS 目标函数按（语义命中, 标点命中）字典序
  最大化（标点绝不与语义争抢对位），差异区段含语义 token → semantic_diff（hard）、
  仅标点 → 元素级 punct_diff（warn）；元素语义命中率 <50% 视为无对应（回落
  line_missing，防垃圾对齐吞掉真漏渲）；③ 余量 → line_missing / line_extra；
- **行间顺序无关**：① 精确配对本就无序；② 每元素各自在全量未消费流上重扫（元素间
  乱序照样各找各的对位），仅元素**内部** token 顺序按 LCS 保序（行内错序＝真差异）；
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
from typing import Any, Callable, Final, Literal, Mapping, Sequence

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
    "extract_truths_from_echo_release",
    "resolve_card_image",
)

STEP4_TEXT_GATE_VERSION: Final = "step4-text-gate-report/1.1"

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


def extract_truths_from_echo_release(fixture: Any) -> dict[str, tuple[str, ...]]:
    """echo-batch-release/1.0 → {source_card_id: 真值行}（P8 桥 §3.5：与出图 prompt 同源）。"""

    truths: dict[str, tuple[str, ...]] = {}
    for card in fixture.cards:
        truths[str(card.source_card_id)] = tuple(item.visible_text for item in card.visible_texts)
    if len(truths) != len(fixture.cards):
        raise ValueError("echo release source_card_id values must be unique")
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


def _tokenize_ordered(line: str) -> tuple[tuple[str, str], ...]:
    """一行 → 有序 (kind, token) 流（kind ∈ {"sem","punct"}）；空白与布局记号丢弃，
    未知字符归语义面（保守从严）。语义/标点字符集不相交，token 文本相等即同类同形。"""

    compact = _normalize_line(line)
    tokens: list[tuple[str, str]] = []
    cursor = 0
    while cursor < len(compact):
        match = _LAYOUT_TOKEN.match(compact, cursor)
        if match is not None:
            cursor = match.end()  # 布局分隔记号：丢弃不参与比对
            continue
        match = _SEMANTIC_TOKEN.match(compact, cursor)
        if match is not None:
            tokens.append(("sem", match.group(0)))
            cursor = match.end()
            continue
        match = _PUNCT_TOKEN.match(compact, cursor)
        if match is not None:
            tokens.append(("punct", match.group(0)))
            cursor = match.end()
            continue
        # 未知字符按语义 token 计（宁可 hard 也不放过考点级差异）。
        tokens.append(("sem", compact[cursor]))
        cursor += 1
    return tuple(tokens)


def _split_ordered(ordered: Sequence[tuple[str, str]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    semantic = tuple(text for kind, text in ordered if kind == "sem")
    punct = tuple(text for kind, text in ordered if kind == "punct")
    return semantic, punct


def tokenize_visible_text(line: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """一行 → (语义 token 序列, 标点 token 序列)；空白丢弃，未知字符归语义面（保守从严）。"""

    return _split_ordered(_tokenize_ordered(line))


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


# 元素级 LCS 接受阈：语义命中率低于该值视为无对应（回落 line_missing，防垃圾对齐）。
_LCS_MIN_SEMANTIC_HIT = 0.5


def _lcs_align(
    truth_seq: Sequence[tuple[str, str]],
    got_seq: Sequence[tuple[str, str]],
) -> list[tuple[str, int, int]]:
    """全序 LCS 对齐（A2 校准 R2）：返回操作走带 [(op, t_pos, g_pos)]，op ∈ match/delete/insert，
    delete=真值侧 token 无对位、insert=转写侧 token 无对位；走带按双侧原始顺序完整覆盖两序列。

    目标函数按（语义命中数, 标点命中数）**字典序**最大化——标点绝不与语义争抢对位；
    回溯偏好 match > delete > insert，全程确定性（同分平局走固定分支）。
    """

    n, m = len(truth_seq), len(got_seq)
    zero = (0, 0)
    dp: list[list[tuple[int, int]]] = [[zero] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row = dp[i]
        below = dp[i + 1]
        token = truth_seq[i]
        sem_gain = 1 if token[0] == "sem" else 0
        punct_gain = 1 - sem_gain
        for j in range(m - 1, -1, -1):
            best = below[j] if below[j] >= row[j + 1] else row[j + 1]
            if token == got_seq[j]:
                diag = below[j + 1]
                candidate = (diag[0] + sem_gain, diag[1] + punct_gain)
                if candidate > best:
                    best = candidate
            row[j] = best
    ops: list[tuple[str, int, int]] = []
    i = j = 0
    while i < n or j < m:
        if i < n and j < m and truth_seq[i] == got_seq[j]:
            sem_gain = 1 if truth_seq[i][0] == "sem" else 0
            diag = dp[i + 1][j + 1]
            if dp[i][j] == (diag[0] + sem_gain, diag[1] + (1 - sem_gain)):
                ops.append(("match", i, j))
                i += 1
                j += 1
                continue
        if i < n and (j >= m or dp[i][j] == dp[i + 1][j]):
            ops.append(("delete", i, j))
            i += 1
            continue
        ops.append(("insert", i, j))
        j += 1
    return ops


def evaluate_card_transcript(*, truth_lines: Sequence[str], transcript: str) -> tuple[Verdict, float, int, tuple[TextGateFinding, ...]]:
    """一卡判定：返回 (verdict, coverage, matched_line_count, findings)。"""

    if not truth_lines:
        raise ValueError("truth_lines must not be empty")
    # 真值条目可含内嵌换行（一个元素跨多物理行）：拆成行级匹配单元（模块 docstring 口径），
    # 保留行→元素归属（元素级全序 LCS 对齐需要，②段）。
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
    truth_ordered = [_tokenize_ordered(line) for line in truth_lines]
    got_ordered = [_tokenize_ordered(line) for line in transcript_lines]
    truth_tokens = [_split_ordered(tokens) for tokens in truth_ordered]
    got_tokens = [_split_ordered(tokens) for tokens in got_ordered]

    # 表格行标签合并等价（A2 #07 口径：重复行标签渲染为合并单元格＝语义等价重组＝pass）：
    # 同元素内「LABEL | CONTENT」行的 LABEL 若与此前行重复，生成去标签备选形参与精确配对
    # 与元素级全序对齐（两形取优）。
    label_pattern = re.compile(r"^([^|]{1,24}?)\s*\|\s*(.+)$")
    alt_ordered: list[tuple[tuple[str, str], ...] | None] = []
    seen_labels: dict[int, set[str]] = {}
    for line, element_index in zip(truth_lines, element_of):
        match = label_pattern.match(line)
        alternate = None
        if match is not None:
            label = match.group(1).strip()
            seen = seen_labels.setdefault(element_index, set())
            if label in seen:
                alternate = _tokenize_ordered(match.group(2).strip())
            seen.add(label)
        alt_ordered.append(alternate)
    alt_tokens = [_split_ordered(tokens) if tokens is not None else None for tokens in alt_ordered]

    open_truth = set(range(len(truth_lines)))
    open_transcript = set(range(len(transcript_lines)))
    matched = 0

    # ── ① 精确配对（语义+标点 token 全等；行序无关；重复行标签可去标签匹配）────
    got_by_tokens: dict[tuple[tuple[str, ...], tuple[str, ...]], list[int]] = {}
    for g_index in sorted(open_transcript):
        got_by_tokens.setdefault(got_tokens[g_index], []).append(g_index)
    for t_index in sorted(open_truth):
        bucket = got_by_tokens.get(truth_tokens[t_index])
        alt = alt_tokens[t_index]
        if not bucket and alt is not None:
            bucket = got_by_tokens.get(alt)
        if bucket:
            g_index = bucket.pop(0)
            open_truth.discard(t_index)
            open_transcript.discard(g_index)
            matched += 1

    # ── ② 全序 LCS 对齐（A2 校准 R2，改进2-③：取代旧②相邻 reflow 窗/②′自适应窗/③相似度贪心）──
    # 未配对转写行按原始行序**全量**拼接成有序 token 流；逐真值元素（其未配对行拼接，
    # 重复行标签有去标签备选形，两形取优——A2 #07/R3 口径）在未消费流上做 LCS 子序列匹配：
    # 无相邻窗口、无跨度上限、无窗口选择启发式。差异区段含语义 token → semantic_diff（hard），
    # 仅标点 → 元素级 punct_diff（warn）；元素语义命中率 < 阈值视为无对应（回落 line_missing）。
    element_open: dict[int, list[int]] = {}
    for t_index in sorted(open_truth):
        element_open.setdefault(element_of[t_index], []).append(t_index)

    stream: list[tuple[int, int, tuple[str, str]]] = []  # (转写行号, 行内 token 位, token)
    for g_index in sorted(open_transcript):
        for token_pos, token in enumerate(got_ordered[g_index]):
            stream.append((g_index, token_pos, token))
    consumed: set[tuple[int, int]] = set()

    for element_index in sorted(element_open):
        pending = element_open[element_index]
        open_stream = [entry for entry in stream if (entry[0], entry[1]) not in consumed]
        if not open_stream:
            continue
        got_seq = [entry[2] for entry in open_stream]

        def _variant(use_alt: bool) -> tuple[str, list[tuple[str, str]]]:
            texts: list[str] = []
            tokens: list[tuple[str, str]] = []
            for index in pending:
                if use_alt and alt_ordered[index] is not None:
                    stripped = label_pattern.match(truth_lines[index])
                    assert stripped is not None
                    texts.append(stripped.group(2).strip())
                    tokens.extend(alt_ordered[index])  # type: ignore[arg-type]
                else:
                    texts.append(truth_lines[index])
                    tokens.extend(truth_ordered[index])
            return "".join(texts), tokens

        variants = [_variant(False)]
        if any(alt_ordered[index] is not None for index in pending):
            variants.append(_variant(True))

        # 逐备选形全序对齐，取（hard 区段数, 总区段数, -语义命中）最小者；平局保全形（忠实优先）。
        best: (
            tuple[
                tuple[int, int, int],
                str,
                list[tuple[str, str]],
                list[list[tuple[str, int, int]]],
                list[int],
                list[int],
            ]
            | None
        ) = None
        for truth_concat, truth_seq in variants:
            total_sem = sum(1 for kind, _text in truth_seq if kind == "sem")
            ops = _lcs_align(truth_seq, got_seq)
            match_g_positions = [j for op, _i, j in ops if op == "match"]
            matched_sem = sum(1 for op, i, _j in ops if op == "match" and truth_seq[i][0] == "sem")
            if matched_sem == 0 or (total_sem and matched_sem < total_sem * _LCS_MIN_SEMANTIC_HIT):
                continue
            span_first, span_last = match_g_positions[0], match_g_positions[-1]
            # 走带过滤：match 全保留；delete（真值侧缺失）全保留；insert 仅计配对跨度内的
            # （跨度外的转写余量归还给后续元素 / line_extra 面，不算进本元素的差异）。
            regions: list[list[tuple[str, int, int]]] = []
            current: list[tuple[str, int, int]] = []
            in_span_inserts: list[int] = []
            for op, i, j in ops:
                if op == "match":
                    if current:
                        regions.append(current)
                        current = []
                    continue
                if op == "insert":
                    if j < span_first or j > span_last:
                        continue
                    in_span_inserts.append(j)
                current.append((op, i, j))
            if current:
                regions.append(current)
            hard_regions = sum(
                1
                for region in regions
                if any(
                    (truth_seq[i][0] if op == "delete" else got_seq[j][0]) == "sem"
                    for op, i, j in region
                )
            )
            score = (hard_regions, len(regions), -matched_sem)
            if best is None or score < best[0]:
                best = (score, truth_concat, truth_seq, regions, match_g_positions, in_span_inserts)
        if best is None:
            continue
        _score, truth_concat, truth_seq, regions, match_g_positions, in_span_inserts = best

        semantic_findings = 0
        punct_mismatch = False
        for region in regions:
            deleted_sem = [truth_seq[i][1] for op, i, _j in region if op == "delete" and truth_seq[i][0] == "sem"]
            inserted_sem = [got_seq[j][1] for op, _i, j in region if op == "insert" and got_seq[j][0] == "sem"]
            if any(
                (truth_seq[i][0] if op == "delete" else got_seq[j][0]) == "punct"
                for op, i, j in region
            ):
                punct_mismatch = True
            if not deleted_sem and not inserted_sem:
                continue
            opcode = "replace" if deleted_sem and inserted_sem else ("delete" if deleted_sem else "insert")
            semantic_findings += 1
            findings.append(
                TextGateFinding(
                    code="semantic_diff",
                    severity="hard_fail",
                    truth_line="".join(deleted_sem) or None,
                    transcript_line="".join(inserted_sem) or None,
                    detail=(
                        f"full-order LCS token diff ({opcode}) inside truth element {element_index + 1}: "
                        f"truth segment {deleted_sem} vs transcript {inserted_sem}"
                    ),
                )
            )
        if punct_mismatch:
            span_first, span_last = match_g_positions[0], match_g_positions[-1]
            got_span_text = "".join(got_seq[j][1] for j in range(span_first, span_last + 1))
            findings.append(
                TextGateFinding(
                    code="punct_diff",
                    severity="warn",
                    truth_line=truth_concat[:80],
                    transcript_line=got_span_text[:80],
                    detail=(
                        "element-level punctuation also diverges alongside semantic diffs"
                        if semantic_findings
                        else "element-level punctuation diverges (full-order aligned element, semantic tokens equal)"
                    ),
                )
            )
        for j in match_g_positions:
            consumed.add((open_stream[j][0], open_stream[j][1]))
        for j in in_span_inserts:
            consumed.add((open_stream[j][0], open_stream[j][1]))
        open_truth.difference_update(pending)
        matched += len(pending)

    # ── ③ 余量 ────────────────────────────────────────────────────────
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
        line_tokens = got_ordered[g_index]
        leftover = [token for token_pos, token in enumerate(line_tokens) if (g_index, token_pos) not in consumed]
        if line_tokens and not leftover:
            continue  # 整行 token 已被全序对齐消费（跨元素合并行也在此闭合）
        if line_tokens and len(leftover) < len(line_tokens):
            if all(kind == "punct" for kind, _text in leftover):
                findings.append(
                    TextGateFinding(
                        code="punct_diff",
                        severity="warn",
                        transcript_line=transcript_lines[g_index],
                        detail="transcript line carries stray punctuation beyond the full-order aligned content",
                    )
                )
            else:
                findings.append(
                    TextGateFinding(
                        code="line_extra",
                        severity="warn",
                        transcript_line="".join(text for _kind, text in leftover),
                        detail="transcript tokens beyond expected visible texts within a partially aligned line (decoration tolerated as warn)",
                    )
                )
            continue
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
        verdicts.append(hash_model(TextGateCardVerdict, payload, "card_sha256"))
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
    return hash_model(Step4TextGateReport, report_payload, "report_sha256")
