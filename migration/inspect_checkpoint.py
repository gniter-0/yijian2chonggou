"""Read-only operator inspection CLI for an explicit live run directory."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from examcard.diagnostics.checkpoint_inspection_v1 import (  # noqa: E402
    CHECKPOINT_NAMES,
    InspectionError,
    inspect_checkpoint,
    inspect_delivery,
    inspect_run,
    render_inspection,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only inspection of an explicit live run checkpoint directory."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="inspect without resuming or changing the run")
    kinds = inspect.add_subparsers(dest="kind", required=True)

    run = kinds.add_parser("run", help="inspect the run state and all known checkpoints")
    run.add_argument("--run-root", type=Path, required=True)

    checkpoint = kinds.add_parser("checkpoint", help="inspect one checkpoint or all checkpoints")
    checkpoint.add_argument("--run-root", type=Path, required=True)
    checkpoint.add_argument("--name", choices=CHECKPOINT_NAMES, default=None)

    delivery = kinds.add_parser("delivery", help="inspect delivery state and receipt references")
    delivery.add_argument("--run-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.kind == "run":
            result = inspect_run(args.run_root)
        elif args.kind == "checkpoint":
            result = inspect_checkpoint(args.run_root, args.name)
        else:
            result = inspect_delivery(args.run_root)
    except InspectionError:
        print("inspection failed: invalid explicit inspection input", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(render_inspection(result))
    return 0 if result.get("inspection_status") != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
