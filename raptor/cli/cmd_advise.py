"""`raptor advise`: what looks wrong or unwise in a model, with reasons."""
from __future__ import annotations

import sys

from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, eprint, open_model, workspace_for, seed_arg, job_count
from raptor.cli.exitcodes import CliError
from raptor.core.run_experiment import IMPORTANCE_MODES

ADVISE_SCHEMA = "raptor-advise/1"
RULES_SCHEMA = "raptor-advise-rules/1"

_MARK = {"error": "error", "warning": "warning", "advice": "advice", "info": "note"}
_ORDER = ("error", "warning", "advice", "info")
_PROGRESS = ("auto", "bar", "json", "none")


def add_parser(sub: Any) -> None:  # noqa: PLR0915 - one flag per statement
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
    a.add_argument("-j", "--jobs", type=job_count, default=1, metavar="N",
                   help="worker processes for --simulate (default 1)")
    a.add_argument("--progress", choices=_PROGRESS, default="auto",
                   help="progress of --simulate on stderr (json: one object per line)")
    a.add_argument("--list-rules", action="store_true",
                   help="list every rule and exit")
    a.add_argument("--ignore", action="append", metavar="RULE[@TARGET]",
                   help="dismiss a finding in the model file (written back): RULE "
                        "for every finding of it, RULE@block:3 for one; repeatable")
    a.add_argument("--restore", action="append", metavar="RULE[@TARGET]",
                   help="bring back a dismissed finding (written back); repeatable")
    a.add_argument("--reason", metavar="TEXT", default="",
                   help="why the --ignore'd findings do not apply (kept in the model)")
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
    e.add_argument("-j", "--jobs", type=job_count, default=1, metavar="N",
                   help="worker processes (default 1; more is reproducible but "
                        "gives different numbers from -j 1)")
    e.add_argument("--seed", type=seed_arg, metavar="N|new")
    # it ran without any limit: 100,000 runs went on for minutes
    e.add_argument("--timeout", metavar="DURATION", default="10m",
                   help="give up after DURATION, e.g. 90s, 10m (default 10m); the "
                        "runs finished by then are kept and the Story says so")
    e.add_argument("--target", action="append", metavar="EXPR",
                   help="a requirement for the Story's headline, e.g. "
                        "'system.ao>=0.995' or 'mdt<=5' (repeatable; the first "
                        "is the headline); 'system.capacity>=0.95' is the "
                        "capacity target, drawn on the capacity charts")
    e.add_argument("--analysis", metavar="LIST",
                   help="weak,cost,capacity for this run (replaces the model's "
                        "setting; the file is untouched): the cost and lost-capacity "
                        "charts need them")
    e.add_argument("--importance", metavar="MODE", choices=IMPORTANCE_MODES,
                   help="also estimate importance measures: improvement, risk or "
                        "both (importance-quadrant needs both); one extra batch per "
                        "block, two for both")
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
    e.add_argument("--chart-fold", metavar="MODE", default=None,
                   help="the Charts tab's Others control: top:10, top:20, "
                        "fraction:0.25 (top quartile) or tail:0.05 (fold the "
                        "smallest items making up the last 5 %% of the total); "
                        "replaces --chart-min-share")
    e.add_argument("--sankey", choices=("hierarchy", "cause"), default="hierarchy",
                   help="capacity-sankey grouping: lost flow by hierarchy then "
                        "cause (default), or straight to the causes")
    e.add_argument("--no-delivered", action="store_true",
                   help="capacity-sankey: leave out the delivered branch so a "
                        "small loss fills the figure")
    e.add_argument("--chart-run", metavar="N", type=int, default=1,
                   help="the run a one-run chart over time shows "
                        "(availability-through-run, interval-availability, "
                        "capacity-through-run; default 1)")
    e.add_argument("--y-floor", metavar="F", type=float, default=None,
                   help="lower limit of an availability chart over time, "
                        "0 to 1 (default: fitted to the data)")
    e.add_argument("--hierarchy", metavar="NAME|ID", default=None,
                   help="scope an element chart to one hierarchy (name, id or "
                        "path like 'Intake > Pump house')")
    e.add_argument("--hierarchies", metavar="LIST", default=None,
                   help="two to four hierarchies for the overlay, comma "
                        "separated (name, id or path)")
    e.add_argument("--compare-to", metavar="RUN-ID", default=None,
                   help="a stored run whose hierarchy table is the baseline "
                        "for hierarchy-delta")
    e.add_argument("--depth", metavar="N", type=int, default=None,
                   help="hierarchy level for the ranking charts and the "
                        "maximum rings for the sunburst (default: all, or "
                        "top level for rankings)")
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
    for entry in doc.get("ignored") or []:
        # the name a person knows, then the RULE@TARGET that --restore takes
        what = entry["rule"] + (f"@{entry['target']}" if entry["target"] else "")
        print(f"  ignored: {entry.get('subject', what)}: {entry.get('title', '')} [{what}]"
              + (f": {entry['reason']}" if entry["reason"] else ""))


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
            if getattr(args, "ignore", None) or getattr(args, "restore", None):
                _change_ignored(ws, mid, path, args)
            found = advise_model(ws.get(mid).rbd, only=_ids(args.only), skip=_ids(args.skip))
            if simulate and not any(f.severity == "error" for f in found):
                found = sort_findings(found + _run_findings(ws, mid, targets, args))
            tally = counts(found)
            shown = [f for f in found if SEVERITY_RANK[f.severity] >= floor]
            from raptor.api.advisor import ignore
            docs.append({"model": path, "counts": tally,
                         "findings": [f.to_dict() for f in shown],
                         "ignored": ignore.listed(ws.get(mid).rbd)})
            if tally["error"] or (args.strict and tally["warning"]):
                worst = max(worst, X.INVALID)
    _emit(docs, args.format, args.why)
    return worst


