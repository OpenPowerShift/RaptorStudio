"""Single point failures -- what one block's failure does to the whole system.

`raptor/core/engine/spf_fev.py` is the port of `WriteSPFfile`, the Ch.10 "Create Single Point Failure File"
listing (menu 10-5). The source decides its SPF set **by simulation**: for
each element currently Running/Standby it sets that one element to
Repairing, re-runs `CheckColds` + `AnalyzeSystem`, and writes a row when the
system turns Red. This port reproduces the same what-if over the static
graph, propagating statuses from the one failed element to the End node and
asking the same question the source asks at Raptor 7: does the End
node leave the up-like bucket?

This file is the behaviour test for that question, and it is the *only* test
in the suite that pins it: at the start of this work `spf.py` had **0.0%**
coverage from `tests/behaviour/` -- nobody had ever looked at it -- against a
floor of 88.3% held by the whole-report comparison tests, which execute it
only as a by-product.

**Why the engine module is called directly.** The public entry point,
`raptor.api.analyse.single_point_failures(workspace, model_id)`, takes a
`Workspace` -- and rule B3 (built in memory, no `Workspace`, no `.rbd` on
disk) forbids one. So every test here builds its model through the harness
(`harness.run(spec)`, the suite's one constructor) and then calls
`load_model(run.rbd)` and `raptor.core.engine.spf` on it. Nothing is written
to disk and no model ever existed outside this process.

The harness *runs* each model to hand back the built one, which these tests do
not need; that is the price of keeping the harness the only path into the
engine, and it is a few milliseconds per model.

Every model here is `Fixed` (distribution code 19, `fail_params`/`repair_params`
given together as `build_from_spec` requires), so each SPF set is exact and
the run consumes zero randomness. SPF itself does not simulate, so the lives
only have to be *valid* here, not interesting.
"""
from __future__ import annotations

import datetime
from typing import Any

from raptor.core.engine.fev import FevSession
from raptor.core.engine.spf import render_spf_file, spf_date_string
from raptor.core.engine.spf_fev import (
    build_spf_listing,
    phased_sections,
    single_point_failures,
)


def phased_single_point_failures(rbd):
    """Per-phase `(name, rows)` sections, from Failure Effects View."""
    return [(name, [{k: v for k, v in r.items() if k != "crit"} for r in rows])
            for name, rows in phased_sections(FevSession(rbd))]

from . import harness
from .spine import FIXED, spine

#: The date every listing in this file is stamped with, so no expectation
#: depends on today (rule B7: SimTime is the only clock a test reads). The
#: listing's date column is a *string the test supplies* -- `spf_date_string`
#: is handed a literal datetime below rather than read from the wall clock.
STAMP = "5 Jan 2026"


def _model(spec: dict[str, Any]):
    """The loaded model for *spec*, built and run through the harness."""
    return harness.run(spec).rbd


def _loaded(spec: dict[str, Any]):
    """The engine model for *spec* (for the tests that look at node state)."""
    from raptor.core.engine.load import load_model
    return load_model(_model(spec))


def _strip(rows):
    """Rows without the criticality value (not what these tests pin)."""
    return [{k: v for k, v in r.items() if k != "crit"} for r in rows]


def _rows(spec: dict[str, Any]) -> list[dict]:
    """The SPF rows for *spec*, in model order (a phased model's carry `phase`)."""
    return _strip(single_point_failures(_model(spec)))


def _series_two() -> dict[str, Any]:
    """A two-block series: `Start -> A -> J -> B -> End`, no redundancy at
    all.

    `J` is a plain k-of-1 node (`n` 1 in, 1 out) rather than a node that
    does nothing, so the topology is the smallest one that still looks like
    a topology: one block, one node, one block. `J` is not an SPF candidate
    -- SPF asks about *elements* (blocks and events), never about nodes.
    """
    return {
        "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [
            {"name": "A", "fail_dist": FIXED, "fail_params": [50.0],
             "repair_dist": FIXED, "repair_params": [10.0]},
            {"name": "B", "fail_dist": FIXED, "fail_params": [40.0],
             "repair_dist": FIXED, "repair_params": [5.0]},
        ],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 1},
            {"name": "J", "type": 2, "n": 1, "out": 1, "k": 1},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
                  {"from": "J", "to": "B"}, {"from": "B", "to": "End"}],
    }


