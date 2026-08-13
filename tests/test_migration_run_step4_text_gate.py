"""migration/run_step4_text_gate.py CLI 薄壳离线单测（改进 A1.1 收尾件；零外呼）。

migration/ 不属于包也不参与测试发现（骨架 T1），故按文件路径显式装载被测模块；
live 转写道本身零调用——只钉 R4 行约定 prompt 与 transcriber_id 版本谱系契约。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "migration" / "run_step4_text_gate.py"
_SPEC = importlib.util.spec_from_file_location("run_step4_text_gate_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
gate_cli = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate_cli)


def test_transcribe_prompt_carries_table_row_line_convention() -> None:
    # R4（A2 校准）：表格卡整行转写为一行、单元格以 " | " 连接——从源头消解逐单元格碎行（G2）。
    prompt = gate_cli.TRANSCRIBE_PROMPT
    assert "one visible element per line" in prompt          # 原 verbatim 行约定保留
    assert "' | '" in prompt                                  # 单元格连接符约定
    assert "each table row as one single line" in prompt      # 整行一行约定
    assert "never split one table row" in prompt              # 禁止拆行（否定式双保险）
    assert "no commentary" in prompt                          # 纯转写输出不变


def test_live_transcriber_id_embeds_prompt_version() -> None:
    # R4：prompt 变更必须体现在 transcriber_id（转写实录缓存/复放按版本区分谱系，A2 提醒）。
    version = gate_cli.TRANSCRIBE_PROMPT_VERSION
    assert version and version != "prompt-v1"
    assert gate_cli._live_transcriber_id("gpt-5.6-sol") == f"live:gpt-5.6-sol:{version}"
    assert gate_cli._live_transcriber_id("m").endswith(version)
