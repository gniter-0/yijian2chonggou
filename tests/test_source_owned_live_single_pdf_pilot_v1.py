"""Focused offline tests for the source-owned live single-PDF runner."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import examcard.knowledge.extraction_v1 as extraction_module
import migration.run_source_owned_live_single_pdf_pilot_v1 as pilot
from examcard.knowledge.contracts import (
    SOURCE_OWNED_PROVIDER_MAX_RETRIES,
    SourceOwnedProviderCompletion,
    SourceOwnedProviderRequest,
    Step2ResponseFormat,
    build_source_owned_provider_request,
)
from examcard.knowledge.llm_adapter import Step2OpenAIAdapter
from migration.run_source_owned_live_single_pdf_pilot_v1 import (
    SourceOwnedLiveOperatorConfigurationError,
    SourceOwnedLiveSinglePdfPilotSummary,
    run_source_owned_live_single_pdf_pilot,
)


def _make_pdf(path: Path, *, text: str | None) -> Path:
    escaped = "" if text is None else text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 24 48 Td ({escaped}) Tj ET".encode("ascii")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    document = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_offset = len(document)
    document += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    document += b"0000000000 65535 f \n"
    document += b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    document += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(document)
    return path


def _response_payload(request: SourceOwnedProviderRequest) -> str:
    model_input = request.model_input
    anchor_ids = model_input.get("anchor_span_ids")
    assert isinstance(anchor_ids, list)
    assert len(anchor_ids) == 1
    return json.dumps(
        {
            "proposals": [
                {
                    "fact_type": "definition",
                    "label": "Live source-owned fact",
                    "assertion": "The selected source defines this fact.",
                    "primary_anchor_span_ids": anchor_ids,
                }
            ]
        }
    )


class _FakeLiveTransport:
    max_retries = SOURCE_OWNED_PROVIDER_MAX_RETRIES

    def __init__(
        self,
        *,
        invalid: bool = False,
        fail: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self.invalid = invalid
        self.fail = fail
        self.error = error
        self.calls = 0
        self.requests: list[SourceOwnedProviderRequest] = []

    def complete_source_owned(
        self,
        *,
        request: SourceOwnedProviderRequest,
        response_schema: dict[str, object],
        prompt: str,
    ) -> SourceOwnedProviderCompletion:
        self.calls += 1
        self.requests.append(request)
        assert response_schema == request.response_schema
        assert prompt == request.prompt_template
        if self.error is not None:
            raise self.error
        if self.fail:
            raise RuntimeError("provider content must not escape")
        payload = json.loads(_response_payload(request))
        if self.invalid:
            proposal = payload["proposals"][0]
            assert isinstance(proposal, dict)
            proposal["numeric_claims"] = []
        return SourceOwnedProviderCompletion(
            content=json.dumps(payload),
            finish_reason="stop",
        )


def _operator_env() -> dict[str, str]:
    return {
        "API_BASE_URL": "https://text.example/v1",
        "API_KEY": "operator-test-secret",
        "TEXT_MODEL": "configured-source-owned-model",
        "OPENAI_USER_AGENT": "source-owned-test-agent",
    }


def test_operator_factory_uses_existing_runtime_env_accessors_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    env = _operator_env()

    class _FakeOperatorTransport:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)

    monkeypatch.setattr(pilot, "load_run_env", lambda _path=None: env)
    monkeypatch.setattr(pilot, "text_base_url", lambda value: value["API_BASE_URL"])
    monkeypatch.setattr(pilot, "text_api_key", lambda value: value["API_KEY"])
    monkeypatch.setattr(pilot, "OpenAICompatibleSourceOwnedLiveTransport", _FakeOperatorTransport)

    transport = pilot._build_default_source_owned_live_transport(env_file="ignored.env")

    assert isinstance(transport, _FakeOperatorTransport)
    assert observed["base_url"] == env["API_BASE_URL"]
    assert observed["api_key"] == env["API_KEY"]
    assert observed["timeout"] == 600.0
    assert observed["default_headers"] == {"User-Agent": env["OPENAI_USER_AGENT"]}
    assert "summary" not in observed


def test_cli_model_resolution_prefers_explicit_model_over_text_model() -> None:
    env = _operator_env()

    assert pilot._resolve_source_owned_live_model(None, env) == env["TEXT_MODEL"]
    assert pilot._resolve_source_owned_live_model("explicit-source-owned-model", env) == "explicit-source-owned-model"


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    (
        (None, Step2ResponseFormat.JSON_SCHEMA_STRICT),
        ("", Step2ResponseFormat.JSON_SCHEMA_STRICT),
        ("STRICT_SCHEMA", Step2ResponseFormat.JSON_SCHEMA_STRICT),
        ("JSON_OBJECT", Step2ResponseFormat.JSON_OBJECT),
    ),
)
def test_operator_response_mode_maps_to_source_owned_decoder(
    raw_mode: str | None,
    expected: Step2ResponseFormat,
) -> None:
    env = {} if raw_mode is None else {"STEP2_RESPONSE_MODE": raw_mode}

    decoder = pilot._source_owned_decoder_config_from_env(env)

    assert decoder.response_format == expected
    assert decoder.temperature == 0
    assert decoder.max_tokens == 1400


def test_invalid_operator_response_mode_fails_closed_before_transport_creation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        pilot,
        "load_run_env",
        lambda _path=None: {
            **_operator_env(),
            "STEP2_RESPONSE_MODE": "UNSUPPORTED_MODE",
        },
    )
    monkeypatch.setattr(
        pilot,
        "OpenAICompatibleSourceOwnedLiveTransport",
        lambda **_kwargs: pytest.fail("transport must not be constructed for invalid response mode"),
    )
    pdf = _make_pdf(repo_tmp_path / "invalid-response-mode.pdf", text="private PDF text")
    output_dir = repo_tmp_path / "invalid-response-mode-output"

    exit_code = pilot.main((str(pdf), "--output-dir", str(output_dir)))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == "stage=operator safe_error_category=response_mode_invalid\n"
    assert "Traceback" not in captured.err
    payload = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert payload["provider_call_count"] == 0


def test_response_modes_change_all_source_owned_request_identity_fields() -> None:
    model_input = {"anchor_span_ids": ["span:" + "a" * 24]}
    strict = pilot._source_owned_decoder_config_from_env({})
    object_mode = pilot._source_owned_decoder_config_from_env(
        {"STEP2_RESPONSE_MODE": "JSON_OBJECT"}
    )

    strict_request = build_source_owned_provider_request(
        model="configured-source-owned-model",
        model_input=model_input,
        decoder=strict,
    )
    object_request = build_source_owned_provider_request(
        model="configured-source-owned-model",
        model_input=model_input,
        decoder=object_mode,
    )

    assert strict_request.identity.decoder.response_format == Step2ResponseFormat.JSON_SCHEMA_STRICT
    assert object_request.identity.decoder.response_format == Step2ResponseFormat.JSON_OBJECT
    assert strict_request.identity.request_id != object_request.identity.request_id
    assert strict_request.identity.cache_key != object_request.identity.cache_key
    assert strict_request.identity.idempotency_key != object_request.identity.idempotency_key


def test_missing_operator_configuration_fails_closed_without_transport_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pilot, "load_run_env", lambda _path=None: {"TEXT_MODEL": "configured-model"})
    monkeypatch.setattr(
        pilot,
        "OpenAICompatibleSourceOwnedLiveTransport",
        lambda **_kwargs: pytest.fail("transport must not be constructed for missing configuration"),
    )

    with pytest.raises(SourceOwnedLiveOperatorConfigurationError) as raised:
        pilot._build_default_source_owned_live_transport()

    assert raised.value.safe_error_category == "base_url_missing"
    assert "operator-test-secret" not in str(raised.value)


def test_cli_explicit_model_is_passed_to_runner_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    env = _operator_env()
    fake_transport = _FakeLiveTransport()
    observed: dict[str, object] = {}

    monkeypatch.setenv("REAL_NETWORK", "0")
    monkeypatch.setattr(pilot, "load_run_env", lambda _path=None: env)
    monkeypatch.setattr(pilot, "_build_default_source_owned_live_transport", lambda **_kwargs: fake_transport)

    def fake_runner(
        pdf_path: Path,
        *,
        transport: object,
        model: str,
        decoder: object,
        diagnostic_sink: object,
        output_dir: Path | None,
    ) -> SourceOwnedLiveSinglePdfPilotSummary:
        observed.update(
            {
                "pdf_path": pdf_path,
                "transport": transport,
                "model": model,
                "decoder": decoder,
                "diagnostic_sink": diagnostic_sink,
                "output_dir": output_dir,
            }
        )
        return SourceOwnedLiveSinglePdfPilotSummary(
            document_sha256="0" * 64,
            pages=0,
            evidence_units=0,
            provider_call_count=0,
            response_validation_status="rejected",
            proposal_count=0,
            anchor_selection_status="failed_closed",
            derivation_status="failed_closed",
            grounded_numeric_claim_count=0,
            materialization_status="failed_closed",
        )

    monkeypatch.setattr(pilot, "run_source_owned_live_single_pdf_pilot", fake_runner)
    pdf = repo_tmp_path / "operator.pdf"
    pdf.write_bytes(b"%PDF-test")
    output_dir = repo_tmp_path / "operator-output"

    exit_code = pilot.main(
        (
            str(pdf),
            "--model",
            "explicit-source-owned-model",
            "--output-dir",
            str(output_dir),
        )
    )

    assert exit_code == 0
    assert observed["model"] == "explicit-source-owned-model"
    assert observed["transport"] is fake_transport
    assert observed["decoder"].response_format == Step2ResponseFormat.JSON_SCHEMA_STRICT
    assert observed["diagnostic_sink"] is pilot._write_safe_dispatch_diagnostic
    assert observed["output_dir"] == output_dir
    assert fake_transport.calls == 0


def test_cli_configuration_failure_emits_safe_summary_and_no_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo_tmp_path: Path,
) -> None:
    secret = "operator-api-key-that-must-not-escape"
    monkeypatch.setattr(pilot, "load_run_env", lambda _path=None: {"API_KEY": secret})
    pdf = _make_pdf(repo_tmp_path / "config-failure.pdf", text="private PDF text")
    output_dir = repo_tmp_path / "config-failure-output"

    exit_code = pilot.main((str(pdf), "--output-dir", str(output_dir)))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert secret not in captured.out
    assert secret not in captured.err
    payload = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "document_sha256",
        "pages",
        "evidence_units",
        "provider_call_count",
        "response_validation_status",
        "proposal_count",
        "anchor_selection_status",
        "derivation_status",
        "grounded_numeric_claim_count",
        "materialization_status",
    }
    assert payload["provider_call_count"] == 0
    assert "private PDF text" not in json.dumps(payload).lower()


def test_cli_missing_env_file_fails_closed_before_transport_creation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        pilot,
        "OpenAICompatibleSourceOwnedLiveTransport",
        lambda **_kwargs: pytest.fail("transport must not be constructed when env is missing"),
    )
    pdf = _make_pdf(repo_tmp_path / "missing-env.pdf", text="private PDF text")
    missing_env = repo_tmp_path / "does-not-exist.env"
    output_dir = repo_tmp_path / "missing-env-output"

    exit_code = pilot.main(
        (
            str(pdf),
            "--env-file",
            str(missing_env),
            "--output-dir",
            str(output_dir),
        )
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err == "stage=operator safe_error_category=run_env_missing\n"
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert str(missing_env) not in captured.err
    payload = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "document_sha256",
        "pages",
        "evidence_units",
        "provider_call_count",
        "response_validation_status",
        "proposal_count",
        "anchor_selection_status",
        "derivation_status",
        "grounded_numeric_claim_count",
        "materialization_status",
    }
    assert payload["provider_call_count"] == 0
    assert "private PDF text" not in json.dumps(payload).lower()


def test_fake_live_transport_runs_the_single_pdf_pipeline_once(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "source.pdf", text="A source-owned fact from the PDF.")
    transport = _FakeLiveTransport()

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        output_dir=repo_tmp_path / "pilot",
    )

    assert isinstance(summary, SourceOwnedLiveSinglePdfPilotSummary)
    assert summary.document_sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert summary.pages == 1
    assert summary.evidence_units > 0
    assert summary.provider_call_count == 1
    assert summary.response_validation_status == "validated"
    assert summary.proposal_count == 1
    assert summary.anchor_selection_status == "selected"
    assert summary.derivation_status == "no_quantitative_tokens"
    assert summary.grounded_numeric_claim_count == 0
    assert summary.materialization_status == "materialized"
    assert transport.calls == 1


def test_json_object_mode_still_uses_strict_local_source_owned_decoder(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "json-object.pdf", text="A source-owned fact.")
    transport = _FakeLiveTransport()
    decoder = pilot._source_owned_decoder_config_from_env(
        {"STEP2_RESPONSE_MODE": "JSON_OBJECT"}
    )

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        decoder=decoder,
        output_dir=repo_tmp_path / "json-object-pilot",
    )

    assert summary.response_validation_status == "validated"
    assert summary.proposal_count == 1
    assert summary.materialization_status == "materialized"
    assert transport.calls == 1
    assert transport.requests[0].identity.decoder.response_format == Step2ResponseFormat.JSON_OBJECT


def test_json_object_mode_rejects_numeric_claim_injection_locally(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "json-object-invalid.pdf", text="A source-owned fact.")
    transport = _FakeLiveTransport(invalid=True)
    decoder = pilot._source_owned_decoder_config_from_env(
        {"STEP2_RESPONSE_MODE": "JSON_OBJECT"}
    )

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        decoder=decoder,
        output_dir=repo_tmp_path / "json-object-invalid-pilot",
    )

    assert summary.response_validation_status == "rejected"
    assert summary.proposal_count == 0
    assert summary.provider_call_count == 1
    assert summary.materialization_status == "failed_closed"
    assert transport.calls == 1


def test_provider_exception_is_fail_closed_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "provider-failure.pdf", text="A source-owned fact.")
    transport = _FakeLiveTransport(fail=True)

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        output_dir=repo_tmp_path / "provider-failure-pilot",
    )

    assert summary.response_validation_status == "rejected"
    assert summary.provider_call_count == 1
    assert summary.proposal_count == 0
    assert summary.materialization_status == "failed_closed"
    assert transport.calls == 1


def test_provider_exception_emits_only_safe_dispatch_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "provider-diagnostic.pdf", text="PRIVATE_EVIDENCE_TEXT")
    transport = _FakeLiveTransport(fail=True)

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        diagnostic_sink=pilot._write_safe_dispatch_diagnostic,
        output_dir=repo_tmp_path / "provider-diagnostic-pilot",
    )
    diagnostic = capsys.readouterr().err

    assert summary.provider_call_count == 1
    assert diagnostic == "stage=transport safe_error_category=transport_failure provider_call_count=1 transport_diagnostic_category=unexpected_transport_failure\n"
    assert "operator-test-secret" not in diagnostic
    assert "provider content must not escape" not in diagnostic
    assert "PRIVATE_EVIDENCE_TEXT" not in diagnostic


def test_invalid_response_schema_fails_closed_before_extraction(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "schema-failure.pdf", text="A source-owned fact.")
    transport = _FakeLiveTransport(invalid=True)

    def forbidden_extraction(*_: object, **__: object) -> None:
        raise AssertionError("source-owned extraction must not receive a rejected response")

    monkeypatch.setattr(extraction_module, "source_owned_extract_document_proposals", forbidden_extraction)
    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        output_dir=repo_tmp_path / "schema-failure-pilot",
    )

    assert summary.response_validation_status == "rejected"
    assert summary.proposal_count == 0
    assert summary.materialization_status == "failed_closed"
    assert transport.calls == 1


def test_invalid_completion_emits_safe_decoder_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "decoder-diagnostic.pdf", text="PRIVATE_EVIDENCE_TEXT")
    transport = _FakeLiveTransport(invalid=True)

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        diagnostic_sink=pilot._write_safe_dispatch_diagnostic,
        output_dir=repo_tmp_path / "decoder-diagnostic-pilot",
    )
    diagnostic = capsys.readouterr().err

    assert summary.provider_call_count == 1
    assert diagnostic.startswith(
        "stage=decoder safe_error_category=response_schema_rejected provider_call_count=1 "
    )
    assert "response_sha256=" in diagnostic
    assert "response_length=" in diagnostic
    assert "finish_reason=stop" in diagnostic
    assert "decoder_diagnostic_category=schema_validation_failure" in diagnostic
    assert "decoder_validation_error_count=1" in diagnostic
    assert "decoder_extra_forbidden_field_count=1" in diagnostic
    assert "decoder_distinct_error_types=extra_forbidden" in diagnostic
    assert "PRIVATE_EVIDENCE_TEXT" not in diagnostic
    assert "provider content must not escape" not in diagnostic
    response_hash = diagnostic.split("response_sha256=", maxsplit=1)[1].split(" ", maxsplit=1)[0]
    assert len(response_hash) == 64
    assert all(character in "0123456789abcdef" for character in response_hash)


def test_transport_failure_emits_typed_safe_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "transport-diagnostic.pdf", text="PRIVATE_EVIDENCE_TEXT")
    transport = _FakeLiveTransport(error=RuntimeError("secret provider transport body"))

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        diagnostic_sink=pilot._write_safe_dispatch_diagnostic,
        output_dir=repo_tmp_path / "transport-diagnostic-pilot",
    )
    diagnostic = capsys.readouterr().err

    assert summary.provider_call_count == 1
    assert diagnostic == (
        "stage=transport safe_error_category=transport_failure provider_call_count=1 "
        "transport_diagnostic_category=unexpected_transport_failure\n"
    )
    assert "PRIVATE_EVIDENCE_TEXT" not in diagnostic
    assert "secret provider transport body" not in diagnostic


def test_network_disabled_does_not_call_fake_provider(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "0")
    pdf = _make_pdf(repo_tmp_path / "network-disabled.pdf", text="A source-owned fact.")
    transport = _FakeLiveTransport()

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        output_dir=repo_tmp_path / "network-disabled-pilot",
    )

    assert summary.response_validation_status == "rejected"
    assert summary.provider_call_count == 0
    assert transport.calls == 0


def test_network_disabled_emits_gate_diagnostic_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "0")
    pdf = _make_pdf(repo_tmp_path / "network-diagnostic.pdf", text="PRIVATE_EVIDENCE_TEXT")
    transport = _FakeLiveTransport()

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        diagnostic_sink=pilot._write_safe_dispatch_diagnostic,
        output_dir=repo_tmp_path / "network-diagnostic-pilot",
    )
    diagnostic = capsys.readouterr().err

    assert summary.provider_call_count == 0
    assert diagnostic == "stage=gate safe_error_category=network_disabled provider_call_count=0\n"
    assert transport.calls == 0
    assert "PRIVATE_EVIDENCE_TEXT" not in diagnostic


def test_nonzero_transport_retry_policy_is_rejected_before_call(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "retry-policy.pdf", text="A source-owned fact.")
    transport = _FakeLiveTransport()
    transport.max_retries = 1

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        output_dir=repo_tmp_path / "retry-policy-pilot",
    )

    assert summary.response_validation_status == "rejected"
    assert summary.provider_call_count == 0
    assert transport.calls == 0


def test_retry_policy_violation_emits_gate_diagnostic_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "retry-diagnostic.pdf", text="PRIVATE_EVIDENCE_TEXT")
    transport = _FakeLiveTransport()
    transport.max_retries = 1

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        diagnostic_sink=pilot._write_safe_dispatch_diagnostic,
        output_dir=repo_tmp_path / "retry-diagnostic-pilot",
    )
    diagnostic = capsys.readouterr().err

    assert summary.provider_call_count == 0
    assert diagnostic == "stage=gate safe_error_category=retry_policy_violation provider_call_count=0\n"
    assert transport.calls == 0
    assert "PRIVATE_EVIDENCE_TEXT" not in diagnostic


def test_live_runner_isolated_from_legacy_extraction_and_adapter(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "isolated.pdf", text="A source-owned fact.")

    def forbidden_legacy(*_: object, **__: object) -> None:
        raise AssertionError("legacy extraction path was entered")

    def forbidden_adapter(*_: object, **__: object) -> None:
        raise AssertionError("legacy Step2 adapter path was entered")

    monkeypatch.setattr(extraction_module, "extract_document_proposals", forbidden_legacy)
    monkeypatch.setattr(Step2OpenAIAdapter, "complete_step2a", forbidden_adapter)
    transport = _FakeLiveTransport()

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=transport,
        output_dir=repo_tmp_path / "isolated-pilot",
    )

    assert summary.materialization_status == "materialized"
    assert transport.calls == 1


def test_summary_is_allow_listed_and_content_free(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    source_text = "PRIVATE_PDF_EVIDENCE_TEXT must never enter the summary."
    pdf = _make_pdf(repo_tmp_path / "secure.pdf", text=source_text)
    output_dir = repo_tmp_path / "secure-pilot"

    run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=_FakeLiveTransport(),
        output_dir=output_dir,
    )
    payload = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    serialized = json.dumps(tuple(payload.values()), sort_keys=True).lower()

    assert set(payload) == {
        "document_sha256",
        "pages",
        "evidence_units",
        "provider_call_count",
        "response_validation_status",
        "proposal_count",
        "anchor_selection_status",
        "derivation_status",
        "grounded_numeric_claim_count",
        "materialization_status",
    }
    assert source_text.lower() not in serialized
    for forbidden in (
        "raw_text",
        "canonical_text",
        "prompt",
        "response",
        "payload",
        "provider",
        "secret",
        "credential",
        "numeric_claims",
    ):
        assert forbidden not in serialized


def test_missing_transport_fails_closed_without_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
    repo_tmp_path: Path,
) -> None:
    monkeypatch.setenv("REAL_NETWORK", "1")
    pdf = _make_pdf(repo_tmp_path / "missing-transport.pdf", text="A source-owned fact.")

    summary = run_source_owned_live_single_pdf_pilot(
        pdf,
        transport=None,
        output_dir=repo_tmp_path / "missing-transport-pilot",
    )

    assert summary.response_validation_status == "rejected"
    assert summary.provider_call_count == 0
    assert summary.materialization_status == "failed_closed"
