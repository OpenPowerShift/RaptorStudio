"""One window per model (a11f2db4): the registry, where Open / New / the
examples put a model, and crash recovery per window.

Also the per-canvas grid-snap step (f5bec51d), which is what lets two
windows' canvases snap independently.
"""
from __future__ import annotations

import gc
import os
import shutil
from pathlib import Path
from types import MethodType

import pytest
from PySide6.QtWidgets import QApplication, QGraphicsRectItem, QGraphicsScene

from raptor.gui import open_models

pytestmark = pytest.mark.gui

A = "tests/golden/TwoBlockRedundancy/input.rbd"
B = "new_docs/models/WaterTreatment.rbds"


class Fake:
    """Anything with `_rbd`, `_current_file`, `_dirty`, `windowTitle()`."""

    def __init__(self, path=None, dirty=False, rbd=True, title="t"):
        self._rbd = object() if rbd else None
        self._current_file = path
        self._dirty = dirty
        self._title = title
        self.copies = []

    def windowTitle(self):
        return self._title

    def _model_for_engine(self, copy=False):
        self.copies.append(copy)
        return ("model", self._current_file)


@pytest.fixture
def registry(monkeypatch):
    """A registry of its own, so windows other tests left behind (or the
    ones this test builds) are the only ones in it."""
    monkeypatch.setattr(open_models, "_WINDOWS", [])
    monkeypatch.setattr(open_models, "_KEPT", [])
    monkeypatch.setattr(open_models, "_SERIAL", [0])
    yield
    for w in list(open_models.windows()):
        if hasattr(w, "close"):
            w._dirty = False
            w.close()


# -- the registry ----------------------------------------------------------

def test_register_numbers_windows_and_lists_them_oldest_first(registry):
    a, b = Fake(), Fake()
    assert open_models.register(a) == 1
    assert open_models.register(b) == 2
    assert open_models.windows() == [a, b]
    open_models.register(a)                       # again: not listed twice
    assert open_models.windows() == [a, b]


def test_unregister_removes_from_both_lists(registry):
    a, b = Fake(), Fake()
    open_models.register(a)
    open_models.register(b)
    open_models.keep(b)
    open_models.keep(b)                           # held once
    assert list(open_models._KEPT) == [b]
    open_models.unregister(b)
    assert open_models.windows() == [a]
    assert open_models._KEPT == []
    open_models.unregister(Fake())                # unknown: no error


def test_the_registry_holds_windows_weakly_but_keep_holds_strongly(registry):
    plain, kept = Fake(), Fake()
    open_models.register(plain)
    open_models.register(kept)
    open_models.keep(kept)
    del plain, kept
    gc.collect()
    left = open_models.windows()
    assert len(left) == 1 and left[0] in open_models._KEPT


def test_window_for_file_matches_the_same_file_by_path(registry, tmp_path):
    f = tmp_path / "m.rbds"
    f.write_text("x")
    a, b = Fake(), Fake(path=str(f))
    open_models.register(a)
    open_models.register(b)
    assert open_models.window_for_file(str(f)) is b
    # a different spelling of the same path
    assert open_models.window_for_file(
        str(tmp_path / "sub" / ".." / "m.rbds")) is b
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert open_models.window_for_file("m.rbds") is b
    finally:
        os.chdir(cwd)
    assert open_models.window_for_file(str(tmp_path / "other.rbds")) is None
    assert open_models.window_for_file(None) is None
    assert open_models.window_for_file("") is None


def test_others_skips_self_and_empty_windows_and_describes_each(registry,
                                                                tmp_path):
    me = Fake(path="/x/Mine.rbds")
    saved = Fake(path=str(tmp_path / "Saved.rbds"))
    edited = Fake(path=str(tmp_path / "Edited.rbds"), dirty=True)
    empty = Fake(rbd=False)
    untitled = Fake(dirty=True)
    for w in (me, saved, empty, edited, untitled):
        open_models.register(w)
    got = open_models.others(me)
    assert [o.window for o in got] == [saved, edited, untitled]
    assert [o.label for o in got] == ["Saved.rbds", "Edited.rbds",
                                      "Untitled (window 5)"]
    assert [o.path for o in got] == [saved._current_file,
                                     edited._current_file, None]
    assert [o.dirty for o in got] == [False, True, True]
    assert got[0].description == "Saved.rbds — open"
    assert got[1].description == "Edited.rbds — open, with unsaved changes"
    assert got[2].description == "Untitled (window 5) — open, unsaved"
    assert open_models.others(Fake()) != []       # a stranger sees all four


