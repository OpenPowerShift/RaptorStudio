"""Structure and redundancy rules."""
from __future__ import annotations

from collections.abc import Iterable

from raptor.api.advisor.context import ModelContext
from raptor.api.advisor.finding import Finding, Fix
from raptor.api.advisor.registry import rule

END_NODE = 3


def _reaction(b: dict) -> str:
    parts = [(float(b.get("dep_idle") or 0), "idle"),
             (float(b.get("dep_pm") or 0), "into maintenance"),
             (float(b.get("dep_fail") or 0), "to fail")]
    return max(parts)[1]


@rule("M-STR-002", title="Backup depends on the unit it backs up",
      category="redundancy", basis="exact", severity="warning")
def defeated_redundancy(ctx: ModelContext) -> Iterable[Finding]:
    for group in ctx.groups:
        ids = {i for kind, i in group.members if kind == "block"}
        for bid in sorted(ids):
            b = ctx.block_by_id.get(bid)
            if b is None or b.get("dep_type") != "RBDBlock":
                continue
            master = int(b.get("dep_num") or 0)
            if master == bid or master not in ids:
                continue
            reacting = sum(float(b.get(f) or 0) for f in ("dep_idle", "dep_pm", "dep_fail"))
            if reacting <= 0:
                continue
            a = ctx.block_by_id[master]
            node = group.node
            yield Finding(
                rule="M-STR-002", severity="warning", category="redundancy",
                title="Backup depends on the unit it backs up",
                message=(f"{b['name']} backs up {a['name']} at {node['name']} but depends on "
                         f"it: when {a['name']} goes down, {b['name']} is forced "
                         f"{_reaction(b)}, so the pair gives no redundancy."),
                why="A dependent block reacts when the element it follows goes down. "
                    "If that element is the one it is meant to replace, the backup is "
                    "unavailable exactly when it is needed.",
                targets=(ctx.block_target(b, "dep_type"), ctx.block_target(a)),
                evidence={"dependent": b["name"], "follows": a["name"],
                          "node": node["name"], "reacting_percent": reacting},
                basis="exact", impact=min(1.0, reacting / 100.0),
                # dep_type None alone left dep_num pointing at the master, a
                # dependency validation rejects; the GUI clears both
                fixes=(Fix("Remove the dependency",
                           (f"block:{bid}.dep_type=None",
                            f"block:{bid}.dep_num=0")),),
                doc="advisor.html#M-STR-002")


@rule("M-STR-003", title="Parallel paths that are all required",
      category="redundancy", basis="exact", severity="advice")
def k_equals_n(ctx: ModelContext) -> Iterable[Finding]:
    for group in ctx.groups:
        node = group.node
        if int(node.get("type") or 0) == END_NODE or group.k != group.n:
            continue
        yield Finding(
            rule="M-STR-003", severity="advice", category="redundancy",
            title="Parallel paths that are all required",
            message=(f"{node['name']} needs all {group.n} of its {group.n} inputs: it is "
                     f"drawn in parallel but behaves as a series arrangement."),
            why="A node is up when at least k of its n inputs are up. With k equal "
                "to n, losing any one input takes the node down, so there is no "
                "redundancy here.",
            targets=(ctx.node_target(node, "k"),),
            evidence={"k": group.k, "n": group.n},
            basis="exact", impact=0.5,
            fixes=(Fix(f"Need only {group.n - 1} of {group.n}",
                       (f"node:{int(node['id'])}.k={group.n - 1}",)),),
            doc="advisor.html#M-STR-003")
