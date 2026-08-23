"""封印：会真联网的 transport 层，错误落纸必须过 providers 的脱敏器（不变式 #12）。

背景不对称：``providers/base.py`` 为 ``sanitized_detail`` 配了强制校验器（原文未脱敏就抛
``probe detail is not sanitized``），可那条通道全是离线录制 fixture；真正会外呼的
``knowledge/llm.py`` 此前一律照抄 ``str(exc)[:500]``，等于同一条不变式只在不联网的地方成立。

全离线（规范 T4 默认禁网）：不构造任何真实客户端，只喂假异常与假身份。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import examcard.knowledge.llm as llm_module
from examcard.knowledge.contracts import (
    STEP2A_REQUEST_VERSION,
    Step2Stage,
    make_step2_request_identity,
)
from examcard.knowledge.llm import (
    Step2JournalOutcome,
    Step2OpenAIAdapter,
    _sanitized_error_message,
)

# 取自真实形态：401 正文回显密钥，且 SDK 的 APIStatusError.__str__ 会把正文一起带出来。
_LEAKED_KEY = "sk-live-ABCDEF1234567890"
_LEAKY_BODY = (
    f'Error code: 401 - {{"error": {{"message": "Incorrect API key provided: {_LEAKED_KEY}", '
    f'"type": "invalid_request_error"}}}} request headers: Authorization: Bearer {_LEAKED_KEY}'
)


def _identity():
    return make_step2_request_identity(
        stage=Step2Stage.FACT_PLANNING,
        request_version=STEP2A_REQUEST_VERSION,
        model="deterministic-fake-v1",
        payload_sha256="0" * 64,
        response_schema={"type": "object"},
    )


def test_sanitized_error_message_masks_credentials_but_keeps_the_type_name():
    masked = _sanitized_error_message(RuntimeError(_LEAKY_BODY))

    assert masked.startswith("RuntimeError: ")
    assert _LEAKED_KEY not in masked
    assert "[REDACTED]" in masked
    # 截断口径统一由脱敏器持有（300 字），本模块不另立长度。
    assert len(masked) <= 300


def test_journal_never_writes_a_credential_echo_to_disk(tmp_path):
    """后果落在这里：journal 是逐行追加的磁盘文件，回显一旦入行就会被审阅与归档带走。"""
    adapter = Step2OpenAIAdapter(
        storage_root=tmp_path / "v2",
        transport=object(),
        idempotency_supported=False,
    )

    adapter._append(
        outcome=Step2JournalOutcome.FAILED,
        request=_identity(),
        http_status=401,
        error=RuntimeError(_LEAKY_BODY),
    )

    journal = tmp_path / "v2" / "telemetry" / "journal.jsonl"
    raw = journal.read_text(encoding="utf-8")
    event = json.loads(raw.splitlines()[0])

    assert event["error_type"] == "RuntimeError"
    assert _LEAKED_KEY not in raw
    assert "[REDACTED]" in event["error_message"]


def test_llm_module_no_longer_slices_a_stringified_value():
    """机械封印其一：``str(...)[:N]`` 这种绕开脱敏器的写法不许再出现在 llm.py 里。

    只测行为会漏掉「新加一处落纸点又照抄 str(exc)」，所以这里直接按 AST 禁掉该写法本身。
    它同时覆盖了两处不以 ``error``/``error_message`` 为名的落纸点（失败态构造与 _failed_b）。
    """
    tree = ast.parse(Path(llm_module.__file__).read_text(encoding="utf-8"))

    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Slice)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "str"
    ]

    assert offenders == []


def test_named_error_sinks_all_route_through_the_sanitizer():
    """机械封印其二：``error_message=`` 与 ``{"error": ...}`` 的取值只能是 None 或脱敏调用。"""
    source = Path(llm_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    sinks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "error_message":
            sinks.append(ast.get_source_segment(source, node.value) or "")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "error":
                    sinks.append(ast.get_source_segment(source, value) or "")

    assert sinks, "一个落纸点都没找到，说明本封印的锚点已经失效，需要重新定位"
    assert [text for text in sinks if text != "None" and "sanitiz" not in text] == []