"""`raptor optimise spares`: the cheapest stock that meets a target."""
from __future__ import annotations

from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, eprint, open_model, workspace_for
from raptor.cli.cmd_run import add_study_progress, study_progress
from raptor.cli.exitcodes import CliError

OPTIMISE_SCHEMA = "raptor-optimise-spares/1"


def add_parser(sub: Any) -> None:
    o = sub.add_parser(
        "optimise", help="find the cheapest spares holding that meets a target",
        description="Simulate the analytic spare-stock path with common "
                    "random numbers and recommend the cheapest point whose "
                    "lower confidence bound meets the target. The model file "
                    "is only changed when the apply flag is given.")
    o.set_defaults(func=_missing)

    f = sub.add_parser(
        "fit", help="fit a life law from experience (no data)",
        description="Turn experience ('one in ten fail by 100, half "
                    "by 300') into Weibull, lognormal or exponential "
                    "parameters. No data needed; the fit is only as good as "
                    "the answers.")
    f.add_argument("--law", required=True,
                   choices=("weibull", "lognormal", "exponential"))
    f.add_argument("--quantile", action="append", default=[],
                   metavar="P=T", help="probability=age, repeatable, e.g. "
                                       "0.1=100")
    f.add_argument("--block", metavar="NAME",
                   help="also print the PATH=VALUE changes that give this "
                        "block the fitted law (for 'raptor set')")
    f.add_argument("--repair", action="store_true",
                   help="with --block, the repair law instead of the failure law")
    f.add_argument("-f", "--format", choices=("text", "json"), default="text")
    f.set_defaults(func=fit_command)
    s = o.add_subparsers(dest="optimise_command", metavar="WHAT")
    p = s.add_parser("spares", help="spare stock levels")
    p.add_argument("model", metavar="MODEL")
    p.add_argument("--target", metavar="EXPR", default="system.ao>=0.995",
                   help="e.g. system.ao>=0.995 (default)")
    p.add_argument("--pools", metavar="NAMES",
                   help="comma list of pool names (default: every finite pool)")
    p.add_argument("--mode", choices=("meet", "curve"), default="meet",
                   help="cheapest that meets (default) or the trade-off curve")
    p.add_argument("--budget", type=int, default=40, metavar="N",
                   help="most simulation points to evaluate (default 40)")
    p.add_argument("-n", "--trials", type=int, metavar="N",
                   help="runs per point (default: the model's own)")
    p.add_argument("--seed", type=int, default=1, metavar="N")
    p.add_argument("-j", "--jobs", type=int, default=1, metavar="N")
    add_study_progress(p)
    p.add_argument("--apply", action="store_true",
                   help="write the recommended stocks into the model")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=optimise_command)

    m = s.add_parser("pm", help="is planned maintenance worthwhile, and when")
    m.add_argument("model", metavar="MODEL")
    m.add_argument("--blocks", metavar="NAMES",
                   help="comma list of block names (default: every block)")
    m.add_argument("--objective", choices=("availability", "cost"),
                   default="availability",
                   help="what to optimise (default availability)")
    m.add_argument("-n", "--trials", type=int, metavar="N",
                   help="runs per point (default: the model's own)")
    m.add_argument("--seed", type=int, default=1, metavar="N")
    m.add_argument("-j", "--jobs", type=int, default=1, metavar="N")
    add_study_progress(m)
    m.add_argument("--apply", action="store_true",
                   help="write the recommended intervals into the model")
    m.add_argument("-f", "--format", choices=("text", "json"), default="text")
    m.set_defaults(func=pm_command)

    r = s.add_parser("resources", help="how many crews or tools are needed")
    r.add_argument("model", metavar="MODEL")
    r.add_argument("--resources", metavar="NAMES",
                   help="comma list of resource names (default: all)")
    r.add_argument("--target", metavar="EXPR", default="system.ao>=0.995",
                   help="e.g. system.ao>=0.995 (default)")
    r.add_argument("--extra", type=int, default=3, metavar="N",
                   help="sizes above the seed to try (default 3)")
    r.add_argument("-n", "--trials", type=int, metavar="N")
    r.add_argument("--seed", type=int, default=1, metavar="N")
    r.add_argument("-j", "--jobs", type=int, default=1, metavar="N")
    add_study_progress(r)
    r.add_argument("--apply", action="store_true",
                   help="write the recommended sizes into the model")
    r.add_argument("-f", "--format", choices=("text", "json"), default="text")
    r.set_defaults(func=resources_command)


