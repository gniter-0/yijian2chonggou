"""Step 2A production-recipe reinforcement instructions (pilot r5 → engine constant).

出处：`migration/run_pilot_gonglu5_step2.py` 的 ``REINFORCEMENT_NOTE``，文本一字不改
搬运（P3 T2；quality 深评 r5 轮实测：追加后考情/碎片/水印三类噪声全部归零、diagram
基准目标全部命中）。用法：作为 ``OpenAICompatibleStep2Transport(system_suffix=…)``
传入——传输层注入，不改冻结的 V25 prompt 模板本体，不进请求 identity；驱动运行
报告须登记 ``reinforcement_id`` 保证可审计。
"""
from __future__ import annotations

import hashlib

# --reinforce 模式：针对 2chat 模型池四大实测症状（考情当知识点/拆行碎片/水印散字/
# diagram 缺失）在 system 尾部追加硬性要求。
STEP2A_REINFORCEMENT_V1 = (
    "\n\n【附加硬性要求（优先级最高）】\n"
    "1. 『历年题量』『考查年份』『（20XX 单/多/案）』等考试元数据不是知识点，严禁作为提案输出。\n"
    "2. 每个提案必须是完整的教学级知识点：跨行的公式、句子或例题必须合并为一个提案，严禁按行拆分。\n"
    "3. 忽略证据中的水印散字（如孤立的『信』『微』『系』『联』『题』单字），严禁写入断言。\n"
    "4. 若输入含受控图示（controlled_diagrams），必须为每个图示产出 diagram_relation 类型提案。\n"
    "5. 宁缺毋滥：只输出教学价值明确的核心知识点，数量与人工讲义知识点密度相当（每页约 1~3 条）。"
)


def reinforcement_id(text: str) -> str:
    """Content-addressed short id (UTF-8 sha256, first 16 hex chars) for run-report audits."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


STEP2A_REINFORCEMENT_V1_ID = reinforcement_id(STEP2A_REINFORCEMENT_V1)
