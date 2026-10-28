"""The ribbon's command table and layout: structural checks that need no
running simulation, and the availability rule end to end.

The failure these guard against is quiet: a typo in a handler name only
shows when someone clicks that one button; two actions on one shortcut make
Qt fire NEITHER and log "Ambiguous shortcut overload" (the F6 double-bind
found in the 09-28 review); a command left off the layout simply never
appears.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from raptor.gui.command_table import (
    BACKSTAGE, BACKSTAGE_ACTIONS, COMMANDS, LAYOUT)
from raptor.gui.commands import Btn, Custom, PREDICATES, UNMET

pytestmark = pytest.mark.gui

#: Commands built here but placed in a menu by a builder, not on a page.
OFF_RIBBON = {
    # Menu items, placed by _build_speed_controls / _build_step_menu.
    "sim.speed_slower", "sim.speed_reset_1", "sim.speed_faster",
    "sim.speed_reset_max", "sim.run_to",
    # On the quick-access bar beside the tabs (always visible), with
    # Ctrl+Z / Ctrl+Y; the Home tab that also held them was merged away.
    "home.undo", "home.redo",
}

#: Attributes of MainWindow that handlers may start from.
HANDLER_ROOTS = {"_diagram_dock", "_results"}


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_ids_and_attrs_are_unique():
    ids = [c.id for c in COMMANDS]
    attrs = [c.attr for c in COMMANDS]
    assert len(ids) == len(set(ids)), \
        [i for i in ids if ids.count(i) > 1]
    assert len(attrs) == len(set(attrs)), \
        [a for a in attrs if attrs.count(a) > 1]


def test_every_layout_button_names_a_real_command():
    known = {c.id for c in COMMANDS}
    missing = [i.id for p in LAYOUT for g in p.groups for i in g.items
               if isinstance(i, Btn) and i.id not in known]
    assert not missing, missing


def test_every_command_is_on_the_layout_or_explicitly_off_it():
    placed = {i.id for p in LAYOUT for g in p.groups for i in g.items
              if isinstance(i, Btn)}
    placed |= {cid for _title, ids in BACKSTAGE for cid in ids}
    placed |= set(BACKSTAGE_ACTIONS)
    orphans = [c.id for c in COMMANDS
               if c.id not in placed and c.id not in OFF_RIBBON]
    assert not orphans, f"built but never shown: {orphans}"


def test_the_file_view_top_level_links_are_real_commands():
    known = {c.id for c in COMMANDS}
    missing = [cid for cid in BACKSTAGE_ACTIONS if cid not in known]
    assert not missing, missing


def test_every_custom_builder_exists_on_the_window():
    from raptor.gui.ui.main_window import MainWindow
    missing = [i.builder for p in LAYOUT for g in p.groups
               for i in g.items
               if isinstance(i, Custom)
               and not callable(getattr(MainWindow, i.builder, None))]
    assert not missing, missing


def test_every_handler_resolves():
    """A typo in a handler name would otherwise surface only when someone
    clicks that button."""
    from raptor.gui.ui.main_window import MainWindow
    bad = []
    for c in COMMANDS:
        if not c.handler:
            continue
        first = c.handler.split(".")[0]
        if "." in c.handler:
            ok = first in HANDLER_ROOTS
        else:
            ok = callable(getattr(MainWindow, first, None))
        if not ok:
            bad.append((c.id, c.handler))
    assert not bad, bad


def test_every_predicate_used_is_defined_and_explained():
    used = {c.enabled_when for c in COMMANDS if c.enabled_when}
    assert used <= set(PREDICATES)
    assert used <= set(UNMET)


def test_the_layout_has_the_expected_tabs():
    # File is not a tab: the File button opens the full-page File view.
    assert [p.title for p in LAYOUT] == [
        "Model", "Arrange", "Data", "Simulate", "Debug",
        "Results", "View", "Help"]


# -- the live window ---------------------------------------------------

@pytest.fixture
def win(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1600, 900)
    w.show()
    qapp.processEvents()
    yield w
    w._dirty = False
    w.close()


def _managed(win):
    return [(c, getattr(win, c.attr)) for c in COMMANDS if c.enabled_when]


def test_no_two_actions_share_a_shortcut(win):
    """Qt fires NEITHER action when two share a key in one context."""
    seen: dict[tuple, list[str]] = {}
    for a in win.findChildren(QAction):
        # One action listing a key twice (Redo's standard key is Ctrl+Shift+Z
        # on some platforms, and Ctrl+Shift+Z is also an extra binding) is
        # harmless; only two DIFFERENT actions clash.
        for ks in {k.toString() for k in a.shortcuts() if not k.isEmpty()}:
            seen.setdefault((ks, int(a.shortcutContext().value)),
                            []).append(a.text() or a.objectName())
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dup, dup


def _available_with_no_model(c):
    """Commands whose rule holds with NO model open (Open, New, Recent follow
    "no run in progress", not "a model is open")."""
    from raptor.gui.commands import AppState
    return PREDICATES[c.enabled_when](AppState())


def test_managed_commands_start_unavailable_and_say_why(win):
    for c, action in _managed(win):
        if _available_with_no_model(c):
            continue
        assert not action.isEnabled(), c.id
        assert "Unavailable" in action.toolTip(), c.id


def test_opening_a_model_enables_them_and_removes_the_reason(win, qapp):
    from raptor.core.fileio.rbd import load_rbd
    path = "tests/golden/TwoBlockRedundancy/input.rbd"
    win._load_into_app(load_rbd(path), path)
    qapp.processEvents()
    from raptor.gui.commands import AppState
    idle = AppState(model_open=True, has_file=True, valid=True)
    for c, action in _managed(win):
        # Pause / Stop / Jump depend on a RUN and are still unavailable.
        expected = PREDICATES[c.enabled_when](idle)
        assert action.isEnabled() is expected, c.id
        assert ("Unavailable" not in action.toolTip()) is expected, c.id


def test_closing_the_model_disables_every_managed_command(win, qapp):
    """Regression: File > Close disabled 7 of ~50 by hand, leaving Print,
    Export PDF, Save As, Fit, Add Block, Copy / Cut / Paste ... enabled
    with no model to act on."""
    from raptor.core.fileio.rbd import load_rbd
    path = "tests/golden/TwoBlockRedundancy/input.rbd"
    win._load_into_app(load_rbd(path), path)
    win._dirty = False
    win._close_current_file()
    qapp.processEvents()
    still = [c.id for c, a in _managed(win)
             if a.isEnabled() and not _available_with_no_model(c)]
    assert not still, still


def test_save_works_on_a_model_never_saved(win, qapp, monkeypatch):
    """Ctrl+S on a new model or an opened example asks where to save. It
    used to be disabled until the model had a file, so it did nothing."""
    from raptor.core.fileio.rbd import load_rbd
    path = "tests/golden/TwoBlockRedundancy/input.rbd"
    win._load_into_app(load_rbd(path), None)      # a new, unsaved model
    qapp.processEvents()
    assert win._save_action.isEnabled()
    assert "choose where" in win._save_action.toolTip()
    asked = []
    monkeypatch.setattr(win, "_save_file_as", lambda: asked.append(1))
    win._save_action.trigger()
    assert asked == [1]


def test_an_unsaved_model_says_so_in_the_title(win, qapp):
    win._new_file()
    assert win.windowTitle().endswith("Untitled")
    win._mark_dirty()
    assert win.windowTitle().endswith("Untitled*")


def test_undo_and_redo_follow_the_undo_stack_not_the_registry(win):
    assert not win._undo_action.isEnabled()
    assert not win._redo_action.isEnabled()
    from PySide6.QtCore import Qt
    assert win._undo_action.shortcutContext() == \
        Qt.ShortcutContext.WindowShortcut
    assert win._save_action.shortcutContext() == \
        Qt.ShortcutContext.WindowShortcut


def test_saving_a_new_model_for_the_first_time_enables_save(win, qapp,
                                                            monkeypatch,
                                                            tmp_path):
    """Regression: Save stayed disabled after Save As on an unsaved model
    until the next load, because nothing recomputed it."""
    from PySide6.QtWidgets import QFileDialog
    from raptor.core.fileio.rbd import load_rbd
    path = "tests/golden/TwoBlockRedundancy/input.rbd"
    win._load_into_app(load_rbd(path), None)
    qapp.processEvents()

    target = str(tmp_path / "saved.rbds")
    monkeypatch.setattr(QFileDialog, "exec", lambda self: True)
    monkeypatch.setattr(QFileDialog, "selectedFiles",
                        lambda self: [target])
    win._save_file_as()
    qapp.processEvents()
    assert win._current_file == target
    assert win._save_action.isEnabled()
    assert "Unavailable" not in win._save_action.toolTip()


def test_a_limited_refresh_does_not_reenable_a_running_simulation(win,
                                                                  qapp):
    """The reason `only=` exists: Save As mid-run must not turn Run back
    on."""
    from raptor.core.fileio.rbd import load_rbd
    path = "tests/golden/TwoBlockRedundancy/input.rbd"
    win._load_into_app(load_rbd(path), path)
    win._run_action.setEnabled(False)       # what a run does
    win._commands.refresh(win._command_state(), only={"has_file"})
    assert not win._run_action.isEnabled()


def test_the_primary_build_tools_are_large_buttons(win):
    """Add Block / Node / Event / Hierarchy are the tools used most: large
    icon-over-label buttons at the ribbon's full 32 px. The two secondary
    commands stack small beside them."""
    from raptor.gui.resources.design_tokens import RIBBON
    from raptor.gui.widgets.ribbon import RibbonButton
    page = win._ribbon._pages[win._model_page_index]
    build = next(g for g in page._groups if g._label.text() == "Build")
    by_text = {b.defaultAction().text(): b
               for b in build.findChildren(RibbonButton)}
    for name in ("Add Block", "Add Node", "Add Event", "Add Hierarchy"):
        b = by_text[name]
        assert b.preferred_large, name
        assert not b.icon().isNull(), name
    assert by_text["Add Block"].iconSize().width() == RIBBON["icon_large"]
    for name in ("Collapse Selection", "Delete Selected"):
        assert not by_text[name].preferred_large, name