def _missing(args: Any) -> int:
    raise CliError("use 'raptor optimise spares MODEL'", X.USAGE)


def _target(text: str) -> tuple[str, float]:
    for op in (">=", "<=", "==", ">"):
        if op in text:
            metric_text, value = text.split(op, 1)
            try:
                return metric_text.strip(), float(value)
            except ValueError:
                raise CliError(f"--target {text!r}: {value!r} is not a "
                               "number", X.USAGE) from None
    raise CliError(f"--target {text!r}: use a comparison, e.g. "
                   "system.ao>=0.995", X.USAGE)


def optimise_command(args: Any) -> int:
    from raptor.api.optimise.spares import optimise_spares

    metric_text, target = _target(args.target)
    metric = {"system.ao": "availability",
              "system.availability": "availability"}.get(metric_text,
                                                         metric_text)
    pools = ([name.strip() for name in args.pools.split(",") if name.strip()]
             if args.pools else None)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = optimise_spares(
                ws, mid, mode=args.mode, target=target, metric=metric,
                pools=pools, max_evaluations=max(1, args.budget),
                trials=args.trials, seed=args.seed,
                workers=max(1, args.jobs),
                progress=study_progress(args))
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        if args.apply and result["changes"]:
            _apply(ws, mid, result["changes"], args.model)
            eprint(f"wrote the recommended stocks to {args.model}")
    if args.format == "json":
        emit_json({**result, "schema": OPTIMISE_SCHEMA}, None)
        return X.OK
    _print_optimise(result)
    return X.OK


def _print_optimise(result: dict[str, Any]) -> None:
    base = result["base"]
    print(f"Base: {base['mean']:.5g} (lower {base['lower']:.5g}), "
          f"holding cost {base['cost']:.6g}")
    for point in result["points"]:
        stocks = ", ".join(f"{name} {stock}"
                           for name, stock in point["stocks"].items())
        mark = "*" if point is result.get("recommended") else " "
        print(f" {mark} {stocks:<32} {point['mean']:.5g} "
              f"(lower {point['lower']:.5g})  cost {point['cost']:.6g}")
    print(f"\n{result['caption']}")
    print(result["note"])


def pm_command(args: Any) -> int:
    from raptor.api.optimise.pm import optimise_pm

    blocks = ([name.strip() for name in args.blocks.split(",") if name.strip()]
              if args.blocks else None)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = optimise_pm(
                ws, mid, blocks=blocks, objective=args.objective,
                trials=args.trials, seed=args.seed, workers=max(1, args.jobs),
                progress=study_progress(args))
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        if args.apply:
            _apply(ws, mid, result["changes"], args.model)
            eprint(f"wrote {len(result['changes'])} change(s) to {args.model}")
    if args.format == "json":
        emit_json({**result, "schema": "raptor-optimise-pm/1"}, None)
        return X.OK
    _print_pm(result)
    return X.OK


def _apply(ws: Any, model_id: str, changes: list[str], model_path: str) -> None:
    """Apply an optimiser's PATH=VALUE changes and write the model back to
    `model_path` (through a temporary file, so a failed save never truncates
    it). Before this saved, --apply edited a throwaway copy and the model
    file never changed."""

    from raptor.api import build, paths
    with build.no_layout_on_save():
        # together: a law and its parameters are one change (applying the
        # law first checked it against the old parameters and failed)
        paths.apply_assignments(ws, model_id, list(changes))
    write_back(ws, model_id, model_path)


