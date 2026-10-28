"""Selecting on the canvas does not rebuild the Inspector inside the click.

A click used to spend ~9 ms building Inspector tabs on a large model, and a
rubber-band drag or arrow-key walk paid that for every element it passed.
The rebuild is queued for the last element instead, so the click and the
repaint stay responsive.
"""
from __future__ import annotations

import os
from time import perf_counter

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.gui.ui.inspector import InspectorDock  # noqa: E402
from raptor.gui.ui.main_window import (  # noqa: E402
    INSPECTOR_SELECT_DEBOUNCE_MS,
    MainWindow,
)

pytestmark = pytest.mark.gui

GOLD = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                           "input.rbd")), None)
    yield w
    w._dirty = False
    w.close()


def _two_blocks(w):
    return [bid for bid in sorted(w._diagram_dock.view.blocks) if bid != 0]


def test_a_click_costs_less_than_5ms_of_rebuild(window, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(InspectorDock, "_populate_block",
                        lambda self, block: calls.append(block.id))
    bid = _two_blocks(window)[0]

    started = perf_counter()
    window._on_canvas_block_selected(bid, "")
    elapsed = perf_counter() - started

    assert elapsed < 0.005, f"selection path spent {elapsed * 1000:.1f} ms"
    assert calls == [], "the rebuild must not run inside the click"

    QTest.qWait(INSPECTOR_SELECT_DEBOUNCE_MS * 3)
    assert calls == [bid]


def test_a_burst_of_clicks_rebuilds_once_for_the_last(window, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(InspectorDock, "_populate_block",
                        lambda self, block: calls.append(block.id))
    first, second = _two_blocks(window)

    window._on_canvas_block_selected(first, "")
    window._on_canvas_block_selected(second, "")
    window._on_canvas_block_selected(first, "")
    assert calls == []

    QTest.qWait(INSPECTOR_SELECT_DEBOUNCE_MS * 3)
    assert calls == [first], "one rebuild, for the element still selected"


def test_clearing_the_selection_cancels_the_queued_rebuild(window,
                                                           monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(InspectorDock, "_populate_block",
                        lambda self, block: calls.append(block.id))
    bid = _two_blocks(window)[0]

    window._on_canvas_block_selected(bid, "")
    window._on_canvas_selection_cleared()
    QTest.qWait(INSPECTOR_SELECT_DEBOUNCE_MS * 3)
    assert calls == []


def test_a_node_selection_cancels_the_queued_block_rebuild(window,
                                                           monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(InspectorDock, "_populate_block",
                        lambda self, block: calls.append(block.id))
    bid = _two_blocks(window)[0]
    nid = next(n for n in window._diagram_dock.view.nodes if n != 0)

    window._on_canvas_block_selected(bid, "")
    window._on_canvas_node_selected(nid, "")
    QTest.qWait(INSPECTOR_SELECT_DEBOUNCE_MS * 3)
    assert calls == []


def test_a_node_click_costs_less_than_5ms_of_rebuild(window, monkeypatch):
    """A node's Inspector rebuild is heavier than a block's (its
    Dependency tab lists every block), so it takes the same debounce."""
    calls: list[int] = []
    monkeypatch.setattr(InspectorDock, "_populate_node",
                        lambda self, node: calls.append(node.id))
    nid = next(n for n in window._diagram_dock.view.nodes if n != 0)

    started = perf_counter()
    window._on_canvas_node_selected(nid, "")
    elapsed = perf_counter() - started

    assert elapsed < 0.005, f"selection path spent {elapsed * 1000:.1f} ms"
    assert calls == [], "the rebuild must not run inside the click"

    QTest.qWait(INSPECTOR_SELECT_DEBOUNCE_MS * 3)
    assert calls == [nid]
