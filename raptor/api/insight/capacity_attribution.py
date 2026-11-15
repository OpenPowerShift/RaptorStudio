"""Lost capacity per element (issue #256): who loses the flow, and how much.

Availability counts an hour down the same for one of a hundred parallel
units as for the single point of connection.  Capacity analysis knows the
*magnitude* of the loss; the attribution replay knows *which elements* are
down.  This module joins the two streams.  It reads the run's capacity sink
and event log and changes neither, so it cannot change a result.

At every instant the **loss** is ``flow_generated - delivered_flow`` (never
negative).  It is shared among the elements that are down at that instant by
one named rule:

``proportional`` (default)
    In proportion to each element's **standalone capacity contribution**: the
    flow lost when that element alone is down.  The shares sum to the engine's
    own lost flow, so a report reconciles to `Lost_Flow`.

``marginal``
    Loss now minus loss with this element restored.  Answers "what would
    fixing this one buy?" but overlapping losses do **not** add up; the shares
    can exceed the whole and the result says so.

``first_failed``
    The whole loss to the element that went down first.  Sums to the whole,
    but it is order-dependent and noisy; comparison only.

Elements Idle through a dependency are not charged: their loss is charged to
the element they depend on, and shown separately as "of which through
dependents".  Standby and bypassed elements are never charged.

Definitions (also in docs/formulas.adoc):

* **Standalone capacity contribution** of an element = flow generated minus
  the flow delivered with only that element down, from the Failure Effects
  machinery (no simulation, no random draw).  It is computed once per model
  and cached by model hash.
* **Lost energy** of an element = the loss energy charged to it over the
  statistics window, in flow x time units.
* **Share of lost capacity** = that element's lost energy / the run's total
  lost energy.
"""
from __future__ import annotations
from raptor.api.insight.axes import pct

import math
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

Key = tuple[str, int]
SYSTEM_KEY: Key = ("system", 0)

#: The sharing rules the issue defines, in the order they are documented.
RULES = ("proportional", "marginal", "first_failed")
DEFAULT_RULE = "proportional"

#: The capacity sink writes its times to six decimal places, the event log
#: to its own rounding, so a flow change and the state change that caused it
#: can land up to one sink step apart.  A loss interval this short with no
#: element down is that rounding: it belongs to the elements down just
#: before it (Water2: 4,491 such slivers, all under 5e-7 h).
SINK_TIME_RESOLUTION = 1e-6

#: The most multi-element states `marginal` keeps per attribution.
MAX_MARGINAL_STATES = 50_000
#: Standalone contributions kept for whole models (about one float per block).
MAX_CACHED_MODELS = 8

_STANDALONE_CACHE: OrderedDict[str, dict[Key, float]] = OrderedDict()

CAPACITY_LABEL = "Working out which element loses the capacity"
STANDALONE_LABEL = "Working out each element's own capacity contribution"


@dataclass(frozen=True)
class CapacityRun:
    """One run's flow track and what is needed to share the loss."""

    flow_points: tuple[tuple[float, float], ...]
    generated: float
    standalone: Mapping[Key, float] = field(default_factory=dict)
    dependencies: Mapping[Key, Key] = field(default_factory=dict)
    rule: str = DEFAULT_RULE
    #: `marginal` only: delivered flow with a set of elements down.  None
    #: means the state could not be evaluated; the rule then says so and
    #: falls back to `proportional`.
    marginal: Callable[[frozenset[Key]], float | None] | None = None
    #: A standalone contribution per element, evaluated only when the
    #: element was actually down (a large model may never fail most of its
    #: elements, and each evaluation is a Failure Effects pass).
    standalone_of: Callable[[Key], float] | None = None
    #: Caveats for the result: a phased model, a missing standalone map...
    notes: tuple[str, ...] = ()


