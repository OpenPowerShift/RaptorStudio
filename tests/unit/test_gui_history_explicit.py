"""Versions are explicit checkpoints; the dialog's Current row is the
working state; the dialog fits its translated text."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from raptor.core.history import HistoryStore
from raptor.gui.ui.history_dialog import HistoryDialog

DC1 = Path(__file__).resolve().parents[2] / "new_docs/models/DC1.rbds"
GOLDEN_RBD = (Path(__file__).resolve().parents[2]
              / "tests/golden/TwoBlockRedundancy/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    from raptor.api import history as api_history
    store = HistoryStore(tmp_path / "history")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    return store


@pytest.fixture
def window(qapp, store, monkeypatch):
    from raptor.gui.ui import msgbox, settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    monkeypatch.setattr(msgbox, "warning", lambda *a, **k: None)
    win = MainWindow()
    yield win
    win._dirty = False
    win.close()


def test_ordinary_saves_and_opens_record_no_version(window, store, tmp_path):
    from raptor.core.fileio.model_file import load_model_file
    for name, src in (("a.rbds", DC1), ("b.rbd", GOLDEN_RBD)):
        target = tmp_path / name
        target.write_bytes(src.read_bytes())
        window._load_into_app(load_model_file(str(target)), str(target))
        assert window._write_model(str(target))
        assert window._write_model(str(target))
        window._open_path(str(target))
        assert store.snapshots(target) == [], name


def test_restore_takes_only_the_before_restore_copy(window, store, tmp_path,
                                                    monkeypatch):
    from PySide6.QtWidgets import QDialog
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui import dialogs
    source = tmp_path / "plant.rbds"
    source.write_bytes(DC1.read_bytes())
    old = store.record(source, reason="manual", label="v1")
    source.write_bytes(DC1.read_bytes() + b"\n")
    window._load_into_app(load_rbds(str(source)), str(source))

    class Fake:
        action, snapshot = "restore", old

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("raptor.gui.ui.history_dialog.HistoryDialog", Fake)
    monkeypatch.setattr(window, "_confirm_discard_unsaved",
                        lambda *a, **k: True)
    monkeypatch.setattr(dialogs, "question_box", lambda *a, **k: True)
    window._open_history_dialog()
    assert sorted(s.reason for s in store.snapshots(source)) == [
        "before restore", "manual"]


def _model_file(tmp_path, store, *, checkpoint=True):
    model = tmp_path / "plant.rbds"
    model.write_bytes(DC1.read_bytes())
    shot = (store.record(model, reason="manual", label="base")
            if checkpoint else None)
    return model, shot


def _dialog(model, store, dirty=False):
    from raptor.core.fileio.rbds import load_rbds
    return HistoryDialog(None, model, store,
                         current_model=load_rbds(str(model)))


def test_current_row_is_first_and_says_it_is_not_checkpointed(
        qapp, tmp_path):
    store = HistoryStore(tmp_path / "history")
    model, _ = _model_file(tmp_path, store, checkpoint=False)
    dialog = _dialog(model, store)
    assert dialog._list.count() == 1
    assert dialog._list.item(0).text().startswith("Current")
    assert "not checkpointed" in dialog._list.item(0).text()
    assert dialog._list.currentRow() == 0
    for button in (dialog._open_btn, dialog._restore_btn,
                   dialog._label_btn, dialog._delete_btn):
        assert not button.isEnabled()
    assert dialog._snapshot_btn.isEnabled()


def test_current_row_matches_or_differs_from_the_newest_checkpoint(
        qapp, tmp_path):
    from raptor.core.fileio.rbds import load_rbds
    store = HistoryStore(tmp_path / "history")
    model, shot = _model_file(tmp_path, store)
    dialog = _dialog(model, store)
    first = dialog._list.item(0).text()
    assert dialog._list.count() == 2
    assert first.startswith("Current — matches the version saved")
    assert shot.time_text() in first and "(base)" in first
    assert "unsaved changes" not in first
    # an edit in memory: differs, and the diff is against the checkpoint
    rbd = load_rbds(str(model))
    row = rbd.sections["BLOCK_DATA"].rows[1]
    old_name = str(row["blockName"])
    row["blockName"] = old_name + "_EDITED"
    from raptor.gui.ui.history_dialog import CurrentState
    dialog = HistoryDialog(
        None, model, store, current_model=rbd,
        current_state=lambda: CurrentState(rbd, dirty=True))
    text = dialog._list.item(0).text()
    assert "not checkpointed" in text and text.endswith("unsaved changes")
    assert dialog._list.currentRow() == 0
    shown = dialog._preview.toPlainText()
    assert "Changes from" in shown and old_name + "_EDITED" in shown
    assert dialog._stats.text().startswith("Model: ")
    assert not dialog._desc.isVisibleTo(dialog)


def test_save_version_now_turns_current_into_a_checkpoint(
        qapp, tmp_path, monkeypatch):
    from raptor.gui.ui import history_dialog as ui
    store = HistoryStore(tmp_path / "history")
    model, _ = _model_file(tmp_path, store)
    from raptor.core.fileio.rbds import load_rbds
    rbd = load_rbds(str(model))
    rbd.sections["BLOCK_DATA"].rows[1]["blockName"] = "Renamed"

    def save() -> bool:
        from raptor.core.fileio.rbds import save_rbds
        save_rbds(rbd, str(model))
        return True

    dialog = HistoryDialog(None, model, store, current_model=rbd,
                           save_current=save)
    assert "not checkpointed" in dialog._list.item(0).text()
    monkeypatch.setattr(ui.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("second", True)))
    dialog._snapshot_now()
    assert len(store.snapshots(model)) == 2
    assert dialog._list.count() == 3
    assert "matches the version saved" in dialog._list.item(0).text()
    assert dialog._selected_id() == store.snapshots(model)[0].id
    assert dialog._delete_btn.isEnabled()


def test_sizes_total_and_the_large_history_warning(qapp, tmp_path):
    store = HistoryStore(tmp_path / "history", max_bytes=100)
    model, shot = _model_file(tmp_path, store)
    dialog = _dialog(model, store)
    assert shot.size_text() in dialog._list.item(1).text()
    assert shot.size_text() in dialog._total.text()
    assert dialog._warning.isHidden() is False
    assert "Delete version" in dialog._warning.text()
    big = HistoryStore(tmp_path / "h2")
    quiet = _dialog(model, big)
    assert quiet._warning.isHidden()


def test_the_intro_describes_checkpoints_not_automatic_saves(qapp, tmp_path):
    from PySide6.QtWidgets import QLabel
    store = HistoryStore(tmp_path / "history")
    model, _ = _model_file(tmp_path, store)
    dialog = _dialog(model, store)
    text = " ".join(w.text() for w in dialog.findChildren(QLabel))
    assert "automatically" not in text and "Save version now" in text


@pytest.mark.parametrize("dark", [False, True])
def test_pt_br_buttons_fit_their_text_in_the_real_flow(qapp, tmp_path, dark):
    from raptor.gui import i18n
    from raptor.gui.resources.design_tokens import ThemeManager
    i18n.install(qapp, "pt_BR")
    ThemeManager.instance().set_dark(dark)
    try:
        store = HistoryStore(tmp_path / "history")
        model, _ = _model_file(tmp_path, store)
        dialog = _dialog(model, store)
        dialog.show()
        dialog._list.setFocus()
        for _ in range(3):
            qapp.processEvents()
        assert dialog._snapshot_btn.text() != "Save version now…"
        for button in dialog.findChildren(QPushButton):
            if not button.isVisibleTo(dialog):
                continue
            assert button.width() >= button.sizeHint().width(), (
                button.text(), button.width(), button.sizeHint().width())
        for item in range(dialog._list.count()):
            text = dialog._list.item(item).text()
            assert "before restore" not in text and "(current)" not in text
        assert "not checkpointed" not in dialog._list.item(0).text()
        assert "blocks" not in dialog._stats.text()
        dialog.close()
    finally:
        ThemeManager.instance().set_dark(False)
        i18n.install(qapp, "en")
