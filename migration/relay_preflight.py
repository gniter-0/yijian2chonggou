"""中继能力预检工具（T1 交付物：四合一自检 + 需求画像结论）。

背景：换中继/换模型的四类独立故障（体积 502、strict 不强制、CF 首字节窗口、
图像信封变体）曾各花数小时排障。本工具把 `probe_relay_body_limit.py` 的体积
阶梯吸收进来，扩成一键四组探针，2 分钟内对任意 OpenAI 兼容中继测清
"能不能用、哪不能用"，并对照本仓需求画像直接给结论。

四组探针（各自独立开关，默认全跑；预算熔断内置）：
  P1 体积阶梯   12/40/70/120/180/260KB 填充 payload + strict 小 schema echo
                → 请求体上限区间 + 各级时延（连续 2 级失败提前止损）
  P2 strict遵从 ① 逃逸探针：schema 要求 minItems=3 且元素为指定 const，
                  用户指令诱导返回空数组——强制解码不可能为空，空=仅提示；
                ② 单卡级回显探针：~1.5K token 输出的逐字段回显（T3 v2 单卡
                  契约的直接可行性信号）
  P3 首字节窗口 （--deep 才跑）长生成小语义请求，非流式 vs 流式各一次，
                判定网关掐断条件（首字节/总时长）与窗口估值；含 1 次吞吐标定
  P4 图像端点   GET /v1/models 查 IMAGE_MODEL；--with-image 才真出 1 张最小图，
                经 providers 严格投影 + 信封门禁验收（b64/url 变体判别）

通道解析（.env 双通道约定，20260812）：
- 旧约定（yijian2/.env）：API_BASE_URL/API_KEY 成对出现多次，按出现顺序配对，
  首对=chatgpt2api-new(127.0.0.1:3100)、末对=2chat → `--channel first|last`；
- 新约定（exam-card-engine/.env，T2 落地）：通用键只留一对（文本通道=2chat），
  图像通道走显式 IMAGE_API_BASE_URL/IMAGE_API_KEY → `--channel image`；
- 任意形态下也可 `--base-url` 显式指定（须能在 .env 中配到成对密钥）。
若新仓根存在 .env 则优先读它（T2 迁移后原仓零接触），否则回落 yijian2/.env。
密钥仅驻内存：不打印、不落盘、不入报告。

运行（exam-card-engine 仓根）：
  uv run python migration/relay_preflight.py --dry-run --channel last      # 零外呼自检（打印探针装配）
  uv run python migration/relay_preflight.py --allow-live --channel image --with-image   # 图像通道(3100)
  uv run python migration/relay_preflight.py --allow-live --channel last --deep          # 文本通道(2chat)

输出：控制台能力矩阵 + data/relay_preflight/<label>/relay_preflight_report.json。
费用：默认口径 ≤12 次小调用 ≈$0.1；--deep --with-image 全开 ≈$0.3-0.5。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

NEW_ROOT = Path(__file__).resolve().parents[1]
if str(NEW_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.utils.legacy_root import resolve_legacy_root  # noqa: E402

OLD_ROOT = resolve_legacy_root(NEW_ROOT)

from examcard.knowledge.llm import OpenAICompatibleStep2Transport  # noqa: E402
from examcard.providers import (  # noqa: E402
    build_image_profile,
    inspect_image_envelope,
    project_image_request,
    sanitize_error_text,
)

TOOL_VERSION = "relay_preflight/1.0"
DEFAULT_KB_LADDER = (12, 40, 70, 120, 180, 260)
DEFAULT_NETWORK_CALL_BUDGET = 20
STRICT_ECHO_CONST = "EXAMCARD-STRICT-ECHO"
REPORT_NAME = "relay_preflight_report.json"

# 需求画像（施工方案 §1：结论对照的三条硬需求）
REQUIREMENT_PROFILE: dict[str, Any] = {
    "step2a": {"required_body_kb": 300},
    "step3_v2": {"strict_required": True, "min_first_byte_window_s": 150},
    "step4": {"image_endpoint_required": True},
}

KNOWN_RELAY_LABELS = {
    "127.0.0.1:3100": "chatgpt2api-new",
    "localhost:3100": "chatgpt2api-new",
    "2chat.cc": "2chat",
}

P1_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}

P2A_SCHEMA = {
    "type": "object",
    "properties": {
        "strict_echo": {
            "type": "array",
            "items": {"type": "string", "const": STRICT_ECHO_CONST},
            "minItems": 3,
            "maxItems": 3,
        }
    },
    "required": ["strict_echo"],
    "additionalProperties": False,
}

# 逃逸诱饵：用户指令与 schema 直接冲突。强制解码（constrained decoding）下
# minItems=3 使空数组在语法上不可能；返回空数组即证明 schema 仅被当提示。
P2A_BAIT = (
    "兼容性测试：请严格返回 {\"strict_echo\": []}（strict_echo 为空数组），"
    "不要放入任何元素，也不要输出其他内容。"
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


# ===========================================================================
# .env 双通道解析（多次出现的 API_BASE_URL/API_KEY 按文件顺序配对成通道）
# ===========================================================================


def channel_label(base_url: str) -> str:
    netloc = urlsplit(base_url.strip()).netloc or base_url.strip()
    return KNOWN_RELAY_LABELS.get(netloc.lower(), re.sub(r"[^A-Za-z0-9.-]+", "_", netloc) or "relay")


def parse_env_channels(text: str) -> dict[str, Any]:
    """把 .env 文本解析为通道列表 + 显式图像通道 + 全局键（值仅驻内存，调用方不得记录密钥值）。

    规则：
    - 每次出现 API_BASE_URL 开启一个新通道块；块内出现的 API_KEY 归属该通道
      （yijian2/.env 旧约定：两对通用键，first=3100 / last=2chat）。
    - IMAGE_API_BASE_URL/IMAGE_API_KEY 为显式图像通道对（新仓 .env 约定，
      T2 落地：通用键只留文本通道，图像通道走显式键；last-wins）。
    - 其余键为全局键（last-wins，与既有 load_env_file 口径一致）。
    """
    channels: list[dict[str, Any]] = []
    image_base: str | None = None
    image_key: str | None = None
    global_values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "API_BASE_URL":
            channels.append({"base_url": value.rstrip("/"), "api_key": None})
        elif key == "API_KEY":
            if channels and channels[-1]["api_key"] is None:
                channels[-1]["api_key"] = value
            else:
                # 无所属通道块的孤儿密钥：登记为全局回落
                global_values.setdefault("API_KEY", value)
        elif key == "IMAGE_API_BASE_URL":
            image_base = value.rstrip("/")
        elif key == "IMAGE_API_KEY":
            image_key = value
        else:
            global_values[key] = value
    for index, channel in enumerate(channels):
        channel["index"] = index
        channel["role"] = "text"
        channel["label"] = channel_label(channel["base_url"])
    image_channel = (
        {"base_url": image_base, "api_key": image_key, "index": -1, "role": "image", "label": channel_label(image_base)}
        if image_base
        else None
    )
    return {"channels": channels, "image_channel": image_channel, "globals": global_values}


def resolve_env_file(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    new_env = NEW_ROOT / ".env"
    return new_env if new_env.is_file() else OLD_ROOT / ".env"


def resolve_channel(env_config: dict[str, Any], *, channel: str | None, base_url: str | None) -> dict[str, Any]:
    channels = env_config["channels"]
    image_channel = env_config.get("image_channel")
    if base_url:
        wanted = base_url.rstrip("/")
        for candidate in [*channels, *( [image_channel] if image_channel else [] )]:
            if candidate["base_url"] == wanted:
                return candidate
        known = [c["base_url"] for c in channels] + ([image_channel["base_url"]] if image_channel else [])
        raise SystemExit(f"--base-url {wanted} 在 .env 中没有成对密钥（现有通道：{known}）")
    if channel == "image":
        if image_channel is None:
            raise SystemExit(".env 无显式图像通道键（IMAGE_API_BASE_URL/IMAGE_API_KEY）")
        return image_channel
    if not channels:
        hint = "；该 .env 只有显式图像通道，用 --channel image" if image_channel else ""
        raise SystemExit(f"未在 .env 中解析到任何 API_BASE_URL 通道{hint}")
    if channel == "first":
        return channels[0]
    if channel == "last":
        return channels[-1]
    raise SystemExit("必须指定 --channel first|last|image 或 --base-url")


# ===========================================================================
# 探针装配（纯函数，离线可测）
# ===========================================================================


def build_p1_levels(kb_ladder: tuple[int, ...]) -> list[dict[str, Any]]:
    levels = []
    for kb in kb_ladder:
        filler = "填" * (kb * 1024 // 3)
        content = "以下为体积测试垫料，请完全忽略其内容，只返回 {\"ok\": true} 这一个 JSON 对象。\n" + filler
        levels.append({"kb": kb, "content": content, "content_utf8_kb": round(len(content.encode("utf-8")) / 1024, 1)})
    return levels


def build_p2b_card(field_count: int = 8) -> dict[str, Any]:
    """构造 ~1.5K token 输出规模的合成单卡（内容确定性，可逐字段判等）。"""
    fields = []
    for index in range(field_count):
        text = (
            f"第{index + 1}号规范条款：路基填筑必须分层摊铺分层压实，单层压实厚度不得超过三十厘米，"
            f"路床顶面以下八十厘米范围内压实度不得低于百分之九十六，填料含水率控制在最优含水率的正负两个百分点以内；"
            f"桥涵台背与挡土墙背回填须采用透水性材料并与路基同步分层夯实，涵顶填土厚度不足五十厘米时禁止振动碾压"
            f"（单卡回显探针填充段第{index + 1}条，本文本仅用于中继能力测试，必须逐字保真返回）。"
        )
        fields.append({"field_id": f"f{index:02d}", "text": text})
    return {
        "schema_version": "relay-preflight/echo-card-1",
        "card_id": "preflight-card-0001",
        "title": "中继单卡回显探针",
        "presentation": "constraint",
        "fields": fields,
    }


def build_p2b_schema() -> dict[str, Any]:
    field_schema = {
        "type": "object",
        "properties": {"field_id": {"type": "string"}, "text": {"type": "string"}},
        "required": ["field_id", "text"],
        "additionalProperties": False,
    }
    card_schema = {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string", "const": "relay-preflight/echo-card-1"},
            "card_id": {"type": "string"},
            "title": {"type": "string"},
            "presentation": {"type": "string"},
            "fields": {"type": "array", "items": field_schema},
        },
        "required": ["schema_version", "card_id", "title", "presentation", "fields"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"draft_cards": {"type": "array", "items": card_schema, "minItems": 1, "maxItems": 1}},
        "required": ["draft_cards"],
        "additionalProperties": False,
    }


def build_p2b_user_content(card: dict[str, Any]) -> str:
    return (
        "请把下面这张卡一字不改、逐字段回显到 draft_cards（恰好一张，不增不删不改任何字符）：\n"
        + json.dumps(card, ensure_ascii=False)
    )


def build_p3_count_payload(*, model: str, count_to: int, max_tokens: int, stream: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是计数器。只输出计数结果本身，不要任何解释。"},
            {
                "role": "user",
                "content": f"请从 1 逐个数到 {count_to}，每行输出一个阿拉伯数字，数完后最后一行输出 DONE。",
            },
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if stream:
        payload["stream"] = True
    return payload


def size_p3_main_workload(
    *,
    calibration_tokens: int,
    calibration_elapsed_s: float,
    calibration_count_to: int,
    target_seconds: float,
    max_tokens_cap: int,
) -> dict[str, Any]:
    """按标定吞吐把主探针工作量放大到目标时长（钳位防失控）。"""
    throughput = max(calibration_tokens / max(calibration_elapsed_s, 0.1), 1.0)
    tokens_per_line = max(calibration_tokens / max(calibration_count_to, 1), 0.5)
    desired_tokens = throughput * target_seconds * 1.15
    count_to = int(min(max(desired_tokens / tokens_per_line, 200), 24000))
    max_tokens = int(min(desired_tokens * 1.25 + 256, max_tokens_cap))
    return {
        "throughput_tokens_per_s": round(throughput, 2),
        "tokens_per_line": round(tokens_per_line, 2),
        "count_to": count_to,
        "max_tokens": max_tokens,
    }


IMAGE_PROBE_PROMPT = "A plain white background with one solid black square in the center."


def build_p4_image_payload(*, image_model: str, size: str) -> dict[str, Any]:
    return {
        "model": image_model,
        "n": 1,
        "prompt": IMAGE_PROBE_PROMPT,
        "response_format": "b64_json",
        "size": size,
    }


# ===========================================================================
# 判定逻辑（纯函数，离线可测）
# ===========================================================================


def classify_body_kind(content_type: str, body: bytes) -> str:
    if not body:
        return "empty"
    try:
        json.loads(body.decode("utf-8"))
        return "json"
    except (UnicodeDecodeError, ValueError):
        pass
    if "html" in (content_type or "").lower() or body.lstrip()[:1] == b"<":
        return "html"
    return "text"


def derive_body_limit(level_results: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [item["kb"] for item in level_results if item.get("result") == "success"]
    max_success = max(successes) if successes else None
    failures_above = [
        item["kb"]
        for item in level_results
        if item.get("result") != "success" and (max_success is None or item["kb"] > max_success)
    ]
    min_failure = min(failures_above) if failures_above else None
    tested_max = max((item["kb"] for item in level_results), default=0)
    if max_success is None and min_failure is None:
        summary = "未执行"
    elif max_success is None:
        summary = f"最小级 {min_failure}KB 即失败"
    elif min_failure is None:
        summary = f"≥{max_success}KB 未触顶"
    else:
        summary = f"上限区间 [{max_success},{min_failure})KB"
    return {
        "max_success_kb": max_success,
        "min_failure_kb": min_failure,
        "tested_max_kb": tested_max,
        "summary": summary,
    }


def classify_strict_escape(content: str) -> dict[str, Any]:
    """空数组/缺项=逃逸判"不强制"；恰好 3 个 const 元素=强制解码生效。"""
    try:
        parsed = json.loads(content)
    except ValueError:
        return {"status": "malformed_output", "detail": "响应不是合法 JSON（schema 未被强制）"}
    echo = parsed.get("strict_echo") if isinstance(parsed, dict) else None
    if not isinstance(echo, list) or len(echo) == 0:
        return {"status": "not_enforced", "detail": "空数组/缺项逃逸：schema 仅被当提示"}
    if len(echo) == 3 and all(item == STRICT_ECHO_CONST for item in echo):
        return {"status": "enforced", "detail": "诱导空数组失败，minItems=3+const 被强制解码"}
    return {
        "status": "not_enforced",
        "detail": f"返回形态违反 schema（len={len(echo)}，const 匹配 {sum(1 for item in echo if item == STRICT_ECHO_CONST)}/{len(echo)}）",
    }


def classify_p2b_echo(content: str, card: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except ValueError:
        return {"status": "malformed_output", "detail": "响应不是合法 JSON"}
    cards = parsed.get("draft_cards") if isinstance(parsed, dict) else None
    if not isinstance(cards, list) or len(cards) == 0:
        return {"status": "escape_empty", "detail": "draft_cards 空/缺项逃逸（与 step3 v1 证伪形态一致）"}
    if len(cards) != 1:
        return {"status": "echo_mismatch", "detail": f"draft_cards 数量 {len(cards)} != 1"}
    if cards[0] == card:
        return {"status": "echo_exact", "detail": "单卡逐字段回显一致"}
    drift_keys = sorted(
        key
        for key in set(card) | set(cards[0] if isinstance(cards[0], dict) else {})
        if not isinstance(cards[0], dict) or cards[0].get(key) != card.get(key)
    )
    return {"status": "echo_mismatch", "detail": f"字段漂移：{drift_keys}"}


def judge_p3(nonstream: dict[str, Any] | None, stream: dict[str, Any] | None) -> dict[str, Any]:
    """判定网关掐断条件（首字节 or 总时长）与窗口估值。"""
    result: dict[str, Any] = {
        "status": "skipped",
        "cutoff_kind": None,
        "window_s": None,
        "window_is_lower_bound": None,
        "streaming_viable": None,
        "detail": "未执行（需 --deep）",
    }
    if nonstream is None:
        return result
    cut_outcomes = {"gateway_cut", "connection_dropped"}
    stream_survived = stream is not None and stream.get("outcome") in {"success", "aborted_early"}
    stream_cut = stream is not None and stream.get("outcome") in {"stream_cut", "http_error"}
    if nonstream.get("outcome") == "success":
        result.update(
            {
                "status": "not_triggered",
                "cutoff_kind": "none_observed",
                "window_s": nonstream.get("elapsed_s"),
                "window_is_lower_bound": True,
                "streaming_viable": stream_survived if stream is not None else None,
                "detail": f"非流式 {nonstream.get('elapsed_s')}s 全程未被掐断（窗口下界）",
            }
        )
        return result
    if nonstream.get("outcome") in cut_outcomes:
        window = nonstream.get("elapsed_s")
        result.update({"status": "measured", "window_s": window, "window_is_lower_bound": False})
        if stream_survived and (stream.get("elapsed_s") or 0) > (window or 0) + 10:
            result.update(
                {
                    "cutoff_kind": "first_byte",
                    "streaming_viable": True,
                    "detail": (
                        f"非流式 ~{window}s 被掐（HTTP {nonstream.get('http_status')}/{nonstream.get('body_kind')}），"
                        f"流式存活至 {stream.get('elapsed_s')}s → 掐断条件=首字节/空闲，流式可续"
                    ),
                }
            )
        elif stream_cut:
            stream_elapsed = stream.get("elapsed_s") or 0
            if abs(stream_elapsed - (window or 0)) <= 20:
                result.update(
                    {
                        "cutoff_kind": "total_duration",
                        "streaming_viable": False,
                        "detail": f"非流式与流式均在 ~{window}s 被掐 → 掐断条件=总时长，流式不可续",
                    }
                )
            else:
                result.update(
                    {
                        "cutoff_kind": "unknown",
                        "streaming_viable": False,
                        "detail": f"非流式 ~{window}s 被掐；流式在 {stream_elapsed}s 异常，形态不一致待复测",
                    }
                )
        else:
            result.update(
                {
                    "cutoff_kind": "unknown",
                    "streaming_viable": None,
                    "detail": f"非流式 ~{window}s 被掐；流式未跑或无法判定",
                }
            )
        return result
    result.update(
        {
            "status": "inconclusive",
            "detail": f"非流式探针未得出网关结论（outcome={nonstream.get('outcome')}, error={nonstream.get('error_type')}）",
        }
    )
    return result


def classify_image_envelope_variant(body: bytes) -> dict[str, Any]:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"variant": "non_json", "top_level_keys": [], "data_item_keys": []}
    if not isinstance(document, dict):
        return {"variant": "non_object", "top_level_keys": [], "data_item_keys": []}
    top_keys = sorted(str(key) for key in document)
    data = document.get("data")
    item_keys: list[str] = []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        item_keys = sorted(str(key) for key in data[0])
    has_b64 = "b64_json" in item_keys
    has_url = "url" in item_keys
    if has_b64 and has_url:
        variant = "mixed"
    elif has_b64:
        variant = "b64_json"
    elif has_url:
        variant = "url"
    elif "error" in top_keys:
        variant = "error_envelope"
    else:
        variant = "unknown"
    return {"variant": variant, "top_level_keys": top_keys, "data_item_keys": item_keys}


# ===========================================================================
# 需求画像结论
# ===========================================================================


def judge_step2a(body_limit: dict[str, Any]) -> dict[str, Any]:
    required = REQUIREMENT_PROFILE["step2a"]["required_body_kb"]
    requirement = f"请求体 ≥{required}KB"
    max_ok = body_limit.get("max_success_kb")
    min_fail = body_limit.get("min_failure_kb")
    if max_ok is None and min_fail is None:
        return {"requirement": requirement, "verdict": "未知", "basis": "P1 未执行"}
    if max_ok is None:
        return {"requirement": requirement, "verdict": "不满足", "basis": f"最小级 {min_fail}KB 即失败"}
    if max_ok >= required:
        return {"requirement": requirement, "verdict": "满足", "basis": f"{max_ok}KB 实测通过"}
    if min_fail is not None:
        return {
            "requirement": requirement,
            "verdict": "不满足",
            "basis": f"体积上限区间 [{max_ok},{min_fail})KB，低于 {required}KB",
        }
    return {
        "requirement": requirement,
        "verdict": "推定满足",
        "basis": f"阶梯至 {max_ok}KB 未触顶（{required}KB 未直测，可 --kb-ladder 加测；step2a 现实最大体 ~250KB）",
    }


def judge_step3_v2(
    strict: dict[str, Any],
    single_card: dict[str, Any],
    first_byte: dict[str, Any],
) -> dict[str, Any]:
    min_window = REQUIREMENT_PROFILE["step3_v2"]["min_first_byte_window_s"]
    requirement = f"strict 强制 + (首字节窗口 ≥{min_window}s 或 流式可续)"
    strict_status = strict.get("status")
    if strict_status in {"not_enforced", "malformed_output"}:
        return {"requirement": requirement, "verdict": "不满足", "basis": f"strict 不强制（{strict.get('detail')}）"}
    if strict_status == "schema_rejected":
        return {
            "requirement": requirement,
            "verdict": "不满足",
            "basis": "strict schema 被端点拒绝（minItems/const 关键字不可用，v2 契约 schema 需适配）",
        }
    if strict_status != "enforced":
        return {"requirement": requirement, "verdict": "未知", "basis": f"strict 探针未得出结论（{strict_status}）"}
    window = first_byte.get("window_s")
    window_ok = window is not None and window >= min_window
    stream_ok = first_byte.get("streaming_viable") is True
    if window_ok or stream_ok:
        basis = "strict 强制"
        if window_ok:
            bound = "≥" if first_byte.get("window_is_lower_bound") else "≈"
            basis += f"；首字节窗口 {bound}{window}s"
        if stream_ok:
            basis += "；流式可续"
        return {"requirement": requirement, "verdict": "满足", "basis": basis}
    if single_card.get("status") == "echo_exact":
        return {
            "requirement": requirement,
            "verdict": "推定满足",
            "basis": (
                f"strict 强制且单卡级回显非流式直接通过（{single_card.get('elapsed_s')}s），"
                "窗口未量化（--deep 补测可确证）"
            ),
        }
    if first_byte.get("status") == "skipped":
        return {"requirement": requirement, "verdict": "未知", "basis": "strict 强制，但窗口未测（--deep）且单卡回显未通过"}
    if first_byte.get("status") == "measured" and not window_ok and first_byte.get("streaming_viable") is False:
        return {
            "requirement": requirement,
            "verdict": "不满足",
            "basis": f"窗口 ≈{window}s < {min_window}s 且流式不可续（掐断={first_byte.get('cutoff_kind')}）",
        }
    return {"requirement": requirement, "verdict": "未知", "basis": f"窗口/流式证据不足（{first_byte.get('detail')}）"}


def judge_step4(image: dict[str, Any]) -> dict[str, Any]:
    requirement = "图像端点可用（b64_json 信封）"
    generation = image.get("generation_status")
    advertised = image.get("model_advertised")
    if generation == "gate_valid":
        return {
            "requirement": requirement,
            "verdict": "满足",
            "basis": f"实际出图信封门禁全绿（{image.get('variant')}，{image.get('image_width')}x{image.get('image_height')}）",
        }
    if generation == "gate_failed":
        return {
            "requirement": requirement,
            "verdict": "不满足",
            "basis": f"出图信封不合格：variant={image.get('variant')}，failure_codes={image.get('gate_failure_codes')}",
        }
    if generation == "error":
        return {"requirement": requirement, "verdict": "不满足", "basis": f"图像调用失败：{image.get('error_type')}"}
    if advertised is True:
        return {
            "requirement": requirement,
            "verdict": "推定满足",
            "basis": "models 已广告 IMAGE_MODEL；未实际出图（--with-image 验证信封）",
        }
    if advertised is False:
        return {"requirement": requirement, "verdict": "不满足", "basis": "models 未广告 IMAGE_MODEL"}
    return {"requirement": requirement, "verdict": "未知", "basis": "P4 未执行"}


# 中文裁决 → 机器可读枚举（T4a 驱动 gate 用；契约经 yijian2-1 对齐 20260812）
VERDICT_CODES = {"满足": "pass", "推定满足": "likely_pass", "不满足": "fail", "未知": "unknown"}


def build_requirement_conclusions(
    *,
    body_limit: dict[str, Any],
    strict: dict[str, Any],
    single_card: dict[str, Any],
    first_byte: dict[str, Any],
    image: dict[str, Any],
) -> dict[str, Any]:
    conclusions = {
        "step2a": judge_step2a(body_limit),
        "step3_v2": judge_step3_v2(strict, single_card, first_byte),
        "step4": judge_step4(image),
    }
    for item in conclusions.values():
        item["verdict_code"] = VERDICT_CODES[item["verdict"]]
    return conclusions


# ===========================================================================
# live 执行
# ===========================================================================


class BudgetExceeded(RuntimeError):
    pass


class CallBudget:
    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.used = 0

    def charge(self) -> None:
        if self.used + 1 > self.cap:
            raise BudgetExceeded(f"network call budget {self.cap} reached")
        self.used += 1


def _error_facts(error: Exception) -> dict[str, Any]:
    status = getattr(error, "status_code", None)
    facts: dict[str, Any] = {
        "error_type": type(error).__name__,
        "http_status": status if isinstance(status, int) and 100 <= status <= 599 else None,
        "message": sanitize_error_text(str(error)),
    }
    response = getattr(error, "response", None)
    if response is not None:
        try:
            body = response.content or b""
            facts["body_kind"] = classify_body_kind(response.headers.get("content-type", ""), body)
            facts["body_bytes"] = len(body)
        except Exception:
            pass
    return facts


def run_p1(transport: OpenAICompatibleStep2Transport, model: str, levels: list[dict[str, Any]], budget: CallBudget) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    failures = 0
    for level in levels:
        budget.charge()
        outcome: dict[str, Any] = {"kb": level["kb"], "content_utf8_kb": level["content_utf8_kb"]}
        started = time.perf_counter()
        try:
            completion = transport.probe_json_schema(
                model=model,
                schema_name="preflight_body_probe",
                response_schema=P1_SCHEMA,
                user_content=level["content"],
                max_tokens=64,
            )
            parsed = json.loads(completion.content)
            outcome.update({"result": "success", "http_status": completion.http_status, "ok": parsed.get("ok")})
        except Exception as error:
            outcome.update({"result": "failure", **_error_facts(error)})
            failures += 1
        outcome["latency_s"] = round(time.perf_counter() - started, 2)
        results.append(outcome)
        print(f"  P1 {level['kb']:>4}KB -> {outcome.get('result')} "
              f"(http={outcome.get('http_status')}, {outcome['latency_s']}s)")
        if failures >= 2:
            break
    return results


def run_p2(transport: OpenAICompatibleStep2Transport, model: str, budget: CallBudget) -> dict[str, Any]:
    section: dict[str, Any] = {}
    budget.charge()
    started = time.perf_counter()
    escape: dict[str, Any]
    try:
        completion = transport.probe_json_schema(
            model=model,
            schema_name="preflight_strict_escape",
            response_schema=P2A_SCHEMA,
            user_content=P2A_BAIT,
            max_tokens=256,
        )
        escape = classify_strict_escape(completion.content)
        escape.update({"http_status": completion.http_status, "content_head": completion.content[:300]})
    except Exception as error:
        facts = _error_facts(error)
        status = facts.get("http_status")
        escape = {"status": "schema_rejected" if isinstance(status, int) and 400 <= status < 500 else "error", **facts}
    escape["latency_s"] = round(time.perf_counter() - started, 2)
    section["escape_probe"] = escape
    print(f"  P2a strict逃逸 -> {escape['status']} ({escape['latency_s']}s)")

    card = build_p2b_card()
    budget.charge()
    started = time.perf_counter()
    single: dict[str, Any]
    try:
        completion = transport.probe_json_schema(
            model=model,
            schema_name="preflight_single_card_echo",
            response_schema=build_p2b_schema(),
            user_content=build_p2b_user_content(card),
            max_tokens=4096,
        )
        single = classify_p2b_echo(completion.content, card)
        single.update(
            {
                "http_status": completion.http_status,
                "output_utf8_kb": round(len(completion.content.encode("utf-8")) / 1024, 1),
            }
        )
    except Exception as error:
        single = {"status": "error", **_error_facts(error)}
    single["elapsed_s"] = round(time.perf_counter() - started, 2)
    section["single_card_probe"] = single
    print(f"  P2b 单卡回显 -> {single['status']} ({single['elapsed_s']}s)")
    return section


def _sse_first_content(buffer: bytes) -> bool:
    return re.search(rb'"content"\s*:\s*"[^"]', buffer) is not None


def run_p3_nonstream(client: httpx.Client, url: str, payload: dict[str, Any], budget: CallBudget) -> dict[str, Any]:
    budget.charge()
    started = time.perf_counter()
    out: dict[str, Any] = {"probe": "p3_nonstream"}
    try:
        response = client.post(url, json=payload)
        elapsed = round(time.perf_counter() - started, 2)
        body_kind = classify_body_kind(response.headers.get("content-type", ""), response.content)
        out.update({"http_status": response.status_code, "elapsed_s": elapsed, "body_kind": body_kind,
                    "response_bytes": len(response.content)})
        if response.status_code == 200 and body_kind == "json":
            document = json.loads(response.content.decode("utf-8"))
            usage = document.get("usage") or {}
            out.update(
                {
                    "outcome": "success",
                    "completion_tokens": usage.get("completion_tokens"),
                    "finish_reason": (document.get("choices") or [{}])[0].get("finish_reason"),
                }
            )
        elif response.status_code in {502, 504, 520, 521, 522, 524} or body_kind == "html":
            out["outcome"] = "gateway_cut"
        else:
            out["outcome"] = "http_error"
    except httpx.TimeoutException as error:
        out.update({"outcome": "client_timeout", "error_type": type(error).__name__,
                    "elapsed_s": round(time.perf_counter() - started, 2)})
    except httpx.HTTPError as error:
        out.update({"outcome": "connection_dropped", "error_type": type(error).__name__,
                    "elapsed_s": round(time.perf_counter() - started, 2)})
    return out


def run_p3_stream(
    client: httpx.Client, url: str, payload: dict[str, Any], budget: CallBudget, *, abort_after_s: float
) -> dict[str, Any]:
    budget.charge()
    started = time.perf_counter()
    out: dict[str, Any] = {
        "probe": "p3_stream",
        "abort_after_s": round(abort_after_s, 1),
        "t_headers_s": None,
        "t_first_byte_s": None,
        "t_first_content_s": None,
        "bytes_received": 0,
        "max_interchunk_gap_s": 0.0,
        "finish_seen": False,
        "aborted_early": False,
    }
    try:
        with client.stream("POST", url, json=payload) as response:
            out["http_status"] = response.status_code
            out["t_headers_s"] = round(time.perf_counter() - started, 2)
            if response.status_code != 200:
                body = response.read()
                out.update(
                    {
                        "outcome": "http_error",
                        "body_kind": classify_body_kind(response.headers.get("content-type", ""), body),
                        "elapsed_s": round(time.perf_counter() - started, 2),
                    }
                )
                return out
            tail = b""
            last_chunk_at = time.perf_counter()
            for chunk in response.iter_raw():
                now = time.perf_counter()
                out["max_interchunk_gap_s"] = max(out["max_interchunk_gap_s"], round(now - last_chunk_at, 2))
                last_chunk_at = now
                if out["t_first_byte_s"] is None:
                    out["t_first_byte_s"] = round(now - started, 2)
                out["bytes_received"] += len(chunk)
                tail = (tail + chunk)[-16384:]
                if out["t_first_content_s"] is None and _sse_first_content(tail):
                    out["t_first_content_s"] = round(now - started, 2)
                if b"[DONE]" in tail:
                    out["finish_seen"] = True
                    break
                if now - started > abort_after_s and out["t_first_content_s"] is not None:
                    out["aborted_early"] = True
                    break
        out["outcome"] = "success" if out["finish_seen"] else ("aborted_early" if out["aborted_early"] else "stream_ended")
    except httpx.HTTPError as error:
        out.update({"outcome": "stream_cut", "error_type": type(error).__name__})
    out["elapsed_s"] = round(time.perf_counter() - started, 2)
    return out


def run_p3(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    target_seconds: float,
    max_tokens_cap: int,
    budget: CallBudget,
) -> dict[str, Any]:
    url = f"{base_url}/chat/completions"
    section: dict[str, Any] = {}
    budget.charge()
    started = time.perf_counter()
    calibration: dict[str, Any] = {"count_to": 150, "max_tokens": 900}
    try:
        response = client.post(url, json=build_p3_count_payload(model=model, count_to=150, max_tokens=900, stream=False))
        elapsed = round(time.perf_counter() - started, 2)
        document = json.loads(response.content.decode("utf-8"))
        usage = document.get("usage") or {}
        content = ((document.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        tokens = usage.get("completion_tokens") or max(int(len(content) / 2), 1)
        calibration.update({"outcome": "success", "elapsed_s": elapsed, "completion_tokens": tokens,
                            "http_status": response.status_code})
    except Exception as error:
        calibration.update({"outcome": "error", **_error_facts(error),
                            "elapsed_s": round(time.perf_counter() - started, 2)})
    section["calibration"] = calibration
    print(f"  P3 标定 -> {calibration.get('outcome')} ({calibration.get('elapsed_s')}s, "
          f"tokens={calibration.get('completion_tokens')})")
    if calibration.get("outcome") != "success":
        section["judgement"] = judge_p3({"outcome": "calibration_failed"}, None)
        return section

    sizing = size_p3_main_workload(
        calibration_tokens=calibration["completion_tokens"],
        calibration_elapsed_s=calibration["elapsed_s"],
        calibration_count_to=150,
        target_seconds=target_seconds,
        max_tokens_cap=max_tokens_cap,
    )
    section["sizing"] = sizing
    print(f"  P3 主工作量 -> 数到 {sizing['count_to']}（吞吐 {sizing['throughput_tokens_per_s']} tok/s, "
          f"max_tokens={sizing['max_tokens']}）")
    main_payload = build_p3_count_payload(model=model, count_to=sizing["count_to"], max_tokens=sizing["max_tokens"], stream=False)
    nonstream = run_p3_nonstream(client, url, main_payload, budget)
    section["nonstream"] = nonstream
    print(f"  P3 非流式 -> {nonstream.get('outcome')} (http={nonstream.get('http_status')}, "
          f"{nonstream.get('elapsed_s')}s, body={nonstream.get('body_kind')})")

    if nonstream.get("outcome") in {"gateway_cut", "connection_dropped"}:
        abort_after = (nonstream.get("elapsed_s") or target_seconds) + 50
    else:
        abort_after = target_seconds * 1.05
    stream_payload = build_p3_count_payload(model=model, count_to=sizing["count_to"], max_tokens=sizing["max_tokens"], stream=True)
    stream = run_p3_stream(client, url, stream_payload, budget, abort_after_s=abort_after)
    section["stream"] = stream
    print(f"  P3 流式 -> {stream.get('outcome')} (首字节 {stream.get('t_first_byte_s')}s, "
          f"首内容 {stream.get('t_first_content_s')}s, 总 {stream.get('elapsed_s')}s)")
    section["judgement"] = judge_p3(nonstream, stream)
    return section


def run_p4(
    client: httpx.Client,
    *,
    base_url: str,
    image_model: str,
    text_model: str,
    image_size: str,
    with_image: bool,
    budget: CallBudget,
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "model_advertised": None,
        "generation_status": "skipped",
        "variant": None,
    }
    budget.charge()
    started = time.perf_counter()
    try:
        response = client.get(f"{base_url}/models")
        ids = [
            str(item["id"])
            for item in (response.json().get("data") or ())
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        section["models_probe"] = {
            "http_status": response.status_code,
            "latency_s": round(time.perf_counter() - started, 2),
            "model_count": len(ids),
            "image_model_advertised": image_model in ids,
            "text_model_advertised": text_model in ids,
            "image_like_ids": [name for name in ids if "image" in name.lower()][:10],
        }
        section["model_advertised"] = image_model in ids if response.status_code == 200 else None
    except Exception as error:
        section["models_probe"] = {"outcome": "error", **_error_facts(error),
                                   "latency_s": round(time.perf_counter() - started, 2)}
    print(f"  P4 models -> 广告 IMAGE_MODEL={section['model_advertised']} "
          f"(http={section.get('models_probe', {}).get('http_status')})")

    if not with_image:
        return section

    width, height = (int(part) for part in image_size.split("x"))
    profile = build_image_profile(model=image_model, image_width=width, image_height=height)
    payload = build_p4_image_payload(image_model=image_model, size=image_size)
    projection, _sidecar = project_image_request(payload, profile)
    budget.charge()
    started = time.perf_counter()
    try:
        response = client.post(f"{base_url}/images/generations", json=projection.http_request.model_dump(mode="json"))
        gate = inspect_image_envelope(response.status_code, response.content, IMAGE_PROBE_PROMPT, profile)
        variant = classify_image_envelope_variant(response.content)
        section.update(
            {
                "generation_status": "gate_valid" if gate.valid else "gate_failed",
                "http_status": response.status_code,
                "latency_s": round(time.perf_counter() - started, 2),
                "variant": variant["variant"],
                "envelope_top_level_keys": variant["top_level_keys"],
                "envelope_data_item_keys": variant["data_item_keys"],
                "gate_failure_codes": list(gate.failure_codes),
                "response_bytes": gate.response_bytes,
                "image_sha256": gate.image_sha256,
                "image_width": gate.image_width,
                "image_height": gate.image_height,
            }
        )
    except Exception as error:
        section.update(
            {
                "generation_status": "error",
                **_error_facts(error),
                "latency_s": round(time.perf_counter() - started, 2),
            }
        )
    print(f"  P4 出图 -> {section['generation_status']} (variant={section.get('variant')}, "
          f"{section.get('latency_s')}s)")
    return section


# ===========================================================================
# 汇总输出
# ===========================================================================


def render_console_matrix(report: dict[str, Any]) -> str:
    channel = report["channel"]
    probes = report["probes"]
    lines = [f"=== relay_preflight 能力矩阵 [{channel['label']} {channel['base_url']}] ==="]
    p1 = probes.get("p1_body_ladder")
    if p1:
        steps = " ".join(
            f"{item['kb']}{'✓' if item.get('result') == 'success' else '✗'}" for item in p1.get("levels", [])
        )
        lines.append(f"P1 体积阶梯 : {steps} → {p1['limit']['summary']}")
    else:
        lines.append("P1 体积阶梯 : 跳过")
    p2 = probes.get("p2_strict")
    if p2:
        escape = p2["escape_probe"]
        single = p2["single_card_probe"]
        lines.append(
            f"P2 strict   : 强制解码={'是' if escape['status'] == 'enforced' else '否' if escape['status'] in ('not_enforced', 'malformed_output') else escape['status']}"
            f"（{escape.get('detail', escape.get('error_type', ''))}）；"
            f"单卡回显={single['status']}（{single.get('elapsed_s')}s, 输出 {single.get('output_utf8_kb', '?')}KB）"
        )
    else:
        lines.append("P2 strict   : 跳过")
    p3 = probes.get("p3_first_byte")
    if p3 and p3.get("judgement"):
        lines.append(f"P3 首字节窗 : {p3['judgement']['detail']}")
    else:
        lines.append("P3 首字节窗 : 跳过（--deep 才跑）")
    p4 = probes.get("p4_image")
    if p4:
        lines.append(
            f"P4 图像端点 : models 广告={p4.get('model_advertised')}；"
            f"实际出图={p4.get('generation_status')}"
            + (
                f"（{p4.get('variant')}, {p4.get('image_width')}x{p4.get('image_height')}, {p4.get('latency_s')}s）"
                if p4.get("generation_status") not in {"skipped", None}
                else ""
            )
        )
    else:
        lines.append("P4 图像端点 : 跳过")
    lines.append("--- 需求画像结论 ---")
    conclusions = report["requirement_conclusions"]
    labels = {"step2a": "step2a ", "step3_v2": "step3v2", "step4": "step4  "}
    for key, label in labels.items():
        item = conclusions[key]
        lines.append(f"{label} ({item['requirement']}): {item['verdict']} —— {item['basis']}")
    return "\n".join(lines)


def build_dry_run_assembly(
    *,
    levels: list[dict[str, Any]],
    text_model: str,
    image_model: str,
    image_size: str,
    deep: bool,
    with_image: bool,
    skip: set[str],
    target_seconds: float,
) -> dict[str, Any]:
    card = build_p2b_card()
    card_json = json.dumps(card, ensure_ascii=False)
    return {
        "p1_body_ladder": {
            "will_run": "p1" not in skip,
            "schema_name": "preflight_body_probe",
            "max_tokens": 64,
            "levels": [{"kb": item["kb"], "content_utf8_kb": item["content_utf8_kb"]} for item in levels],
            "early_stop": "连续 2 级失败即止损",
        },
        "p2_strict": {
            "will_run": "p2" not in skip,
            "escape_probe": {
                "schema": P2A_SCHEMA,
                "user_content": P2A_BAIT,
                "max_tokens": 256,
                "judgement": "空数组/缺项=逃逸判不强制；恰好 3 个 const=强制",
            },
            "single_card_probe": {
                "schema_name": "preflight_single_card_echo",
                "card_utf8_bytes": len(card_json.encode("utf-8")),
                "expected_output_scale": "~1.5K token（T3 v2 单卡契约可行性信号）",
                "max_tokens": 4096,
            },
        },
        "p3_first_byte": {
            "will_run": deep and "p3" not in skip,
            "gate": "--deep",
            "plan": [
                "标定：数到 150（max_tokens 900，非流式），测吞吐",
                f"主探针：按吞吐放大到 ~{target_seconds}s 生成时长，非流式 vs 流式各一次",
                "流式在确证存活越过非流式掐点后提前中止以省费",
            ],
            "text_model": text_model,
        },
        "p4_image": {
            "models_probe": "GET /models（总是跑）",
            "image_generation": {
                "will_run": with_image and "p4" not in skip,
                "gate": "--with-image",
                "payload": build_p4_image_payload(image_model=image_model, size=image_size),
                "envelope_gate": "providers.inspect_image_envelope（b64 严格门禁）+ b64/url 变体判别",
            },
        },
    }


def run_preflight(
    *,
    dry_run: bool = False,
    channel: str | None = None,
    base_url: str | None = None,
    env_file: str | Path | None = None,
    deep: bool = False,
    with_image: bool = False,
    skip: set[str] | frozenset[str] = frozenset(),
    kb_ladder: tuple[int, ...] = DEFAULT_KB_LADDER,
    text_model: str | None = None,
    image_model: str | None = None,
    image_size: str = "1024x1536",
    timeout: float = 480.0,
    p3_target_seconds: float = 210.0,
    p3_max_tokens: int = 16000,
    max_calls: int = DEFAULT_NETWORK_CALL_BUDGET,
    out: str | Path | None = None,
) -> dict[str, Any]:
    """库入口（T4a 全链驱动直调契约，20260812 与 yijian2-1 对齐）。

    - 返回完整 report dict；同一内容总会原子写盘（缺省
      data/relay_preflight/<label>/relay_preflight_report.json），落点见返回值
      ``report["report_path"]``。
    - ``dry_run=True`` 零外呼（仅探针装配自检）；live 时进度打到 stdout。
    - 机器可读判定：``report["requirement_conclusions"][{step2a,step3_v2,step4}]
      ["verdict_code"] ∈ {pass, likely_pass, fail, unknown}``（中文 verdict/basis
      为人读字段）。调用方按本次要跑的 stage 自行取用对应结论做 gate。
    - 配置错误（.env 缺失/通道无密钥/缺 TEXT_MODEL）抛 SystemExit（带中文说明）；
      预算熔断不抛出，落在 ``report["stop_reason"]``。
    """
    skip = {token.lower() for token in skip}
    env_path = resolve_env_file(str(env_file) if env_file else None)
    if not env_path.is_file():
        raise SystemExit(f".env 不存在：{env_path}")
    env_config = parse_env_channels(env_path.read_text(encoding="utf-8", errors="replace"))
    resolved = resolve_channel(env_config, channel=channel, base_url=base_url)
    globals_ = env_config["globals"]
    api_key = resolved.get("api_key") or globals_.get("API_KEY") or ""
    user_agent = globals_.get("OPENAI_USER_AGENT")
    text_model = text_model or globals_.get("TEXT_MODEL") or ""
    image_model = image_model or globals_.get("IMAGE_MODEL") or ""
    if not text_model:
        raise SystemExit(".env 缺 TEXT_MODEL 且未给 text_model")

    # dry-run 报告独立命名，避免覆盖同通道的 live 实测报告
    default_name = REPORT_NAME if not dry_run else REPORT_NAME.replace(".json", "_dryrun.json")
    out_path = Path(out) if out else NEW_ROOT / "data" / "relay_preflight" / resolved["label"] / default_name
    report: dict[str, Any] = {
        "tool": TOOL_VERSION,
        "started_at_utc": _utc_now(),
        "mode": "dry_run" if dry_run else "live",
        "report_path": str(out_path),
        "channel": {
            "label": resolved["label"],
            "base_url": resolved["base_url"],
            "channel_index": resolved["index"],
            "role": resolved.get("role", "text"),
            "env_file": str(env_path),
            "api_key_present": bool(api_key),
            "user_agent_present": bool(user_agent),
        },
        "models": {"text_model": text_model, "image_model": image_model or None},
        "flags": {"deep": deep, "with_image": with_image, "skip": sorted(skip)},
        "requirement_profile": REQUIREMENT_PROFILE,
        "probes": {},
    }

    levels = build_p1_levels(kb_ladder)
    if dry_run:
        report["dry_run_assembly"] = build_dry_run_assembly(
            levels=levels,
            text_model=text_model,
            image_model=image_model or "<未配置>",
            image_size=image_size,
            deep=deep,
            with_image=with_image,
            skip=skip,
            target_seconds=p3_target_seconds,
        )
        report["network_calls"] = 0
        report["finished_at_utc"] = _utc_now()
        _atomic_write_json(out_path, report)
        return report

    if not api_key:
        raise SystemExit("所选通道在 .env 中没有 API_KEY")

    budget = CallBudget(max_calls)
    transport = OpenAICompatibleStep2Transport(
        base_url=resolved["base_url"],
        api_key=api_key,
        timeout=min(timeout, 300.0),
        default_headers={"User-Agent": user_agent} if user_agent else None,
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if user_agent:
        headers["User-Agent"] = user_agent
    client = httpx.Client(
        timeout=httpx.Timeout(connect=30.0, read=timeout, write=120.0, pool=30.0),
        follow_redirects=False,
        headers=headers,
    )

    stop_reason = "completed"
    try:
        if "p1" not in skip:
            print(f"[P1 体积阶梯] {resolved['label']} {tuple(kb_ladder)}")
            level_results = run_p1(transport, text_model, levels, budget)
            report["probes"]["p1_body_ladder"] = {"levels": level_results, "limit": derive_body_limit(level_results)}
        if "p2" not in skip:
            print("[P2 strict 遵从]")
            report["probes"]["p2_strict"] = run_p2(transport, text_model, budget)
        if deep and "p3" not in skip:
            print("[P3 首字节窗口（--deep）]")
            report["probes"]["p3_first_byte"] = run_p3(
                client,
                base_url=resolved["base_url"],
                model=text_model,
                target_seconds=p3_target_seconds,
                max_tokens_cap=p3_max_tokens,
                budget=budget,
            )
        if "p4" not in skip:
            print("[P4 图像端点]")
            if image_model:
                report["probes"]["p4_image"] = run_p4(
                    client,
                    base_url=resolved["base_url"],
                    image_model=image_model,
                    text_model=text_model,
                    image_size=image_size,
                    with_image=with_image,
                    budget=budget,
                )
            else:
                print("  跳过：.env 无 IMAGE_MODEL 且未给 --image-model")
    except BudgetExceeded as error:
        stop_reason = f"budget_exceeded: {sanitize_error_text(str(error))}"
    finally:
        client.close()

    probes = report["probes"]
    body_limit = (probes.get("p1_body_ladder") or {}).get("limit") or derive_body_limit([])
    strict = (probes.get("p2_strict") or {}).get("escape_probe") or {"status": "skipped"}
    single_card = (probes.get("p2_strict") or {}).get("single_card_probe") or {"status": "skipped"}
    first_byte = (probes.get("p3_first_byte") or {}).get("judgement") or judge_p3(None, None)
    image = probes.get("p4_image") or {"model_advertised": None, "generation_status": "skipped"}
    report["requirement_conclusions"] = build_requirement_conclusions(
        body_limit=body_limit,
        strict=strict,
        single_card=single_card,
        first_byte=first_byte,
        image=image,
    )
    report["network_calls"] = budget.used
    report["stop_reason"] = stop_reason
    report["finished_at_utc"] = _utc_now()
    _atomic_write_json(out_path, report)
    return report


def main() -> int:
    """CLI 薄壳。退出码语义：0=预检流程完成且报告已产出（结论判定由调用方读
    report 的 verdict_code 决定，工具不替调用方裁决）；非 0=流程本身失败
    （配置错误 SystemExit / 参数错误 argparse）。"""
    # Windows GBK 控制台打不出 ✓/→ 等矩阵符号，统一按 UTF-8 输出
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="中继能力预检（四合一自检 + 需求画像结论）")
    parser.add_argument("--allow-live", action="store_true", help="真实外呼实测")
    parser.add_argument("--dry-run", action="store_true", help="零外呼自检：打印探针装配")
    parser.add_argument("--channel", choices=("first", "last", "image"), default=None,
                        help=".env 通道选择：first/last=通用键按出现顺序的首/末对；image=显式 IMAGE_API_* 对")
    parser.add_argument("--base-url", default=None, help="显式指定 base_url（须在 .env 中有成对密钥）")
    parser.add_argument("--env-file", default=None, help="显式指定 .env 路径（默认：新仓根 .env，缺则 yijian2/.env）")
    parser.add_argument("--deep", action="store_true", help="跑 P3 首字节窗口探针（长调用，费钱）")
    parser.add_argument("--with-image", action="store_true", help="P4 真出 1 张最小图验信封")
    parser.add_argument("--skip", default="", help="跳过探针组，逗号分隔（p1,p2,p3,p4）")
    parser.add_argument("--kb-ladder", default=",".join(str(kb) for kb in DEFAULT_KB_LADDER),
                        help="P1 体积阶梯（KB，逗号分隔）")
    parser.add_argument("--text-model", default=None, help="覆盖 .env TEXT_MODEL")
    parser.add_argument("--image-model", default=None, help="覆盖 .env IMAGE_MODEL")
    parser.add_argument("--image-size", default="1024x1536", help="P4 出图尺寸（与既有 pilot 口径一致）")
    parser.add_argument("--timeout", type=float, default=480.0, help="单请求读超时（秒）")
    parser.add_argument("--p3-target-seconds", type=float, default=210.0, help="P3 主探针目标生成时长")
    parser.add_argument("--p3-max-tokens", type=int, default=16000, help="P3 主探针 max_tokens 上限")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_NETWORK_CALL_BUDGET, help="网络调用预算熔断")
    parser.add_argument("--out", default=None, help="报告输出路径（默认 data/relay_preflight/<label>/relay_preflight_report.json）")
    args = parser.parse_args()
    if args.allow_live == args.dry_run:
        parser.error("choose exactly one of --allow-live or --dry-run")

    report = run_preflight(
        dry_run=args.dry_run,
        channel=args.channel,
        base_url=args.base_url,
        env_file=args.env_file,
        deep=args.deep,
        with_image=args.with_image,
        skip={token.strip().lower() for token in args.skip.split(",") if token.strip()},
        kb_ladder=tuple(int(token) for token in str(args.kb_ladder).split(",") if token.strip()),
        text_model=args.text_model,
        image_model=args.image_model,
        image_size=args.image_size,
        timeout=args.timeout,
        p3_target_seconds=args.p3_target_seconds,
        p3_max_tokens=args.p3_max_tokens,
        max_calls=args.max_calls,
        out=args.out,
    )
    if args.dry_run:
        print(json.dumps(report["dry_run_assembly"], ensure_ascii=False, indent=2))
        print(f"\n[dry-run] 探针装配自检完成，零外呼。报告：{report['report_path']}")
    else:
        print()
        print(render_console_matrix(report))
        print(f"\n网络调用 {report['network_calls']} 次；报告：{report['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
