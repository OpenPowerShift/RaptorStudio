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
    assert "no versions yet" in dialog._list.item(0).text()
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
    assert first.startswith("Current — same as the version saved")
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
    assert "not saved as a version" in text and text.endswith("unsaved changes")
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
    assert "not saved as a version" in dialog._list.item(0).text()
    monkeypatch.setattr(ui.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("second", True)))
    dialog._snapshot_now()
    assert len(store.snapshots(model)) == 2
    assert dialog._list.count() == 3
    assert "same as the version saved" in dialog._list.item(0).text()
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
        assert "not saved as a version" not in dialog._list.item(0).text()
        assert "blocks" not in dialog._stats.text()
        dialog.close()
    finally:
        ThemeManager.instance().set_dark(False)
        i18n.install(qapp, "en")


def test_current_row_wording_with_and_without_versions(qapp, tmp_path, store):
    model, _ = _model_file(tmp_path, store, checkpoint=False)
    text = _dialog(model, store)._list.item(0).text()
    assert text == ("Current — no versions yet. Use Save version now to "
                    "keep this state.")
    store.record(model, reason="manual", label="base")
    text = _dialog(model, store)._list.item(0).text()
    assert text.startswith("Current — same as the version saved ")
    assert text.endswith("(base)")
    from raptor.core.fileio.rbds import load_rbds
    changed = load_rbds(str(model))
    sections = changed.sections["BLOCK_DATA"].rows
    name_col = "Name" if "Name" in sections[0] else next(iter(sections[0]))
    sections[0][name_col] = str(sections[0][name_col]) + "_edited"
    dialog = HistoryDialog(None, model, store, current_model=changed)
    assert "not saved as a version" in dialog._list.item(0).text()


def test_rows_show_label_first_no_reason_word_and_an_auto_tag(
        qapp, tmp_path, store):
    from raptor.gui.ui.history_dialog import _ROW_ROLE
    model, _ = _model_file(tmp_path, store, checkpoint=False)
    store.record(model, reason="manual", label="")
    model.write_bytes(model.read_bytes() + b"\n")
    store.record(model, reason="before restore")
    model.write_bytes(model.read_bytes() + b"\n")
    store.record(model, reason="manual", label="named")
    dialog = _dialog(model, store)
    texts = [dialog._list.item(i).text() for i in range(1, 4)]
    assert texts[0].startswith("named · ")
    assert texts[1].startswith("(no label) · ")
    for text in texts:
        assert "manual" not in text and "before restore" not in text
    autos = [dialog._list.item(i).data(_ROW_ROLE)["auto"]
             for i in range(1, 4)]
    assert autos == [False, True, False]


def test_seconds_appear_only_when_two_versions_share_a_minute(
        qapp, tmp_path, store):
    from raptor.gui.ui.history_dialog import _ROW_ROLE
    model, _ = _model_file(tmp_path, store, checkpoint=False)
    store.record(model, reason="manual", label="a")
    model.write_bytes(model.read_bytes() + b"\n")
    store.record(model, reason="manual", label="b")
    dialog = _dialog(model, store)
    whens = [dialog._list.item(i).data(_ROW_ROLE)["when"] for i in (1, 2)]
    # Both were taken within the same second-ish: same minute, so seconds.
    assert all(len(w) == len("2026-11-25 02:31:52") for w in whens)
    only = tmp_path / "other"
    only.mkdir()
    model2 = only / "plant.rbds"
    model2.write_bytes(DC1.read_bytes())
    store.record(model2, reason="manual", label="x")
    from raptor.core.fileio.rbds import load_rbds
    one = HistoryDialog(None, model2, store,
                        current_model=load_rbds(str(model2)))
    assert len(one._list.item(1).data(_ROW_ROLE)["when"]) == \
        len("2026-11-25 02:31")


def test_enter_closes_and_never_triggers_an_action(qapp, tmp_path, store):
    model, _ = _model_file(tmp_path, store)
    dialog = _dialog(model, store)
    for button in dialog.findChildren(QPushButton):
        if button.text() == "Close":
            assert button.isDefault()
        else:
            assert not button.isDefault() and not button.autoDefault(), \
                button.text()


def test_the_empty_list_shows_a_hint_and_the_note_is_plain_text(
        qapp, tmp_path, store):
    from PySide6.QtCore import Qt
    model, _ = _model_file(tmp_path, store, checkpoint=False)
    dialog = _dialog(model, store)
    dialog.show()
    assert not dialog._empty_hint.isHidden()
    assert dialog._note.textFormat() == Qt.TextFormat.PlainText
    store.record(model, reason="manual")
    dialog.reload()
    assert dialog._empty_hint.isHidden()


def test_an_unreadable_index_is_reported_not_rebuilt(
        qapp, tmp_path, store, monkeypatch):
    from raptor.core.history import HistoryError
    model, _ = _model_file(tmp_path, store)

    def boom(_file):
        raise HistoryError("cannot read the version history index")

    monkeypatch.setattr(store, "snapshots", boom)
    dialog = _dialog(model, store)
    assert "could not be read" in dialog._list.item(0).text()
    assert "cannot read the version history index" in \
        dialog._preview.toPlainText()


def test_reload_compares_with_the_newest_version_once(
        qapp, tmp_path, store, monkeypatch):
    from raptor.api import model_diff
    model, _ = _model_file(tmp_path, store)
    dialog = _dialog(model, store)
    calls = []
    real = model_diff.diff_models
    monkeypatch.setattr(model_diff, "diff_models",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    dialog.reload()
    assert len(calls) == 1


def test_save_version_now_command_opens_the_dialog_in_save_mode(
        window, store, tmp_path, monkeypatch):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.command_table import COMMANDS
    ids = {c.id: c for c in COMMANDS}
    assert ids["file.save_version"].shortcut == "Ctrl+Alt+S"
    shortcuts = [c.shortcut for c in COMMANDS if c.shortcut]
    assert shortcuts.count("Ctrl+Alt+S") == 1
    model = tmp_path / "plant.rbds"
    model.write_bytes(DC1.read_bytes())
    window._load_into_app(load_rbds(str(model)), str(model))
    seen: dict = {}

    class Fake:
        action, snapshot = None, None

        def __init__(self, *a, **k):
            seen.update(k)

        def exec(self):
            return 0

    monkeypatch.setattr("raptor.gui.ui.history_dialog.HistoryDialog", Fake)
    window._save_version_now()
    assert seen["start_save"] is True
    seen.clear()
    window._open_history_dialog()
    assert seen["start_save"] is False