def write_back(ws: Any, model_id: str, model_path: str) -> None:
    """Write the model to `model_path` through a temporary file, so a failed
    save never truncates it."""
    from pathlib import Path
    dest = Path(model_path)
    fmt = "rbd" if dest.suffix.lower() == ".rbd" else "rbds"
    try:
        res = ws.save_model_as(model_id, str(dest.with_name(
            dest.stem + ".raptor-tmp" + dest.suffix)), fmt=fmt)
        Path(res["path"]).replace(dest)
    except OSError as exc:
        raise CliError(f"cannot write {dest}: {exc}", X.OUTPUT) from exc


def _print_pm(result: dict[str, Any]) -> None:
    for advice in result["advice"]:
        state = ("worthwhile" if advice["worthwhile"]
                 else "not worthwhile" if advice["worthwhile"] is False
                 else "screen incomplete")
        interval = (f", about {advice['interval']:.4g}"
                    if advice["interval"] else "")
        print(f"{advice['block']}: {state}{interval} — {advice['reason']}")
    print()
    for recommendation in result["recommendations"]:
        print(f"{recommendation['block']}: {recommendation['option']} "
              f"({recommendation['mean']:.6g})"
              + ("" if recommendation["worthwhile"] else "  keep as is"))
    print(f"\n{result['caption']}")
    print(result["note"])


def resources_command(args: Any) -> int:
    from raptor.api.optimise.resources import optimise_resources

    _metric_text, target = _target(args.target)
    names = ([name.strip() for name in args.resources.split(",")
              if name.strip()] if args.resources else None)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = optimise_resources(
                ws, mid, resources=names, target=target,
                extra=max(0, args.extra), trials=args.trials, seed=args.seed,
                workers=max(1, args.jobs),
                progress=study_progress(args))
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        if args.apply:
            _apply(ws, mid, result["changes"], args.model)
            eprint(f"wrote the recommended sizes to {args.model}")
    if args.format == "json":
        emit_json({**result, "schema": "raptor-optimise-resources/1"}, None)
        return X.OK
    _print_resources(result)
    return X.OK


def _print_resources(result: dict[str, Any]) -> None:
    for recommendation in result["recommendations"]:
        print(f"{recommendation['resource']}: {recommendation['units']} "
              f"({recommendation['mean']:.5g}) — {recommendation['why']}")
        for row in recommendation["curve"]:
            cost = (f"  cost {row['cost']:,.6g}" if row.get("cost") is not None else "")
            print(f"   {row['units']:>3} crews  {row['mean']:.5g} "
                  f"(lower {row['lower']:.5g}){cost}")
    print(f"\n{result['caption']}")
    print(result["note"])


def fit_command(args: Any) -> int:
    import shlex
    import sys

    from raptor.api.distributions import from_quantiles, experience_changes

    quantiles: dict[float, float] = {}
    for text in args.quantile:
        if "=" not in text:
            raise CliError(f"--quantile {text!r} is not P=T", X.USAGE)
        probability, age = text.split("=", 1)
        try:
            quantiles[float(probability)] = float(age)
        except ValueError:
            raise CliError(f"--quantile {text!r}: both sides must be "
                           "numbers", X.USAGE) from None
    try:
        fit = from_quantiles(args.law, quantiles)
    except ValueError as exc:
        raise CliError(str(exc), X.USAGE) from exc
    if args.block:
        fit["changes"] = experience_changes(fit, args.block,
                                           "repair" if args.repair else "fail")
    if args.format == "json":
        emit_json(fit, None)
        return X.OK
    params = ", ".join(f"{name} {value:.6g}"
                       for name, value in fit["params"].items())
    print(f"{fit['law']}: {params}")
    print(f"mean {fit['mean']:.6g}")
    for probability, age in fit["fitted"].items():
        print(f"  P{probability:.2f} = {age:.6g}")
    print(fit["shape_note"])
    for warning in fit.get("warnings") or []:
        print(f"warning: {warning}", file=sys.stderr)
    if fit.get("changes"):
        print("raptor set MODEL --in-place "
              + " ".join(shlex.quote(c) for c in fit["changes"]))
    return X.OK
