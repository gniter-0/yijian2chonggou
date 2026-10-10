"""The agent Step 2 CLI flags Facts that cite Evidence still overlapping the watermark."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _cli():
    spec = importlib.util.spec_from_file_location("run_agent_step2_v1_overlay", ROOT / "migration" / "run_agent_step2_v1.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _unit(evidence_id: str, text: str, *tags: str) -> SimpleNamespace:
    return SimpleNamespace(evidence_id=evidence_id, raw_text=text, risk_tags=frozenset(tags))


def _fact(label: str, primary=(), secondary=(), numbers=()) -> SimpleNamespace:
    return SimpleNamespace(
        label=label,
        primary_evidence_ids=list(primary),
        secondary_evidence_ids=list(secondary),
        numbers=[SimpleNamespace(evidence_id=evidence_id) for evidence_id in numbers],
    )


def test_overlay_warnings_name_each_fact_citing_overlapping_evidence_once():
    prepared = SimpleNamespace(
        manifest=SimpleNamespace(
            pages=[
                SimpleNamespace(
                    page_num=4,
                    evidence_units=[
                        _unit("ev:clean", "宜不小于200m。", "table"),
                        _unit("ev:mixed", "（1）二级及二级以上（高、一）公路", "table", "overlay_overlap"),
                    ],
                )
            ]
        )
    )
    facts = [
        _fact("试验路段长度", primary=["ev:clean"], numbers=["ev:clean"]),
        _fact("试验路段情形", primary=["ev:mixed"], secondary=["ev:clean"], numbers=["ev:mixed"]),
    ]
    findings = _cli().overlay_warnings(prepared, facts)
    assert findings == [
        {
            "fact_label": "试验路段情形",
            "page_num": 4,
            "evidence_id": "ev:mixed",
            "evidence_text": "（1）二级及二级以上（高、一）公路",
        }
    ]