def _two_of_two_join() -> dict[str, Any]:
    """S2's two parallel blocks, but the `Join` between them demands
    **both** paths (`k = 2` of `n = 2`) instead of one."""
    spec = spine(2)
    for node in spec["nodes"]:
        if node["name"] == "Join":
            node["k"] = 2
    return spec


def _phased_k_of_n() -> dict[str, Any]:
    """A two-phase model whose `Join` carries a **per-phase** k column:
    `2` in phase `P1` and `1` in phase `P2`.

    `phase_states` is the spec key that writes the `P001`/`P002` cells and
    sets the `Phases` flag (`build._add_spec_phase_states` ->
    `_apply_phase_assignment`); a node's cell is one token -- `C`, `L`, or
    an integer k. Two `Fixed` phases of 60 h and 40 h make the model exactly
    the ladder's S9 phase table without S9's other eight mechanisms.
    """
    spec = spine(2)
    spec["phases"] = [
        {"name": "P1", "mission": 1, "dist": FIXED, "params": [60.0]},
        {"name": "P2", "mission": 0, "dist": FIXED, "params": [40.0]},
    ]
    spec["phase_states"] = {"Join": [2, 1]}
    return spec


# ---------------------------------------------------------------------------
# What counts as a single point failure
# ---------------------------------------------------------------------------

def test_the_only_block_of_a_series_model_is_a_single_point_failure() -> None:
    """A one-block series has exactly one SPF: the block.

    Why: SPF sets that one element to Repairing and asks whether the system
    turns Red. `A` is the only element between the
    Start marker (always feeding) and the End
    node, so failing it drops the End node's only input link and the End
    node's status to `Bad`.

    Setup: S1 -- one `Fixed` 50 h block A, Start -> A -> End.

    Expected: exactly one row, `{"kind": "Block", "id": 1, "name": "A"}`.

    Source: Raptor 7 (ReconfigureNode's status aggregation),
    Raptor 7 (the End node's up/down buckets);
    Raptor 7 (the unphased block loop)
    """
    assert _rows(spine(1)) == [
        {"kind": "Block", "id": 1, "name": "A"},
    ]


def test_a_one_of_two_parallel_stage_has_no_single_point_failures() -> None:
    """A k-of-2 stage that needs one path is not a single point failure --
    neither of its blocks is.

    Why: failing `A` leaves one good path into `Join` and `k = 1`, so
    `determine_node_status` returns `Degraded`, which is in the up-like
    bucket. The system never goes Red, so the
    source writes no row. This is the shape the
    analysis exists for: a redundant stage has no SPF.

    Setup: S2 -- A and B in parallel into a `Join` with `k = 1`, `n = 2`.

    Expected: no rows at all.

    Source: Raptor 7 (ReconfigureNode's status aggregation),
    """
    assert _rows(spine(2)) == []


def test_both_paths_of_a_two_of_two_stage_are_single_point_failures() -> None:
    """Raise the same stage's k to 2 and *both* blocks become single point
    failures.

    Why: with `k = 2`, failing either path leaves one good path, which is
    fewer than `k`, so `Join` goes `Bad`, its output link goes `Down`, the
    End node has no good path and is `Bad` -- the source's Red test at Raptor 7 is satisfied. The SPF set is a property of the
    *topology*, not of any block's own life: nothing about A or B changed
    between this test and the one above, only the node's k.

    Setup: S2 with the `Join` node's `k` set to 2.

    Expected: `A` then `B`, in model order.

Source: raptor/core/engine/spf_fev.py (`build_spf_listing`)
"""
    assert _rows(_two_of_two_join()) == [
        {"kind": "Block", "id": 1, "name": "A"},
        {"kind": "Block", "id": 2, "name": "B"},
    ]


def test_every_block_of_a_two_block_series_is_a_single_point_failure() -> None:
    """A model with no redundancy at all: every block is a single point
    failure.

    Why: with nothing in parallel, failing *either* block breaks the one
    path through the model, so the End node is `Bad` in both what-ifs. Two
    blocks, two rows -- the source walks the whole block group and writes a
    row for each element that satisfies its Red test.

    Setup: `Start -> A -> J -> B -> End`, both `Fixed`, no pool, no spare, no
    cold standby.

    Expected: `A` and `B`, in model order. `J` is never a candidate: SPF
    asks about blocks and events, not about nodes.

Source: raptor/core/engine/spf_fev.py (`build_spf_listing`)
"""
    assert _rows(_series_two()) == [
        {"kind": "Block", "id": 1, "name": "A"},
        {"kind": "Block", "id": 2, "name": "B"},
    ]


