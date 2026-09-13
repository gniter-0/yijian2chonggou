"""CLI option compatibility tests for the V3/Echo V2 migration surfaces."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from migration import run_exam_pipeline
from examcard.delivery.backend_registry_v1 import DeliveryBackendRegistryV1
from examcard.delivery.filesystem_v1 import (
    FilesystemDirectoryDeliveryAdapter,
    build_filesystem_delivery_target,
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
