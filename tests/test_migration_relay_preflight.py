"""migration/relay_preflight.py 判定逻辑离线单测（T1 交付物配套）。

migration/ 不属于包也不参与测试发现（骨架 T1），故按文件路径显式装载被测模块；
全部用例零外呼（规范 T4），只覆盖探针装配与判定纯函数。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "migration" / "relay_preflight.py"
_SPEC = importlib.util.spec_from_file_location("relay_preflight_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
rp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rp)


SAMPLE_ENV = """
# chatgpt2api 本地地址
API_BASE_URL = http://127.0.0.1:3100/v1
API_KEY = first-key-material
API_BASE_URL = https://2chat.cc/v1
OPENAI_USER_AGENT = Mozilla/5.0
API_KEY = last-key-material
TEXT_MODEL = gpt-5.6-sol
IMAGE_MODEL = gpt-image-2
IMAGE_SIZE = 1536x2048
IMAGE_SIZE = 1024x1536
"""


class TestEnvChannels:
    def test_channels_pair_in_file_order(self) -> None:
        config = rp.parse_env_channels(SAMPLE_ENV)
        channels = config["channels"]
        assert len(channels) == 2
        assert channels[0]["base_url"] == "http://127.0.0.1:3100/v1"
        assert channels[0]["api_key"] == "first-key-material"
        assert channels[0]["label"] == "chatgpt2api-new"
        assert channels[1]["base_url"] == "https://2chat.cc/v1"
        assert channels[1]["api_key"] == "last-key-material"
        assert channels[1]["label"] == "2chat"

    def test_globals_are_last_wins_and_exclude_channel_keys(self) -> None:
        config = rp.parse_env_channels(SAMPLE_ENV)
        assert config["globals"]["IMAGE_SIZE"] == "1024x1536"
        assert config["globals"]["OPENAI_USER_AGENT"] == "Mozilla/5.0"
        assert "API_BASE_URL" not in config["globals"]
        assert "API_KEY" not in config["globals"]

    def test_resolve_channel_first_last_and_base_url(self) -> None:
        config = rp.parse_env_channels(SAMPLE_ENV)
        assert rp.resolve_channel(config, channel="first", base_url=None)["label"] == "chatgpt2api-new"
        assert rp.resolve_channel(config, channel="last", base_url=None)["label"] == "2chat"
        explicit = rp.resolve_channel(config, channel=None, base_url="https://2chat.cc/v1/")
        assert explicit["api_key"] == "last-key-material"

    def test_resolve_channel_requires_selection(self) -> None:
        config = rp.parse_env_channels(SAMPLE_ENV)
        with pytest.raises(SystemExit):
            rp.resolve_channel(config, channel=None, base_url=None)
        with pytest.raises(SystemExit):
            rp.resolve_channel(config, channel=None, base_url="https://unknown.example/v1")

    def test_orphan_api_key_falls_back_to_globals(self) -> None:
        config = rp.parse_env_channels("API_KEY=orphan\nAPI_BASE_URL=https://a.example/v1\n")
        assert config["globals"]["API_KEY"] == "orphan"
        assert config["channels"][0]["api_key"] is None

    # 新仓 .env 约定（T2 落地）：通用键一对（文本）+ 显式 IMAGE_API_* 对（图像）
    NEW_REPO_ENV = (
        "API_BASE_URL = https://2chat.cc/v1\nAPI_KEY = text-key\nTEXT_MODEL = gpt-5.6-sol\n"
        "IMAGE_API_BASE_URL = http://127.0.0.1:3100/v1\nIMAGE_API_KEY = image-key\nIMAGE_MODEL = gpt-image-2\n"
    )

    def test_explicit_image_channel_is_parsed_separately(self) -> None:
        config = rp.parse_env_channels(self.NEW_REPO_ENV)
        assert len(config["channels"]) == 1
        assert config["channels"][0]["role"] == "text"
        image = config["image_channel"]
        assert image["base_url"] == "http://127.0.0.1:3100/v1"
        assert image["api_key"] == "image-key"
        assert image["role"] == "image"
        assert image["label"] == "chatgpt2api-new"
        assert "IMAGE_API_BASE_URL" not in config["globals"]
        assert "IMAGE_API_KEY" not in config["globals"]

    def test_resolve_channel_image_and_base_url_matches_image_pair(self) -> None:
        config = rp.parse_env_channels(self.NEW_REPO_ENV)
        assert rp.resolve_channel(config, channel="image", base_url=None)["api_key"] == "image-key"
        assert rp.resolve_channel(config, channel=None, base_url="http://127.0.0.1:3100/v1")["role"] == "image"
        # 新约定下 first == last == 文本通道
        assert rp.resolve_channel(config, channel="first", base_url=None)["label"] == "2chat"
        assert rp.resolve_channel(config, channel="last", base_url=None)["label"] == "2chat"

    def test_resolve_channel_image_missing_is_explicit_error(self) -> None:
        config = rp.parse_env_channels(SAMPLE_ENV)
        with pytest.raises(SystemExit):
            rp.resolve_channel(config, channel="image", base_url=None)


class TestProbeAssembly:
    def test_p1_levels_match_requested_kb(self) -> None:
        levels = rp.build_p1_levels((12, 40))
        assert [item["kb"] for item in levels] == [12, 40]
        for item in levels:
            assert abs(item["content_utf8_kb"] - item["kb"]) < 1.0

    def test_p2b_card_is_deterministic_and_scaled(self) -> None:
        card = rp.build_p2b_card()
        assert card == rp.build_p2b_card()
        encoded = json.dumps(card, ensure_ascii=False).encode("utf-8")
        # ~1.5K token 输出规模：中文正文 1.2-2.5K 字符量级
        assert 3000 < len(encoded) < 12000
        assert len(card["fields"]) == 8

    def test_p3_sizing_scales_to_target_and_clamps(self) -> None:
        sizing = rp.size_p3_main_workload(
            calibration_tokens=300,
            calibration_elapsed_s=5.0,
            calibration_count_to=150,
            target_seconds=200.0,
            max_tokens_cap=16000,
        )
        assert sizing["throughput_tokens_per_s"] == 60.0
        assert sizing["tokens_per_line"] == 2.0
        assert sizing["count_to"] == pytest.approx(6900, abs=1)
        assert sizing["max_tokens"] == 16000

    def test_dry_run_assembly_reflects_gates(self) -> None:
        assembly = rp.build_dry_run_assembly(
            levels=rp.build_p1_levels((12,)),
            text_model="gpt-x",
            image_model="img-x",
            image_size="1024x1536",
            deep=False,
            with_image=True,
            skip=set(),
            target_seconds=210.0,
        )
        assert assembly["p3_first_byte"]["will_run"] is False
        assert assembly["p4_image"]["image_generation"]["will_run"] is True
        assert assembly["p1_body_ladder"]["levels"] == [{"kb": 12, "content_utf8_kb": pytest.approx(12, abs=1)}]


class TestBodyLimitDerivation:
    def test_limit_range(self) -> None:
        results = [
            {"kb": 12, "result": "success"},
            {"kb": 40, "result": "success"},
            {"kb": 70, "result": "failure"},
            {"kb": 120, "result": "failure"},
        ]
        limit = rp.derive_body_limit(results)
        assert limit["max_success_kb"] == 40
        assert limit["min_failure_kb"] == 70
        assert limit["summary"] == "上限区间 [40,70)KB"

    def test_unbounded(self) -> None:
        results = [{"kb": kb, "result": "success"} for kb in (12, 40, 70, 120, 180, 260)]
        limit = rp.derive_body_limit(results)
        assert limit["min_failure_kb"] is None
        assert limit["summary"] == "≥260KB 未触顶"

    def test_all_fail_and_empty(self) -> None:
        assert rp.derive_body_limit([{"kb": 12, "result": "failure"}])["summary"] == "最小级 12KB 即失败"
        assert rp.derive_body_limit([])["summary"] == "未执行"

    def test_flaky_failure_below_success_is_not_the_ceiling(self) -> None:
        results = [
            {"kb": 12, "result": "failure"},
            {"kb": 40, "result": "success"},
            {"kb": 70, "result": "failure"},
        ]
        limit = rp.derive_body_limit(results)
        assert limit["max_success_kb"] == 40
        assert limit["min_failure_kb"] == 70


class TestStrictEscapeClassification:
    def test_enforced(self) -> None:
        content = json.dumps({"strict_echo": [rp.STRICT_ECHO_CONST] * 3})
        assert rp.classify_strict_escape(content)["status"] == "enforced"

    def test_empty_array_escape(self) -> None:
        assert rp.classify_strict_escape('{"strict_echo": []}')["status"] == "not_enforced"

    def test_missing_key_escape(self) -> None:
        assert rp.classify_strict_escape("{}")["status"] == "not_enforced"

    def test_shape_violation(self) -> None:
        content = json.dumps({"strict_echo": [rp.STRICT_ECHO_CONST, "drift"]})
        assert rp.classify_strict_escape(content)["status"] == "not_enforced"

    def test_malformed(self) -> None:
        assert rp.classify_strict_escape("not json")["status"] == "malformed_output"


class TestSingleCardEcho:
    def test_exact_echo(self) -> None:
        card = rp.build_p2b_card()
        content = json.dumps({"draft_cards": [card]}, ensure_ascii=False)
        assert rp.classify_p2b_echo(content, card)["status"] == "echo_exact"

    def test_empty_escape(self) -> None:
        card = rp.build_p2b_card()
        assert rp.classify_p2b_echo('{"draft_cards": []}', card)["status"] == "escape_empty"

    def test_field_drift(self) -> None:
        card = rp.build_p2b_card()
        drifted = dict(card, title="漂移标题")
        verdict = rp.classify_p2b_echo(json.dumps({"draft_cards": [drifted]}, ensure_ascii=False), card)
        assert verdict["status"] == "echo_mismatch"
        assert "title" in verdict["detail"]


class TestP3Judgement:
    def test_skipped(self) -> None:
        assert rp.judge_p3(None, None)["status"] == "skipped"

    def test_not_triggered_gives_lower_bound(self) -> None:
        verdict = rp.judge_p3(
            {"outcome": "success", "elapsed_s": 182.0},
            {"outcome": "success", "elapsed_s": 190.0},
        )
        assert verdict["status"] == "not_triggered"
        assert verdict["window_s"] == 182.0
        assert verdict["window_is_lower_bound"] is True
        assert verdict["streaming_viable"] is True

    def test_first_byte_cutoff_with_stream_survival(self) -> None:
        verdict = rp.judge_p3(
            {"outcome": "gateway_cut", "elapsed_s": 126.0, "http_status": 524, "body_kind": "html"},
            {"outcome": "aborted_early", "elapsed_s": 178.0},
        )
        assert verdict["status"] == "measured"
        assert verdict["cutoff_kind"] == "first_byte"
        assert verdict["window_s"] == 126.0
        assert verdict["streaming_viable"] is True

    def test_total_duration_cutoff(self) -> None:
        verdict = rp.judge_p3(
            {"outcome": "gateway_cut", "elapsed_s": 125.0, "http_status": 502, "body_kind": "html"},
            {"outcome": "stream_cut", "elapsed_s": 130.0},
        )
        assert verdict["cutoff_kind"] == "total_duration"
        assert verdict["streaming_viable"] is False

    def test_inconclusive_client_timeout(self) -> None:
        verdict = rp.judge_p3({"outcome": "client_timeout", "error_type": "ReadTimeout"}, None)
        assert verdict["status"] == "inconclusive"


class TestImageEnvelopeVariant:
    def test_b64_variant(self) -> None:
        body = json.dumps({"created": 1, "data": [{"b64_json": "aGk=", "revised_prompt": "p"}], "usage": {}}).encode()
        assert rp.classify_image_envelope_variant(body)["variant"] == "b64_json"

    def test_url_variant(self) -> None:
        body = json.dumps({"created": 1, "data": [{"url": "https://cdn.example/x.png"}]}).encode()
        assert rp.classify_image_envelope_variant(body)["variant"] == "url"

    def test_error_envelope_and_non_json(self) -> None:
        assert rp.classify_image_envelope_variant(b'{"error": {"message": "x"}}')["variant"] == "error_envelope"
        assert rp.classify_image_envelope_variant(b"<html></html>")["variant"] == "non_json"


class TestRequirementConclusions:
    def test_step2a_fail_when_limit_below_requirement(self) -> None:
        verdict = rp.judge_step2a({"max_success_kb": 40, "min_failure_kb": 70, "tested_max_kb": 70})
        assert verdict["verdict"] == "不满足"
        assert "[40,70)" in verdict["basis"]

    def test_step2a_presumed_pass_when_unbounded_below_300(self) -> None:
        verdict = rp.judge_step2a({"max_success_kb": 260, "min_failure_kb": None, "tested_max_kb": 260})
        assert verdict["verdict"] == "推定满足"

    def test_step2a_pass_when_direct(self) -> None:
        verdict = rp.judge_step2a({"max_success_kb": 300, "min_failure_kb": None, "tested_max_kb": 300})
        assert verdict["verdict"] == "满足"

    def test_step3v2_fail_on_escape(self) -> None:
        verdict = rp.judge_step3_v2(
            {"status": "not_enforced", "detail": "空数组逃逸"},
            {"status": "echo_exact"},
            {"status": "skipped"},
        )
        assert verdict["verdict"] == "不满足"

    def test_step3v2_pass_with_stream(self) -> None:
        verdict = rp.judge_step3_v2(
            {"status": "enforced"},
            {"status": "echo_exact", "elapsed_s": 40.0},
            {"status": "measured", "window_s": 126.0, "window_is_lower_bound": False, "streaming_viable": True},
        )
        assert verdict["verdict"] == "满足"
        assert "流式可续" in verdict["basis"]

    def test_step3v2_presumed_pass_via_single_card(self) -> None:
        verdict = rp.judge_step3_v2(
            {"status": "enforced"},
            {"status": "echo_exact", "elapsed_s": 38.0},
            {"status": "skipped", "window_s": None, "streaming_viable": None},
        )
        assert verdict["verdict"] == "推定满足"

    def test_step3v2_fail_when_window_short_and_no_stream(self) -> None:
        verdict = rp.judge_step3_v2(
            {"status": "enforced"},
            {"status": "escape_empty"},
            {
                "status": "measured",
                "window_s": 90.0,
                "window_is_lower_bound": False,
                "streaming_viable": False,
                "cutoff_kind": "total_duration",
            },
        )
        assert verdict["verdict"] == "不满足"

    def test_step4_variants(self) -> None:
        assert rp.judge_step4({"generation_status": "gate_valid", "variant": "b64_json",
                               "image_width": 1024, "image_height": 1536})["verdict"] == "满足"
        assert rp.judge_step4({"generation_status": "gate_failed", "variant": "url",
                               "gate_failure_codes": ["b64_strict"]})["verdict"] == "不满足"
        assert rp.judge_step4({"generation_status": "skipped", "model_advertised": True})["verdict"] == "推定满足"
        assert rp.judge_step4({"generation_status": "skipped", "model_advertised": False})["verdict"] == "不满足"
        assert rp.judge_step4({"generation_status": "skipped", "model_advertised": None})["verdict"] == "未知"


class TestLibraryEntryContract:
    """T4a 驱动直调契约（run_preflight + verdict_code，20260812 与 yijian2-1 对齐）。"""

    def test_conclusions_carry_machine_readable_verdict_code(self) -> None:
        conclusions = rp.build_requirement_conclusions(
            body_limit={"max_success_kb": 260, "min_failure_kb": None, "tested_max_kb": 260},
            strict={"status": "enforced"},
            single_card={"status": "echo_exact", "elapsed_s": 26.9},
            first_byte={"status": "measured", "window_s": 125.0, "window_is_lower_bound": False,
                        "streaming_viable": True, "cutoff_kind": "first_byte"},
            image={"model_advertised": False, "generation_status": "skipped"},
        )
        assert conclusions["step2a"]["verdict_code"] == "likely_pass"
        assert conclusions["step3_v2"]["verdict_code"] == "pass"
        assert conclusions["step4"]["verdict_code"] == "fail"
        assert set(rp.VERDICT_CODES.values()) == {"pass", "likely_pass", "fail", "unknown"}

    def test_run_preflight_dry_run_is_offline_and_returns_report(self, tmp_path: Path) -> None:
        env_file = tmp_path / "probe.env"
        env_file.write_text(
            "API_BASE_URL=http://127.0.0.1:3100/v1\nAPI_KEY=k1\n"
            "API_BASE_URL=https://2chat.cc/v1\nAPI_KEY=k2\n"
            "TEXT_MODEL=gpt-x\nIMAGE_MODEL=img-x\n",
            encoding="utf-8",
        )
        out = tmp_path / "report.json"
        report = rp.run_preflight(dry_run=True, channel="last", env_file=env_file, out=out)
        assert report["mode"] == "dry_run"
        assert report["network_calls"] == 0
        assert report["report_path"] == str(out)
        assert report["channel"]["label"] == "2chat"
        assert report["channel"]["api_key_present"] is True
        assert "k2" not in json.dumps(report)  # 密钥值决不入报告
        assert report["dry_run_assembly"]["p1_body_ladder"]["will_run"] is True
        assert out.is_file()


class TestConsoleRendering:
    def test_render_matrix_smoke(self) -> None:
        report = {
            "channel": {"label": "2chat", "base_url": "https://2chat.cc/v1"},
            "probes": {
                "p1_body_ladder": {
                    "levels": [{"kb": 12, "result": "success"}, {"kb": 40, "result": "failure"}],
                    "limit": {"summary": "上限区间 [12,40)KB"},
                },
                "p2_strict": {
                    "escape_probe": {"status": "not_enforced", "detail": "空数组逃逸"},
                    "single_card_probe": {"status": "escape_empty", "elapsed_s": 12.3, "output_utf8_kb": 0.1},
                },
            },
            "requirement_conclusions": {
                "step2a": {"requirement": "r", "verdict": "不满足", "basis": "b"},
                "step3_v2": {"requirement": "r", "verdict": "不满足", "basis": "b"},
                "step4": {"requirement": "r", "verdict": "未知", "basis": "b"},
            },
        }
        rendered = rp.render_console_matrix(report)
        assert "上限区间 [12,40)KB" in rendered
        assert "强制解码=否" in rendered
        assert "P3 首字节窗 : 跳过" in rendered
