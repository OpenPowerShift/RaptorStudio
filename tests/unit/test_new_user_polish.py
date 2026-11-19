"""New-user testing fixes (2026-11-17): what a first-time user met on the
Camping lantern example and a Blank model."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from raptor.gui.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_save_before_run():
    """A test here sets the preference to "ask"; left set, a later test in
    the same process that runs a saved, edited model meets a real modal
    and the run hangs."""
    from PySide6.QtCore import QSettings
    keys = ("files/save_before_run", "confirm/results_on_screen",
            "confirm/long_run")
    old = {key: QSettings().value(key) for key in keys}
    yield
    for key, value in old.items():
        if value is None:
            QSettings().remove(key)
        else:
            QSettings().setValue(key, value)


@pytest.fixture()
def win(qapp):
    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    window._new_file()
    yield window
    window._dirty = False
    window.close()


def test_a_never_saved_model_runs_without_the_save_prompt(win, monkeypatch):
    """No file on disk, so nothing for the results to differ from."""
    from PySide6.QtCore import QSettings
    QSettings().setValue("files/save_before_run", "ask")
    win._current_file = None
    win._dirty = True
    monkeypatch.setattr(QMessageBox, "exec", lambda _self: pytest.fail(
        "asked to save a model that has never been saved"))
    assert win._maybe_save_before_run() is True
    assert win._dirty is True


def test_a_saved_model_still_asks(win, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    QSettings().setValue("files/save_before_run", "ask")
    win._current_file = str(tmp_path / "m.rbds")
    win._dirty = True
    asked = []
    monkeypatch.setattr(MainWindow, "_confirm_discard_unsaved",
                        lambda self, **_kw: asked.append(1) or True)
    assert win._maybe_save_before_run() is True
    assert asked


def test_enter_on_the_results_guard_keeps_the_edit(win, monkeypatch):
    """The default button undid the edit the user had just typed."""
    seen = {}

    def fake_exec(box):
        seen["default"] = box.defaultButton().text()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(type(win), "_results_are_stale", lambda self: True)
    win._confirm_edit_discarding_results()
    assert seen["default"].startswith("Discard results")


def _form_labels(scroll) -> list[str]:
    from PySide6.QtWidgets import QFormLayout, QLabel
    labels = []
    for form in scroll.widget().findChildren(QFormLayout):
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is not None and isinstance(item.widget(), QLabel):
                labels.append(item.widget().text())
    return labels


@pytest.mark.parametrize(("tab", "scroll"), [
    ("failure", "_scroll_failure"), ("repair", "_scroll_repair")])
def test_stream_comes_after_the_parameters(win, tab, scroll):
    """The random-number stream sat between the type and its parameters,
    pushing the numbers people change below the fold."""
    view = win._diagram_dock.view
    block = view.blocks[next(b for b in view.blocks if b)]
    getattr(win._inspector, f"_populate_{tab}")(block)
    labels = _form_labels(getattr(win._inspector, scroll))
    stream = labels.index("Stream:")
    params = [i for i, text in enumerate(labels)
              if "(hours)" in text or "Mean" in text]
    assert params and stream > max(params), labels


@pytest.mark.parametrize(("per_run", "text"), [
    (0.02, "about once every 50 runs"),
    (0.4, "about once every 2 runs"),
    (0.7, "about 0.7 times per run"),
    (1.0, "about once per run"),
    (3.5, "about 3.5 times per run")])
def test_how_often_it_went_down_reads_naturally(per_run, text):
    """0.7 failures a run read "about once every 1 runs"."""
    from raptor.gui.ui.results import _how_often_down
    assert _how_often_down(per_run) == text


def test_clicking_a_block_brings_a_covered_inspector_forward(win, qapp):
    """The first run with findings brings the Advisor over the Inspector;
    a new user then clicked a block to change it and saw nothing happen."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win._show_advisor()
    qapp.processEvents()
    assert win._inspector_dock.visibleRegion().isEmpty()
    view = win._diagram_dock.view
    item = view._block_items[next(b for b in view._block_items if b)]
    at = view.mapFromScene(item.sceneBoundingRect().center())
    QTest.mouseMove(view.viewport(), at)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=at)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=at)
    qapp.processEvents()
    assert item.isSelected()
    assert not win._inspector_dock.visibleRegion().isEmpty()


def test_selecting_without_a_click_leaves_the_advisor_in_front(win, qapp):
    """The Advisor's "go to element" selects a block: its list stays."""
    win._show_advisor()
    qapp.processEvents()
    view = win._diagram_dock.view
    view._block_items[next(b for b in view._block_items if b)].setSelected(True)
    qapp.processEvents()
    assert win._inspector_dock.visibleRegion().isEmpty()