def test_open_model_model_is_a_copy_as_the_window_shows_it(registry):
    w = Fake(path="/x/a.rbds", dirty=True)
    open_models.register(w)
    om = open_models.others(Fake())[0]
    assert om.model() == ("model", "/x/a.rbds")
    assert w.copies == [True]


# -- per-canvas grid snap (f5bec51d) -----------------------------------------

def test_two_canvases_keep_separate_snap_states():
    from raptor.gui.ui.diagram import DiagramView
    from raptor.gui.ui.diagram_items import _snap_of
    QApplication.instance() or QApplication([])
    v1, v2 = DiagramView(), DiagramView()
    assert v1._snap_state is not v2._snap_state
    assert v1._scene._snap_state is v1._snap_state
    v1._snap_state["x"] = 10.0
    v2._snap_state["x"] = 25.0
    assert v1._snap_state["x"] == 10.0 and v2._snap_state["x"] == 25.0
    r1, r2 = QGraphicsRectItem(), QGraphicsRectItem()
    v1._scene.addItem(r1)
    v2._scene.addItem(r2)
    assert _snap_of(r1) is v1._snap_state
    assert _snap_of(r2) is v2._snap_state


def test_items_snap_to_their_own_canvas_step():
    from raptor.gui.ui import diagram as diag
    from raptor.gui.ui.diagram import DiagramView
    QApplication.instance() or QApplication([])
    v1, v2 = DiagramView(), DiagramView()
    v1._snap_state["x"] = v1._snap_state["y"] = 10.0
    v2._snap_state["x"] = v2._snap_state["y"] = 25.0
    i1 = diag._EventItem(1, "E1", 0.0, 0.0)
    i2 = diag._EventItem(1, "E1", 0.0, 0.0)
    v1._scene.addItem(i1)
    v2._scene.addItem(i2)
    i1.setPos(23.0, 47.0)
    i2.setPos(23.0, 47.0)
    assert (round(i1.pos().x()), round(i1.pos().y())) == (20, 50)
    assert (round(i2.pos().x()), round(i2.pos().y())) == (25, 50)


def test_snap_of_falls_back_to_the_module_default_on_a_bare_scene():
    from raptor.gui.ui.diagram_common import _SNAP_STATE
    from raptor.gui.ui.diagram_items import _snap_of
    QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    item = QGraphicsRectItem()
    assert _snap_of(item) is _SNAP_STATE          # on no scene at all
    scene.addItem(item)
    assert _snap_of(item) is _SNAP_STATE
    assert _snap_of(None) is _SNAP_STATE


def test_toggling_snap_on_one_canvas_leaves_the_other(registry):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.diagram import DiagramView
    QApplication.instance() or QApplication([])
    v1, v2 = DiagramView(), DiagramView()
    for v in (v1, v2):
        v.load_rbd(load_rbd(A))
    assert v1._snap_state["x"] > 0 and v2._snap_state["x"] > 0
    v1.set_snap_enabled(False)
    assert v1._snap_state == {"x": 0.0, "y": 0.0}
    assert v2._snap_state["x"] > 0 and v2._snap_state["y"] > 0
    v1.set_snap_enabled(True)
    assert v1._snap_state["x"] == pytest.approx(v2._snap_state["x"])


# -- routing: which window a model goes to ---------------------------------

@pytest.fixture
def win(registry):
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    return MainWindow()


def test_registering_gives_each_window_a_serial_and_close_unregisters(win):
    assert win._window_serial == 1
    assert open_models.windows() == [win]
    second = win._spawn_window()
    assert second._window_serial == 2
    assert open_models.windows() == [win, second]
    assert second in open_models._KEPT and win not in open_models._KEPT
    second.close()
    assert open_models.windows() == [win]
    assert second not in open_models._KEPT


