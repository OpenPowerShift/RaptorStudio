"""The phase-driven cluster: what a phase boundary does to a phased element.

The ladder splits the phase mechanism in two, and so does
this file. `spine(9)` is the *phase table* alone -- two phases whose second is
non-mission, with nothing phased, so the boundary has nothing to Cut or Link.
The mechanism this file pins is the one underneath: `phase_states`, the
per-element `P 001` / `P 002` cells that give a block, a node, an event or a
hierarchy its own state in each phase.

**Why one model covers so much.** A phased block's Cut/Linked state is not
just what the boundary writes: it is what *every one of the block's own state
changes* writes, because `ChangeBlockState` logs the *active* status, not the
operating status, for a block that is not Active (`blocks.change_block_state`).
So a block that is Cut while its PM comes due logs
`Cut -> PM_is_due` and then `PM_is_due -> Cut`; while its repair completes it
logs `Cut -> Repaired` and `Repaired -> Cut`; while it fails it logs
`Cut -> Failed` and `Failed -> Cut`. One phased block with a long Cut/Linked
history therefore exercises the whole `(block state) -> Cut` and `-> Linked`
column of the corpus's transition table without most of those rows being about
phases at all -- which is the honest description of where they come from.

**The eight models.** Two start from a `spine(9)`/`spine(10)` spec and add
`phase_states` on top -- the *sweep*, which phases all four of S9's blocks on
a nine-phase clock, and the *event* model, which phases two of S10's events.
Two are the same six-block spec with the phase cells swapped, so the same
instant produces the same rows twice, once Cut and once Linked. One is a
six-block model whose only job is to put two `Done` blocks and two cold-group
`Standby` members in front of a boundary. Two are a two-block model that differ
only in whether `B` can be repaired, which is what decides whether the run's
own mission close is a success or a failure. One is four blocks into a 4-of-4
node, for the node's `NodePM` aggregation.

Every model is built in memory and run through `harness.run` (B3), every
distribution is `Fixed` so the run is exact and draws nothing -- each test
asserts `Run.draws == 0` rather than hoping for it (B2) -- one replication,
six blocks and four nodes at most (B4). The literals are written **inline in
the `assert_timeline` call**, because `test_b_behaviour_coverage.py` finds them
with an `ast` pass and can only see a literal that is syntactically an argument
of the call.

**One divergence from the corpus, recorded rather than forced.** Real Raptor
logs `Idle -> Linked` when a boundary Links a block that was Idle. This port
does not: the phase change's `Phase_Change` interrupt reaches the block's Idle
wait first, the block wakes, finds itself Active again and logs
`DependenceStress=... `, and the boundary's `Linked` row lands on top of that
-- `Idle -> Good`, then `Good -> Linked`. The same boundary on the same block
with a `Cut` cell logs `Idle -> Cut` with nothing in between, which the Cut
model pins. The corpus row is `Block Idle -> Linked`, and it is the one row in
this cluster left unasserted; nothing in `tests/behaviour/` forces it.
"""
from __future__ import annotations

from typing import Any

from .harness import FIXED, assert_timeline, run
from .spine import spine

#: How many 20-hour phases the sweep models use. Nine phases give eight
#: boundaries inside a 180-hour run, enough for one block to walk its whole
#: lifecycle twice with a boundary landing in the middle of most of it.
N_PHASES = 9
PHASE_LEN = 20.0

#: Which phases are missions. The run opens in a mission (`P1`), and the
#: non-mission phases at 3, 5 and 7 are what close and re-open the mission at
#: their boundaries -- `ConductPhaseChange`'s mission-closing arm
#: and its mission-beginning tail.
MISSIONS = (1, 1, 0, 1, 0, 1, 0, 1, 1)

#: A phased cell. `build_from_spec`'s `_apply_phase_assignment` insists on the
#: `"<code>: <value>"` form for a block or event, because the loader
#: unconditionally does `float(value.partition(":")[2])` on it; a bare `"C"`
#: would partition to an empty value and crash the phase parser
#: (`raptor/api/build.py:2047-2069`).
ACTIVE = "A: 1.000"
CUT = "C: 1.000"
LINKED = "L: 1.000"

#: A phased *event* cell. The value is the event's fire value for that phase
#: -- `ConductEvent` reads `phase_value[current_phase]` as the Bernoulli
#: parameter when the phase's type is `"A"` (`raptor/core/engine/events.py:
#:70-79`), and the port writes the draw through the event's own `Fixed`
#: distribution, so `A: 1.000` is a certain success and `A: 0.000` a certain
#: failure, both draw-free. `"P"` is Prior: a phase type that does not fire the
#: event at all.
FIRE_OK = "A: 1.000"
FIRE_BAD = "A: 0.000"


def _phased(pattern: dict[str, list[str]], *, rung: int = 9,
            missions: tuple[int, ...] = MISSIONS,
            n_phases: int = N_PHASES,
            phase_len: float = PHASE_LEN,
            length: float | None = None,
            tweak: Any = None) -> dict[str, Any]:
    """`spine(rung)` re-phased as `n_phases` equal phases, with `phase_states`.

    `pattern` maps an element name to one cell per phase, e.g. `{"A": [CUT,
    LINKED, ACTIVE]}`. The phases are rebuilt here rather than reusing S9's two
    so that a model's boundaries land where its claim needs them; everything
    else is the rung's own, so the ladder's S9 literal is untouched.
    """
    spec = spine(rung)
    spec["phases"] = [
        {"name": f"P{index + 1}", "mission": mission, "dist": FIXED,
         "params": [phase_len]}
        for index, mission in enumerate(missions[:n_phases])
    ]
    spec["phase_states"] = {name: list(cells)
                            for name, cells in pattern.items()}
    if tweak is not None:
        tweak(spec)
    if length is not None:
        spec["system"]["run_length"] = float(length)
    return spec


def _spec_sweep() -> dict[str, Any]:
    """S9's four blocks, each walking Cut/Linked/Active on its own schedule."""
    return _phased(
        {
            # A: the trigger-driven PM block. Cut/Linked/Active in turn, so its
            # PM at 30, its failure at 52 and its repair at 62 all land while
            # it is not Active.
            "A": [ACTIVE, CUT, LINKED, CUT, LINKED, ACTIVE, CUT, LINKED,
                  ACTIVE],
            # B: the dependent block, and the one the one-unit spare pool
            # starves into RepHold.
            "B": [LINKED, ACTIVE, CUT, LINKED, ACTIVE, CUT, ACTIVE, CUT,
                  LINKED],
            # C: the running member of the cold group.
            "C": [CUT, LINKED, CUT, LINKED, CUT, LINKED, CUT, LINKED, CUT],
            # D: its standby member, which `RunColdLogic` promotes at C's
            # failure.
            "D": [LINKED, CUT, LINKED, CUT, LINKED, CUT, LINKED, CUT,
                  LINKED],
        },
        length=180.0)


