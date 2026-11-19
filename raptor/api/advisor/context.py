"""What the rules read: cached, friendly views of one model.

`ModelContext` turns the file rows into dictionaries that use the same
friendly field names as `.rbds` and `raptor.api.paths`, and adds the derived
quantities several rules share (distribution means, who feeds which node,
who draws from which pool). Everything is computed once, on first use.
Rules must treat it as read-only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from raptor.api.advisor.finding import Target

WEIBULL, EXPONENTIAL, LOGNORMAL, NORMAL, NONE_DIST, FIXED = 15, 4, 7, 8, 18, 19

_SECTION_KIND = {"Block": "block", "Node": "node", "Event": "event", "Hier": "hier"}


def _value(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    try:
        f = float(text)
    except ValueError:
        return text
    return int(f) if f.is_integer() and "." not in text and "e" not in text.lower() else f


def on(value: Any) -> bool:
    """A stored flag ("1", 1, True, "TRUE") as a bool."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _distinct_names(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Give each row a name a reader can tell apart: an unnamed element, or
    one whose name another shares, is called by its id as well.  Two blocks
    both called "unnamed" otherwise gave the Advisor two identical lines
    that looked like one finding listed twice."""
    from collections import Counter
    def raw(row: dict[str, Any]) -> str:
        name = str(row.get("name") or "").strip()
        return "" if name.lower() in ("", "none", "unnamed") else name
    counts = Counter(raw(row) for row in rows)
    for row in rows:
        name = raw(row)
        if not name:
            row["name"] = f"{kind} {row.get('id')}"
        elif counts[name] > 1:
            row["name"] = f"{name} ({kind} {row.get('id')})"
    return rows


def _friendly_rows(rbd: Any, section: str) -> list[dict[str, Any]]:
    from raptor.api.build import combined_map
    sec = rbd.sections.get(section)
    if sec is None:
        return []
    seen_cols: set[Any] = set()
    fields: list[tuple[str, Any]] = []
    for name, col in combined_map(section).items():
        key = col if isinstance(col, str) else tuple(col)
        if key in seen_cols:
            continue                       # an alias of a field already taken
        seen_cols.add(key)
        fields.append((name, col))
    out = []
    for row in sec.rows:
        d: dict[str, Any] = {}
        for name, col in fields:
            if isinstance(col, str):
                if col in row:
                    d[name] = _value(row[col])
            else:
                d[name] = [_value(row.get(c, "0")) for c in col]
        out.append(d)
    return out


@dataclass(frozen=True)
class Group:
    """A node with more than one input: `k` of its `n` inputs must be up."""
    node: dict
    k: int
    n: int
    members: tuple[tuple[str, int], ...]      # (kind, id) feeding the node


class ModelContext:
    def __init__(self, rbd: Any, validation: list | None = None) -> None:
        self.rbd = rbd
        #: `raptor.api.validate.issues(rbd)` when the caller already has it
        #: (the application validates on every edit); None = compute it.
        self.validation = validation

    # ---- run settings ----------------------------------------------------
    @cached_property
    def experiment(self) -> dict:
        from raptor.core import run_experiment
        exp = getattr(self.rbd, "experiment", None)
        if not exp:
            rows = self.rbd.sections["SYSTEM_DATA"].rows
            exp = run_experiment.from_system_row(rows[0]) if rows else {}
        return run_experiment.normalise(exp)

    @cached_property
    def system(self) -> dict[str, Any]:
        rows = _friendly_rows(self.rbd, "SYSTEM_DATA")
        return rows[0] if rows else {}

    @property
    def units(self) -> str:
        return str(self.system.get("units", "") or "")

    @property
    def trials(self) -> int:
        return int(self.experiment.get("trials") or 1)

    @property
    def run_length(self) -> float | None:
        """The run length for a time-terminated run, else None."""
        if self.experiment.get("termination_type") != "TimeSim":
            return None
        return float(self.experiment.get("term_condition") or 0.0) or None

    @property
    def analyses(self) -> set[str]:
        return set(self.experiment.get("analyses") or ())

    # ---- elements --------------------------------------------------------
    @cached_property
    def _all_blocks(self) -> list[dict]:
        return _friendly_rows(self.rbd, "BLOCK_DATA")

    @cached_property
    def blocks(self) -> list[dict]:
        return _distinct_names([b for b in self._all_blocks if b.get("id")], "block")

    @cached_property
    def default_block(self) -> dict | None:
        """The application's default block data (the file's id-0 template row)."""
        for b in self._all_blocks:
            if not b.get("id"):
                return b
        from raptor.api.build import build_from_spec, series
        try:
            rows = _friendly_rows(build_from_spec(series(1)), "BLOCK_DATA")
        except Exception:  # noqa: BLE001 - no template: the default-data rule stays silent
            return None
        return next((b for b in rows if not b.get("id")), None)

    @cached_property
    def nodes(self) -> list[dict]:
        return _distinct_names(_friendly_rows(self.rbd, "NODE_DATA"), "node")

    @cached_property
    def events(self) -> list[dict]:
        return _friendly_rows(self.rbd, "EVENT_DATA")

    @cached_property
    def pools(self) -> list[dict]:
        return _friendly_rows(self.rbd, "SPARE_POOL_DATA")

    @cached_property
    def resources(self) -> list[dict]:
        return _friendly_rows(self.rbd, "RESOURCE_DATA")

    @cached_property
    def block_by_id(self) -> dict[int, dict]:
        return {int(b["id"]): b for b in self.blocks}

    @cached_property
    def node_by_id(self) -> dict[int, dict]:
        return {int(n["id"]): n for n in self.nodes}

    # ---- graph -----------------------------------------------------------
    @cached_property
    def _edges(self) -> tuple[dict, dict]:
        ins: dict[tuple[str, int], list[tuple[str, int]]] = {}
        outs: dict[tuple[str, int], list[tuple[str, int]]] = {}
        sec = self.rbd.sections.get("LINK_DATA")
        for row in (sec.rows if sec else ()):
            try:
                a = (_SECTION_KIND[row["FromType"]], int(float(row["FromID"])))
                b = (_SECTION_KIND[row["ToType"]], int(float(row["ToID"])))
            except (KeyError, ValueError):
                continue
            outs.setdefault(a, []).append(b)
            ins.setdefault(b, []).append(a)
        return ins, outs

    def inputs_of(self, kind: str, id: int) -> list[tuple[str, int]]:
        return self._edges[0].get((kind, id), [])

    def outputs_of(self, kind: str, id: int) -> list[tuple[str, int]]:
        return self._edges[1].get((kind, id), [])

    def redundant(self, element: tuple[str, int]) -> bool:
        """Whether a path from the start to the end avoids `element` (a
        hierarchy counts as one step): whether, drawn as it is, the system
        could carry on with `element` alone down. k-of-n nodes are read as
        1-of-n here, so this is about the drawing, not the vote."""
        starts = [("node", int(n["id"])) for n in self.nodes if int(n.get("type") or 0) == 1]
        ends = {("node", int(n["id"])) for n in self.nodes if int(n.get("type") or 0) == 3}
        outs = self._edges[1]
        seen = {s for s in starts if s != element}
        todo = list(seen)
        while todo:
            here = todo.pop()
            if here in ends:
                return True
            for nxt in outs.get(here, ()):
                if nxt != element and nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return False

    @cached_property
    def groups(self) -> list[Group]:
        out = []
        for node in self.nodes:
            members = tuple(self.inputs_of("node", int(node["id"])))
            if len(members) > 1:
                k = int(node.get("k") or 1)
                out.append(Group(node, k if k > 0 else 1, len(members), members))
        return out

    # ---- named supplies --------------------------------------------------
    @cached_property
    def pool_members(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for b in self.blocks:
            if b.get("sparing_type") == "Pooled":
                out.setdefault(str(b.get("spare_pool")), []).append(b)
        return out

    @cached_property
    def resource_members(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for b in self.blocks:
            if int(b.get("num_resources") or 0) > 0 and b.get("resource_name") not in (None, "None"):
                out.setdefault(str(b["resource_name"]), []).append(b)
        return out

    # ---- distributions ---------------------------------------------------
    @staticmethod
    def dist_mean(code: Any, params: list) -> float | None:
        from raptor.api.distributions import describe_dist
        try:
            mean = describe_dist(int(code), list(params)).get("mean")
        except Exception:  # noqa: BLE001 - an unknown or malformed law has no mean here
            return None
        if mean is None or not math.isfinite(mean):
            return None
        return float(mean)

    def mean_life(self, b: dict) -> float | None:
        return self.dist_mean(b.get("fail_dist"), b.get("fail_params", []))

    def mean_repair(self, b: dict) -> float | None:
        if int(b.get("repair_dist") or 0) == NONE_DIST:
            return None
        return self.dist_mean(b.get("repair_dist"), b.get("repair_params", []))

    def mean_ldt(self, b: dict) -> float:
        pre = self.dist_mean(b.get("pre_ldt_dist"), b.get("pre_ldt_params", [])) or 0.0
        post = self.dist_mean(b.get("post_ldt_dist"), b.get("post_ldt_params", [])) or 0.0
        return pre + post

    def mean_pm(self, b: dict) -> float | None:
        return self.dist_mean(b.get("pm_duration_dist"), b.get("pm_params", []))

    @staticmethod
    def hazard_shape(b: dict) -> str:
        """`decreasing`, `constant`, `increasing` or `unknown` failure rate."""
        code = int(b.get("fail_dist") or 0)
        if code == EXPONENTIAL:
            return "constant"
        if code == WEIBULL:
            shape = float((b.get("fail_params") or [1])[0])
            if abs(shape - 1.0) < 1e-9:
                return "constant"
            return "increasing" if shape > 1.0 else "decreasing"
        if code in (NORMAL, FIXED):
            return "increasing"
        return "unknown"

    def failure_rate(self, b: dict) -> float | None:
        """Failures per unit time while operating (duty cycle applied)."""
        life = self.mean_life(b)
        if not life or life <= 0:
            return None
        duty = float(b.get("duty_cycle") or 1.0) or 1.0
        return duty / life

    # ---- targets ---------------------------------------------------------
    @staticmethod
    def block_target(b: dict, field: str | None = None) -> Target:
        return Target("block", int(b["id"]), str(b.get("name", "")), field)

    @staticmethod
    def node_target(n: dict, field: str | None = None) -> Target:
        return Target("node", int(n["id"]), str(n.get("name", "")), field)

    def element_name(self, kind: str, id: int) -> str:
        if kind == "block" and id in self.block_by_id:
            return str(self.block_by_id[id].get("name"))
        if kind == "node" and id in self.node_by_id:
            return str(self.node_by_id[id].get("name"))
        return f"{kind} {id}"


#: A requirement's metric -> the per-run field that holds it.
REQUIREMENT_FIELDS = {
    "system.ao": "availability", "system.availability": "availability",
    "system.reliability": "reliability", "system.dependability": "dependability",
    "system.mtbde": "mtbde", "system.mdt": "mdt", "system.mtbm": "mtbm",
    "system.capacity_weighted_availability": "capacity_weighted_availability",
    "system.capacity": "capacity_weighted_availability",
}
# the bare names, as `--precision-metric` and the result tables use them
REQUIREMENT_FIELDS.update({key.split(".", 1)[1]: value
                           for key, value in list(REQUIREMENT_FIELDS.items())})


def check_requirements(requirements: list[dict] | None) -> None:
    """Refuse a requirement on a metric the run does not report. An unknown
    name ("ao") used to give no verdict and no word why."""
    for req in requirements or []:
        metric = str(req.get("metric", ""))
        if metric not in REQUIREMENT_FIELDS:
            names = ", ".join(k for k in REQUIREMENT_FIELDS if k.startswith("system."))
            raise ValueError(f"unknown requirement metric {metric!r}; use one of {names}")


class RunContext(ModelContext):
    """A model plus one finished batch: what the run rules read."""

    def __init__(self, rbd: Any, summary: dict, per_run: list[dict],
                 requirements: list[dict] | None = None,
                 attribution: dict | None = None) -> None:
        super().__init__(rbd)
        self.summary = summary or {}
        self.per_run = list(per_run or [])
        self.requirements = list(requirements or [])
        #: `raptor.api.insight.attribution.aggregate` output, or None when
        #: the run was made without downtime attribution.
        self.attribution = attribution

    @property
    def n(self) -> int:
        return len(self.per_run)

    def values(self, field: str) -> list[float]:
        """The runs' values of one per-run field, leaving out undefined ones."""
        out = []
        for row in self.per_run:
            v = row.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                out.append(float(v))
        return out

    @staticmethod
    def mean_sem(values: list[float]) -> tuple[float, float]:
        n = len(values)
        if n == 0:
            return math.nan, math.nan
        mean = sum(values) / n
        if n < 2:
            return mean, math.nan
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        return mean, math.sqrt(var / n)

    def field_for(self, metric: str) -> str:
        return REQUIREMENT_FIELDS.get(metric, metric.split(".", 1)[-1])