def test_an_event_in_series_behind_two_redundant_stages_is_still_a_spf_in_a_phased_model() -> None:
    """In a PHASED model an event is a candidate like any block, and one that
    no amount of upstream redundancy can cover; and a dependency counts.

    Why: the phased branch of the source really fails
    each event. `E1` sits in series *after* both redundant stages, so failing
    it drops the End node's only input. `A` is listed too, which an earlier
    version of this test (written against a static walk of the graph) did not
    expect: `B` follows `A` with 100% idle (`dep_idle`), so failing `A` idles `B`
    and the 1-of-2 `Join` goes down with it. Raptor does exactly this, because
    it fails `A` in Failure Effects View, where dependents react; four real
    files with such dependencies confirm it (`BlockDependency`, `DepHierProbe`,
    `LadL4Block`, `LadL8BEvents`).

    Setup: S10 -- A/B (B follows A) into a 1-of-2 `Join`, C/D into a cold
    group, then the event `E1` in series before End; two phases.

    Expected: `A` and `E1` in each phase, in model order (blocks then events).

    Source: Raptor 7 (the phased block and event loops)
    """
    assert _rows(spine(10)) == [
        {"kind": "Block", "id": 1, "name": "A", "phase": "P1"},
        {"kind": "Event", "id": 9, "name": "E1", "phase": "P1"},
        {"kind": "Block", "id": 1, "name": "A", "phase": "P2"},
        {"kind": "Event", "id": 9, "name": "E1", "phase": "P2"},
    ]


# ---------------------------------------------------------------------------
# The End node's k
# ---------------------------------------------------------------------------

def test_an_end_node_written_with_k_of_three_still_demands_one_good_path() -> None:
    """An End node's `k` column is ignored: it demands one good path
    whatever the file says.

    Why: the `.rbd` reader coerces `k` to 1 for every `Type = 3` node
    (as read by `typed.node_from_row`), because the
    GUI never offers K-of-N for an End node -- an End node written `k = 2`
    `n = 3` logs `2-1/3` and stays up on one good path in 100 runs of a real
    file (`LadL2Res`, real 2026-10-01). This is the one place in the SPF
    walk where the file's column is deliberately *not* the value used, so it
    is pinned here rather than left to the reader of the loader -- and pinned
    on the model's own `good_paths`, because the row list alone cannot
    distinguish "one good path required" from "always down" when every block
    is a single point failure either way.

    Setup: S1 -- one block -- with the End node's `k` written as 3.

    Expected: the built model's `NODE_DATA` row says `k = 3`, the loaded
    End node's `good_paths` is `1`, and the SPF rows are S1's one row -- so
    A is a single point failure because *it* breaks the one path, not
    because the End node was already down.

    Source: Raptor 7, read by typed.node_from_row (the `k = 1`
    coercion for `Type = 3`); Raptor 7 (the End node's
    up-like bucket, which a `good_paths` of 3 could never satisfy)
    """
    spec = spine(1)
    for node in spec["nodes"]:
        if node["name"] == "End":
            node["k"] = 3
    built = harness.run(spec)

    end_row = next(row for row in built.rbd.sections["NODE_DATA"].rows
                   if row["Name"] == "End")
    assert end_row["k"] == "3"

    from raptor.core.engine.load import load_model
    model = load_model(built.rbd)
    end = next(node for node in model.nodes_by_id.values()
               if node.type_node == 3)
    assert end.good_paths == 1
    assert _strip(single_point_failures(built.rbd)) == [
        {"kind": "Block", "id": 1, "name": "A"},
    ]


# ---------------------------------------------------------------------------
# Per-phase SPF
# ---------------------------------------------------------------------------

