"""`raptor advise`: what looks wrong or unwise in a model, with reasons."""
from __future__ import annotations

from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, open_model, workspace_for
from raptor.cli.exitcodes import CliError

ADVISE_SCHEMA = "raptor-advise/1"
RULES_SCHEMA = "raptor-advise-rules/1"

_MARK = {"error": "error", "warning": "warning", "advice": "advice", "info": "note"}
_ORDER = ("error", "warning", "advice", "info")


def add_parser(sub: Any) -> None:
    a = sub.add_parser(
        "advise", help="say what looks wrong or unwise in a model",
        description="Check each MODEL for things a reliability engineer would "
                    "question: redundancy that cannot work, placeholder data, "
                    "spares or crews that cannot meet demand, maintenance that "
                    "cannot pay off, too few runs. Nothing is run and nothing "
                    "is changed. Each finding has a stable rule id and says "
                    "whether it is exact or an estimate.")
    a.add_argument("models", nargs="*", metavar="MODEL")
    a.add_argument("-f", "--format", choices=("text", "json", "md"), default="text")
    a.add_argument("--min-severity", choices=_ORDER, default="info",
                   help="hide findings below this level (default: show all)")
    a.add_argument("--only", metavar="IDS",
                   help="comma list of rule ids or prefixes to run, e.g. M-SPR,M-PM-003")
    a.add_argument("--skip", metavar="IDS",
                   help="comma list of rule ids or prefixes to leave out")
    a.add_argument("--why", action="store_true",
                   help="print the reasoning under each finding (text format)")
    a.add_argument("--strict", action="store_true",
                   help="exit 3 on warnings as well as errors")
    a.add_argument("--simulate", action="store_true",
                   help="also run each model with its own settings and say how far "
                        "to trust the results")
    a.add_argument("--target", action="append", metavar="EXPR",
                   help="a requirement to judge the run against, e.g. "
                        "'system.ao>=0.995' (implies --simulate; repeatable)")
    a.add_argument("--list-rules", action="store_true",
                   help="list every rule and exit")
    a.set_defaults(func=advise_command)

    e = sub.add_parser(
        "explain", help="run a model and say what causes its downtime",
        description="Run MODEL and attribute every system outage to the element "
                    "that started it and to a cause: hands-on repair, waiting for "
                    "a spare, waiting for people or equipment, logistics delay, "
                    "planned maintenance, or being held by another element. Shows "
                    "each element's share of system downtime (with its sampling "
                    "error), of outages and of failures, and what typical outages "
                    "look like. The engine's results are unchanged: this replays "
                    "the event log.")
    e.add_argument("model", metavar="MODEL")
    e.add_argument("-n", "--runs", type=int, metavar="N",
                   help="number of runs (default: the model's own)")
    e.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                   help="worker processes (default 1)")
    e.add_argument("--seed", type=int, metavar="N")
    e.add_argument("--top", type=int, default=10, metavar="N",
                   help="how many elements to list (default 10)")
    e.add_argument("--yearly", action="store_true",
                   help="also slice availability by period (a year when the model's "
                        "unit is hours) and give percentiles: what a bad year looks like")
    e.add_argument("--year-length", type=float, metavar="T",
                   help="period length in the model's time unit (implies --yearly)")
    e.add_argument("--year-offset", type=float, default=0.0, metavar="T",
                   help="where the first period starts (default 0)")
    e.add_argument("--below", type=float, metavar="A",
                   help="report the share of periods with availability below A, "
                        "e.g. 0.97 (implies --yearly)")
    e.add_argument("--capacity-rule",
                   choices=("proportional", "marginal", "first_failed"),
                   default="proportional",
                   help="how to share a capacity-analysis run's lost flow among "
                        "the elements that were down (default proportional)")
    e.add_argument("-f", "--format", choices=("text", "json"), default="text")
    e.set_defaults(func=explain_command)


def _ids(text: str | None) -> list[str] | None:
    if not text:
        return None
    return [t.strip() for t in text.split(",") if t.strip()]


def _list_rules(fmt: str) -> int:
    from raptor.api.advisor import list_rules
    rules = [r.describe() for r in list_rules()]
    if fmt == "json":
        emit_json({"schema": RULES_SCHEMA, "rules": rules}, None)
    elif fmt == "md":
        print("| Rule | Level | Method | Title |\n|---|---|---|---|")
        for r in rules:
            print(f"| {r['id']} | {r['severity']} | {r['basis']} | {r['title']} |")
    else:
        for r in rules:
            print(f"{r['id']:<10} {r['severity']:<8} {r['basis']:<12} {r['title']}")
    return X.OK


