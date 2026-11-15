"""`raptor advise`: what looks wrong or unwise in a model, with reasons."""
from __future__ import annotations

import sys

from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, open_model, workspace_for
from raptor.cli.exitcodes import CliError

ADVISE_SCHEMA = "raptor-advise/1"
RULES_SCHEMA = "raptor-advise-rules/1"

_MARK = {"error": "error", "warning": "warning", "advice": "advice", "info": "note"}
_ORDER = ("error", "warning", "advice", "info")
_PROGRESS = ("auto", "bar", "json", "none")


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
    a.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                   help="worker processes for --simulate (default 1)")
    a.add_argument("--progress", choices=_PROGRESS, default="auto",
                   help="progress of --simulate on stderr (json: one object per line)")
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
    e.add_argument("model", metavar="MODEL", nargs="?")
    e.add_argument("-n", "--runs", type=int, metavar="N",
                   help="number of runs (default: the model's own)")
    e.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                   help="worker processes (default 1; more is reproducible but "
                        "gives different numbers from -j 1)")
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
    e.add_argument("--report", metavar="PATH",
                   help="also write the report for a reader who wants the "
                        "answer: PATH.pdf or one self-contained PATH.html "
                        "(the GUI's File > Export PDF)")
    e.add_argument("--report-include", metavar="LIST", default="diagram,story",
                   help="report sections, comma separated, from diagram, "
                        "story, results (every result table) and data_tables "
                        "(default diagram,story)")
    e.add_argument("--story", action="store_true",
                   help="print the Story: headline against target, distribution, "
                        "drivers, findings and key numbers")
    e.add_argument("--chart", metavar="NAME",
                   help="also build one chart from this run, e.g. downtime-pareto "
                        "or availability-ladder (see --list-charts)")
    e.add_argument("--chart-svg", metavar="PATH",
                   help="write the chart as SVG (implies --chart when it is missing)")
    e.add_argument("--chart-page", choices=("screen", "slide", "a4", "a3"),
                   default="screen",
                   help="page the SVG is drawn for: a bigger page gives long "
                        "names more room; slide, a4 and a3 also set the "
                        "physical size (default screen, 760 x 400)")
    e.add_argument("--chart-min-share", metavar="S", type=float, default=0.0,
                   help="fold the items below this share of the whole (0 to 1, "
                        "e.g. 0.01) into one 'N others' row or cell; rankings "
                        "and treemaps only")
    e.add_argument("--chart-run", metavar="N", type=int, default=1,
                   help="the run a one-run chart over time shows "
                        "(availability-through-run, interval-availability, "
                        "capacity-through-run; default 1)")
    e.add_argument("--y-floor", metavar="F", type=float, default=None,
                   help="lower limit of an availability chart over time, "
                        "0 to 1 (default: fitted to the data)")
    e.add_argument("--list-charts", action="store_true",
                   help="list the chart names and exit")
    e.add_argument("--progress", choices=_PROGRESS, default="auto",
                   help="progress on stderr: set-up, replications and the "
                        "attribution replay (json: one object per line)")
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
    from raptor.api.advisor.context import check_requirements
    out = []
    for text in texts or []:
        for op in (">=", "<="):
            if op in text:
                metric, value = text.split(op, 1)
                value = value.strip()
                try:
                    # "99.5%" as the output prints it, or 0.995
                    number = (float(value[:-1]) / 100.0 if value.endswith("%")
                              else float(value))
                except ValueError as exc:
                    raise CliError(f"--target {text!r}: {value!r} is not a number", X.USAGE) from exc
                out.append({"metric": metric.strip(), "op": op, "value": number})
                break
        else:
            raise CliError(f"--target {text!r} must be METRIC>=VALUE or METRIC<=VALUE", X.USAGE)
    try:
        check_requirements(out)
    except ValueError as exc:
        raise CliError(f"--target: {exc}", X.USAGE) from exc
    return out


def _run_findings(ws: Any, mid: str, targets: list[dict], args: Any = None) -> list:
    from raptor.api import run as run_api
    from raptor.api.advisor import advise_stored_run
    from raptor.cli.cmd_run import progress_options
    jobs = max(1, getattr(args, "jobs", 1) or 1)
    hooks = progress_options(getattr(args, "progress", "none") or "none")
    try:
        # attribution=True: the GUI's Advisor says what drives the downtime
        # after a run (R-DRV rules); without it the CLI said less.
        result = run_api.run(ws, mid, attribution=True, confirm_long_run=True,
                             workers=jobs, **hooks)
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
                found = sort_findings(found + _run_findings(ws, mid, targets, args))
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
    from raptor.api.insight import charts, drivers
    if args.list_charts:
        for name, function in sorted(charts.CHARTS.items()):
            summary = function.__doc__.splitlines()[0] if function.__doc__ else ""
            print(f"{name:<22} {summary}")
        return X.OK
    if not args.model:
        raise CliError("explain needs a MODEL (or use --list-charts)", X.USAGE)
    chart_name = args.chart or ("downtime-pareto" if args.chart_svg else None)
    from raptor.cli.cmd_run import progress_options
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            from raptor.api.insight.timecharts import TIME_CHARTS
            # an availability chart over time needs the plot recorded (the
            # capacity plot comes with capacity analysis)
            plot = (True if chart_name in TIME_CHARTS
                    and not chart_name.startswith("capacity") else None)
            result = run_api.run(ws, mid, num_runs=args.runs, workers=max(1, args.jobs),
                                 seed=args.seed, attribution=True,
                                 availability_plot=plot,
                                 capacity_rule=args.capacity_rule,
                                 confirm_long_run=True,
                                 **progress_options(args.progress))
        except run_api.RunError as exc:
            raise CliError(f"cannot simulate: {exc}", X.RUNTIME) from exc
        attribution = drivers.read(ws, result.run_id)
        story = None
        if args.story:
            from raptor.api.insight.story import from_run
            story = from_run(ws, result.run_id)
        chart = _chart_for(ws, result.run_id, chart_name, attribution,
                           run=args.chart_run, y_floor=args.y_floor,
                           min_share=args.chart_min_share)
        yearly = None
        if args.yearly or args.year_length is not None or args.below is not None:
            try:
                yearly = drivers.periods(ws, result.run_id, args.year_length,
                                         offset=args.year_offset, below=args.below)
            except ValueError as exc:
                raise CliError(str(exc), X.USAGE) from exc
        if args.report:
            _write_report(ws, result.run_id, args)
    _explain_output(attribution, story, yearly, chart, args)
    return X.OK


