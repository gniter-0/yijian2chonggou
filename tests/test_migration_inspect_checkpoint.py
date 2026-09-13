from __future__ import annotations

import json
from pathlib import Path

from migration import inspect_checkpoint


def test_cli_requires_an_explicit_run_root(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["inspect_checkpoint.py", "inspect", "run"])
    try:
        inspect_checkpoint._parse_args()
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("missing --run-root must be rejected")


def test_cli_emits_machine_readable_observation_without_mutation(tmp_path: Path, capfd) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    state = run_root / "run_state.json"
    state.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "exam_id": "exam-1",
                "stages_requested": ["step1"],
                "stage_states": {"step1": {"status": "completed"}},
                "checkpoints": {},
            }
        ),
        encoding="utf-8",
    )
    before = state.read_bytes()

    assert inspect_checkpoint.main(["inspect", "run", "--run-root", str(run_root)]) == 0

    output, error = capfd.readouterr()
    assert error == ""
    document = json.loads(output)
    assert document["inspection_mode"] == "observation_only"
    assert document["run"]["run_id"] == "run-1"
    assert state.read_bytes() == before


def test_existing_execution_cli_is_not_imported_or_changed() -> None:
    source = Path(inspect_checkpoint.__file__).read_text(encoding="utf-8")
    assert "run_exam_pipeline" not in source
    assert "resume" not in source.casefold()
    assert "delivery" in source
