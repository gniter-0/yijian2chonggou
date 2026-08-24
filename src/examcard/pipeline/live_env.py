"""运行配置（.env）读取与文本/图像双通道取值（拆分层 1）。

默认读**新仓根** `.env`（gitignored），也支持显式传路径；``IMAGE_*`` 显式键优先、
回落通用键。密钥值仅驻内存：调用方不得打印、落盘或写进任何报告，本模块也从不把它
拼进错误串——这条纪律由 tests/test_error_sink_redaction_seal.py 按家族逐文件封印。
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .shadow_wiring import load_env_file

from .live_support import _REPO_ROOT


# ---------------------------------------------------------------------------
# 运行配置（.env）读取与双通道取值
# ---------------------------------------------------------------------------


def default_env_path() -> Path:
    """新仓根 `.env` 的法定缺省路径（exam-card-engine/.env）。"""

    return _REPO_ROOT / ".env"


def load_run_env(env_file: Path | str | None = None) -> dict[str, str]:
    """读取运行配置；缺省新仓根 `.env`，可显式传路径。值仅驻内存，不得记录。

    解析口径与 ``shadow_wiring.load_env_file`` 完全一致（同键 last-wins）——新仓
    `.env` 约定通用键只留一对（文本通道），图像通道走显式 ``IMAGE_API_*`` 键。
    """

    path = Path(env_file) if env_file is not None else default_env_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"run env file does not exist: {path} "
            "(the new-repo .env is gitignored — recreate it per new_exam_onboarding_runbook.md, "
            "or pass env_file explicitly)"
        )
    return load_env_file(path)


def _channel_value(env: Mapping[str, str], keys: tuple[str, ...], *, channel: str) -> str:
    for key in keys:
        value = (env.get(key) or "").strip()
        if value:
            return value
    raise ValueError(f"{channel} channel is not configured: none of {list(keys)} is set in the run env")


def text_base_url(env: Mapping[str, str]) -> str:
    return _channel_value(env, ("API_BASE_URL",), channel="text")


def text_api_key(env: Mapping[str, str]) -> str:
    return _channel_value(env, ("API_KEY",), channel="text")


def image_base_url(env: Mapping[str, str]) -> str:
    """图像通道 base_url：``IMAGE_API_BASE_URL`` 显式键优先，缺席回落通用键。"""

    return _channel_value(env, ("IMAGE_API_BASE_URL", "API_BASE_URL"), channel="image")


def image_api_key(env: Mapping[str, str]) -> str:
    """图像通道密钥：``IMAGE_API_KEY`` 显式键优先，缺席回落通用键。"""

    return _channel_value(env, ("IMAGE_API_KEY", "API_KEY"), channel="image")


def image_channel_fallback(env: Mapping[str, str]) -> bool:
    """图像通道是否回落到了文本通道同源（T4a §8.1 要求报告登记此事实）。"""

    return not ((env.get("IMAGE_API_BASE_URL") or "").strip() and (env.get("IMAGE_API_KEY") or "").strip())