def _text(doc: dict, why: bool) -> None:
    c = doc["counts"]
    print(f"{doc['model']}: {c['error']} error(s), {c['warning']} warning(s), "
          f"{c['advice']} suggestion(s), {c['info']} note(s)")
    for f in doc["findings"]:
        tag = "" if f["basis"] == "exact" else " (estimate)" if f["basis"] == "heuristic" else " (statistical)"
        print(f"  {_MARK[f['severity']]}: {f['rule']}{tag}: {f['message']}")
        if why:
            print(f"      why: {f['why']}")
        for fix in f["fixes"]:
            how = "; ".join(fix["assignments"]) or "in the application"
            print(f"      fix: {fix['label']} [{how}]")


def _markdown(doc: dict) -> None:
    print(f"## {doc['model']}\n")
    if not doc["findings"]:
        print("No findings.\n")
        return
    print("| Level | Rule | Finding | Method |\n|---|---|---|---|")
    for f in doc["findings"]:
        message = f["message"].replace("|", "\\|")
        print(f"| {_MARK[f['severity']]} | {f['rule']} | {message} | {f['basis']} |")
    print()


def _targets(texts: list[str] | None) -> list[dict]:
    out = []
    for text in texts or []:
        for op in (">=", "<="):
            if op in text:
                metric, value = text.split(op, 1)
                try:
                    out.append({"metric": metric.strip(), "op": op, "value": float(value)})
                except ValueError as exc:
                    raise CliError(f"--target {text!r}: {value!r} is not a number", X.USAGE) from exc
                break
        else:
            raise CliError(f"--target {text!r} must be METRIC>=VALUE or METRIC<=VALUE", X.USAGE)
    return out


def _run_findings(ws: Any, mid: str, targets: list[dict]) -> list:
    from raptor.api import run as run_api
    from raptor.api.advisor import advise_stored_run
    try:
        result = run_api.run(ws, mid, confirm_long_run=True)
    except run_api.RunError as exc:
        raise CliError(f"cannot simulate: {exc}", X.RUNTIME) from exc
    return advise_stored_run(ws, result.run_id, requirements=targets)


def advise_command(args: Any) -> int:
    if args.list_rules:
        return _list_rules(args.format)
    if not args.models:
        raise CliError("advise needs at least one MODEL (or --list-rules)", X.USAGE)
    from raptor.api.advisor import advise_model, counts
    from raptor.api.advisor.finding import SEVERITY_RANK, sort_findings
    targets = _targets(args.target)
    simulate = args.simulate or bool(targets)
    floor = SEVERITY_RANK[args.min_severity]
    worst = X.OK
    docs: list[dict] = []
    with workspace_for() as ws:
        for path in args.models:
            try:
                mid = open_model(ws, path)
            except CliError as exc:
                docs.append({"model": path, "error": str(exc)})
                worst = max(worst, exc.code)
                continue
            found = advise_model(ws.get(mid).rbd, only=_ids(args.only), skip=_ids(args.skip))
            if simulate and not any(f.severity == "error" for f in found):
                found = sort_findings(found + _run_findings(ws, mid, targets))
            tally = counts(found)
            shown = [f for f in found if SEVERITY_RANK[f.severity] >= floor]
            docs.append({"model": path, "counts": tally,
                         "findings": [f.to_dict() for f in shown]})
            if tally["error"] or (args.strict and tally["warning"]):
                worst = max(worst, X.INVALID)
    _emit(docs, args.format, args.why)
    return worst


def _emit(docs: list[dict], fmt: str, why: bool) -> None:
    if fmt == "json":
        emit_json({"schema": ADVISE_SCHEMA, "results": docs}, None)
        return
    for d in docs:
        if "error" in d:
            print(f"{d['model']}: {d['error']}")
        elif fmt == "md":
            _markdown(d)
        else:
            _text(d, why)


def explain_command(args: Any) -> int:
    from raptor.api import run as run_api
    from raptor.api.insight import drivers
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = run_api.run(ws, mid, num_runs=args.runs, workers=max(1, args.jobs),
                                 seed=args.seed, attribution=True,
                                 capacity_rule=args.capacity_rule,
                                 confirm_long_run=True)
        except run_api.RunError as exc:
            raise CliError(f"cannot simulate: {exc}", X.RUNTIME) from exc
        attribution = drivers.read(ws, result.run_id)
        yearly = None
        if args.yearly or args.year_length is not None or args.below is not None:
            try:
                yearly = drivers.periods(ws, result.run_id, args.year_length,
                                         offset=args.year_offset, below=args.below)
            except ValueError as exc:
                raise CliError(str(exc), X.USAGE) from exc
    if args.format == "json":
        attribution["elements"] = attribution["elements"][:max(0, args.top)]
        if yearly is not None:
            attribution["periods"] = yearly
        emit_json(attribution, None)
    else:
        print("\n".join(drivers.text_lines(attribution, args.top)))
        if yearly is not None:
            print("\n" + "\n".join(drivers.period_lines(yearly)))
    return X.OK
