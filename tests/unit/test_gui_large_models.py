"""Interaction hot spots on a large reliability diagram.

A drag used to cost O(level) per settle, per release and per undo: every
item and every file row was walked to record one coordinate, and every
link in the diagram was re-routed (about 0.7 ms each -- seconds on a
large level). These tests pin the selective paths, their equivalence with
the full ones, and a generous stopwatch budget on a 5,000-element level
(10,000 in the slow tier).
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.diagram_commands import MoveCmd
from raptor.gui.ui.diagram_items import _LinkItem

#: The default-tier synthetic level. Big enough that an O(n) walk shows up
#: against the stopwatch, small enough to keep the suite quick.
N_LARGE = 5_000


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _model(n=N_LARGE, step=1):
    """A chain level. `step` keeps the total element count under the
    spec limit (20,000) for the 10,000-block slow case."""
    blocks = [{"name": f"B{i}", "id": 10 + i} for i in range(n)]
    nodes = [{"id": 1, "name": "Start", "type": "start"},
             {"id": 2, "name": "End", "type": "end"}]
    links = [{"from": "Start", "to": "B0"}]
    links += [{"from": f"B{i}", "to": f"B{i + step}"}
              for i in range(0, n - step, step)]
    links += [{"from": f"B{n - 1}", "to": "End"}]
    return build_from_spec({
        "name": "Large", "blocks": blocks, "nodes": nodes, "links": links,
        "system": {"term_condition": 100, "num_runs": 1}},
        auto_layout=False)


@pytest.fixture(scope="module")
def large_view(qapp):
    """One 5,000-element level shared by the timing tests. Each test moves
    a different element, so they do not depend on each other's order."""
    view = DiagramView()
    view.load_rbd(_model())
    return view


@pytest.fixture
def small_view(qapp):
    """A fresh, small level for the correctness tests."""
    view = DiagramView()
    view.load_rbd(_model(120))
    return view


def _row(view, section, oid):
    rows = view._rbd_ref.sections[section].rows
    return next(r for r in rows if int(float(r.get("ID", "-1"))) == oid)


# -- the moved-element paths are selective ----------------------------------

def test_a_single_drag_syncs_only_the_moved_element(large_view, monkeypatch):
    view = large_view
    view._pos_overrides.clear()         # shared view: start from no moves
    bid = next(iter(view._block_items))
    item = view._block_items[bid]
    seen = []
    original = view._write_positions_to_rows

    def capture(only=None):
        seen.append(only)
        original(only)

    monkeypatch.setattr(view, "_write_positions_to_rows", capture)
    item.setPos(item.pos().x() + 20.0, item.pos().y())
    view.flush_pending_moves()
    assert seen and seen[-1] == {("block", bid)}
    assert set(view._pos_overrides) == {("block", bid)}


def test_a_multi_select_drag_records_each_moved_element(large_view):
    view = large_view
    view._pos_overrides.clear()
    bids = list(view._block_items)[:3]
    for bid in bids:
        item = view._block_items[bid]
        item.setPos(item.pos().x() + 20.0, item.pos().y() + 10.0)
    view.flush_pending_moves()
    assert set(view._pos_overrides) == {("block", bid) for bid in bids}


def test_a_single_drag_is_not_proportional_to_level_size(large_view):
    view = large_view
    item = next(iter(view._block_items.values()))
    started = time.perf_counter()
    item.setPos(item.pos().x() + 20.0, item.pos().y())
    view.flush_pending_moves()
    elapsed = time.perf_counter() - started
    # Generous: guards against a return to walking every row/item per drag.
    assert elapsed < 1.0, f"one-block settle took {elapsed:.2f}s"


def test_a_drag_release_routes_a_handful_of_links(large_view, monkeypatch):
    """MoveCmd (drag release, its undo and redo) re-routed every link.
    `links_affected_by_moving` is exact and was already trusted for the
    drag steps; the release now uses it too."""
    view = large_view
    assert len(view._links) > 1_000
    calls = []
    real = _LinkItem._update_route
    monkeypatch.setattr(_LinkItem, "_update_route",
                        lambda self: (calls.append(self), real(self))[1])
    bid = sorted(view._block_items)[5]
    old = view._block_items[bid].scenePos()
    view._stack.push(MoveCmd(view, [("block", bid, (old.x(), old.y()),
                                     (old.x() + 20.0, old.y()))]))
    assert 0 < len(set(map(id, calls))) < 40, len(calls)


