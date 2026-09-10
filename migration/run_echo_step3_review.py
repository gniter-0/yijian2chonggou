"""echo 批 step3 评审外置道薄壳（P8 补链施工 #3 CLI 面；B1-P2 先例的驱动外置车道）。

两个子命令（零网络）：
- ``emit``：echo run 逐卡实录 + typed fixture → 待审底稿 `echo_step3_review_sheet.json`
 （人工回填 disposition/reviewer/basis/b2_review 取证块——评审执行权归 B2 权威口径）。
- ``ingest``：严格摄取回填后的底稿 → released 集合 → **链内构建** `echo-batch-release/1.0`
  fixture + 签发 `echo-batch-step4-handoff/1.0`（授权语义 = 摄取门禁签发的正向事实）。
  产物落 --out-dir：echo-batch-release.json / echo-batch-handoff.json。

随后出图：
  uv run python migration/run_exam_pipeline.py --exam <exam> --stages step4 --allow-live \
      --budget-usd 0.4 --run-label <label> --step4-source echo-batch \
      --echo-release <out>/echo-batch-release.json --echo-handoff <out>/echo-batch-handoff.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

NEW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.compilers.step4_echo_bridge import (  # noqa: E402
    build_echo_release_fixture,
    emit_echo_step3_review_sheet,
)
from examcard.gates.review.echo_step3_review import (  # noqa: E402
    ingest_echo_step3_review,
    issue_echo_batch_handoff,
)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    emit = commands.add_parser("emit", help="produce the review sheet awaiting human dispositions")
    emit.add_argument("--echo-run-dir", type=Path, required=True)
    emit.add_argument("--typed-fixture", type=Path, required=True)
    emit.add_argument("--out-dir", type=Path, required=True)
    emit.add_argument("--contract-version", choices=("v1", "v2"), default="v1")

    ingest = commands.add_parser("ingest", help="ingest the backfilled sheet; build release + issue handoff")
    ingest.add_argument("--sheet", type=Path, required=True)
    ingest.add_argument("--echo-run-dir", type=Path, required=True)
    ingest.add_argument("--typed-fixture", type=Path, required=True)
    ingest.add_argument("--echo-run-id", type=str, required=True)
    ingest.add_argument("--out-dir", type=Path, required=True)
    ingest.add_argument("--contract-version", choices=("v1", "v2"), default="v1")

    args = parser.parse_args()

    if args.command == "emit":
        if args.contract_version == "v2":
            from examcard.compilers.step4_echo_bridge_v2 import emit_echo_step3_review_sheet_v2

            sheet_path = emit_echo_step3_review_sheet_v2(
                echo_run_dir=args.echo_run_dir, typed_fixture_path=args.typed_fixture, out_dir=args.out_dir
            )
        else:
            sheet_path = emit_echo_step3_review_sheet(
                echo_run_dir=args.echo_run_dir, typed_fixture_path=args.typed_fixture, out_dir=args.out_dir
            )
        print(json.dumps({"sheet": str(sheet_path), "status": "awaiting_human"}, ensure_ascii=False))
        return 0

    release: Any
    handoff: Any
    if args.contract_version == "v2":
        from examcard.compilers.step4_echo_bridge_v2 import build_echo_release_fixture_v2
        from examcard.gates.review.echo_step3_review_v2 import ingest_echo_step3_review_v2, issue_echo_batch_handoff_v2

        outcome = ingest_echo_step3_review_v2(sheet_path=args.sheet)
    else:
        outcome = ingest_echo_step3_review(sheet_path=args.sheet)
    if outcome["status"] == "waiting_human":
        print(json.dumps(outcome, ensure_ascii=False))
        return 2
    if outcome["status"] != "released":
        print(json.dumps(outcome, ensure_ascii=False))
        return 1
    sheet = json.loads(args.sheet.read_text(encoding="utf-8"))
    approved_seqs = {row["card_seq"] for row in outcome["released"]}
    if args.contract_version == "v2":
        release = build_echo_release_fixture_v2(
            typed_fixture_path=args.typed_fixture,
            echo_run_dir=args.echo_run_dir,
            review_sheet_path=args.sheet,
            approved_rows=outcome["released"],
            source_echo_run_id=args.echo_run_id,
        )
    else:
        release = build_echo_release_fixture(
            typed_fixture_path=args.typed_fixture,
            echo_run_dir=args.echo_run_dir,
            review_sheet_path=args.sheet,
            approved_rows=outcome["released"],
            source_echo_run_id=args.echo_run_id,
        )
    # 授权签发：签发人=底稿内首个 approved 行的评审人（B2），取证块取其行内 b2_review。
    first_approved = next(row for row in sheet["cards"] if row["card_seq"] in approved_seqs)
    if args.contract_version == "v2":
        handoff = issue_echo_batch_handoff_v2(
            release=release,
            authorized_by=str(first_approved["reviewer"]),
            authorization_evidence=dict(first_approved["b2_review"]),
            ingest_receipt=outcome.get("ingest_receipt"),
        )
    else:
        handoff = issue_echo_batch_handoff(
            release=release,
            authorized_by=str(first_approved["reviewer"]),
            authorization_evidence=dict(first_approved["b2_review"]),
        )
    release_path = args.out_dir / "echo-batch-release.json"
    handoff_path = args.out_dir / "echo-batch-handoff.json"
    _atomic_write_json(release_path, release.model_dump(mode="json"))
    _atomic_write_json(handoff_path, handoff.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "status": "issued",
                "batch_id": release.batch_id,
                "card_count": release.card_count,
                "release": str(release_path),
                "handoff": str(handoff_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
