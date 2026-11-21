"""The Version History dialog."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.history import HistoryStore
from raptor.gui.ui.history_dialog import HistoryDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_dialog_lists_previews_and_chooses_a_version(qapp, tmp_path):
    model = tmp_path / "plant.rbds"
    model.write_text("model-v1", encoding="utf-8")
    store = HistoryStore(tmp_path / "history")
    older = store.record(model, reason="save")
    model.write_text("model-v2 is longer", encoding="utf-8")
    newer = store.record(model, reason="manual", label="before edit")
    dialog = HistoryDialog(None, model, store)
    assert dialog._list.count() == 2
    assert dialog._list.item(0).text().startswith(newer.time_text())
    assert "model-v2" in dialog._preview.toPlainText()
    dialog._list.setCurrentRow(1)
    assert "model-v1" in dialog._preview.toPlainText()
    dialog._open()
    assert dialog.action == "open" and dialog.snapshot.id == older.id


def test_dialog_delete_keeps_the_file_and_other_versions(qapp, tmp_path):
    model = tmp_path / "plant.rbds"
    model.write_text("model-v1", encoding="utf-8")
    store = HistoryStore(tmp_path / "history")
    first = store.record(model)
    model.write_text("model-v2", encoding="utf-8")
    store.record(model)
    dialog = HistoryDialog(None, model, store)
    dialog._list.setCurrentRow(1)
    import raptor.gui.ui.dialogs as dialogs
    dialogs.question_box = lambda *a, **k: True  # type: ignore[attr-defined]
    dialog._delete()
    assert model.read_text() == "model-v2"
    assert all(s.id != first.id for s in store.snapshots(model))