def test_a_drag_release_is_not_proportional_to_level_size(large_view):
    view = large_view
    bid = sorted(view._block_items)[7]
    item = view._block_items[bid]
    old = item.scenePos()
    started = time.perf_counter()
    view._stack.push(MoveCmd(view, [("block", bid, (old.x(), old.y()),
                                     (old.x() + 20.0, old.y()))]))
    elapsed = time.perf_counter() - started
    # Generous: on a 10,000-element level the old all-rows/all-links path
    # took seconds; the selective one is milliseconds.
    assert elapsed < 1.0, f"one-block release took {elapsed:.2f}s"


# -- partial sync correctness ------------------------------------------------

def test_partial_sync_writes_the_moved_row_and_leaves_the_rest(
        small_view, monkeypatch):
    view = small_view
    moved, untouched = sorted(view._block_items)[:2]
    untouched_before = dict(_row(view, "BLOCK_DATA", untouched))
    item = view._block_items[moved]
    item.setPos(item.pos().x() + 40.0, item.pos().y())

    captured = []
    original = view._write_positions_to_rows
    monkeypatch.setattr(view, "_write_positions_to_rows",
                        lambda only=None: (captured.append(only),
                                           original(only))[1])
    view.flush_pending_moves()

    assert captured == [{("block", moved)}]
    mx, my = view._pos_overrides[("block", moved)]
    row = _row(view, "BLOCK_DATA", moved)
    assert float(row["xPos"]) == pytest.approx(mx, abs=1e-2)
    assert float(row["yPos"]) == pytest.approx(my, abs=1e-2)
    assert _row(view, "BLOCK_DATA", untouched) == untouched_before


def test_a_partial_sync_of_an_unknown_id_is_a_no_op(small_view):
    view = small_view
    view.sync_model_positions(only={("block", 987_654), ("nonsense", 1)})
    assert view._pos_overrides == {}


def test_a_partial_sync_skips_an_element_no_longer_on_the_canvas(small_view):
    view = small_view
    bid = sorted(view._block_items)[0]
    view.sync_model_positions(only={("block", bid)})
    view._remove_block(bid)
    # The item is gone, so the recorded override (not a stale item read)
    # must be what a second partial pass reports -- and it must not raise.
    view.sync_model_positions(only={("block", bid)})
    assert ("block", bid) in view._pos_overrides


def test_a_settle_with_nothing_moved_does_not_sync(small_view, monkeypatch):
    view = small_view
    calls = []
    monkeypatch.setattr(view, "sync_model_positions",
                        lambda only=None: calls.append(only))
    view._moved_settle.start()          # armed, but no move recorded
    view.flush_pending_moves()
    assert calls == [], "an empty dirty set took the full O(n) pass"


def test_a_move_during_loading_is_not_recorded(small_view):
    view = small_view
    view._loading = True
    item = next(iter(view._block_items.values()))
    item.setPos(item.pos().x() + 20.0, item.pos().y())
    view._loading = False
    assert view._dirty_moves == set()
    assert view._pos_overrides == {}


def test_a_pending_dirty_move_does_not_follow_into_a_new_model(small_view):
    view = small_view
    item = next(iter(view._block_items.values()))
    item.setPos(item.pos().x() + 20.0, item.pos().y())
    assert view._dirty_moves
    view.load_rbd(_model(60))           # same ids, different model
    assert view._dirty_moves == set()
    view.flush_pending_moves()          # nothing from the old file
    assert view._pos_overrides == {}


def test_the_row_index_follows_added_and_replaced_rows(small_view):
    view = small_view
    sec = view._rbd_ref.sections["BLOCK_DATA"]
    added = dict(sec.rows[0])
    added["ID"] = "99999"
    sec.rows.append(added)
    assert view._row_for("BLOCK_DATA", 99999) is added

    # A same-length replacement (delete one, append one) keeps both the
    # row list's identity and its length: only the id check catches it.
    victim = sec.rows[1]
    old_id = int(float(victim["ID"]))
    replacement = dict(victim)
    replacement["ID"] = "99998"
    sec.rows[:] = [replacement if r is victim else r for r in sec.rows]
    assert view._row_for("BLOCK_DATA", 99998) is replacement
    assert view._row_for("BLOCK_DATA", old_id) is not victim