def _change_ignored(ws: Any, mid: str, path: str, args: Any) -> None:
    """--ignore / --restore: change the model's dismissed findings and write
    the file back (an .rbds file: a legacy .rbd has nowhere to keep them)."""
    from raptor.api.advisor import ignore, list_rules
    from raptor.cli.cmd_optimise import write_back
    if path.lower().endswith(".rbd"):
        raise CliError(f"{path}: a legacy .rbd cannot keep dismissed findings; "
                       "save it as .rbds first", X.USAGE)
    known = {r.id for r in list_rules()}
    rbd = ws.get(mid).rbd
    for text in args.ignore or []:
        rule, target = ignore.parse(text)
        if rule not in known:
            raise CliError(f"no advisor rule {rule!r} (advise --list-rules)", X.USAGE)
        ignore.ignore(rbd, rule, target, args.reason)
    for text in args.restore or []:
        rule, target = ignore.parse(text)
        if not ignore.restore(rbd, rule, target):
            eprint(f"warning: {text} was not ignored in {path}")
    ws.save(mid)
    write_back(ws, mid, path)


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


def explain_command(args: Any) -> int:  # noqa: C901, PLR0912 - one command, its options in turn
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
    _warn_unused_chart_options(args, chart_name)
    targets = _targets(args.target)
    from raptor.cli.cmd_run import progress_options
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        from raptor.cli.runopts import apply_run_options
        apply_run_options(ws, mid, args)
        if not targets:
            # the target the Charts tab saved in the model, as the window uses
            saved = (getattr(ws.get(mid).rbd, "targets", None) or {}).get("availability")
            if saved is not None:
                targets = [{"metric": "availability", "op": ">=", "value": float(saved)}]
        try:
            from raptor.api.insight.timecharts import TIME_CHARTS
            # an availability chart over time needs the plot recorded (the
            # capacity plot comes with capacity analysis)
            plot = (True if chart_name in TIME_CHARTS
                    and not chart_name.startswith("capacity") else None)
            from raptor.cli.common import parse_duration
            # the cost charts over time need the interim cost report
            cost_plot = True if chart_name in charts.COST_SERIES_CHARTS else None
            result = run_api.run(ws, mid, num_runs=args.runs, workers=max(1, args.jobs),
                                 seed=args.seed, attribution=True,
                                 availability_plot=plot,
                                 interim_cost=cost_plot,
                                 capacity_rule=args.capacity_rule,
                                 confirm_long_run=True,
                                 timeout_s=parse_duration(args.timeout),
                                 **progress_options(args.progress))
        except run_api.RunError as exc:
            raise CliError(f"cannot simulate: {exc}", X.RUNTIME) from exc
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        try:
            attribution = drivers.read(ws, result.run_id)
        except drivers.NoAttribution as exc:
            # the batch stopped before the replay: the Story of the runs that
            # finished still says what it can, and that it stopped early
            if args.story or args.target:
                from raptor.api.insight.story import from_run, text_lines
                print("\n".join(text_lines(from_run(ws, result.run_id,
                                                      requirements=targets))))
                print(f"raptor: {exc}", file=sys.stderr)
                return X.TIMEOUT
            raise CliError(str(exc), X.RUNTIME) from exc
        story = None
        if args.story or args.target:
            from raptor.api.insight.story import from_run
            story = from_run(ws, result.run_id, requirements=targets,
                             capacity_target=_capacity_target(targets))
        chart = _chart_for(ws, result.run_id, chart_name, attribution,
                           run=args.chart_run, y_floor=args.y_floor,
                           min_share=args.chart_min_share,
                           fold=args.chart_fold,
                           by_hierarchy=args.sankey == "hierarchy",
                           show_delivered=not args.no_delivered,
                           target=_availability_target(targets),
                           capacity_target=_capacity_target(targets),
                           hierarchy=args.hierarchy,
                           hierarchies=(_split_csv(args.hierarchies)
                                        if args.hierarchies else None),
                           compare_to=args.compare_to, depth=args.depth)
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
    if chart is None or yearly is not None:
        # --chart alone prints the chart: it came after 30 to 60 lines of
        # the drivers report
        print("\n".join(drivers.text_lines(attribution, args.top)))
    if yearly is not None:
        print("\n" + "\n".join(drivers.period_lines(yearly)))
    if chart is not None:
        _print_chart(chart, args)


