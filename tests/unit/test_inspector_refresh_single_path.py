"""One Inspector refresh path (review E-16).

`MainWindow` had two functions doing the same job:

| Function | Dispatch | Kinds handled |
|---|---|---|
| `_refresh_inspector` | `self._inspector.current_target()` | all 6 |
| `_refresh_inspector_current_element` | `self._inspector._current_block_id` / `_current_node_id` | **2** |

The narrower one reached into **private attributes of another class**, for
which `InspectorDock.current_target()` exists precisely to avoid that — and
it handled only `block` and `node`, so `event`, `hier`, `system` and
`annotation` were silently skipped on exactly the flows that used it:

- the pool-dialog return (`main_window.py:_on_manage_pool_requested`)
- the ribbon pool path
- the display-unit switch (`dialogs.py`'s preferences save)

So "change a display-unit setting while an event is selected" refreshed
nothing, while the same change with a block selected did. Two dispatch
tables for one job, and the narrower one had the holes.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent.parent
GOLD = REPO / "tests" / "golden"


def _main_window_py() -> str:
    return (REPO / "raptor" / "gui" / "ui" / "main_window.py").read_text()


class TestOnlyOneRefreshPath:
    def test_the_twin_is_gone(self):
        """Not merely unused — deleted. An unused duplicate is one edit away
        from being used again."""
        src = _main_window_py()
        assert "def _refresh_inspector_current_element" not in src, (
            "the narrower twin is back; it skips 4 of the 6 element kinds")
        # The name may appear in the docstring explaining its removal.
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) \
                    and node.attr == "_refresh_inspector_current_element":
                pytest.fail(
                    f"main_window.py:{node.lineno} still calls the twin")

    def test_no_module_calls_the_twin(self):
        """Scanned as an AST, not as text.

        `main_window.py`'s docstring NAMES the twin when explaining that it
        was removed, which is worth keeping -- so a text scan flags the
        explanation instead of a live reference. My first version did
        exactly that and failed for the wrong reason."""
        for path in (REPO / "raptor").rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute)
                        and node.attr == "_refresh_inspector_current_element"):
                    pytest.fail(
                        f"{path}:{node.lineno} still references the twin")
                if (isinstance(node, ast.FunctionDef)
                        and node.name == "_refresh_inspector_current_element"):
                    pytest.fail(f"{path}:{node.lineno} still DEFINES it")

    def test_it_does_not_reach_into_inspector_privates(self):
        """The reason the twin existed was an accident of ordering; the
        public `current_target()` answers the same question."""
        src = _main_window_py()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr.startswith("_current_") and node.attr in (
                    "_current_block_id", "_current_node_id",
                    "_current_event_id", "_current_hier_id"):
                lineno = getattr(node, "lineno", 0)
                pytest.fail(
                    f"main_window.py:{lineno} reads InspectorDock's private "
                    f"{node.attr}; use current_target()")


class TestTheRefreshHandlesEveryKind:
    """The behaviour the twin lost. Each kind must re-populate."""

    def _harness(self):
        """A MainWindow with a real Inspector and a loaded model, wired the
        way `_refresh_inspector` needs."""
        from PySide6.QtWidgets import QApplication
        from raptor.core.fileio.rbd import load_rbd
        from raptor.gui.ui.inspector import InspectorDock
        from raptor.gui.ui.main_window import MainWindow
        QApplication.instance() or QApplication([])
        from raptor.gui.ui.diagram import DiagramView
        view = DiagramView()
        # Phasing1, not TwoBlockRedundancy: this harness needs a model
        # that actually HAS events and hierarchies, because the kinds the
        # twin skipped are exactly what is being tested here.
        view.load_rbd(load_rbd(str(GOLD / "Phasing1" / "input.rbd")))
        insp = InspectorDock()
        insp.set_rbd(view._rbd_ref)
        w = MainWindow.__new__(MainWindow)
        w._inspector = insp
        w._diagram_dock = type("Dock", (), {"view": view})()
        return w, insp, view

    def test_an_event_target_refreshes(self):
        """THE GAP. The twin skipped `event`, so a display-unit or pool
        change with an event selected refreshed nothing."""
        w, insp, view = self._harness()
        eid = next(iter(view.events))
        insp.show_event(view.events[eid])
        assert insp.current_target() == ("event", eid)
        calls = []
        insp.show_event = lambda *a: calls.append(a)   # observe, do not re-populate
        from raptor.gui.ui.main_window import MainWindow
        MainWindow._refresh_inspector(w)
        assert calls, "an event target must re-populate"

    def test_a_hier_target_refreshes(self):
        w, insp, view = self._harness()
        assert view.hiers, "premise: Phasing1 has a HIERARCHY_DATA section"
        hid = next(iter(view.hiers))
        insp.show_hier(view.hiers[hid])
        assert insp.current_target()[0] == "hier", (
            f"unexpected target {insp.current_target()!r}")
        calls = []
        insp.show_hier = lambda *a: calls.append(a)
        from raptor.gui.ui.main_window import MainWindow
        MainWindow._refresh_inspector(w)
        assert calls, "a hierarchy target must re-populate"

    def test_a_system_target_refreshes(self):
        w, insp, _view = self._harness()
        insp.show_system_config()
        assert insp.current_target() is None or insp.current_target()[0] == \
            "system", f"unexpected target {insp.current_target()!r}"
        calls = []
        insp.show_system_config = lambda *a: calls.append(a)
        from raptor.gui.ui.main_window import MainWindow
        MainWindow._refresh_inspector(w)
        assert calls, "a system target must re-populate"

    def test_a_block_target_still_refreshes(self):
        """The regression guard: the common case must be unharmed."""
        w, insp, view = self._harness()
        bid = next(iter(view.blocks))
        insp.show_block(bid, view.blocks)
        calls = []
        insp.show_block = lambda *a: calls.append(a)
        from raptor.gui.ui.main_window import MainWindow
        MainWindow._refresh_inspector(w)
        assert calls, "a block target must re-populate"

    def test_no_target_is_a_no_op(self):
        w, insp, _view = self._harness()
        insp._current_target = None
        from raptor.gui.ui.main_window import MainWindow
        MainWindow._refresh_inspector(w)   # must not raise


class TestTheCallersUseTheSinglePath:
    @pytest.mark.parametrize("rel", [
        "raptor/gui/ui/main_window.py",
        "raptor/gui/ui/dialogs.py",
    ])
    def test_every_caller_uses_the_full_refresh(self, rel):
        src = (REPO / rel).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith("_refresh_inspector")):
                assert node.func.attr == "_refresh_inspector", (
                    f"{rel}:{node.lineno} calls {node.func.attr}")
