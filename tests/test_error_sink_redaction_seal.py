"""封印：错误落纸只许经 providers 的脱敏器（不变式 #12），src / migration / tools 用同一把尺子。

cm15/cm18 只把这条不变式钉在 knowledge/llm.py 一个模块里，代价是同一个漏法在别处照样成立：
migration 下的实跑脚本一律 ``print(f"...{exc}")`` 并 tee 成日志（census_s2.log / vision_s2.log
就是这么来的），pipeline/live_run.py 把 ``f"{type(error).__name__}: {error}"`` 写进 run report
再归档。供应商与中继的 401/403 正文会回显 ``Incorrect API key provided: sk-...`` 与
``Authorization: Bearer ...``，于是一次失败就能把密钥落进磁盘。

本封印把三条机械规则变成可执行门禁，覆盖 src/examcard、migration 与 tools 全量 .py：

1. ``except ... as NAME`` 块里不得用 f-string 直插 NAME（直插会调 ``__str__`` 带出正文）；
   ``raise`` 语句豁免——那是在造新异常，不是往报告/日志里写。
2. 不得出现 ``traceback.print_exc``/``format_exc`` 一类把异常原文整段倒出的调用。
3. 外呼客户端必须显式钉 ``timeout``；``OpenAI``/``AsyncOpenAI`` 另需显式 ``max_retries``
   （cm15 的实测教训：SDK 默认 ``max_retries=2`` 会让一次计费调用发三个付费请求，把预算硬顶
   击穿三倍）。

覆盖面是诚实有限的：只认 f-string 直插，``"%s" % exc``、``str.format`` 或先赋值再拼这类写法
逃得掉；它拦的是仓内实际在用的那一种写法，以及照抄它的下一次。全离线（规范 T4）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import examcard
import examcard.providers as providers_pkg
from examcard.providers.base import sanitize_error_text, sanitized_error_summary

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = Path(examcard.__file__).resolve().parent
MIGRATION_ROOT = REPO_ROOT / "migration"
# cm33：`tools/` 也进扫描面。那里住着 leakscan/eolaudit——专门跟凭据和字节打交道的脚本，
# 恰恰最不该自己往标准输出倒异常原文。扫描面与 ruff `src` 的一致性由
# tests/test_repo_discipline_seal.py 钉住。
TOOLS_ROOT = REPO_ROOT / "tools"

_OPENAI_CTORS = frozenset({"OpenAI", "AsyncOpenAI"})
_HTTPX_ONESHOT = frozenset({"Client", "AsyncClient", "get", "post", "stream"})
_RAW_DUMP_CALLS = frozenset({"print_exc", "format_exc", "print_exception", "format_exception"})

_LEAKED_KEY = "sk-live-ABCDEF1234567890"


def _scanned_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for root in (SRC_ROOT, MIGRATION_ROOT, TOOLS_ROOT):
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return tuple(sorted(files))


def _parse(path: Path) -> tuple[str, ast.Module]:
    source = path.read_text(encoding="utf-8")
    return source, ast.parse(source)


def _raise_owned_fstrings(handler: ast.ExceptHandler) -> set[int]:
    owned: set[int] = set()
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            owned.update(id(child) for child in ast.walk(node) if isinstance(child, ast.JoinedStr))
    return owned


def _client_calls(tree: ast.Module) -> list[tuple[int, str, frozenset[str]]]:
    found: list[tuple[int, str, frozenset[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        label = ""
        if isinstance(func, ast.Name) and func.id in _OPENAI_CTORS:
            label = func.id
        elif isinstance(func, ast.Attribute) and func.attr in _OPENAI_CTORS:
            label = func.attr
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in _HTTPX_ONESHOT
            and isinstance(func.value, ast.Name)
            and func.value.id == "httpx"
        ):
            label = f"httpx.{func.attr}"
        if label:
            found.append((node.lineno, label, frozenset(kw.arg or "**" for kw in node.keywords)))
    return found


def test_no_except_block_interpolates_the_exception_object() -> None:
    """规则 1：落纸点一律走脱敏调用，直插异常对象的写法整仓禁绝。"""
    offenders: list[str] = []
    handlers_seen = 0
    for path in _scanned_files():
        source, tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not node.name:
                continue
            handlers_seen += 1
            exempt = _raise_owned_fstrings(node)
            for inner in ast.walk(node):
                if not isinstance(inner, ast.JoinedStr) or id(inner) in exempt:
                    continue
                for part in inner.values:
                    if (
                        isinstance(part, ast.FormattedValue)
                        and isinstance(part.value, ast.Name)
                        and part.value.id == node.name
                    ):
                        text = ast.get_source_segment(source, inner) or ""
                        offenders.append(f"{path.name}:{inner.lineno}: {text}")

    assert handlers_seen > 50, f"具名 except 只扫到 {handlers_seen} 个，锚点已失效，需重新定位"
    assert offenders == [], f"异常正文未过脱敏器就落纸: {offenders}"


def test_no_raw_traceback_dump_survives() -> None:
    """规则 2：``traceback.print_exc`` 把正文原样打到 stderr，实跑一 tee 就落盘。"""
    offenders = [
        f"{path.name}:{node.lineno}: {node.func.attr}"
        for path in _scanned_files()
        for node in ast.walk(_parse(path)[1])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _RAW_DUMP_CALLS
    ]

    assert offenders == [], f"异常原文整段倒出: {offenders}"


def test_every_outbound_client_pins_timeout_and_retries() -> None:
    """规则 3：超时与重试次数都必须写在调用点上，不许吃 SDK 默认值。"""
    offenders: list[str] = []
    clients_seen = 0
    for path in _scanned_files():
        _, tree = _parse(path)
        for lineno, label, kwargs in _client_calls(tree):
            clients_seen += 1
            missing = [name for name in ("timeout",) if name not in kwargs]
            if label in _OPENAI_CTORS and "max_retries" not in kwargs:
                missing.append("max_retries")
            if missing:
                offenders.append(f"{path.name}:{lineno} {label} 缺 {'+'.join(missing)}")

    assert clients_seen >= 4, f"外呼客户端只扫到 {clients_seen} 个，锚点已失效，需重新定位"
    assert offenders == [], f"外呼客户端未钉死超时/重试: {offenders}"


def test_sanitized_error_summary_is_the_single_shape() -> None:
    leaky = RuntimeError(
        f'Error code: 401 - {{"message": "Incorrect API key provided: {_LEAKED_KEY}"}} '
        f"headers: Authorization: Bearer {_LEAKED_KEY}"
    )

    masked = sanitized_error_summary(leaky)

    assert masked.startswith("RuntimeError: ")
    assert _LEAKED_KEY not in masked
    assert "[REDACTED]" in masked
    # 长度口径只有脱敏器一处（300 字），落纸点不许另立。
    assert len(masked) == len(sanitize_error_text(f"RuntimeError: {leaky}")) <= 300


def test_sanitizer_is_reachable_from_the_package_surface() -> None:
    """migration 侧按包名导入（``from examcard.providers import ...``），导出面得跟上。"""
    assert "sanitized_error_summary" in providers_pkg.__all__
    assert providers_pkg.sanitized_error_summary is sanitized_error_summary