def _warn_unused_chart_options(args: Any, chart_name: str | None) -> None:
    """Say so when a chart option has nothing to act on: they were ignored
    without a word."""
    from raptor.api.insight.timecharts import PER_RUN_CHARTS
    unused = []
    if chart_name is None:
        unused += [flag for flag, set_ in (
            ("--chart-page", args.chart_page != "screen"),
            ("--chart-run", args.chart_run != 1),
            ("--chart-min-share", bool(args.chart_min_share)),
            ("--chart-fold", args.chart_fold is not None),
            ("--sankey", args.sankey != "hierarchy"),
            ("--no-delivered", args.no_delivered)) if set_]
    else:
        if args.chart_run != 1 and chart_name not in PER_RUN_CHARTS:
            unused.append("--chart-run (only the one-run charts use it)")
        if ((args.sankey != "hierarchy" or args.no_delivered)
                and chart_name != "capacity-sankey"):
            unused.append("--sankey/--no-delivered (only capacity-sankey uses them)")
        if args.chart_fold is not None and chart_name not in _RANKED_CHARTS:
            unused.append("--chart-fold (only rankings and treemaps fold "
                          "small items)")
        if args.chart_min_share and chart_name not in _RANKED_CHARTS:
            unused.append("--chart-min-share (only rankings and treemaps fold "
                          "small items)")
        if args.chart_page != "screen" and not args.chart_svg:
            unused.append("--chart-page (it sizes --chart-svg)")
    for flag in unused:
        print(f"warning: {flag} has no effect here", file=sys.stderr)


#: The charts `--chart-min-share` folds.
_RANKED_CHARTS = ("downtime-pareto", "downtime-treemap", "cost-pareto", "cost-treemap",
                  "lost-capacity-pareto", "lost-capacity-treemap",
                  "component-failures")


def _split_csv(text: str) -> list[str]:
    return [part.strip() for part in str(text).split(",") if part.strip()]


def _availability_target(targets: list[dict]) -> float | None:
    """The first availability target, for the charts' target line (the
    window's Target button draws one; --target drew none)."""
    from raptor.api.advisor.context import REQUIREMENT_FIELDS
    return next((float(t["value"]) for t in targets
                 if REQUIREMENT_FIELDS.get(str(t.get("metric"))) == "availability"), None)


def _capacity_target(targets: list[dict]) -> float | None:
    """The first capacity target (`--target system.capacity>=0.95`), a
    fraction of the generated flow, for the capacity charts and the Story."""
    from raptor.api.advisor.context import REQUIREMENT_FIELDS
    return next((float(t["value"]) for t in targets
                 if REQUIREMENT_FIELDS.get(str(t.get("metric")))
                 == "capacity_weighted_availability"), None)


