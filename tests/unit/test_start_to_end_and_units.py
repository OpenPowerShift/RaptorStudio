"""Two front-door asymmetries closed (review D-06 and D-08).

**D-06 — Start→End reachability lived only in the GUI window.**
`main_window._validation_issues` hand-rolled a DFS from every Start marker
and reported when no End marker was reachable — wrapped in
`except Exception: pass`, and behind a `load_model` call that raised for
exactly the models most likely to have broken wiring. So an MCP or script
user got **no** Start→End check at all: a model the window rejected was
silently accepted through the API.

`api.validate._check_start_to_end_reachable` now owns the rule, reads the
model's own LINK_DATA/NODE_DATA rows (no engine import, cannot raise), and
the window delegates.

**D-08 — the pool dialog's Mean/Std Dev skipped unit conversion.**
`DistroEditor` printed the *raw native* number while labelling it with the
*native* unit, even though every parameter field in the same form went
through `native_to_display`. With `display_unit="years"` active, one form
showed `Mean 8760 (hours)` beside `1 (years)` — a silently wrong number,
in the widget whose docstring claims parity with the Inspector's forms.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.validate import _check_start_to_end_reachable, issues

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _load(stem: str):
    from raptor.core.fileio.rbd import load_rbd
    return load_rbd(str(GOLDEN / stem / "input.rbd"))


def _node(node_id: str, node_type: int, **extra) -> dict:
    row = {"ID": node_id, "Name": f"N{node_id}", "Type": str(node_type),
           "ParentId": "0", "xPos": "0", "yPos": "0"}
    row.update(extra)
    return row


def _link(link_id: str, ftype: str, fid: str, ttype: str, tid: str) -> dict:
    return {"ID": link_id, "FromType": ftype, "FromID": fid,
            "ToType": ttype, "ToID": tid, "pID": "0"}


def _graph(rbd, nodes, links):
    """Replace the graph sections outright, so the fixture is exactly the
    shape the check reasons about rather than a subset of a real model."""
    rbd.sections["NODE_DATA"].rows = nodes
    rbd.sections["LINK_DATA"].rows = links
    return rbd


class TestStartToEndReachability:
    def test_a_connected_model_is_silent(self):
        rbd = _graph(_load("TwoBlockRedundancy"),
                     [_node("1", 1), _node("2", 3)],
                     [_link("1", "Node", "1", "Node", "2")])
        assert _check_start_to_end_reachable(rbd) == []

    def test_a_broken_model_warns(self):
        """THE GAP. This model produced nothing at all over the API."""
        rbd = _graph(_load("TwoBlockRedundancy"),
                     [_node("1", 1), _node("2", 2)],
                     [_link("1", "Node", "1", "Node", "2")])
        found = _check_start_to_end_reachable(rbd)
        assert found, "a Start that reaches no End must be reported"
        assert found[0].severity == "warning"
        assert "Start to End" in found[0].message

    def test_it_is_a_warning_not_an_error(self):
        """A model with an unreachable End still simulates; blocking it
        would refuse models the GUI and the engine both run."""
        rbd = _graph(_load("TwoBlockRedundancy"),
                     [_node("1", 1), _node("2", 2)],
                     [_link("1", "Node", "1", "Node", "2")])
        assert all(i.severity != "error"
                   for i in _check_start_to_end_reachable(rbd))

    def test_a_multi_hop_path_is_followed(self):
        """The walk must be transitive, not just one hop."""
        rbd = _graph(_load("TwoBlockRedundancy"),
                     [_node("1", 1), _node("2", 2), _node("3", 2),
                      _node("4", 3)],
                     [_link("1", "Node", "1", "Node", "2"),
                      _link("2", "Node", "2", "Node", "3"),
                      _link("3", "Node", "3", "Node", "4")])
        assert _check_start_to_end_reachable(rbd) == []

    def test_any_one_reachable_start_is_enough(self):
        """Two Starts, one broken: the reachable one still reaches End, so
        the model's flow is not broken."""
        rbd = _graph(_load("TwoBlockRedundancy"),
                     [_node("1", 1), _node("2", 1), _node("9", 3)],
                     [_link("1", "Node", "1", "Node", "9"),
                      _link("2", "Node", "2", "Node", "8")])
        assert _check_start_to_end_reachable(rbd) == []

    def test_no_start_marker_is_not_this_checks_business(self):
        """An events-only model has no Start node and is fully supported."""
        rbd = _graph(_load("TwoBlockRedundancy"),
                     [_node("1", 2)],
                     [_link("1", "Node", "1", "Node", "2")])
        assert _check_start_to_end_reachable(rbd) == []

    def test_a_model_with_no_links_section_is_silent(self):
        rbd = _load("TwoBlockRedundancy")
        del rbd.sections["LINK_DATA"]
        assert _check_start_to_end_reachable(rbd) == []

    def test_a_link_cycle_does_not_hang(self):
        """The stack is bounded by `seen`, so a cycle terminates."""
        rbd = _graph(_load("TwoBlockRedundancy"),
                     [_node("1", 1), _node("2", 2)],
                     [_link("1", "Node", "1", "Node", "2"),
                      _link("2", "Node", "2", "Node", "1")])
        found = _check_start_to_end_reachable(rbd)
        assert found and "Start to End" in found[0].message

    def test_it_is_part_of_the_public_validator(self):
        """The whole point: a script user must get it from `issues()`."""
        rbd = _graph(_load("TwoBlockRedundancy"),
                     [_node("1", 1), _node("2", 2)],
                     [_link("1", "Node", "1", "Node", "2")])
        msgs = [i.message for i in issues(rbd)]
        assert any("Start to End" in m for m in msgs), (
            "the check is not wired into issues(), so the API door still "
            "does not see it")

    def test_real_goldens_do_not_newly_fail(self):
        """Every golden model must be Start->End clean, or this check would
        fire on models the project ships."""
        import glob
        flagged = []
        for path in sorted(glob.glob(str(GOLDEN / "*" / "input.rbd"))):
            try:
                rbd = _load(Path(path).parent.name)
            except Exception:
                continue
            if _check_start_to_end_reachable(rbd):
                flagged.append(Path(path).parent.name)
        assert not flagged, f"goldens flagged as Start->End broken: {flagged}"

    def test_the_window_no_longer_walks_the_graph_itself(self):
        """Structural: the capability must not move back into the widget."""
        import inspect

        from raptor.gui.ui import main_window
        src = inspect.getsource(main_window.MainWindow._validation_issues)
        assert ("_check_start_to_end_reachable" in src
                or "api_validate" in src), (
            "the window must delegate to the validator, not walk the graph "
            "itself")
        # The walk must be gone from the CODE, not just from the prose.
        # Stripped of comments first: the delegation comment legitimately
        # explains that the old version called `load_model`, and a naive
        # substring check flags the explanation instead of the defect.
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(src))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        called |= {n.func.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute)}
        assert not called & {"load_model", "_lm"}, (
            f"the window still loads the model to walk the graph itself: "
            f"{sorted(called & {'load_model', '_lm'})}")


