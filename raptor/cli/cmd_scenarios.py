"""`raptor whatif`, `sensitivity` and `recommend`: changes compared with Base."""
from __future__ import annotations

import json
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, eprint, open_model, workspace_for
from raptor.cli.exitcodes import CliError

WHATIF_SCHEMA = "raptor-whatif/1"

_METRICS = (("availability", "Availability", True),
            ("reliability", "Reliability", True),
            ("mtbde", "MTBDE", False),
            ("mdt", "MDT", False),
            ("capacity_weighted_availability", "Delivered fraction", True),
            ("lost_flow", "Lost flow", False),
            ("grand_total", "Grand total", False))


def add_parser(sub: Any) -> None:
    w = sub.add_parser(
        "whatif", help="compare one or more changes with the open model",
        description="Run the model and each scenario with the same seed and "
                    "compare them by paired trials: the same change costs far "
                    "less noise than two independent batches. A scenario is "
                    "the model plus a short list of PATH=VALUE changes; the "
                    "model file is never modified.")
    w.add_argument("model", metavar="MODEL")
    w.add_argument("--change", action="append", default=[], metavar="PATH=VALUE",
                   help="one change for the scenario, repeatable")
    w.add_argument("--name", default="Scenario", metavar="N")
    w.add_argument("--note", default="", metavar="TEXT")
    w.add_argument("--change-file", metavar="FILE",
                   help="JSON list of {name, changes, note} scenarios")
    w.add_argument("-n", "--trials", type=int, metavar="N",
                   help="runs per scenario (default: the model's own)")
    w.add_argument("--precision", metavar="P",
                   help="size the batch first with run-to-precision, e.g. 10%%")
    w.add_argument("--requirement", action="append", default=[],
                   metavar="EXPR", help="e.g. system.ao>=0.995 (repeatable)")
    w.add_argument("--seed", type=int, default=1, metavar="N")
    w.add_argument("-j", "--jobs", type=int, default=1, metavar="N")
    w.add_argument("--save", metavar="FILE",
                   help="write the scenario definition(s) as JSON")
    w.add_argument("-f", "--format", choices=("text", "json"), default="text")
    w.set_defaults(func=whatif_command)

    s = sub.add_parser(
        "sensitivity", help="which inputs the answer is most sensitive to",
        description="One-at-a-time tornado: vary each candidate input at a "
                    "low and a high value, run every variant with Base's "
                    "seed, and rank the paired swings. This screens inputs; "
                    "it cannot show interactions.")
    s.add_argument("model", metavar="MODEL")
    s.add_argument("--top", type=int, default=10, metavar="N",
                   help="how many inputs to screen (default 10)")
    s.add_argument("-n", "--trials", type=int, metavar="N",
                   help="runs per variant (default: the model's own)")
    s.add_argument("--metric", default="availability", metavar="FIELD",
                   help="per-run field to watch (default availability)")
    s.add_argument("--seed", type=int, default=1, metavar="N")
    s.add_argument("-j", "--jobs", type=int, default=1, metavar="N")
    s.add_argument("-f", "--format", choices=("text", "json"), default="text")
    s.set_defaults(func=sensitivity_command)

    r = sub.add_parser(
        "recommend", help="test a change for each top downtime driver",
        description="Find the blocks that cause the most system downtime, "
                    "propose one change for each from the cause that "
                    "dominates its outages (halve the repair time, one more "
                    "spare, one more crew, ...), and test every change "
                    "against the open model with paired runs. Each result "
                    "states the change, the measured gain and the cost. The "
                    "verdict says what was measured, never whether a change is "
                    "worth its price. The model file is never modified.")
    r.add_argument("model", metavar="MODEL")
    r.add_argument("--run", metavar="RUN_ID",
                   help="a stored run made with attribution to take the "
                        "drivers from (default: run Base with attribution "
                        "first)")
    r.add_argument("--top", type=int, default=3, metavar="N",
                   help="how many top drivers get a change (default 3)")
    r.add_argument("-n", "--trials", type=int, metavar="N",
                   help="runs per change (default: the model's own)")
    r.add_argument("--min-gain", type=float, metavar="POINTS",
                   help="smallest availability gain, in points, called "
                        "'clearly helps' (default: removing 5 %% of Base's downtime)")
    r.add_argument("--seed", type=int, default=1, metavar="N")
    r.add_argument("-j", "--jobs", type=int, default=1, metavar="N")
    r.add_argument("-f", "--format", choices=("text", "json"), default="text")
    r.set_defaults(func=recommend_command)


def recommend_command(args: Any) -> int:
    from raptor.api.insight.drivers import NoAttribution
    from raptor.api.scenarios.recommend import recommend, text_lines

    if args.top < 1:
        raise CliError("--top must be at least 1", X.USAGE)
    if args.min_gain is not None and not 0 <= args.min_gain <= 100:
        raise CliError("--min-gain is availability points, 0 to 100", X.USAGE)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = recommend(
                ws, mid, run_id=args.run, top=args.top, trials=args.trials,
                seed=args.seed, workers=max(1, args.jobs),
                min_gain=None if args.min_gain is None else args.min_gain / 100,
                progress=lambda done, total, label:
                eprint(f"[{done}/{total}] {label}") if label else None)
        except NoAttribution as exc:
            raise CliError(str(exc), X.REFUSED) from exc
    if args.format == "json":
        emit_json(result.to_dict(), None)
        return X.OK
    print("\n".join(text_lines(result)))
    return X.OK


