"""Deterministic numeric-token classification (regex constants byte-exact)."""
from __future__ import annotations
import re

from pydantic import Field, model_validator

from ...materials.schema import StrictModel

from .enums import NumericTokenClass


_NUMERIC_DECIMAL = r"\d+(?:\.\d+)?"


_NUMERIC_UNIT = r"(?:%|‰|mm/h|mm|cm|MPa|kPa|Pa|m|h|小时|分钟|天|年|倍|万元|亿元|万|台|个|题)"


_QUANTITATIVE_RATIO_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9.]){_NUMERIC_DECIMAL}\s*(?:/|／|:|：|∶)\s*{_NUMERIC_DECIMAL}(?![A-Za-z0-9.])"
)


_QUANTITATIVE_RANGE_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9.]){_NUMERIC_DECIMAL}\s*(?:[~～]|至|到|[-‐‑‒–—])\s*{_NUMERIC_DECIMAL}(?:\s*{_NUMERIC_UNIT})?(?![A-Za-z0-9.])",
    re.IGNORECASE,
)


_QUANTITATIVE_TOKEN_PATTERN = re.compile(
    rf"(?:[≤≥<>]=?\s*{_NUMERIC_DECIMAL}(?:\s*{_NUMERIC_UNIT})?|{_NUMERIC_DECIMAL}\s*{_NUMERIC_UNIT})",
    re.IGNORECASE,
)


_TABLE_GRID_DIMENSION_PATTERNS = (
    re.compile(r"(?<!\d)(\d+)\s*(?:[xX×])\s*(\d+)(?!\d)"),
    re.compile(r"(?<!\d)(\d+)\s*行\s*(\d+)\s*列(?!\d)"),
)


_MATERIAL_GRADE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:HPB|HRB|RRB|Q|C)\s*-?\s*\d{2,4}(?![A-Za-z0-9])", re.IGNORECASE)


_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:P\d+(?:\s*[~～-]\s*\d+)?|(?=[A-Za-z0-9/._-]*\d)(?=[A-Za-z0-9/._-]*[A-Za-z])[A-Za-z][A-Za-z0-9/._-]+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


_SECTION_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)+(?:[-‐‑‒–—]\d+)?(?![A-Za-z0-9.])")


_LIST_INDEX_PATTERN = re.compile(
    r"(?:[（(]\s*\d+\s*[）)]|(?<!\d)\d+\s*[、.)）](?!\d)|^[ \t]*\d+[ \t]*$|[①-⑳])",
    re.MULTILINE,
)


_ORDINAL_REFERENCE_PATTERN = re.compile(r"第\s*\d+(?:\s*(?:[~～]|至|到|[-‐‑‒–—])\s*\d+)?\s*(?:章|节|条|款|项|目|列|行|页|表)")


_BARE_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![A-Za-z0-9.])")


class NumericTokenClassificationRecord(StrictModel):
    value: str = Field(min_length=1)
    classification: NumericTokenClass
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_span(self) -> "NumericTokenClassificationRecord":
        if self.end <= self.start:
            raise ValueError("numeric token classification requires a non-empty source span")
        return self


def classify_numeric_tokens(text: str) -> tuple[NumericTokenClassificationRecord, ...]:
    """Classify numeric-looking spans as complete semantic expressions.

    Ordering is deliberate. A ratio, range, parenthesized ordinal, section
    reference, material grade, or identifier must occupy its entire source
    range before the bare-number fallback can see any of its digits.
    """

    records: list[NumericTokenClassificationRecord] = []
    occupied: list[tuple[int, int]] = []

    def collect(pattern: re.Pattern[str], classification: NumericTokenClass) -> None:
        for match in pattern.finditer(text):
            start, end = match.span()
            raw_value = match.group(0)
            left_trim = len(raw_value) - len(raw_value.lstrip())
            right_trim = len(raw_value) - len(raw_value.rstrip())
            start += left_trim
            end -= right_trim
            if start >= end or any(start < observed_end and end > observed_start for observed_start, observed_end in occupied):
                continue
            occupied.append((start, end))
            records.append(
                NumericTokenClassificationRecord(
                    value=text[start:end],
                    classification=classification,
                    start=start,
                    end=end,
                )
            )

    collect(_MATERIAL_GRADE_PATTERN, NumericTokenClass.MATERIAL_GRADE)
    collect(_IDENTIFIER_PATTERN, NumericTokenClass.IDENTIFIER)
    collect(_SECTION_NUMBER_PATTERN, NumericTokenClass.SECTION_NUMBER)
    # Complete ratios/ranges must claim their source range before a trailing
    # Chinese list delimiter can misclassify the denominator as ``400、``.
    collect(_QUANTITATIVE_RATIO_PATTERN, NumericTokenClass.QUANTITATIVE_VALUE)
    collect(_QUANTITATIVE_RANGE_PATTERN, NumericTokenClass.QUANTITATIVE_VALUE)
    collect(_LIST_INDEX_PATTERN, NumericTokenClass.LIST_INDEX)
    collect(_ORDINAL_REFERENCE_PATTERN, NumericTokenClass.LIST_INDEX)
    collect(_QUANTITATIVE_TOKEN_PATTERN, NumericTokenClass.QUANTITATIVE_VALUE)
    collect(_BARE_NUMBER_PATTERN, NumericTokenClass.QUANTITATIVE_VALUE)
    return tuple(sorted(records, key=lambda item: (item.start, item.end)))