def test_a_phased_nodes_k_column_decides_the_spf_set_of_each_phase() -> None:
    """A phased node's per-phase `k` is what decides that phase's SPF set.

    Why: for a phased model the source re-applies each phase's
    `GoodPathsRequired` before the what-if walk (the per-phase `phase[i]` apply), which is why the phased
    listing is a list of sections rather than one list. Here the `Join`
    demands both paths in `P1` and one in `P2`, so the same two blocks are
    SPFs in the first phase and neither is in the second.

    Setup: A and B into a `Join` phased `2` then `1`, two `Fixed` phases.

    Expected: `[("P1", [A, B]), ("P2", [])]` -- sections in phase order,
    rows in model order.

    Source: Raptor 7 (the per-phase SPF sections),
    Raptor 7 (the per-phase GoodPathsRequired apply)
    """
    assert phased_single_point_failures(_model(_phased_k_of_n())) == [
        ("P1", [{"kind": "Block", "id": 1, "name": "A"},
                {"kind": "Block", "id": 2, "name": "B"}]),
        ("P2", []),
    ]


def test_the_phased_walk_leaves_the_model_file_and_a_fresh_session_untouched() -> None:
    """Running SPF is read-only for the caller.

    Why: the what-if walk changes phases, nodes and blocks of its own Failure
    Effects session; the file the caller holds must not change, and the next
    session on it must start exactly as the first did (the source restores
    phase 1 and `RefreshPhase`s after each candidate).

    Expected: the rendered file text is identical before and after, and two
    sessions' start snapshots are equal.

    Source: Raptor 7 (the phased walk and its closing
    `FEVPhaseChange(1)`), Raptor 7 (`RefreshPhase`)
    """
    from raptor.core.fileio.rbd import render_rbd
    rbd = _model(_phased_k_of_n())
    text = render_rbd(rbd)
    start = FevSession(rbd).snapshot()
    phased_single_point_failures(rbd)
    assert render_rbd(rbd) == text
    assert FevSession(rbd).snapshot() == start


# ---------------------------------------------------------------------------
# The listing itself
# ---------------------------------------------------------------------------

def test_the_listing_of_a_one_block_model_is_the_header_and_that_one_name() -> None:
    """The unphased listing is a header line, two blank lines, the column
    head `Block`, and one row per SPF.

    Why: Raptor 7 writes the header plus THREE `WriteLn` (so
    two blank lines follow it) writes the column head plus
    one `WriteLn`; each row is the element name left-justified into 20
    characters behind three spaces. The line terminator is CRLF throughout,
    because that is what the real capture contains.

    Setup: S1, one `Fixed` block, listed as `Probe.rbd` stamped
    `5 Jan 2026`.

    Expected: `'Single Point Failure File        Probe.rbd            5 Jan 2026'
    + CRLF + CRLF + CRLF + 'Block' + CRLF + '   A' + 19 spaces + CRLF`.

    Source: Raptor 7 (header and its three WriteLn),
    Raptor 7 (the column head and the rows)
    """
    listing = build_spf_listing(_model(spine(1)), "Probe.rbd", STAMP)
    assert listing == (
        "Single Point Failure File        Probe.rbd            5 Jan 2026"
        "\r\n\r\n\r\n"
        "Block\r\n"
        "   A                   \r\n"
    )


def test_a_model_with_no_single_point_failures_says_so_in_the_listing() -> None:
    """No SPF rows is a line of its own, not an empty table.

    Why: Raptor 7 writes `No single point failures` when the
    block loop produced nothing, so a redundant model produces a valid
    listing rather than a bare column head -- the absence is stated, which
    is the whole difference between "no redundancy" and "not analysed".

    Setup: S2 -- a 1-of-2 parallel stage, listed the same way.

    Expected: header, column head `Block`, then
    `   No single point failures` and the trailing CRLF.

Source: raptor/core/engine/spf_fev.py (`build_spf_listing`)
"""
    listing = build_spf_listing(_model(spine(2)), "Probe.rbd", STAMP)
    assert listing == (
        "Single Point Failure File        Probe.rbd            5 Jan 2026"
        "\r\n\r\n\r\n"
        "Block\r\n"
        "   No single point failures\r\n"
    )


