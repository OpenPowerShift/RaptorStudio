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


DC1 = Path(__file__).resolve().parents[2] / "new_docs/models/DC1.rbds"


def _renamed_copy(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new, 1)


def _first_block_name(path: Path) -> str:
    from raptor.core.fileio.rbds import load_rbds
    return str(load_rbds(str(path)).sections["BLOCK_DATA"].rows[0]["blockName"])


def test_dialog_lists_previews_and_chooses_a_version(qapp, tmp_path):
    """The preview describes the version and compares it with the open
    model; it never shows the file's raw text."""
    from raptor.core.fileio.rbds import load_rbds
    model = tmp_path / "plant.rbds"
    text = DC1.read_text(encoding="utf-8")
    model.write_text(text, encoding="utf-8")
    name = _first_block_name(model)
    store = HistoryStore(tmp_path / "history")
    older = store.record(model, reason="save")
    model.write_text(_renamed_copy(text, name, name + "_v2"), encoding="utf-8")
    newer = store.record(model, reason="manual", label="before edit")
    dialog = HistoryDialog(None, model, store,
                           current_model=load_rbds(str(model)))
    assert dialog._list.count() == 2
    assert dialog._list.item(0).text().startswith(newer.time_text())
    assert dialog._stats.text().startswith("Model: ")
    assert "block" in dialog._stats.text()
    shown = dialog._preview.toPlainText()
    assert "Same as the model that is open now" in shown
    assert '"blocks"' not in shown and "{" not in dialog._stats.text()
    dialog._list.setCurrentRow(1)
    shown = dialog._preview.toPlainText()
    assert "Changes from this version to the model open now" in shown
    assert name + "_v2" in shown
    dialog._open()
    assert dialog.action == "open" and dialog.snapshot.id == older.id


def test_an_archive_version_is_summarised_not_dumped(qapp, tmp_path):
    """An `.rbdz` is a zip: its bytes as text were noise, and their stray
    Syriac/Thaana/N'Ko code points flooded the log with font warnings."""
    import json
    from raptor.core.fileio.rbdz import write_archive
    archive = tmp_path / "plant.rbdz"
    write_archive(archive, DC1.read_text(encoding="utf-8"), results={
        "summary.json": json.dumps({"num_runs": 250,
                                    "run_at": "2026-11-23T10:15:00"})})
    store = HistoryStore(tmp_path / "history")
    store.record(archive)
    dialog = HistoryDialog(None, archive, store)
    assert dialog._stats.text().startswith("Model: ")
    shown = dialog._preview.toPlainText()
    assert "250 runs" in shown and "2026-11-23 10:15" in shown
    assert "PK" not in shown and "\ufffd" not in shown
    assert not any(0x0700 <= ord(ch) < 0x0800 for ch in shown)


def test_a_damaged_version_says_so(qapp, tmp_path):
    model = tmp_path / "plant.rbds"
    model.write_bytes(b"\x00\xff not a model")
    store = HistoryStore(tmp_path / "history")
    store.record(model)
    dialog = HistoryDialog(None, model, store)
    assert "could not be read as a model" in dialog._preview.toPlainText()


