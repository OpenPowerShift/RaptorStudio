"""The wait-site gate: is every `WaitSite x InterruptReason` cell accounted for?

The companion page
`docs/engine-design/state-coverage.adoc` is a **113-cell matrix** of
`InterruptReason` against the engine's `WaitSite` enum, and **54 of those cells
no model in the 283-model snapshot corpus ever reaches**. That number is "probably inflated" -- some of the 54 are defensive branches a
well-formed model cannot construct -- and the retirement gate has no denominator
until the two are separated out. This module is that separation, made a gate.

**The denominator.** 113 cells, re-derived from `state-coverage.adoc` with
`state_reachability.all_cells()` on every run rather than read from a file this
gate also writes. A gate whose denominator can rot is worse than no gate.

**Every cell has exactly one class**, and it is one of three:

    reachable        a constructible model drives a block into that
                     `(site, reason)` state;
    already_pinned   reachable AND already reached by a fixture committed in
                     `tests/behaviour/`;
    unreachable      needs an argument, and the note is the argument.

Two of those three are *construction* and one is *argument*, and the split is
the point. An `unreachable` claim is accepted only when it points at the
engine -- a code location, or a sibling cell that carries the full argument.
"I could not build one" is not an exemption, so this module refuses a short
note, a note with nothing in backticks, and a note that defers to a cell
which is not itself `unreachable`. Conversely an `unreachable` claim is
**falsifiable**: `unreachable x Pool_Available` was `unreachable` when this
matrix was first written and is now `reachable`, because a model was built for
it. `state_reachability.compare` fails the run if a probe reaches a cell the
manifest calls unreachable, so an argument cannot outlive its refutation.

**How "reached" is decided.** By running models. Every probe in
`state_reachability.py` is a `build_from_spec` spec with all distributions
`Fixed` (code 19), so it is exact and consumes no randomness, and it is run
through the *existing* `state_coverage._Recorder` observer -- the same object
the corpus scan installs -- so "what a cell was reached" means here exactly
what it means on the page. Nothing is detected by a second implementation of
the detection.

**How "reached by a committed fixture" is decided.** Also by running it. Every
fixture `tests/behaviour/` commits is run under the same observer and the
cells it reaches are recorded, so the `already_pinned` set cannot flatter
itself. There are two kinds of fixture and both are measured:
`state_reachability.suite_fixtures()` discovers every `test_b_*.py` module
that exposes a `WAIT_SITE_FIXTURES` registry (test name -> model builder) and
runs each model, and `state_reachability.pinned_by_suite()` runs every rung
of `spine` the same way. `suite_cells()` below delegates to the second, so
there is one measurement of the numerator rather than two.

Every rung counts, not just the first three: S8 added cold standby, S9
phases and S11 capacity, and they reach seven cells S1-S3 never touch.
`test_b_ladder.py` asserts each rung's **complete** event-log timeline as a
literal (`test_spine_rung_timeline_is_unchanged`), so a cell a rung reaches
is inside an asserted literal -- that is what `already_pinned` means here,
and it is why measuring only S1-S3 understated the suite. A wait-site
fixture asserts the same thing one cell at a time:
`test_b_wait_sites.py` commits the model's complete timeline *and* the
`(block, wait site, reason)` triple the observer recorded, which is the only
place a wait site is visible at all -- a block idling and a block parking on
a pmSync trigger log the same rows.

**The ratchet.** `[ratchet] classified = 113` and `[ratchet] pinned = 58` are
committed baselines in `tests/behaviour/state_reachability.toml`. The gate
fails if either number went **down**. That is what makes this a ratchet
rather than a snapshot: a cell may not be silently dropped from the matrix to
make a run green, and a fixture may not quietly stop exercising a wait site.

`pinned` was 11 and is now 58. The first eleven are the ladder's own:
`kickoff x (timer)`, `fail_wait x (timer)`, `repair x (timer)`,
`repair_resource x Pool_Available`, `fail_wait x Dependence_Idle`,
`fail_wait x Triggered_PM_Due`, `idle x Dependence_All_Up`,
`pm_prereqs_tell x (timer)`, `pm_handback x (timer)`,
`repair_pool_spare x Pool_Available` and `zero_stress x Switched_On`. The
other 47 are `test_b_wait_sites.py`: 45 tests, each one claim about one
`(WaitSite, InterruptReason)` cell, over 37 small models. The 15 that remain
are in `state_reachability.toml` as `reachable`, each with either a corpus
count or an attempted probe attached.

**Two self-checks the tool cannot make for itself.** The gate also asserts that
the `_nothing reaches it_` placeholder row is the only pseudo-reason in the
matrix (no other row may borrow the loophole), and that the six
`Switch_Failed_Path` cells are classified as engine findings -- policy permits
the reason at six wait sites and no code path in `raptor/` ever sends it.

`pytest -s`, or `python tests/behaviour/test_b_state_coverage.py --cov-report`,
prints the count. It only goes up.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BEHAVIOUR = REPO / "tests" / "behaviour"

sys.path.insert(0, str(REPO / "tools"))

import state_reachability as sr  # noqa: E402

#: The matrix has 113 cells. Quoted rather than derived, so a page that
#: silently gains or loses a row is caught here instead of quietly changing
#: what the gate covers.
DOCUMENTED_CELLS = 114

#: The ladder rungs whose runs count as "reached by a committed fixture" --
#: all of them, read out of `spine.RUNGS` so a rung added there is counted
#: without editing this file.
PINNED_RUNGS = None  # resolved in `suite_cells` from `spine.RUNGS`

#: An `unreachable` note shorter than this is an apology, not an argument.
#: The shortest real argument in the manifest is 64 characters; the longest is
#: 801.
MIN_ARGUMENT = 60

#: The reason string the coverage page prints instead of a reason when a site's
#: policy permits nothing (`state_coverage.render`). It is not an
#: `InterruptReason`, so no wake can carry it.
PLACEHOLDER_REASON = "_nothing reaches it_"

#: `Switch_Failed_Path`: permitted by `WAKE_POLICY` at six sites, sent by
#: nothing in `raptor/`. These are the engine findings, and they are REPORTED
#: rather than tested -- a test for them would have to assert a lie.
ENGINE_FINDING_REASON = "Switch_Failed_Path"
ENGINE_FINDING_SITES = 6

#: Any backticked span counts as a citation -- a code location, a symbol, or a
#: sibling cell written `` `site x reason` ``.
_CITATION = re.compile(r"`[^`]+`")

#: A cross-reference from one cell's note to another cell.
_CELL_REF = re.compile(r"`(\w+) x (\w+)`")

_FILENAME = re.compile(r"(test_b_[a-z0-9_]+\.py)")


def suite_cells(rungs: tuple[int, ...] | None = None) -> dict[sr.Cell, str]:
    """Cells the committed fixtures reach, measured by running them.

    **One measurement, in one place.** `state_reachability.pinned_by_suite()`
    runs every rung of the ladder *and* every `WAIT_SITE_FIXTURES` entry the
    suite declares, under the same observer, and this delegates to it. The
    function used to run the ladder itself, which was right while the ladder
    was the only source of fixtures and quietly became wrong the moment a
    second one existed: an `already_pinned` row backed by a wait-site fixture
    would have been reported as unbacked, because this gate could not see the
    fixture that backed it. Two measurements of one numerator is exactly the
    drift this module exists to prevent.

    The value is the fixture that reaches the cell, for a failure message;
    the callers here ask only whether a cell is in the set.

    This is the same discipline `test_b_behaviour_coverage.py` applies to
    `assert_timeline` literals, for the same reason: a gate that reads its
    own numerator out of a file it also writes cannot notice the file
    rotting.
    """
    return dict(sr.pinned_by_suite(rungs))


def classification_problems(entries: dict[sr.Cell, sr.Entry],
                            results: dict[str, sr.Probe],
                            load_problems: list[str]) -> list[str]:
    """`state_reachability.compare` with the ratchet calls left out.

    `compare` checks the ratchet too, and this module gives it a test of its
    own with its own message; reporting one regression twice from two tests
    makes the failure harder to read, not easier. Passing an empty ratchet
    turns those two checks into `[no baseline]` lines, which is exactly the
    tag dropped here.
    """
    problems = sr.compare(entries, {}, results, load_problems)
    return [problem for problem in problems if "[no baseline]" not in problem]


def ratchet_problems(state: dict) -> list[str]:
    """The two baselines. Both may only go up; a fall is a red build."""
    entries: dict[sr.Cell, sr.Entry] = state["entries"]
    ratchet: dict[str, int] = state["ratchet"]
    suite: dict[sr.Cell, int] = state["suite"]
    problems: list[str] = []

    classified = len(entries)
    baseline = ratchet.get("classified")
    if baseline is None:
        problems.append("  [no baseline] the manifest has no [ratchet].classified")
    elif classified < baseline:
        problems.append(
            f"  [regression] {classified} cells classified, was {baseline}. A "
            f"classification may only be added, never deleted to make a run "
            f"green; `git diff HEAD~ -- tests/behaviour/state_reachability.toml` "
            f"names the cell that went missing."
        )

    pinned = sum(1 for cell, entry in entries.items()
                 if entry.klass == "already_pinned" and cell in suite)
    baseline = ratchet.get("pinned")
    if baseline is None:
        problems.append("  [no baseline] the manifest has no [ratchet].pinned")
    elif pinned < baseline:
        problems.append(
            f"  [regression] {pinned} cells are reached by a committed fixture "
            f"in tests/behaviour/, was {baseline}. Wait-site coverage went "
            f"backwards: either an 'already_pinned' entry was reclassified or "
            f"a ladder rung stopped reaching a cell it used to reach. "
            f"`git diff HEAD~ -- tests/behaviour/spine.py` names which -- a "
            f"ratchet that cannot be lowered is the whole mechanism."
        )
    return problems


@pytest.fixture(scope="module")
def state() -> dict:
    """The whole comparison, computed once for the module.

    Nineteen probe models plus the eleven spine rungs: about a fifth of a
    second, but once rather than once per test.
    """
    results = sr.measured()
    entries, ratchet, load_problems = sr.load()
    return {
        "results": results,
        "entries": entries,
        "ratchet": ratchet,
        "load_problems": load_problems,
        "suite": suite_cells(),
    }


def test_denominator_is_the_113_cells_of_the_coverage_page(state: dict) -> None:
    """The count the page quotes, re-derived from the page itself.

    Source: docs/engine-design/state-coverage.adoc -- the matrix itself,
      `WaitSite x InterruptReason` rows, measured as 113.
    """
    cells = sr.all_cells()
    assert len(cells) == DOCUMENTED_CELLS, (
        f"state-coverage.adoc now has {len(cells)} WaitSite x reason rows, not "
        f"the documented {DOCUMENTED_CELLS}: "
        f"either the engine grew a wait site or the page lost a row, and both "
        f"need a decision recorded in state_reachability.toml, not a silent "
        f"change of denominator"
    )
    assert len(state["entries"]) == DOCUMENTED_CELLS, (
        f"the manifest classifies {len(state['entries'])} cells; the matrix has "
        f"{len(cells)}"
    )


def test_every_cell_is_classified_exactly_once(state: dict) -> None:
    """Both directions, plus every reachability claim backed by a witness.

    Unclassified is the missing case, stale is the flattering one, and an
    unbacked claim is a `reachable` entry naming nothing that measured it --
    the failure mode a hand-written matrix always drifts into.

    Source: docs/engine-design/state-coverage.adoc, cross-read with
      raptor/core/engine/wait_sites.py `WAIT_POLICY`: a cell the page lists
      but no policy permits is a cell no model can enter.
    """
    problems = classification_problems(state["entries"], state["results"],
                                      state["load_problems"])
    if problems:
        _fail("wait-site reachability: the manifest and the models disagree",
              problems)


def test_every_cell_is_permitted_by_the_engine_policy(state: dict) -> None:
    """A cell the engine does not permit cannot be reached, so cannot be claimed.

    Sites whose policy says "any reason" (`reasons is None`) are exempt: the
    page lists reasons that actually arrived there, and there is no list to
    check them against.

    Source: raptor/core/engine/wait_sites.py `WAKE_POLICY`, one entry per
      `WaitSite`, which is where the permitted reasons are declared.
    """
    from raptor.core.engine.wait_sites import WAKE_POLICY

    problems: list[str] = []
    for cell in sr.all_cells():
        site, reason = cell
        policies = [p for k, p in WAKE_POLICY.items() if str(k) == site]
        if not policies:
            problems.append(f"  [unknown site] {cell} names a WaitSite that "
                            f"wait_sites.WAKE_POLICY does not define")
            continue
        policy = policies[0]
        if policy.reasons is None:
            continue
        if reason == sr.TIMER:
            if not policy.timer:
                problems.append(f"  [impossible cell] {cell}: "
                                f"WAKE_POLICY[{site}].timer is False")
        elif reason not in policy.reasons:
            problems.append(f"  [impossible cell] {cell}: {reason} is not in "
                            f"WAKE_POLICY[{site}].reasons")
    if problems:
        _fail("the coverage page claims cells the engine policy forbids",
              problems)


def test_every_unreachable_cell_carries_an_argument(state: dict) -> None:
    """No unargued exemptions. The note has to point at the engine.

    Three ways a bare "I could not build one" is rejected: it must be long
    enough to be an argument rather than a shrug, it must contain a citation
    in backticks, and if it defers to a sibling cell for the argument, that
    sibling has to be `unreachable` too.

    Source: raptor/core/engine/wait_sites.py `IDLE_WAKE_REASONS` and
      cascade_dependency.py:105 -- the two mechanisms that exclude a wake, and
      therefore the only two arguments that hold up.
    """
    entries: dict[sr.Cell, sr.Entry] = state["entries"]
    problems: list[str] = []
    for cell, entry in sorted(entries.items()):
        if entry.klass != "unreachable":
            continue
        note = " ".join(entry.reason.split())
        if not note:
            problems.append(
                f"  [no reason] {cell} is 'unreachable' with an empty note -- an "
                f"unargued exemption is the thing this gate exists to prevent")
        elif len(note) < MIN_ARGUMENT:
            problems.append(
                f"  [no argument] {cell} is 'unreachable' but its note is only "
                f"{len(note)} characters ({note!r}); 'I could not build one' is "
                f"not an exemption, a code location is")
        elif not _CITATION.search(note):
            problems.append(
                f"  [no citation] {cell} is 'unreachable' but its note cites "
                f"nothing in backticks -- name the line in `raptor/` that "
                f"excludes it")
        else:
            for ref in _CELL_REF.findall(note):
                target = (ref[0], ref[1])
                other = entries.get(target)
                if other is None:
                    problems.append(
                        f"  [dangling cross-reference] {cell} argues 'as for "
                        f"{ref}', which is not a row of the matrix")
                elif other.klass != "unreachable":
                    problems.append(
                        f"  [dangling cross-reference] {cell} argues 'as for "
                        f"{ref}', but that cell is {other.klass!r} -- an "
                        f"unreachable claim cannot lean on a reachable one")
    if problems:
        _fail("an 'unreachable' claim is unargued", problems)


def test_every_pinned_cell_is_reached_by_a_committed_fixture(state: dict) -> None:
    """`already_pinned` is measured, not declared, in both directions.

    The manifest has to name a real file for the claim, and running that
    ladder has to reach the cell. The second half is the part that matters:
    it is what turns `already_pinned` from a label into a measurement.

    Source: tests/behaviour/spine.py, every rung, run through
      block_lifecycle.py `_repair_wait` -- the pool-grant that S3 exercises is
      `repair_resource x Pool_Available`.
    """
    entries: dict[sr.Cell, sr.Entry] = state["entries"]
    suite: dict[sr.Cell, int] = state["suite"]
    problems: list[str] = []
    for cell, entry in sorted(entries.items()):
        if entry.klass != "already_pinned":
            continue
        found = _FILENAME.search(entry.pinned_by)
        if not found:
            problems.append(f"  [no fixture] {cell} is 'already_pinned' but "
                            f"names no test_b_*.py file")
        elif not (BEHAVIOUR / found.group(1)).is_file():
            problems.append(f"  [missing fixture] {cell} names "
                            f"{found.group(1)}, which is not in "
                            f"tests/behaviour/")
        if cell not in suite:
            problems.append(
                f"  [unbacked claim] {cell} is 'already_pinned' by "
                f"{entry.pinned_by!r}, but running the ladder under the "
                f"observer does not reach it -- the fixture changed, or "
                f"never did")
    if problems:
        _fail("a pinned claim is not backed by a run", problems)


def test_the_placeholder_row_is_the_only_pseudo_reason(state: dict) -> None:
    """The one non-cell row, and it may not be borrowed.

    `_nothing reaches it_` is what `state_coverage.render` prints when a
    site's policy permits neither a timer nor any reason, so the `Ends by`
    column has nothing to put in it. It is not an `InterruptReason`, so no
    wake can ever carry it: `InterruptReason` is the closed vocabulary a
    cell's reason must come from, and this string is not in it.

    That check is what stops the placeholder from becoming a loophole -- an
    unargued cell could be relabelled a placeholder and classified
    `unreachable` without anyone writing an argument. Exactly one row may
    carry it, and that row must be the one the page's own printer emits.

    Source: tools/state_coverage.py `render`, the `if not ends:`
      branch, against `raptor/core/engine/interrupts.py` `InterruptReason`.
    """
    from raptor.core.engine.interrupts import InterruptReason

    entries: dict[sr.Cell, sr.Entry] = state["entries"]
    reasons = {reason for reason in InterruptReason}
    pseudo = sorted(cell for cell in entries
                    if cell[1] not in reasons and cell[1] != sr.TIMER)
    assert pseudo == [sr.PLACEHOLDER], (
        f"the matrix has {len(pseudo)} row(s) whose reason is not an "
        f"InterruptReason: {pseudo}. Only `{sr.PLACEHOLDER[1]}` -- the "
        f"placeholder `state_coverage.render` prints -- may be one; another "
        f"row with an unnameable reason is an unargued exemption wearing a "
        f"disguise"
    )


def test_the_engine_findings_are_reported_not_tested(state: dict) -> None:
    """Six cells the policy permits and the engine never sends.

    `Switch_Failed_Path` is in `STATE_ALREADY_APPLIED`, so `WAKE_POLICY`
    admits it at six wait sites; nothing in `raptor/` ever sends it --
    `cold_standby._switch_failed` sets `cold_path_done` and deliberately does
    not interrupt the block. They are classified `unreachable` with an
    `ENGINE FINDING` note, which is a report about the engine, not a gap in
    the suite, and this gate's job is to make sure the report keeps being
    made rather than quietly reclassified into a test.

    Source: raptor/core/engine/wait_sites.py `WAKE_POLICY` (which sites admit
      the reason) against `raptor/core/engine/cold_standby.py`
      `_switch_failed` (the one place that would have sent it).
    """
    entries: dict[sr.Cell, sr.Entry] = state["entries"]
    findings = sorted(cell for cell, entry in entries.items()
                      if "ENGINE FINDING" in entry.reason)
    assert len(findings) == ENGINE_FINDING_SITES, (
        f"{len(findings)} cells carry an ENGINE FINDING note, not "
        f"{ENGINE_FINDING_SITES}: {findings}. `Switch_Failed_Path` is "
        f"permitted by WAKE_POLICY at every site in "
        f"`STATE_ALREADY_APPLIED` and sent by nothing in `raptor/`; if that "
        f"set has changed, say so here rather than letting the count drift"
    )
    for cell in findings:
        entry = entries[cell]
        assert entry.klass == "unreachable", (
            f"{cell} is an engine finding but classified {entry.klass!r} -- "
            f"there is no model to reach it with, so `reachable` would be a "
            f"claim nothing backs"
        )
        assert cell[1] == ENGINE_FINDING_REASON, (
            f"{cell} carries an ENGINE FINDING note but its reason is not "
            f"`{ENGINE_FINDING_REASON}` -- the note and the row disagree"
        )


def test_wait_site_coverage_does_not_regress(state: dict) -> None:
    """The ratchet. This is the test that makes the suite's progress monotone.

    Source: docs/engine-design/state-coverage.adoc -- the coverage page a
      fall in this number is a regression of.
    """
    failures = ratchet_problems(state)
    if failures:
        _fail("wait-site coverage regressed", failures)


def test_report(state: dict, capsys: pytest.CaptureFixture[str]) -> None:
    """Print the progress count.

    Visible with `pytest -s`, or without pytest at all via
    `python tests/behaviour/test_b_state_coverage.py --cov-report`. This is
    how progress is made visible: the number is in CI output, and it only
    ever goes up.

    Source: docs/engine-design/state-coverage.adoc, the page whose
      numerator this prints.
    """
    text = sr.summary(state["entries"], state["ratchet"], state["results"])
    pinned = sum(1 for cell, entry in state["entries"].items()
                 if entry.klass == "already_pinned" and cell in state["suite"])
    baseline = state["ratchet"].get("pinned", 0)
    text += (f"\n  reached by a committed fixture: {pinned} "
             f"(ratchet baseline {baseline})"
             + ("" if pinned >= baseline else "  <-- BELOW BASELINE"))
    problems = classification_problems(state["entries"], state["results"],
                                      state["load_problems"])
    if problems:
        text += f"\n{len(problems)} problem(s) outstanding"
    with capsys.disabled():
        print("\n" + text)


def _fail(title: str, problems: list[str]) -> None:
    shown = problems[:25]
    tail = (
        f"\n  ... and {len(problems) - len(shown)} more" if len(problems) > len(shown) else ""
    )
    pytest.fail(
        f"{title}: {len(problems)} problem(s)\n" + "\n".join(shown) + tail,
        pytrace=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cov-report", action="store_true",
                    help="print the summary and stop")
    ap.parse_args()

    results = sr.measured()
    entries, ratchet, load_problems = sr.load()
    state = {"results": results, "entries": entries, "ratchet": ratchet,
             "load_problems": load_problems, "suite": suite_cells()}
    print(sr.summary(entries, ratchet, results))
    pinned = sum(1 for cell, entry in entries.items()
                 if entry.klass == "already_pinned" and cell in state["suite"])
    print(f"  reached by a committed fixture: {pinned} "
          f"(ratchet baseline {ratchet.get('pinned', 0)})")
    problems = classification_problems(entries, results, load_problems)
    for problem in ratchet_problems(state) + problems:
        print(problem)
    return 1 if problems or ratchet_problems(state) else 0


if __name__ == "__main__":
    raise SystemExit(main())
