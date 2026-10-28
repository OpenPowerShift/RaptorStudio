"""The six `Node X -> Standby` rows: how a standby *reaches* a node at all.

`determine_node_status` (`raptor/core/engine/nodes.py:281-299`) only ever
picks `Standby` on one arm:

    elif runs + standbys >= good_paths_required:
        new_status, status_string = NodeStatus.STANDBY, "Standby"

so a node goes to `Standby` exactly when it is **not** meeting its k out of
running paths but *would* meet it counting standby ones, and a link only
counts as standby once `reconfigure_node` has put
`LinkStatus.STANDBY` on it (`nodes.py:402-411`), which needs a non-zero
`StandbyChange` on that path. **Nothing else in the model produces
standby.** There is no "standby" switch a block can be thrown into from the
outside; `Standby` is what a *block* reaches, and the node reads it off the
block's transition deltas. So these six rows are really six claims about
six different routes into `BlockStatus.STANDBY`, and reading them as six
k-of-n shapes is how you end up unable to build one.

**The three routes that exist**, all in this file:

* **A cold group's own switch-off, followed into by a dependent.**
  `run_cold_logic`'s `Too_many_on` demotion moves a still-`Running`
  candidate to `Standby` (`cold_standby.py:650-657`), and that transition's
  `RunChange = -1, StandbyChange = +1` fires
  `BackFlowDependencies(NodeStandby)`, which puts every `Running` **or
  `Idle`** block that depends on the switched-off block into `Standby`
  (`dependency.py:305-313`). One demotion, two blocks into `Standby`, two
  nodes.
* **A repair that completes into `Standby` because its dependency target
  is not up.** `repair_completion_status` returns `Standby` when the
  depended-on node reports `NodeStandby` or has `higherCold` set
  (`dependency.py:846-873`). That is the only way a block reaches
  `Standby` **without** having been failed first, and it is the reason
  `Good -> Standby` is buildable at all: the step is
  `Repairing -> Standby`, i.e. `FailChange = -1, StandbyChange = +1`, so
  the path's *run* count never falls -- which is exactly why a `1-of-1`
  node behind it goes `Bad -> Standby` while a `2-of-2` node in front of a
  still-running sibling goes `Good -> Standby`.
* **The same arm, for a PM**: a `PM_complete` whose block node-depends on
  a `higherCold` node goes `PM -> Standby`
  (`blocks.py:268-269`, `FailChange` untouched, `pmChange = -1`), which is
  the *only* way a node in `NodePM` can leave for `Standby` -- see the
  arithmetic note in `_standby_arm` below.

**The fourth route is a node, not a block.** `ChangeNodeState`'s
`priorStatus = Down, Status = NodeStandby` arm cascades
`FailChange = -1, StandbyChange = +1` downstream (`nodes.py:612-620`), so a
node in `Standby` puts the *next* node's path on standby without costing it
a run. That is the route `_idle` uses, and it is why `Idle -> Standby`
needs no cold group at all.

**Why `higherCold` is the load-bearing part of every one of these.** A
block can only be sent to `Standby` by a repair/PM completion whose
*depended node* refuses it (`dependency.py:856/862/869/873`), and the only
way a node gets that state in this file is `higherCold` -- the flag
`run_cold_logic` sets on a cold node whose own link *lost* its slot in a
**higher** cold group (`cold_standby.py:621-644`, ported at
`cold_standby.py:638-640`). That is why every model here is two cold
groups deep with the first group's link into the second one deliberately
the *lower* priority: the node-sourced candidate is the one that loses, and
losing it is what freezes the first group. `_scaffold` is that shape, and
every test below is the scaffold plus exactly one more mechanism.

**Every model here is `Fixed` (distribution code 19)**, so each run is
exact and consumes zero randomness; every test asserts `Run.draws == 0`.
Rule B4 holds: each model is one run of a handful of blocks and the
slowest test here is a few milliseconds.
"""
from __future__ import annotations

from typing import Any

from .harness import FIXED, assert_timeline, run

#: One block row: a `Fixed` life of *fail* hours, a `Fixed` repair of
#: *repair* hours, and whatever else the caller asks for. `repair=None`
#: writes no repair distribution at all, which is the same thing as a block
#: with no sparing.
def _block(name: str, fail: float, repair: float | None = 10.0,
           **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "fail_dist": FIXED,
                           "fail_params": [fail]}
    if repair is not None:
        row["repair_dist"] = FIXED
        row["repair_params"] = [repair]
    row.update(extra)
    return row


def _cold(prio: int) -> dict[str, Any]:
    """The switching attributes every cold link carries.

    `AProb = 1.0` short-circuits `SetSwitchTime`'s `UniformReal(0,1)` draw
    (`cold_standby.py:103-106`) and `AutoTime = 0.0` resolves
    `switch_status` to `"ready"` rather than `"auto"`, so no
    `PrepForSwitch`/`PerformSwitch` timer is ever started and the whole
    fixture stays draw-free. `ManTime = -1.0` says "no manual fallback";
    it is never read, because the automatic branch is decided first.
    """
    return {"cold_pri": prio, "a_prob": 1.0, "auto_time": 0.0,
            "man_time": -1.0}


def _depends_on_node(name: str) -> dict[str, Any]:
    """Depend on a node, and react to its going down with `DepNothing`.

    The reaction split is the deterministic member of the set -- 100 % on
    `DepNothing` -- so `draw_dependency_action` short-circuits it with no
    draw (`dependency.py`'s own docstring). This is the *node*-dependency
    `repair_completion_status` needs (`dependency.py:846-849`).
    """
    return {"dep_type": "RBDNode", "dep_num": name, "dep_idle": 0.0,
            "dep_nothing": 100.0, "dep_pm": 0.0, "dep_fail": 0.0}


def _follows_block(name: str, *, idle: bool = False) -> dict[str, Any]:
    """Depend on a block.

    With `idle=False` the only reaction is `DepNothing`, so this block is
    left alone when its target fails and only follows it into `Standby`
    (`dependency.py:305-313`). With `idle=True` the split is 100 % on
    `DepIdle`, so the block is idled the moment its target goes down --
    which is what puts its own node in `Idle`.
    """
    return {"dep_type": "RBDBlock", "dep_num": name,
            "dep_idle": 100.0 if idle else 0.0,
            "dep_nothing": 0.0 if idle else 100.0,
            "dep_pm": 0.0, "dep_fail": 0.0}