def test_a_phased_model_gets_a_phase_column_and_one_section_per_phase() -> None:
    """A phased model's listing is headed `Phase`/`Block` and repeats the
    phase name on every row, with the "no failures" line per phase.

    Why: the source switches layout on `IF activePhases>0`
    (writes `   Phase                  Block` plus
    TWO `WriteLn`, the per-phase rows). A phase with no SPF
    gets its own `No single point failures` line, so the reader can tell
    "phase 2 has no single point failures" from "phase 2 was not analysed".

    Setup: the phased 2-then-1 k-of-n model, listed as `Probe.rbd`.

    Expected: header, `   Phase                  Block`, one blank, then
    `   P1                     A`, `   P1                     B`, and
    `   No single point failures` for P2.

    Source: Raptor 7 (the phased column head),
    Raptor 7 (the per-phase rows and their "no failures" line)
    """
    listing = build_spf_listing(_model(_phased_k_of_n()), "Probe.rbd", STAMP)
    assert listing == (
        "Single Point Failure File        Probe.rbd            5 Jan 2026"
        "\r\n\r\n\r\n"
        "   Phase                  Block\r\n"
        "\r\n"
        "   P1                     A                   \r\n"
        "   P1                     B                   \r\n"
        "   No single point failures\r\n"
    )


def test_the_criticality_column_is_written_only_when_asked_for() -> None:
    """The `Criticality` column appears only in the password-protected
    layout, and reads `1.000000` because the source hardcodes the factor.

    Why: the source's `critFactor := 1.0` is written
    through `REALTOSN(1.0,15,6,0)` in 15 columns. The column is not a
    computation in the port -- there is no criticality model here -- so what
    is pinned is the *layout*: the same rows, plus the column, only when the
    caller asks for it.

    Setup: one row, rendered directly (no model: the renderer takes rows).

    Expected: `'Block                  Criticality '` for the head and
    `'   A                      1.000000       '` for the row.

    Source: Raptor 7 (critFactor := 1.0), Raptor 7
    """
    rows = [{"kind": "Block", "id": 1, "name": "A"}]
    listing = render_spf_file("P.rbd", STAMP, rows, password=True)
    assert listing == (
        "Single Point Failure File        P.rbd            5 Jan 2026"
        "\r\n\r\n\r\n"
        "Block                  Criticality \r\n"
        "   A                      1.000000       \r\n"
    )


def test_the_phased_layout_carries_a_criticality_column_too_when_asked() -> None:
    """The phased layout keeps its `Criticality` column: the protected
    variant is the phased one with the column appended, not a different
    table.

    Why: the phased branch of `WriteSPFfile`
    chooses the `   Phase                  Block                  Criticality`
    head, and its per-phase row appends the same hardcoded
    `REALTOSN(1.0,15,6,0)` the unphased branch does. Pinning it
    says the phase name and the block name still both fit when the column is
    there -- which is a layout question, since neither name is ever longer
    than 20 characters.

    Setup: the phased 2-then-1 k-of-n model's own sections, rendered with
    `password=True`.

    Expected: the `   Phase                  Block                  Criticality `
    head, then P1's two rows carrying `1.000000`, then P2's
    `No single point failures` line.

    Source: Raptor 7 (critFactor := 1.0)
    """
    protected = render_spf_file(
        "Probe.rbd", STAMP, [],
        password=True,
        phase_sections=phased_single_point_failures(_model(_phased_k_of_n())),
    )
    assert protected == (
        "Single Point Failure File        Probe.rbd            5 Jan 2026"
        "\r\n\r\n\r\n"
        "   Phase                  Block                  Criticality \r\n"
        "\r\n"
        "   P1                     A                      1.000000       \r\n"
        "   P1                     B                      1.000000       \r\n"
        "   No single point failures\r\n"
    )


def test_the_date_is_the_day_the_month_and_the_year_with_no_padding() -> None:
    """`spf_date_string` renders a date as `"<day> <Mon> <year>"`, with the
    day carrying no leading zero.

    Why: Raptor 7 splits `DateTime` into a day, a three-letter
    month and a four-digit year. The source reads them out of the platform's
    own formatted string at fixed offsets; this port takes the components
    directly, which is only the same thing if a single-digit day is not
    zero-padded -- so the fifth is the day that proves it.

    Setup: a literal datetime, passed in rather than defaulted (rule B7: no
    test reads the wall clock).

    Expected: `'5 Jan 2026'`.

Source: raptor/core/engine/spf.py (`spf_date_string`)
"""
    assert spf_date_string(datetime.datetime(2026, 1, 5, 13, 45)) == "5 Jan 2026"