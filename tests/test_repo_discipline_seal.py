"""仓级纪律封印（cm33）：把「字节权威」与「扫描面口径」从人记得手跑变成机器强制。

本仓 `.gitattributes` 是 `* -text`，golden/fixture/canonical 哈希锚定**字节**。可在 cm33
之前，这条纪律唯一的执行手段是 `.test-tmp/eolaudit.py`——一个 gitignore 掉的脚本，靠人
记得在提交前跑一次。工作台一删，纪律就没牙了。本封印把其中「与提交态有关」的那一半搬进
CI：全树跟踪文件零 BOM、每个文件行尾自洽。

同批还钉一条姊妹不变式：**新的顶层代码目录必须进 ruff 的 `src`**。cm33 自己就是活例子——
`tools/` 一诞生就在 lint 与几处扫描面之外，这种漏洞不该靠下一个人恰好想起来。

三条口径写在前面，免得后人误读：

1. **按 HEAD 字节判定，不看工作区。** 「提交进去的字节是否干净」才是不变式；而且全量跑
   测试会把 7 个受版本管理的黄金件按 LF 重写，看工作区会让这组用例随执行顺序变脸。
   工作区那一档仍由 `tools/eolaudit.py` 默认模式（逐文件对比 HEAD）负责。
2. **profile 实现只有一份**，来自 `tools/eolaudit.py`。封印里另写一份就等于给自己发豁免。
3. **既存混杂行尾按清单逐条钉住，且只许缩短。** 清单里那 3 个都是彩排预铺件：头部注释块
   由 `migration/prelay_gonglu5_rehearsal_workspace.py` 用 `\\n` 拼出来，正文是从源件逐字节
   拷来的纯 CRLF。它们不能就地「规范化」——评审侧车自身的 sha256 进了高清裁剪清单的
   `source_sidecar_sha256`，清单的 sha256 又进了处置侧车，动一个字节要重算整条哈希链。
   所以这里的选择是**如实钉住 + 写清代价**，不是假装干净。
"""
from __future__ import annotations

import subprocess
import tomllib
from functools import lru_cache
from pathlib import Path

import pytest

# `tools` 这个顶层包能在这里 import 成功，靠的是 `tests/__init__.py` 存在：`prepend` 导入模式
# 会一路向上找到第一个没有 `__init__.py` 的目录当 basedir，于是插进 sys.path 的是**仓根**而非
# tests/。所以 CI 里的 `uv run pytest`（控制台脚本，cwd 不入 sys.path）同样能用，已实测。
from tools.eolaudit import BINARY_SUFFIXES, Profile, head_blobs, profile, tracked_files

ROOT = Path(__file__).resolve().parents[1]

# 必须入仓的字节纪律工具：cm33 起它们不许再退回 gitignore 掉的工作台。
TOOLS = ("tools/eolaudit.py", "tools/leakscan.py", "tools/msgnorm.py")


# 既存混杂行尾豁免：路径 → (CRLF 行数, 孤立 LF 行数)，按 HEAD 字节实测。
# 只许缩短：修好一个就要从这里删掉，否则 test_mixed_eol_waiver_is_exact 当场失败。
MIXED_EOL_WAIVER: dict[str, tuple[int, int]] = {
    "configs/v2/diagram_disposition_sidecars/"
    "gonglu_golden5_rehearsal_phase0_v1.phase25f_remaining_audited.yaml": (167, 4),
    "configs/v2/diagram_review_sidecars/"
    "gonglu_golden5_rehearsal_phase0_v1.phase25f_structural_reviewed.yaml": (2358, 2),
    "exams/gonglu_golden5_rehearsal/fixtures/golden_annotations/"
    "gonglu_golden5_rehearsal_phase0_v1.yaml": (2845, 2),
}


@lru_cache(maxsize=1)
def _tracked() -> tuple[str, ...]:
    return tuple(tracked_files(ROOT))


@lru_cache(maxsize=1)
def _head_profiles() -> dict[str, Profile]:
    """HEAD 里每个跟踪文本文件的行尾体检结果。"""
    if not (ROOT / ".git").exists():
        pytest.fail(
            "找不到 .git：本封印按 HEAD 字节判定，没有 git 就无法判定。"
            "这里**故意不 skip**——一条会在工具缺席时自动消失的封印，正是 cm32 刚修掉的病"
        )
    names = [
        name for name in _tracked() if Path(name).suffix.lower() not in BINARY_SUFFIXES
    ]
    return {
        name: profile(blob)
        for name, blob in head_blobs(names).items()
        if b"\x00" not in blob
    }


def test_byte_discipline_tools_live_in_the_repo() -> None:
    """工具入仓这件事本身要被看住，否则下次「顺手挪回工作台」没人拦。"""
    tracked = set(_tracked())
    missing = [name for name in TOOLS if name not in tracked]
    assert not missing, f"字节纪律工具不在版本管理里：{missing}"
    assert profile.__module__ == "tools.eolaudit", (
        f"封印必须复用 tools/eolaudit.py 的 profile，实测来自 {profile.__module__}"
    )