class _Model:
    """A spec under construction, with `DepNum` resolved by *name*.

    `build_from_spec` walks `blocks` before `nodes` and hands out the lowest
    unused positive integer as it goes, so the *n*-th entry of `blocks` is
    id *n* and the nodes follow. A spec dict carries no ids, so every
    `DepNum` here is written as a **name** and `spec()` resolves it once the
    whole model is known -- the same trick `spine._block_id` plays, done at
    the end so a test can add blocks in any order.
    """

    def __init__(self, length: float) -> None:
        self.length = length
        self.blocks: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.nodes.append({"name": "Start", "type": 1, "n": 0, "out": 0})
        self.nodes.append({"name": "End", "type": 3, "n": 1, "out": 0})

    def block(self, row: dict[str, Any]) -> _Model:
        """Add a block and wire `Start -> block`."""
        self.blocks.append(row)
        name = row["name"]
        self.link("Start", name)
        return self

    def node(self, name: str, n: int, *, k: int = 1, out: int = 1,
             cold: bool = False) -> _Model:
        """Add a k-of-n node, cold-standby or not."""
        row: dict[str, Any] = {"name": name, "type": 2, "n": n, "out": out,
                               "k": k}
        if cold:
            row.update({"cold": 1, "k_star": k, "pr": 0, "autos": 0})
        self.nodes.append(row)
        return self

    def link(self, source: str, target: str, **extra: Any) -> _Model:
        """Add a link, keeping `Start`'s `out` count honest."""
        self.links.append({"from": source, "to": target, **extra})
        if source == "Start":
            self.nodes[0]["out"] += 1
        return self

    def spec(self) -> dict[str, Any]:
        """The finished spec dict, with every `DepNum` name resolved to an id."""
        ids: dict[str, int] = {}
        for index, block in enumerate(self.blocks, start=1):
            ids[block["name"]] = index
        for index, node in enumerate(self.nodes, start=len(self.blocks) + 1):
            ids[node["name"]] = index
        blocks = [
            {**block, "dep_num": ids[block["dep_num"]]}
            if isinstance(block.get("dep_num"), str) else dict(block)
            for block in self.blocks
        ]
        return {
            "system": {"run_length": self.length, "num_runs": 1,
                       "termination": "TimeSim"},
            "blocks": blocks,
            "nodes": list(self.nodes),
            "links": list(self.links),
        }


def _scaffold(length: float = 30.0, *, n1_into: str = "End") -> _Model:
    """Two cold groups deep, the upper one frozen by `higherCold`.

    `Cold1` is the cold group whose own link into `Cold2` is the **lower**
    priority, so `Cold2`'s `run_cold_logic` keeps `G` and switches the
    node-sourced path off. That arm is the only writer of `higherCold`
    (`cold_standby.py:621-644`): the source of a losing node-sourced cold
    link is frozen with `SetHigherCold(TRUE)`, and because `Cold1` is
    itself a cold group it is exactly the flag the `higherCold` branch of
    `run_cold_logic` reads (`cold_standby.py:323-338`) and the
    `node.cold_standby and not node.higher_cold` test in `apply` inverts
    (`orchestrator.py:565`) -- a `higherCold` node aggregates k-of-n inline
    instead of deferring to its own switching.

    `F` then hangs off `Cold1` and *depends on it*, which is the only way a
    running block is ever sent to `Standby` without having been failed
    first: `repair_completion_status` asks the dependency target, finds
    `higherCold`, and answers `Standby` (`dependency.py:862-866`). With `A`
    never failing, `F` fails at 10, repairs at 20, and completes into
    `Standby`; the `1-of-1` node `N1` behind it then goes `Bad -> Standby`.

    `A` must **not** fail. If it did, `Cold1` would lose its last running
    path, aggregate to `Standby`, and cascade that into `Cold2`'s
    node-sourced cold link -- a link whose `SetSwitchTime` needs
    `SysStream[11]`, which the node-to-node cascade does not thread. That
    is reported as an engine finding in this module's companion note, not
    worked around here.

    `n1_into` names the node `N1` feeds -- `End` for the plain case, and the
    `k`-of-`n` node under test in the rows below. Every test that needs a
    node *behind* `N1`'s neighbour adds it and passes its name here, so the
    scaffold's own rows are the same in all of them.
    """
    model = _Model(length)
    model.block(_block("A", 200.0))
    model.block(_block("P", 200.0))
    model.block(_block("G", 200.0))
    model.node("Cold1", 2, k=1, cold=True, out=2)
    model.node("Cold2", 2, k=1, cold=True)
    model.node("N1", 1, k=1)
    model.link("A", "Cold1", **_cold(10))
    model.link("P", "Cold1", **_cold(20))
    model.link("Cold1", "Cold2", **_cold(20))
    model.link("G", "Cold2", **_cold(10))
    model.link("Cold1", "F")
    model.link("F", "N1")
    model.link("N1", n1_into)
    model.link("Cold2", "End")
    # `F` is added last so its dependency can name `Cold1`.
    model.block(_block("F", 10.0, 10.0, **_depends_on_node("Cold1")))
    return model