@dataclass
class TrialCapacity:
    """One run's lost capacity, plain and picklable."""

    rule: str = DEFAULT_RULE
    generated: float = 0.0
    span: float = 0.0
    lost_energy: float = 0.0
    delivered_energy: float = 0.0
    by_element: dict[Key, float] = field(default_factory=dict)
    by_cause: dict[str, float] = field(default_factory=dict)
    #: each element's lost energy split by what caused it
    element_causes: dict[Key, dict[str, float]] = field(default_factory=dict)
    through_dependents: dict[Key, float] = field(default_factory=dict)
    standalone: dict[Key, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def normalise_rule(rule: str | None) -> str:
    """A supported rule name, or a `ValueError` naming the choices."""
    value = (rule or DEFAULT_RULE).strip().lower()
    if value not in RULES:
        raise ValueError(
            f"unknown capacity attribution rule {rule!r}; expected one of "
            f"{', '.join(RULES)}")
    return value


def _is_down(state: str | None) -> bool:
    return state not in (None, "up", "standby", "bypass")


class CapacityTracker:
    """Integrates the flow loss while the attribution replay walks the log.

    The replay calls :meth:`advance` before it changes an element's state, so
    the down set it passes is the one that held during the elapsed interval.
    """

    def __init__(self, run: CapacityRun, start_stats: float = 0.0) -> None:
        self.out = TrialCapacity(rule=normalise_rule(run.rule),
                                 generated=float(run.generated))
        for note in run.notes:
            self.out.note(note)
        self.start_stats = max(0.0, float(start_stats))
        self.generated = max(0.0, float(run.generated))
        self.standalone = {k: max(0.0, float(v)) for k, v in run.standalone.items()}
        self.standalone_of = run.standalone_of
        self.dependencies = dict(run.dependencies)
        self.marginal_fn = run.marginal
        self.out.standalone = dict(self.standalone)
        points = sorted((float(t), float(f)) for t, f in run.flow_points)
        self._points = points
        # The value at t=0 is the first recorded value; a point later than 0
        # says nothing about the interval before it.
        self._delivered = points[0][1] if points else self.generated
        self._index = 0
        self._cursor = 0.0
        # Apply any points at or before zero before the replay starts.
        while self._index < len(points) and points[self._index][0] <= 0.0:
            self._delivered = points[self._index][1]
            self._index += 1
        self._marginal_cache: dict[frozenset[Key], float | None] = {}
        self._no_element_noted = False
        self._last_group: tuple[list[Key], dict[Key, list[Key]],
                                dict[Key, str]] | None = None
        self._last_keyed_end = -math.inf
        self._pending = 0.0          # a leading sliver waiting for its owner
        self._zero_weight_noted = False
        self._fallback_noted = False
        self._marginal_failed = False

    # ---- time ------------------------------------------------------------
    def advance(self, to: float, down: Mapping[Key, int],
                state: Mapping[Key, str]) -> None:
        """Book the loss from the previous call up to `to`."""
        if to <= self._cursor:
            return
        while self._index < len(self._points) and self._points[self._index][0] < to:
            when, flow = self._points[self._index]
            if when > self._cursor:
                self._book(self._cursor, when, down, state)
                self._cursor = when
            self._delivered = flow
            self._index += 1
        self._book(self._cursor, to, down, state)
        self._cursor = max(self._cursor, to)

    def finish(self, at: float, down: Mapping[Key, int],
               state: Mapping[Key, str]) -> None:
        self.advance(at, down, state)
        if self._pending:
            # Nothing went down after it: no element owns it.
            self._add(SYSTEM_KEY, self._pending, "other")
            self._pending = 0.0

    def _book(self, start: float, end: float, down: Mapping[Key, int],
              state: Mapping[Key, str]) -> None:
        lo = max(start, self.start_stats)
        if end <= lo:
            return
        dt = end - lo
        delivered = max(0.0, self._delivered)
        self.out.span += dt
        self.out.delivered_energy += delivered * dt
        loss_rate = max(0.0, self.generated - delivered)
        if loss_rate <= 0.0:
            return
        lost = loss_rate * dt
        self.out.lost_energy += lost
        self._allocate(lost, loss_rate, down, state, lo, end)

    # ---- sharing ---------------------------------------------------------
    def _weight(self, key: Key) -> float:
        """The element's standalone contribution, computed on first need."""
        if key in self.standalone:
            return self.standalone[key]
        if self.standalone_of is None:
            return 0.0
        try:
            value = max(0.0, float(self.standalone_of(key)))
        except Exception:  # noqa: BLE001 - a bad element must not break the trial
            value = 0.0
        self.standalone[key] = value
        self.out.standalone[key] = value
        return value

    def _charge(self, key: Key, state: Mapping[Key, str]) -> Key:
        """Follow a dependency chain to the element that is down for its own
        reasons.  A cycle (a bad model) stops where it started."""
        seen: set[Key] = set()
        current = key
        while state.get(current) == "dependency" and current not in seen:
            seen.add(current)
            master = self.dependencies.get(current)
            if master is None or master == current:
                return current
            current = master
        return current

    def _down_keys(self, down: Mapping[Key, int],
                   state: Mapping[Key, str]) -> list[Key]:
        return [key for key in down if _is_down(state.get(key))]

    def _allocate(self, lost: float, loss_rate: float,
                  down: Mapping[Key, int], state: Mapping[Key, str],
                  start: float = -math.inf, end: float = math.inf) -> None:
        keys = self._down_keys(down, state)
        if not keys and end - start <= SINK_TIME_RESOLUTION:
            # A rounding sliver (see SINK_TIME_RESOLUTION).  Right after a
            # span with elements down, the flow came back a step late: it is
            # theirs.  Otherwise the flow fell a step early: it belongs to
            # whatever goes down next, so hold it until then.
            if (self._last_group is not None
                    and start - self._last_keyed_end <= SINK_TIME_RESOLUTION):
                self._spread(lost, *self._last_group)
            else:
                self._pending += lost
            return
        if not keys:
            self._add(SYSTEM_KEY, lost, "other")
            if not self._no_element_noted:
                self.out.note(
                    "Some flow was lost while no element was down (a phase or "
                    "a node change); it is shown as '(no single element)'.")
                self._no_element_noted = True
            return
        charges, cause_of = self._groups(keys, state)
        master_keys = sorted(charges)
        self._last_group = (master_keys, charges, cause_of)
        self._last_keyed_end = end
        if self._pending:
            self._spread(self._pending, master_keys, charges, cause_of)
            self._pending = 0.0
        if self.out.rule == "first_failed":
            first = self._charge(min(keys, key=lambda k: (down.get(k, 0), k)), state)
            self._add_group(first, lost, cause_of[first], charges[first])
            return
        marginal = (self._marginal_shares(master_keys, loss_rate)
                    if self.out.rule == "marginal" else None)
        if marginal is not None:
            for key, amount in marginal.items():
                if amount > 0:
                    self._add_group(key, amount * (lost / loss_rate),
                                    cause_of[key], charges[key])
            return
        self._spread(lost, master_keys, charges, cause_of)

    def _groups(self, keys: list[Key],
                state: Mapping[Key, str]) -> tuple[dict[Key, list[Key]],
                                                   dict[Key, str]]:
        """Charge each down element to the element it is really held by."""
        charges: dict[Key, list[Key]] = {}
        for key in sorted(keys):
            charges.setdefault(self._charge(key, state), []).append(key)
        cause_of = {key: self._cause(key, state) for key in charges}
        return charges, cause_of

    def _spread(self, lost: float, master_keys: list[Key],
                charges: Mapping[Key, list[Key]],
                cause_of: Mapping[Key, str]) -> None:
        """Proportional when a contribution exists, else an equal split.

        A master's standalone contribution already includes the elements it
        holds Idle (failing A in FEV idles its dependants), so the group's
        weight is the master's own, not the sum of its members'.
        """
        weights = {key: self._weight(key) for key in master_keys}
        total_weight = sum(weights.values())
        if total_weight > 0:
            for key in master_keys:
                weight = weights[key]
                if weight > 0:
                    self._add_group(key, lost * weight / total_weight,
                                    cause_of[key], charges[key])
            return
        share = lost / len(master_keys)
        for key in master_keys:
            self._add_group(key, share, cause_of[key], charges[key])
        if not self._zero_weight_noted:
            self.out.note(
                "No element's own failure explains the loss at some instants "
                "(redundancy still met, or a phase change); it was split "
                "equally among the elements down then.")
            self._zero_weight_noted = True

    def _cause(self, key: Key, state: Mapping[Key, str]) -> str:
        cause = state.get(key)
        if cause is None or cause in ("up", "standby", "bypass"):
            return "dependency"
        return cause

    def _marginal_shares(self, keys: list[Key],
                         loss_rate: float) -> dict[Key, float] | None:
        """Each element's own marginal loss while the others stay down."""
        shares: dict[Key, float] = {}
        down_set = frozenset(keys)
        for key in keys:
            restored = down_set - {key}
            delivered = self._evaluate(restored)
            if delivered is None:
                if not self._marginal_failed:
                    self.out.note(
                        "The marginal rule could not evaluate a multi-element "
                        "state, so the loss was shared proportionally instead.")
                    self._marginal_failed = True
                return None
            loss_without = max(0.0, self.generated - delivered)
            shares[key] = max(0.0, loss_rate - loss_without)
        if sum(shares.values()) <= 0.0:
            if not self._marginal_failed:
                self.out.note(
                    "Restoring any one down element would not restore flow "
                    "(a redundancy or a phase limit binds), so the loss was "
                    "shared proportionally instead.")
                self._marginal_failed = True
            return None
        self.out.note(
            "Marginal shares answer 'what would fixing this one buy?': "
            "overlapping losses are counted once per element, so the shares "
            "need not add up to the whole.")
        return shares

    def _evaluate(self, keys: frozenset[Key]) -> float | None:
        if keys in self._marginal_cache:
            return self._marginal_cache[keys]
        if len(self._marginal_cache) >= MAX_MARGINAL_STATES:
            return None
        value = self.marginal_fn(keys) if self.marginal_fn is not None else None
        self._marginal_cache[keys] = value
        return value

    def _add(self, key: Key, amount: float, cause: str) -> None:
        self.out.by_element[key] = self.out.by_element.get(key, 0.0) + amount
        self.out.by_cause[cause] = self.out.by_cause.get(cause, 0.0) + amount
        own = self.out.element_causes.setdefault(key, {})
        own[cause] = own.get(cause, 0.0) + amount

    def _add_group(self, key: Key, amount: float, cause: str,
                   group: list[Key]) -> None:
        """Charge a master, splitting out the part its Idle dependants explain.

        The dependants' own standalone contributions say how much of the
        master's loss is really theirs; the part is capped at the master's
        whole, because a master's contribution already includes them.
        """
        self._add(key, amount, cause)
        dependants = [k for k in group if k != key]
        if not dependants:
            return
        base = self._weight(key)
        their = sum(self._weight(k) for k in dependants)
        if base > 0:
            portion = amount * min(their, base) / base
        else:
            portion = amount * len(dependants) / len(group)
        self.out.through_dependents[key] = (
            self.out.through_dependents.get(key, 0.0) + portion)


def attribute_capacity(
    lines: Iterable[Any],
    flow_points: Iterable[tuple[float, float]],
    flow_generated: float,
    *,
    start_stats: float = 0.0,
    rule: str = DEFAULT_RULE,
    standalone: Mapping[Key, float] | None = None,
    dependencies: Mapping[Key, Key] | None = None,
    marginal: Callable[[frozenset[Key]], float | None] | None = None,
) -> TrialCapacity:
    """Replay one run's lines against its capacity sink, returning only the
    capacity half.  `raptor.api.insight.attribution.attribute_trial` does the
    same work when it is given a `CapacityRun`; use that when both halves are
    wanted."""
    from raptor.api.insight.attribution import attribute_trial
    run = CapacityRun(
        tuple(flow_points), float(flow_generated), dict(standalone or {}),
        dict(dependencies or {}), rule, marginal)
    trial = attribute_trial(lines, start_stats, capacity=run)
    return trial.capacity or TrialCapacity(rule=normalise_rule(rule),
                                           generated=float(flow_generated))


# ---- standalone capacity contributions -----------------------------------

def flow_generated_of(rbd: Any) -> float:
    """The model's ``SYSTEM_DATA.flowGenerated`` (0 when absent)."""
    sec = getattr(rbd, "sections", {}).get("SYSTEM_DATA")
    if not (sec and sec.rows):
        return 0.0
    try:
        return max(0.0, float(sec.rows[0].get("flowGenerated", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def capacity_analysis_on(rbd: Any) -> bool:
    """Whether the model's own run settings switch capacity analysis on.

    The `experiment` dict is the .rbds answer; a legacy .rbd has only the
    system row's `capacityAnalysis` column, so both are read.
    """
    sec = getattr(rbd, "sections", {}).get("SYSTEM_DATA")
    if sec and sec.rows:
        value = sec.rows[0].get("capacityAnalysis", "")
        if str(value).strip().lower() in ("1", "true", "yes", "on"):
            return True
    experiment = getattr(rbd, "experiment", None)
    if experiment:
        from raptor.core import run_experiment
        normalised = run_experiment.normalise(experiment)
        if "capacity" in (normalised.get("analyses") or ()):
            return True
    return False


def is_phased(rbd: Any) -> bool:
    """Whether any block or event row carries a phasing flag."""
    for section in ("BLOCK_DATA", "EVENT_DATA"):
        sec = getattr(rbd, "sections", {}).get(section)
        if sec is None:
            continue
        for row in sec.rows:
            value = row.get("Phasing", row.get("Phases", ""))
            if str(value).strip() not in ("", "0", "0.0"):
                return True
    return False


def dependencies_of(rbd: Any) -> dict[Key, Key]:
    """Block key -> the block or event it depends on.

    Read from the file's own ``BLOCK_DATA`` rows, so it works before the
    model is built.  Only dependencies on another block or event are
    followed; a dependency on a node or hierarchy has no element state in the
    replay and is charged to the dependent itself.  (Only blocks carry a
    dependency column in the file format.)
    """
    out: dict[Key, Key] = {}
    sec = getattr(rbd, "sections", {}).get("BLOCK_DATA")
    if sec is None:
        return out
    for row in sec.rows:
        try:
            block_id = int(float(row.get("ID", 0) or 0))
            dep_num = int(float(row.get("DepNum", 0) or 0))
        except (TypeError, ValueError):
            continue
        if block_id <= 0 or dep_num <= 0:
            continue
        dep_type = str(row.get("DepType", "") or "")
        if dep_type == "RBDBlock":
            out[("block", block_id)] = ("block", dep_num)
        elif dep_type == "RBDEvent":
            out[("block", block_id)] = ("event", dep_num)
    return out


def standalone_capacity(
    rbd: Any,
    *,
    progress: Any = None,
    abort: Any = None,
) -> dict[Key, float]:
    """Each block's (and event's) own capacity contribution.

    Uses `raptor.core.engine.fev.FevSession` -- the Failure Effects View, no
    simulation and no random draw -- with every other element operating.  A
    model without capacity analysis, or a phased one (where the contribution
    depends on the phase), returns an empty map; the caller says so in the
    result's notes.
    """
    from raptor.api.progress import check, report
    check(abort)
    if not capacity_analysis_on(rbd) or flow_generated_of(rbd) <= 0:
        return {}
    if is_phased(rbd):
        return {}
    from raptor.core.engine.fev import FevSession

    try:
        session = FevSession(rbd)
    except Exception:  # noqa: BLE001 - an unrunnable model has no contribution
        return {}
    entries: list[tuple[Key, str, int]] = []
    for block in session.model.blocks_by_id.values():
        entries.append((("block", int(block.id)), "block", int(block.id)))
    for event in session.model.events_by_id.values():
        entries.append((("event", int(event.id)), "event", int(event.id)))
    session.refresh_all()
    fast = _FastCapacity(session)
    ready = fast.ready()
    out: dict[Key, float] = {}
    total = len(entries)
    for done, (key, kind, element_id) in enumerate(entries, start=1):
        check(abort)
        value = fast.contribution(key) if ready else None
        if value is None:
            session.refresh_all()
            value = _fail_one(session, kind, element_id)
        out[key] = value
        report(progress, "capacity_states", STANDALONE_LABEL, done, total)
    return out


def _fail_one(session: Any, kind: str, element_id: int) -> float:
    """Delivered flow lost when one element alone is failed (0 if it cannot
    be failed alone, or failing it changes no flow)."""
    from raptor.core.model import BlockStatus

    try:
        if kind == "block":
            block = session.model.blocks_by_id.get(element_id)
            if block is None or block.op_status != BlockStatus.RUNNING:
                return 0.0
            session.click_block(element_id)
        else:
            session.click_event(element_id)
    except Exception:  # noqa: BLE001 - this element cannot be failed alone
        return 0.0
    capacity = session.capacity()
    return max(0.0, float(capacity["lost"])) if capacity else 0.0


class _FastCapacity:
    """One-element capacity loss without a node-status cascade.

    Valid when one failed element still leaves its downstream node at or
    above its required good paths: the node stays up-like (Degraded counts),
    so only the link status changes and the engine's own `analyze_capacity`
    can measure the flow.  This is the many-parallel-units case; anything
    else (a series node, a dependency, a standby) returns None and the
    caller uses the Failure Effects view.
    """

    def __init__(self, session: Any) -> None:
        from raptor.core.engine.capacity import (
            CapacityState, reset_for_new_run,
        )
        self.session = session
        self.model = session.model
        self.generated = float(self.model.flow_generated)
        self.state = CapacityState()
        reset_for_new_run(self.state, self.model.nodes_by_id)
        self.state.flowing_at_max_cap = session._flowing_at_max_cap
        #: Parallel units with the same link caps and the same upstream and
        #: downstream nodes have the same standalone contribution; one
        #: capacity pass serves the whole group.
        self._cache: dict[tuple, float] = {}
        self._upstream: dict[int, Any] = {}
        by_target: dict[int, list[Any]] = {}
        for link in self.model.links_by_id.values():
            if link.connect_to_id in self.model.blocks_by_id:
                self._upstream[link.connect_to_id] = link
            else:
                by_target.setdefault(link.connect_to_id, []).append(link)
        # Equal-cap links into one node are interchangeable: their priority
        # order changes which one is filled first, not the flow delivered
        # when one of them is removed.
        self._symmetric: set[int] = {
            node for node, links in by_target.items()
            if len({(l.sim_nom_flow, l.sim_max_flow, l.cap_priority)
                    for l in links}) == 1}

    def ready(self) -> bool:
        from raptor.core.model import (
            ActiveStatus, BlockStatus, LinkStatus, NodeStatus,
        )
        if self.model.events_by_id:
            return False
        for node in self.model.nodes_by_id.values():
            if node.status != NodeStatus.ALL_UP or node.type_node not in (1, 2, 3):
                return False
        # Only a block's own downstream node is touched by one block failing;
        # a Start/join node with one input is not.
        for link in self.model.links_by_id.values():
            if link.status != LinkStatus.UP or link.switch_status in (
                    "autoInProgress", "manualInProgress"):
                return False
        for block in self.model.blocks_by_id.values():
            if (block.op_status != BlockStatus.RUNNING
                    or block.active_status != ActiveStatus.ACTIVE
                    or (block.dependency_num > 0 and block.dep_type)):
                return False
            link = self.model.links_by_id.get(block.downstream_link_id)
            if link is None:
                return False
            node = self.model.nodes_by_id.get(link.connect_to_id)
            if node is None or node.good_paths >= len(node.in_paths_array):
                return False          # one failure would take the node down
        return True

    def signature(self, block: Any, link: Any) -> tuple:
        upstream = self._upstream.get(int(block.id))
        same_priority = link.connect_to_id in self._symmetric
        sibling_specs = []
        for sibling_id in getattr(link, "siblings_group", ()) or ():
            if sibling_id == link.object_num:
                continue
            sibling = self.model.links_by_id.get(sibling_id)
            if sibling is not None:
                sibling_specs.append((sibling.sim_nom_flow,
                                      sibling.sim_max_flow,
                                      sibling.cap_priority))
        siblings = tuple(sorted(sibling_specs))
        return (
            link.connect_to_id, link.sim_nom_flow, link.sim_max_flow,
            0 if same_priority else link.sim_cap_priority,
            0 if same_priority else link.cap_priority,
            link.dep_link, siblings,
            int(upstream.connect_from_id) if upstream is not None else 0,
            int(upstream.sim_nom_flow) if upstream is not None else 0,
            int(upstream.sim_max_flow) if upstream is not None else 0,
        )

    def contribution(self, key: Key) -> float | None:
        from raptor.core.engine.capacity import analyze_capacity
        from raptor.core.model import LinkStatus

        if key[0] != "block":
            return 0.0
        block = self.model.blocks_by_id.get(key[1])
        if block is None:
            return 0.0
        link = self.model.links_by_id.get(block.downstream_link_id)
        if link is None:
            return None
        signature = self.signature(block, link)
        if signature in self._cache:
            return self._cache[signature]
        old = link.status
        link.status = LinkStatus.DOWN
        self.state.cap_file_lines.clear()
        try:
            analyze_capacity(
                self.state, new_sys_status_red=False, sim_time=0.0,
                cap_node_group=self.model.cap_node_group,
                cap_link_ids=self.model.cap_link_ids,
                nodes_by_id=self.model.nodes_by_id,
                links_by_id=self.model.links_by_id,
                start_node=self.model.nodes_by_id[self.model.start_node_id],
                end_node=self.model.nodes_by_id[self.model.end_node_id],
                flow_generated=self.model.flow_generated,
                flowing_at_max_cap=self.state.flowing_at_max_cap)
            delivered = self.state.node_flow[self.model.start_node_id]
        finally:
            link.status = old
        value = max(0.0, self.generated - float(delivered))
        self._cache[signature] = value
        return value


class _LazyStandalone:
    """One element's contribution, from a reused Failure Effects session.

    Most elements of a large model never go down in a run, so they are never
    evaluated.  After each element the session is restored and the delivered
    flow checked; a dependency that left something behind triggers a full
    refresh, which is slower but still exact.
    """

    def __init__(self, rbd: Any, abort: Any) -> None:
        from raptor.api.progress import check
        from raptor.core.engine.fev import FevSession

        check(abort)
        self.abort = abort
        self.session = FevSession(rbd)
        self.session.refresh_all()
        capacity = self.session.capacity()
        self.base = float(capacity["delivered"]) if capacity else 0.0
        self.fast = _FastCapacity(self.session)
        self.fast_ready = capacity is not None and self.fast.ready()
        self.current: Key | None = None

    def _restore(self) -> None:
        if self.current is None:
            return
        kind, element_id = self.current
        self.current = None
        try:
            if kind == "block":
                self.session.click_block(element_id)
            else:
                self.session.click_event(element_id)
        except Exception:  # noqa: BLE001 - fall through to a full refresh
            self.session.refresh_all()
            return
        capacity = self.session.capacity()
        if capacity is None or float(capacity["delivered"]) != self.base:
            self.session.refresh_all()

    def __call__(self, key: Key) -> float:
        from raptor.api.progress import check
        from raptor.core.model import BlockStatus

        check(self.abort)
        if self.fast_ready:
            value = self.fast.contribution(key)
            if value is not None:
                return value
            self.fast_ready = False
        self._restore()
        kind, element_id = key
        if kind == "block":
            block = self.session.model.blocks_by_id.get(element_id)
            if block is None or block.op_status != BlockStatus.RUNNING:
                self.session.refresh_all()
                block = self.session.model.blocks_by_id.get(element_id)
            if block is None or block.op_status != BlockStatus.RUNNING:
                return 0.0
            self.session.click_block(element_id)
        else:
            self.session.click_event(element_id)
        self.current = key
        capacity = self.session.capacity()
        return (max(0.0, self.base - float(capacity["delivered"]))
                if capacity else 0.0)


def lazy_standalone(
    rbd: Any,
    *,
    abort: Any = None,
) -> Callable[[Key], float]:
    """A per-element standalone contribution, computed on first need."""
    from raptor.api.progress import Cancelled
    try:
        return _LazyStandalone(rbd, abort)
    except Cancelled:
        raise
    except Exception:  # noqa: BLE001 - no session means no contribution
        return lambda _key: 0.0


def capacity_runs(
    rbd: Any,
    cap_lines: Iterable[str],
    *,
    model_key: str | None = None,
    rule: str = DEFAULT_RULE,
    progress: Any = None,
    abort: Any = None,
    standalone: Mapping[Key, float] | None = None,
) -> list[CapacityRun] | None:
    """One `CapacityRun` per run from a run's capacity-sink lines.

    None when the model did not run capacity analysis or the sink is empty.
    Standalone contributions come from the Failure Effects machinery and are
    cached by model hash; a phased model has none (they depend on the phase)
    and the result says so.
    """
    rule = normalise_rule(rule)
    if not capacity_analysis_on(rbd):
        return None
    generated = flow_generated_of(rbd)
    if generated <= 0:
        return None
    from raptor.api.series import parse_capacity_runs
    parsed = parse_capacity_runs("\n".join(cap_lines))
    if not parsed:
        return None
    rule, standalone, standalone_of, marginal, notes = _capacity_plan(
        rbd, rule, abort, standalone)
    dependencies = dependencies_of(rbd)
    out = []
    for run in parsed:
        points = tuple(zip(run["t"], run["flow"], strict=False))
        out.append(CapacityRun(
            flow_points=points, generated=generated,
            standalone=dict(standalone), dependencies=dependencies,
            rule=rule, marginal=marginal, standalone_of=standalone_of,
            notes=tuple(notes)))
    return out


def _capacity_plan(
    rbd: Any,
    rule: str,
    abort: Any,
    standalone: Mapping[Key, float] | None,
) -> tuple[str, dict[Key, float], Callable[[Key], float] | None,
           Callable[[frozenset[Key]], float | None] | None, list[str]]:
    """The rule actually in force, its weights and the caveats to report."""
    notes: list[str] = []
    phased = is_phased(rbd)
    weights = dict(standalone or {})
    standalone_of = None
    if not phased and rule != "first_failed" and not weights:
        standalone_of = lazy_standalone(rbd, abort=abort)
    if phased:
        notes.append(
            "The model is phased, so an element's own capacity contribution "
            "depends on the phase; the loss was split equally among the "
            "elements that were down at each instant.")
    marginal = None
    if rule == "marginal":
        if phased:
            rule = "proportional"
            notes.append(
                "The marginal rule is not available for a phased model; the "
                "loss was shared proportionally instead.")
        else:
            marginal = make_marginal_evaluator(rbd)
    return rule, weights, standalone_of, marginal, notes


def cached_standalone(
    rbd: Any,
    model_key: str,
    *,
    progress: Any = None,
    abort: Any = None,
) -> dict[Key, float]:
    """`standalone_capacity` memoised by model hash, bounded to a few models."""
    if model_key in _STANDALONE_CACHE:
        _STANDALONE_CACHE.move_to_end(model_key)
        return dict(_STANDALONE_CACHE[model_key])
    values = standalone_capacity(rbd, progress=progress, abort=abort)
    _STANDALONE_CACHE[model_key] = dict(values)
    while len(_STANDALONE_CACHE) > MAX_CACHED_MODELS:
        _STANDALONE_CACHE.popitem(last=False)
    return values


def make_marginal_evaluator(
    rbd: Any,
    *,
    cache: dict[frozenset[Key], float | None] | None = None,
) -> Callable[[frozenset[Key]], float | None]:
    """A cached `failure_effects`-style delivered-flow evaluator for
    `marginal`.  Each state is one Failure Effects pass; the tracker caps how
    many it keeps."""
    from raptor.core.engine.fev import FevSession
    from raptor.core.model import BlockStatus

    memo = cache if cache is not None else {}
    try:
        session = FevSession(rbd)
    except Exception:  # noqa: BLE001 - callers fall back to proportional
        return lambda _keys: None

    def delivered(keys: frozenset[Key]) -> float | None:
        if keys in memo:
            return memo[keys]
        if len(memo) >= MAX_MARGINAL_STATES:
            return None
        session.refresh_all()
        try:
            for kind, element_id in sorted(keys):
                if kind == "block":
                    block = session.model.blocks_by_id.get(element_id)
                    if block is None or block.op_status != BlockStatus.RUNNING:
                        memo[keys] = None
                        return None
                    session.click_block(element_id)
                elif kind == "event":
                    session.click_event(element_id)
        except Exception:  # noqa: BLE001 - an impossible state, not a crash
            memo[keys] = None
            return None
        capacity = session.capacity()
        value = float(capacity["delivered"]) if capacity is not None else None
        memo[keys] = value
        return value

    return delivered


def _stat(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"mean": None, "sem": None, "min": None, "max": None, "n": 0}
    mean = sum(values) / n
    sem = (math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1) / n)
           if n > 1 else None)
    return {"mean": mean, "sem": sem, "min": min(values), "max": max(values),
            "n": n}


def _own_causes(entries: list[Any], key: Key) -> dict[str, float]:
    """What caused one element's lost capacity, as fractions of it, over all
    runs (results saved before causes were kept per element have none)."""
    own: dict[str, float] = {}
    for cap in entries:
        for cause, amount in (getattr(cap, "element_causes", None) or {}).get(key, {}).items():
            own[cause] = own.get(cause, 0.0) + amount
    total = sum(own.values())
    return {c: v / total for c, v in own.items()} if total > 0 else {}


def aggregate_capacity(
    trials: Iterable[Any],
    names: Mapping[Key, str] | None = None,
) -> dict[str, Any] | None:
    """The `capacity` block of `attribution.json`, or None without capacity.

    `trials` are `TrialAttribution`s; only those with a `.capacity` count.
    Every per-element figure is a mean over the runs; the share is a per-run
    ratio averaged like DTCI, so it has an SEM.
    """
    trials = list(trials) if names is None else trials
    if names is None:
        found: dict[Key, str] = {}
        for trial in trials:
            found.update(getattr(trial, "names", {}) or {})
        names = found
    entries = [t.capacity for t in trials
               if getattr(t, "capacity", None) is not None]
    if not entries:
        return None
    rule = entries[0].rule
    total = [c.lost_energy for c in entries]
    keys = sorted({k for c in entries for k in c.by_element}
                  | {k for c in entries for k in c.through_dependents})
    rows = []
    for key in keys:
        energies, shares, dependents = [], [], []
        for cap in entries:
            value = cap.by_element.get(key, 0.0)
            energies.append(value)
            shares.append(value / cap.lost_energy if cap.lost_energy > 0 else 0.0)
            dependents.append(cap.through_dependents.get(key, 0.0))
        own = _own_causes(entries, key)
        rows.append({
            "kind": key[0], "id": key[1],
            "name": names.get(key, "(no single element)"),
            "lost_energy": _stat(energies),
            "share": _stat(shares),
            "through_dependents": _stat(dependents),
            "standalone": next((c.standalone.get(key) for c in entries
                                if key in c.standalone), None),
            # what caused this element's own lost capacity (not its downtime)
            "causes": own,
            "main_cause": max(own, key=lambda c: own[c]) if own else None,
        })
    rows.sort(key=lambda r: (-(r["lost_energy"]["mean"] or 0.0), r["name"]))
    cause_names = sorted({c for cap in entries for c in cap.by_cause})
    causes = {}
    for cause in cause_names:
        values = [cap.by_cause.get(cause, 0.0) for cap in entries]
        causes[cause] = _stat(values)
    notes: list[str] = []
    for cap in entries:
        for note in cap.notes:
            if note not in notes:
                notes.append(note)
    return {
        "schema": "raptor-capacity-attribution/1",
        "rule": rule,
        "generated": entries[0].generated,
        "lost_energy": _stat(total),
        "delivered_energy": _stat([c.delivered_energy for c in entries]),
        "elements": rows,
        "causes": causes,
        "notes": notes,
    }


#: Why the "via dependants" figures are all zero, said instead of a column
#: of zeros.
NO_DEPENDANTS = (
    "Nothing was charged via dependants: no element was held Idle by a block "
    "or event it depends on while flow was lost. (A dependency on a node, a "
    "hierarchy or the whole system is charged to the dependant itself.)")


def text_lines(capacity: Mapping[str, Any], n: int = 10) -> list[str]:
    """The lost-capacity attribution as a plain-text table."""
    rule = capacity.get("rule", DEFAULT_RULE)
    lines = [f"Lost capacity, attributed by {rule.replace('_', ' ')} split:", ""]
    lost = capacity["lost_energy"]["mean"]
    lines.append(f"Lost energy {_amount(lost)} (flow x time) a run, "
                 f"from {capacity['lost_energy']['n']:,} runs")
    by_cause = sorted(capacity.get("causes", {}).items(),
                      key=lambda kv: -(kv[1]["mean"] or 0.0))
    shown = [(c, v["mean"]) for c, v in by_cause if v.get("mean")]
    if shown and lost:
        lines.append("By cause: " + ", ".join(
            f"{c.replace('_', ' ')} {pct(v / lost)}" for c, v in shown))
    rows = capacity["elements"][:n]
    # the column only means something when a master held a dependant Idle
    held = any((row["through_dependents"]["mean"] or 0.0) > 0
               for row in capacity["elements"])
    lines += ["", f"{'Element':<22}{'Lost capacity':>16}{'Share':>10}"
                  + (f"{'Of which via dependants':>26}" if held else "")]
    for row in rows:
        value = row["lost_energy"]["mean"] or 0.0
        share = row["share"]["mean"] or 0.0
        dependants = row["through_dependents"]["mean"] or 0.0
        lines.append(f"{row['name'][:21]:<22}{_amount(value):>16}"
                     f"{100 * share:>9.3g}%"
                     + (f"{_amount(dependants):>26}" if held else ""))
    if not held:
        lines += ["", NO_DEPENDANTS]
    for note in capacity.get("notes") or []:
        lines += ["", f"Note: {note}"]
    return lines


def _amount(value: float) -> str:
    """Flow x time without exponents: 62,980 or 0.25."""
    if value == 0:
        return "0"
    if abs(value) >= 100:
        return f"{value:,.0f}"
    return f"{value:.3g}"
