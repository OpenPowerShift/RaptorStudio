"""Headless tests for the Find/Zoom and Help mixins of the main window.

The pure find rules are in test_find_text.py; these check the window wiring around
them: the backing list, Enter on the box, the zoom commands, and the Help entries
(with every external effect -- browser, dialogs, the help viewer -- replaced).
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QInputDialog  # noqa: E402

from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests/golden/HierarchyExample/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), None)
    w._find_combo_refresh()
    return w


def _status(w):
    return w.statusBar().currentMessage()


# ------------------------------------------------------------------- find

def test_find_list_has_every_element_and_enables_the_box(window):
    v = window._diagram_dock.view
    entries = window._find_model.stringList()
    assert len(entries) == (len(v.blocks) + len(v.nodes) + len(v.events)
                            + len(v.hiers))
    assert window._find_edit.isEnabled()
    assert entries[0].startswith("Block  ")


def test_find_list_empty_without_a_diagram_dock(window):
    window._diagram_dock = None
    window._find_combo_refresh()
    assert window._find_model.stringList() == []
    assert not window._find_edit.isEnabled()


def test_typing_filters_the_explorer(window, monkeypatch):
    seen = []
    monkeypatch.setattr(window._explorer, "filter_items", seen.append)
    window._on_find_text_changed("pump")
    assert seen == ["pump"]


def test_submit_unknown_text_says_so(window):
    window._on_find_submit("  no such thing  ")
    assert "No element matches 'no such thing'" in _status(window)


def test_submit_block_navigates_and_clears_the_box(window, monkeypatch):
    v = window._diagram_dock.view
    name = next(iter(v.blocks.values())).name
    calls = []
    monkeypatch.setattr(v, "locate_element", lambda *a: calls.append(a))
    window._find_edit.setText(name)
    window._on_find_submit(name)
    assert calls == [("block", name)]
    assert window._find_edit.text() == ""


def test_submit_event_or_hierarchy_selects_the_item(window):
    v = window._diagram_dock.view
    if v.hiers:
        hid = next(iter(v.hiers))
        item = v._hier_items[hid]
        window._on_find_submit(f"Hier  {v.hiers[hid].name} ({hid})")
        assert item.isSelected()
    elif v.events:
        eid = next(iter(v.events))
        window._on_find_submit(f"Event  {v.events[eid].name} ({eid})")
        assert v._event_items[eid].isSelected()
    else:
        pytest.skip("fixture has neither hierarchies nor events")


# ------------------------------------------------------------------- zoom

def test_set_zoom_percent_applies_the_entered_value(window, monkeypatch):
    v = window._diagram_dock.view
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (50, True))
    window._set_zoom_percent()
    assert round(v.transform().m11() * 100) == 50
    assert v._user_zoomed is True


def test_set_zoom_percent_cancel_changes_nothing(window, monkeypatch):
    v = window._diagram_dock.view
    before = v.transform().m11()
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (500, False))
    window._set_zoom_percent()
    assert v.transform().m11() == before


def test_zoom_to_selection_messages(window):
    v = window._diagram_dock.view
    v._scene.clearSelection()
    window._zoom_to_selection()
    assert "Select one or more elements first" in _status(window)
    next(iter(v._block_items.values())).setSelected(True)
    window._zoom_to_selection()
    assert "Zoomed to 1 selected element(s)" in _status(window)


def test_zoom_to_hierarchy_without_selection_explains(window):
    window._diagram_dock.view._scene.clearSelection()
    window._zoom_to_hierarchy()
    assert "Select a hierarchy oval first" in _status(window)


def test_fit_all_delegates_to_the_view(window, monkeypatch):
    called = []
    monkeypatch.setattr(window._diagram_dock.view, "fit_all",
                        lambda: called.append(1))
    window._fit_all()
    assert called == [1]


# ------------------------------------------------------------------- help

def test_doc_candidates_nearest_first_and_include_the_model_folder(window):
    window._current_file = os.path.join(REPO, "tests", "golden", "x.rbd")
    paths = list(window._doc_candidates("A.md"))
    assert all(p.endswith("A.md") for p in paths)
    assert os.path.join(REPO, "tests", "golden", "A.md") in paths


def test_open_shortcuts_reports_a_missing_doc(window, monkeypatch):
    monkeypatch.setattr(window, "_doc_candidates", lambda name: iter(()))
    window._open_shortcuts()
    assert "KEYBOARD_SHORTCUTS.md not found" in _status(window)


def test_open_shortcuts_shows_the_first_existing_doc(window, monkeypatch, tmp_path):
    doc = tmp_path / "KEYBOARD_SHORTCUTS.md"
    doc.write_text("# Keys\n", encoding="utf-8")
    shown = []
    monkeypatch.setattr(window, "_doc_candidates",
                        lambda name: iter([str(tmp_path / "missing.md"), str(doc)]))
    monkeypatch.setattr("raptor.gui.ui.shortcuts_window.show_shortcuts",
                        lambda parent, text, prev: shown.append(text) or "win")
    window._open_shortcuts()
    assert shown == ["# Keys\n"]
    assert window._shortcuts_window == "win"


def test_open_doc_falls_back_to_the_system_browser(window, monkeypatch, tmp_path):
    from PySide6.QtGui import QDesktopServices
    from raptor.gui.ui import help_viewer
    doc = tmp_path / "manual.html"
    doc.write_text("<html></html>", encoding="utf-8")
    opened = []
    monkeypatch.setattr(help_viewer, "WEBENGINE_OK", False)
    monkeypatch.setattr(window, "_doc_candidates", lambda name: iter([str(doc)]))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda u: opened.append(u))
    window._open_doc("manual.html")
    assert len(opened) == 1 and opened[0].toLocalFile() == str(doc)
    assert "Opened manual.html" in _status(window)


def test_open_doc_missing_says_not_found(window, monkeypatch):
    from raptor.gui.ui import help_viewer
    monkeypatch.setattr(help_viewer, "WEBENGINE_OK", False)
    monkeypatch.setattr(window, "_doc_candidates", lambda name: iter(()))
    window._open_doc("nope.html")
    assert "nope.html not found" in _status(window)


def test_open_model_from_help_opens_the_path(window, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "_open_path", opened.append)
    window.open_model_from_help("/tmp/m.rbds")
    assert opened == ["/tmp/m.rbds"]


def test_report_issue_prefills_version_and_platform(window, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda u: urls.append(u))
    window._report_issue()
    text = urls[0].toString()
    assert text.startswith(window.GITHUB_REPO_URL + "/issues/new?body=")
    assert "RAPTOR Studio version" in text and "Platform" in text
    assert "issue tracker" in _status(window)


def test_about_box_names_the_licences(window, monkeypatch):
    shown = []
    monkeypatch.setattr("raptor.gui.ui.msgbox.about",
                        lambda parent, title, body: shown.append((title, body)))
    window._show_about()
    title, body = shown[0]
    assert title == "About RAPTOR Studio"
    assert "Apache License 2.0" in body and "Booz Allen" in body


def test_context_help_defaults_to_the_manual_front_page(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "_open_doc", lambda *a: calls.append(a))
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: None))
    window._open_context_help()
    assert calls[0][0] == "manual.html"