def _standby_arm() -> None:  # pragma: no cover - documentation only
    """The arithmetic every `X -> Standby` row below has to satisfy.

    `determine_node_status` counts, per incoming link, one of
    `runs` / `standbys` / `idles` / `pms` / `fails`, and picks `Standby` on
    the third arm -- `runs + standbys >= good_paths_required` -- after
    `runs >= good_paths_required` has already failed. So for a node that was
    `Good` before:

    * `runs == n` (all paths up), and the step is `RunChange = -1,
      StandbyChange = +1` (a `Running -> Standby` block, or a node's
      `Up-like -> Standby` cascade): `runs` falls by one and `standbys`
      rises by one, so `runs + standbys` is unchanged at `n`. `Standby`
      therefore needs `runs < k <= n`, i.e. **`k == n`**. That is
      `_good`'s `2-of-2` `N3`, and it is the only reason `Good -> Standby`
      is reachable at all: a step that cost a run could not leave an
      all-up `k < n` node in `Standby`.
    * the step is `FailChange = -1, StandbyChange = +1` (a repair that
      completes into `Standby`, or a node's `Down -> Standby` cascade):
      `runs` does not move at all, so `runs >= k` still holds and the node
      cannot land on the standby arm. That is why every `Bad`/`Degraded`/
      `Idle` row below is reached by a *different* step, and why
      `Good -> Standby` had to be found first.

    The six rows and the step each one needs:

    ==========================  ===================================
    row                         step
    ==========================  ===================================
    `Good -> Standby`           `Running -> Standby`, `k == n`
    `Degraded -> Standby`       `Running -> Standby`, `runs == k`
    `Bad -> Standby`            `Repairing -> Standby` (`fail -1`)
    `Idle -> Standby`           node cascade `Down -> Standby`
    `NodePM -> Standby`         `PM -> Standby` (`pm -1`)
    `Standby -> Standby`        any of the above, on a node
                                already counting one standby path
    ==========================  ===================================

    `NodePM -> Standby` is the odd one out, and it is the reason `_nodepm`
    needs a PM at all. `NodePM` is the fourth arm, so it requires
    `idles + runs + standbys < k` -- a node in `NodePM` has **no idle
    path**. A `Running -> Standby` step keeps `runs + standbys` unchanged,
    which is still `< k`, so the node stays `NodePM`. Only a step that
    *adds* to `runs + standbys` can reach the standby arm, and the only
    such step that leaves a `NodePM` node's other counters alone is
    `PM -> Standby` (`pmChange = -1, standbyChange = +1`,
    `blocks.py:268-269`). There is no other producer of a `PM -> Standby`
    block in the port: `run_cold_logic`'s switch-off is gated on
    `opStatus = Running` (`cold_standby.py:655-656`) and
    `BackFlowDependencies(NodeStandby)` on `opStatus IN (Running, Idle)`
    (`dependency.py:311-312`). So this row has exactly one route, and it is
    the PM completion.
    """


# ---------------------------------------------------------------------------
# Bad -> Standby
# ---------------------------------------------------------------------------

