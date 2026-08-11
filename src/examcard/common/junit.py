"""JUnit 回执：JUnitReceipt + parse_junit_receipt（验收机器专用，4 份历史重复的归一）。

批复面（compilers 稿 §2.2）；形态取 r10x/r11x 家族终态：双道（targeted/full）回执，
passed = failures == 0 and errors == 0（skipped 不影响通过判定），
sha256 钉扎 JUnit XML 文件本体。解析对 testsuites/testsuite 根与 XML 命名空间不敏感。
签名落地即冻（bc-9d82a196）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Final, Literal
from xml.etree import ElementTree

from pydantic import Field, StrictBool, StrictInt, StrictStr

from .hashing import SHA256_HEX, file_hash
from .schema import StrictModel

JUNIT_RECEIPT_VERSION: Final = "examcard_common_junit_receipt_v1"

JUnitLabel = Literal["targeted", "full"]


class JUnitReceipt(StrictModel):
    schema_version: Literal["examcard_common_junit_receipt_v1"] = JUNIT_RECEIPT_VERSION
    label: JUnitLabel
    path: StrictStr = Field(min_length=1)
    sha256: StrictStr = Field(pattern=SHA256_HEX)
    tests: StrictInt = Field(ge=1)
    failures: StrictInt = Field(ge=0)
    errors: StrictInt = Field(ge=0)
    skipped: StrictInt = Field(ge=0)
    passed: StrictBool


def junit_stats(path: Path) -> tuple[int, int, int, int]:
    node = ElementTree.parse(path).getroot()
    suites = (
        (node,)
        if node.tag.rsplit("}", 1)[-1] == "testsuite"
        else tuple(item for item in node.iter() if item.tag.rsplit("}", 1)[-1] == "testsuite")
    )
    if not suites:
        raise ValueError("JUnit document has no testsuite")
    return tuple(  # type: ignore[return-value]
        sum(int(float(item.attrib.get(name, "0"))) for item in suites)
        for name in ("tests", "failures", "errors", "skipped")
    )


def parse_junit_receipt(label: JUnitLabel, path: Path) -> JUnitReceipt:
    tests, failures, errors, skipped = junit_stats(path)
    return JUnitReceipt(
        label=label,
        path=str(path.resolve()),
        sha256=file_hash(path),
        tests=tests,
        failures=failures,
        errors=errors,
        skipped=skipped,
        passed=failures == 0 and errors == 0,
    )