def sensitivity_command(args: Any) -> int:
    from raptor.api.scenarios.sensitivity import default_inputs, tornado

    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        inputs = default_inputs(ws.get(mid).rbd, limit=args.top)
        if not inputs:
            raise CliError("no input could be varied in this model", X.REFUSED)
        result = tornado(
            ws, mid, inputs, trials=args.trials, metric=args.metric,
            seed=args.seed, workers=max(1, args.jobs),
            progress=lambda done, total, label:
            eprint(f"[{done}/{total}] {label}") if label else None)
    if args.format == "json":
        emit_json({**result, "schema": "raptor-sensitivity/1"}, None)
        return X.OK
    _print_sensitivity(result)
    return X.OK


def _print_sensitivity(result: dict[str, Any]) -> None:
    base = result["base"]
    shown = (f"{100 * base['mean']:.5g} %" if result["metric"] in
             ("availability", "reliability", "capacity_weighted_availability")
             else f"{base['mean']:.6g}")
    print(f"Sensitivity of {result['metric']} (base {shown}), "
          f"{result['batches']} batches x {result['trials']} paired runs")
    for row in result["inputs"]:
        if row["swing"] is None:
            print(f"  {row['input']['name']:<28} no data")
            continue
        marks = "" if row["distinguishable"] else "  (not distinguishable)"
        small = "  small" if row.get("small") else ""
        print(f"  {row['input']['name']:<28} swing {row['swing']:.4g}"
              f"  low {row['low']['effect']:+.4g}  high "
              f"{row['high']['effect']:+.4g}{marks}{small}")
    print(f"\n{result['caption']}")
    print(result["note"])


def _parse_requirement(text: str) -> dict[str, Any]:
    for op in (">=", "<=", "!=", "==", ">", "<"):
        if op in text:
            metric, value = text.split(op, 1)
            try:
                target = float(value)
            except ValueError:
                raise CliError(f"--requirement {text!r}: {value!r} is not a "
                               "number", X.USAGE) from None
            return {"metric": metric.strip(), "op": op, "value": target,
                    "label": text.strip()}
    raise CliError(f"--requirement {text!r}: use a comparison, e.g. "
                   "system.ao>=0.995", X.USAGE)


def _scenario(args: Any) -> dict[str, Any]:
    if args.change_file:
        try:
            from pathlib import Path
            data = json.loads(Path(args.change_file).read_text(
                encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CliError(f"cannot read {args.change_file}: {exc}",
                           X.USAGE) from exc
        if not isinstance(data, list):
            raise CliError("--change-file must hold a JSON list of scenarios",
                           X.USAGE)
        return {"scenarios": data}
    return {"scenarios": [{"name": args.name, "changes": list(args.change),
                           "note": args.note}]}


def whatif_command(args: Any) -> int:
    from raptor.api.scenarios import Scenario, compare
    from raptor.api.scenarios.stopping import Precision

    doc = _scenario(args)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, indent=2)
            handle.write("\n")
        eprint(f"wrote {len(doc['scenarios'])} scenario(s) to {args.save}")
        if not args.change_file:
            return X.OK
    precision = None
    if args.precision:
        text = args.precision
        try:
            relative = (float(text.rstrip("%")) / 100 if text.endswith("%")
                        else float(text))
            precision = Precision(relative=relative,
                                  min_trials=args.trials or 50,
                                  max_trials=max(args.trials or 50, 20000))
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
    try:
        scenarios = [Scenario.from_dict(row) for row in doc["scenarios"]]
    except (TypeError, AttributeError) as exc:
        raise CliError(f"bad scenario: {exc}", X.USAGE) from exc
    requirements = [_parse_requirement(text) for text in args.requirement]
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        comparison = compare(
            ws, mid, scenarios, trials=args.trials, precision=precision,
            workers=max(1, args.jobs), seed=args.seed,
            requirements=requirements,
            progress=(lambda done, total, label:
                      eprint(f"[{done}/{total}] {label}") if label else None))
    if args.format == "json":
        emit_json({**comparison.to_dict(), "schema": WHATIF_SCHEMA}, None)
        return X.OK
    _print_comparison(comparison)
    return X.OK


def _print_comparison(comparison: Any) -> None:
    print(f"Base: {comparison.trials} paired runs, seed {comparison.seed}")
    for row in comparison.rows:
        print(f"\n{row['name']}  ({'; '.join(row['changes']) or 'no changes'})")
        if row.get("error"):
            print(f"  error: {row['error']}")
            continue
        for key, label, fraction in _METRICS:
            metric = row["metrics"].get(key) or {}
            value = (metric.get("value") or {}).get("mean")
            delta = (metric.get("delta") or {}).get("mean")
            sem = (metric.get("delta") or {}).get("sem")
            if value is None:
                continue
            shown = f"{100 * value:.5g} %" if fraction else f"{value:,.6g}"
            change = (f"{100 * delta:+.3g} pts" if fraction and delta is not None
                      else f"{delta:+,.3g}" if delta is not None else "—")
            error = (f" ± {100 * sem:.3g}" if fraction and sem
                     else f" ± {sem:.3g}" if sem else "")
            chance = metric.get("p_better")
            chance_text = f"  P(better) {100 * chance:.3g} %" if chance is not None else ""
            print(f"  {label:<22} {shown:>14}   {change}{error}{chance_text}")
    if comparison.variance_reduction:
        cut = comparison.variance_reduction.get("availability")
        if cut is not None:
            print(f"\nPairing cut the variance by {100 * cut:.3g} %.")
    for requirement in comparison.requirements:
        print(f"\nRequirement {requirement.get('label')}: "
              f"Base {requirement['base']}")
        for name, verdict in (requirement.get("scenarios") or {}).items():
            print(f"  {name}: {verdict}")
    print(f"\n{comparison.caption}")
