"""Spares rules.

Supplies are a spare pool (blocks with `sparing_type` Pooled) or one block's
own stock (Custom). The estimates assume failures arrive as a Poisson
process at the blocks' mean rates with the blocks always running, so they
are marked heuristic: the simulation is the real answer.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from raptor.api.advisor import text
from raptor.api.advisor.context import ModelContext, on
from raptor.api.advisor.finding import Finding, Fix, Target
from raptor.api.advisor.registry import rule
from raptor.api.advisor.stats import poisson_cdf, smallest_stock

#: An empty-shelf chance above this is worth saying.
STOCKOUT_P = 0.05


@dataclass(frozen=True)
class Supply:
    label: str                 # "Pool Seals" / "Pump_A's own stock"
    target: Target
    row: dict
    rate: float                # demands per unit time
    block_id: int | None       # set for a block's own (Custom) stock

    @property
    def stock(self) -> int:
        return int(self.row.get("stock") or 0)

    @property
    def restock(self) -> tuple[int, float]:
        """(spares per delivery, time between deliveries); (0, 0) when off."""
        if not on(self.row.get("rso")):
            return 0, 0.0
        return int(self.row.get("new_spares") or 0), float(self.row.get("arrive_every") or 0.0)

    @property
    def reorders(self) -> bool:
        """Stock-level or emergency ordering is on: every shortage starts an
        order, so the supply is not capped at stock plus routine deliveries
        (counting only those warned "will run out" on models that reorder,
        and suggested a stock of 12 where the reorder loop already copes)."""
        return on(self.row.get("slo")) or on(self.row.get("eso"))

    def fix_stock(self, value: int) -> tuple[Fix, ...]:
        if self.block_id is None:
            return ()
        return (Fix(f"Set stock to {value}", (f"block:{self.block_id}.stock={value}",)),)


def supplies(ctx: ModelContext) -> Iterator[Supply]:
    for pool in ctx.pools:
        name = str(pool.get("name"))
        members = ctx.pool_members.get(name, [])
        rate = sum(ctx.failure_rate(b) or 0.0 for b in members)
        if members and rate > 0:
            yield Supply(f"Pool {name}", Target("pool", None, name, "stock"), pool, rate, None)
    for b in ctx.blocks:
        if b.get("sparing_type") == "Custom":
            rate = ctx.failure_rate(b) or 0.0
            if rate > 0:
                yield Supply(f"{b['name']}'s own stock", ctx.block_target(b, "stock"),
                             b, rate, int(b["id"]))


def _certain_shortfall(demand: float, supply: float) -> bool:
    """More spares expected to be needed than can arrive, and at least one
    needed: below one expected demand it is a risk (M-SPR-003), not a certainty."""
    return demand > supply and demand >= 1.0


@rule("M-SPR-001", title="Spares are unlimited everywhere",
      category="spares", basis="exact", severity="info")
def all_infinite(ctx: ModelContext) -> Iterable[Finding]:
    if ctx.blocks and all(b.get("sparing_type") == "Infinite" for b in ctx.blocks):
        yield Finding(
            rule="M-SPR-001", severity="info", category="spares",
            title="Spares are unlimited everywhere",
            message="Spares are unlimited for every block: the results assume a "
                    "spare is always on the shelf.",
            why="Unlimited spares is the default. If spares are limited in "
                "reality, availability will be lower than this model predicts.",
            basis="exact", impact=0.1, doc="advisor.html#M-SPR-001")


@rule("M-SPR-002", title="Spares will run out",
      category="spares", basis="heuristic", severity="warning")
def supply_below_demand(ctx: ModelContext) -> Iterable[Finding]:
    run = ctx.run_length
    if not run:
        return
    for s in supplies(ctx):
        if s.reorders:
            continue                     # M-SPR-003 judges the lead time
        q, every = s.restock
        deliveries = math.floor(run / every) if q and every > 0 else 0
        supply = s.stock + q * deliveries
        demand = s.rate * run
        if not _certain_shortfall(demand, supply):
            continue
        need = math.ceil(demand) - q * deliveries
        yield Finding(
            rule="M-SPR-002", severity="warning", category="spares",
            title="Spares will run out",
            message=(f"{s.label} will run out: about {text.fmt_num(demand)} spares are "
                     f"needed over the run and only {supply} can arrive."),
            why="Expected failures over the run exceed the starting stock plus "
                "every routine delivery. Once the shelf is empty each failure "
                "waits for the next delivery or an emergency order.",
            targets=(s.target,),
            evidence={"expected_demand": demand, "supply": supply, "stock": s.stock,
                      "deliveries": deliveries, "suggested_stock": need},
            basis="heuristic", impact=min(1.0, (demand - supply) / demand),
            fixes=s.fix_stock(need), doc="advisor.html#M-SPR-002")


@rule("M-SPR-003", title="The shelf may be empty when needed",
      category="spares", basis="heuristic", severity="advice")
def stockout_risk(ctx: ModelContext) -> Iterable[Finding]:
    run = ctx.run_length
    if not run:
        return
    for s in supplies(ctx):
        if on(s.row.get("slo")):
            yield from _lead_time_risk(s, ctx)
            continue
        q, every = s.restock
        restocked = bool(q and every > 0)
        deliveries = math.floor(run / every) if restocked else 0
        if not s.reorders and _certain_shortfall(s.rate * run, s.stock + q * deliveries):
            continue                                   # M-SPR-002 says it
        horizon = min(every, run) if restocked else run
        mean = s.rate * horizon
        p_out = 1.0 - poisson_cdf(s.stock, mean)
        if p_out <= STOCKOUT_P:
            continue
        need = smallest_stock(mean, STOCKOUT_P)
        period = (f"between deliveries ({text.fmt_time(horizon, ctx.units)})"
                  if restocked else "over the run")
        yield Finding(
            rule="M-SPR-003", severity="advice", category="spares",
            title="The shelf may be empty when needed",
            message=(f"{s.label} has a {text.fmt_pct(p_out)} chance of running out "
                     f"{period}: stock {s.stock}, about {text.fmt_num(mean)} needed."),
            why="Failures do not arrive evenly. Even when the average demand is "
                "covered, an unlucky cluster can empty the shelf. "
                f"A stock of {need} keeps that chance at or below "
                f"{text.fmt_pct(STOCKOUT_P)}.",
            targets=(s.target,),
            evidence={"p_stockout": p_out, "stock": s.stock, "mean_demand": mean,
                      "horizon": horizon, "suggested_stock": need},
            basis="heuristic", impact=p_out,
            fixes=s.fix_stock(need), doc="advisor.html#M-SPR-003")


def _lead_time_risk(s: Supply, ctx: ModelContext) -> Iterable[Finding]:
    """With stock-level ordering an order goes out when the shelf falls to
    the reorder level; the shelf runs dry only if more than that level fail
    before the order arrives."""
    level = int(s.row.get("level") or 0)
    lead = float(s.row.get("slo_time") or 0.0)
    if lead <= 0:
        return
    mean = s.rate * lead
    p_out = 1.0 - poisson_cdf(level, mean)
    if p_out <= STOCKOUT_P:
        return
    need = smallest_stock(mean, STOCKOUT_P)
    yield Finding(
        rule="M-SPR-003", severity="advice", category="spares",
        title="The shelf may be empty when needed",
        message=(f"{s.label} has a {text.fmt_pct(p_out)} chance of running out "
                 f"while an order is on its way ({text.fmt_time(lead, ctx.units)}): "
                 f"it reorders at {level}, about {text.fmt_num(mean)} needed in that time."),
        why="A stock-level order is placed when the shelf falls to the reorder "
            "level, so only that many spares cover the lead time. A reorder "
            f"level of {need} keeps the chance at or below {text.fmt_pct(STOCKOUT_P)}.",
        targets=(s.target,),
        evidence={"p_stockout": p_out, "reorder_level": level, "mean_demand": mean,
                  "horizon": lead, "suggested_level": need},
        basis="heuristic", impact=p_out, doc="advisor.html#M-SPR-003")


def _ordering_rows(ctx: ModelContext) -> Iterator[tuple[str, Target, dict]]:
    for pool in ctx.pools:
        yield f"Pool {pool.get('name')}", Target("pool", None, str(pool.get("name"))), pool
    for b in ctx.blocks:
        if b.get("sparing_type") == "Custom":
            yield f"{b['name']}'s own stock", ctx.block_target(b), b


@rule("M-SPR-006", title="Emergency order is slower than routine delivery",
      category="spares", basis="exact", severity="warning")
def slow_emergency(ctx: ModelContext) -> Iterable[Finding]:
    for label, target, row in _ordering_rows(ctx):
        if not (on(row.get("eso")) and on(row.get("rso"))):
            continue
        emer, every = float(row.get("emer_time") or 0), float(row.get("arrive_every") or 0)
        if every > 0 and emer >= every:
            yield Finding(
                rule="M-SPR-006", severity="warning", category="spares",
                title="Emergency order is slower than routine delivery",
                message=(f"{label}: an emergency order takes {text.fmt_time(emer, ctx.units)}, "
                         f"no faster than the routine delivery every "
                         f"{text.fmt_time(every, ctx.units)}."),
                why="An emergency order is meant to arrive sooner than waiting for "
                    "the next routine delivery. Here it does not, so it only adds cost.",
                targets=(target,), evidence={"emer_time": emer, "arrive_every": every},
                basis="exact", impact=0.3, doc="advisor.html#M-SPR-006")


@rule("M-SPR-007", title="Reorder level at or above the starting stock",
      category="spares", basis="exact", severity="warning")
def reorder_at_start(ctx: ModelContext) -> Iterable[Finding]:
    for label, target, row in _ordering_rows(ctx):
        if not on(row.get("slo")):
            continue
        level, stock = int(row.get("level") or 0), int(row.get("stock") or 0)
        if level >= stock:
            yield Finding(
                rule="M-SPR-007", severity="warning", category="spares",
                title="Reorder level at or above the starting stock",
                message=(f"{label} reorders when stock falls to {level}, which is at or "
                         f"above its starting stock of {stock}: it orders at time zero."),
                why="A stock-level order is placed when the shelf falls to the "
                    "reorder level. A level at or above the starting stock "
                    "triggers an order immediately.",
                targets=(target,), evidence={"level": level, "stock": stock},
                basis="exact", impact=0.3, doc="advisor.html#M-SPR-007")


@rule("M-SPR-008", title="Spare pool not used",
      category="spares", basis="exact", severity="advice")
def unused_pool(ctx: ModelContext) -> Iterable[Finding]:
    for pool in ctx.pools:
        name = str(pool.get("name"))
        if not ctx.pool_members.get(name):
            yield Finding(
                rule="M-SPR-008", severity="advice", category="spares",
                title="Spare pool not used",
                message=f"Pool {name} is defined but no block draws from it.",
                why="A pool only matters when a block's sparing is set to Pooled "
                    "and names it.",
                targets=(Target("pool", None, name),), basis="exact", impact=0.1,
                doc="advisor.html#M-SPR-008")