def test_the_row_index_follows_an_in_place_row_replacement(small_view):
    """The phase-edit restore deep-copies every row back into the same
    list (same identity, same length). The index must not keep writing to
    the orphaned dicts."""
    view = small_view
    sec = view._rbd_ref.sections["BLOCK_DATA"]
    old_rows = list(sec.rows)
    rows = [dict(r) for r in old_rows]
    sec.rows[:] = rows
    bid = sorted(view._block_items)[0]
    old_row = next(r for r in old_rows if int(float(r["ID"])) == bid)
    before = old_row.get("xPos")
    view._pos_overrides[("block", bid)] = (12.5, 34.5)
    view._write_positions_to_rows({("block", bid)})
    new_row = next(r for r in rows if int(float(r["ID"])) == bid)
    assert float(new_row["xPos"]) == pytest.approx(12.5, abs=1e-2)
    assert old_row.get("xPos") == before, "wrote to an orphaned row"


def test_undo_and_redo_after_a_partial_sync_restore_the_row(small_view):
    view = small_view
    bid = sorted(view._block_items)[3]
    item = view._block_items[bid]
    old = (item.pos().x(), item.pos().y())
    new = (old[0] + 60.0, old[1])
    start_row = dict(_row(view, "BLOCK_DATA", bid))

    view._stack.push(MoveCmd(view, [("block", bid, old, new)]))
    moved_row = _row(view, "BLOCK_DATA", bid)
    assert float(moved_row["xPos"]) != float(start_row["xPos"])

    view._stack.undo()
    undone = _row(view, "BLOCK_DATA", bid)
    assert float(undone["xPos"]) == pytest.approx(float(start_row["xPos"]),
                                                  abs=1e-2)
    assert view.blocks[bid].x_position == pytest.approx(
        view._pos_overrides[("block", bid)][0])

    view._stack.redo()
    assert float(_row(view, "BLOCK_DATA", bid)["xPos"]) == pytest.approx(
        float(moved_row["xPos"]), abs=1e-2)


def test_align_syncs_only_the_selection(small_view, monkeypatch):
    view = small_view
    bids = sorted(view._block_items)[:2]
    for bid in bids:
        view._block_items[bid].setSelected(True)
    seen = []
    monkeypatch.setattr(view, "sync_model_positions",
                        lambda only=None: seen.append(only))
    view.align_selected("left")
    assert seen and all(only is not None for only in seen), seen
    assert all(only <= {("block", bid) for bid in bids} for only in seen)
    assert seen[-1] == {("block", bid) for bid in bids}


def test_selected_elements_does_not_walk_the_whole_level(small_view):
    view = small_view

    class NoFullWalk(dict):
        def items(self):        # noqa: A003 - guard, not a data API
            raise AssertionError("walked every item map on the level")

        def values(self):
            raise AssertionError("walked every item map on the level")

    bid = sorted(view._block_items)[0]
    view._block_items = NoFullWalk(view._block_items)
    view._block_items[bid].setSelected(True)
    assert view.selected_elements()[0] == [bid]
    assert view.selected_element_count() == 1


# -- the slow tier: 10,000 elements -----------------------------------------

@pytest.mark.slow
def test_a_drag_on_a_ten_thousand_element_level_is_not_proportional(
        qapp, monkeypatch):
    view = DiagramView()
    view.load_rbd(_model(10_000, step=2))
    assert len(view._block_items) == 10_000

    seen = []
    original = view._write_positions_to_rows
    monkeypatch.setattr(view, "_write_positions_to_rows",
                        lambda only=None: (seen.append(only),
                                           original(only))[1])
    calls = []
    real = _LinkItem._update_route
    monkeypatch.setattr(_LinkItem, "_update_route",
                        lambda self: (calls.append(self), real(self))[1])

    # `step=2`: only every other block is a link endpoint, and a moved
    # block with no links would trivially route nothing.
    bid = sorted(view._block_items)[4_320]
    item = view._block_items[bid]
    old = (item.pos().x(), item.pos().y())
    started = time.perf_counter()
    item.setPos(old[0] + 20.0, old[1])
    view.flush_pending_moves()
    view._stack.push(MoveCmd(view, [("block", bid, old,
                                     (old[0] + 20.0, old[1]))]))
    elapsed = time.perf_counter() - started

    assert seen and all(only == {("block", bid)} for only in seen), seen
    assert 0 < len(set(map(id, calls))) < 40, len(calls)
    # Generous (a slow CI machine): the O(n) paths took many seconds here.
    assert elapsed < 2.0, f"a one-block drag took {elapsed:.2f}s"