def _write_report(ws: Any, run_id: str, args: Any) -> None:
    from raptor.api.report import ReportError, write_report
    sections = tuple(s.strip() for s in args.report_include.split(",") if s.strip())
    try:
        path = write_report(ws, run_id, args.report, sections=sections)
    except ReportError as exc:
        raise CliError(str(exc), X.USAGE) from exc
    print(f"report: {path}", file=sys.stderr)


def _explain_output(attribution: dict, story: Any, yearly: Any,
                    chart: Any, args: Any) -> None:
    if args.format == "json":
        attribution["elements"] = attribution["elements"][:max(0, args.top)]
        if yearly is not None:
            attribution["periods"] = yearly
        if story is not None:
            attribution["story"] = story.to_dict()
        if chart is not None:
            attribution["chart"] = chart.to_dict()
        emit_json(attribution, None)
        return
    if story is not None:
        from raptor.api.insight.story import text_lines as story_lines
        print("\n".join(story_lines(story)))
        print()
    from raptor.api.insight import drivers
    print("\n".join(drivers.text_lines(attribution, args.top)))
    if yearly is not None:
        print("\n" + "\n".join(drivers.period_lines(yearly)))
    if chart is not None:
        _print_chart(chart, args)


def _chart_for(ws: Any, run_id: str, name: str | None,
               attribution: dict, *, run: int = 1,
               y_floor: float | None = None, min_share: float = 0.0) -> Any:
    """Build the requested chart from a finished run, or None; with
    `min_share`, its small items folded into one (`charts.fold_small`)."""
    if not name:
        return None
    from raptor.api.insight import charts
    from raptor.api.insight.timecharts import TIME_CHARTS, stored_inputs
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run
    per_run = read_per_run(run_dir(ws, run_id), run_id) or []
    over_time = (stored_inputs(run_dir(ws, run_id)) if name in TIME_CHARTS
                 else {})
    if y_floor is not None and not 0.0 <= y_floor < 1.0:
        raise CliError("--y-floor must be at least 0 and below 1", X.USAGE)
    if not 0.0 <= min_share < 1.0:
        raise CliError("--chart-min-share must be at least 0 and below 1", X.USAGE)
    periods = None
    if name in ("annual-fan", "annual-dots", "annual-capacity-fan"):
        from raptor.api.insight import drivers
        periods = drivers.periods(ws, run_id)
    try:
        costs = (charts.stored_element_costs(ws, run_id)
                 if name in charts.COST_CHARTS else None)
        return charts.fold_small(
            charts.chart_named(name, per_run=per_run, attribution=attribution,
                               periods=periods, costs=costs, run=run,
                               y_floor=y_floor, **over_time), min_share)
    except ValueError as exc:
        raise CliError(str(exc), X.USAGE) from exc


def _print_chart(chart: Any, args: Any) -> None:
    print()
    print(f"{chart.title} — {chart.caption}")
    if chart.kind == "timeline":
        # one line per layer: where it ends (the caption has the reading)
        fmt = str(chart.provenance.get("format") or "")
        from raptor.api.insight.axes import format_value

        def shown(value: float) -> str:
            return format_value(value, fmt, 5) if fmt else f"{value:,.4g}"

        for layer in chart.series:
            if layer.get("read") == "none" or not layer.get("t"):
                continue
            if layer.get("role") != "area":
                text = shown(float(layer["y"][-1]))
            elif layer.get("read") == "range":
                text = f"{shown(float(layer['lo'][-1]))} to {shown(float(layer['hi'][-1]))}"
            else:
                text = shown(float(layer["hi"][-1]) - float(layer["lo"][-1]))
            print(f"  {layer.get('name', '')}: {text} at the end")
    for row in ([] if chart.kind == "timeline" else chart.series[:args.top]):
        name = row.get("name") or row.get("label") or ""
        value = row.get("value", row.get("count", row.get("mean")))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            from raptor.api.insight.axes import format_value
            fmt = str(chart.provenance.get("format") or "")
            value = (f"{value:,.2f}" if fmt == "money"
                     else format_value(float(value), fmt, 6))
        print(f"  {name}: {value}")
    if args.chart_svg:
        from raptor.render.charts_svg import write
        write(chart, args.chart_svg, page=args.chart_page)
        print(f"SVG written to {args.chart_svg}")
