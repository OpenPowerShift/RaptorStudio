"""Command availability must not re-validate the model on every click.

Reported from use: selecting blocks felt slow. `_command_state` asked for
whole-model validity every time it ran, and it runs on every selection
change -- twice per click -- by handing the model to the engine and
running the Raptor 7 input rules over every link. A click can only change
the selection, so the answer cannot change; it is now memoised per model
revision and only an edit (or a load/close) throws it away.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent.parent
MODEL = str(REPO / "new_docs" / "models" / "Lantern1.rbds")


@pytest.fixture(scope="module")
def qapp():
    """Built once per module: a second QApplication in one process kills Qt."""
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow

    w = MainWindow()
    w._load_into_app(load_rbds(MODEL), MODEL)
    w._dirty = False
    qapp.processEvents()
    yield w
    w._dirty = False          # no "unsaved changes" prompt on teardown
    w.close()


def _count_validations(win) -> list[int]:
    """Count real validations, keeping the real answer."""
    calls: list[int] = []
    real = win._validation_issues

    def spy():
        calls.append(1)
        return real()

    win._validation_issues = spy
    return calls


def test_a_selection_change_does_not_revalidate_the_model(win, monkeypatch):
    calls = _count_validations(win)
    win._validity_cache = None
    win._refresh_commands()
    first = len(calls)
    assert first >= 1, "the fix must not cache an never-computed answer"

    # The selection path is the one that made a click cost ~100 ms on a
    # 44-block model: it rebuilds the command state, which includes
    # validity, twice per click.
    view = win._diagram_dock.view
    block_id = next(iter(view._block_items))
    view._block_items[block_id].setSelected(True)
    win._refresh_commands(only={"has_selection"})
    view._scene.selectionChanged.emit()
    assert len(calls) == first, (
        f"selecting a block re-validated the whole model {len(calls) - first} "
        f"more time(s)")


def test_an_edit_throws_the_cached_validity_away(win):
    calls = _count_validations(win)
    win._validity_cache = None                 # cold, as after a load
    assert win._model_is_valid() is True
    assert len(calls) == 1
    assert win._model_is_valid() is True      # memoised
    assert len(calls) == 1

    # A breaking edit: validity must be recomputed, not remembered.
    real = win._validation_issues
    win._validation_issues = lambda: ["something is broken"]
    win._mark_dirty()
    assert win._model_is_valid() is False
    win._validation_issues = real


def test_undo_revalidates_too(win):
    """Undo goes through `model_changed` -> `_mark_dirty`, so a command
    that fixes the model re-enables Run instead of leaving it greyed."""
    win._model_is_valid()                      # warm the cache
    revision = win._model_revision
    win._set_exp_field("trials", 7)
    win._undo_stack.undo()
    assert win._model_revision > revision, (
        "an undo must invalidate the memoised validity")


def test_loading_a_model_invalidates_the_cache(win, qapp):
    from raptor.core.fileio.rbds import load_rbds

    win._model_is_valid()
    revision = win._model_revision
    win._load_into_app(load_rbds(MODEL), MODEL)
    win._dirty = False
    qapp.processEvents()
    assert win._model_revision > revision
