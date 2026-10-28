"""Qt-free editing of the live model.

`DiagramView` used to do two jobs in one 6,900-line class: draw and handle
the canvas, and edit the model. The edit half is not Qt code at all -- it
sets attributes on typed objects (`RBDBlock`, `RBDNode`, `RBDEvent`), writes
the matching columns of their file rows, and remembers which node fields the
user touched so Save writes only those. It was tangled up with the view only
because `_apply_value` also happened to refresh a canvas label or badge in
the same breath.

This module is that half, on its own. `ModelEditor.apply_value` performs the
edit and RETURNS what the canvas should redraw (`Refresh` records); the view
does the drawing. Nothing here imports Qt, so it is tested without a window
(`tests/unit/test_model_edit.py`).

Every edit key is now an entry in a table (`_ARMS`) or a named handler
(`_CUSTOM`), instead of one arm of a 257-line if-chain. An unknown key raises
`UnknownEditKey`; the chain used to fall off the end and do nothing, so a
mistyped key silently discarded the user's edit.

State stays on the host (the view) and is reached through `ModelHost`, so
nothing had to move and nothing goes stale when a model is reloaded.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from raptor.core.fileio.rbd import store_distribution_values
from raptor.core.fileio.typed import SPARING_TYPE_TO_FILE_STRING
from raptor.core.model import SparingType
from raptor.core.fileio.typed import int_or_none

# -- what the canvas should redraw afterwards ---------------------------

SUBLABELS = "sublabels"                # a block's F / R at-a-glance lines
DEPENDENCY_BADGE = "dependency badge"  # a block's or node's badge


@dataclass(frozen=True)
class Refresh:
    """A canvas update that follows an edit. The editor says WHAT changed;
    the view knows how to draw it."""
    what: str
    kind: str          # "block" | "node" | "event"
    oid: int


class UnknownEditKey(KeyError):
    """An edit key with no arm. The if-chain this replaced ignored these."""


# -- state the editor reads and writes ----------------------------------

@dataclass
class EditedFields:
    """Which node fields the user has edited this session.

    Save writes a node's row from the typed object only for the fields in
    these sets: a field the user never touched keeps its file value rather
    than being overwritten with the parser's default (see the comments at
    the write-back in `DiagramView.write_back_to_rbd`)."""
    good_paths: set[int] = field(default_factory=set)
    flow_flags: set[int] = field(default_factory=set)
    node_deps: set[int] = field(default_factory=set)
    report_anal: set[int] = field(default_factory=set)
    standby: set[int] = field(default_factory=set)


class ModelHost(Protocol):
    """What the editor needs from its owner (in practice the DiagramView)."""
    blocks: dict[int, Any]
    nodes: dict[int, Any]
    events: dict[int, Any]
    hiers: dict[int, Any]
    @property
    def rbd(self) -> Any:        # the RbdFile, or None before a model loads
        ...

    @property
    def edited(self) -> EditedFields:
        ...


_OBJECTS = {"block": "blocks", "node": "nodes", "event": "events"}
_RENAMEABLE = {"block": "blocks", "event": "events", "hier": "hiers"}
_SECTIONS = {"block": "BLOCK_DATA", "node": "NODE_DATA",
             "event": "EVENT_DATA"}


def _safe_int(v: Any) -> int | None:
    """DELEGATED to `core.fileio.typed.int_or_none` (review E-12).

    Five copies of this coercion existed with THREE different sentinels.
    This one is `None` rather than -1 or 0 because its callers must
    distinguish "absent or unreadable" from a real `0` -- and ID 0 is
    meaningful in this codebase (the file's injected template row), so a
    numeric sentinel would be indistinguishable from a genuine hit.
    """
    return int_or_none(v)


# -- column formatters ---------------------------------------------------

def _pos(v: float) -> str:
    """A position cell. Computed back from the canvas, a position on an
    axis can come out as -0.0 or -1e-12, which format as "-0.000000"."""
    return f"{round(float(v), 6) + 0.0:.6f}"


def _f6(v: Any) -> str:
    return f"{v:.6f}"


def _flag(v: Any) -> str:
    return "1" if v else "0"


#: Cost tab fields: (model attr, BLOCK_DATA column), in UI order.
BLOCK_COST_FIELDS: tuple[tuple[str, str], ...] = (
    ("initial_cost", "initial"),
    ("operating_cost", "Running"),
    ("standby_cost", "standby"),
    ("idle_cost", "idle"),
    ("rep_fixed_cost", "repFixed"),
    ("repairing_cost", "repPerTime"),
    ("rep_hold_cost", "repHold"),
    ("pm_fixed_cost", "pmFixedCost"),
    ("pm_cost", "pmCost"),
    ("pm_hold_cost", "pmHoldCost"),
    ("done_fixed_cost", "doneFixed"),
    ("done_cost", "donePerTime"),
    ("spare_cost", "perSpare"),
    ("emergency_shipping_cost", "emerShipping"),
)

#: The five PM option checkboxes: dialog key -> attribute on the block.
PM_OPTION_ATTRS = {
    "spare_needed": "pm_spare_needed", "refresh": "pm_refresh",
    "mis_defer": "pm_mis_defer", "fail_reset": "pm_fail_reset",
    "req_defer": "pm_req_defer"}


@dataclass(frozen=True)
class Arm:
    """One edit key, described rather than coded.

    * `attrs`: attributes of the object to set. With `spread_attrs` the
      value is a sequence distributed over them; otherwise `attrs[0]`
      receives the (optionally `convert`ed) value whole.
    * `cols`: BLOCK_DATA / EVENT_DATA row columns to write, as
      (column, formatter). With `spread_cols` the value is a sequence and
      column i takes item i.
    * `edited`: the `EditedFields` set that records this node edit.
    * `refresh`: canvas updates to report.
    * `sync_row`: re-mirror an event's fields into its row afterwards.
    """
    kind: str
    attrs: tuple[str, ...] = ()
    convert: Callable[[Any], Any] | None = None
    spread_attrs: bool = False
    cols: tuple[tuple[str, Callable[[Any], str]], ...] = ()
    spread_cols: bool = False
    edited: str = ""
    refresh: tuple[str, ...] = ()
    sync_row: bool = False


def _cols3(names: Sequence[str]) -> tuple[tuple[str, Callable], ...]:
    return tuple((n, _f6) for n in names)


_ARMS: dict[str, Arm] = {
    # -- blocks: parameter triples (their row columns are written at save)
    "failure params": Arm("block", ("fail_values",), list,
                          refresh=(SUBLABELS,)),
    "repair params": Arm("block", ("repair_values",), list,
                         refresh=(SUBLABELS,)),
    "PM frequency": Arm("block", ("pm_freq",)),
    "PM stagger": Arm("block", ("pm_stagger",),
                      cols=(("PMStaggerTime", _f6),)),
    "dependency fields": Arm(
        "block", ("dep_nothing_percent", "dep_idle_percent", "dep_pm_percent",
                  "dep_fail_percent"), spread_attrs=True,
        cols=_cols3(("DepNothing", "DepIdle", "DepPM", "DepFail",
                     "DepPMThresh")), spread_cols=True),
    "costs": Arm("block", tuple(a for a, _ in BLOCK_COST_FIELDS),
                 spread_attrs=True,
                 cols=tuple((c, _f6) for _, c in BLOCK_COST_FIELDS),
                 spread_cols=True),
    "always add done cost": Arm("block", ("always_add_done_cost",),
                                cols=(("addDone", _flag),)),
    "pre LDT params": Arm("block", ("pre_params",), list,
                          cols=_cols3(("Param1#1", "Param2#1", "Param3#1")),
                          spread_cols=True),
    "post LDT params": Arm("block", ("post_params",), list,
                           cols=_cols3(("Param1#2", "Param2#2", "Param3#2")),
                           spread_cols=True),
    "PM distribution": Arm("block", ("pm_dist",), cols=(("Dist", str),)),
    "PM params": Arm("block", ("pm_params",), list,
                     cols=_cols3(("Param1#3", "Param2#3", "Param3#3")),
                     spread_cols=True),
    "pre LDT dist": Arm("block", ("pre_dist",),
                        cols=(("Pre-LDTDist", str),)),
    "post LDT dist": Arm("block", ("post_dist",),
                         cols=(("Post-LDTDist", str),)),
    # Whitespace-delimited row: an empty token vanishes on save and shifts
    # every later column left. "unnamed" is the placeholder a non-pooled
    # block's own SparePoolName column already carries -- not "None", a
    # different sentinel this format uses elsewhere (DepType/Resource1Name).
    "pool name": Arm("block", ("pool_name",),
                     cols=(("SparePoolName", lambda v: v or "unnamed"),)),
    "initial stock": Arm("block", ("init_stock",), cols=(("Stock", str),)),
    "block uses pm": Arm("block", ("uses_pm",)),
    "block dependency": Arm("block", ("dependency_num", "dep_type"),
                            spread_attrs=True,
                            refresh=(DEPENDENCY_BADGE,)),
    "block dep default idle": Arm("block", ("def_dep_state_idle",)),
    "block resource pool": Arm("block", ("resource_pool_name", "resource_type_count"),
                               spread_attrs=True),
    "block resource counts": Arm("block", ("repair_resources_needed", "pm_resources_needed"),
                                 spread_attrs=True),
    "block new spares": Arm("block", ("new_spares", "new_spares_arrival"),
                            spread_attrs=True),
    "block standby stress": Arm("block", ("sb_stress",)),
    "block gd type": Arm("block", ("gd_type",)),
    "block gd rate limit": Arm("block", ("gd_rate", "gd_limit"),
                               spread_attrs=True),
    # -- nodes: every edit is recorded so Save writes only touched fields
    "node k": Arm("node", ("good_paths",), edited="good_paths"),
    "node full flow": Arm("node", ("full_flow",), edited="flow_flags"),
    "node any path": Arm("node", ("any_path",), edited="flow_flags"),
    "node dependency": Arm("node", ("dependency_num", "dep_type"),
                           spread_attrs=True, edited="node_deps",
                           refresh=(DEPENDENCY_BADGE,)),
    "node report anal": Arm("node", ("report_node_anal",),
                            edited="report_anal"),
    "node cold standby": Arm("node", ("cold_standby",), edited="standby"),
    "node priority return": Arm("node", ("priority_return",),
                                edited="standby"),
    "node check autos first": Arm("node", ("check_autos_first",),
                                  edited="standby"),
    "node k star": Arm("node", ("k_star",), edited="standby"),
    # -- events
    "event costs": Arm("event", ("initial_cost", "operating_cost",
                                 "repairing_cost"),
                       spread_attrs=True, sync_row=True),
    "event dist": Arm("event", ("fail_distro",), sync_row=True),
    "event param": Arm("event", ("fail_mean",), sync_row=True),
    # `event_field_changed` is a fixed Signal(int, str, float) shared with
    # "param", so the stream arrives as a float; coerce back to int or
    # fail_stream (an RNG seed index / system-stream code, never
    # fractional) drifts into a float and is written as "205.0".
    "event stream": Arm("event", ("fail_stream",), int, sync_row=True),
}


#: Keys with a named handler rather than a table entry (plus the
#: "block pm <option>" family, which `known_keys` expands).
_CUSTOM_KEYS = ("stream edit", "sparing type", "pm trigger")


class ModelEditor:
    """Performs edits on the host's live model."""

    def __init__(self, host: ModelHost) -> None:
        self._h = host

    # -- file rows ------------------------------------------------------

    def row(self, kind: str, oid: int) -> dict | None:
        """The file row of an element, or None."""
        rbd = self._h.rbd
        sec = rbd.sections.get(_SECTIONS[kind]) if rbd is not None else None
        if sec is None:
            return None
        for r in sec.rows:
            if _safe_int(r.get("ID")) == oid:
                return r
        return None

    def block_row(self, bid: int) -> dict | None:
        return self.row("block", bid)

    def sync_event_row(self, eid: int) -> None:
        """Mirror an event's editable fields into its EVENT_DATA row so
        Save persists them without a dedicated write-back pass."""
        event = self._h.events.get(eid)
        row = self.row("event", eid)
        if event is None or row is None:
            return
        row["eventName"] = event.name
        row["Distro"] = str(event.fail_distro)
        row["failVal"] = f"{event.fail_mean:.6f}"
        row["failStream"] = str(event.fail_stream)
        row["InitCost"] = f"{event.initial_cost:.6f}"
        row["OpCost"] = f"{event.operating_cost:.6f}"
        row["RepCost"] = f"{event.repairing_cost:.6f}"

    def sync_hier_row(self, hid: int) -> None:
        """Mirror a hierarchy's editable name into its HIERARCHY_DATA row
        (positions go through write_back_to_rbd's own oval pass)."""
        h = self._h.hiers.get(hid)
        rbd = self._h.rbd
        sec = rbd.sections.get("HIERARCHY_DATA") if rbd is not None else None
        if h is None or sec is None:
            return
        for row in sec.rows:
            if _safe_int(row.get("ID", "-1")) == hid:
                row["hierarchyName"] = h.name
                return

    # -- edits ----------------------------------------------------------

    def rename(self, kind: str, oid: int, name: str) -> bool:
        """Rename a block, node, event or hierarchy. Returns False when
        there is no such element (nothing changed, nothing to redraw)."""
        if kind == "node":
            obj = self._h.nodes.get(oid)
        elif kind in ("block", "event", "hier"):
            obj = getattr(self._h, _RENAMEABLE[kind]).get(oid)
        else:
            return False
        if obj is None:
            return False
        attr = "node_name" if kind == "node" else "name"
        old = getattr(obj, attr)
        setattr(obj, attr, name)
        if kind == "event":
            self.sync_event_row(oid)
        elif kind == "hier":
            self.sync_hier_row(oid)
        self._rename_comment_row(kind, oid, old, name)
        return True

    def _rename_comment_row(self, etype: str, oid: int, old: str,
                            new: str) -> None:
        """COMMENTS rows are keyed by name, so a rename would otherwise
        orphan the comment and the next edit would create a duplicate."""
        rbd = self._h.rbd
        sec = rbd.sections.get("COMMENTS") if rbd is not None else None
        if sec is None or old == new:
            return
        for row in sec.rows:
            if (row.get("Type", "").lower() == etype
                    and row.get("Name", "") == old):
                row["Name"] = new
                row["ID"] = str(oid)
                return

    def apply_dist(self, bid: int, which: str,
                   dist_npar: tuple[int, int]) -> None:
        """Set a block's failure ("F") or repair distribution and mirror
        it into the file row (DistCmd owns this change)."""
        block = self._h.blocks.get(bid)
        if block is None:
            return
        distro, npar = dist_npar
        prefix = "F" if which == "F" else "R"
        if prefix == "F":
            block.fail_distro = distro
            block.num_fail_params = npar
        else:
            block.repair_distro = distro
            block.num_repair_params = npar
        row = self.block_row(bid)
        if row is not None:
            row[f"{prefix}Dist"] = str(distro)
            row[f"#{prefix}Param"] = str(npar)

    def apply_streams(self, bid: int, f: int | None, r: int | None) -> None:
        block = self._h.blocks.get(bid)
        if block is None:
            return
        if f is not None:
            block.fail_stream = f
        if r is not None:
            block.repair_stream = r
        row = self.block_row(bid)
        if row is not None:
            row["FStream"] = str(block.fail_stream)
            row["RStream"] = str(block.repair_stream)

    @staticmethod
    def known_keys() -> frozenset[str]:
        """Every edit key the editor can perform."""
        return frozenset(_ARMS) | frozenset(_CUSTOM_KEYS) | frozenset(
            f"block pm {k}" for k in PM_OPTION_ATTRS)

    def handles(self, what: str) -> bool:
        return what in _ARMS or self._custom(what) is not None

    def apply_value(self, what: str, oid: int, value: Any) -> list[Refresh]:
        """Perform edit `what` on element `oid` and return the canvas
        updates that follow. Raises `UnknownEditKey` for a key with no arm.
        An element that no longer exists is skipped, as before."""
        arm = _ARMS.get(what)
        if arm is not None:
            return self._run_arm(arm, oid, value)
        custom = self._custom(what)
        if custom is not None:
            return custom(oid, value)
        raise UnknownEditKey(what)

    @staticmethod
    def kind_of(what: str) -> str:
        """The element kind an edit key acts on (every custom key is a
        block edit)."""
        arm = _ARMS.get(what)
        return arm.kind if arm is not None else "block"

    def _run_arm(self, arm: Arm, oid: int, value: Any) -> list[Refresh]:
        obj = getattr(self._h, _OBJECTS[arm.kind]).get(oid)
        if not obj:
            return []
        new = arm.convert(value) if arm.convert else value
        if arm.spread_attrs:
            for attr, v in zip(arm.attrs, new):
                setattr(obj, attr, v)
        else:
            setattr(obj, arm.attrs[0], new)
        if arm.cols:
            row = self.row(arm.kind, oid)
            if row is not None:
                # Columns take the value AS GIVEN (not `convert`ed).
                values = value if arm.spread_cols else (value,)
                for (col, fmt), v in zip(arm.cols, values):
                    row[col] = fmt(v)
        if arm.edited:
            getattr(self._h.edited, arm.edited).add(oid)
        if arm.sync_row:
            self.sync_event_row(oid)
        return [Refresh(what, arm.kind, oid) for what in arm.refresh]

    # -- edits that are not a plain attribute-and-columns arm -----------

    def _custom(self, what: str) -> Callable[[int, Any], list[Refresh]] | None:
        if what.startswith("block pm "):
            attr = PM_OPTION_ATTRS.get(what[len("block pm "):])
            if attr is None:
                return None
            return lambda oid, value: self._set_block_attr(oid, attr, value)
        return {
            "stream edit": self._stream_edit,
            "sparing type": self._sparing_type,
            "pm trigger": self._pm_trigger,
        }.get(what)

    def _set_block_attr(self, oid: int, attr: str, value: Any
                        ) -> list[Refresh]:
        block = self._h.blocks.get(oid)
        if block:
            setattr(block, attr, value)
        return []

    def _stream_edit(self, oid: int, value: Any) -> list[Refresh]:
        which, val = value
        self.apply_streams(oid,
                           val if which in ("F", "FR") else None,
                           val if which in ("R", "FR") else None)
        return []

    def _sparing_type(self, oid: int, value: Any) -> list[Refresh]:
        block = self._h.blocks.get(oid)
        if block:
            # Either spelling: the enum name ("CUSTOM") or the file's
            # ("Custom"); callers have used both.
            if isinstance(value, str):
                value = (SparingType[value] if value in SparingType.__members__
                         else SparingType(value))
            block.sparing_type = value
            row = self.block_row(oid)
            if row is not None:
                row["SpType"] = SPARING_TYPE_TO_FILE_STRING.get(
                    block.sparing_type, "None")
        return []

    def _pm_trigger(self, oid: int, value: Any) -> list[Refresh]:
        block = self._h.blocks.get(oid)
        if block:
            triggered, trigger_name = value
            block.pm_triggered = triggered
            block.pm_trig = trigger_name if triggered else ""
            row = self.block_row(oid)
            if row is not None:
                row["pmTriggered"] = "1" if triggered else "0"
                row["pmTrig"] = trigger_name if triggered else ""
        return []

    # -- write-back -----------------------------------------------------

    def write_back(self, rbd: Any, hier_ids: Iterable[int] | None = None
                   ) -> None:
        """Write the live model out to the file rows.

        Positions of events and hierarchies must already be current on the
        objects (the view reads them off the canvas items first). Only
        hierarchies in `hier_ids` are written; None means all, and the view
        passes the ones it has an item for, because a hierarchy at another
        level has no item and its row is left as it was."""
        self._write_blocks(rbd)
        self._write_nodes(rbd)
        for name, objs, ids in (("EVENT_DATA", self._h.events, None),
                                ("HIERARCHY_DATA", self._h.hiers, hier_ids)):
            sec = rbd.sections.get(name)
            if sec is None:
                continue
            for row in sec.rows:
                oid = _safe_int(row.get("ID", "-1"))
                if oid is None:
                    continue
                obj = objs.get(oid)
                if obj is None or (ids is not None and oid not in ids):
                    continue
                row["xPos"] = _pos(obj.x_position)
                row["yPos"] = _pos(obj.y_position)

    def write_element(self, rbd: Any, kind: str, oid: int) -> None:
        """Write one element's row now, right after an edit.

        Many edits (failure/repair parameters, PM frequency, dependencies,
        every node field) set only the typed object and left the row to
        Save. But the canvas re-parses the rows whenever it rebuilds --
        changing hierarchy level, a Rates change, an undo -- so such an edit
        was silently lost there, and a run straight after used the old
        value while the title still said "modified"."""
        if rbd is None:
            return
        if kind == "block":
            self._write_blocks(rbd, only=oid)
        elif kind == "node":
            self._write_nodes(rbd, only=oid)
        elif kind == "event":
            self.sync_event_row(oid)

    def _write_blocks(self, rbd: Any, only: int | None = None) -> None:
        section = rbd.sections.get("BLOCK_DATA")
        if not section:
            return
        for row in section.rows:
            bid = _safe_int(row.get("ID", "0"))
            if only is not None and bid != only:
                continue
            b = self._h.blocks.get(bid) if bid is not None else None
            if b is None:
                continue
            row["xPos"] = _pos(b.x_position)
            row["yPos"] = _pos(b.y_position)
            row["blockName"] = b.name
            # Distribution codes + param counts: plain non-lossy
            # ints in typed.py (no sentinel/coercion), so a
            # blanket write is safe. Needed since the Data Tables
            # grid can change them.
            # An Empirical law keeps every data point (continuation line,
            # count = number of points); writing only the first three
            # values lost the rest and saved `#FParam=0`.
            row["#FParam"] = str(b.num_fail_params)
            row["#RParam"] = str(b.num_repair_params)
            store_distribution_values(row, "F", b.fail_distro, b.fail_values)
            store_distribution_values(row, "R", b.repair_distro, b.repair_values)
            row["PMFreq"] = f"{b.pm_freq:.6f}"
            # PM enable + option flags, dependency master, and
            # resource/custom-sparing assignment: all plain
            # non-lossy bool/int/str in typed.py (no sentinel), so
            # a blanket write is safe.
            row["usesPM"] = "1" if b.uses_pm else "0"
            row["PMSpare"] = "1" if b.pm_spare_needed else "0"
            row["PMRefresh"] = "1" if b.pm_refresh else "0"
            row["PMMisDefer"] = "1" if b.pm_mis_defer else "0"
            row["PMFailReset"] = "1" if b.pm_fail_reset else "0"
            row["PMReqDefer"] = "1" if b.pm_req_defer else "0"
            row["DepNum"] = str(b.dependency_num)
            # "None" (Raptor's own placeholder -- depType always
            # holds a token, never blank) rather than "": an empty
            # string as a whitespace-delimited token vanishes on
            # save, shifting every later column left by one. The
            # engine only compares against "RBDBlock"/"RBDNode",
            # so this is pure format safety, no semantic change.
            row["DepType"] = b.dep_type or "None"
            # Same empty-token guard -- Resource1Name carries
            # "None" when NumRes=0.
            row["Resource1Name"] = b.resource_pool_name or "None"
            row["NumRes"] = str(b.resource_type_count)
            row["#Res1Rep"] = str(b.repair_resources_needed)
            row["#Res1pmRep"] = str(b.pm_resources_needed)
            row["NewSp"] = str(b.new_spares)
            row["ArriveEvery"] = f"{b.new_spares_arrival:.6f}"
            # Advanced tab: standby stress + growth/decay --
            # plain floats/int, no sentinel, blanket-safe.
            # Keep the file's own spelling when the value is unchanged
            # (Raptor 7 writes "0.000"): rewriting it on every write-back
            # changed rows nobody had touched.
            try:
                same = float(row.get("SBStress", "")) == float(b.sb_stress)
            except ValueError:
                same = False
            if not same:
                row["SBStress"] = f"{b.sb_stress:.6f}"
            row["GDType"] = str(b.gd_type)
            row["GDRate"] = f"{b.gd_rate:.6f}"
            row["GDLimit"] = f"{b.gd_limit:.6f}"

    def _write_nodes(self, rbd: Any, only: int | None = None) -> None:
        section = rbd.sections.get("NODE_DATA")
        if not section:
            return
        ed = self._h.edited
        for row in section.rows:
            nid = _safe_int(row.get("ID", "0"))
            if only is not None and nid != only:
                continue
            n = self._h.nodes.get(nid) if nid is not None else None
            if n is None:
                continue
            row["xPos"] = _pos(n.x_position)
            row["yPos"] = _pos(n.y_position)
            row["Name"] = n.node_name
            # only for nodes the user actually edited via
            # set_node_good_paths -- see `_edited_good_paths`'s own
            # docstring. `node_from_row` coerces a file `k=-1`
            # sentinel to `good_paths=1`, irreversibly losing which
            # nodes were really -1, and that sentinel turns up on
            # ordinary type_node==2 nodes too, so neither "type==2"
            # nor "good_paths!=-1" is a safe proxy. Blanket-writing
            # `good_paths` would turn untouched sentinel
            # rows into literal 1s on every save.
            if nid in ed.good_paths:
                row["k"] = str(n.good_paths)
            if nid in ed.flow_flags:
                row["fullflow"] = "1" if n.full_flow else "0"
                row["divFlow"] = "1" if n.any_path else "0"
            if nid in ed.node_deps:
                row["depNum"] = str(n.dependency_num)
                # "None" not "" -- see the identical block-side
                # comment above (same issue): an empty
                # whitespace-delimited token vanishes on save.
                row["depType"] = n.dep_type or "None"
            if nid in ed.report_anal:
                row["repNA"] = "1" if n.report_node_anal else "0"
            if nid in ed.standby:
                row["Cold"] = "1" if n.cold_standby else "0"
                row["PR"] = "1" if n.priority_return else "0"
                row["Autos"] = "1" if n.check_autos_first else "0"
                row["k*"] = str(n.k_star)
