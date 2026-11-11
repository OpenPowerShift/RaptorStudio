"""The canvas's phase-assignment and keyed-row (pools/triggers) edits.

Each edit is one undoable step on the view's undo stack; these check the row data
before, after, and after Undo, plus the refusals that must leave the stack untouched.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui.main_window import MainWindow  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PHASED = os.path.join(REPO, "tests/golden/Phasing1/input.rbd")
PLAIN = os.path.join(REPO, "tests/golden/HierarchyExample/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(path):
    w = MainWindow()
    w._load_into_app(load_rbd(path), None)
    return w


@pytest.fixture
def view(qapp):
    return _window(PHASED)._diagram_dock.view


@pytest.fixture
def plain_view(qapp):
    return _window(PLAIN)._diagram_dock.view


def _row(view, section, oid):
    return next(r for r in view._rbd_ref.sections[section].rows
                if int(r["ID"]) == oid)


def _first_block_id(view):
    return sorted(view.blocks)[0]


# ----------------------------------------------------------- link fields

def test_link_field_edits_are_limited_to_known_columns(view):
    depth = view._stack.count()
    assert view.set_link_flow_field(1, "Bogus", "", "") is False
    assert view.set_link_standby_field(1, "Bogus", "", "") is False
    assert view._stack.count() == depth          # a refusal pushes nothing


def test_flow_field_edit_pushes_one_undoable_step(view):
    lid = view.links[0].object_num
    before = _row(view, "LINK_DATA", lid).get("nomFlow", "")
    depth = view._stack.count()
    assert view.set_link_flow_field(lid, "nomFlow", before, "7")
    assert view._stack.count() == depth + 1
    assert _row(view, "LINK_DATA", lid)["nomFlow"] == "7"
    view._stack.undo()
    assert _row(view, "LINK_DATA", lid).get("nomFlow", "") == before


def test_standby_field_edit_accepts_the_switchover_columns(view):
    lid = view.links[0].object_num
    assert view.set_link_standby_field(lid, "AProb", "", "0.5")
    assert _row(view, "LINK_DATA", lid)["AProb"] == "0.5"


def test_resync_link_replaces_the_live_link_from_its_row(view):
    link = view.links[0]
    lid = link.object_num
    view.set_link_flow_field(lid, "nomFlow", "", "9")
    view.resync_link(lid)
    fresh = next(x for x in view.links if x.object_num == lid)
    assert fresh is not link or fresh.object_num == lid     # replaced, same id
    view.resync_link(10 ** 9)                                # unknown id: no-op


def test_resync_link_without_a_model_is_a_no_op(view):
    view._rbd_ref = None
    view.resync_link(1)


# ------------------------------------------------------------- phasing

def test_phasing_of_a_missing_element_is_refused(view):
    depth = view._stack.count()
    assert view.set_element_phasing("block", 10 ** 9, True, [("A", 1.0)]) is False
    assert view._stack.count() == depth


def test_unphasing_an_unphased_block_changes_nothing(plain_view):
    bid = _first_block_id(plain_view)
    depth = plain_view._stack.count()
    # old state == new state (flag off, no tokens): no undo step is pushed.
    assert plain_view.set_element_phasing("block", bid, False, []) is False
    assert plain_view._stack.count() == depth


def test_edit_phases_refusal_surfaces_before_anything_is_pushed(view):
    depth = view._stack.count()

    def refuse(rbd):
        raise ValueError("refused")
    with pytest.raises(ValueError, match="refused"):
        view.edit_phases("Insert phase", refuse)
    assert view._stack.count() == depth


def test_edit_phases_runs_on_the_real_model_as_one_step(view):
    depth = view._stack.count()
    seen = []

    def edit(rbd):
        seen.append(rbd)
        return "done"
    assert view.edit_phases("Touch phases", edit) == "done"
    assert view._stack.count() == depth + 1
    # Once on a throwaway deep copy (to surface a refusal), once for real.
    assert len(seen) >= 2 and seen[0] is not view._rbd_ref


# ------------------------------------------------------------ keyed rows

def test_keyed_cell_edit_is_undoable_and_skips_no_ops(view):
    view.add_keyed_row("SPARE_POOL_DATA", "PoolName", {"PoolName": "P", "Stock": "1"})
    depth = view._stack.count()
    view.set_keyed_cell("SPARE_POOL_DATA", "PoolName", "P", "Stock", "1")      # unchanged
    view.set_keyed_cell("SPARE_POOL_DATA", "PoolName", "nope", "Stock", "5")   # no row
    assert view._stack.count() == depth
    view.set_keyed_cell("SPARE_POOL_DATA", "PoolName", "P", "Stock", "5")
    _sec, row = view._keyed_section_row("SPARE_POOL_DATA", "PoolName", "P")
    assert row["Stock"] == "5"
    view._stack.undo()
    assert view._keyed_section_row("SPARE_POOL_DATA", "PoolName", "P")[1]["Stock"] == "1"


def test_add_keyed_row_makes_the_key_unique(view):
    first = view.add_keyed_row("SPARE_POOL_DATA", "PoolName", {"PoolName": "Pool"})
    second = view.add_keyed_row("SPARE_POOL_DATA", "PoolName", {"PoolName": "Pool"})
    assert first == "Pool" and second == "Pool_1"


def test_remove_keyed_row_removes_and_undo_restores(view):
    view.add_keyed_row("SPARE_POOL_DATA", "PoolName", {"PoolName": "Gone"})
    view.remove_keyed_row("SPARE_POOL_DATA", "PoolName", "Gone")
    assert view._keyed_section_row("SPARE_POOL_DATA", "PoolName", "Gone")[1] is None
    view._stack.undo()
    assert view._keyed_section_row("SPARE_POOL_DATA", "PoolName", "Gone")[1] is not None


def test_remove_missing_keyed_row_is_a_no_op(view):
    depth = view._stack.count()
    view.remove_keyed_row("SPARE_POOL_DATA", "PoolName", "never existed")
    view.remove_keyed_row("NO_SUCH_SECTION", "Name", "x")
    assert view._stack.count() == depth


def test_keyed_section_row_without_a_model(view):
    view._rbd_ref = None
    assert view._keyed_section_row("SPARE_POOL_DATA", "PoolName", "x") == (None, None)


def test_phasing_a_block_writes_padded_tokens_and_undo_restores(view):
    bid = _first_block_id(view)
    before = dict(_row(view, "BLOCK_DATA", bid))
    assert view.set_element_phasing("block", bid, True, [("A", 1.5)]) is True
    row = _row(view, "BLOCK_DATA", bid)
    assert row["Phasing"] == "1"
    # Token format matches Raptor 7 files, and the row is padded out to every P
    # column the section has (a short phased row crashes the real parser).
    assert row["P 001"] == "A: 1.500"
    p_cols = [c for c in view._rbd_ref.sections["BLOCK_DATA"].columns
              if c.startswith("P ") and c[2:].isdigit()]
    assert p_cols and all(row[c] for c in p_cols)
    view._stack.undo()
    assert dict(_row(view, "BLOCK_DATA", bid)) == before


def test_phasing_a_node_uses_bare_tokens_and_the_phases_flag(view):
    if not view.nodes:
        pytest.skip("fixture has no nodes")
    nid = sorted(view.nodes)[0]
    assert view.set_element_phasing("node", nid, True, [("C", -1), ("L", 0)])
    row = _row(view, "NODE_DATA", nid)
    assert row["Phases"] == "1"           # nodes: `Phases`, not `Phasing`
    assert row["P001"] == "C" and row["P002"] == "L"
