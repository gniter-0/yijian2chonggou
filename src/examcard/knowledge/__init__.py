"""knowledge：语义知识内核（原 knowledge_v2 的 19 个核心模块 + V25 现役契约）。

落点约定（docs/extraction_manifest.md 第一、二节）：
- schema.py / fact_engine.py / llm.py / projection.py / quality.py
- adapter_v1.py / state_machine.py / identity.py
- contracts/（semantic_contracts 拆分 + v25 现役契约）
- continuation/（core / integration / review / repair_sidecar）
- diagram/（sidecar / disposition）
- planner/（step2b bridge）
"""
