"""common/junit：JUnit 统计解析与回执构建。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from examcard.common.hashing import file_hash
from examcard.common.junit import JUnitReceipt, junit_stats, parse_junit_receipt

PLAIN_SUITES = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="one" tests="3" failures="1" errors="0" skipped="1"/>
  <testsuite name="two" tests="2.0" failures="0" errors="1" skipped="0"/>
</testsuites>
"""

SINGLE_SUITE = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="only" tests="5" failures="0" errors="0" skipped="2"/>
"""

NAMESPACED_SUITE = """<?xml version="1.0" encoding="utf-8"?>
<ns:testsuite xmlns:ns="http://example.invalid/junit" tests="1" failures="0" errors="0" skipped="0"/>
"""

NO_SUITE = """<?xml version="1.0" encoding="utf-8"?>
<report/>
"""


def _write(tmp_path: Path, content: str) -> Path:
    target = tmp_path / "junit.xml"
    target.write_text(content, encoding="utf-8")
    return target


def test_junit_stats_sums_across_suites_and_parses_floats(tmp_path: Path) -> None:
    assert junit_stats(_write(tmp_path, PLAIN_SUITES)) == (5, 1, 1, 1)


def test_junit_stats_accepts_single_suite_root(tmp_path: Path) -> None:
    assert junit_stats(_write(tmp_path, SINGLE_SUITE)) == (5, 0, 0, 2)


def test_junit_stats_is_namespace_insensitive(tmp_path: Path) -> None:
    assert junit_stats(_write(tmp_path, NAMESPACED_SUITE)) == (1, 0, 0, 0)


def test_junit_stats_rejects_document_without_testsuite(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        junit_stats(_write(tmp_path, NO_SUITE))


def test_parse_junit_receipt_passes_with_skips_only(tmp_path: Path) -> None:
    target = _write(tmp_path, SINGLE_SUITE)
    receipt = parse_junit_receipt("full", target)
    assert receipt.label == "full"
    assert receipt.passed is True
    assert receipt.tests == 5
    assert receipt.skipped == 2
    assert receipt.sha256 == file_hash(target)
    assert receipt.path == str(target.resolve())


def test_parse_junit_receipt_fails_on_failures_or_errors(tmp_path: Path) -> None:
    receipt = parse_junit_receipt("targeted", _write(tmp_path, PLAIN_SUITES))
    assert receipt.passed is False


def test_receipt_requires_at_least_one_test(tmp_path: Path) -> None:
    empty = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="none" tests="0" failures="0" errors="0" skipped="0"/>
"""
    with pytest.raises(ValidationError):
        parse_junit_receipt("targeted", _write(tmp_path, empty))


def test_receipt_rejects_unknown_label(tmp_path: Path) -> None:
    target = _write(tmp_path, SINGLE_SUITE)
    with pytest.raises(ValidationError):
        JUnitReceipt(
            label="smoke",  # type: ignore[arg-type]
            path=str(target),
            sha256=file_hash(target),
            tests=1,
            failures=0,
            errors=0,
            skipped=0,
            passed=True,
        )
