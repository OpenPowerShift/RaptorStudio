"""`raptor.api.series` is the only implementation of the Ao / capacity sink
domain; `raptor.gui.ui.plots` adapts its run dicts.

The two used to be two parsers, two interpolation routines and two
envelope builders, already diverged: the GUI's `_interp` had no empty-
series guard (so it raised `IndexError` where the API returns `0.0`), and
the GUI's own run-boundary rule was a second copy of the same
`End_of_Run_` check.

The DECIMATION is deliberately still two functions -- `api.series` uses
LTTB (shape-preserving, keeps change-points) for a served/exported
series, the widget strides uniformly for painting only. That difference
is intentional and asserted here so nobody "deduplicates" it.

These tests pin:
* the GUI's interpolated values equal the shared function's, including
  the empty-series case that used to raise;
* the shared parsers and the GUI adapters produce the same runs on real
  engine sink output;
* structurally, `plots.py` defines no `_interp` / `parse_ao_runs` /
  `parse_cap_runs` of its own.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from raptor.api import series as api_series
from raptor.gui.ui import plots as gui_plots

PLOTS_PY = (Path(__file__).resolve().parents[2]
            / "raptor" / "gui" / "ui" / "plots.py")
GOLDEN = Path(__file__).resolve().parents[1] / "golden"

#: The Ao-sink format, including both row widths (6 columns mid-run, 5 at
#: the run-end) and the separator that is itself a `*`-prefixed line.
AO_LINES = [
    "SimulationTime        Ao        Green     Yellow    Red       Interval",
    "10.000000             0.900000  0.600000  0.300000  0.100000  0.900000",
    "20.000000             0.800000  0.550000  0.250000  0.200000  0.800000",
    "30.000000             0.700000  0.500000  0.200000  0.300000",
    "***End_of_Run_1***",
    "10.000000             0.500000  0.400000  0.100000  0.500000  0.500000",
    "40.000000             0.450000  0.350000  0.100000  0.550000",
]

CAP_LINES = [
    "SimulationTime        Flow       Capacity",
    "0.000000              2050       2050",
    "13.901797             1550       2050",
    "***End_of_Run_1***",
    "0.000000              4          999999999",
]


# ---------------------------------------------------------------------
# Interpolation: one implementation, and it survives an empty series
# ---------------------------------------------------------------------

def test_gui_interp_is_the_shared_interp():
    """Not "behaves the same" -- the SAME function object, so the empty-
    series guard cannot be dropped from one copy without the other
    noticing."""
    assert gui_plots._interp is api_series._interp


@pytest.mark.parametrize("x", [-5.0, 0.0, 5.0, 10.0, 12.5, 30.0, 1e6])
def test_gui_and_api_interpolate_the_same_values(x):
    xs = [10.0, 20.0, 30.0]
    ys = [0.9, 0.8, 0.7]
    assert gui_plots._interp(xs, ys, x) == api_series._interp(xs, ys, x)


def test_empty_series_returns_zero_rather_than_raising():
    """The bug this closes: `plots._interp` dereferenced `xs[0]` with no
    guard, so an empty series raised `IndexError` in the GUI where the API
    returned 0.0. Any caller that relied on the IndexError was a bug."""
    assert api_series._interp([], [], 5.0) == 0.0
    assert gui_plots._interp([], [], 5.0) == 0.0
    # and through the envelope that consumes it
    assert api_series.mean_envelope([]) == {"t": [], "mean": [],
                                             "min": [], "max": []}


# ---------------------------------------------------------------------
# Parsing: one set of rules, adapted not re-implemented
# ---------------------------------------------------------------------

def test_ao_adapter_produces_the_shared_runs():
    """Same runs, same samples -- only the column names differ."""
    shared = api_series.parse_ao_runs("\n".join(AO_LINES))
    adapted = gui_plots.parse_ao_runs(AO_LINES)
    assert len(adapted) == len(shared) == 2
    for run, src in zip(adapted, shared):
        assert run["t"] == src["t"]
        assert run["ao"] == src["values"]
        assert run["g"] == src["green"]
        assert run["y"] == src["yellow"]
        assert run["r"] == src["red"]
        assert run["iv"] == src["interval"]
    # The run-end row carries no Interval, and both copies say so.
    assert adapted[0]["iv"] == [0.9, 0.8, None]
    assert adapted[1]["iv"] == [0.5, None]


def test_ao_adapter_matches_the_shared_run_boundary_rule():
    """`***End_of_Run_1***` is BOTH a `*`-prefixed line and the boundary, so
    the boundary check has to precede the generic skip. One copy of that
    ordering now (`api.series`), and both parsers agree."""
    lines = [
        "10.0  0.9  0.6  0.3  0.1  0.9",
        "***End_of_Run_1***",
        "20.0  0.5  0.4  0.1  0.5  0.5",
        "***End_of_Run_2***",
        "30.0  0.4  0.3  0.1  0.6  0.4",
    ]
    runs = gui_plots.parse_ao_runs(lines)
    assert [r["t"] for r in runs] == [[10.0], [20.0], [30.0]]


def test_capacity_adapter_produces_the_shared_runs():
    shared = api_series.parse_capacity_runs("\n".join(CAP_LINES))
    adapted = gui_plots.parse_cap_runs(CAP_LINES)
    assert len(adapted) == len(shared) == 2
    for run, src in zip(adapted, shared):
        assert run["t"] == src["t"]
        assert run["flow"] == src["flow"]
        assert run["cap"] == src["capacity"]
    assert adapted[1]["cap"] == [999999999.0]


def test_real_engine_sink_output_parses_identically_on_both_sides():
    """The engine's own output, not a hand-written fixture: the Graphs tab
    and `api.analyse` must see the same runs."""
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbd import load_rbd

    ao_lines: list[str] = []
    cap_lines: list[str] = []
    run_simulation_with_reports(
        load_rbd(str(GOLDEN / "Capacity1" / "input.rbd")), num_runs=3,
        date="fixed", ao_sink=ao_lines, capacity_sink=cap_lines)

    shared = api_series.parse_ao_runs("\n".join(ao_lines))
    adapted = gui_plots.parse_ao_runs(ao_lines)
    assert len(shared) == len(adapted) == 3
    for run, src in zip(adapted, shared):
        assert run["t"] == src["t"]
        assert run["ao"] == src["values"]
        for g, y, r in zip(run["g"], run["y"], run["r"]):
            assert g + y + r == pytest.approx(1.0, abs=2e-6)

    shared_cap = api_series.parse_capacity_runs("\n".join(cap_lines))
    adapted_cap = gui_plots.parse_cap_runs(cap_lines)
    assert len(shared_cap) == len(adapted_cap) == 3
    for run, src in zip(adapted_cap, shared_cap):
        assert run["t"] == src["t"]
        assert run["flow"] == src["flow"]
        assert run["cap"] == src["capacity"]


def test_gui_envelope_matches_the_shared_envelope():
    """`multi_run_envelope` and `mean_envelope` resample the same runs
    onto the same grid with the same interpolation, so on one input they
    must produce the same numbers -- including for runs of different
    lengths, which is where a second interpolation routine would drift."""
    runs = gui_plots.parse_ao_runs(AO_LINES)
    times, mins, avgs, maxs = gui_plots.multi_run_envelope(runs)
    shared = api_series.parse_ao_runs("\n".join(AO_LINES))
    env = api_series.mean_envelope(shared)
    assert len(times) > 2, "the envelope must not be empty (else this compares [])"
    assert times == pytest.approx(env["t"])
    assert mins == pytest.approx(env["min"])
    assert avgs == pytest.approx(env["mean"])
    assert maxs == pytest.approx(env["max"])


# ---------------------------------------------------------------------
# Structural: plots.py re-implements none of it
# ---------------------------------------------------------------------

def _plots_tree() -> ast.Module:
    return ast.parse(PLOTS_PY.read_text(encoding="utf-8"))


def _code_of(func: ast.FunctionDef) -> str:
    """The function's source WITHOUT its docstring (the docstring is prose
    about the format, so a phrase like `End_of_Run_` legitimately appears
    there -- but never in the code)."""
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


def _defs(*names: str) -> dict[str, list[ast.FunctionDef]]:
    out: dict[str, list[ast.FunctionDef]] = {n: [] for n in names}
    for node in ast.walk(_plots_tree()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in out:
            out[node.name].append(node)
    return out


def test_plots_defines_no_interpolation_of_its_own():
    assert not _defs("_interp")["_interp"], \
        "plots.py must use api.series._interp, not its own copy"


def test_plots_adapts_the_parsers_rather_than_reimplementing_them():
    """`parse_ao_runs` / `parse_cap_runs` must stay importable from this
    module (the Results dock and the UI sweeps import them by name), but
    each must be defined exactly ONCE, must not parse numbers itself, and
    must delegate to the shared parsers.

    The "exactly once" part matters: a shadow copy pasted *above* the
    adapter would satisfy a naive check that only looks at the last
    definition of the name."""
    defs = _defs("parse_ao_runs", "parse_cap_runs", "parse_capacity_runs")
    assert not defs["parse_capacity_runs"], \
        "the shared name must not be shadowed by a local definition"
    for name in ("parse_ao_runs", "parse_cap_runs"):
        found = defs[name]
        assert len(found) == 1, f"{name} defined {len(found)} times"
        body = _code_of(found[0])        # the docstring is prose, not code
        assert "float(" not in body, f"{name} re-parses rows itself:\n{body}"
        assert "End_of_Run_" not in body, \
            f"{name} re-implements the run-boundary rule:\n{body}"
        doc = ast.get_docstring(found[0]) or ""
        assert "series" in doc, \
            f"{name} must name the shared parser it delegates to"


def test_plots_imports_the_shared_implementations():
    imported: set[str] = set()
    for node in ast.walk(_plots_tree()):
        if isinstance(node, ast.ImportFrom) and node.module == "raptor.api.series":
            for alias in node.names:
                imported.update({alias.name, alias.asname or alias.name})
    assert "_interp" in imported
    assert "parse_ao_runs" in imported          # imported as _parse_ao_runs
    assert "parse_capacity_runs" in imported


# ---------------------------------------------------------------------
# The decimation difference is intentional -- and recorded
# ---------------------------------------------------------------------

def test_two_decimators_exist_and_differ():
    """Pinned so a future 'there are two decimators' cleanup knows this is
    deliberate: the API's LTTB keeps the shape/change-points of a served
    series, the GUI's stride is for painting only."""
    assert hasattr(gui_plots, "_decimate_for_painting")
    assert not hasattr(gui_plots, "_decimate"), \
        "the painting decimator must keep its explicit name"
    src = gui_plots._decimate_for_painting
    doc = src.__doc__ or ""
    assert "LTTB" in doc and "api.series.decimate" in doc, \
        "the deliberate divergence must be recorded at the function"
    # The two pick different samples from the same input.
    t = [float(i) for i in range(1000)]
    v = [0.0] * 1000
    v[500] = 1.0                       # a single spike LTTB keeps, stride misses
    painted = src(t, v, max_points=10)
    served = api_series.decimate({"t": t, "values": v}, 10)
    assert len(painted) == 2 and len(painted[0]) == len(painted[1])
    assert painted[0][0] == t[0] and painted[0][-1] == t[-1]
    assert served["t"][0] == t[0] and served["t"][-1] == t[-1]
    assert served["t"] != painted[0], \
        "if these ever agree the comment's justification needs revisiting"


def test_painting_decimation_keeps_the_endpoints():
    t = [float(i) for i in range(5000)]
    (ts,) = gui_plots._decimate_for_painting(t, max_points=100)
    assert ts[0] == 0.0 and ts[-1] == 4999.0
    # The stride emits max_points picks PLUS the pinned final sample, so
    # the cap here is max_points + 1 (pre-existing; the API's decimator
    # is the one with a strict "never more than n" contract). What matters
    # for painting is that the true endpoints never move.
    assert len(ts) == 101
    # Short enough to paint is returned untouched.
    (same,) = gui_plots._decimate_for_painting(t, max_points=10_000)
    assert same == t