"""Deterministic layout and semantic-role classification for Step 1 evidence."""
from __future__ import annotations

import re
import statistics
from collections import Counter

from examcard.materials.schema import SemanticRole

from .engine_types import NativeLine, NativePage
from .schema import EvidenceKind


_PAGE_NUMBER = re.compile(r"^\s*(?:第?\d+页?|\d+)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[（(]?\d+[)）.、]|[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十]+[、.])")
_HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+){1,}|\d+\.(?=\D)|第[一二三四五六七八九十百]+[章节]|[一二三四五六七八九十]+、)")
_EXAM_TAG = re.compile(r"(?:19|20)\d{2}\s*(?:年)?\s*(?:单|多|案|真题)")
_EXERCISE = re.compile(r"(?:例题|【例|练习题|单项选择|多项选择|案例分析|下列.*(?:正确|错误))")
_EXERCISE_ANSWER = re.compile(r"^\s*(?:【答案】|答案\s*[：:])")
_FORMULA = re.compile(r"[=＝≈≠≤≥<>±×÷]|(?:不超过|不少于|大于|小于)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|mm|cm|m|km|MPa|kPa|℃|d|h|s|倍|年|月|天)", re.IGNORECASE)
_NOISE_TEXT = re.compile(
    r"(?:嗨学|haixue\.com|职业价值点亮者|扫码关注|学员专用|请勿外泄|"
    r"(?:课程|押题|绝密押题).*联系(?:QQ|微信)|联系(?:QQ|微信)[：:]?\s*\d+|"
    r"名师面授|考前三页纸|精准押题)",
    re.IGNORECASE,
)


def normalized_compare_text(value: str) -> str:
    normalized = re.sub(r"[\s\u3000]+", "", value)
    return normalized.translate(str.maketrans({"．": ".", "，": ",", "：": ":", "＞": ">", "＜": "<", "～": "~"}))


def equivalent_text(left: str, right: str, *, strict_formula: bool = False) -> bool:
    """Compare OCR text without turning typography-only drift into a source conflict.

    Formula operators and enumeration ordinals deliberately stay strict: omitting a
    summation sign, minus sign or item number is source-significant, while full/half
    width brackets and a decorative page-range prefix are not.
    """
    left_normalized = normalized_compare_text(left)
    right_normalized = normalized_compare_text(right)
    if left_normalized == right_normalized:
        return True
    if strict_formula:
        return False
    removable = re.compile(r"[|丨（）()［］\[\]{}，,。.:：;；、]")
    left_relaxed = removable.sub("", left_normalized)
    right_relaxed = removable.sub("", right_normalized)
    if left_relaxed == right_relaxed:
        return True
    # The publisher concatenates P425~428 directly with a numeric section title;
    # after whitespace normalization there is no reliable digit boundary to parse.
    if left_normalized.startswith("P") and left_relaxed.endswith(right_relaxed) and len(right_relaxed) >= 3:
        return True
    if right_normalized.startswith("P") and right_relaxed.endswith(left_relaxed) and len(left_relaxed) >= 3:
        return True
    return False


def bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def bbox_intersects(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    return left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]


def bbox_iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    if not bbox_intersects(left, right):
        return 0.0
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1, y1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = bbox_area(left) + bbox_area(right) - intersection
    return intersection / union if union else 0.0


def expand_bbox(bbox: tuple[float, float, float, float], *, margin: float, width: float, height: float) -> tuple[float, float, float, float]:
    return (
        max(0.0, bbox[0] - margin),
        max(0.0, bbox[1] - margin),
        min(width, bbox[2] + margin),
        min(height, bbox[3] + margin),
    )


def detect_repeated_noise(pages: tuple[NativePage, ...]) -> frozenset[str]:
    """Identify repeated header/footer strings without deleting their evidence."""
    counts: Counter[str] = Counter()
    minimum = max(2, round(len(pages) * 0.6))
    for page in pages:
        for line in page.lines:
            key = normalized_compare_text(line.text)
            y_ratio = (line.bbox[1] + line.bbox[3]) / 2 / page.height
            if key and (y_ratio <= 0.13 or y_ratio >= 0.87):
                counts[key] += 1
    return frozenset(key for key, count in counts.items() if count >= minimum)


