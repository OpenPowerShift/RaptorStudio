"""MCP tools: the optimisers (plan WP-4.1, 4.2 and 4.5).

Registers ``optimise_spares``, ``optimise_pm`` and ``optimise_resources``,
and ``fit_from_experience`` (a life law from experience). Each search with paired simulation, never edits the open model, and returns
the recommendation as ``changes``: PATH=VALUE edits to apply with
``update_element``, ``manage_spare_pools`` or ``manage_resources`` (or
``raptor set``). Long searches report progress and can be cancelled.
"""
from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.jobs import run_job
from raptor.mcp.result import ADDITIVE, READ_ONLY, guarded, ok
from raptor.mcp.server import mcp



#: Most points optimise_spares may simulate, and most crew sizes above the
#: offered load optimise_resources may try.
MAX_SPARE_EVALUATIONS = 200
MAX_EXTRA_UNITS = 20
#: Most named blocks, pools or resources one optimiser call may take.
MAX_SUBJECTS = 50


def _check_subjects(items: list[str] | None, name: str) -> None:
    if items is not None and len(items) > MAX_SUBJECTS:
        raise ValueError(f"{name} accepts at most {MAX_SUBJECTS} names")


def _fmt(value: Any) -> str:
    return "—" if value is None else f"{value:.6g}"


def _changes_md(changes: list[str]) -> str:
    if not changes:
        return "\n\nNo change to apply."
    return "\n\nChanges to apply:\n" + "\n".join(f"- `{c}`" for c in changes)


@mcp.tool(annotations=ADDITIVE("Optimise spare stock"))
@guarded
async def optimise_spares(
    model_id: str,
    target: float | None = 0.995,
    metric: Literal["availability", "mdt"] = "availability",
    mode: Literal["meet", "curve"] = "meet",
    pools: list[str] | None = None,
    max_evaluations: int = 40,
    trials: int | None = None,
    seed: int | None = None,
    workers: int | None = None,
    timeout_s: float = 1800.0,
    ctx: Context | None = None,
) -> CallToolResult:
    """The cheapest spare stock that meets a target, found by simulation.

    Seeds a path of stock levels from a Poisson backorder model (one more
    unit where it removes the most expected shortage per unit cost) and
    simulates each point with paired trials. `mode="meet"` stops at the
    cheapest point whose lower confidence bound meets `target` on `metric`
    ('availability' or 'mdt'; an MDT target is met by the upper bound);
    `mode="curve"` returns the whole evaluated frontier. `pools` limits the
    search to those spare pools (default: every finite pool with demand).
    `max_evaluations` caps the number of points simulated.

    Returns the base point, every evaluated point ({stocks, cost, mean,
    lower, upper, changes}), the `recommended` point, `target_met`, and
    `changes`: the PATH=VALUE edits for the recommended stock (apply them
    with `manage_spare_pools` or `raptor set`). A point that misses the
    target is said to miss it, never presented as meeting it. The open model
    is never modified.
    """
    from raptor.api.optimise.spares import optimise_spares as search
    context.whole_number(max_evaluations, "max_evaluations", minimum=1,
                         maximum=MAX_SPARE_EVALUATIONS)
    _check_subjects(pools, "pools")
    context.check_study(model_id, trials, timeout_s, workers=workers,
                        variants=max_evaluations)
    ws = context.workspace()

    def work(abort, progress):
        return search(ws, model_id, mode=mode, target=target, metric=metric,
                      pools=pools, max_evaluations=max_evaluations,
                      trials=trials, seed=seed, workers=max(1, workers or 1),
                      timeout_s=timeout_s, abort=abort,
                      progress=lambda done, total, text: progress(done, total, text))

    result = await run_job(ctx, work, label="Searching spare stock")
    lines = ["| Stock | Cost | Mean | Lower bound |", "|---|---|---|---|"]
    for point in result["points"]:
        stock = ", ".join(f"{name} {n}" for name, n in point["stocks"].items())
        mark = " ★" if point is result.get("recommended") else ""
        lines.append(f"| {stock}{mark} | {point['cost']:.6g} | {_fmt(point['mean'])} | "
                     f"{_fmt(point['lower'])} |")
    md = f"{result['caption']}\n\n" + "\n".join(lines) + f"\n\n{result['note']}"
    return ok(result, md + _changes_md(result["changes"]))


@mcp.tool(annotations=ADDITIVE("Optimise preventive maintenance"))
@guarded
async def optimise_pm(
    model_id: str,
    blocks: list[str] | None = None,
    objective: Literal["availability", "cost"] = "availability",
    trials: int | None = None,
    seed: int | None = None,
    workers: int | None = None,
    timeout_s: float = 1800.0,
    ctx: Context | None = None,
) -> CallToolResult:
    """Is planned maintenance worthwhile on each block, and at what interval.

    Screens each block (`blocks`, default every block with a life law) with
    the age-replacement formula: PM pays only for a wearing-out part (Weibull
    shape above 1) whose planned job is cheaper than a failure. Then it
    simulates no PM and 0.5 to 2 times the formula's interval with paired
    trials, on `objective` 'availability' or 'cost'. The recommendation
    comes from the simulation, not the formula.

    Returns `advice` (the screen, with its reason per block), `rows` (every
    option simulated, with its `changes`), `recommendations` (the best
    option per block, `worthwhile`, its `changes`) and `changes`: the
    PATH=VALUE edits for the recommendations worth making (apply them with
    `update_element` or `raptor set`). The open model is never modified.
    """
    from raptor.api.optimise.pm import optimise_pm as search
    _check_subjects(blocks, "blocks")
    context.check_study(model_id, trials, timeout_s, workers=workers)
    ws = context.workspace()

    def work(abort, progress):
        return search(ws, model_id, blocks=blocks, objective=objective, trials=trials,
                      seed=seed, workers=max(1, workers or 1), timeout_s=timeout_s,
                      abort=abort,
                      progress=lambda done, total, text: progress(done, total, text))

    result = await run_job(ctx, work, label="Testing maintenance intervals")
    lines = ["| Block | Screen | Best option | Mean | Worth it |", "|---|---|---|---|---|"]
    best = {r["block"]: r for r in result["recommendations"]}
    for advice in result["advice"]:
        rec = best.get(advice["block"]) or {}
        screen = ("worthwhile" if advice["worthwhile"]
                  else "not worthwhile" if advice["worthwhile"] is False else "incomplete")
        lines.append(f"| {advice['block']} | {screen} | {rec.get('option', '—')} | "
                     f"{_fmt(rec.get('mean'))} | {'yes' if rec.get('worthwhile') else 'no'} |")
    md = f"{result['caption']}\n\n" + "\n".join(lines) + f"\n\n{result['note']}"
    return ok(result, md + _changes_md(result["changes"]))