def test_save_version_now_saves_unsaved_edits_first(
        qapp, tmp_path, monkeypatch):
    """The button recorded the file on disk only: right after a save (which
    already recorded it) or with unsaved edits, nothing was added."""
    from raptor.gui.ui import history_dialog as history_ui
    model = tmp_path / "plant.rbds"
    text = DC1.read_text(encoding="utf-8")
    model.write_text(text, encoding="utf-8")
    name = _first_block_name(model)
    store = HistoryStore(tmp_path / "history")
    store.record(model, reason="save")
    saved = []

    def save_current() -> bool:
        # The host saves the edited model; its save records a version.
        model.write_text(_renamed_copy(text, name, name + "_edited"),
                         encoding="utf-8")
        store.record(model, reason="save")
        saved.append(True)
        return True

    dialog = HistoryDialog(None, model, store, save_current=save_current)
    monkeypatch.setattr(history_ui.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("checkpoint", True)))
    dialog._snapshot_now()
    assert saved
    shots = store.snapshots(model)
    assert len(shots) == 2 and shots[0].label == "checkpoint"
    assert "Labelled the version" in dialog._note.text()

    monkeypatch.setattr(history_ui.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", True)))
    dialog = HistoryDialog(None, model, store, save_current=lambda: True)
    dialog._snapshot_now()
    assert len(store.snapshots(model)) == 2
    assert "has not changed since the version recorded" in dialog._note.text()
    assert dialog._list.currentRow() == 0

    dialog = HistoryDialog(None, model, store, save_current=lambda: False)
    dialog._snapshot_now()
    assert "Saving was cancelled" in dialog._note.text()


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


# -- Markdown description ------------------------------------------------
def _described_dialog(tmp_path, description: str = ""):
    model = tmp_path / "plant.rbds"
    text = DC1.read_text(encoding="utf-8")
    model.write_text(text, encoding="utf-8")
    store = HistoryStore(tmp_path / "history")
    older = store.record(model, reason="save", description=description)
    name = _first_block_name(model)
    model.write_text(_renamed_copy(text, name, name + "_v2"), encoding="utf-8")
    newer = store.record(model, reason="manual")
    dialog = HistoryDialog(None, model, store)
    return dialog, store, model, older, newer


def test_description_is_rendered_until_edit_and_saved_with_the_api(
        qapp, tmp_path):
    dialog, store, model, older, _newer = _described_dialog(
        tmp_path, "# Plan\n\nSome **bold** words\n\n- one\n- two")
    dialog._list.setCurrentRow(1)                     # the older version
    panel = dialog._desc
    assert not panel.editing and panel.snapshot_id == older.id
    shown = panel.view.toPlainText()
    assert "Plan" in shown and "bold" in shown and "**" not in shown
    assert "#" not in shown
    assert panel.edit_btn.isVisibleTo(dialog) and \
        not panel.save_btn.isVisibleTo(dialog)
    assert panel._stack.currentWidget() is panel.view

    panel.edit_btn.click()
    assert panel.editing and panel._stack.currentWidget() is panel.editor
    assert panel.editor.toPlainText().startswith("# Plan")     # raw source
    assert panel.save_btn.isVisibleTo(dialog)
    assert not panel.edit_btn.isVisibleTo(dialog)
    panel.editor.setPlainText("## Revised\n\n*text*")
    assert panel.is_dirty()
    panel.save_btn.click()
    assert not panel.editing
    assert store.snapshot(model, older.id).description == "## Revised\n\n*text*"
    assert "Revised" in panel.view.toPlainText()
    assert "¶" in dialog._list.item(1).text()
    assert "¶" not in dialog._list.item(0).text()
    assert "Revised" in dialog._list.item(1).toolTip()


def test_cancel_restores_and_empty_shows_the_placeholder(qapp, tmp_path):
    dialog, store, model, _older, newer = _described_dialog(tmp_path)
    panel = dialog._desc
    assert panel.snapshot_id == newer.id
    assert "No description" in panel.view.toPlainText()
    panel.begin_edit()
    panel.editor.setPlainText("draft")
    panel.cancel_btn.click()
    assert not panel.editing
    assert store.snapshot(model, newer.id).description == ""
    assert "No description" in panel.view.toPlainText()
    # an empty save clears
    panel.begin_edit()
    panel.editor.setPlainText("")
    assert not panel.is_dirty()
    assert panel.save()
    assert store.snapshot(model, newer.id).description == ""


def test_ctrl_enter_saves_and_escape_cancels_without_closing(qapp, tmp_path):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    dialog, store, model, _older, newer = _described_dialog(tmp_path)
    dialog.show()
    panel = dialog._desc
    panel.begin_edit()
    panel.editor.setPlainText("typed")
    qapp.sendEvent(panel.editor, QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert dialog.isVisible() and not panel.editing
    assert store.snapshot(model, newer.id).description == ""
    panel.begin_edit()
    panel.editor.setPlainText("saved by keys")
    qapp.sendEvent(panel.editor, QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier))
    assert not panel.editing and dialog.isVisible()
    assert store.snapshot(model, newer.id).description == "saved by keys"
    dialog.close()


def test_changing_selection_while_editing_asks_only_when_changed(
        qapp, tmp_path, monkeypatch):
    dialog, store, model, older, newer = _described_dialog(tmp_path)
    panel = dialog._desc
    asked = []
    monkeypatch.setattr(dialog, "_confirm_save_description",
                        lambda: asked.append(1) or True)
    panel.begin_edit()                       # unchanged: no question
    dialog._list.setCurrentRow(1)
    assert not asked and not panel.editing and panel.snapshot_id == older.id
    dialog._list.setCurrentRow(0)
    panel.begin_edit()
    panel.editor.setPlainText("kept")
    dialog._list.setCurrentRow(1)            # changed: asks, answer Save
    assert asked == [1]
    assert store.snapshot(model, newer.id).description == "kept"
    assert panel.snapshot_id == older.id and not panel.editing
    # answer Discard
    monkeypatch.setattr(dialog, "_confirm_save_description", lambda: False)
    panel.begin_edit()
    panel.editor.setPlainText("thrown away")
    dialog._list.setCurrentRow(0)
    assert store.snapshot(model, older.id).description == ""
    # closing with unsaved changes asks too
    dialog._desc.begin_edit()
    dialog._desc.editor.setPlainText("on close")
    monkeypatch.setattr(dialog, "_confirm_save_description", lambda: True)
    dialog.reject()
    assert store.snapshot(model, newer.id).description == "on close"


def test_description_view_is_inert_and_only_opens_web_links(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QUrl
    from raptor.gui.ui import history_description as hd
    dialog, *_ = _described_dialog(
        tmp_path, "![x](http://example.invalid/a.png) "
                  "[web](https://example.org) [f](file:///etc/passwd) "
                  "<script>alert(1)</script>")
    dialog._list.setCurrentRow(1)
    view = dialog._desc.view
    assert view.loadResource(2, QUrl("http://example.invalid/a.png")) is None
    assert not view.openLinks() and not view.openExternalLinks()
    opened = []
    monkeypatch.setattr(hd.QDesktopServices, "openUrl",
                        staticmethod(lambda u: opened.append(u.toString()) or True))
    assert hd.open_link(QUrl("https://example.org"))
    assert not hd.open_link(QUrl("file:///etc/passwd"))
    assert not hd.open_link(QUrl("javascript:alert(1)"))
    assert opened == ["https://example.org"]


def test_save_version_now_opens_the_description_editor(
        qapp, tmp_path, monkeypatch):
    from raptor.gui.ui import history_dialog as history_ui
    dialog, store, model, _older, newer = _described_dialog(tmp_path)
    monkeypatch.setattr(history_ui.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("named", True)))
    dialog._snapshot_now()                  # unchanged: labels the newest
    assert dialog._desc.editing and dialog._desc.snapshot_id == newer.id
    dialog._desc.editor.setPlainText("why I saved this")
    assert dialog._desc.save()
    assert store.snapshot(model, newer.id).description == "why I saved this"


def test_description_strings_are_translated_in_pt_br(qapp, tmp_path):
    from raptor.gui import i18n
    i18n.install(qapp, "pt_BR")
    try:
        dialog, *_ = _described_dialog(tmp_path)
        i18n.translate_widgets(dialog)
        panel = dialog._desc
        assert panel.edit_btn.text() != "Edit"
        assert panel.save_btn.text() != "Save"
        assert "Nenhuma descrição" in panel.view.toPlainText()
        assert "Markdown" in panel.editor.placeholderText()
        assert panel.editor.placeholderText() != \
            "Markdown supported: **bold**, *italic*, lists, links…"
        assert panel.hint.text().startswith("Markdown compat")
    finally:
        i18n.install(qapp, "en")