def test_gitattributes_pins_whole_tree_byte_authority() -> None:
    """`* -text` 是这一切的地基：它一没，git 就会开始替我们改行尾。"""
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "show", "HEAD:.gitattributes"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    rules = [
        line.strip()
        for line in raw.replace("\r\n", "\n").split("\n")
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert rules, ".gitattributes 里一条规则都没扫到，防空转不通过"
    assert "* -text" in rules, f".gitattributes 丢了全树 `* -text`，现有规则：{rules}"
    revived = [rule for rule in rules if "text=" in rule or rule.endswith(" text")]
    assert not revived, f"有规则把行尾转换重新打开了：{revived}"


def test_scan_surface_is_not_empty() -> None:
    """防空转：扫描面必须真的覆盖两个阵营，否则下面几条会悄悄变成空话。"""
    profiles = _head_profiles()
    assert len(profiles) >= 400, f"只扫到 {len(profiles)} 个跟踪文本文件，扫描面可疑"
    crlf_camp = [name for name, prof in profiles.items() if prof.crlf and not prof.lone_lf]
    lf_camp = [name for name, prof in profiles.items() if prof.lone_lf and not prof.crlf]
    assert len(crlf_camp) >= 100, f"纯 CRLF 阵营只有 {len(crlf_camp)} 个，扫描器可疑"
    assert len(lf_camp) >= 100, f"纯 LF 阵营只有 {len(lf_camp)} 个，扫描器可疑"


def test_no_tracked_file_carries_bom() -> None:
    """零 BOM 是硬线，没有豁免：BOM 会连着改掉哈希与首行解析。"""
    offenders = sorted(name for name, prof in _head_profiles().items() if prof.has_bom)
    assert not offenders, "跟踪文件带 BOM：\n  " + "\n  ".join(offenders)


def test_eol_is_homogeneous_within_each_file() -> None:
    """每个文件自己的行尾必须一致；混杂只允许出现在逐条钉住的清单里。"""
    mixed = sorted(name for name, prof in _head_profiles().items() if prof.is_mixed)
    surprises = [name for name in mixed if name not in MIXED_EOL_WAIVER]
    assert not surprises, (
        "新出现混用 CRLF 与孤立 LF 的跟踪文件（改文件时用了不匹配的写法）：\n  "
        + "\n  ".join(surprises)
    )


def test_mixed_eol_waiver_is_exact_and_only_shrinks() -> None:
    """清单钉死到条数：修好一个就必须删掉一条，混杂面变大也会当场露头。"""
    actual = {
        name: (prof.crlf, prof.lone_lf)
        for name, prof in _head_profiles().items()
        if prof.is_mixed
    }
    assert actual == MIXED_EOL_WAIVER, f"混杂行尾豁免清单与实测不符：实测 {actual}"


def test_waived_files_are_mixed_only_in_their_comment_prologue() -> None:
    """豁免不是一句「这几个文件随便」：孤立 LF 必须只落在头部注释块里，正文得是纯 CRLF。"""
    blobs = head_blobs(list(MIXED_EOL_WAIVER))
    assert len(blobs) == len(MIXED_EOL_WAIVER), f"豁免清单里有路径取不到 HEAD 字节：{blobs.keys()}"
    for name, blob in blobs.items():
        body_start = 0
        for line in blob.split(b"\n"):
            if not line.startswith(b"#"):
                break
            body_start += len(line) + 1
        assert body_start > 0, f"{name}：头部注释块为空，这条豁免的理由已不成立"
        head, body = blob[:body_start], blob[body_start:]
        assert profile(body).lone_lf == 0, (
            f"{name}：正文里出现了 {profile(body).lone_lf} 处孤立 LF——"
            "豁免只覆盖头部注释块，正文混杂是真漂移"
        )
        assert profile(head).lone_lf == MIXED_EOL_WAIVER[name][1], (
            f"{name}：头部注释块的孤立 LF 数与清单不符"
        )


def test_profile_implementation_tells_the_truth() -> None:
    """给共用实现设的防空转：profile 一旦谎报，上面每条断言都会静默变成空话。"""
    assert profile(b"a\r\nb\r\n") == (False, 2, 0)
    assert profile(b"a\nb\n") == (False, 0, 2)
    assert profile(b"a\r\nb\n") == (False, 1, 1)
    assert profile(b"\xef\xbb\xbfa\r\n") == (True, 1, 0)
    assert profile(b"") == (False, 0, 0)
    assert profile(b"a\r\nb\n").is_mixed and not profile(b"a\r\n").is_mixed


def _top_level_python_dirs() -> set[str]:
    """放着受版本管理 `.py` 的顶层目录（按 HEAD 的跟踪清单，不看工作区残留）。"""
    return {name.split("/")[0] for name in _tracked() if name.endswith(".py") and "/" in name}


def test_ruff_src_covers_every_top_level_python_dir() -> None:
    """新建顶层代码目录必须同时进 ruff 的 `src`，否则它一诞生就在 lint 之外。

    cm33 就是活例子：`tools/` 建好那一刻，ruff 的 `src` 还是 `["src","tests","migration"]`，
    新代码既不被 lint 也不被几处扫描面看见。这种漏洞不该靠下一个人恰好想起来，所以在这里
    钉成机器判定——两边**集合相等**，多一个少一个都失败：漏收编会漏 lint，留着已删目录
    则会让 `src` 变成骗人的文档。
    """
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = set(config["tool"]["ruff"]["src"])
    actual = _top_level_python_dirs()
    assert actual, "一个放 .py 的顶层目录都没扫到，防空转不通过"
    assert declared == actual, (
        f"ruff 的 `src` 与实际放 .py 的顶层目录不符：\n"
        f"  漏收编（不被 lint）：{sorted(actual - declared)}\n"
        f"  已不存在（`src` 在说谎）：{sorted(declared - actual)}"
    )


def test_no_tracked_python_sits_at_repo_root() -> None:
    """顶层散落 `.py` 会让上一条口径失去定义域，所以顺手钉住：根目录不放代码。"""
    strays = sorted(name for name in _tracked() if name.endswith(".py") and "/" not in name)
    assert not strays, f"仓根出现受版本管理的 .py，请归入某个顶层目录：{strays}"

