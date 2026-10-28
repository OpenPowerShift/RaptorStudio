"""No GUI module opens a plain message box except through `raptor.gui.ui.msgbox` (review G2), so a test can answer it."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "raptor"
VERBS = {"warning", "critical", "information", "about"}


def _direct_calls(path: Path) -> list[int]:
    hits = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in VERBS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "QMessageBox"):
            hits.append(node.lineno)
    return hits


def test_no_module_calls_the_qmessagebox_statics_directly():
    offenders = {}
    for path in (ROOT / "gui").rglob("*.py"):
        if path.name == "msgbox.py":
            continue
        lines = _direct_calls(path)
        if lines:
            offenders[str(path.relative_to(ROOT))] = lines
    assert not offenders, f"use raptor.gui.ui.msgbox instead: {offenders}"


def test_a_failure_can_be_answered_through_the_seam(monkeypatch):
    pytest.importorskip("PySide6")
    from raptor.gui.ui import msgbox
    seen = []
    monkeypatch.setattr(msgbox, "critical", lambda *a, **k: seen.append(a[1:]))
    msgbox.critical(None, "Export failed", "disk full")
    assert seen == [("Export failed", "disk full")]
