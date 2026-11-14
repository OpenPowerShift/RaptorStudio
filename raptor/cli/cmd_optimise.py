"""`raptor optimise spares`: the cheapest stock that meets a target."""
from __future__ import annotations

from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, eprint, open_model, workspace_for
from raptor.cli.exitcodes import CliError

OPTIMISE_SCHEMA = "raptor-optimise-spares/1"


def add_parser(sub: Any) -> None:
    o = sub.add_parser(
        "optimise", help="find the cheapest spares holding that meets a target",
        description="Simulate the analytic spare-stock path with common "
                    "random numbers and recommend the cheapest point whose "
                    "lower confidence bound meets the target. The model file "
                    "is only changed with --apply.")
    o.set_defaults(func=_missing)
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
    p.add_argument("--apply", action="store_true",
                   help="write the recommended stocks into the model")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=optimise_command)


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
                progress=lambda done, total, text:
                eprint(f"[{done}/{total}] {text}") if text else None)
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        recommended = result.get("recommended")
        if args.apply and recommended is not None:
            from raptor.api import paths
            for pool, stock in recommended["stocks"].items():
                paths.apply_assignment(ws, mid, f"pool:{pool}.stock={stock}")
            eprint("applied the recommended stocks to the model")
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
