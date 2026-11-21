"""The Version History dialog."""
from __future__ import annotations

import os
from pathlib import Path

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


def test_dialog_empty_state_snapshot_label_and_current_marker(
        qapp, tmp_path, monkeypatch):
    from raptor.gui.ui import history_dialog as history_ui

    model = tmp_path / "plant.rbds"
    model.write_text("model-v1", encoding="utf-8")
    store = HistoryStore(tmp_path / "history")
    dialog = HistoryDialog(None, model, store)
    assert dialog._list.count() == 0
    assert "No versions yet" in dialog._details.text()
    assert not dialog._open_btn.isEnabled()

    monkeypatch.setattr(history_ui.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("baseline", True)))
    dialog._snapshot_now()
    assert dialog._list.count() == 1
    item = dialog._list.item(0)
    assert "(current)" in item.text() and "baseline" in item.text()
    assert dialog.current_id() == store.snapshots(model)[0].id

    model.write_text("model-v2", encoding="utf-8")
    dialog.reload()
    assert "(current)" not in dialog._list.item(0).text()
    assert dialog.current_id() is None

    dialog._list.setCurrentRow(0)
    monkeypatch.setattr(history_ui.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("renamed", True)))
    dialog._label_selected()
    assert store.snapshots(model)[0].label == "renamed"


def test_dialog_only_shows_the_models_own_versions(qapp, tmp_path):
    a = tmp_path / "a.rbds"
    b = tmp_path / "b.rbds"
    a.write_text("same", encoding="utf-8")
    b.write_text("same", encoding="utf-8")
    store = HistoryStore(tmp_path / "history")
    store.record(a)
    dialog = HistoryDialog(None, b, store)
    assert dialog._list.count() == 0
    assert "No versions yet" in dialog._details.text()


def test_switching_models_keeps_each_history_separate(
        qapp, tmp_path, monkeypatch):
    from raptor.api import history as api_history
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow

    payload = Path("new_docs/models/DC1.rbds").read_bytes()
    a = tmp_path / "a.rbds"
    b = tmp_path / "b.rbds"
    a.write_bytes(payload)
    b.write_bytes(payload)
    store = HistoryStore(tmp_path / "history")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    try:
        win._load_into_app(load_rbds(str(a)), str(a))
        win._record_history(str(a))
        assert len(store.snapshots(a)) == 1
        win._load_into_app(load_rbds(str(b)), str(b))
        assert win._current_file == str(b)
        assert store.snapshots(b) == []
        assert len(store.snapshots(a)) == 1
    finally:
        win._dirty = False
        win.close()


def test_open_as_copy_spawns_an_unsaved_window(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from raptor.api import history as api_history
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow

    source = tmp_path / "plant.rbds"
    source.write_bytes(Path("new_docs/models/DC1.rbds").read_bytes())
    store = HistoryStore(tmp_path / "history")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    target = MainWindow()
    try:
        win._load_into_app(load_rbds(str(source)), str(source))
        win._record_history(str(source))
        shot = store.snapshots(source)[0]

        class FakeDialog:
            def __init__(self, *_a, **_k):
                self.action = "open"
                self.snapshot = shot

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(
            "raptor.gui.ui.history_dialog.HistoryDialog", FakeDialog)
        monkeypatch.setattr(win, "_spawn_window", lambda: target)
        win._open_history_dialog()
        assert target._rbd is not None
        assert target._current_file is None       # Save As is required
        assert target._dirty is False
        assert target._rbd is not win._rbd
        assert "unsaved model" in target._statusbar.currentMessage()
    finally:
        for window in (target, win):
            window._dirty = False
            window.close()


def test_restore_warns_when_the_file_changed_outside_the_app(
        qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from raptor.api import history as api_history
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import dialogs, settings
    from raptor.gui.ui.main_window import MainWindow

    source = tmp_path / "plant.rbds"
    source.write_bytes(Path("new_docs/models/DC1.rbds").read_bytes())
    store = HistoryStore(tmp_path / "history")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    try:
        win._load_into_app(load_rbds(str(source)), str(source))
        win._record_history(str(source))
        shot = store.snapshots(source)[0]
        original = source.read_bytes()
        source.write_bytes(original + b"\n")     # changed by another program
        assert win._history_changed_on_disk(str(source))

        class FakeDialog:
            def __init__(self, *_a, **_k):
                self.action = "restore"
                self.snapshot = shot

            def exec(self):
                return QDialog.DialogCode.Accepted

        asked: list[str] = []
        monkeypatch.setattr(
            "raptor.gui.ui.history_dialog.HistoryDialog", FakeDialog)
        monkeypatch.setattr(
            win, "_confirm_discard_unsaved", lambda *a, **k: True)
        monkeypatch.setattr(
            dialogs, "question_box",
            lambda _p, title, *a, **k: asked.append(title) or True)
        win._open_history_dialog()
        assert "File Changed Outside RAPTOR Studio" in asked
        assert source.read_bytes() == original
        # The externally changed bytes were captured before the overwrite.
        assert any(s.reason == "before restore" for s in store.snapshots(source))
    finally:
        win._dirty = False
        win.close()


def test_restore_reloads_the_window_and_clears_the_dirty_flag(
        qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from raptor.api import history as api_history
    from raptor.core.fileio.rbds import load_rbds
    from raptor.core.history import HistoryStore
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow

    source = tmp_path / "plant.rbds"
    source.write_bytes(Path("new_docs/models/DC1.rbds").read_bytes())
    store = HistoryStore(tmp_path / "history")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    try:
        win._load_into_app(load_rbds(str(source)), str(source))
        win._record_history(str(source))
        shot = store.snapshots(source)[0]
        # An edit that the restore must take away.
        row = next(r for r in win._rbd.sections["BLOCK_DATA"].rows
                   if r.get("ID") not in ("", "0"))
        original = row["blockName"]
        row["blockName"] = "CHANGED IN MEMORY"
        win._dirty = True

        class FakeDialog:
            def __init__(self, *_a, **_k):
                self.action = "restore"
                self.snapshot = shot

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(
            "raptor.gui.ui.history_dialog.HistoryDialog", FakeDialog)
        monkeypatch.setattr(
            win, "_confirm_discard_unsaved",
            lambda *a, **k: True)
        from raptor.gui.ui import dialogs
        monkeypatch.setattr(dialogs, "question_box", lambda *a, **k: True)
        win._open_history_dialog()
        row = next(r for r in win._rbd.sections["BLOCK_DATA"].rows
                   if r.get("ID") not in ("", "0"))
        assert row["blockName"] == original
        assert win._dirty is False
        assert load_rbds(str(source)).sections["BLOCK_DATA"].rows[1]["blockName"]
    finally:
        win._dirty = False
        win.close()