def test_a_node_whose_only_block_completes_its_repair_into_standby_goes_standby() -> None:
    """`Bad -> Standby`: the repair behind the node completes into
    `Standby`, and the node's own `1-of-1` arm takes it there.

    Why: the block's transition is `Repairing -> Standby`, which
    `blocks.py:244-245` scores as `FailChange = -1, StandbyChange = +1` --
    the run count never comes back, so `N1`'s only path goes from
    `LinkDown` to `LinkStandby` and `determine_node_status` reaches
    `runs + standbys = 0 + 1 >= k = 1` with `runs = 0 < 1`
    (`nodes.py:290-291`). The block is only *allowed* to complete into
    `Standby` because it depends on a node with `higherCold` set
    (`dependency.py:862-866`), which is what `_scaffold`'s two-level cold
    group exists to produce.

    Setup: `_scaffold()` alone -- `A` and `P` into a 1-of-1 cold `Cold1`,
    `Cold1` linked into a second cold node `Cold2` at the *lower* priority
    so it loses its slot and freezes itself, `F` depending on `Cold1`, and a
    `1-of-1` node `N1` behind `F`. A 30-hour run.

    Expected: at kickoff `P` is switched off (`Too_many_on`) and `F` is
    `Good`; `F` fails at 10 and `N1` goes `Bad`; at 20 `F` repairs and
    completes into `Standby` because `Cold1.higherCold` is set, and `N1`
    goes `Bad -> Standby`. `N2`/`Cold3` and the rest of the mechanisms in
    this file's other tests are deliberately absent from this model.

    Source: Raptor 7 (`repair_completion_status`'s
    `NodeStandby` / `higherCold` answers), dependency.py:846-873 (its port),
    Raptor 7 (`SetHigherCold(TRUE)` on a losing node-sourced
    cold link), cold_standby.py:621-644,
    Raptor 7 / nodes.py:290-291 (the node's standby arm),
    Raptor 7 / blocks.py:244-245 (the block's deltas);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_scaffold().spec())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'P', '--', 'Good'),
        (0, 'Block', 'G', '--', 'Good'),
        (0, 'Block', 'F', '--', 'Good'),
        (0, 'Block', 'P', 'Good', 'Standby'),
        (0, 'Node', 'Cold1', '--', 'Good'),
        (0, 'Node', 'Cold2', '--', 'Good'),
        (10, 'Block', 'F', 'Good', 'Failed'),
        (10, 'Block', 'F', 'Failed', 'Repairing'),
        (10, 'Node', 'N1', '--', 'Bad'),
        (10, 'Node', 'end', '--', 'Degraded'),
        (10, 'System', 'State_change', '--', 'Yellow'),
        (20, 'Block', 'F', 'Repairing', 'Repaired'),
        (20, 'Block', 'F', 'Repaired', 'Standby'),
        (20, 'Node', 'N1', 'Bad', 'Standby'),
        (20, 'Node', 'end', 'Degraded', 'Good'),
        (20, 'System', 'State_change', 'Yellow', 'Green'),
        (30, 'Block', 'A', 'Good', 'Done'),
        (30, 'Block', 'P', 'Standby', 'Done'),
        (30, 'Block', 'G', 'Good', 'Done'),
        (30, 'Block', 'F', 'Standby', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_success'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="Bad -> Standby")

def _cold_group(model: _Model, node: str, running: str, spare: str,
                *, life: float = 200.0, repair: float = 10.0) -> None:
    """Add `running` and `spare` into a fresh 1-of-1 cold node `node`.

    `running` takes the lower `ColdPri` and so holds the slot; `spare` is
    switched off at kickoff with `Too_many_on`. `spare` is wired to `End`
    because every node needs an outgoing link, and to `End` rather than
    anywhere else so it cannot perturb the node under test.
    """
    model.node(node, 2, k=1, cold=True)
    model.block(_block(running, life, repair))
    model.block(_block(spare, 200.0, 10.0))
    model.link(running, node, **_cold(10))
    model.link(spare, node, **_cold(20))
    model.link(node, "End")


def _good(length: float = 30.0) -> _Model:
    """`_scaffold()` plus a `2-of-2` `N3` whose left member follows `W`.

    `W` fails at 12 and repairs at 13; its repair hands the slot back to
    `U`, so the very next `CheckColds` demotes `W` again
    (`Repaired -> Good -> Standby`, `Too_many_on`) -- a *running* block
    becoming `Standby`, which is the step `Good -> Standby` needs. `I`
    depends on `W` and follows it in, so `N3`'s left path becomes a standby
    path while its right one is still up: `runs` falls to 1 and `standbys`
    rises to 1, and `runs + standbys = 2 >= k = 2` with `runs = 1 < 2`.

    `k == n` is load-bearing here and is the whole reason this row is
    reachable: the step keeps `runs + standbys` at `n`, so any `k < n` node
    would still satisfy `runs >= k` and stay `Good`.
    """
    model = _scaffold(length)
    _cold_group(model, "Cold3", "W", "U", life=12.0, repair=1.0)
    model.node("N3", 2, k=2)
    model.block(_block("I", 200.0, 10.0, **_follows_block("W", idle=True)))
    model.block(_block("J", 200.0, 10.0))
    model.link("I", "N3")
    model.link("J", "N3")
    model.link("N3", "End")
    return model


def _degraded(length: float = 30.0) -> _Model:
    """`_scaffold()` plus a `2-of-4` `N2` that is `Degraded` first.

    `N2`'s four paths are `N1`, `P2`, `Q` and `R`. `P2` follows the kickoff
    switch-off of `P` into `Standby` at t=0, so `N2` starts `Good` with one
    standby path. `F`'s failure at 10 takes `N1` down and leaves `runs = 2
    = k` with `runs + standbys = 3 < 4`, which is the `Degraded` arm. `R`
    then follows `W`'s post-repair demotion into `Standby` at 13: `runs` is
    now 1, so `runs < k = 2`, and `runs + standbys = 3 >= 2` -- the standby
    arm.
    """
    model = _scaffold(length, n1_into="N2")
    _cold_group(model, "Cold3", "W", "U", life=12.0, repair=1.0)
    model.node("N2", 4, k=2)
    model.block(_block("P2", 200.0, 10.0, **_follows_block("P")))
    model.block(_block("Q", 20.0, 5.0, **_depends_on_node("Cold1")))
    model.block(_block("R", 200.0, 10.0, **_follows_block("W")))
    model.link("P2", "N2")
    model.link("Q", "N2")
    model.link("R", "N2")
    model.link("N2", "End")
    return model


def _standby(length: float = 30.0) -> _Model:
    """`_scaffold()` plus a `2-of-3` `N2` that counts two standby paths.

    `N2`'s three paths are `N1`, `P2` and `Q`; `P2` is on standby from t=0
    and `N1` joins it at 20 when `F` completes into `Standby`, and then `Q`
    joins at 25 when its own repair completes into `Standby` too. The node
    re-logs on each because `reconfigure_node` emits a line whenever the
    *link* status moved, even when the aggregate status string is
    unchanged (`nodes.py:455-472`) -- so `Standby -> Standby` is a real
    corpus row and a real thing this engine does, not a duplicate.
    """
    model = _scaffold(length, n1_into="N2")
    model.node("N2", 3, k=2)
    model.block(_block("P2", 200.0, 10.0, **_follows_block("P")))
    model.block(_block("Q", 20.0, 5.0, **_depends_on_node("Cold1")))
    model.link("P2", "N2")
    model.link("Q", "N2")
    model.link("N2", "End")
    return model


def _nodepm(length: float = 30.0) -> _Model:
    """`_scaffold()` plus a `2-of-2` `N4` fed by a periodic-PM block.

    `M` carries a periodic PM (`PMFreq = 10`, a `Fixed` 2-hour duration,
    both cross-resets off so the PM does not restart the failure clock) and
    depends on `Cold1`. While `M` is in `PM`, `N4` has one running path and
    one PM path with `runs = 1 < k = 2` and
    `pms + idles + runs + standbys = 2` -- the `NodePM` arm. When the PM
    completes, `repair_completion_status` finds `Cold1.higherCold` and
    answers `Standby`, and the block's deltas are
    `pmChange = -1, standbyChange = +1` (`blocks.py:268-269`), so `N4`'s
    `runs + standbys` reaches 2 and it takes the standby arm.
    """
    model = _scaffold(length)
    model.node("N4", 2, k=2)
    model.block(_block("M", 200.0, 10.0, **_depends_on_node("Cold1"),
                       uses_pm=True, pm_freq=10.0, pm_dist=FIXED,
                       pm_params=[2.0], pm_refresh=False,
                       pm_fail_reset=False))
    model.block(_block("K", 200.0, 10.0))
    model.link("M", "N4")
    model.link("K", "N4")
    model.link("N4", "End")
    return model


def _idle(length: float = 30.0) -> _Model:
    """`_scaffold()` plus a `4-of-5` `N5` that is `Idle` first.

    `N5`'s five paths are `N1`, `X2`, `Y2`, `Z2` and `S2`. `S2` fails at 15
    and repairs at 21; `Y2` depends on `S2` with `DepIdle = 100%`, so it is
    idled at 15 -- and `N5` follows it to `Idle`, because `F`'s failure at
    10 has already taken `N1` down and the remaining three up paths leave
    `runs = 2 < 4` while `idles + runs + standbys = 4`. At 20 `F`
    completes into `Standby`, and the `Bad -> Standby` node cascade
    (`nodes.py:612-620`, `FailChange = -1, StandbyChange = +1`) brings
    `N5` to `runs = 2`, `standbys = 1`, `idles = 1` -- still `Idle`, one
    step short. `S2`'s repair at 21 then revives its own path
    (`FailChange = -1, RunChange = +1`) and `runs + standbys` reaches 4,
    and `N5` takes the standby arm.
    """
    model = _scaffold(length, n1_into="N5")
    model.node("N5", 5, k=4)
    model.block(_block("X2", 200.0, 10.0))
    model.block(_block("Y2", 200.0, 10.0, **_follows_block("S2", idle=True)))
    model.block(_block("Z2", 200.0, 10.0))
    model.block(_block("S2", 15.0, 6.0))
    model.link("X2", "N5")
    model.link("Y2", "N5")
    model.link("Z2", "N5")
    model.link("S2", "N5")
    model.link("N5", "End")
    return model


# ---------------------------------------------------------------------------
# Good -> Standby
# ---------------------------------------------------------------------------

def test_an_all_up_node_loses_its_last_running_path_to_a_standby_one() -> None:
    """`Good -> Standby`: an all-up `2-of-2` node whose left member follows a
    switched-off block into `Standby` while its right one is still running.

    Why: this is the only row of the six that a `FailChange`-free step can
    produce, and it needs `k == n`. A `Running -> Standby` transition scores
    `RunChange = -1, StandbyChange = +1` (`blocks.py:231-232`), so a node's
    `runs + standbys` is *unchanged* by it -- which means a node that was
    `Good` because `runs + standbys == n` stays at `n`, and the standby arm
    (`runs + standbys >= k`, reached only because `runs >= k` failed first)
    is reachable **only** when `runs` has just fallen below `k`. With all
    `n` paths up that means `n - 1 < k <= n`, i.e. `k == n`. Every other
    shape in this file is `k < n` and needs a different step.

    Setup: `_good()` -- `_scaffold()` plus a cold group `Cold3` (`W` running,
    `U` spare), a `2-of-2` node `N3` fed by `I` (which depends on `W` and
    follows it into `Standby`) and `J` (which never fails), all `Fixed`, a
    30-hour run. `W` fails at 12 and repairs at 13, and the repair hands the
    slot back to `U`, so the following `CheckColds` demotes `W` -- a
    `Running -> Standby` block, which is the step this row needs.

    Expected: at kickoff `P` and `U` are switched off (`Too_many_on`) and
    `I` is idled by `W`'s failure at 12; at 13 `W` is demoted, `I` follows
    it into `Standby`, and `N3` goes `Good -> Standby` -- the row this test
    exists for. `F`'s own repair at 20 puts `N1` in `Standby` too, which
    this test is not about.

    Source: Raptor 7 (`run_cold_logic`'s `Too_many_on` demotion),
    cold_standby.py:650-657, Raptor 7
    (`BackFlowDependencies(NodeStandby)` on a `Running`/`Idle` dependent),
    dependency.py:305-313, Raptor 7 / blocks.py:231-232 (the
    demotion's deltas), Raptor 7 / nodes.py:281-299 (the
    node's k-of-n aggregation);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_good().spec())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'P', '--', 'Good'),
        (0, 'Block', 'G', '--', 'Good'),
        (0, 'Block', 'F', '--', 'Good'),
        (0, 'Block', 'W', '--', 'Good'),
        (0, 'Block', 'U', '--', 'Good'),
        (0, 'Block', 'I', '--', 'Good'),
        (0, 'Block', 'J', '--', 'Good'),
        (0, 'Block', 'P', 'Good', 'Standby'),
        (0, 'Node', 'Cold1', '--', 'Good'),
        (0, 'Node', 'Cold2', '--', 'Good'),
        (0, 'Block', 'U', 'Good', 'Standby'),
        (0, 'Node', 'Cold3', '--', 'Good'),
        (10, 'Block', 'F', 'Good', 'Failed'),
        (10, 'Block', 'F', 'Failed', 'Repairing'),
        (10, 'Node', 'N1', '--', 'Bad'),
        (10, 'Node', 'end', '--', 'Degraded'),
        (10, 'System', 'State_change', '--', 'Yellow'),
        (12, 'Block', 'W', 'Good', 'Failed'),
        (12, 'Block', 'W', 'Failed', 'Repairing'),
        (12, 'Block', 'I', 'Good', 'Idle'),
        (12, 'Node', 'N3', '--', 'Idle'),
        (12, 'Node', 'end', 'Degraded', 'Degraded'),
        (12, 'Block', 'U', 'Standby', 'Good'),
        (12, 'Node', 'Cold3', 'Good', 'Degraded'),
        (13, 'Block', 'W', 'Repairing', 'Repaired'),
        (13, 'Block', 'W', 'Repaired', 'Good'),
        (13, 'Block', 'I', 'Idle', 'Good'),
        (13, 'Node', 'N3', 'Idle', 'Good'),
        (13, 'Node', 'end', 'Degraded', 'Degraded'),
        (13, 'Block', 'W', 'Good', 'Standby'),
        (13, 'Block', 'I', 'Good', 'Standby'),
        (13, 'Node', 'N3', 'Good', 'Standby'),
        (13, 'Node', 'end', 'Degraded', 'Degraded'),
        (13, 'Node', 'Cold3', 'Degraded', 'Good'),
        (20, 'Block', 'F', 'Repairing', 'Repaired'),
        (20, 'Block', 'F', 'Repaired', 'Standby'),
        (20, 'Node', 'N1', 'Bad', 'Standby'),
        (20, 'Node', 'end', 'Degraded', 'Good'),
        (20, 'System', 'State_change', 'Yellow', 'Green'),
        (30, 'Block', 'A', 'Good', 'Done'),
        (30, 'Block', 'P', 'Standby', 'Done'),
        (30, 'Block', 'G', 'Good', 'Done'),
        (30, 'Block', 'F', 'Standby', 'Done'),
        (30, 'Block', 'W', 'Standby', 'Done'),
        (30, 'Block', 'U', 'Good', 'Done'),
        (30, 'Block', 'I', 'Standby', 'Done'),
        (30, 'Block', 'J', 'Good', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_success'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="Good -> Standby")


# ---------------------------------------------------------------------------
# Degraded -> Standby
# ---------------------------------------------------------------------------

def test_a_degraded_node_goes_standby_when_a_running_path_does() -> None:
    """`Degraded -> Standby`: the node is meeting its k on running paths
    alone, and then one of them is switched off.

    Why: the `Degraded` arm is `runs >= k` with `runs + standbys < n`
    (`nodes.py:285-289`), so the node still has slack above `k`. A
    `Running -> Standby` block takes one of its running paths down and adds
    a standby one, leaving `runs + standbys` unchanged but dropping `runs`
    by one. `Standby` therefore needs `runs` to have just fallen *below* `k`,
    i.e. the node must have been sitting at exactly `runs == k`. That is
    the difference from the `Good` case, where the invariant is
    `runs == n` and `k` had to be `n`.

    Setup: `_degraded()` -- `_scaffold()` plus a cold group `Cold3`
    (`W` running, `U` spare) and a `2-of-4` node `N2` fed by `N1`, `P2`
    (which follows `P` into `Standby` at kickoff), `Q` (which fails at 20)
    and `R` (which follows `W`). All `Fixed`, a 30-hour run.

    Expected: `P2` is on standby from t=0 and `N2` starts `Good`; `F`'s
    failure at 10 takes `N1` down and `N2` goes `Good -> Degraded` with
    `runs = 2 = k`; `W`'s post-repair demotion at 13 puts `W` -- and `R`
    with it -- into `Standby`, so `runs` drops to 1 and `N2` goes
    `Degraded -> Standby`, the row this test exists for.

    Source: Raptor 7 (the k-of-n aggregation the port splits
    across `determine_node_status`), nodes.py:283-291 (its four arms),
    Raptor 7 / cold_standby.py:650-657 (the `Too_many_on`
    demotion), Raptor 7 / dependency.py:305-313 (the
    follow-in), Raptor 7 / blocks.py:231-232 (its deltas);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_degraded().spec())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'P', '--', 'Good'),
        (0, 'Block', 'G', '--', 'Good'),
        (0, 'Block', 'F', '--', 'Good'),
        (0, 'Block', 'W', '--', 'Good'),
        (0, 'Block', 'U', '--', 'Good'),
        (0, 'Block', 'P2', '--', 'Good'),
        (0, 'Block', 'Q', '--', 'Good'),
        (0, 'Block', 'R', '--', 'Good'),
        (0, 'Block', 'P', 'Good', 'Standby'),
        (0, 'Block', 'P2', 'Good', 'Standby'),
        (0, 'Node', 'N2', '--', 'Good'),
        (0, 'Node', 'Cold1', '--', 'Good'),
        (0, 'Node', 'Cold2', '--', 'Good'),
        (0, 'Block', 'U', 'Good', 'Standby'),
        (0, 'Node', 'Cold3', '--', 'Good'),
        (10, 'Block', 'F', 'Good', 'Failed'),
        (10, 'Block', 'F', 'Failed', 'Repairing'),
        (10, 'Node', 'N1', '--', 'Bad'),
        (10, 'Node', 'N2', 'Good', 'Degraded'),
        (10, 'System', 'State_change', '--', 'Yellow'),
        (12, 'Block', 'W', 'Good', 'Failed'),
        (12, 'Block', 'W', 'Failed', 'Repairing'),
        (12, 'Block', 'U', 'Standby', 'Good'),
        (12, 'Node', 'Cold3', 'Good', 'Degraded'),
        (13, 'Block', 'W', 'Repairing', 'Repaired'),
        (13, 'Block', 'W', 'Repaired', 'Good'),
        (13, 'Block', 'W', 'Good', 'Standby'),
        (13, 'Block', 'R', 'Good', 'Standby'),
        (13, 'Node', 'N2', 'Degraded', 'Standby'),
        (13, 'Node', 'end', '--', 'Good'),
        (13, 'Node', 'Cold3', 'Degraded', 'Good'),
        (20, 'Block', 'Q', 'Good', 'Failed'),
        (20, 'Block', 'F', 'Repairing', 'Repaired'),
        (20, 'Block', 'F', 'Repaired', 'Standby'),
        (20, 'Node', 'N1', 'Bad', 'Standby'),
        (20, 'Node', 'N2', 'Standby', 'Standby'),
        (20, 'Block', 'Q', 'Failed', 'Repairing'),
        (20, 'Node', 'N2', 'Standby', 'Standby'),
        (25, 'Block', 'Q', 'Repairing', 'Repaired'),
        (25, 'Block', 'Q', 'Repaired', 'Standby'),
        (25, 'Node', 'N2', 'Standby', 'Standby'),
        (25, 'System', 'State_change', 'Yellow', 'Green'),
        (30, 'Block', 'A', 'Good', 'Done'),
        (30, 'Block', 'P', 'Standby', 'Done'),
        (30, 'Block', 'G', 'Good', 'Done'),
        (30, 'Block', 'F', 'Standby', 'Done'),
        (30, 'Block', 'W', 'Standby', 'Done'),
        (30, 'Block', 'U', 'Good', 'Done'),
        (30, 'Block', 'P2', 'Standby', 'Done'),
        (30, 'Block', 'Q', 'Standby', 'Done'),
        (30, 'Block', 'R', 'Standby', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_success'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="Degraded -> Standby")


# ---------------------------------------------------------------------------
# Standby -> Standby
# ---------------------------------------------------------------------------

def test_a_node_in_standby_logs_standby_again_when_a_further_path_goes_standby() -> None:
    """`Standby -> Standby`: the node is already in `Standby` and a second
    path joins it there, and the node logs the row again.

    Why: `reconfigure_node` returns a status line whenever the *link's*
    status moved, not whenever the aggregate changed -- the guard is
    `IF (linkStatusChanged) OR (inPath=0)` and `determine_node_status` is
    called unconditionally after it (`nodes.py:455-472`). So a node whose
    status string does not move still writes a row, and chaining those rows
    is where the corpus's `X -> X` self-transitions come from. This test is
    the clearest case in the file: the node is `Standby` at t=10, t=20 and
    t=25 and writes three identical status strings.

    Setup: `_standby()` -- `_scaffold()` plus a `2-of-3` node `N2` fed by
    `N1`, `P2` (on standby from kickoff) and `Q` (which fails at 20 and
    repairs at 25). All `Fixed`, a 30-hour run.

    Expected: `N2` reaches `Standby` at t=10 -- `P2` is the standby path,
    `Q` is the only running one, and `F`'s failure takes `N1` down, so
    `runs = 1 < 2` with `runs + standbys = 2 >= 2`. At 20 `F` completes into
    `Standby` and at 25 `Q`'s repair completes into `Standby`, each logging
    `Standby -> Standby`; by 25 the counts are `runs = 0, standbys = 2`.

    Source: Raptor 7 (`ReconfigureNode`'s
    `IF (linkStatusChanged) OR (inPath=0)` guard) (the
    aggregation it then performs unconditionally), nodes.py:455-472 and,
    Raptor 7 / blocks.py:244-245 (the repair's
    deltas), Raptor 7 / dependency.py:869-873 (why the repair
    is allowed to complete into `Standby`);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_standby().spec())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'P', '--', 'Good'),
        (0, 'Block', 'G', '--', 'Good'),
        (0, 'Block', 'F', '--', 'Good'),
        (0, 'Block', 'P2', '--', 'Good'),
        (0, 'Block', 'Q', '--', 'Good'),
        (0, 'Block', 'P', 'Good', 'Standby'),
        (0, 'Block', 'P2', 'Good', 'Standby'),
        (0, 'Node', 'N2', '--', 'Good'),
        (0, 'Node', 'Cold1', '--', 'Good'),
        (0, 'Node', 'Cold2', '--', 'Good'),
        (10, 'Block', 'F', 'Good', 'Failed'),
        (10, 'Block', 'F', 'Failed', 'Repairing'),
        (10, 'Node', 'N1', '--', 'Bad'),
        (10, 'Node', 'N2', 'Good', 'Standby'),
        (10, 'Node', 'end', '--', 'Good'),
        (10, 'System', 'State_change', '--', 'Yellow'),
        (20, 'Block', 'Q', 'Good', 'Failed'),
        (20, 'Block', 'F', 'Repairing', 'Repaired'),
        (20, 'Block', 'F', 'Repaired', 'Standby'),
        (20, 'Node', 'N1', 'Bad', 'Standby'),
        (20, 'Node', 'N2', 'Standby', 'Standby'),
        (20, 'Block', 'Q', 'Failed', 'Repairing'),
        (20, 'Node', 'N2', 'Standby', 'Standby'),
        (25, 'Block', 'Q', 'Repairing', 'Repaired'),
        (25, 'Block', 'Q', 'Repaired', 'Standby'),
        (25, 'Node', 'N2', 'Standby', 'Standby'),
        (25, 'System', 'State_change', 'Yellow', 'Green'),
        (30, 'Block', 'A', 'Good', 'Done'),
        (30, 'Block', 'P', 'Standby', 'Done'),
        (30, 'Block', 'G', 'Good', 'Done'),
        (30, 'Block', 'F', 'Standby', 'Done'),
        (30, 'Block', 'P2', 'Standby', 'Done'),
        (30, 'Block', 'Q', 'Standby', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_success'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="Standby -> Standby")


# ---------------------------------------------------------------------------
# NodePM -> Standby
# ---------------------------------------------------------------------------

def test_a_node_pm_goes_standby_when_its_pm_completes_into_standby() -> None:
    """`NodePM -> Standby`: the node's only PM path completes its PM into
    `Standby`.

    Why: `NodePM` is the fourth arm of `determine_node_status`, reached only
    when `idles + runs + standbys < k` (`nodes.py:294-295`) -- so a `NodePM`
    node has **no idle path at all** and its `runs + standbys` is strictly
    below `k`. That is why this row cannot be reached the way its five
    siblings are: a `Running -> Standby` step leaves `runs + standbys`
    unchanged, and an `Idle -> Standby` step is impossible from `NodePM`
    because there is no idle path. The one step that *adds* to
    `runs + standbys` while touching nothing else is
    `PM -> Standby`: `pmChange = -1, standbyChange = +1`
    (`blocks.py:268-269`). A PM completion can reach `Standby` through
    exactly the same `repair_completion_status` answer as a repair -- the
    depended node's `higherCold` (`dependency.py:862-866`) -- so the
    mechanism is shared with the `Bad` case and only the block's own prior
    state differs.

    Setup: `_nodepm()` -- `_scaffold()` plus a `2-of-2` node `N4` fed by `M`
    (a periodic PM of `PMFreq = 10` and a `Fixed` 2-hour duration, both
    cross-resets off, depending on `Cold1`) and `K` (which never fails).
    All `Fixed`, a 30-hour run. `k = 2 = n` because `runs` has to end up
    below `k` when the PM path becomes a standby path.

    Expected: `M`'s PM is due at 10 and runs 10-12, so `N4` is `NodePM`;
    at 12 the PM completes, `repair_completion_status` finds
    `Cold1.higherCold`, and `M` goes `PM_complete -> Standby`, taking `N4`
    from `NodePM` to `Standby` -- the row this test exists for.

    Source: Raptor 7 (the k-of-n aggregation
    and its `linkStatusChanged` guard), nodes.py:283-295,
    Raptor 7 / dependency.py:846-873 (the
    `higherCold` answer, for a PM as for a repair),
    Raptor 7 / blocks.py:268-269 (`PM -> Standby`'s deltas);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_nodepm().spec())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'P', '--', 'Good'),
        (0, 'Block', 'G', '--', 'Good'),
        (0, 'Block', 'F', '--', 'Good'),
        (0, 'Block', 'M', '--', 'Good'),
        (0, 'Block', 'K', '--', 'Good'),
        (0, 'Block', 'P', 'Good', 'Standby'),
        (0, 'Node', 'Cold1', '--', 'Good'),
        (0, 'Node', 'Cold2', '--', 'Good'),
        (10, 'Block', 'F', 'Good', 'Failed'),
        (10, 'Block', 'M', 'Good', 'PM_is_due'),
        (10, 'Block', 'F', 'Failed', 'Repairing'),
        (10, 'Node', 'N1', '--', 'Bad'),
        (10, 'Node', 'end', '--', 'Degraded'),
        (10, 'Block', 'M', 'PM_is_due', 'PM'),
        (10, 'Node', 'N4', '--', 'NodePM'),
        (10, 'Node', 'end', 'Degraded', 'Degraded'),
        (10, 'System', 'State_change', '--', 'Yellow'),
        (12, 'Block', 'M', 'PM', 'PM_complete'),
        (12, 'Block', 'M', 'PM_complete', 'Standby'),
        (12, 'Node', 'N4', 'NodePM', 'Standby'),
        (12, 'Node', 'end', 'Degraded', 'Degraded'),
        (20, 'Block', 'F', 'Repairing', 'Repaired'),
        (20, 'Block', 'F', 'Repaired', 'Standby'),
        (20, 'Node', 'N1', 'Bad', 'Standby'),
        (20, 'Node', 'end', 'Degraded', 'Good'),
        (20, 'System', 'State_change', 'Yellow', 'Green'),
        (30, 'Block', 'A', 'Good', 'Done'),
        (30, 'Block', 'P', 'Standby', 'Done'),
        (30, 'Block', 'G', 'Good', 'Done'),
        (30, 'Block', 'F', 'Standby', 'Done'),
        (30, 'Block', 'M', 'Standby', 'Done'),
        (30, 'Block', 'K', 'Good', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_success'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="NodePM -> Standby")


# ---------------------------------------------------------------------------
# Idle -> Standby
# ---------------------------------------------------------------------------

def test_a_node_idle_on_a_dependency_goes_standby_when_a_sibling_path_recovers() -> None:
    """`Idle -> Standby`: the node is idled by a dependence, and the standby
    arrives from a *node* cascade rather than from any of its own blocks.

    Why: an `Idle` node satisfies `runs + standbys < k <= idles + runs +
    standbys` (`nodes.py:292-293`), so its `runs + standbys` is exactly one
    or more short of `k`. A block transition of its own cannot close that
    gap without also spending a run (an `Idle -> Standby` block would leave
    `runs` alone and needs an idle path, which is there -- but the only
    producer of `Idle -> Standby` is `BackFlowDependencies(NodeStandby)`,
    which needs a *block* in `Standby` upstream of the node, and the node
    under test has none). What does work is the node cascade: `N1` going
    `Bad -> Standby` cascades `FailChange = -1, standbyChange = +1` into
    every node behind it (`nodes.py:612-620`), which raises
    `runs + standbys` without touching `runs`. That is one step short, and
    `S2`'s own repair -- which revives *its* path and nothing else -- is
    what tips it over.

    Setup: `_idle()` -- `_scaffold()` plus a `4-of-5` node `N5` fed by `N1`,
    `X2`, `Y2`, `Z2` and `S2`; `Y2` depends on `S2` with `DepIdle = 100%`.
    `S2` fails at 15 and repairs at 21, `F` fails at 10 and repairs at 20.
    All `Fixed`, a 30-hour run.

    Expected: `F`'s failure at 10 takes `N1` down and `N5` to `Degraded`;
    `S2`'s failure at 15 idles `Y2` and takes `N5` through `Idle` to `Bad`;
    `F`'s repair completing into `Standby` at 20 brings `N5` back to `Idle`
    (one step short of the standby arm); and `S2`'s repair at 21 revives
    its path, at which point `N5` goes `Idle -> Standby` -- the row this
    test exists for -- before `Y2`'s own revival takes it on to `Good`.

    Source: Raptor 7 (the aggregation and its
    link-status guard), nodes.py:283-293 (the `Idle` and
    `Standby` arms), Raptor 7 / nodes.py:567-581 (the
    `priorStatus = Down, Status = NodeStandby` cascade's
    `FailChange = -1, standbyChange = +1`), Raptor 7 /
    dependency.py:315-340 (`BackFlowDependencies(Down)` idling the
    dependent), Raptor 7 / blocks.py:235-236 (a running
    block's repair-recovery deltas);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_idle().spec())

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'P', '--', 'Good'),
        (0, 'Block', 'G', '--', 'Good'),
        (0, 'Block', 'F', '--', 'Good'),
        (0, 'Block', 'X2', '--', 'Good'),
        (0, 'Block', 'Y2', '--', 'Good'),
        (0, 'Block', 'Z2', '--', 'Good'),
        (0, 'Block', 'S2', '--', 'Good'),
        (0, 'Block', 'P', 'Good', 'Standby'),
        (0, 'Node', 'Cold1', '--', 'Good'),
        (0, 'Node', 'Cold2', '--', 'Good'),
        (10, 'Block', 'F', 'Good', 'Failed'),
        (10, 'Block', 'F', 'Failed', 'Repairing'),
        (10, 'Node', 'N1', '--', 'Bad'),
        (10, 'Node', 'N5', '--', 'Degraded'),
        (10, 'System', 'State_change', '--', 'Yellow'),
        (15, 'Block', 'S2', 'Good', 'Failed'),
        (15, 'Block', 'S2', 'Failed', 'Repairing'),
        (15, 'Block', 'Y2', 'Good', 'Idle'),
        (15, 'Node', 'N5', 'Degraded', 'Idle'),
        (15, 'Node', 'end', '--', 'Degraded'),
        (15, 'Node', 'N5', 'Idle', 'Bad'),
        (15, 'Node', 'end', 'Degraded', 'Degraded'),
        (20, 'Block', 'F', 'Repairing', 'Repaired'),
        (20, 'Block', 'F', 'Repaired', 'Standby'),
        (20, 'Node', 'N1', 'Bad', 'Standby'),
        (20, 'Node', 'N5', 'Bad', 'Idle'),
        (20, 'Node', 'end', 'Degraded', 'Degraded'),
        (21, 'Block', 'S2', 'Repairing', 'Repaired'),
        (21, 'Block', 'S2', 'Repaired', 'Good'),
        (21, 'Block', 'Y2', 'Idle', 'Good'),
        (21, 'Node', 'N5', 'Idle', 'Standby'),
        (21, 'Node', 'end', 'Degraded', 'Good'),
        (21, 'Node', 'N5', 'Standby', 'Good'),
        (21, 'Node', 'end', 'Good', 'Good'),
        (21, 'System', 'State_change', 'Yellow', 'Green'),
        (30, 'Block', 'A', 'Good', 'Done'),
        (30, 'Block', 'P', 'Standby', 'Done'),
        (30, 'Block', 'G', 'Good', 'Done'),
        (30, 'Block', 'F', 'Standby', 'Done'),
        (30, 'Block', 'X2', 'Good', 'Done'),
        (30, 'Block', 'Y2', 'Good', 'Done'),
        (30, 'Block', 'Z2', 'Good', 'Done'),
        (30, 'Block', 'S2', 'Good', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_success'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="Idle -> Standby")