@mcp.tool(annotations=ADDITIVE("Optimise crew sizes"))
@guarded
async def optimise_resources(
    model_id: str,
    target: float | None = 0.995,
    resources: list[str] | None = None,
    extra: int = 3,
    trials: int | None = None,
    seed: int | None = None,
    workers: int | None = None,
    timeout_s: float = 1800.0,
    ctx: Context | None = None,
) -> CallToolResult:
    """How many crews or tools each repair resource needs to meet a target.

    Starts from the offered load (failures per unit time × repair time) and
    simulates sizes from one below it to `extra` above, with paired trials.
    The recommendation is the smallest size whose lower confidence bound on
    availability meets `target`; when none does, the best measured size,
    said so. `resources` limits the search to those resources.

    Returns every size simulated (`rows`), `recommendations` ({resource,
    units, mean, lower, cost, why, changes}; `cost` is the run's mean total
    cost, present when the model has cost analysis on) and `changes`: the PATH=VALUE edits
    for the recommended sizes (apply them with `manage_resources` or `raptor
    set`). The open model is never modified.
    """
    from raptor.api.optimise.resources import optimise_resources as search
    context.whole_number(extra, "extra", minimum=0, maximum=MAX_EXTRA_UNITS)
    _check_subjects(resources, "resources")
    context.check_study(model_id, trials, timeout_s, workers=workers)
    ws = context.workspace()

    def work(abort, progress):
        return search(ws, model_id, resources=resources, target=target,
                      extra=extra, trials=trials, seed=seed,
                      workers=max(1, workers or 1), timeout_s=timeout_s, abort=abort,
                      progress=lambda done, total, text: progress(done, total, text))

    result = await run_job(ctx, work, label="Sizing crews")
    lines = ["| Resource | Units | Mean | Lower bound | Mean cost | Why |",
             "|---|---|---|---|---|---|"]
    for rec in result["recommendations"]:
        lines.append(f"| {rec['resource']} | {rec['units']} | {_fmt(rec['mean'])} | "
                     f"{_fmt(rec['lower'])} | {_fmt(rec.get('cost'))} | {rec['why']} |")
    md = f"{result['caption']}\n\n" + "\n".join(lines) + f"\n\n{result['note']}"
    return ok(result, md + _changes_md(result["changes"]))


@mcp.tool(annotations=READ_ONLY("Fit a law from experience"))
@guarded
def fit_from_experience(
    law: Literal["weibull", "lognormal", "exponential"],
    quantiles: list[dict[str, float]],
    block: str | None = None,
    use: Literal["fail", "repair"] = "fail",
) -> CallToolResult:
    """Turn experience into a life law, with no failure data.

    Each answer is a share and an age: "one in ten fail by 100 hours,
    half by 300" is `[{probability: 0.1, age: 100}, {probability: 0.5,
    age: 300}]`. Two answers solve the law exactly (one is enough for an
    exponential); three or more are a least-squares fit. Weibull suits wear-out lives, lognormal repair
    times, exponential random failures. Inconsistent answers (a higher share
    at an earlier age) are refused by name. The fit is only as good as the
    answers.

    Returns `params` (named), `slots` (in the model's parameter order),
    `mean`, `fitted` (the law's ages at 1, 10, 50, 90 and 99 %), `stated`,
    `shape_note` and `warnings` (a loose fit, or answers so close that the
    law is nearly a fixed age). With `block`, also `changes` (PATH=VALUE, for `raptor
    set`) and `update` (the fields to pass to `update_element`, law and
    parameters together) for its failure (`use="fail"`) or repair law.
    Nothing is changed here.
    """
    from raptor.api.distributions import fit_document, from_quantiles
    try:
        answers = {float(q["probability"]): float(q["age"]) for q in quantiles}
    except (KeyError, TypeError, ValueError):
        raise ValueError("each answer needs a numeric 'probability' and "
                         "'age', e.g. {\"probability\": 0.1, \"age\": 100}") from None
    fit = from_quantiles(law, answers)
    out = fit_document(fit, block, use)
    params = ", ".join(f"{name} {value:.6g}" for name, value in fit["params"].items())
    rows = "\n".join(f"| {100 * p:g} % | {t:.6g} |" for p, t in fit["fitted"].items())
    warnings = "".join(f"\n\n**Warning:** {text}" for text in fit["warnings"])
    md = (f"**{law}**: {params}; mean {fit['mean']:.6g}. {fit['shape_note']}{warnings}\n\n"
          f"| Share | Age |\n|---|---|\n{rows}")
    return ok(out, md + (_changes_md(out["changes"]) if block else ""))
