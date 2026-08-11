"""compilers：Step3/Step4 卡片编译器（自 4 个 phase 编译器脚本提炼，见 manifest 4.1 节）。

提炼来源（脚本本体留旧仓；设计 docs/design/p1_compilers_release_gate_design.md §4）：

- ``step3_card`` ← phase2r9：reviewed typed-Evidence → Step3CardContract 全套离线工件
- ``step3_response`` ← phase2r92：Step3 严格响应契约与 fail-closed 信封检查
- ``step4_contract`` ← phase2r4：统一发布 fixture → Step4 通用图卡契约
- ``step4_image`` ← phase2r11：reviewed Step3 → Step4 图像离线请求与确定性 fake 后端

跨桶输入契约统一自 ``examcard.knowledge.contracts.admission_fixtures``（A 案）；
法定哈希依铁律 bc-b8b9aca6 绑定 ``admission_legacy_hash``。
"""

from examcard.compilers.step3_card import Step3Compilation, compile_reviewed_fixture
from examcard.compilers.step3_response import (
    ResponseModelInput,
    build_response_model_input,
    inspect_response,
    make_inner_inspector,
)
from examcard.compilers.step4_contract import ExpectedCounts, assess_offline_gate, derive_step4_bundle
from examcard.compilers.step4_image import ImageBatch, compile_image_batch

__all__ = [
    "ExpectedCounts",
    "ImageBatch",
    "ResponseModelInput",
    "Step3Compilation",
    "assess_offline_gate",
    "build_response_model_input",
    "compile_image_batch",
    "compile_reviewed_fixture",
    "derive_step4_bundle",
    "inspect_response",
    "make_inner_inspector",
]
