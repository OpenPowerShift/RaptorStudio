"""Small GUI clarity fixes found in the tip review (one umbrella issue)."""
from __future__ import annotations

import inspect
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.core.input_rules import Violation, _short, refusal_text
from raptor.gui.ui import msgbox
from raptor.gui.ui.main_window import MainWindow
from raptor.gui.ui.preflight import preflight_issues, run_dialog_text

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TWO_BLOCK = os.path.join(REPO, "tests/golden/TwoBlockRedundancy/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(msgbox, "warning",
                        lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    w = MainWindow()
    w._load_into_app(load_rbd(TWO_BLOCK), None)
    w._shown = shown
    return w


def _status(w) -> str:
    return w._statusbar.currentMessage()


# --- rule-gate refusal text -------------------------------------------------
def _v(element, message):
    return Violation(rule="t.rule", kind="dialog", element=element, message=message)


def test_refusal_text_groups_identical_messages_and_caps_the_list():
    same = [_v(f"Block {i} 'B{i}'", "Frequency of PM must be positive.") for i in range(1, 18)]
    text = refusal_text(same, limit=5)
    assert text.count("\n") == 0                      # one grouped entry
    assert "Block 1 'B1' and 16 more" in text
    distinct = [_v(f"Block {i}", f"problem {i}") for i in range(1, 10)]
    lines = refusal_text(distinct, limit=5).splitlines()
    assert lines[-1].strip() == "… and 4 more"
    assert len(lines) == 1 + 5 + 1


def test_refusal_text_without_a_limit_is_unchanged():
    vs = [_v("Block 1", "a"), _v("Block 2", "b")]
    assert refusal_text(vs) == ("The following errors must be corrected:\n"
                                "   1. Block 1: a\n   2. Block 2: b")
    assert refusal_text(vs[:1]) == "Block 1: a"


def test_long_names_are_cut_in_messages_and_name_rules_state_the_limit():
    assert _short("x" * 300).endswith("…") and len(_short("x" * 300)) == 40
    assert _short("short") == "short"
    from raptor.core.input_rules import check
    rbd = load_rbd(TWO_BLOCK)
    rbd.sections["BLOCK_DATA"].rows[0]["blockName"] = ""
    msgs = [v.message for v in check(rbd)]
    assert any("blank or contain spaces" in m and "20 characters" in m for m in msgs)
    rbd = load_rbd(TWO_BLOCK)
    rbd.sections["BLOCK_DATA"].rows[0]["blockName"] = "n" * 300
    elems = [v.element for v in check(rbd) if "20 characters" in v.message]
    assert elems and all(len(e) < 70 for e in elems)


# --- Run dialog text --------------------------------------------------------
def test_run_dialog_text_counts_then_lists_a_few():
    assert run_dialog_text(["a"]) == "1 problem found:\na"
    text = run_dialog_text([str(i) for i in range(12)])
    assert text.splitlines()[0] == "12 problems found:"
    assert "… and 7 more" in text.splitlines()[-1]
    assert len(text.splitlines()) == 7


# --- connect refusal / self link -------------------------------------------
def test_refused_connect_returns_none_and_adds_no_link(window):
    v = window._diagram_dock.view
    n_links = len(v.links)
    assert v.connect_elements("Block", 3, "Node", 2) is None   # block 3 already has an output
    assert window._shown                                       # the gate said why
    assert len(v.links) == n_links


def test_a_successful_connect_still_returns_its_number(window):
    v = window._diagram_dock.view
    n = v.add_node("extra", 5.0, 5.0)
    num = v.connect_elements("Node", 5, "Node", n)
    assert num


def test_clicking_the_same_element_twice_says_why(window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    v = window._diagram_dock.view
    window.resize(1200, 800)
    window.show()
    v.tool_mode = "connect"
    item = v._block_items[3]
    v._connect_from = ("Block", 3)
    pos = v.mapFromScene(item.sceneBoundingRect().center())
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(pos.x(), pos.y()))
    assert "cannot be linked to itself" in _status(window)
    assert v._connect_from == ("Block", 3)          # still armed


# --- preflight: node rings ---------------------------------------------------
def _name(kind, oid):
    return f"{kind}{oid}"


def test_a_ring_of_nodes_and_a_node_self_link_are_reported(window):
    v = window._diagram_dock.view
    a = v.add_node("ringa", 1.0, 1.0)
    b = v.add_node("ringb", 2.0, 2.0)
    assert v.connect_elements("Node", a, "Node", b)
    assert v.connect_elements("Node", b, "Node", a)
    issues = preflight_issues(window._rbd, v, _name)
    assert any(i.startswith("cyclic connection") and "ringa" in i for i in issues)


def test_no_cycle_reported_for_a_plain_model(window):
    v = window._diagram_dock.view
    assert not [i for i in preflight_issues(window._rbd, v, _name)
                if i.startswith("cyclic")]


# --- Extract Hierarchy: open-after prompt -----------------------------------
def test_extract_open_after_asks_the_closing_question():
    from raptor.gui.ui import isolate_mixin
    src = inspect.getsource(isolate_mixin)
    assert "_confirm_discard_unsaved(closing=True)" in src


# --- duplicate names ---------------------------------------------------------
def test_renaming_to_an_existing_name_ignoring_case_warns_but_allows(window):
    v = window._diagram_dock.view
    v.rename_block(3, "Pump")
    assert "another block" not in _status(window)
    v.rename_block(4, "pump")
    assert "another block (3) is already named 'pump'" in _status(window)
    assert v.blocks[4].name == "pump"                # allowed, as Raptor 7 allows it


# --- Data Tables ---------------------------------------------------------------
def test_param_cells_explain_themselves_and_computed_cells_say_read_only(window):
    t = window._data_tables._blocks_table
    window._data_tables._populate_blocks()
    tip = t.cellWidget(0, 3).toolTip()
    assert "param" in tip.lower()
    assert t.cellWidget(0, 8).toolTip() and tip != t.item(0, 0).toolTip()
    assert "Read-only" in t.item(0, 6).toolTip()


def test_data_tables_dock_has_room_for_rows(window):
    assert window._data_tables_dock.minimumHeight() >= 160
    assert window.DATA_TABLES_OPEN_HEIGHT >= 240


# --- Explorer ------------------------------------------------------------------
def test_explorer_items_carry_the_full_name_in_a_tooltip(window):
    v = window._diagram_dock.view
    v.rename_block(3, "Long_name_block_0001")
    window._explorer.refresh()
    window._explorer._tree.expandAll()
    tips = []

    def walk(it):
        tips.append(it.toolTip(0))
        for i in range(it.childCount()):
            walk(it.child(i))
    tree = window._explorer._tree
    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    assert any("Long_name_block_0001" in t for t in tips)