def _open_tab(win) -> str:
    tabs = win._inspector._tabs
    return tabs.tabText(tabs.currentIndex())


def test_a_block_opens_on_its_failure_tab(win, qapp):
    """A new user selects a block to change its life, not its name."""
    view = win._diagram_dock.view
    view._block_items[next(b for b in view._block_items if b)].setSelected(True)
    qapp.processEvents()
    win._flush_canvas_block_selection()
    assert "Failure" in _open_tab(win)


def test_a_node_still_opens_on_general(win, qapp):
    view = win._diagram_dock.view
    view._node_items[next(iter(view._node_items))].setSelected(True)
    qapp.processEvents()
    win._flush_canvas_block_selection()
    assert "General" in _open_tab(win)


def test_the_tab_the_user_picks_is_kept(win, qapp):
    view = win._diagram_dock.view
    view._block_items[next(b for b in view._block_items if b)].setSelected(True)
    qapp.processEvents()
    win._flush_canvas_block_selection()
    win._inspector._tabs.setCurrentIndex(0)          # the user clicks General
    view._scene.clearSelection()
    view._block_items[next(b for b in view._block_items if b)].setSelected(True)
    qapp.processEvents()
    win._flush_canvas_block_selection()
    assert "General" in _open_tab(win)


def test_a_new_model_runs_100_trials(win):
    """Ten trials gave too few system failures to time: the Advisor
    warned about the default on a new user's first run."""
    assert win._current_experiment()["trials"] == 100


# ---- "Don't ask again" -------------------------------------------------

def _answer(text: str, tick: bool):
    """A stand-in for QMessageBox.exec: tick the box, click `text`."""
    def fake_exec(box):
        if tick:
            box.checkBox().setChecked(True)
        next(b for b in box.buttons() if b.text().startswith(text)).click()
        return 0
    return fake_exec


def test_dont_ask_again_on_the_results_prompt(win, monkeypatch):
    from raptor.gui.ui import ask_again
    monkeypatch.setattr(type(win), "_results_are_stale", lambda self: True)
    discarded = []
    monkeypatch.setattr(type(win), "_discard_results",
                        lambda self: discarded.append(1))
    monkeypatch.setattr(QMessageBox, "exec", _answer("Discard results", True))
    win._confirm_edit_discarding_results()
    assert discarded and not ask_again.should_ask("confirm/results_on_screen")
    # the next edit discards without asking
    monkeypatch.setattr(QMessageBox, "exec", lambda _b: pytest.fail("asked again"))
    win._confirm_edit_discarding_results()
    assert len(discarded) == 2


def test_not_ticking_it_keeps_asking(win, monkeypatch):
    from raptor.gui.ui import ask_again
    monkeypatch.setattr(type(win), "_results_are_stale", lambda self: True)
    monkeypatch.setattr(type(win), "_discard_results", lambda self: None)
    monkeypatch.setattr(QMessageBox, "exec", _answer("Discard results", False))
    win._confirm_edit_discarding_results()
    assert ask_again.should_ask("confirm/results_on_screen")


@pytest.mark.parametrize(("button", "mode"), [("Run Without Saving", "never"),
                                              ("Save", "always")])
def test_the_save_prompt_remembers_the_choice(win, monkeypatch, tmp_path,
                                              button, mode):
    from PySide6.QtCore import QSettings
    QSettings().setValue("files/save_before_run", "ask")
    win._current_file = str(tmp_path / "m.rbds")
    win._dirty = True
    monkeypatch.setattr(type(win), "_save_file",
                        lambda self: setattr(self, "_dirty", False))
    monkeypatch.setattr(QMessageBox, "exec", _answer(button, True))
    assert win._maybe_save_before_run() is True
    assert QSettings().value("files/save_before_run") == mode


def test_closing_never_offers_to_remember(win, monkeypatch):
    seen = {}

    def fake_exec(box):
        seen["box"] = box.checkBox()
        next(b for b in box.buttons() if "Cancel" in b.text()).click()
        return 0
    win._dirty = True
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    win._confirm_discard_unsaved(closing=True)
    assert seen["box"] is None


def test_the_long_run_warning_can_be_switched_off(win, monkeypatch):
    from raptor.gui.ui import ask_again
    ask_again.stop_asking("confirm/long_run")
    monkeypatch.setattr(QMessageBox, "exec", lambda _b: pytest.fail("warned"))
    assert win._confirm_long_batch(10**9) is True


def test_preferences_can_bring_them_all_back():
    from raptor.gui.ui import ask_again
    ask_again.stop_asking("confirm/results_on_screen")
    ask_again.stop_asking("confirm/long_run")
    assert len(ask_again.switched_off()) == 2
    ask_again.ask_all_again()
    assert ask_again.switched_off() == []