def classify_native_line(
    line: NativeLine,
    *,
    page: NativePage,
    repeated_noise: frozenset[str],
) -> tuple[EvidenceKind, SemanticRole, frozenset[str], bool]:
    text = line.text.strip()
    normalized = normalized_compare_text(text)
    y_ratio = (line.bbox[1] + line.bbox[3]) / 2 / page.height
    if _PAGE_NUMBER.fullmatch(text):
        return EvidenceKind.PAGE_NUMBER, SemanticRole.PAGE_NUMBER, frozenset({"page_number"}), False
    if normalized in repeated_noise or _NOISE_TEXT.search(text):
        role = SemanticRole.HEADER_FOOTER if y_ratio <= 0.13 or y_ratio >= 0.87 else SemanticRole.WATERMARK
        kind = EvidenceKind.HEADER_FOOTER if role == SemanticRole.HEADER_FOOTER else EvidenceKind.WATERMARK
        return kind, role, frozenset({"repeated_noise"}), False
    if _EXAM_TAG.search(text):
        return EvidenceKind.EXAM_TAG, SemanticRole.EXAM_TAG, frozenset({"exam_tag"}), False
    if _EXERCISE_ANSWER.search(text):
        return EvidenceKind.EXERCISE, SemanticRole.EXERCISE_ANSWER, frozenset({"exercise", "exercise_answer"}), True
    if _EXERCISE.search(text):
        return EvidenceKind.EXERCISE, SemanticRole.WORKED_EXAMPLE, frozenset({"exercise"}), True
    sizes = [span.size for span in line.spans if span.size > 0]
    all_sizes = [span.size for item in page.lines for span in item.spans if span.size > 0]
    median_size = statistics.median(all_sizes) if all_sizes else 0
    if _HEADING.search(text) or (sizes and median_size and max(sizes) >= median_size * 1.25 and len(text) <= 48):
        return EvidenceKind.HEADING, SemanticRole.HEADING, frozenset({"heading"}), False
    if _FORMULA.search(text) and ("=" in text or "＝" in text or _NUMBER.search(text)):
        return EvidenceKind.FORMULA, SemanticRole.CORE, frozenset({"formula", "numeric"}), True
    if _LIST_ITEM.search(text):
        return EvidenceKind.LIST_ITEM, SemanticRole.CORE, frozenset({"enumeration"}), True
    risk_tags: set[str] = set()
    if _NUMBER.search(text):
        risk_tags.add("numeric")
    if any(token in text for token in ("不得", "不应", "严禁", "必须", "不超过", "不少于")):
        risk_tags.add("constraint")
    return EvidenceKind.PARAGRAPH, SemanticRole.CORE, frozenset(risk_tags), bool(risk_tags)


def heading_level(text: str) -> int:
    """Return a conservative outline level for a heading evidence unit."""
    value = text.strip()
    numeric = re.match(r"^(\d+(?:\.\d+)+)", value)
    if numeric:
        return numeric.group(1).count(".") + 1
    if re.match(r"^\d+\.(?=\D)", value):
        return 1
    if re.match(r"^第[一二三四五六七八九十百]+[章节]", value):
        return 1
    return 1


def classify_visual_text(text: str, *, bbox: tuple[float, float, float, float], page: NativePage) -> tuple[EvidenceKind, SemanticRole, frozenset[str], bool]:
    y_ratio = (bbox[1] + bbox[3]) / 2 / page.height
    if _PAGE_NUMBER.fullmatch(text):
        return EvidenceKind.PAGE_NUMBER, SemanticRole.PAGE_NUMBER, frozenset({"page_number"}), False
    if _NOISE_TEXT.search(text):
        role = SemanticRole.HEADER_FOOTER if y_ratio <= 0.13 or y_ratio >= 0.87 else SemanticRole.WATERMARK
        kind = EvidenceKind.HEADER_FOOTER if role == SemanticRole.HEADER_FOOTER else EvidenceKind.WATERMARK
        return kind, role, frozenset({"watermark"}), False
    return EvidenceKind.PARAGRAPH, SemanticRole.CORE, frozenset({"visual_only"}), False