def test_an_empty_window_is_reused(win):
    assert win._rbd is None
    assert win._window_for_open() is win
    assert win._window_for_open(A) is win
    assert len(open_models.windows()) == 1


def test_an_untouched_unsaved_model_is_reused(win):
    from raptor.core.fileio.rbd import load_rbd
    win._load_into_app(load_rbd(A), None)         # a fresh New / an example
    win._dirty = False
    assert win._window_for_open() is win
    assert len(open_models.windows()) == 1


def test_a_window_with_a_file_gets_a_new_window_for_the_next_model(win):
    win._open_path(A)
    assert win._current_file == A
    target = win._window_for_open()
    assert target is not win
    assert target._rbd is None and target in open_models.windows()
    assert win._current_file == A                  # untouched


def test_a_window_with_unsaved_edits_gets_a_new_window(win):
    from raptor.core.fileio.rbd import load_rbd
    win._load_into_app(load_rbd(A), None)
    win._dirty = True
    assert win._window_for_open() is not win


def test_open_path_in_a_busy_window_opens_a_second_window(win):
    win._open_path(A)
    win._open_path(B)
    ws = open_models.windows()
    assert len(ws) == 2
    assert win._current_file == A
    other = next(w for w in ws if w is not win)
    assert other._current_file == B and other._rbd is not None
    # the second window is now the busy one: a third model gets a third
    other._open_path("tests/golden/HierarchyExample/input.rbd")
    assert len(open_models.windows()) == 3


def test_a_file_already_open_brings_its_window_forward_and_opens_nothing(win):
    win._open_path(A)
    other = win._spawn_window()
    shown = []
    win.raise_ = lambda: shown.append("raise")
    assert other._window_for_open(A) is None
    assert shown == ["raise"]
    assert "is open in this window" in win._statusbar.currentMessage()
    other._open_path(A)
    assert len(open_models.windows()) == 2
    assert other._rbd is None, "the empty window was not used either"
    assert win._current_file == A


def test_open_in_this_window_keeps_one_window(win):
    from raptor.gui.ui import settings
    settings.set_value("files/open_in", "this")
    win._open_path(A)
    assert win._window_for_open() is win
    win._open_path(B)
    assert len(open_models.windows()) == 1
    assert win._current_file == B


def test_new_in_a_busy_window_opens_an_untitled_model_in_another(win):
    win._open_path(A)
    win._new_file()
    ws = open_models.windows()
    assert len(ws) == 2
    other = next(w for w in ws if w is not win)
    assert win._current_file == A
    assert other._rbd is not None and other._current_file is None


def test_new_in_an_empty_window_stays_there(win):
    win._new_file()
    assert len(open_models.windows()) == 1
    assert win._rbd is not None and win._current_file is None


def test_an_example_opens_in_a_new_window_when_this_one_is_busy(win):
    win._open_path(A)
    win._open_example("WaterTreatment.rbds")
    ws = open_models.windows()
    assert len(ws) == 2
    other = next(w for w in ws if w is not win)
    assert other._rbd is not None and other._current_file is None
    assert win._current_file == A


def test_an_example_reuses_an_empty_window(win):
    win._open_example("WaterTreatment.rbds")
    assert len(open_models.windows()) == 1
    assert win._rbd is not None and win._current_file is None


# -- crash recovery, one copy per window ----------------------------------

class _Stub:
    def __init__(self, serial=None):
        if serial is not None:
            self._window_serial = serial


def test_recovery_path_differs_by_window(original_recovery_path, monkeypatch,
                                         tmp_path):
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    names = {n: Path(original_recovery_path(_Stub(n))).name
             for n in (1, 2, 3)}
    assert names == {1: "unsaved_recovery.rbds",
                     2: "unsaved_recovery-2.rbds",
                     3: "unsaved_recovery-3.rbds"}
    assert Path(original_recovery_path(_Stub())).name == \
        "unsaved_recovery.rbds", "no serial reads as the first window"
    assert Path(original_recovery_path(_Stub(0))).name == \
        "unsaved_recovery.rbds"
    p = Path(original_recovery_path(_Stub(2)))
    assert p.parent == tmp_path / "raptor_studio_recovery"


