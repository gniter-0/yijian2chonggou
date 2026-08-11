"""审阅工作台渲染器：纯函数、无 flask、两界原则的结构保障。"""
from __future__ import annotations

import ast
import inspect
import json

import pytest

import examcard.webui.review_workbench as workbench_module
from examcard.gates.review.browser import PENDING_DECISION_HASH
from examcard.gates.review.contracts import precheck_contract_payload
from examcard.webui.review_workbench import compact_queue_entry, render_review_workbench
from tests.fixtures.admission_kernel import KernelCandidate, make_queue_item

QUEUE_SHA = "d" * 64


def _entries() -> list[dict[str, object]]:
    item = make_queue_item()
    candidate = KernelCandidate()
    return [compact_queue_entry(item, candidate)]


def test_compact_queue_entry_projects_pages_with_encoded_uri() -> None:
    entry = _entries()[0]
    assert entry["candidate_id"] == "candidate:test:0001"
    page = entry["pages"][0]  # type: ignore[index]
    assert page["image_uri"].startswith("/output_images/")
    assert "%" in page["image_uri"]  # 中文路径段已百分号编码
    assert page["image_width"] == 1200


def test_render_workbench_is_selfcontained_html() -> None:
    html = render_review_workbench(_entries(), queue_sha256=QUEUE_SHA)
    assert html.startswith("<!doctype html>")
    assert QUEUE_SHA in html
    assert PENDING_DECISION_HASH in html
    assert "浏览器决定是 audit，不是生产授权" in html
    assert "queue-data" in html and json.dumps(_entries(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") in html


def test_render_workbench_escapes_script_breaker() -> None:
    entries = _entries()
    entries[0]["subject_label"] = "</script><b>x</b>"
    html = render_review_workbench(entries, queue_sha256=QUEUE_SHA)
    assert "</script><b>x</b>" not in html
    assert "<\\/script>" in html


def test_workbench_module_never_imports_flask_or_gates_upward() -> None:
    tree = ast.parse(inspect.getsource(workbench_module))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert "flask" not in imported


@pytest.mark.parametrize(
    ("payload", "presentation", "expected"),
    [
        (None, "diagram", ("contract_missing",)),
        ({"presentation": "table", "schema_version": "v", "contract_sha256": "h"}, "diagram", ("presentation_mismatch", "diagram_structure_incomplete")),
        ({"presentation": "table", "schema_version": "v", "contract_sha256": "h", "cells": [1], "rows": 2, "columns": 2}, "table", ()),
        ({"presentation": "ordered_list", "schema_version": "v", "contract_sha256": "h", "items": [1], "continuation_binding_ids": ["x"]}, "ordered_list", ("ordered_list_structure_incomplete",)),
        ({"presentation": "formula", "schema_version": "v", "contract_sha256": "h", "tokens": []}, "formula", ("token_structure_incomplete",)),
    ],
)
def test_precheck_contract_payload(payload: dict | None, presentation: str, expected: tuple[str, ...]) -> None:
    assert precheck_contract_payload(payload, presentation) == expected