class TestDistroEditorUnitConversion:
    """D-08. The Mean/Std Dev readouts must be converted and labelled with
    the SHOWN unit, like every parameter field in the same form."""

    @pytest.fixture(scope="module")
    def qapp(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])

    def _editor(self, distro, params, units, display_unit):
        from raptor.gui.ui.distro_editor import DistroEditor
        return DistroEditor(distro, params, use="Length", units=units,
                            display_unit=display_unit)

    #: Exponential (code 4), chosen because its MEAN *is* its first
    #: parameter -- so the number the readout should show is readable
    #: straight off the input. My first fixture was Uniform Real, whose
    #: mean is the MIDPOINT of its two bounds; I then asserted 8760 where
    #: the correct displayed value is 4380 hours = 0.5 years. The fix was
    #: right and the test was wrong, twice, before I checked the shape.
    _EXPO = 4

    def test_hours_are_converted_to_years(self, qapp):
        """8760 hours is 1 year. The old code printed `8760 (hours)`.

        Exponential (4) is the fixture because its MEAN is the scale
        parameter, so the readout shows the number verbatim. Uniform Real
        would not do: its mean is the midpoint of its bounds, so the
        displayed figure is legitimately 4380 rather than 8760 -- which is
        a property of the distribution, not of the conversion.
        """
        e = self._editor(4, [8760.0, 0.0, 0.0], "hours", "years")
        text = e._stats_labels[0].text()
        assert "8760" not in text, (
            f"the readout still shows the raw native value: {text!r}")
        assert "1" in text, f"8760 hours should read as ~1 year: {text!r}"

    def test_the_label_names_the_shown_unit(self, qapp):
        e = self._editor(4, [8760.0, 0.0, 0.0], "hours", "years")
        for lbl in e._stats_labels:
            assert "years" in lbl.text(), (
                f"labelled with the native unit instead of the shown one: "
                f"{lbl.text()!r}")
            assert "hours" not in lbl.text(), (
                f"labelled 'hours' on a years-valued number: {lbl.text()!r}")

    def test_with_no_override_the_native_number_and_unit_are_shown(self, qapp):
        """The no-override case is the regression guard: the fix must not
        divide by a conversion factor that was never requested."""
        e = self._editor(4, [8760.0, 0.0, 0.0], "hours", None)
        assert "8760" in e._stats_labels[0].text()
        assert "hours" in e._stats_labels[0].text()

    def test_no_units_means_no_suffix_and_no_conversion(self, qapp):
        e = self._editor(4, [42.0, 0.0, 0.0], "", None)
        assert "42" in e._stats_labels[0].text()
        assert "(" not in e._stats_labels[0].text()

    def test_the_parameter_fields_and_the_stats_agree(self, qapp):
        """The bug in one line: the form disagreed with itself."""
        from raptor.gui.ui.units_conv import native_to_display
        e = self._editor(4, [8760.0, 0.0, 0.0], "hours", "years")
        field = e._param_edits[0].text()          # the Mean/scale field
        expected = native_to_display(8760.0, "hours", "years")
        assert field.startswith(f"{expected:.6g}"), (
            f"param field shows {field!r}, expected {expected:.6g}")
        assert "1" in e._stats_labels[0].text()

    def test_a_non_numeric_readout_passes_through_untouched(self, qapp):
        """Pearson5 (11) with shape 1 has no mean: `compute_stats` returns
        the STRING "Undefined" -- not a number, and not the bare `None` this
        test's first draft claimed. (It used all-zero parameters, which are
        not a valid set: the editor now shows "—" for those.)

        The unit conversion must not scale a string, and must not append a
        unit suffix to it either. Worth pinning because the display-unit
        override is the only thing that makes this branch reachable at all
        in the pool dialogs.
        """
        e = self._editor(11, [1.0, 2.0, 0.0], "hours", "years")
        assert e._stats_labels[0].text() == "Undefined"
        assert e._stats_labels[1].text() == "Undefined"