def _use_real_recovery_paths(monkeypatch, original, w):
    monkeypatch.setattr(w, "_recovery_path", MethodType(original, w))


def test_closing_one_window_leaves_the_others_recovery_copy(
        win, original_recovery_path, monkeypatch, tmp_path):
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    second = win._spawn_window()
    for w in (win, second):
        _use_real_recovery_paths(monkeypatch, original_recovery_path, w)
    d = tmp_path / "raptor_studio_recovery"
    d.mkdir()
    for w in (win, second):
        Path(w._recovery_path()).write_text("x")
    assert win._recovery_path() != second._recovery_path()
    second._clear_recovery()
    assert Path(win._recovery_path()).exists()
    assert not Path(second._recovery_path()).exists()


@pytest.fixture
def recovery_dir(tmp_path):
    d = tmp_path / "raptor_studio_recovery"
    d.mkdir()
    shutil.copy(B, d / "unsaved_recovery.rbds")
    shutil.copy("new_docs/models/Bakery1.rbds", d / "unsaved_recovery-2.rbds")
    return d


def _ask(monkeypatch, answer=True):
    asked = []

    def question_box(parent, title, text, **kw):
        asked.append((title, text, kw))
        return answer
    import raptor.gui.ui.dialogs as dialogs
    monkeypatch.setattr(dialogs, "question_box", question_box)
    return asked


def test_the_first_window_offers_every_copy_and_opens_each_in_its_own(
        win, recovery_dir, monkeypatch):
    monkeypatch.setattr(win, "_recovery_path",
                        lambda: str(recovery_dir / "unsaved_recovery.rbds"))
    asked = _ask(monkeypatch, True)
    win._check_recovery()
    assert len(asked) == 1
    title, text, kw = asked[0]
    assert title == "Recovery Copy Found"
    assert text.startswith("2 auto-saved recovery copies")
    assert str(recovery_dir / "unsaved_recovery-2.rbds") in text
    assert kw["yes_text"] == "Open recovery copies"
    ws = open_models.windows()
    assert len(ws) == 2
    assert all(w._rbd is not None and w._current_file is None for w in ws)
    assert win._rbd is not None, "the first window takes the first copy"


def test_one_copy_reads_in_the_singular(win, recovery_dir, monkeypatch):
    (recovery_dir / "unsaved_recovery-2.rbds").unlink()
    monkeypatch.setattr(win, "_recovery_path",
                        lambda: str(recovery_dir / "unsaved_recovery.rbds"))
    asked = _ask(monkeypatch, True)
    win._check_recovery()
    assert asked[0][1].startswith("An auto-saved recovery copy")
    assert asked[0][2]["yes_text"] == "Open recovery copy"
    assert len(open_models.windows()) == 1 and win._rbd is not None


def test_declining_opens_nothing(win, recovery_dir, monkeypatch):
    monkeypatch.setattr(win, "_recovery_path",
                        lambda: str(recovery_dir / "unsaved_recovery.rbds"))
    asked = _ask(monkeypatch, False)
    win._check_recovery()
    assert len(asked) == 1
    assert win._rbd is None and len(open_models.windows()) == 1


def test_only_the_first_empty_window_asks(win, recovery_dir, monkeypatch):
    asked = _ask(monkeypatch, True)
    second = win._spawn_window()
    for w in (win, second):
        monkeypatch.setattr(w, "_recovery_path", lambda: str(
            recovery_dir / "unsaved_recovery.rbds"))
    second._check_recovery()                       # serial 2: never asks
    assert asked == []
    from raptor.core.fileio.rbd import load_rbd
    win._load_into_app(load_rbd(A), None)
    win._check_recovery()                          # already holds a model
    assert asked == []


def test_nothing_to_recover_asks_nothing(win, tmp_path, monkeypatch):
    monkeypatch.setattr(win, "_recovery_path",
                        lambda: str(tmp_path / "empty" / "unsaved_recovery.rbds"))
    asked = _ask(monkeypatch, True)
    win._check_recovery()
    assert asked == []