def _chart_for(ws: Any, run_id: str, name: str | None,  # noqa: C901, PLR0912 - gathers each chart's inputs
               attribution: dict, *, run: int = 1,
               y_floor: float | None = None, min_share: float = 0.0,
               target: float | None = None,
               capacity_target: float | None = None,
               hierarchy: str | int | None = None,
               hierarchies: list[str] | None = None,
               compare_to: str | None = None,
               depth: int | None = None,
               fold: str | None = None,
               by_hierarchy: bool = True,
               show_delivered: bool = True) -> Any:
    """Build the requested chart from a finished run, or None; with
    `min_share` or `fold`, its small items folded into one
    (`charts.fold_small`).  Without `target` or `capacity_target`, the
    model's saved targets."""
    if not name:
        return None
    from raptor.api.insight import charts
    from raptor.api.insight.timecharts import TIME_CHARTS, stored_inputs
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run, run_currency, run_units
    per_run = read_per_run(run_dir(ws, run_id), run_id) or []
    over_time = (stored_inputs(run_dir(ws, run_id)) if name in TIME_CHARTS
                 else {})
    over_time.pop("units", None)       # passed as units= (it is the run's)
    if y_floor is not None and not 0.0 <= y_floor < 1.0:
        raise CliError("--y-floor must be at least 0 and below 1", X.USAGE)
    if not 0.0 <= min_share < 1.0:
        raise CliError("--chart-min-share must be at least 0 and below 1", X.USAGE)
    if fold is not None and min_share:
        raise CliError("pass --chart-fold or --chart-min-share, not both", X.USAGE)
    try:
        folding = (charts.parse_fold(fold) if fold is not None
                   else {"min_share": min_share})
    except ValueError as exc:
        raise CliError(f"--chart-fold: {exc}", X.USAGE) from exc
    if target is None or capacity_target is None:
        from raptor.api.run_tables import run_targets
        saved = run_targets(ws, run_id)
        if target is None:
            target = saved.get("availability")
        if capacity_target is None:
            capacity_target = saved.get("capacity")
    periods = None
    from raptor.api.insight import drivers
    if name in ("ensemble-ribbon", "hierarchy-overlay", "hierarchy-ribbon"):
        attribution = drivers.with_outages(ws, run_id, attribution) or attribution
    if name in ("annual-fan", "annual-dots", "annual-capacity-fan"):
        periods = drivers.periods(ws, run_id)
    baseline = None
    if compare_to:
        try:
            baseline = drivers.read(ws, compare_to).get("hierarchies")
        except Exception as exc:  # noqa: BLE001 - a CLI error, not a traceback
            raise CliError(f"cannot read baseline run {compare_to}: {exc}",
                           X.USAGE) from exc
    try:
        costs = (charts.stored_element_costs(ws, run_id)
                 if name in charts.COST_CHARTS else None)
        cost_runs = None
        if name in charts.COST_SERIES_CHARTS:
            from raptor.api.insight.cost_series import stored_runs
            cost_runs = stored_runs(ws, run_id)
        importance = (charts.stored_importance(ws, run_id)
                      if name == "importance-quadrant" else None)
        return charts.fold_small(
            charts.chart_named(name, currency=run_currency(ws, run_id),
                               per_run=per_run, attribution=attribution,
                               periods=periods, costs=costs,
                               cost_runs=cost_runs, run=run,
                               importance=importance, y_floor=y_floor,
                               units=run_units(ws, run_id), surface="cli",
                               target=target, capacity_target=capacity_target,
                               hierarchy=hierarchy,
                               hierarchies=hierarchies, baseline=baseline,
                               baseline_label=compare_to,
                               depth=depth, by_hierarchy=by_hierarchy,
                               show_delivered=show_delivered,
                               **over_time), **folding)
    except ValueError as exc:
        raise CliError(str(exc), X.USAGE) from exc


def _print_chart(chart: Any, args: Any) -> None:
    from raptor.api.insight.charts import text_lines
    print()
    for line in text_lines(chart, top=args.top):
        print(line)
    if args.chart_svg:
        from raptor.render.charts_svg import write
        write(chart, args.chart_svg, page=args.chart_page)
        print(f"SVG written to {args.chart_svg}")
