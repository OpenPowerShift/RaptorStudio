"""Project Health and Share-safe dialogs."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.api import health
from raptor.core.fileio.rbds import load_rbds
from raptor.gui.ui.project_health_dialog import ProjectHealthDialog, ShareSafeDialog

MODEL = "new_docs/models/DC1.rbds"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_health_dialog_lists_the_report_and_locates(qapp):
    rbd = load_rbds(MODEL)
    report = health.report(rbd)
    seen = []
    dialog = ProjectHealthDialog(None, report, on_locate=seen.append)
    assert dialog._list.count() == len(report.issues)
    if report.issues and report.issues[0].element:
        dialog._list.setCurrentRow(0)
        dialog._locate()
        assert seen == [report.issues[0].element]


def test_share_safe_dialog_writes_a_declassified_copy(qapp, tmp_path):
    rbd = load_rbds(MODEL)
    dialog = ShareSafeDialog(None, str(tmp_path / "share.rbds"))
    assert dialog._names.isChecked()
    assert dialog.options().annotations == "all"
    dialog.path = str(tmp_path / "share.rbds")
    dest = dialog.write(rbd)
    loaded = load_rbds(str(dest))
    names = [r["blockName"] for r in loaded.sections["BLOCK_DATA"].rows
             if r.get("ID") not in ("0", "")]
    assert all(name.startswith("Block ") for name in names)
    assert dialog.report is not None and dialog.report.names >= len(names)


def test_share_safe_can_keep_annotations_and_comments(qapp, tmp_path):
    rbd = load_rbds(MODEL)
    rbd.annotations = [{"id": 1, "kind": "text", "text": "keep me",
                        "parent": 0}]
    dialog = ShareSafeDialog(None, str(tmp_path / "share.rbds"))
    dialog._annotations.setCurrentIndex(
        dialog._annotations.findData("keep"))
    dialog._comments.setChecked(False)
    dialog.path = str(tmp_path / "share.rbds")
    dialog.write(rbd)
    assert load_rbds(str(dialog.path)).annotations
