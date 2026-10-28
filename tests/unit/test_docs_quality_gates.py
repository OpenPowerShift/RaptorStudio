"""Documentation quality gates, run as part of the regular test suite.

`check_links`, `check_numbers` and `check_models` check the book and rerun
every tutorial model. Each registry entry and each model file is a
separate parametrized case, keeping a single test at a couple of seconds
rather than the ~1 minute / ~20 seconds the whole batch would take, which
is well under the suite's 60-second per-test watchdog.

**Rule B2, and what that means for this file.** Almost nothing here is a
candidate for `Fixed` distributions, because almost nothing here is the
engine's subject -- the engine's subject is the *tutorial models*, which
are the book's assets and are run exactly as the book runs them. What B2
does require is that a stochastic family be paid for honestly, and it was
not:

* `check_numbers` re-runs each model at the row's trial count and compares
  the reported mean to the number the chapter quotes. Those numbers *are*
  sample means, so the gate cannot be made exact without re-deriving the
  book. What it can do is stop paying for trials the gate does not need:
  the deciding quantity is the mean's own SEM against the row's
  tolerance, and `check_numbers.REGISTRY` now records, per row, the
  smallest trial count that keeps the SEM well inside the tolerance. The
  Availability rows (a continuous per-run ratio) converge fast and were
  cut by 40-400x; the Reliability rows (per-run 0/1) are noise-dominated
  at any affordable count and are left alone, with the reason written
  down rather than hidden.
* `check_models` does not read any number -- it asserts that a model file
  still loads and runs headless, whatever its extension says its content
  is. The run count is therefore not part of that claim, so this test
  asks for two replications rather than the file's shipped `NumRuns`
  (up to 2000, which is what made Flight1 and ProdLine1 expensive). Two
  rather than one, so the report path still aggregates over more than a
  single row.
* Both tools now pass `collect_logs=False`. They read only the report
  tables, which the runner builds from its per-run report rows rather
  than from the event logs, so the Final_Results_Report is unchanged --
  asserted below rather than assumed.

The seeds are the runner's default (`seed=0`, i.e. the unmodified
`fetch_raptor_seed` stream table), so every one of these numbers is
reproducible run-to-run and a failure names a real drift rather than a
re-seed.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = os.path.join(REPO, "new_docs", "tools")

if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import check_links  # noqa: E402
import check_numbers  # noqa: E402
import check_models  # noqa: E402
import check_tools  # noqa: E402
import gen_help_reference  # noqa: E402

#: Replications for the `check_models` sweep. The claim is "this file
#: loads and runs", which one run already establishes; two are used so the
#: Final_Results_Report aggregates over more than one row and the
#: multi-run code path is still executed. Independent of each model's
#: shipped `NumRuns`, which is what used to cost 2000 trials on Flight1.
CHECK_MODELS_RUNS = 2


def test_check_links():
    """Every <<xref>>, image::, and opsmodel:: in the book resolves to
    a real anchor/file, and no captured screenshot sits unreferenced.
    Fast (no simulation) -- stays as one test."""
    assert check_links.main() == 0


def test_check_tools():
    """Every tool the MCP server registers is documented in ch16, and the
    tool counts the chapter quotes match the live tool set.

    Without this the chapter said "32 tools" in one paragraph and "42" in
    another indefinitely -- nothing compared either to reality.  See
    `new_docs/tools/check_tools.py`."""
    assert check_tools.main() == 0


@pytest.mark.long
@pytest.mark.parametrize(
    "entry", check_numbers.REGISTRY,
    ids=[e[0] for e in check_numbers.REGISTRY])
def test_check_numbers(entry):
    """One tutorial model still produces the headline number its
    chapter/README.md quotes, within tolerance. All models use a fixed
    seed, so this is a near-exact check, not a statistical one -- see
    check_numbers.py's own module docstring.

    The trial count is the row's (see `REGISTRY`'s "HOW MANY TRIALS"
    comment): enough that the mean's SEM is inside the tolerance, so a
    failure means the documented number moved, not that the draw was
    unlucky."""
    failure = check_numbers.check_one(entry)
    assert failure is None, failure


@pytest.mark.parametrize("name", check_models.all_model_names())
def test_check_models(name):
    """One model file under new_docs/models/ still loads and runs
    headless, regardless of its extension vs. its real (JSON or legacy)
    content.

    Run count is not part of this claim, so it is fixed at
    `CHECK_MODELS_RUNS` rather than taken from the file -- the shipped
    counts run to 2000 and bought nothing here."""
    failure = check_models.check_one(name, verbose=False, runs=CHECK_MODELS_RUNS)
    assert failure is None, failure


def test_collect_logs_false_does_not_change_the_reported_summary():
    """The premise behind `collect_logs=False` in both tools: a model
    run without retaining its event logs produces a byte-identical
    Final_Results_Report, because the report is assembled from the
    runner's per-run report rows and never reads the logs back.

    This is asserted rather than assumed because the whole run-count
    reduction rests on it -- if it were false, every number the two gates
    compare would be measuring something slightly different from what
    they used to."""
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbds import load_rbds

    path = os.path.join(check_numbers.MODELS, "CarCare0.rbds")
    with_logs = run_simulation_with_reports(load_rbds(path), 3, date="fixed")[2]
    without = run_simulation_with_reports(load_rbds(path), 3, date="fixed", collect_logs=False)[2]
    assert with_logs == without


def test_manual_reference_matches_live_gui():
    """docs/manual-reference.adoc (auto-generated from the live GUI) must
    match what `gen_help_reference.py` generates now. A mismatch means a
    ribbon/dialog changed without regenerating the reference."""
    generated = "\n".join(gen_help_reference.generate_lines()) + "\n"
    with open(gen_help_reference.OUT, encoding="utf-8") as fh:
        checked_in = fh.read()
    assert generated == checked_in, (
        "docs/manual-reference.adoc is stale -- re-run "
        "`.venv/bin/python new_docs/tools/gen_help_reference.py` and commit it")