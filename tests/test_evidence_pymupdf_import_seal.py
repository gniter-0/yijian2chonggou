"""封印：PyMuPDF 只走 `import pymupdf`，存根豁免清单只许缩短、不许留过期条目。

cm31 把全仓 39 处 `fitz` 改成 `pymupdf`。这不只是「消掉一条弃用告警」：

- `fitz` 是 PyMuPDF 附带的 shim 包，**不发 py.typed**，于是 mypy 眼里
  `page: fitz.Page` 等于 `page: Any`——整个 evidence 层的 PyMuPDF 调用都在盲区。
  新导入名 `pymupdf` 自带 py.typed，属性是否存在这一层从此受检
  （`pymupdf.Rect(...).nosuchattr` 会报 attr-defined）。要说清的是它**不是**全面收严：
  `Page.rect`、`find_tables()`、`get_pixmap` 的 dpi 都还是 Any。
- `pymupdf` 这个导入名自 **1.24.3** 才存在（1.24.2 的 wheel 顶层只有 `fitz/`，实测），
  所以依赖下限必须跟着从 `>=1.24.0` 提到 `>=1.24.3`，否则就是一句「装得上、一 import
  就 ModuleNotFoundError」的假声明；uv.lock 里记的 specifier 也要同批更新，
  因为 CI 跑的是 `uv sync --locked`。

于是本封印看住五件事：全树零 `fitz` 导入（含函数内局部导入）；PyMuPDF 的用点清单不许
悄悄扩散；`pymupdf` 确实带 py.typed（迁移收益的前提）；pyproject 的存根豁免清单只许缩短，
且清单里的每个条目**当场对账**「该包到底有没有 py.typed / -stubs」——cm31 正是靠这条口径
发现 `cv2.*` 早就过期（opencv-python 自带 py.typed + `cv2/__init__.pyi`，那条豁免从头到尾
没起过作用，却让读者以为全仓 cv2 调用都不受检）；以及 pyproject 与 uv.lock 的下限不许漂移。
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"

# 扫描面与 ruff 的 src 配置一致（src / tests / migration / tools）：四处都可能 import PyMuPDF。
# 这条一致性本身由 tests/test_repo_discipline_seal.py 钉住，新顶层目录漏收编会当场失败。
_SCAN_ROOTS = ("src/examcard", "tests", "migration", "tools")

# PyMuPDF 的用点清单（相对仓根）。写成精确相等而不是「至少这些」：多一个文件用上
# PyMuPDF 就得在提交里说清——渲染/取字是 evidence 层的职责，别的层要用先问清为什么。
_PYMUPDF_IMPORTERS = frozenset(
    {
        "migration/b1_echo_admission_materials.py",
        "migration/b1_echo_diagram_admission_materials.py",
        # agent Step2 通道：export 把整页渲染成 page.png 给扮演模型的 agent 看图核对，
        # 与 run_exam_pipeline.py 同属 migration 进程边缘，不进 knowledge 层。
        "migration/run_agent_step2_v1.py",
        "migration/run_exam_pipeline.py",
        "src/examcard/evidence/fusion.py",
        "src/examcard/evidence/golden.py",
        "src/examcard/evidence/ingest.py",
        "src/examcard/evidence/native.py",
        "src/examcard/evidence/ocr.py",
        "src/examcard/evidence/visual.py",
        "tests/test_evidence_v2_engine.py",
    }
)

# 允许 ignore_missing_imports 的模块（只许缩短）。cm31 从 3 条降到 1 条。
_WAIVED_MISSING_STUBS = frozenset({"rapidocr_onnxruntime.*"})

# `import pymupdf` 可用的最低版本（1.24.2 的 wheel 顶层只有 fitz/，1.24.3 起才有 pymupdf/）。
_PYMUPDF_MIN_VERSION = (1, 24, 3)

_SPEC_PATTERN = re.compile(r"^PyMuPDF\s*(>=\s*[0-9][0-9.]*)$", re.IGNORECASE)


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        files.extend(sorted((ROOT / root).rglob("*.py")))
    return files


def _imported_top_levels(tree: ast.AST) -> dict[str, int]:
    """顶层模块名 → 首次出现的行号。ast.walk 覆盖函数内的局部 import。"""
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.setdefault(alias.name.split(".")[0], node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.setdefault(node.module.split(".")[0], node.lineno)
    return found


def _scan() -> tuple[dict[str, int], set[str], int]:
    fitz_hits: dict[str, int] = {}
    pymupdf_users: set[str] = set()
    for path in _iter_python_files():
        rel = path.relative_to(ROOT).as_posix()
        imported = _imported_top_levels(ast.parse(path.read_bytes()))
        if "fitz" in imported:
            fitz_hits[rel] = imported["fitz"]
        if "pymupdf" in imported:
            pymupdf_users.add(rel)
    return fitz_hits, pymupdf_users, len(_iter_python_files())


def _package_dir(name: str) -> Path | None:
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def _has_stub_package(name: str) -> bool:
    """site-packages 里是否有 `<name>-stubs`（PEP 561 的第三方存根包，如 types-pyyaml）。"""
    return any((Path(entry) / f"{name}-stubs").is_dir() for entry in sys.path if entry)


def _mypy_overrides() -> list[dict]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return list(config["tool"]["mypy"].get("overrides", []))


def _declared_pymupdf_spec() -> str:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    specs = [
        item
        for item in config["project"]["dependencies"]
        if item.lower().startswith("pymupdf")
    ]
    assert len(specs) == 1, "pyproject 的 dependencies 里 PyMuPDF 条目应恰好一条：%s" % specs
    return specs[0]


def test_no_file_imports_the_deprecated_fitz_shim() -> None:
    """全树零 `import fitz`（含函数内局部导入）。

    fitz 不发 py.typed，一处局部 import 就能把该文件的 PyMuPDF 调用重新推回 Any，
    而且它是「导入时打一行弃用告警、将来直接消失」的过渡包。
    """
    fitz_hits, pymupdf_users, scanned = _scan()
    assert not fitz_hits, (
        "又出现了 `import fitz`：%s。PyMuPDF 一律 `import pymupdf`（fitz 无 py.typed，"
        "用它等于把这些调用交回 Any）。" % fitz_hits
    )
    # 防空转：扫描面塌了（比如目录改名）也会「零命中」，那是假绿。
    assert scanned > 200, "扫描面只剩 %d 个 .py 文件，清单或目录结构变了" % scanned
    assert pymupdf_users, "一个 pymupdf 导入都没扫到，说明扫描逻辑本身失效了"


def test_pymupdf_use_sites_match_the_recorded_surface() -> None:
    """PyMuPDF 的用点清单精确相等：多一处、少一处都要在提交里显式改。"""
    _, pymupdf_users, _ = _scan()
    assert pymupdf_users == set(_PYMUPDF_IMPORTERS), (
        "PyMuPDF 用点变了（多出=%s，少了=%s）。渲染与取字属于 evidence 层，"
        "别的层要用 PyMuPDF 先说清为什么；确实该变就同步本封印的 _PYMUPDF_IMPORTERS。"
        % (sorted(pymupdf_users - _PYMUPDF_IMPORTERS), sorted(_PYMUPDF_IMPORTERS - pymupdf_users))
    )


def test_pymupdf_ships_py_typed_and_fitz_does_not() -> None:
    """迁移收益的前提：pymupdf 带 py.typed，fitz 不带。

    哪天上游把 py.typed 撤了，这条会打红——那时要么补 -stubs，要么把 `pymupdf.*`
    加回存根豁免清单，而不是让 evidence 层静默退回 Any。
    """
    package = _package_dir("pymupdf")
    assert package is not None, "pymupdf 没装上（它是运行时硬依赖）"
    assert (package / "py.typed").is_file() or _has_stub_package("pymupdf"), (
        "pymupdf 不再自带 py.typed，也没有 -stubs 包：evidence 层的类型已退回 Any"
    )
    shim = _package_dir("fitz")
    if shim is not None:
        assert not (shim / "py.typed").is_file(), (
            "fitz shim 竟然带上了 py.typed——本封印的理由需要重写，别直接删断言"
        )


def test_missing_stub_waivers_only_shrink() -> None:
    """存根豁免清单只许缩短；PyMuPDF 与 fitz 永不出现在其中。"""
    waived: set[str] = set()
    for override in _mypy_overrides():
        if override.get("ignore_missing_imports") is not True:
            continue
        module = override.get("module")
        waived |= {module} if isinstance(module, str) else set(module or ())
    assert waived == set(_WAIVED_MISSING_STUBS), (
        "存根豁免清单变了（多出=%s，少了=%s）。加宽前先确认该包真的既无 py.typed 也无 -stubs；"
        "缩短是好事，但要显式改本封印的 _WAIVED_MISSING_STUBS。"
        % (sorted(waived - _WAIVED_MISSING_STUBS), sorted(_WAIVED_MISSING_STUBS - waived))
    )
    strays = sorted(item for item in waived if item.split(".")[0] in {"fitz", "pymupdf"})
    assert not strays, (
        "PyMuPDF 不该出现在存根豁免里：%s。pymupdf 自带 py.typed，写上这条只会让读者"
        "以为 evidence 层不受检（旧的 `fitz.*` 还会被 warn_unused_configs 判成 unused section）"
        % strays
    )


def test_waived_modules_are_really_stubless() -> None:
    """逐条对账：清单里的包必须确实拿不到类型，否则就是过期条目。

    cm31 正是靠这条口径抓出 `cv2.*`——opencv-python 自带 py.typed 与 `cv2/__init__.pyi`，
    那条豁免早已无效。过期条目的坏处不在于放宽了检查（它没放宽），而在于它是一句假注释：
    读者会以为这些调用都在盲区，于是不敢依赖 mypy 的结论。
    """
    judged = 0
    stale: list[str] = []
    for pattern in sorted(_WAIVED_MISSING_STUBS):
        name = pattern.split(".")[0]
        package = _package_dir(name)
        if package is None:
            continue  # 该包没装（比如只装了部分可选依赖），本环境无从判断，交给 CI
        judged += 1
        typed = (package / "py.typed").is_file() or _has_stub_package(name)
        if typed:
            stale.append(pattern)
    assert not stale, (
        "这些存根豁免已经过期（该包现在自带 py.typed 或有 -stubs 包）：%s。"
        "删掉条目、跑一遍 mypy 确认仍然干净，别把假注释留在 pyproject 里。" % stale
    )
    assert judged, "一条豁免都没判到（运行环境缺依赖？），本封印这一轮等于空转"


def test_declared_lower_bound_reaches_the_new_import_name() -> None:
    """`PyMuPDF>=1.24.3`：下限必须够到 `pymupdf` 这个导入名出现的那一版。"""
    spec = _declared_pymupdf_spec()
    found = _SPEC_PATTERN.match(spec)
    assert found, (
        "PyMuPDF 的版本约束写法变了（现为 %r）：本封印只认 `PyMuPDF>=X.Y.Z`，"
        "换写法请同步这里的判据" % spec
    )
    lower = tuple(int(part) for part in found.group(1).lstrip(">= ").split("."))
    assert lower >= _PYMUPDF_MIN_VERSION, (
        "PyMuPDF 下限 %s 低于 %s：`import pymupdf` 在更早的版本上不存在，"
        "按下限解析出的环境会装得上却一 import 就 ModuleNotFoundError"
        % (".".join(map(str, lower)), ".".join(map(str, _PYMUPDF_MIN_VERSION)))
    )


def test_lock_file_records_the_same_lower_bound() -> None:
    """uv.lock 记的 specifier 必须与 pyproject 一致。

    CI 跑 `uv sync --locked`：不一致会直接失败，但那要等到推上去才知道。本封印把它
    提前到本地——改依赖下限时忘了 `uv lock` 是很容易发生的事。
    """
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    assert "examcard" in packages, "uv.lock 里找不到本包的条目，锁文件结构变了"
    requires = packages["examcard"]["metadata"]["requires-dist"]
    recorded = [item for item in requires if item["name"] == "pymupdf"]
    assert len(recorded) == 1, "uv.lock 的 requires-dist 里 pymupdf 应恰好一条：%s" % recorded
    declared = _declared_pymupdf_spec()
    expected = declared[len("PyMuPDF"):].strip()
    assert recorded[0].get("specifier") == expected, (
        "uv.lock 里的 pymupdf specifier=%r 与 pyproject 的 %r 不一致：改完下限要跑 `uv lock`，"
        "否则 CI 的 `uv sync --locked` 会直接失败" % (recorded[0].get("specifier"), expected)
    )