def test_a_phased_sweep_cuts_links_and_relinks_every_block():
    """A phase boundary writes each phased block's Cut/Linked state, and the
    block's own lifecycle then keeps writing *that* state until the next
    boundary -- so `PM_is_due`, `PM_complete`, `Repaired`, `RepHold`, `Sparing`
    and `Failed` all land as `-> Cut` / `-> Linked` rows.

    Why: `ConductPhaseChange` classifies every phased block whose value or
    type differs between the old and the new phase, buckets it improve / stress
    / degrade (`phasing.classify_block_phase_transition`), and applies the
    groups in the order the system's colour selects
    (`cascade_phase._conduct_phase_change_body`). Applying a group calls
    `ChangeBlockState(oldOpStatus, CUT-or-LINKED, "Phase_Change")`, and
    `ChangeBlockState` logs the *active* status whenever the new active status
    is not Active -- so every later state change of that block, from whatever
    its operating state, logs the same `Cut`/`Linked` again. That is why the
    literal has `PM_is_due -> Cut` at t=30 and `PM_complete -> Cut` at t=32:
    the PM is not a phase transition at all, it is A's own PM landing while A
    is Cut.

    Setup: `spine(9)` (two blocks sharing a one-unit repair pool and a
    one-spare pool, B dependent on A, a trigger-driven PM on A, and a cold
    group C/D) re-phased as nine 20-hour phases, the third, fifth and seventh
    non-mission, and a 180-hour run. All four blocks carry a Cut/Linked/Active
    pattern. Every distribution is `Fixed`, so the run draws nothing.

    Expected: at each of the eight boundaries every phased block whose phase
    cell differs logs its own `X -> Cut` or `X -> Linked`, and the non-phased
    machinery moves exactly as it does in S9: the trigger fires at 30/60/90/120,
    the pool is `Sparing` at 40/80/120/160, the cold group promotes D at C's
    failure at 50, and the run ends with every block `Done`. Eight
    `System:Phase_change` boundaries, the mission closed at each non-mission
    one and re-opened at the next, and one `Mission_success` at the run end.

    Source: Raptor 7 (`ControlPhaseChanges`, the clock and its
    `Begin_phase_*` line), Raptor 7 (`ConductPhaseChange`),
    Raptor 7 (`ChangePhases`, whose tail runs unconditionally and
    whose state-transition half is the only thing that logs `Phase_Change`),
    Raptor 7 (`ChangeBlockState`'s active-status log);
    docs/engine-design/events-and-phases.adoc (Purpose, Source anchors,
    Structure, Ordering at a boundary)
    """
    result = run(_spec_sweep())

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Linked'),
        (0, 'Block', 'C', '--', 'Cut'),
        (0, 'Block', 'C', 'Cut', 'Cut'),
        (0, 'Block', 'D', '--', 'Linked'),
        (0, 'Node', 'Cold', '--', 'Degraded'),
        (20, 'System', 'Phase_change', '--', 'Phase_change'),
        (20, 'Block', 'C', 'Cut', 'Linked'),
        (20, 'Block', 'A', 'Good', 'Cut'),
        (20, 'Block', 'B', 'Linked', 'Linked'),
        (20, 'Node', 'Join', '--', 'Degraded'),
        (20, 'Block', 'B', 'Linked', 'Idle'),
        (20, 'Node', 'Join', 'Degraded', 'Idle'),
        (20, 'Node', 'end', '--', 'Idle'),
        (20, 'System', 'State_change', 'Mission', 'Red'),
        (20, 'Block', 'D', 'Linked', 'Cut'),
        (20, 'Block', 'D', 'Cut', 'Cut'),
        (20, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (30, 'Trigger', 'T1', '--', 'Fired'),
        (30, 'Block', 'A', 'Cut', 'PM_is_due'),
        (30, 'Block', 'A', 'PM_is_due', 'Cut'),
        (30, 'System', 'Red_change', '--', 'Red'),
        (32, 'Block', 'A', 'Cut', 'PM_complete'),
        (32, 'Block', 'A', 'PM_complete', 'Cut'),
        (40, 'Pool', 'SP', '--', 'Sparing'),
        (40, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (40, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (40, 'System', 'State_change', 'Red', 'Red'),
        (40, 'Block', 'B', 'Idle', 'Cut'),
        (40, 'Node', 'Join', 'Idle', 'Bad'),
        (40, 'Node', 'end', 'Idle', 'Bad'),
        (40, 'Block', 'C', 'Linked', 'Cut'),
        (40, 'Block', 'A', 'Cut', 'Linked'),
        (40, 'Block', 'B', 'Cut', 'Cut'),
        (40, 'Node', 'Join', 'Bad', 'Degraded'),
        (40, 'Node', 'end', 'Bad', 'Good'),
        (40, 'Block', 'D', 'Cut', 'Linked'),
        (40, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (40, 'System', 'State_change', 'Red', 'Green'),
        (52, 'Block', 'A', 'Linked', 'Failed'),
        (52, 'Block', 'A', 'Failed', 'Linked'),
        (52, 'System', 'State_change', 'Green', 'Yellow'),
        (60, 'Trigger', 'T1', 'Fired', 'Fired'),
        (60, 'System', 'Phase_change', 'Mission_failure', 'Phase_change'),
        (60, 'Block', 'B', 'Cut', 'Linked'),
        (60, 'Node', 'Join', 'Degraded', 'Good'),
        (60, 'Block', 'C', 'Cut', 'Linked'),
        (60, 'Block', 'A', 'Linked', 'Cut'),
        (60, 'Block', 'B', 'Linked', 'Linked'),
        (60, 'Node', 'Join', 'Good', 'Degraded'),
        (60, 'Block', 'D', 'Linked', 'Cut'),
        (60, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (60, 'System', 'State_change', 'Yellow', 'Yellow'),
        (62, 'Block', 'A', 'Cut', 'Repaired'),
        (62, 'Block', 'A', 'Repaired', 'Cut'),
        (80, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (80, 'System', 'Phase_change', 'Mission', 'Phase_change'),
        (80, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (80, 'System', 'State_change', 'Yellow', 'Yellow'),
        (80, 'Block', 'A', 'Cut', 'Linked'),
        (80, 'Block', 'B', 'Linked', 'Linked'),
        (80, 'Node', 'Join', 'Degraded', 'Good'),
        (80, 'Block', 'D', 'Cut', 'Linked'),
        (80, 'Block', 'B', 'Linked', 'Good'),
        (80, 'Block', 'C', 'Linked', 'Cut'),
        (80, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (80, 'System', 'State_change', 'Yellow', 'Green'),
        (90, 'Trigger', 'T1', 'Fired', 'Fired'),
        (90, 'Block', 'A', 'Linked', 'PM_is_due'),
        (90, 'Block', 'A', 'PM_is_due', 'Linked'),
        (90, 'System', 'State_change', 'Green', 'Yellow'),
        (92, 'Block', 'A', 'Linked', 'PM_complete'),
        (92, 'Block', 'A', 'PM_complete', 'Linked'),
        (92, 'System', 'State_change', 'Yellow', 'Green'),
        (95, 'Block', 'B', 'Good', 'Failed'),
        (95, 'Block', 'B', 'Failed', 'Sparing'),
        (95, 'Block', 'B', 'Sparing', 'RepHold'),
        (95, 'Node', 'Join', 'Good', 'Degraded'),
        (95, 'System', 'State_change', 'Green', 'Yellow'),
        (100, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (100, 'Block', 'C', 'Cut', 'Linked'),
        (100, 'Block', 'A', 'Linked', 'Good'),
        (100, 'Block', 'B', 'RepHold', 'Cut'),
        (100, 'Block', 'D', 'Linked', 'Cut'),
        (100, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (100, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (100, 'System', 'State_change', 'Yellow', 'Yellow'),
        (114, 'Block', 'A', 'Good', 'Failed'),
        (114, 'Block', 'A', 'Failed', 'Sparing'),
        (114, 'Block', 'A', 'Sparing', 'RepHold'),
        (114, 'Node', 'Join', 'Degraded', 'Bad'),
        (114, 'Node', 'end', 'Good', 'Bad'),
        (114, 'System', 'State_change', 'Yellow', 'Red'),
        (120, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (120, 'Trigger', 'T1', 'Fired', 'Fired'),
        (120, 'System', 'Phase_change', 'Mission', 'Phase_change'),
        (120, 'Block', 'B', 'Cut', 'Sparing'),
        (120, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (120, 'System', 'State_change', 'Red', 'Red'),
        (120, 'Block', 'A', 'RepHold', 'Cut'),
        (120, 'Block', 'B', 'Sparing', 'Cut'),
        (120, 'Block', 'C', 'Linked', 'Cut'),
        (120, 'Block', 'B', 'Cut', 'Repairing'),
        (120, 'Block', 'D', 'Cut', 'Linked'),
        (120, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (120, 'System', 'Red_change', 'Red', 'Red'),
        (125, 'Block', 'B', 'Repairing', 'Repaired'),
        (125, 'Block', 'B', 'Repaired', 'Idle'),
        (125, 'Node', 'Join', 'Bad', 'Idle'),
        (125, 'Node', 'end', 'Bad', 'Idle'),
        (125, 'System', 'Red_change', 'Red', 'Red'),
        (140, 'System', 'Phase_change', 'Mission_failure', 'Phase_change'),
        (140, 'Block', 'B', 'Idle', 'Cut'),
        (140, 'Node', 'Join', 'Idle', 'Bad'),
        (140, 'Node', 'end', 'Idle', 'Bad'),
        (140, 'Block', 'D', 'Linked', 'Cut'),
        (140, 'Block', 'A', 'Cut', 'Linked'),
        (140, 'Block', 'B', 'Cut', 'Cut'),
        (140, 'Node', 'Join', 'Bad', 'Degraded'),
        (140, 'Node', 'end', 'Bad', 'Good'),
        (140, 'Block', 'C', 'Cut', 'Linked'),
        (140, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (140, 'System', 'State_change', 'Red', 'Yellow'),
        (140, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (140, 'System', 'State_change', 'Yellow', 'Yellow'),
        (150, 'Trigger', 'T1', 'Fired', 'Fired'),
        (160, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (160, 'System', 'Phase_change', 'Mission', 'Phase_change'),
        (160, 'Block', 'A', 'Linked', 'Sparing'),
        (160, 'Block', 'B', 'Cut', 'Linked'),
        (160, 'Node', 'Join', 'Degraded', 'Good'),
        (160, 'Block', 'A', 'Sparing', 'Linked'),
        (160, 'Block', 'D', 'Cut', 'Linked'),
        (160, 'Block', 'A', 'Linked', 'Repairing'),
        (160, 'Block', 'B', 'Linked', 'Linked'),
        (160, 'Node', 'Join', 'Good', 'Degraded'),
        (160, 'Block', 'C', 'Linked', 'Cut'),
        (160, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (170, 'Block', 'A', 'Repairing', 'Repaired'),
        (170, 'Block', 'A', 'Repaired', 'Good'),
        (170, 'Block', 'B', 'Linked', 'Linked'),
        (170, 'Node', 'Join', 'Degraded', 'Good'),
        (170, 'System', 'State_change', 'Yellow', 'Green'),
        (180, 'Trigger', 'T1', 'Fired', 'Fired'),
        (180, 'Block', 'A', 'Good', 'PM_is_due'),
        (180, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (180, 'Block', 'A', 'pmReqDefer', 'Done'),
        (180, 'Block', 'B', 'Linked', 'Linked'),
        (180, 'Block', 'C', 'Cut', 'Cut'),
        (180, 'Block', 'D', 'Linked', 'Linked'),
        (180, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (180, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="phased sweep")


def _spec_events() -> dict[str, Any]:
    """S10's event, two of them in series, each on its own phase schedule."""
    fire = [FIRE_BAD, CUT, FIRE_BAD, FIRE_OK, CUT, FIRE_OK, FIRE_BAD, FIRE_BAD,
            FIRE_OK, CUT]
    prior = ["P: 0.500", CUT, FIRE_OK, FIRE_OK, CUT, FIRE_BAD, FIRE_BAD,
             LINKED, FIRE_BAD, FIRE_BAD]

    def two_events(spec: dict[str, Any]) -> None:
        for link in spec["links"]:
            if link["from"] == "E1" and link["to"] == "End":
                link["to"] = "E2"
        spec["events"].append({"name": "E2", "dist": FIXED, "failVal": 1.0})
        spec["links"].append({"from": "E2", "to": "End"})

    return _phased({"E1": fire, "E2": prior}, rung=10, n_phases=10,
                   missions=(1, 1, 0, 1, 0, 1, 0, 1, 1, 1), length=200.0,
                   tweak=two_events)


def test_a_phased_event_refires_on_every_phase_where_its_fire_value_moves():
    """A phased event is re-fired by the boundary whenever its phase type or
    its fire value differs from the phase it is leaving -- so one event's own
    `Armed`, `Success` and `Failure` rows chain into each other, and `Cut` and
    `Linked` interleave with them.

    Why: `ConductPhaseChange` buckets a phased event with
    `classify_event_phase_transition` (and, when nothing differs but the event
    is still `"A"` or `"F"`, with `classify_event_same_value_refire`, which
    still redraws). An `A`/`F` target fires the event unconditionally, so the
    boundary logs `Cut -> Failed` or `Failed -> Success` exactly as an
    ordinary firing would; a `C`/`L` target only moves its active status. The
    fire value that decides the outcome is the *phase's* value when the phase
    type is `"A"`, and `ConductEvent` reads `draw == 1.0` as success, so a
    `Fixed` event whose phase cell is `A: 0.000` fails for certain and one
    whose cell is `A: 1.000` succeeds for certain -- neither costs a draw.
    The run's own `EndSimulation` sweep is what writes the last two rows,
    `Cut -> Cut` and `Failed -> Armed`: it puts every event back to `Armed`
    while keeping its active status, so an event that is Cut when the clock
    stops logs `Cut`, not `Armed`.

    Setup: `spine(10)` (S9's blocks plus one event in series before the End
    node) with a **second** event spliced in behind the first, ten 20-hour
    phases, and a 200-hour run. `E1` fires certain-failure / certain-success
    on a `Fire, Cut, Fire, Fire, Cut, ...` schedule; `E2` opens in a `Prior`
    phase, so it is still `Armed` when the first boundary Cuts it. Both events
    keep `dist = Fixed`.

    Expected: `E1` goes `Armed -> Failed` at t=0, then
    `Failed -> Cut`, `Cut -> Failed`, `Failed -> Success`, `Success -> Cut`,
    `Cut -> Success`, `Success -> Failed`, `Failed -> Failed`,
    `Failed -> Success`, `Success -> Cut` at the nine boundaries, and
    `Cut -> Cut` at the run end. `E2` stays `Armed` through phase 1 and then
    walks `Armed -> Cut`, `Cut -> Success`, `Success -> Success`,
    `Success -> Cut`, `Cut -> Failed`, `Failed -> Failed`, `Failed -> Linked`,
    `Linked -> Failed`, `Failed -> Failed`, and `Failed -> Armed` at the run
    end. Twelve of the corpus's thirteen `Event` rows.

    Source: Raptor 7 (`ConductPhaseChange`'s event bucketing),
    Raptor 7 (the same-value refire), Raptor 7
    (`ConductEvent`'s own dispatch table), Raptor 7
    (`fireVals[1]:=phaseValue[phaseNumber]` when the phase type is `"A"`),
    Raptor 7 (`ChangeEventState`), Raptor 7
    (`EndSimulation`'s event sweep);
    docs/engine-design/events-and-phases.adoc (Purpose, Source anchors,
    Structure)
    """
    result = run(_spec_events())

    assert result.draws == 0, (
        "both events are Fixed, so a certain success or a certain failure "
        f"costs no draw; this run drew {result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Good'),
        (0, 'Block', 'D', '--', 'Good'),
        (0, 'Event', 'E1', '--', 'Armed'),
        (0, 'Event', 'E1', 'Armed', 'Failed'),
        (0, 'Node', 'end', '--', 'Bad'),
        (0, 'System', 'State_change', 'Mission', 'Red'),
        (0, 'Event', 'E2', '--', 'Armed'),
        (0, 'Block', 'D', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (20, 'System', 'Phase_change', '--', 'Phase_change'),
        (20, 'Event', 'E1', 'Failed', 'Cut'),
        (20, 'Event', 'E2', 'Armed', 'Cut'),
        (30, 'Trigger', 'T1', '--', 'Fired'),
        (30, 'Block', 'A', 'Good', 'PM_is_due'),
        (30, 'Block', 'A', 'PM_is_due', 'PM'),
        (30, 'Block', 'B', 'Good', 'Idle'),
        (30, 'Node', 'Join', '--', 'Degraded'),
        (30, 'Node', 'Join', 'Degraded', 'Idle'),
        (30, 'System', 'Red_change', '--', 'Red'),
        (32, 'Block', 'A', 'PM', 'PM_complete'),
        (32, 'Block', 'A', 'PM_complete', 'Good'),
        (32, 'Block', 'B', 'Idle', 'Good'),
        (32, 'Node', 'Join', 'Idle', 'Degraded'),
        (32, 'Node', 'Join', 'Degraded', 'Good'),
        (32, 'System', 'Red_change', 'Red', 'Red'),
        (40, 'Pool', 'SP', '--', 'Sparing'),
        (40, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (40, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (40, 'System', 'State_change', 'Red', 'Red'),
        (40, 'Event', 'E1', 'Cut', 'Failed'),
        (40, 'Event', 'E2', 'Cut', 'Success'),
        (50, 'Block', 'C', 'Good', 'Failed'),
        (50, 'Block', 'C', 'Failed', 'Repairing'),
        (50, 'Block', 'D', 'Standby', 'Good'),
        (50, 'Node', 'Cold', 'Good', 'Degraded'),
        (50, 'System', 'Red_change', 'Red', 'Red'),
        (52, 'Block', 'A', 'Good', 'Failed'),
        (52, 'Block', 'A', 'Failed', 'Repairing'),
        (52, 'Block', 'B', 'Good', 'Idle'),
        (52, 'Node', 'Join', 'Good', 'Degraded'),
        (52, 'Node', 'Join', 'Degraded', 'Idle'),
        (52, 'System', 'Red_change', 'Red', 'Red'),
        (60, 'Trigger', 'T1', 'Fired', 'Fired'),
        (60, 'System', 'Phase_change', 'Mission_failure', 'Phase_change'),
        (60, 'Block', 'C', 'Repairing', 'Repaired'),
        (60, 'Block', 'C', 'Repaired', 'Good'),
        (60, 'Block', 'C', 'Good', 'Standby'),
        (60, 'Node', 'Cold', 'Degraded', 'Good'),
        (60, 'Event', 'E2', 'Success', 'Success'),
        (60, 'Event', 'E1', 'Failed', 'Success'),
        (60, 'Node', 'end', 'Bad', 'Idle'),
        (60, 'System', 'Red_change', 'Red', 'Red'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (60, 'System', 'State_change', 'Red', 'Red'),
        (60, 'System', 'Red_change', 'Red', 'Red'),
        (62, 'Block', 'A', 'Repairing', 'Repaired'),
        (62, 'Block', 'A', 'Repaired', 'Good'),
        (62, 'Block', 'B', 'Idle', 'Good'),
        (62, 'Node', 'Join', 'Idle', 'Degraded'),
        (62, 'Node', 'end', 'Idle', 'Good'),
        (62, 'Node', 'Join', 'Degraded', 'Good'),
        (62, 'System', 'State_change', 'Red', 'Green'),
        (67, 'Block', 'B', 'Good', 'Failed'),
        (67, 'Block', 'B', 'Failed', 'Sparing'),
        (67, 'Block', 'B', 'Sparing', 'RepHold'),
        (67, 'Node', 'Join', 'Good', 'Degraded'),
        (67, 'System', 'State_change', 'Green', 'Yellow'),
        (80, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (80, 'System', 'Phase_change', 'Mission', 'Phase_change'),
        (80, 'Block', 'B', 'RepHold', 'Sparing'),
        (80, 'Block', 'B', 'Sparing', 'Repairing'),
        (80, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (80, 'System', 'State_change', 'Yellow', 'Yellow'),
        (80, 'Event', 'E1', 'Success', 'Cut'),
        (80, 'Node', 'end', 'Good', 'Bad'),
        (80, 'System', 'State_change', 'Yellow', 'Red'),
        (80, 'Event', 'E2', 'Success', 'Cut'),
        (85, 'Block', 'B', 'Repairing', 'Repaired'),
        (85, 'Block', 'B', 'Repaired', 'Good'),
        (85, 'Node', 'Join', 'Degraded', 'Good'),
        (85, 'System', 'Red_change', 'Red', 'Red'),
        (90, 'Trigger', 'T1', 'Fired', 'Fired'),
        (90, 'Block', 'A', 'Good', 'PM_is_due'),
        (90, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (100, 'System', 'Phase_change', 'Mission_failure', 'Phase_change'),
        (100, 'Event', 'E1', 'Cut', 'Success'),
        (100, 'Event', 'E2', 'Cut', 'Failed'),
        (100, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (100, 'System', 'State_change', 'Red', 'Red'),
        (112, 'Block', 'A', 'pmReqDefer', 'Failed'),
        (112, 'Block', 'A', 'Failed', 'Sparing'),
        (112, 'Block', 'A', 'Sparing', 'RepHold'),
        (112, 'Block', 'B', 'Good', 'Idle'),
        (112, 'Node', 'Join', 'Good', 'Degraded'),
        (112, 'Node', 'Join', 'Degraded', 'Idle'),
        (112, 'System', 'Red_change', 'Red', 'Red'),
        (120, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (120, 'Trigger', 'T1', 'Fired', 'Fired'),
        (120, 'System', 'Phase_change', 'Mission', 'Phase_change'),
        (120, 'Block', 'A', 'RepHold', 'Sparing'),
        (120, 'Block', 'A', 'Sparing', 'Repairing'),
        (120, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (120, 'System', 'State_change', 'Red', 'Red'),
        (120, 'Event', 'E1', 'Success', 'Failed'),
        (120, 'Event', 'E2', 'Failed', 'Failed'),
        (120, 'System', 'Red_change', 'Red', 'Red'),
        (130, 'Block', 'A', 'Repairing', 'Repaired'),
        (130, 'Block', 'A', 'Repaired', 'Good'),
        (130, 'Block', 'B', 'Idle', 'Good'),
        (130, 'Node', 'Join', 'Idle', 'Degraded'),
        (130, 'Node', 'Join', 'Degraded', 'Good'),
        (130, 'System', 'Red_change', 'Red', 'Red'),
        (140, 'System', 'Phase_change', 'Mission_failure', 'Phase_change'),
        (140, 'Event', 'E1', 'Failed', 'Failed'),
        (140, 'Event', 'E2', 'Failed', 'Linked'),
        (140, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (140, 'System', 'State_change', 'Red', 'Red'),
        (150, 'Trigger', 'T1', 'Fired', 'Fired'),
        (150, 'Block', 'A', 'Good', 'PM_is_due'),
        (150, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (158, 'Block', 'B', 'Good', 'Failed'),
        (158, 'Block', 'B', 'Failed', 'Sparing'),
        (158, 'Block', 'B', 'Sparing', 'RepHold'),
        (158, 'Node', 'Join', 'Good', 'Degraded'),
        (158, 'System', 'Red_change', 'Red', 'Red'),
        (160, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (160, 'System', 'Phase_change', 'Mission', 'Phase_change'),
        (160, 'Block', 'A', 'pmReqDefer', 'Sparing'),
        (160, 'Event', 'E2', 'Linked', 'Failed'),
        (160, 'Block', 'A', 'Sparing', 'PM'),
        (160, 'Node', 'Join', 'Degraded', 'NodePM'),
        (160, 'Event', 'E1', 'Failed', 'Success'),
        (160, 'System', 'Red_change', 'Red', 'Red'),
        (162, 'Block', 'A', 'PM', 'PM_complete'),
        (162, 'Block', 'A', 'PM_complete', 'Good'),
        (162, 'Node', 'Join', 'NodePM', 'Degraded'),
        (162, 'System', 'Red_change', 'Red', 'Red'),
        (180, 'Trigger', 'T1', 'Fired', 'Fired'),
        (180, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (180, 'Block', 'A', 'Good', 'PM_is_due'),
        (180, 'Event', 'E1', 'Success', 'Cut'),
        (180, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (180, 'Event', 'E2', 'Failed', 'Failed'),
        (182, 'Block', 'A', 'pmReqDefer', 'Failed'),
        (182, 'Block', 'A', 'Failed', 'Sparing'),
        (182, 'Block', 'A', 'Sparing', 'RepHold'),
        (182, 'Node', 'Join', 'Degraded', 'Bad'),
        (182, 'System', 'Red_change', 'Red', 'Red'),
        (200, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (200, 'Block', 'B', 'RepHold', 'Sparing'),
        (200, 'Block', 'B', 'Sparing', 'Repairing'),
        (200, 'Block', 'A', 'RepHold', 'Done'),
        (200, 'Block', 'B', 'Repairing', 'Done'),
        (200, 'Block', 'C', 'Standby', 'Done'),
        (200, 'Block', 'D', 'Good', 'Done'),
        (200, 'Event', 'E1', 'Cut', 'Cut'),
        (200, 'Event', 'E2', 'Failed', 'Armed'),
        (200, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (200, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="phased events")

# ---------------------------------------------------------------------------
# The boundary sweep: one block per state the boundary has to land in
# ---------------------------------------------------------------------------

#: The one-unit repair pool the two resource-using blocks contend for. `U` holds
#: it across its whole repair, which is what parks `Q` in `RepHold` from t=40
#: until t=80 -- a boundary landing in that window is the `RepHold -> Cut` /
#: `RepHold -> Linked` row.
POOL = "R"

#: `SpType = "None"` is how a block is given no sparing at all. `R`'s failure
#: then goes `Failed -> Done` and its link stays down for the rest of the run,
#: which is the `Done -> Cut` / `Done -> Linked` row at the next boundary.
NO_SPARES = {"sp_type": "None"}

#: The four phases the sweep models use, and the two that do nothing. Phases 1
#: and 2 share the cell `"A: 1.000"`, so the boundary at t=20 classifies no
#: change and logs nothing -- which is what puts every block in a *different*
#: operating state at the t=60 boundary.
SWEEP_PHASES = (20.0, 40.0, 30.0, 30.0)


def _block(name: str, fail: float, repair: float, **patch: Any) -> dict[str, Any]:
    """One `Fixed`-life block: fails at *fail*, repaired *repair* later."""
    return {"name": name, "fail_dist": FIXED, "fail_params": [float(fail)],
            "repair_dist": FIXED, "repair_params": [float(repair)], **patch}


def _four_phases(pattern: dict[str, list[str]], run_length: float,
                 resources: bool = False) -> dict[str, Any]:
    """A six-block model with four phases whose cells come from *pattern*."""
    missions = (1, 1, 0, 1)
    spec: dict[str, Any] = {
        "system": {"run_length": float(run_length), "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            # `P` is the PM block: a 40-hour PM starting at t=30, so the
            # t=60 boundary lands with `P` in PM and stays there until t=70.
            # It borrows no resource, so it PMs even though `R` is held.
            _block("P", 300.0, 5.0, uses_pm=True, pm_freq=30.0,
                   pm_dist=FIXED, pm_params=[40.0], pm_refresh=False),
            # `Q` fails at t=40 into `RepHold`: `U` holds the only repair
            # resource unit from its own failure at t=10 until t=80.
            _block("Q", 40.0, 30.0, num_res=1, resource_name=POOL),
            # `U` fails at t=10 and is `Repairing` across the t=60 boundary;
            # its 70-hour repair is followed by a `Fixed` 10-hour post-LDT
            # wait, which is what puts an `End_Post-LDT` line under the t=90
            # boundary.
            _block("U", 10.0, 70.0, num_res=1, resource_name=POOL,
                   post_dist=FIXED, post_params=[10.0]),
            # `R` fails at t=25 with nothing to draw on: `Done`, permanently.
            _block("R", 25.0, 5.0, **NO_SPARES),
            # `V` wants the same one-unit PM resource, so its t=30 PM cannot
            # start: with `PMReqDefer` off it parks in `PMhold` -- the
            # `pmHold` row -- until `U` hands the unit back at t=80.
            _block("V", 300.0, 5.0, uses_pm=True, pm_freq=30.0,
                   pm_dist=FIXED, pm_params=[40.0], pm_refresh=False,
                   num_res=1, resource_name=POOL, pm_req_defer=False),
            # `W` wants it too but keeps `PMReqDefer` on, so its t=30 PM is
            # *deferred* instead of parked: `PM_is_due -> pmReqDefer`, then a
            # `Resources` line when `U` hands the unit back at t=80 -- which
            # is the row the t=90 boundary writes over.
            _block("W", 300.0, 5.0, uses_pm=True, pm_freq=30.0,
                   pm_dist=FIXED, pm_params=[40.0], pm_refresh=False,
                   num_res=1, resource_name=POOL),
        ],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 6},
            {"name": "J", "type": 2, "n": 6, "out": 1, "k": 1},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": name}
                  for name in _SWEEP_BLOCKS]
                 + [{"from": name, "to": "J"} for name in _SWEEP_BLOCKS]
                 + [{"from": "J", "to": "End"}],
        "phases": [
            {"name": f"P{index + 1}", "mission": mission, "dist": FIXED,
             "params": [length]}
            for index, (mission, length)
            in enumerate(zip(missions, SWEEP_PHASES))
        ],
        "phase_states": dict(pattern),
    }
    if resources:
        spec["resources"] = [{"name": POOL, "num_in": 1}]
    return spec


#: The six blocks the sweep models phase, in the order `_four_phases` builds
#: them -- which is the order the builder numbers them, so block ids are
#: `P`=1, `Q`=2, `U`=3, `R`=4, `V`=5, `W`=6.
_SWEEP_BLOCKS = ("P", "Q", "U", "R", "V", "W")


def _spec_straggler_cut() -> dict[str, Any]:
    """The sweep with a **Cut** boundary at t=60 and a Linked one at t=90."""
    cells = [ACTIVE, ACTIVE, CUT, LINKED]
    return _four_phases({name: list(cells) for name in _SWEEP_BLOCKS},
                        run_length=110.0, resources=True)


def test_a_cut_boundary_lands_on_a_different_operating_state_in_each_block():
    """Put each of six blocks in a different operating state and the t=60
    boundary writes `Cut` over all six: `PM -> Cut`, `RepHold -> Cut`,
    `Repairing -> Cut`, `Done -> Cut`, `pmHold -> Cut` and
    `pmReqDefer -> Cut` in one instant.

    Why: `ConductPhaseChange` applies each block's `ChangePhases` group with
    `ChangeBlockState(oldOpStatus, CUT, "Phase_Change", ..., top_level=FALSE)`,
    and `ChangeBlockState` logs the *active* status for a block that is not
    Active, so the `old` column of that row is whatever the block last logged
    -- its PM, its `RepHold`, its `Standby`, or the `Failed` line
    `DetermineNewState` writes before it changes state. The operating status
    itself is untouched: `P` is still in PM at t=60 and completes it at t=70,
    still logging `Cut`, and `U` is still `Repairing` until its own t=80
    repair ends. `top_level=FALSE` matters too: five blocks change in the same
    instant and there is exactly **one** system-status line afterwards, not one
    per block.

    Setup: six `Fixed` blocks into a 1-of-6 `Join`, all six after the same
    one-unit resource `R`: a PM block that borrows nothing and is therefore in
    a 40-hour PM spanning t=30..70; a block that fails at t=40 and parks in
    `RepHold` behind the unit; a block that fails at t=10, holds the unit
    across the boundary and then waits out a `Fixed` 10-hour post-LDT; a block
    with no sparing that goes `Done` at t=25; and two more PM blocks that both
    want the unit at t=30 -- one with `PMReqDefer` off, which parks in
    `PMhold`, and one with it on, which defers instead. Four phases
    (20, 40, 30, 30 h) with cells `Active, Active, Cut, Linked`, so the t=20
    boundary is a no-op and the t=60 one is the claim.

    Expected: at t=60 `PM -> Cut`, `RepHold -> Cut`, `Repairing -> Cut`,
    `Done -> Cut`, `pmHold -> Cut` and `pmReqDefer -> Cut`; between the
    boundaries every block's own events re-log `Cut` (`Cut -> Failed` at t=25,
    `Cut -> PM_is_due` at t=30, `Cut -> PM_complete` at t=70,
    `Cut -> Repaired` and `Cut -> Resources` at t=80); `U`'s post-LDT line
    lands exactly on the t=90 boundary, which reads `Cut -> End_Post-LDT`,
    `End_Post-LDT -> Cut`, then `Cut -> Linked` for every block; and the run
    ends with six `Cut -> Cut` rows.

    Source: Raptor 7 (`ConductPhaseChange`),
    Raptor 7 (`ChangePhases`),
    Raptor 7 (`ChangeBlockState`'s active-status log),
    Raptor 7 (`ChangeBlockState` sets `SystemStateChange`
    unconditionally); docs/engine-design/events-and-phases.adoc (Structure)
    """
    result = run(_spec_straggler_cut())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'P', '--', 'Good'),
        (0, 'Block', 'Q', '--', 'Good'),
        (0, 'Block', 'U', '--', 'Good'),
        (0, 'Block', 'R', '--', 'Good'),
        (0, 'Block', 'V', '--', 'Good'),
        (0, 'Block', 'W', '--', 'Good'),
        (10, 'Block', 'U', 'Good', 'Failed'),
        (10, 'Block', 'U', 'Failed', 'Repairing'),
        (10, 'Node', 'J', '--', 'Degraded'),
        (10, 'System', 'State_change', 'Mission', 'Yellow'),
        (20, 'System', 'Phase_change', '--', 'Phase_change'),
        (25, 'Block', 'R', 'Good', 'Failed'),
        (25, 'Block', 'R', 'Failed', 'Done'),
        (25, 'Node', 'J', 'Degraded', 'Degraded'),
        (30, 'Block', 'P', 'Good', 'PM_is_due'),
        (30, 'Block', 'V', 'Good', 'PM_is_due'),
        (30, 'Block', 'W', 'Good', 'PM_is_due'),
        (30, 'Block', 'V', 'PM_is_due', 'pmHold'),
        (30, 'Node', 'J', 'Degraded', 'Degraded'),
        (30, 'Block', 'W', 'PM_is_due', 'pmReqDefer'),
        (30, 'Block', 'P', 'PM_is_due', 'PM'),
        (30, 'Node', 'J', 'Degraded', 'Degraded'),
        (40, 'Block', 'Q', 'Good', 'Failed'),
        (40, 'Block', 'Q', 'Failed', 'RepHold'),
        (40, 'Node', 'J', 'Degraded', 'Degraded'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (60, 'System', 'State_change', 'Yellow', 'Yellow'),
        (60, 'Block', 'P', 'PM', 'Cut'),
        (60, 'Node', 'J', 'Degraded', 'Degraded'),
        (60, 'Block', 'Q', 'RepHold', 'Cut'),
        (60, 'Block', 'U', 'Repairing', 'Cut'),
        (60, 'Block', 'R', 'Done', 'Cut'),
        (60, 'Block', 'V', 'pmHold', 'Cut'),
        (60, 'Node', 'J', 'Degraded', 'Degraded'),
        (60, 'Block', 'W', 'pmReqDefer', 'Cut'),
        (60, 'Node', 'J', 'Degraded', 'Bad'),
        (60, 'Node', 'end', '--', 'Bad'),
        (60, 'System', 'State_change', 'Yellow', 'Red'),
        (70, 'Block', 'P', 'Cut', 'PM_complete'),
        (70, 'Block', 'P', 'PM_complete', 'Cut'),
        (80, 'Block', 'U', 'Cut', 'Repaired'),
        (80, 'Block', 'U', 'Repaired', 'Cut'),
        (80, 'Block', 'V', 'Cut', 'Resources'),
        (80, 'Block', 'V', 'Resources', 'Cut'),
        (90, 'Block', 'U', 'Cut', 'End_Post-LDT'),
        (90, 'Block', 'U', 'End_Post-LDT', 'Cut'),
        (90, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (90, 'Block', 'P', 'Cut', 'Linked'),
        (90, 'Node', 'J', 'Bad', 'Degraded'),
        (90, 'Node', 'end', 'Bad', 'Good'),
        (90, 'Block', 'Q', 'Cut', 'Linked'),
        (90, 'Node', 'J', 'Degraded', 'Degraded'),
        (90, 'Block', 'U', 'Cut', 'Linked'),
        (90, 'Node', 'J', 'Degraded', 'Degraded'),
        (90, 'Block', 'R', 'Cut', 'Linked'),
        (90, 'Node', 'J', 'Degraded', 'Degraded'),
        (90, 'Block', 'V', 'Cut', 'Linked'),
        (90, 'Node', 'J', 'Degraded', 'Degraded'),
        (90, 'Block', 'W', 'Cut', 'Linked'),
        (90, 'Node', 'J', 'Degraded', 'Good'),
        (90, 'System', 'State_change', 'Red', 'Yellow'),
        (90, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (90, 'System', 'State_change', 'Yellow', 'Yellow'),
        (100, 'Block', 'P', 'Linked', 'PM_is_due'),
        (100, 'Block', 'U', 'Linked', 'Failed'),
        (100, 'Block', 'U', 'Failed', 'Linked'),
        (100, 'Block', 'P', 'PM_is_due', 'Linked'),
        (110, 'Block', 'P', 'Linked', 'Linked'),
        (110, 'Block', 'Q', 'Linked', 'Linked'),
        (110, 'Block', 'U', 'Linked', 'Linked'),
        (110, 'Block', 'R', 'Linked', 'Linked'),
        (110, 'Block', 'V', 'Linked', 'Linked'),
        (110, 'Block', 'W', 'Linked', 'Linked'),
        (110, 'System', 'Phase_change', 'Mission', 'Mission_success'),
        (110, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="cut boundary")


def _spec_straggler_linked() -> dict[str, Any]:
    """The same model with **Linked** at t=60 and Cut at t=90."""
    cells = [ACTIVE, ACTIVE, LINKED, CUT]
    return _four_phases({name: list(cells) for name in _SWEEP_BLOCKS},
                        run_length=110.0, resources=True)


def test_a_linked_boundary_lands_on_a_different_operating_state_in_each_block():
    """The same six blocks, the same instant, and `Linked` over all six
    instead of `Cut` -- the direction of the phase change, not its kind, is
    what the block's own lifecycle then keeps logging.

    Why: `classify_block_phase_transition` buckets `"A" -> "L"` as `improve`
    and `"A" -> "C"` as `degrade`, and the two groups are applied in opposite
    orders depending on the system's colour -- but the *log line* is identical
    in shape, because `ChangeBlockState` picks the string from the new active
    status alone. So every `(state) -> Cut` row the Cut model pins has a
    `(state) -> Linked` twin here, in the same instant and the same order,
    which is what makes the pair a claim about the mechanism rather than about
    one hand-picked model.

    Setup: the Cut model's spec with the phase cells swapped: `Active, Active,
    Linked, Cut`. Every distribution `Fixed`.

    Expected: at t=60 `PM -> Linked`, `RepHold -> Linked`,
    `Repairing -> Linked`, `Done -> Linked`, `pmHold -> Linked` and
    `pmReqDefer -> Linked`; the blocks' own events re-log `Linked` in between
    (`Linked -> Failed` at t=25, `Linked -> PM_is_due` at t=30,
    `Linked -> PM_complete` at t=70, `Linked -> Repaired` and
    `Linked -> Resources` at t=80); and at t=90 `Linked -> End_Post-LDT`,
    `End_Post-LDT -> Linked`, then `Linked -> Cut` for every block.

    Source: Raptor 7 (`ConductPhaseChange`'s Cut/Link/Active
    bucketing), Raptor 7 (`ConductPhaseChange`, including the
    colour-selected group order), Raptor 7 (`ChangePhases`),
    Raptor 7 (`ChangeBlockState`'s active-status log);
    docs/engine-design/events-and-phases.adoc (Structure, Purpose)
    """
    result = run(_spec_straggler_linked())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'P', '--', 'Good'),
        (0, 'Block', 'Q', '--', 'Good'),
        (0, 'Block', 'U', '--', 'Good'),
        (0, 'Block', 'R', '--', 'Good'),
        (0, 'Block', 'V', '--', 'Good'),
        (0, 'Block', 'W', '--', 'Good'),
        (10, 'Block', 'U', 'Good', 'Failed'),
        (10, 'Block', 'U', 'Failed', 'Repairing'),
        (10, 'Node', 'J', '--', 'Degraded'),
        (10, 'System', 'State_change', 'Mission', 'Yellow'),
        (20, 'System', 'Phase_change', '--', 'Phase_change'),
        (25, 'Block', 'R', 'Good', 'Failed'),
        (25, 'Block', 'R', 'Failed', 'Done'),
        (25, 'Node', 'J', 'Degraded', 'Degraded'),
        (30, 'Block', 'P', 'Good', 'PM_is_due'),
        (30, 'Block', 'V', 'Good', 'PM_is_due'),
        (30, 'Block', 'W', 'Good', 'PM_is_due'),
        (30, 'Block', 'V', 'PM_is_due', 'pmHold'),
        (30, 'Node', 'J', 'Degraded', 'Degraded'),
        (30, 'Block', 'W', 'PM_is_due', 'pmReqDefer'),
        (30, 'Block', 'P', 'PM_is_due', 'PM'),
        (30, 'Node', 'J', 'Degraded', 'Degraded'),
        (40, 'Block', 'Q', 'Good', 'Failed'),
        (40, 'Block', 'Q', 'Failed', 'RepHold'),
        (40, 'Node', 'J', 'Degraded', 'Degraded'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (60, 'System', 'State_change', 'Yellow', 'Yellow'),
        (60, 'Block', 'P', 'PM', 'Linked'),
        (60, 'Node', 'J', 'Degraded', 'Degraded'),
        (60, 'Block', 'Q', 'RepHold', 'Linked'),
        (60, 'Node', 'J', 'Degraded', 'Degraded'),
        (60, 'Block', 'U', 'Repairing', 'Linked'),
        (60, 'Node', 'J', 'Degraded', 'Degraded'),
        (60, 'Block', 'R', 'Done', 'Linked'),
        (60, 'Node', 'J', 'Degraded', 'Degraded'),
        (60, 'Block', 'V', 'pmHold', 'Linked'),
        (60, 'Node', 'J', 'Degraded', 'Good'),
        (60, 'Block', 'W', 'pmReqDefer', 'Linked'),
        (70, 'Block', 'P', 'Linked', 'PM_complete'),
        (70, 'Block', 'P', 'PM_complete', 'Linked'),
        (80, 'Block', 'U', 'Linked', 'Repaired'),
        (80, 'Block', 'U', 'Repaired', 'Linked'),
        (80, 'Block', 'V', 'Linked', 'Resources'),
        (80, 'Block', 'V', 'Resources', 'Linked'),
        (90, 'Block', 'U', 'Linked', 'End_Post-LDT'),
        (90, 'Block', 'U', 'End_Post-LDT', 'Linked'),
        (90, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (90, 'Block', 'P', 'Linked', 'Cut'),
        (90, 'Node', 'J', 'Good', 'Degraded'),
        (90, 'Block', 'Q', 'Linked', 'Cut'),
        (90, 'Node', 'J', 'Degraded', 'Degraded'),
        (90, 'Block', 'U', 'Linked', 'Cut'),
        (90, 'Node', 'J', 'Degraded', 'Degraded'),
        (90, 'Block', 'R', 'Linked', 'Cut'),
        (90, 'Node', 'J', 'Degraded', 'Degraded'),
        (90, 'Block', 'V', 'Linked', 'Cut'),
        (90, 'Node', 'J', 'Degraded', 'Degraded'),
        (90, 'Block', 'W', 'Linked', 'Cut'),
        (90, 'Node', 'J', 'Degraded', 'Bad'),
        (90, 'Node', 'end', '--', 'Bad'),
        (90, 'System', 'State_change', 'Yellow', 'Red'),
        (90, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (90, 'System', 'State_change', 'Red', 'Red'),
        (100, 'Block', 'P', 'Cut', 'PM_is_due'),
        (100, 'Block', 'U', 'Cut', 'Failed'),
        (100, 'Block', 'U', 'Failed', 'Cut'),
        (100, 'Block', 'P', 'PM_is_due', 'Cut'),
        (110, 'Block', 'P', 'Cut', 'Cut'),
        (110, 'Block', 'Q', 'Cut', 'Cut'),
        (110, 'Block', 'U', 'Cut', 'Cut'),
        (110, 'Block', 'R', 'Cut', 'Cut'),
        (110, 'Block', 'V', 'Cut', 'Cut'),
        (110, 'Block', 'W', 'Cut', 'Cut'),
        (110, 'System', 'Phase_change', 'Mission', 'Mission_failure'),
        (110, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="linked boundary")


def _spec_done_standby() -> dict[str, Any]:
    """Two `Done` blocks and two `Standby` blocks, each with a different
    target cell, so the one t=60 boundary produces `Done -> Cut`,
    `Done -> Linked`, `Standby -> Cut` and `Standby -> Linked` together."""
    return {
        "system": {"run_length": 110.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [
            # `W` runs the cold group; it never fails, so neither standby
            # member is ever promoted and both stay `Standby` throughout.
            _block("W", 300.0, 5.0),
            _block("T1", 300.0, 5.0),
            _block("T2", 300.0, 5.0),
            # Two blocks with nothing to draw on, so both are `Done` by t=35.
            _block("R1", 25.0, 5.0, **NO_SPARES),
            _block("R2", 35.0, 5.0, **NO_SPARES),
            # `G` is an ordinary block, phased the other way round from `W`, so
            # the one boundary also supplies `Good -> Linked`.
            _block("G", 300.0, 5.0),
        ],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 4},
            {"name": "Cold", "type": 2, "n": 3, "out": 1, "k": 1,
             "cold": 1, "k_star": 1, "pr": 0, "autos": 0},
            {"name": "End", "type": 3, "n": 4, "out": 0},
        ],
        "links": [
            {"from": "Start", "to": "W"},
            {"from": "W", "to": "Cold", "cold_pri": 10, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "T1", "to": "Cold", "cold_pri": 20, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "T2", "to": "Cold", "cold_pri": 30, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "Cold", "to": "End"},
            {"from": "Start", "to": "R1"}, {"from": "R1", "to": "End"},
            {"from": "Start", "to": "R2"}, {"from": "R2", "to": "End"},
            {"from": "Start", "to": "G"}, {"from": "G", "to": "End"},
        ],
        "phases": [
            {"name": f"P{index + 1}", "mission": mission, "dist": FIXED,
             "params": [length]}
            for index, (mission, length)
            in enumerate(zip((1, 1, 0, 1), SWEEP_PHASES))
        ],
        # The two standby members and the two `Done` blocks are given
        # *opposite* target cells, because a block's logged state after a
        # boundary is its new active status: one block per cell can only ever
        # reach one of the two, so it takes two to pin both.
        "phase_states": {
            "W":  [ACTIVE, ACTIVE, CUT, LINKED],
            "T1": [ACTIVE, ACTIVE, CUT, LINKED],
            "T2": [ACTIVE, ACTIVE, LINKED, CUT],
            "R1": [ACTIVE, ACTIVE, LINKED, CUT],
            "R2": [ACTIVE, ACTIVE, CUT, LINKED],
            "G":  [ACTIVE, ACTIVE, LINKED, CUT],
        },
    }


def test_a_boundary_reaches_a_done_block_and_a_cold_standby_member():
    """One boundary, four blocks, four different rows: `Done -> Cut`,
    `Done -> Linked`, `Standby -> Cut` and `Standby -> Linked`.

    Why: neither status is a special case for a phase change. `Done` and
    `Standby` are operating statuses like any other; the boundary's
    `ChangePhases` group calls `ChangeBlockState` with the new *active* status
    and whatever the old operating status happened to be, and the log line
    shows the active one -- `ChangeBlockState` picks `_ACTIVE_STRING` over
    `_STATUS_STRING` whenever the new active status is not `Active`. So a
    block that is `Done` because it has no sparing and a member of a cold
    group that was demoted at kickoff are both simply "a block whose last
    logged line was X", and the boundary writes over X the same way.

    **Why two blocks per status.** After a boundary a block's logged state is
    its *new* active status, so one block can only ever produce one of the two
    rows: give it `Cut` and it can never again be `Standby -> Linked`. Each
    status therefore needs a block with the other target cell, and that is the
    whole reason this model has five blocks and two `Done` ones.

    Setup: six `Fixed` blocks. `W`, `T1` and `T2` feed a cold group of 3
    (`k=1`, `kStar=1`, `ColdPri` 10/20/30, `AProb=1.0`, `AutoTime=0`), so `T1`
    and `T2` are demoted to `Standby` at kickoff -- and stay there, because
    `W` never fails and `RunColdLogic`'s survey never finds room to promote
    either of them. `R1` and `R2` have `SpType = "None"`, fail at t=25 and t=35
    and are `Done` from then on. Four phases (20, 40, 30, 30 h); `T1`/`R2` are
    phased `Active, Active, Cut, Linked` and `T2`/`R1` the opposite.

    Expected: at t=60 `Standby -> Linked` and `Done -> Linked` for `T2` and
    `R1`, then `Good -> Cut`, `Standby -> Cut` and `Done -> Cut` for `W`, `T1`
    and `R2`; at t=90 `Cut -> Linked` for `W`, `T1` and `R2` and
    `Linked -> Cut` for `T2` and `R1`; the run ends with two `Cut -> Cut` and
    three `Linked -> Linked` rows. The mission opened at t=0 closes at t=60 as
    a success and the one opened at t=90 closes at the run end as
    `Mission_success`, the system being `Yellow` throughout.

    Source: Raptor 7 (`ConductPhaseChange`),
    Raptor 7 (`ChangePhases`),
    Raptor 7 (`RunColdLogic`'s promotion, and its lack here),
    Raptor 7 (`CheckForSpare`'s `SpType = None` arm);
    docs/engine-design/events-and-phases.adoc (Structure),
    node-status-and-cold-standby.adoc (Purpose)
    """
    result = run(_spec_done_standby())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'W', '--', 'Good'),
        (0, 'Block', 'T1', '--', 'Good'),
        (0, 'Block', 'T2', '--', 'Good'),
        (0, 'Block', 'R1', '--', 'Good'),
        (0, 'Block', 'R2', '--', 'Good'),
        (0, 'Block', 'G', '--', 'Good'),
        (0, 'Block', 'T1', 'Good', 'Standby'),
        (0, 'Block', 'T2', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (20, 'System', 'Phase_change', '--', 'Phase_change'),
        (25, 'Block', 'R1', 'Good', 'Failed'),
        (25, 'Block', 'R1', 'Failed', 'Done'),
        (25, 'Node', 'end', '--', 'Degraded'),
        (25, 'System', 'State_change', 'Mission', 'Yellow'),
        (35, 'Block', 'R2', 'Good', 'Failed'),
        (35, 'Block', 'R2', 'Failed', 'Done'),
        (35, 'Node', 'end', 'Degraded', 'Degraded'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (60, 'System', 'State_change', 'Yellow', 'Yellow'),
        (60, 'Block', 'T2', 'Standby', 'Linked'),
        (60, 'Block', 'R1', 'Done', 'Linked'),
        (60, 'Node', 'end', 'Degraded', 'Degraded'),
        (60, 'Block', 'G', 'Good', 'Linked'),
        (60, 'Block', 'W', 'Good', 'Cut'),
        (60, 'Block', 'W', 'Cut', 'Cut'),
        (60, 'Block', 'T1', 'Standby', 'Cut'),
        (60, 'Block', 'R2', 'Done', 'Cut'),
        (60, 'Node', 'Cold', 'Good', 'Degraded'),
        (90, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (90, 'Block', 'W', 'Cut', 'Linked'),
        (90, 'Block', 'T1', 'Cut', 'Linked'),
        (90, 'Block', 'R2', 'Cut', 'Linked'),
        (90, 'Node', 'end', 'Degraded', 'Good'),
        (90, 'Block', 'T2', 'Linked', 'Cut'),
        (90, 'Block', 'R1', 'Linked', 'Cut'),
        (90, 'Node', 'end', 'Good', 'Degraded'),
        (90, 'Block', 'G', 'Linked', 'Cut'),
        (90, 'Node', 'end', 'Degraded', 'Degraded'),
        (90, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (90, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (90, 'System', 'State_change', 'Yellow', 'Yellow'),
        (110, 'Block', 'W', 'Linked', 'Linked'),
        (110, 'Block', 'T1', 'Linked', 'Linked'),
        (110, 'Block', 'T2', 'Cut', 'Cut'),
        (110, 'Block', 'R1', 'Cut', 'Cut'),
        (110, 'Block', 'R2', 'Linked', 'Linked'),
        (110, 'Block', 'G', 'Cut', 'Cut'),
        (110, 'System', 'Phase_change', 'Mission', 'Mission_success'),
        (110, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="done and standby")


# ---------------------------------------------------------------------------
# Missions: what the run's own end does to a mission
# ---------------------------------------------------------------------------

def _mission_model(repairable: bool) -> dict[str, Any]:
    """Two `Fixed` blocks, three phases (30 mission, 30 non-mission, 40
    mission), and `B` repairable or not -- the only difference between the two
    mission models.
    """
    b = _block("B", 50.0, 5.0) if repairable else _block(
        "B", 50.0, 5.0, **NO_SPARES)
    return {
        "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [_block("A", 15.0, 5.0), b],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 2},
            {"name": "J", "type": 2, "n": 2, "out": 1, "k": 1},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
                  {"from": "Start", "to": "B"}, {"from": "B", "to": "J"},
                  {"from": "J", "to": "End"}],
        "phases": [
            {"name": "P1", "mission": 1, "dist": FIXED, "params": [30.0]},
            {"name": "P2", "mission": 0, "dist": FIXED, "params": [30.0]},
            {"name": "P3", "mission": 1, "dist": FIXED, "params": [40.0]},
        ],
        "phase_states": {"A": [ACTIVE] * 3, "B": [ACTIVE] * 3},
    }


def _spec_mission_green() -> dict[str, Any]:
    """The mission model with `B` repairable."""
    return _mission_model(repairable=True)


def test_a_mission_that_begins_green_closes_as_a_success_at_the_run_end():
    """A run that ends *inside* a mission closes it on the run's own
    `ChangeStatus(DDone, NonMission)`, and records the outcome the mission
    began with -- a mission that began while the system was not Red is a
    success.

    Why: the run-end close is `ASK System TO ChangeStatus(DDone, NonMission)`,
   and it runs *after* the ordinary per-boundary close,
    so the `Mission_success` line it writes is the third kind of mission row:
    the boundary wrote `Phase_change -> Phase_change` and then
    `Phase_change -> Mission_success`, and the run's end writes
    `Mission -> Mission_success` -- the `Mission` being the row the t=60
    boundary wrote when it *opened* the mission. Whether that is a success is
    decided when the mission opens, not when it closes: `ChangeStatus` sets
    `missionSuccess:=(newColor<>RRed)` on the way in.

    Setup: two `Fixed` blocks into a 1-of-2 `Join`, `A` failing at t=15 and
    repaired at t=20, `B` at t=50 with a 5-hour repair. Three phases: 30 h
    mission, 30 h non-mission, 40 h mission; a 100-hour run, so the clock stops
    inside the third phase.

    Expected: `A`'s failure at t=15 takes the system `Yellow` while the mission
    that opened at t=0 is still running, which is the `Mission -> Yellow` row;
    the t=30 boundary closes that mission and re-opens one at t=60, each
    writing a `State_change` row for a colour that did not move (`Green ->
    Green` at t=30 and again at t=60 -- the mission-status change alone is
    enough to make `ChangeStatus` write); and the run's end closes the t=60
    mission `Mission -> Mission_success`, because it began while the system was
    `Yellow`.

    Source: Raptor 7 (`EndSimulation`'s `ChangeStatus(DDone,
    NonMission)`), Raptor 7 (`ChangeStatus`'s mission CASE, and
    the `missionSuccess:=(newColor<>RRed)` decision), Raptor 7
    (`ConductPhaseChange`, whose mission-closing and mission-beginning arms
    pass the *cached* colour, which is why a mission boundary emits a
    `State_change` line without a colour change),
    Raptor 7 (`ChangeStatus`'s `State_change` tail);
    docs/engine-design/events-and-phases.adoc (Structure)
    """
    result = run(_mission_model(repairable=True))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (15, 'Block', 'A', 'Good', 'Failed'),
        (15, 'Block', 'A', 'Failed', 'Repairing'),
        (15, 'Node', 'J', '--', 'Degraded'),
        (15, 'System', 'State_change', 'Mission', 'Yellow'),
        (20, 'Block', 'A', 'Repairing', 'Repaired'),
        (20, 'Block', 'A', 'Repaired', 'Good'),
        (20, 'Node', 'J', 'Degraded', 'Good'),
        (20, 'System', 'State_change', 'Yellow', 'Green'),
        (30, 'System', 'Phase_change', '--', 'Phase_change'),
        (30, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (30, 'System', 'State_change', 'Green', 'Green'),
        (35, 'Block', 'A', 'Good', 'Failed'),
        (35, 'Block', 'A', 'Failed', 'Repairing'),
        (35, 'Node', 'J', 'Good', 'Degraded'),
        (35, 'System', 'State_change', 'Green', 'Yellow'),
        (40, 'Block', 'A', 'Repairing', 'Repaired'),
        (40, 'Block', 'A', 'Repaired', 'Good'),
        (40, 'Node', 'J', 'Degraded', 'Good'),
        (40, 'System', 'State_change', 'Yellow', 'Green'),
        (50, 'Block', 'B', 'Good', 'Failed'),
        (50, 'Block', 'B', 'Failed', 'Repairing'),
        (50, 'Node', 'J', 'Good', 'Degraded'),
        (50, 'System', 'State_change', 'Green', 'Yellow'),
        (55, 'Block', 'A', 'Good', 'Failed'),
        (55, 'Block', 'B', 'Repairing', 'Repaired'),
        (55, 'Block', 'B', 'Repaired', 'Good'),
        (55, 'Node', 'J', 'Degraded', 'Good'),
        (55, 'Block', 'A', 'Failed', 'Repairing'),
        (55, 'Node', 'J', 'Good', 'Degraded'),
        (60, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (60, 'Block', 'A', 'Repairing', 'Repaired'),
        (60, 'Block', 'A', 'Repaired', 'Good'),
        (60, 'Node', 'J', 'Degraded', 'Good'),
        (60, 'System', 'State_change', 'Yellow', 'Green'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (60, 'System', 'State_change', 'Green', 'Green'),
        (75, 'Block', 'A', 'Good', 'Failed'),
        (75, 'Block', 'A', 'Failed', 'Repairing'),
        (75, 'Node', 'J', 'Good', 'Degraded'),
        (75, 'System', 'State_change', 'Green', 'Yellow'),
        (80, 'Block', 'A', 'Repairing', 'Repaired'),
        (80, 'Block', 'A', 'Repaired', 'Good'),
        (80, 'Node', 'J', 'Degraded', 'Good'),
        (80, 'System', 'State_change', 'Yellow', 'Green'),
        (95, 'Block', 'A', 'Good', 'Failed'),
        (95, 'Block', 'A', 'Failed', 'Repairing'),
        (95, 'Node', 'J', 'Good', 'Degraded'),
        (95, 'System', 'State_change', 'Green', 'Yellow'),
        (100, 'Block', 'A', 'Repairing', 'Repaired'),
        (100, 'Block', 'A', 'Repaired', 'Good'),
        (100, 'Node', 'J', 'Degraded', 'Good'),
        (100, 'Block', 'A', 'Good', 'Done'),
        (100, 'Block', 'B', 'Good', 'Done'),
        (100, 'System', 'State_change', 'Yellow', 'Green'),
        (100, 'System', 'Phase_change', 'Mission', 'Mission_success'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="mission that began Green")


def _spec_mission_red() -> dict[str, Any]:
    """The mission model with `B` given no sparing, so it never repairs."""
    return _mission_model(repairable=False)


def test_a_mission_that_begins_red_closes_as_a_failure_at_the_run_end():
    """The same model with `B` given no sparing: the mission that opens at the
    t=60 boundary opens while the system is `Red`, and the run's end therefore
    records `Mission -> Mission_failure`.

    Why: the same `ChangeStatus` arm as the Green model, and the same
    `missionSuccess:=(newColor<>RRed)` line -- the only thing the missing spare
    changes is that `B` never repairs, so the system is still `Red` when the
    t=60 boundary begins the second mission. This is also why `B`'s own
    failure is `Failed -> Done` rather than `Failed -> Repairing`: a block with
    no sparing cannot be repaired, so `CheckForSpare` takes it straight to
    `Done`.

    Setup: the Green model's spec with `B SpType = "None"`, everything else --
    including the three phases and the 100-hour run -- unchanged.

    Expected: the same `Mission -> Yellow` at t=15 and `Green -> Green` at t=30,
    then at t=60 `Yellow -> Green` as `B` goes `Done`, `Phase_change ->
    Mission` for the new mission and `Yellow -> Yellow` for the mission-status
    change that produced it; and at the run end `Mission -> Mission_failure`,
    because that mission opened while the system was `Red`.

    Source: Raptor 7 (the run-end `ChangeStatus(DDone, NonMission)`),
    Raptor 7 (`missionSuccess:=(newColor<>RRed)` at the mission's
    beginning), Raptor 7 (`CheckForSpare`'s `SpType = None` arm),
    Raptor 7 (`ConductPhaseChange`'s mission-beginning tail);
    docs/engine-design/events-and-phases.adoc (Structure)
    """
    result = run(_mission_model(repairable=False))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (15, 'Block', 'A', 'Good', 'Failed'),
        (15, 'Block', 'A', 'Failed', 'Repairing'),
        (15, 'Node', 'J', '--', 'Degraded'),
        (15, 'System', 'State_change', 'Mission', 'Yellow'),
        (20, 'Block', 'A', 'Repairing', 'Repaired'),
        (20, 'Block', 'A', 'Repaired', 'Good'),
        (20, 'Node', 'J', 'Degraded', 'Good'),
        (20, 'System', 'State_change', 'Yellow', 'Green'),
        (30, 'System', 'Phase_change', '--', 'Phase_change'),
        (30, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (30, 'System', 'State_change', 'Green', 'Green'),
        (35, 'Block', 'A', 'Good', 'Failed'),
        (35, 'Block', 'A', 'Failed', 'Repairing'),
        (35, 'Node', 'J', 'Good', 'Degraded'),
        (35, 'System', 'State_change', 'Green', 'Yellow'),
        (40, 'Block', 'A', 'Repairing', 'Repaired'),
        (40, 'Block', 'A', 'Repaired', 'Good'),
        (40, 'Node', 'J', 'Degraded', 'Good'),
        (40, 'System', 'State_change', 'Yellow', 'Green'),
        (50, 'Block', 'B', 'Good', 'Failed'),
        (50, 'Block', 'B', 'Failed', 'Done'),
        (50, 'Node', 'J', 'Good', 'Degraded'),
        (50, 'System', 'State_change', 'Green', 'Yellow'),
        (55, 'Block', 'A', 'Good', 'Failed'),
        (55, 'Block', 'A', 'Failed', 'Repairing'),
        (55, 'Node', 'J', 'Degraded', 'Bad'),
        (55, 'Node', 'end', '--', 'Bad'),
        (55, 'System', 'State_change', 'Yellow', 'Red'),
        (60, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (60, 'Block', 'A', 'Repairing', 'Repaired'),
        (60, 'Block', 'A', 'Repaired', 'Good'),
        (60, 'Node', 'J', 'Bad', 'Degraded'),
        (60, 'Node', 'end', 'Bad', 'Good'),
        (60, 'System', 'State_change', 'Red', 'Yellow'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (60, 'System', 'State_change', 'Yellow', 'Yellow'),
        (75, 'Block', 'A', 'Good', 'Failed'),
        (75, 'Block', 'A', 'Failed', 'Repairing'),
        (75, 'Node', 'J', 'Degraded', 'Bad'),
        (75, 'Node', 'end', 'Good', 'Bad'),
        (75, 'System', 'State_change', 'Yellow', 'Red'),
        (80, 'Block', 'A', 'Repairing', 'Repaired'),
        (80, 'Block', 'A', 'Repaired', 'Good'),
        (80, 'Node', 'J', 'Bad', 'Degraded'),
        (80, 'Node', 'end', 'Bad', 'Good'),
        (80, 'System', 'State_change', 'Red', 'Yellow'),
        (95, 'Block', 'A', 'Good', 'Failed'),
        (95, 'Block', 'A', 'Failed', 'Repairing'),
        (95, 'Node', 'J', 'Degraded', 'Bad'),
        (95, 'Node', 'end', 'Good', 'Bad'),
        (95, 'System', 'State_change', 'Yellow', 'Red'),
        (100, 'Block', 'A', 'Repairing', 'Repaired'),
        (100, 'Block', 'A', 'Repaired', 'Good'),
        (100, 'Node', 'J', 'Bad', 'Degraded'),
        (100, 'Node', 'end', 'Bad', 'Good'),
        (100, 'Block', 'A', 'Good', 'Done'),
        (100, 'Block', 'B', 'Done', 'Done'),
        (100, 'System', 'State_change', 'Red', 'Yellow'),
        (100, 'System', 'Phase_change', 'Mission', 'Mission_failure'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="mission that began Red")


# ---------------------------------------------------------------------------
# A phased block's effect on the node behind it
# ---------------------------------------------------------------------------

def _spec_nodes() -> dict[str, Any]:
    """Four blocks into a 4-of-4 node, two of them in overlapping long PMs.

    `J`'s k is its own path count, so it is `Good` only while all four links
    are `LinkUp`; one `LinkPM` is enough to take it to `NodePM`, and the node
    stays `NodePM` for as long as `pm + idle + running + standby >= k`. `C` is
    phased `Cut` for the middle phase, which is what drives the node back out
    of `Bad` and into `NodePM` at the t=60 boundary.
    """
    return {
        "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [
            # A 80-hour PM from t=10: A's link is `LinkPM` for most of the run.
            _block("A", 300.0, 5.0, uses_pm=True, pm_freq=10.0, pm_dist=FIXED,
                   pm_params=[80.0], pm_refresh=False),
            # The only block that ever fails: a 5-hour life, so it is down for
            # five hours out of every twenty-five.
            _block("B", 20.0, 5.0),
            # The phased block: Cut for phase 2, Linked for phase 3.
            _block("C", 300.0, 5.0),
            # A second PM, offset by two hours, so both are in progress at once.
            _block("D", 300.0, 5.0, uses_pm=True, pm_freq=12.0, pm_dist=FIXED,
                   pm_params=[80.0], pm_refresh=False),
        ],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 4},
            {"name": "J", "type": 2, "n": 4, "out": 1, "k": 4},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": name} for name in "ABCD"]
                 + [{"from": name, "to": "J"} for name in "ABCD"]
                 + [{"from": "J", "to": "End"}],
        "phases": [
            {"name": "P1", "mission": 1, "dist": FIXED, "params": [20.0]},
            {"name": "P2", "mission": 0, "dist": FIXED, "params": [40.0]},
            {"name": "P3", "mission": 1, "dist": FIXED, "params": [40.0]},
        ],
        "phase_states": {"C": [ACTIVE, CUT, LINKED]},
    }


def test_a_node_whose_k_is_its_path_count_is_node_pm_while_a_block_is_in_pm():
    """A node that needs *every* one of its paths reports `NodePM` the moment
    one of them goes into PM -- and stays `NodePM`, logging `NodePM -> NodePM`,
    for as long as the PM lasts.

    Why: `determine_node_status` aggregates the
    incoming *link* statuses, and a block in PM sets its outgoing link to
    `LinkPM`. Its k-of-N ladder puts the PM bucket last of the "enough paths"
    tests: `Idle` wins while `idle + running + standby >= k`, and `NodePM`
    while only `pm + idle + running + standby >= k`. With `k` equal to the
    path count, one `LinkPM` leaves three running and one PM -- three is below
    k, four is not -- so the node is `NodePM` and not `Degraded`, and a second
    `LinkPM` leaves it exactly where it is, which is the
    `NodePM -> NodePM` row. `C`'s Cut for the whole of phase 2 is what holds
    the node in `Bad` until the t=60 boundary Links it again.

    Setup: four `Fixed` blocks into a 4-of-4 `J`. `A` has a 40-hour PM
    frequency and a `Fixed` 80-hour PM duration, so its link is `LinkPM` from
    t=10 to t=90; `D` the same from t=12 to t=92; `B` a 20-hour life with a
    5-hour repair; `C` phased `Active, Cut, Linked` over phases of 20, 40 and
    40 hours.

    Expected: `J -- -> NodePM` at t=10 as `A` enters PM, `NodePM -> NodePM` at
    t=12 as `D` joins it and again at t=90 as `A` leaves it, `NodePM -> Bad` at
    each of `B`'s failures, `Bad -> Bad` throughout phase 2 while `C` is Cut,
    and `Bad -> NodePM` at the t=60 boundary that Links `C` again.

    Source: Raptor 7 (`determine_node_status`'s buckets and
    their order), Raptor 7 (`ReconfigureNode`'s link-status
    aggregation), Raptor 7 (`ConductPhaseChange`),
    Raptor 7 (`ChangePhases`);
    docs/engine-design/node-status-and-cold-standby.adoc (Purpose, Source
    anchors), events-and-phases.adoc (Structure)
    """
    result = run(_spec_nodes())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Good'),
        (0, 'Block', 'D', '--', 'Good'),
        (10, 'Block', 'A', 'Good', 'PM_is_due'),
        (10, 'Block', 'A', 'PM_is_due', 'PM'),
        (10, 'Node', 'J', '--', 'NodePM'),
        (10, 'Node', 'end', '--', 'NodePM'),
        (10, 'System', 'State_change', 'Mission', 'Red'),
        (12, 'Block', 'D', 'Good', 'PM_is_due'),
        (12, 'Block', 'D', 'PM_is_due', 'PM'),
        (12, 'Node', 'J', 'NodePM', 'NodePM'),
        (12, 'System', 'Red_change', '--', 'Red'),
        (20, 'Block', 'B', 'Good', 'Failed'),
        (20, 'System', 'Phase_change', '--', 'Phase_change'),
        (20, 'Block', 'B', 'Failed', 'Repairing'),
        (20, 'Node', 'J', 'NodePM', 'Bad'),
        (20, 'Node', 'end', 'NodePM', 'Bad'),
        (20, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (20, 'System', 'State_change', 'Red', 'Red'),
        (20, 'Block', 'C', 'Good', 'Cut'),
        (20, 'Node', 'J', 'Bad', 'Bad'),
        (20, 'System', 'Red_change', 'Red', 'Red'),
        (25, 'Block', 'B', 'Repairing', 'Repaired'),
        (25, 'Block', 'B', 'Repaired', 'Good'),
        (25, 'Node', 'J', 'Bad', 'Bad'),
        (25, 'System', 'Red_change', 'Red', 'Red'),
        (45, 'Block', 'B', 'Good', 'Failed'),
        (45, 'Block', 'B', 'Failed', 'Repairing'),
        (45, 'Node', 'J', 'Bad', 'Bad'),
        (45, 'System', 'Red_change', 'Red', 'Red'),
        (50, 'Block', 'B', 'Repairing', 'Repaired'),
        (50, 'Block', 'B', 'Repaired', 'Good'),
        (50, 'Node', 'J', 'Bad', 'Bad'),
        (50, 'System', 'Red_change', 'Red', 'Red'),
        (60, 'System', 'Phase_change', 'Mission_failure', 'Phase_change'),
        (60, 'Block', 'C', 'Cut', 'Linked'),
        (60, 'Node', 'J', 'Bad', 'NodePM'),
        (60, 'Node', 'end', 'Bad', 'NodePM'),
        (60, 'System', 'Red_change', 'Red', 'Red'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (60, 'System', 'State_change', 'Red', 'Red'),
        (60, 'System', 'Red_change', 'Red', 'Red'),
        (70, 'Block', 'B', 'Good', 'Failed'),
        (70, 'Block', 'B', 'Failed', 'Repairing'),
        (70, 'Node', 'J', 'NodePM', 'Bad'),
        (70, 'Node', 'end', 'NodePM', 'Bad'),
        (70, 'System', 'Red_change', 'Red', 'Red'),
        (75, 'Block', 'B', 'Repairing', 'Repaired'),
        (75, 'Block', 'B', 'Repaired', 'Good'),
        (75, 'Node', 'J', 'Bad', 'NodePM'),
        (75, 'Node', 'end', 'Bad', 'NodePM'),
        (75, 'System', 'Red_change', 'Red', 'Red'),
        (90, 'Block', 'A', 'PM', 'PM_complete'),
        (90, 'Block', 'A', 'PM_complete', 'Good'),
        (90, 'Node', 'J', 'NodePM', 'NodePM'),
        (90, 'System', 'Red_change', 'Red', 'Red'),
        (92, 'Block', 'D', 'PM', 'PM_complete'),
        (92, 'Block', 'D', 'PM_complete', 'Good'),
        (92, 'Node', 'J', 'NodePM', 'Good'),
        (92, 'Node', 'end', 'NodePM', 'Good'),
        (92, 'System', 'State_change', 'Red', 'Green'),
        (95, 'Block', 'B', 'Good', 'Failed'),
        (95, 'Block', 'B', 'Failed', 'Repairing'),
        (95, 'Node', 'J', 'Good', 'Bad'),
        (95, 'Node', 'end', 'Good', 'Bad'),
        (95, 'System', 'State_change', 'Green', 'Red'),
        (100, 'Block', 'A', 'Good', 'PM_is_due'),
        (100, 'Block', 'B', 'Repairing', 'Repaired'),
        (100, 'Block', 'B', 'Repaired', 'Good'),
        (100, 'Node', 'J', 'Bad', 'Good'),
        (100, 'Node', 'end', 'Bad', 'Good'),
        (100, 'Block', 'A', 'PM_is_due', 'PM'),
        (100, 'Node', 'J', 'Good', 'NodePM'),
        (100, 'Node', 'end', 'Good', 'NodePM'),
        (100, 'Block', 'A', 'PM', 'Done'),
        (100, 'Block', 'B', 'Good', 'Done'),
        (100, 'Block', 'C', 'Linked', 'Linked'),
        (100, 'Block', 'D', 'Good', 'Done'),
        (100, 'System', 'Phase_change', 'Mission', 'Mission_failure'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="phased node")
