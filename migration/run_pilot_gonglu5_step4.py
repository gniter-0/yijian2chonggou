"""公路黄金五章 step4 出图 pilot（16 张统一发布卡真实出图）。

背景（pilot 决策 20260812，用户已批 ≈$5 全链；step2a 被中继体积上限挡住另行处理）：
本段校准 step4 的真实出图成功率、单张时延与信封兼容性——全量 65 章跑批的最大成本
与不确定项。中继可用性已由老仓 r119（20260811，同端点同模型）验证。

方案要点：
- 输入：老仓只读——r2242 统一发布 fixture（16 卡/28 facts/52 可见文本，YAML）+
  r319 pilot handoff；新仓 admission 模型装载，`derive_step4_bundle` 强校验后
  取 16 张 GenericCardContract；
- prompt：沿用 r119 单卡源文本投影形态（source-owned only，逐字保真，不加装饰）；
- 传输：POST {base}/v1/images/generations，公网体经 providers.project_image_request
  严格五字段投影（内部字段决不进 HTTP 体——r116 教训）；响应经
  providers.inspect_image_envelope 具名门禁校验（含 PNG 尺寸/哈希）；
- 纪律：1 次 /models 探针先行；单卡失败登记后继续（中继波动不熔断整场）；
  预算硬上限 20 次（1 探针 + 16 图 + 3 余量，余量仅供人工指定重发时使用，本驱动
  不自动重试）；密钥自 yijian2\\.env 只读，仅驻内存；图片与审计件落 storage root。

运行（exam-card-engine 仓根）：
  uv run python migration/run_pilot_gonglu5_step4.py --dry-run --storage-root data/pilot_gonglu5/step4_dryrun
  uv run python migration/run_pilot_gonglu5_step4.py --allow-live --storage-root data/pilot_gonglu5/step4_live

prompt v2（20260812 评审底稿修复轨：diagram 结构枚举 + 字形保真硬性要求；默认 v1 逐字节不变）：
  uv run python migration/run_pilot_gonglu5_step4.py --allow-live --prompt-variant v2 --only-cards 2,6,8,10,12,16 \
      --env-file .env --storage-root data/pilot_gonglu5/step4_fix_v2
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

NEW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.utils.legacy_root import resolve_legacy_root  # noqa: E402

OLD_ROOT = resolve_legacy_root(NEW_ROOT)

from examcard.compilers.step4_contract import derive_step4_bundle  # noqa: E402
from examcard.knowledge.contracts.admission_fixtures import (  # noqa: E402
    Step3UnifiedReviewedReleaseFixture,
    Step4PilotHandoffRefresh,
)
from examcard.providers import (  # noqa: E402
    build_image_profile,
    inspect_image_envelope,
    project_image_request,
)
from examcard.pipeline.shadow_wiring import load_env_file  # noqa: E402

UNIFIED_FIXTURE = OLD_ROOT / "scripts/tests/fixtures/v2/release_fixtures/gonglu_phase2r2242_step3_reviewed_release.yaml"
R319_HANDOFF = OLD_ROOT / "data/semantic_llm_v2/phase2r319_step4_card4_reviewed_image_fixture_run1/step4-pilot-handoff-refresh.json"

IMAGE_WIDTH = 1024
IMAGE_HEIGHT = 1536
NETWORK_CALL_BUDGET = 20
REPORT_NAME = "pilot_gonglu5_step4_report.json"
PRESENTATIONS = ("diagram", "table", "ordered_list", "constraint", "formula")


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def source_prompt(presentation: str, source_texts: tuple[str, ...]) -> str:
    """r119 单卡源文本投影形态（presentation 面放开到全部五种发布卡型）。"""
    if presentation not in PRESENTATIONS or not source_texts:
        raise ValueError("pilot prompt requires one presentation and source-owned texts")
    return (
        "Render one PNG using only the source-owned text below. Preserve every source character and line break exactly. "
        "Do not add, rewrite, translate, summarize, infer, or expose metadata. Do not add a title, relation label, watermark, or branding.\n"
        f"Presentation: {presentation}\n"
        "SOURCE_TEXT_BEGIN\n"
        + "\nSOURCE_ITEM_SEPARATOR\n".join(source_texts)
        + "\nSOURCE_TEXT_END"
    )


def classify_benign_envelope_drift(body: bytes, profile) -> bytes | None:
    """20260812 深夜实测：本地中继 data[0] 新增 width/height 两个良性元数据键（值=画像尺寸），
    冻结适配器按键集精确性正确拒绝（envelope drift 检测在履职）。本函数只在
    「恰好多出 width/height 且与画像钉扎尺寸一致」时返回剥离后的等价 body 供门禁复检；
    任何其他漂移一律返回 None（保持 fail-closed）。providers 冻结面零改动。"""
    try:
        document = json.loads(body.decode("utf-8"))
        item = document["data"][0]
    except Exception:
        return None
    if not isinstance(item, dict):
        return None
    expected = set(profile.data_item_keys)
    if set(item) != expected | {"width", "height"}:
        return None
    if item.get("width") != profile.image_width or item.get("height") != profile.image_height:
        return None
    document = dict(document)
    document["data"] = [{key: value for key, value in item.items() if key in expected}]
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def _v2_structure_block(card) -> str:
    """prompt v2 结构枚举块：diagram 出 NODES/EDGES，table 出 TABLE_GRID；其余卡型无块。

    信息全部来自 GenericCardContract 既有字段（bundle 内已冻结、零推断）：
    diagram 节点标签取 visible_texts 的 semantic_node_id 映射，边取 semantic_layout_edges；
    table 网格取 table_contracts 的 reviewed_grid 单元格图。关系词仅作布局提示，
    并在硬性要求里禁止其成为可见文字（对齐 v1 冻结口径 relation_layout_only）。
    """
    if card.render_presentation == "diagram":
        labels = {item.semantic_node_id: item.visible_text for item in card.visible_texts if item.semantic_node_id}
        nodes = [labels[node_id] for node_id in card.diagram_node_ids]
        node_lines = "\n".join(f"  N{index}. {label}" for index, label in enumerate(nodes, 1))
        edge_lines = "\n".join(
            f"  E{index}. {labels[edge.source_node_id]} -> {labels[edge.target_node_id]} (layout hint: {edge.layout_relation})"
            for index, edge in enumerate(card.semantic_layout_edges, 1)
        )
        return (
            "STRUCTURE_BEGIN\n"
            f"NODES ({len(nodes)}):\n{node_lines}\n"
            f"EDGES ({len(card.semantic_layout_edges)}):\n{edge_lines}\n"
            "STRUCTURE_END"
        )
    if card.render_presentation == "table":
        blocks: list[str] = []
        for table in card.table_contracts:
            if table.table_state != "reviewed_grid":
                continue
            cell_lines = "\n".join(
                f"  r{cell.row} c{cell.column}"
                + (f" (span {cell.row_span}x{cell.column_span})" if cell.row_span > 1 or cell.column_span > 1 else "")
                + f": {cell.text}"
                for cell in table.cells
            )
            blocks.append(
                f"TABLE_GRID_BEGIN ({table.row_count} rows x {table.column_count} columns; cells listed as row/column, 0-based)\n"
                f"{cell_lines}\n"
                "TABLE_GRID_END"
            )
        return "\n".join(blocks)
    return ""


def source_prompt_v2(card) -> str:
    """v2 变体（20260812）：v1 全文保留 + 硬性要求（字形保真/全量覆盖/禁空框禁臆造）+ 结构枚举块。

    针对评审底稿缺陷归因：D1 diagram 结构失真（prompt 只给标签没给节点/边）、
    D2 阈值符号级错误（＞550→≥550）、D3 表题整行漏渲染。默认 v1 路径逐字节不变。
    """
    texts = tuple(item.visible_text for item in card.visible_texts)
    base = source_prompt(card.render_presentation, texts)
    requirements = [
        "HARD REQUIREMENTS:",
        "1. Copy every comparison/range symbol glyph-for-glyph exactly as written in the source "
        "(＞ vs ≥, ＜ vs ≤, ～ ranges, — dashes). Never substitute one symbol for another: "
        "if the source says ＞550 the image must show ＞550, not ≥550.",
        "2. Render every SOURCE item completely and exactly once, including any table caption line "
        "(a line like 表x.x-x ...) as a visible caption row above the table grid. Omitting a line is a failure.",
        "3. Never draw an empty/unlabeled box, never invent an extra box, node, row, column, line or text "
        "that is not in the source.",
    ]
    structure = _v2_structure_block(card)
    if card.render_presentation == "diagram":
        node_count = len(card.diagram_node_ids)
        edge_count = len(card.semantic_layout_edges)
        requirements.append(
            f"4. Draw a node-and-edge diagram with exactly {node_count} labeled boxes (one per NODES entry) "
            f"and exactly {edge_count} connections (one per EDGES entry). Every box must contain its label text.",
        )
        requirements.append(
            "5. Connect exactly the listed EDGES pairs with clear lines/arrows between their two named boxes; "
            "a child box in a containment/hierarchy edge must visually attach under its own parent box, never under "
            "any other box. Do not connect any pair that is not listed. The layout-hint words in parentheses "
            "describe placement only and must NOT appear as visible text in the image.",
        )
    elif card.render_presentation == "table" and structure:
        requirements.append(
            "4. Reproduce the table exactly as TABLE_GRID specifies: same row count, same column count, "
            "every cell text verbatim in its listed row/column position.",
        )
    parts = [base, "\n".join(requirements)]
    if structure:
        parts.append(structure)
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--only-card-index", type=int, default=None, help="live: send exactly one card (manual retry lane)")
    parser.add_argument("--only-cards", type=str, default=None, help="live: comma separated card indexes, e.g. 2,6,8,10,12,16 (defect re-render lane)")
    parser.add_argument("--prompt-variant", choices=("v1", "v2"), default="v1", help="v1: r119 source projection (default, unchanged); v2: + hard requirements & structure enumeration (20260812 fix lane)")
    parser.add_argument("--env-file", type=Path, default=OLD_ROOT / ".env", help="env file path (default: yijian2/.env; new-repo runs pass .env)")
    args = parser.parse_args()
    if args.allow_live == args.dry_run:
        parser.error("choose exactly one of --allow-live or --dry-run")
    selected_cards: set[int] | None = None
    if args.only_cards:
        selected_cards = {int(item) for item in args.only_cards.split(",") if item.strip()}
    if args.only_card_index is not None:
        selected_cards = (selected_cards or set()) | {args.only_card_index}

    env = load_env_file(args.env_file)
    # 图像通道显式键优先（20260812 双通道配置：文本走 2chat 覆盖值，生图锁定 chatgpt2api-new）
    base_url = (env.get("IMAGE_API_BASE_URL") or env.get("API_BASE_URL") or "").rstrip("/")
    api_key = env.get("IMAGE_API_KEY") or env.get("API_KEY") or ""
    model = env.get("IMAGE_MODEL") or ""
    missing = [name for name, value in (("IMAGE_API_BASE_URL/API_BASE_URL", base_url), ("IMAGE_MODEL", model)) if not value]
    if args.allow_live and not api_key:
        missing.append("IMAGE_API_KEY/API_KEY")
    if missing:
        raise SystemExit(f"yijian2/.env is missing required keys: {missing}")

    storage = Path(args.storage_root).resolve()
    storage.mkdir(parents=True, exist_ok=True)

    fixture_bytes = UNIFIED_FIXTURE.read_bytes()
    handoff_bytes = R319_HANDOFF.read_bytes()
    fixture = Step3UnifiedReviewedReleaseFixture.model_validate(yaml.safe_load(fixture_bytes.decode("utf-8")))
    handoff = Step4PilotHandoffRefresh.model_validate_json(handoff_bytes.decode("utf-8"))
    bundle, model_input, _request, _artifacts, _batch = derive_step4_bundle(
        fixture,
        handoff,
        unified_fixture_sha256=_sha256_bytes(fixture_bytes),
        handoff_sha256=_sha256_bytes(handoff_bytes),
    )

    profile = build_image_profile(model=model, image_width=IMAGE_WIDTH, image_height=IMAGE_HEIGHT)
    projections = []
    for card in bundle.cards:
        if args.prompt_variant == "v2":
            prompt = source_prompt_v2(card)
        else:
            prompt = source_prompt(card.render_presentation, tuple(item.visible_text for item in card.visible_texts))
        payload = {
            "model": model,
            "n": 1,
            "prompt": prompt,
            "response_format": "b64_json",
            "size": f"{IMAGE_WIDTH}x{IMAGE_HEIGHT}",
            "source_card_id": card.source_card_id,
            "card_index": card.card_index,
            "sample_id": card.sample_id,
        }
        projection, sidecar = project_image_request(payload, profile)
        projections.append({"card": card, "prompt": prompt, "projection": projection, "sidecar": sidecar})

    report: dict[str, object] = {
        "pilot": "gonglu5_step4",
        "started_at_utc": _utc_now(),
        "mode": "live" if args.allow_live else "dry_run",
        "model": model,
        "prompt_variant": args.prompt_variant,
        "size": f"{IMAGE_WIDTH}x{IMAGE_HEIGHT}",
        "unified_fixture_sha256": _sha256_bytes(fixture_bytes),
        "r319_handoff_sha256": _sha256_bytes(handoff_bytes),
        "card_count": len(bundle.cards),
        "fact_count": model_input.fact_count,
        "network_call_budget": NETWORK_CALL_BUDGET,
        "probe": None,
        "cards": [],
        "accepted": False,
        "stop_reason": None,
    }
    _atomic_write_json(
        storage / "requests-manifest.json",
        [
            {
                "card_index": entry["card"].card_index,
                "sample_id": entry["card"].sample_id,
                "source_card_id": entry["card"].source_card_id,
                "render_presentation": entry["card"].render_presentation,
                "prompt_variant": args.prompt_variant,
                "visible_text_count": len(entry["card"].visible_texts),
                "prompt_utf8_bytes": len(entry["prompt"].encode("utf-8")),
                "http_payload_utf8_bytes": entry["projection"].http_payload_utf8_bytes,
                "http_payload_sha256": entry["projection"].http_payload_sha256,
                "sidecar_field_names": list(entry["projection"].sidecar_field_names),
            }
            for entry in projections
        ],
    )

    def finish(exit_code: int) -> int:
        report["finished_at_utc"] = _utc_now()
        cards = report["cards"]
        report["calibration"] = {
            "attempted": len(cards),
            "succeeded": sum(1 for item in cards if item["outcome"] == "success"),
            "total_latency_ms": round(sum(item["latency_ms"] for item in cards), 2),
            "total_response_bytes": sum(item["response_bytes"] for item in cards),
        }
        _atomic_write_json(storage / REPORT_NAME, report)
        print(
            json.dumps(
                {
                    "report": str(storage / REPORT_NAME),
                    "mode": report["mode"],
                    "network_calls": report.get("network_calls", 0),
                    "cards_attempted": report["calibration"]["attempted"],
                    "cards_succeeded": report["calibration"]["succeeded"],
                    "accepted": report["accepted"],
                    "stop_reason": report["stop_reason"],
                },
                ensure_ascii=False,
            )
        )
        return exit_code

    if not args.allow_live:
        report.update({"network_calls": 0, "accepted": True, "stop_reason": "dry_run_complete"})
        return finish(0)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if env.get("OPENAI_USER_AGENT"):
        headers["User-Agent"] = env["OPENAI_USER_AGENT"]
    client = httpx.Client(timeout=240.0, follow_redirects=False, headers=headers)
    network_calls = 0
    try:
        started = time.perf_counter()
        probe_response = client.get(f"{base_url}/models")
        network_calls += 1
        advertised: list[str] = []
        try:
            advertised = [
                str(item["id"])
                for item in probe_response.json().get("data", ())
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
        except ValueError:
            pass
        report["probe"] = {
            "http_status": probe_response.status_code,
            "model_advertised": model in advertised,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if probe_response.status_code != 200 or model not in advertised:
            report.update({"network_calls": network_calls, "stop_reason": "models_probe_failed"})
            return finish(1)

        images_dir = storage / "images"
        for entry in projections:
            card = entry["card"]
            if selected_cards is not None and card.card_index not in selected_cards:
                continue
            image_path = images_dir / f"step4-card-{card.card_index:02d}-{card.source_card_id.rsplit(':', 1)[-1][:8]}.png"
            if image_path.exists():
                continue  # 断点续跑：已成功出图的卡直接跳过
            if network_calls + 1 > NETWORK_CALL_BUDGET:
                report.update({"network_calls": network_calls, "stop_reason": "network_call_budget_reached"})
                return finish(1)
            payload = entry["projection"].http_request.model_dump(mode="json")
            started = time.perf_counter()
            record: dict[str, object] = {
                "card_index": card.card_index,
                "sample_id": card.sample_id,
                "source_card_id": card.source_card_id,
                "render_presentation": card.render_presentation,
                "outcome": "unknown_outcome",
                "http_status": None,
                "latency_ms": 0.0,
                "response_bytes": 0,
                "gate_failures": [],
                "image_path": None,
            }
            try:
                response = client.post(f"{base_url}/images/generations", json=payload)
                network_calls += 1
                record["http_status"] = response.status_code
                record["response_bytes"] = len(response.content)
                gate = inspect_image_envelope(response.status_code, response.content, entry["prompt"], profile)
                record["gate_failures"] = list(gate.failure_codes)
                if not gate.valid:
                    stripped = classify_benign_envelope_drift(response.content, profile)
                    if stripped is not None:
                        regate = inspect_image_envelope(response.status_code, stripped, entry["prompt"], profile)
                        if regate.valid:
                            record["envelope_drift"] = "data0_extra_width_height_stripped"
                            gate = regate
                if gate.valid:
                    document = json.loads(response.content.decode("utf-8"))
                    image_bytes = base64.b64decode(document["data"][0]["b64_json"], validate=True)
                    images_dir.mkdir(parents=True, exist_ok=True)
                    image_path.write_bytes(image_bytes)
                    record.update(
                        {
                            "outcome": "success",
                            "image_path": image_path.relative_to(storage).as_posix(),
                            "image_sha256": gate.image_sha256,
                            "image_bytes": len(image_bytes),
                        }
                    )
                else:
                    record["outcome"] = "failed"
            except Exception as error:  # 单卡失败登记后继续（网络波动不熔断整场）
                network_calls += 1
                record["outcome"] = "unknown_outcome"
                record["error_type"] = type(error).__name__
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            report["cards"].append(record)
            _atomic_write_json(storage / REPORT_NAME, report)
    finally:
        client.close()

    succeeded = sum(1 for item in report["cards"] if item["outcome"] == "success")
    attempted = len(report["cards"])
    report.update(
        {
            "network_calls": network_calls,
            "accepted": attempted > 0 and succeeded == attempted,
            "stop_reason": "completed" if succeeded == attempted else "completed_with_failures",
        }
    )
    return finish(0 if report["accepted"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
