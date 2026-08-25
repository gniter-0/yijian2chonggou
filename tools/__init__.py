"""仓内开发工具（cm33 起）：字节纪律与提交卫生的执行手段。

- `eolaudit.py`：EOL/BOM 平权审计。改完文件跑一次，落盘前要求 `VIOLATIONS=0`。
  同时是封印 `tests/test_repo_byte_discipline_seal.py` 的 profile 实现来源。
- `leakscan.py`：用项目自己的 `_SECRET_PATTERN` 扫落盘产物里的疑似凭据。
- `msgnorm.py`：把提交信息文件规成「无 BOM + 纯 CRLF」。

这三件原先住在 gitignore 掉的 `.test-tmp/` 里，等于「纪律全靠人记得手跑，工作台一删
就没了」。入仓后至少可被引用、可被 ruff 覆盖，且 eolaudit 的口径已由上述封印在 CI 里
机器强制。
"""
