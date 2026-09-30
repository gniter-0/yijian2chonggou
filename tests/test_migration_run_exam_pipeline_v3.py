"""CLI option compatibility tests for the V3/Echo V2 migration surfaces."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from migration import run_exam_pipeline
from examcard.config import DeliveryBackendConfigV1, DeliveryBackendType
from examcard.delivery.backend_registry_v1 import DeliveryBackendRegistryV1
from examcard.delivery.filesystem_v1 import (
    FilesystemDirectoryDeliveryAdapter,
    build_filesystem_delivery_target,
)
from examcard.delivery.object_storage_v1 import ObjectStorageDeliveryAdapter
from examcard.pipeline.live_run import orchestrate
from tests.test_pipeline_live_run_orchestration import (
    _REHEARSAL_EXAM,
    _resume_config,
    _resume_providers,
    _seed_exam_workspace,
    _seed_paused_run,
)
from examcard.gates.release.publication_v2 import (
    PUBLICATION_SCOPE_V2,
    PublicationAuthorizationV2,
    build_publication_authorization_v2,
)


def test_run_exam_pipeline_accepts_v3_and_echo_batch_v2(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step3,step4",
            "--dry-run",
            "--step3-contract",
            "v3",
            "--step4-source",
            "echo-batch",
        ],
    )
    args = run_exam_pipeline._parse_args()
    assert args.step3_contract == "v3"
    assert args.step4_source == "echo-batch"


def test_run_exam_pipeline_keeps_v2_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_exam_pipeline.py", "--exam", "demo", "--stages", "step3", "--dry-run"],
    )
    args = run_exam_pipeline._parse_args()
    assert args.step3_contract == "v2"
    assert args.step4_source == "pilot-release"
    assert args.delivery_backend is None
    assert args.delivery_backend_config is None


def _write_object_storage_config(path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "backend_type": "object-storage-v1",
        "provider_type": "s3",
        "endpoint": "objects.example.test",
        "bucket": "exam-artifacts",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _FakeObjectStorageProvider:
    provider_type = "s3"

    def __init__(self) -> None:
        self.upload_calls: list[dict[str, str]] = []
        self.lookup_calls: list[dict[str, str]] = []

    def upload(self, *, package_sha256: str, target_sha256: str, idempotency_key: str) -> dict[str, str]:
        call = {
            "package_sha256": package_sha256,
            "target_sha256": target_sha256,
            "idempotency_key": idempotency_key,
        }
        self.upload_calls.append(call)
        return {"status": "success", "remote_reference": "objects/exam-artifacts/package"}

    def lookup(self, *, idempotency_key: str) -> dict[str, str]:
        self.lookup_calls.append({"idempotency_key": idempotency_key})
        return {"status": "success", "remote_reference": "objects/exam-artifacts/package"}


class _FakeCredentialProvider:
    provider_type = "s3"

    def __init__(self, client: _FakeObjectStorageProvider) -> None:
        self.client = client
        self.resolve_calls = 0

    def resolve(self, *, config: DeliveryBackendConfigV1) -> _FakeObjectStorageProvider:
        assert config.backend_type is DeliveryBackendType.OBJECT_STORAGE
        self.resolve_calls += 1
        return self.client


class _MismatchedCredentialProvider(_FakeCredentialProvider):
    provider_type = "gcs"


class _FailingCredentialProvider:
    provider_type = "s3"

    def resolve(self, *, config: DeliveryBackendConfigV1) -> _FakeObjectStorageProvider:
        del config
        raise RuntimeError("secret-value-must-not-escape")


def test_run_exam_pipeline_accepts_object_storage_backend_config(monkeypatch, tmp_path: Path) -> None:
    config_path = _write_object_storage_config(tmp_path / "delivery.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step4",
            "--allow-live",
            "--resume",
            "run-1",
            "--delivery-backend",
            "object-storage-v1",
            "--delivery-backend-config",
            str(config_path),
        ],
    )

    args = run_exam_pipeline._parse_args()

    assert args.delivery_backend == DeliveryBackendType.OBJECT_STORAGE.value
    assert args.delivery_backend_config == config_path
    assert isinstance(args.delivery_backend_config_model, DeliveryBackendConfigV1)
    assert args.delivery_backend_config_model.backend_type is DeliveryBackendType.OBJECT_STORAGE


def test_configure_operator_providers_assembles_object_storage_adapter(
    tmp_path: Path,
) -> None:
    config_path = _write_object_storage_config(tmp_path / "delivery.json")
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
    )
    args = SimpleNamespace(
        enable_publication=False,
        resume="run-1",
        publication_authorization=None,
        delivery_filesystem_root=None,
        delivery_backend=DeliveryBackendType.OBJECT_STORAGE.value,
        delivery_backend_config=config_path,
        delivery_backend_config_model=config,
    )
    provider_client = _FakeObjectStorageProvider()
    credential_provider = _FakeCredentialProvider(provider_client)
    providers: dict[str, object] = {}

    run_exam_pipeline._configure_operator_providers(
        args=args,
        paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
        providers=providers,
        credential_provider=credential_provider,
    )

    assert providers["delivery_backend_config"] is config
    assert providers["publication_enabled"] is True
    assert providers["delivery_enabled"] is True
    assert providers["delivery_adapter_type"] == "object-storage-v1"
    assert isinstance(providers["delivery_adapter"], ObjectStorageDeliveryAdapter)
    assert providers["delivery_target"].destination_config_sha256 == config.canonical_sha256()
    assert credential_provider.resolve_calls == 1
    assert provider_client.upload_calls == []
    assert provider_client.lookup_calls == []


def test_object_storage_assembly_preserves_p6_bindings_and_lookup(tmp_path: Path) -> None:
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
    )
    provider_client = _FakeObjectStorageProvider()
    args = SimpleNamespace(
        enable_publication=False,
        resume="run-1",
        publication_authorization=None,
        delivery_filesystem_root=None,
        delivery_backend=DeliveryBackendType.OBJECT_STORAGE.value,
        delivery_backend_config=tmp_path / "delivery.json",
        delivery_backend_config_model=config,
    )
    providers: dict[str, object] = {}

    run_exam_pipeline._configure_operator_providers(
        args=args,
        paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
        providers=providers,
        credential_provider=_FakeCredentialProvider(provider_client),
    )

    adapter = providers["delivery_adapter"]
    result = adapter.deliver(
        package_sha256="a" * 64,
        target_sha256="b" * 64,
        idempotency_key="c" * 64,
    )

    assert result.status == "success"
    assert provider_client.upload_calls == [
        {
            "package_sha256": "a" * 64,
            "target_sha256": "b" * 64,
            "idempotency_key": "c" * 64,
        }
    ]
    assert adapter.lookup(idempotency_key="c" * 64).status == "success"
    assert provider_client.lookup_calls == [{"idempotency_key": "c" * 64}]



def _object_storage_operator_args(
    *,
    root: Path,
    run_id: str,
    config_path: Path,
    config: DeliveryBackendConfigV1,
    publication_authorization: Path | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        enable_publication=False,
        resume=run_id,
        publication_authorization=publication_authorization,
        delivery_filesystem_root=None,
        delivery_backend=DeliveryBackendType.OBJECT_STORAGE.value,
        delivery_backend_config=config_path,
        delivery_backend_config_model=config,
    )


def _write_operator_publication_authorization(run_root: Path) -> Path:
    publication_dir = run_root / "checkpoints" / "publication"
    manifest = json.loads((publication_dir / "publication-manifest-v2.json").read_text(encoding="utf-8"))
    image_ticket = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))["checkpoints"]["image_review"]
    authorization = build_publication_authorization_v2(
        authorization_id="object-storage-operator-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256=image_ticket["final_release_receipt_sha256"],
        publication_manifest_sha256=manifest["manifest_sha256"],
        authorized_by="release-operator",
        authorization_reason="object-storage operator path test",
        authorized_at="2026-09-27T00:00:00Z",
    )
    path = publication_dir / "publication-authorization-v2.json"
    path.write_text(json.dumps(authorization.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_object_storage_operator_path_reaches_live_p6_with_frozen_identity(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "object-storage-operator-p6"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    config_path = _write_object_storage_config(repo_tmp_path / "delivery.json")
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3", endpoint="objects.example.test", bucket="exam-artifacts"
    )
    provider_client = _FakeObjectStorageProvider()
    credential_provider = _FakeCredentialProvider(provider_client)
    paths = SimpleNamespace(
        live_runs_root=repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs"
    )

    providers = _resume_providers(repo_tmp_path)
    run_exam_pipeline._configure_operator_providers(
        args=_object_storage_operator_args(
            root=repo_tmp_path, run_id=run_id, config_path=config_path, config=config
        ),
        paths=paths,
        providers=providers,
        credential_provider=credential_provider,
    )
    waiting_report, waiting_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (waiting_exit, waiting_report.stop_reason) == (2, "waiting_human_publication")
    assert provider_client.upload_calls == []

    authorization_path = _write_operator_publication_authorization(run_root)
    providers = _resume_providers(repo_tmp_path)
    run_exam_pipeline._configure_operator_providers(
        args=_object_storage_operator_args(
            root=repo_tmp_path,
            run_id=run_id,
            config_path=config_path,
            config=config,
            publication_authorization=authorization_path,
        ),
        paths=paths,
        providers=providers,
        credential_provider=credential_provider,
    )
    report, exit_code = orchestrate(_resume_config(repo_tmp_path, run_id), providers)

    assert (exit_code, report.stop_reason) == (0, "completed")
    assert providers["delivery_adapter_type"] == DeliveryBackendType.OBJECT_STORAGE.value
    assert isinstance(providers["delivery_adapter"], ObjectStorageDeliveryAdapter)
    assert credential_provider.resolve_calls == 2
    assert len(provider_client.upload_calls) == 1
    request = json.loads(
        (run_root / "checkpoints" / "delivery" / "delivery-request-v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert provider_client.upload_calls == [
        {
            "package_sha256": request["package_sha256"],
            "target_sha256": request["target_sha256"],
            "idempotency_key": request["idempotency_key"],
        }
    ]
    state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
    assert state["checkpoints"]["delivery"]["status"] == "frozen"
    assert state["checkpoints"]["delivery"]["delivery_identity"] == request["delivery_identity"]


def test_object_storage_operator_frozen_resume_is_credential_free_and_read_only(repo_tmp_path: Path) -> None:
    _seed_exam_workspace(repo_tmp_path)
    run_id = "object-storage-frozen-resume"
    run_root = _seed_paused_run(repo_tmp_path, run_id)
    config_path = _write_object_storage_config(repo_tmp_path / "delivery.json")
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3", endpoint="objects.example.test", bucket="exam-artifacts"
    )
    provider_client = _FakeObjectStorageProvider()
    credential_provider = _FakeCredentialProvider(provider_client)
    paths = SimpleNamespace(
        live_runs_root=repo_tmp_path / "exams" / _REHEARSAL_EXAM / "data" / "live_runs"
    )

    providers = _resume_providers(repo_tmp_path)
    run_exam_pipeline._configure_operator_providers(
        args=_object_storage_operator_args(
            root=repo_tmp_path, run_id=run_id, config_path=config_path, config=config
        ),
        paths=paths,
        providers=providers,
        credential_provider=credential_provider,
    )
    orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    authorization_path = _write_operator_publication_authorization(run_root)
    providers = _resume_providers(repo_tmp_path)
    run_exam_pipeline._configure_operator_providers(
        args=_object_storage_operator_args(
            root=repo_tmp_path,
            run_id=run_id,
            config_path=config_path,
            config=config,
            publication_authorization=authorization_path,
        ),
        paths=paths,
        providers=providers,
        credential_provider=credential_provider,
    )
    first_report, first_exit = orchestrate(_resume_config(repo_tmp_path, run_id), providers)
    assert (first_exit, first_report.stop_reason) == (0, "completed")
    assert len(provider_client.upload_calls) == 1

    class _NeverResolveCredentialProvider:
        provider_type = "s3"

        def __init__(self) -> None:
            self.resolve_calls = 0

        def resolve(self, *, config: DeliveryBackendConfigV1) -> _FakeObjectStorageProvider:
            del config
            self.resolve_calls += 1
            raise AssertionError("frozen delivery resume must not resolve credentials")

    frozen_credential_provider = _NeverResolveCredentialProvider()
    before = {
        path: path.read_bytes()
        for path in (
            run_root / "run_state.json",
            *(run_root / "checkpoints" / "delivery").glob("delivery-*.json"),
        )
    }
    frozen_providers = _resume_providers(repo_tmp_path)
    run_exam_pipeline._configure_operator_providers(
        args=_object_storage_operator_args(
            root=repo_tmp_path,
            run_id=run_id,
            config_path=config_path,
            config=config,
            publication_authorization=authorization_path,
        ),
        paths=paths,
        providers=frozen_providers,
        credential_provider=frozen_credential_provider,
    )
    assert "delivery_adapter" not in frozen_providers
    frozen_report, frozen_exit = orchestrate(_resume_config(repo_tmp_path, run_id), frozen_providers)

    assert (frozen_exit, frozen_report.stop_reason) == (0, "completed")
    assert frozen_credential_provider.resolve_calls == 0
    assert provider_client.upload_calls == [provider_client.upload_calls[0]]
    assert provider_client.lookup_calls == []
    after = {
        path: path.read_bytes()
        for path in (
            run_root / "run_state.json",
            *(run_root / "checkpoints" / "delivery").glob("delivery-*.json"),
        )
    }
    assert after == before

def test_object_storage_missing_credential_provider_fails_closed(tmp_path: Path) -> None:
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
    )
    args = SimpleNamespace(
        enable_publication=False,
        resume="run-1",
        publication_authorization=None,
        delivery_filesystem_root=None,
        delivery_backend=DeliveryBackendType.OBJECT_STORAGE.value,
        delivery_backend_config=tmp_path / "delivery.json",
        delivery_backend_config_model=config,
    )

    with pytest.raises(SystemExit, match="injected credential provider"):
        run_exam_pipeline._configure_operator_providers(
            args=args,
            paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
            providers={},
        )


def test_object_storage_provider_mismatch_fails_closed(tmp_path: Path) -> None:
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
    )
    args = SimpleNamespace(
        enable_publication=False,
        resume="run-1",
        publication_authorization=None,
        delivery_filesystem_root=None,
        delivery_backend=DeliveryBackendType.OBJECT_STORAGE.value,
        delivery_backend_config=tmp_path / "delivery.json",
        delivery_backend_config_model=config,
    )

    with pytest.raises(SystemExit, match="provider does not match"):
        run_exam_pipeline._configure_operator_providers(
            args=args,
            paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
            providers={},
            credential_provider=_MismatchedCredentialProvider(_FakeObjectStorageProvider()),
        )


def test_object_storage_provider_error_is_redacted(tmp_path: Path) -> None:
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
    )
    args = SimpleNamespace(
        enable_publication=False,
        resume="run-1",
        publication_authorization=None,
        delivery_filesystem_root=None,
        delivery_backend=DeliveryBackendType.OBJECT_STORAGE.value,
        delivery_backend_config=tmp_path / "delivery.json",
        delivery_backend_config_model=config,
    )

    with pytest.raises(SystemExit) as error:
        run_exam_pipeline._configure_operator_providers(
            args=args,
            paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
            providers={},
            credential_provider=_FailingCredentialProvider(),
        )
    assert "secret-value-must-not-escape" not in str(error.value)


def test_completed_object_storage_resume_does_not_require_credentials(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run-1"
    receipt_path = run_root / "checkpoints" / "delivery" / "delivery-receipt-v2.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text("{}", encoding="utf-8")
    config = DeliveryBackendConfigV1.object_storage(
        provider_type="s3",
        endpoint="objects.example.test",
        bucket="exam-artifacts",
    )
    args = SimpleNamespace(
        enable_publication=False,
        resume="run-1",
        publication_authorization=None,
        delivery_filesystem_root=None,
        delivery_backend=DeliveryBackendType.OBJECT_STORAGE.value,
        delivery_backend_config=tmp_path / "delivery.json",
        delivery_backend_config_model=config,
    )
    providers: dict[str, object] = {}

    run_exam_pipeline._configure_operator_providers(
        args=args,
        paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
        providers=providers,
    )

    assert providers["delivery_enabled"] is True
    assert "delivery_adapter" not in providers


def test_object_storage_config_missing_file_fails_closed(monkeypatch, tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step4",
            "--allow-live",
            "--resume",
            "run-1",
            "--delivery-backend",
            "object-storage-v1",
            "--delivery-backend-config",
            str(missing),
        ],
    )

    with pytest.raises(SystemExit, match="missing or not a regular JSON file"):
        run_exam_pipeline._parse_args()
    assert "credential" not in capsys.readouterr().err.lower()


def test_object_storage_config_invalid_file_fails_without_echoing_input(monkeypatch, tmp_path: Path, capsys) -> None:
    config_path = _write_object_storage_config(
        tmp_path / "invalid.json",
        token="do-not-echo-this-secret",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step4",
            "--allow-live",
            "--resume",
            "run-1",
            "--delivery-backend",
            "object-storage-v1",
            "--delivery-backend-config",
            str(config_path),
        ],
    )

    with pytest.raises(SystemExit, match="invalid delivery backend configuration"):
        run_exam_pipeline._parse_args()
    assert "do-not-echo-this-secret" not in capsys.readouterr().err


def test_object_storage_config_missing_required_field_fails_closed(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.json"
    config_path.write_text(
        json.dumps(
            {
                "backend_type": "object-storage-v1",
                "provider_type": "s3",
                "endpoint": "objects.example.test",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step4",
            "--allow-live",
            "--resume",
            "run-1",
            "--delivery-backend",
            "object-storage-v1",
            "--delivery-backend-config",
            str(config_path),
        ],
    )

    with pytest.raises(SystemExit, match="invalid delivery backend configuration"):
        run_exam_pipeline._parse_args()


def test_run_exam_pipeline_rejects_unknown_delivery_backend(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step4",
            "--allow-live",
            "--delivery-backend",
            "unknown-backend",
        ],
    )

    with pytest.raises(SystemExit):
        run_exam_pipeline._parse_args()
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (
            ("--delivery-backend", "object-storage-v1", "--delivery-backend-config", "delivery.json"),
            "requires --resume",
        ),
        (
            ("--delivery-backend", "object-storage-v1"),
            "requires --delivery-backend-config",
        ),
        (
            ("--delivery-backend-config", "delivery.json"),
            "requires --delivery-backend object-storage-v1",
        ),
        (
            ("--delivery-backend", "object-storage-v1", "--delivery-filesystem-root", "delivery"),
            "conflicts",
        ),
        (
            ("--delivery-backend", "filesystem-directory-v1"),
            "requires --delivery-filesystem-root",
        ),
    ],
)
def test_run_exam_pipeline_rejects_object_storage_option_conflicts(monkeypatch, capsys, extra, message) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_exam_pipeline.py", "--exam", "demo", "--stages", "step4", "--allow-live", *extra],
    )

    with pytest.raises(SystemExit):
        run_exam_pipeline._parse_args()
    assert message in capsys.readouterr().err


def test_object_storage_backend_is_rejected_in_dry_run(monkeypatch, tmp_path: Path, capsys) -> None:
    config_path = _write_object_storage_config(tmp_path / "delivery.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step4",
            "--dry-run",
            "--resume",
            "run-1",
            "--delivery-backend",
            "object-storage-v1",
            "--delivery-backend-config",
            str(config_path),
        ],
    )

    with pytest.raises(SystemExit):
        run_exam_pipeline._parse_args()
    assert "require --allow-live" in capsys.readouterr().err


def test_object_storage_parse_does_not_invoke_delivery(monkeypatch, tmp_path: Path) -> None:
    config_path = _write_object_storage_config(tmp_path / "delivery.json")
    provider_client = _FakeObjectStorageProvider()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step4",
            "--allow-live",
            "--resume",
            "run-1",
            "--delivery-backend",
            "object-storage-v1",
            "--delivery-backend-config",
            str(config_path),
        ],
    )

    args = run_exam_pipeline._parse_args()
    run_exam_pipeline._configure_operator_providers(
        args=args,
        paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
        providers={},
        credential_provider=_FakeCredentialProvider(provider_client),
    )

    assert provider_client.upload_calls == []
    assert provider_client.lookup_calls == []


def test_run_exam_pipeline_accepts_p8_publication_and_delivery_options(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exam_pipeline.py",
            "--exam",
            "demo",
            "--stages",
            "step4",
            "--allow-live",
            "--resume",
            "run-1",
            "--enable-publication",
            "--publication-authorization",
            "authorization.json",
            "--delivery-filesystem-root",
            "delivery",
        ],
    )
    args = run_exam_pipeline._parse_args()
    assert args.enable_publication is True
    assert args.publication_authorization == Path("authorization.json")
    assert args.delivery_filesystem_root == Path("delivery")


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--allow-live", "--publication-authorization", "authorization.json"), "requires --resume"),
        (("--allow-live", "--delivery-filesystem-root", "delivery"), "requires --resume"),
        (("--dry-run", "--resume", "run-1", "--delivery-filesystem-root", "delivery"), "dry-run"),
        (("--dry-run", "--enable-publication"), "require --allow-live"),
    ],
)
def test_run_exam_pipeline_rejects_unsafe_p8_combinations(monkeypatch, capsys, extra, message) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_exam_pipeline.py", "--exam", "demo", "--stages", "step4", *extra],
    )
    with pytest.raises(SystemExit):
        run_exam_pipeline._parse_args()
    assert message in capsys.readouterr().err


def _authorization() -> PublicationAuthorizationV2:
    return build_publication_authorization_v2(
        authorization_id="cli-auth",
        publication_scope=PUBLICATION_SCOPE_V2,
        final_release_closure_sha256="a" * 64,
        publication_manifest_sha256="b" * 64,
        authorized_by="operator",
        authorization_reason="approved",
        authorized_at="2026-09-13T00:00:00Z",
    )


def test_load_publication_authorization_is_strict_and_does_not_mutate_input(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    raw = json.dumps(_authorization().model_dump(mode="json"), ensure_ascii=False, indent=2).encode()
    path.write_bytes(raw)

    loaded = run_exam_pipeline._load_publication_authorization(path)

    assert loaded.model_dump(mode="json") == _authorization().model_dump(mode="json")
    assert path.read_bytes() == raw


def test_load_publication_authorization_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="invalid PublicationAuthorizationV2"):
        run_exam_pipeline._load_publication_authorization(path)


def test_configure_operator_providers_publication_only(monkeypatch, tmp_path: Path) -> None:
    authorization = _authorization()
    auth_path = tmp_path / "authorization.json"
    auth_path.write_bytes(json.dumps(authorization.model_dump(mode="json")).encode())
    publication_dir = tmp_path / "runs" / "run-1" / "checkpoints" / "publication"
    calls: list[Path] = []
    monkeypatch.setattr(run_exam_pipeline, "_load_publication_authorization", lambda _path: authorization)

    def validate(*, publication_dir: Path, authorization: object) -> None:
        assert authorization is _authorization_value
        calls.append(publication_dir)

    _authorization_value = authorization
    monkeypatch.setattr(run_exam_pipeline, "_validate_publication_manifest_binding", validate)
    args = SimpleNamespace(
        enable_publication=True,
        resume="run-1",
        publication_authorization=auth_path,
        delivery_filesystem_root=None,
    )
    providers: dict[str, object] = {}

    run_exam_pipeline._configure_operator_providers(
        args=args,
        paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
        providers=providers,
    )

    assert providers == {
        "publication_enabled": True,
        "publication_authorization": authorization,
    }
    assert calls == [publication_dir]


def test_configure_operator_providers_builds_frozen_p7_adapter_through_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "delivery"
    destination.mkdir()
    run_root = tmp_path / "runs" / "run-1"
    create_calls: list[str] = []
    original_create = DeliveryBackendRegistryV1.create

    def record_create(self, backend_type: str, **kwargs: object) -> object:
        create_calls.append(backend_type)
        return original_create(self, backend_type, **kwargs)

    monkeypatch.setattr(DeliveryBackendRegistryV1, "create", record_create)
    args = SimpleNamespace(
        enable_publication=True,
        resume="run-1",
        publication_authorization=None,
        delivery_filesystem_root=destination,
    )
    providers: dict[str, object] = {}

    run_exam_pipeline._configure_operator_providers(
        args=args,
        paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
        providers=providers,
    )

    adapter = providers["delivery_adapter"]
    assert isinstance(adapter, FilesystemDirectoryDeliveryAdapter)
    assert providers["publication_enabled"] is True
    assert providers["delivery_enabled"] is True
    assert providers["delivery_target"] is adapter.target
    assert create_calls == ["filesystem-directory-v1"]
    expected_target = build_filesystem_delivery_target(destination)
    assert adapter.target.model_dump(mode="json") == expected_target.model_dump(mode="json")
    assert adapter.publication_dir == run_root / "checkpoints" / "publication"
    assert adapter.destination_root == destination


def test_configure_operator_providers_rejects_checkpoint_destination(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run-1"
    destination = run_root / "checkpoints" / "nested"
    destination.mkdir(parents=True)
    args = SimpleNamespace(
        enable_publication=True,
        resume="run-1",
        publication_authorization=None,
        delivery_filesystem_root=destination,
    )

    with pytest.raises(SystemExit, match="outside the current run checkpoints"):
        run_exam_pipeline._configure_operator_providers(
            args=args,
            paths=SimpleNamespace(live_runs_root=tmp_path / "runs"),
            providers={},
        )
