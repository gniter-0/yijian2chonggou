"""live_run 编排辅助库测试（P3 T2）：ResendPlan 身份管理 + 运行配置双通道读取。

全离线（规范 T4）；.env 样本一律写入 pytest tmp_path，不触真实密钥文件。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from examcard.knowledge.contracts import (
    STEP2A_REQUEST_VERSION,
    Step2Stage,
    make_step2_request_identity,
)
from examcard.pipeline.live_run import (
    ResendPlan,
    ResendRoundsExhausted,
    default_env_path,
    image_api_key,
    image_base_url,
    image_channel_fallback,
    load_run_env,
    text_api_key,
    text_base_url,
)

ROOT = Path(__file__).resolve().parents[1]


class TestLoadRunEnv:
    def test_default_path_is_new_repo_root_env(self) -> None:
        assert default_env_path() == ROOT / ".env"

    def test_reads_explicit_path_with_env_file_semantics(self, tmp_path: Path) -> None:
        env_file = tmp_path / "run.env"
        env_file.write_text(
            "# comment line\n"
            "API_BASE_URL=http://relay.example/v1\n"
            "API_KEY='quoted-key'\n"
            "TEXT_MODEL=\"gpt-5.6-sol\"\n"
            "API_KEY=last-wins-key\n"
            "not-a-pair-line\n",
            encoding="utf-8",
        )
        env = load_run_env(env_file)
        assert env == {
            "API_BASE_URL": "http://relay.example/v1",
            "API_KEY": "last-wins-key",  # 与 load_env_file 同口径：同键 last-wins
            "TEXT_MODEL": "gpt-5.6-sol",
        }

    def test_missing_file_fails_closed_with_path_in_message(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent.env"
        with pytest.raises(FileNotFoundError, match="absent.env"):
            load_run_env(missing)


class TestDualChannelFallbackOrder:
    GENERIC_ONLY = {
        "API_BASE_URL": "http://text.example/v1",
        "API_KEY": "text-key",
    }
    EXPLICIT_IMAGE = {
        "API_BASE_URL": "http://text.example/v1",
        "API_KEY": "text-key",
        "IMAGE_API_BASE_URL": "http://127.0.0.1:3100/v1",
        "IMAGE_API_KEY": "image-key",
    }

    def test_text_channel_reads_generic_keys(self) -> None:
        assert text_base_url(self.GENERIC_ONLY) == "http://text.example/v1"
        assert text_api_key(self.GENERIC_ONLY) == "text-key"

    def test_image_channel_prefers_explicit_keys(self) -> None:
        assert image_base_url(self.EXPLICIT_IMAGE) == "http://127.0.0.1:3100/v1"
        assert image_api_key(self.EXPLICIT_IMAGE) == "image-key"
        assert image_channel_fallback(self.EXPLICIT_IMAGE) is False

    def test_image_channel_falls_back_to_generic_keys(self) -> None:
        assert image_base_url(self.GENERIC_ONLY) == "http://text.example/v1"
        assert image_api_key(self.GENERIC_ONLY) == "text-key"
        assert image_channel_fallback(self.GENERIC_ONLY) is True

    def test_blank_explicit_key_counts_as_absent(self) -> None:
        env = {**self.GENERIC_ONLY, "IMAGE_API_BASE_URL": "   ", "IMAGE_API_KEY": ""}
        assert image_base_url(env) == "http://text.example/v1"
        assert image_api_key(env) == "text-key"
        assert image_channel_fallback(env) is True

    def test_partial_explicit_image_pair_still_reports_fallback(self) -> None:
        env = {**self.GENERIC_ONLY, "IMAGE_API_BASE_URL": "http://127.0.0.1:3100/v1"}
        assert image_base_url(env) == "http://127.0.0.1:3100/v1"
        assert image_api_key(env) == "text-key"
        assert image_channel_fallback(env) is True  # 显式对不完整 = 仍属回落形态，报告须登记

    def test_missing_channel_fails_closed_naming_keys(self) -> None:
        with pytest.raises(ValueError, match=r"text channel .*API_BASE_URL"):
            text_base_url({})
        with pytest.raises(ValueError, match=r"image channel .*IMAGE_API_KEY.*API_KEY"):
            image_api_key({"API_BASE_URL": "http://text.example/v1"})


class TestResendPlanIdentityUniqueness:
    BASE = "pilot5-step2-20260812"

    def test_first_attempt_uses_base_reason_then_suffixed_rounds(self) -> None:
        plan = ResendPlan(base_reason=self.BASE)
        assert plan.next_reason("gonglu_62") == self.BASE
        assert plan.next_reason("gonglu_62") == f"{self.BASE}-r2"
        assert plan.next_reason("gonglu_62") == f"{self.BASE}-r3"
        assert plan.issued_reasons("gonglu_62") == (self.BASE, f"{self.BASE}-r2", f"{self.BASE}-r3")

    def test_reasons_are_unique_per_sample_and_isolated_across_samples(self) -> None:
        plan = ResendPlan(base_reason=self.BASE)
        issued_62 = [plan.next_reason("gonglu_62"), plan.next_reason("gonglu_62")]
        issued_55 = [plan.next_reason("gonglu_55"), plan.next_reason("gonglu_55")]
        assert len(set(issued_62)) == len(issued_62)
        assert issued_55 == issued_62  # 每样本独立计轮：同轮次同 reason，互不挤占轮数
        assert plan.attempts_used("gonglu_62") == 2
        assert plan.attempts_used("gonglu_55") == 2

    def test_round_cap_refuses_reusing_failed_identities(self) -> None:
        plan = ResendPlan(base_reason=self.BASE, max_attempts=2)
        plan.next_reason("gonglu_62")
        assert plan.can_issue("gonglu_62") is True
        plan.next_reason("gonglu_62")
        assert plan.can_issue("gonglu_62") is False
        with pytest.raises(ResendRoundsExhausted, match="gonglu_62"):
            plan.next_reason("gonglu_62")

    def test_reason_grammar_and_length_fail_closed_at_construction(self) -> None:
        with pytest.raises(ValueError):
            ResendPlan(base_reason="Bad_Upper")  # 大写不满足审计标识符文法
        with pytest.raises(ValueError):
            ResendPlan(base_reason="a" * 94, max_attempts=3)  # 派生 -r3 后超 96 上限
        ResendPlan(base_reason="a" * 93, max_attempts=3)  # 94+3=96 恰好合法
        with pytest.raises(ValueError):
            ResendPlan.reason_for_attempt(self.BASE, 0)
        with pytest.raises(ValueError):
            ResendPlan(base_reason=self.BASE, max_attempts=0)

    def test_new_reason_yields_new_step2_request_identity(self) -> None:
        plan = ResendPlan(base_reason=self.BASE)
        template = "identity integration template"

        def identity(reason: str):
            return make_step2_request_identity(
                stage=Step2Stage.FACT_PLANNING,
                request_version=STEP2A_REQUEST_VERSION,
                model="deterministic-fake-v1",
                payload_sha256="0" * 64,
                prompt_template=template,
                response_schema={"type": "object"},
                manual_reissue_reason=reason,
            )

        first = identity(plan.next_reason("gonglu_62"))
        resend = identity(plan.next_reason("gonglu_62"))
        assert first.manual_reissue_reason == self.BASE
        assert resend.manual_reissue_reason == f"{self.BASE}-r2"
        # reason 参与 identity 摘要：重发 = 新身份，失败身份不可能被复用。
        assert first.request_id != resend.request_id
        assert first.cache_key != resend.cache_key
        assert first.idempotency_key != resend.idempotency_key
