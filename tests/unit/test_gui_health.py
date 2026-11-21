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
    assert all(name.startswith("Block_") for name in names)
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


def test_health_dialog_shows_an_empty_state_and_no_dead_locate_button(qapp):
    report = health.report(load_rbds(MODEL))
    assert not report.issues
    dialog = ProjectHealthDialog(None, report, on_locate=lambda _ref: True)
    assert not dialog._empty.isHidden()
    assert dialog._locate_btn.isEnabled() is False
    assert dialog._list.count() == 0


def test_health_dialog_stays_open_when_locate_fails(qapp):
    rbd = load_rbds(MODEL)
    from raptor.core.fileio.typed import row_int
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    row["blockName"] = ""
    report = health.report(rbd)
    assert report.issues
    dialog = ProjectHealthDialog(None, report, on_locate=lambda _ref: False)
    dialog._list.setCurrentRow(0)
    dialog._locate()
    assert dialog.result() == 0
    assert not dialog._hint.isHidden()
    assert "Could not locate" in dialog._hint.text()


def test_share_safe_dialog_discloses_the_residual_risk(qapp):
    dialog = ShareSafeDialog(None, "share.rbds")
    text = dialog._risk.text()
    assert "Residual risk" in text
    assert "structure" in text and "costs" in text
