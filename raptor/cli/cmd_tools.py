"""Analysis commands: sweep, compare, diff, estimate, report, tables, render, spf, fev."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import (
    add_run_options, eprint, emit_json, open_model, parse_duration,
    require_runnable, workspace_for, write_text,
)
from raptor.cli.exitcodes import CliError
from raptor.cli.runopts import apply_run_options

SWEEP_SCHEMA = "raptor-sweep/1"
COMPARE_SCHEMA = "raptor-compare/1"
ESTIMATE_SCHEMA = "raptor-estimate/1"
SPF_SCHEMA = "raptor-spf/1"
FEV_SCHEMA = "raptor-fev/1"


def add_parser(sub: Any) -> None:
    s = sub.add_parser(
        "sweep", help="run a parameter sweep",
        description="Run MODEL once per combination of the --vary values "
                    "(a cartesian product), on copies, and tabulate the "
                    "metrics.")
    s.add_argument("model", metavar="MODEL")
    s.add_argument("--vary", action="append", required=True, metavar="SPEC",
                   help="PATH=START:STOP:STEP or PATH=v1,v2,...; repeat for "
                        "a grid")
    s.add_argument("--metric", action="append", metavar="NAME",
                   help="metric(s) to tabulate (default Availability); "
                        "repeatable")
    s.add_argument("-f", "--format", choices=("text", "json", "csv"),
                   default="text")
    add_run_options(s)
    s.set_defaults(func=sweep_command)

    c = sub.add_parser(
        "compare", help="run two or more models and compare the results",
        description="Run each MODEL with the same options and compare "
                    "metric by metric.")
    c.add_argument("models", nargs="+", metavar="MODEL")
    c.add_argument("--metric", action="append", metavar="NAME")
    c.add_argument("-f", "--format", choices=("text", "json", "csv"),
                   default="text")
    add_run_options(c)
    c.set_defaults(func=compare_command)

    df = sub.add_parser(
        "diff", help="show what is different between two models",
        description="Compare two model files (.rbd or .rbds, in any "
                    "combination) and list the differences: elements "
                    "added, removed, renamed or changed, connections, "
                    "hierarchy moves, system settings, rates, spare pools "
                    "and the rest. Nothing is run; this compares the models, "
                    "not their results (see raptor compare). Elements are "
                    "matched by name, then by id.")
    df.add_argument("a", metavar="A", help="the reference model")
    df.add_argument("b", metavar="B", help="the model to compare with it")
    df.add_argument("-f", "--format", choices=("text", "json", "md", "csv"),
                    default="text")
    df.add_argument("--ignore", default="layout,derived", metavar="CATS",
                    help="comma-separated categories to leave out "
                         "(default layout,derived; categories: "
                         "layout, derived, identity, failure, repair, "
                         "streams, sparing, maintenance, logistics, "
                         "dependency, cost, phasing, capacity, structure, "
                         "system, project, other)")
    df.add_argument("--all", action="store_true", dest="show_all",
                    help="ignore nothing (same as --ignore '')")
    df.add_argument("--match", choices=("auto", "name", "id"), default="auto",
                    help="how elements are paired: name then id (auto), name "
                         "only, or id only (default auto)")
    df.add_argument("--only", metavar="GROUPS",
                    help="print only these comma-separated groups: elements, "
                         "links, structure, system, rates, spare_pools, "
                         "resources, triggers, phases, project")
    df.add_argument("--exit-code", action="store_true", dest="exit_code",
                    help="exit 1 when the models differ and 0 when they do "
                         "not (like git diff --exit-code)")
    df.set_defaults(func=diff_command)

    e = sub.add_parser(
        "estimate", help="estimate run time before running",
        description="Time a short pilot and predict the wall-clock time for "
                    "N runs.")
    e.add_argument("model", metavar="MODEL")
    e.add_argument("-n", "--runs", type=int, default=100, metavar="N")
    e.add_argument("-t", "--length", type=float, metavar="T")
    e.add_argument("-f", "--format", choices=("text", "json"), default="text")
    e.set_defaults(func=estimate_command)

    rp = sub.add_parser(
        "report", help="print a stored run's report",
        description="Print a report of an earlier run kept with 'raptor run "
                    "--workspace DIR' (the run id is in the JSON output and "
                    "on stderr).")
    rp.add_argument("run_id", metavar="RUN-ID")
    rp.add_argument("--workspace", required=True, metavar="DIR")
    rp.add_argument("-r", "--report", default="final",
                    metavar="NAME", help="final, end-of-run, events, ao-plot, "
                                         "capacity-plot, interim-cost "
                                         "(default final)")
    rp.add_argument("-f", "--format", choices=("text", "json"),
                    default="text")
    rp.set_defaults(func=report_command)

    r = sub.add_parser(
        "tables", help="export input and result tables",
        description="Write the model's input tables (and, with --run, the "
                    "result tables of a fresh run) as CSV, text or a "
                    "spreadsheet.")
    r.add_argument("model", metavar="MODEL")
    r.add_argument("--run", action="store_true",
                   help="run the model first and include result tables")
    r.add_argument("--table", action="append", metavar="KEY",
                   help="a table key (see --list); repeatable")
    r.add_argument("--list", action="store_true", dest="list_tables",
                   help="list the available tables and exit")
    r.add_argument("--format", action="append",
                   choices=("csv", "txt", "report", "xlsx"), dest="fmts",
                   help="output format(s); default csv")
    r.add_argument("-o", "--output", metavar="DIR",
                   help="folder (default ./Tables beside the model)")
    add_run_options(r)
    r.set_defaults(func=tables_command)

    d = sub.add_parser(
        "render", help="draw the diagram to PNG",
        description="Render the diagram of MODEL to a PNG file.")
    d.add_argument("model", metavar="MODEL")
    d.add_argument("-o", "--output", required=True, metavar="FILE.png")
    d.add_argument("--level", type=int, default=0,
                   help="hierarchy depth to draw (0 = top)")
    d.add_argument("--highlight", action="append", default=[],
                   metavar="SELECTOR")
    d.add_argument("--zoom-to", action="append", default=[],
                   metavar="SELECTOR")
    d.add_argument("--theme", choices=("light", "dark"), default="light")
    d.add_argument("--max-px", type=int, default=1600)
    d.add_argument("--no-annotations", action="store_true")
    d.set_defaults(func=render_command)

    sp = sub.add_parser(
        "spf", help="write the Single Point Failure file",
        description="List the elements whose failure alone takes the "
                    "system down.")
    sp.add_argument("model", metavar="MODEL")
    sp.add_argument("-o", "--output", metavar="FILE",
                    help="write the Raptor 7-format text file to FILE")
    sp.add_argument("-f", "--format", choices=("text", "json"),
                    default="text")
    sp.add_argument("--criticality", action="store_true", default=None,
                    help="add the criticality column to the file (a "
                         "Raptor 7 developer option)")
    sp.set_defaults(func=spf_command)

    ex = sub.add_parser(
        "extract", help="save one hierarchy as a standalone model",
        description="Build a standalone model from one hierarchy (its In node "
                    "becomes Start, its Out node End) and save it, or list "
                    "what crosses its boundary. A port extension: Raptor 7 "
                    "has no such feature, but the saved .rbd opens in it.")
    ex.add_argument("model", metavar="MODEL")
    ex.add_argument("--hierarchy", required=True, metavar="NAME|ID")
    ex.add_argument("-o", "--output", metavar="FILE",
                    help="write the extract here (.rbd or .rbds)")
    ex.add_argument("--report-hazards", action="store_true",
                    help="list what crosses the boundary and the treatment "
                         "each would get; write nothing")
    ex.add_argument("-f", "--format", choices=("text", "json"), default="text")
    from raptor.cli.cmd_run import add_isolation_options
    add_isolation_options(ex, hierarchy=False)
    ex.set_defaults(func=extract_command)

    fv = sub.add_parser(
        "fev", help="Failure Effects View: fail things, read the effects",
        description="Fail things and read the effects (Failure Effects View).\n"
                    "Start from the all-operating state (phase 1 of a phased "
                    "model), apply the actions, and print the Failure Effects "
                    "text. Nothing is simulated and no random number is "
                    "drawn. Actions run in this order: --phase, --fail, "
                    "--repair, --cycle; use --script for any other order "
                    "(see the script actions in raptor-fev(1)).")
    fv.add_argument("model", metavar="MODEL")
    fv.add_argument("--phase", type=int, metavar="N",
                    help="go to phase N first (phased models)")
    fv.add_argument("--fail", action="append", default=[], metavar="SEL",
                    help="fail a block or event (repeatable)")
    fv.add_argument("--repair", action="append", default=[], metavar="SEL",
                    help="repair a failed block or event (repeatable)")
    fv.add_argument("--cycle", action="append", default=[], metavar="SEL",
                    help="click an element, as the mouse does: cycles a "
                         "hatched (induced) block (repeatable)")
    fv.add_argument("--script", metavar="FILE",
                    help="read actions from FILE, one per line (- for stdin); "
                         "each `write` line records the text at that point")
    fv.add_argument("--log", metavar="FILE",
                    help="write the Raptor 7-format Failure Effects log (CRLF, "
                         "the file Write To Failure Effects Log appends to)")
    fv.add_argument("-f", "--format", choices=("text", "json"), default="text")
    fv.add_argument("--criticality", action="store_true", default=None,
                    help="add the Criticality line (Raptor 7's developer "
                         "option)")
    fv.set_defaults(func=fev_command)

    st = sub.add_parser(
        "step", help="step through a run, printing what each step changed",
        description="Run one replication (--run, default the first) one state change at a time (the window's Step button) and print, for "
                    "every stop, the time, the system state, the event-log lines written and the blocks whose "
                    "status changed. Stops after --events stops, at the first stop at or past --until, when the "
                    "--break block changes status, or at the end of the run (the last record has done=true).")
    st.add_argument("model", metavar="MODEL")
    st.add_argument("--run", type=int, default=1, metavar="N",
                    help="step run N (default 1); runs 1 to N-1 are jumped over, as the window's Jump does")
    st.add_argument("--until", type=float, metavar="T", help="stop at the first stop at or past time T")
    st.add_argument("--events", type=int, metavar="N", help="stop after N stops")
    st.add_argument("--break", dest="break_on", metavar="SEL",
                    help="stop when this block (name or number) changes status")
    st.add_argument("--fine", action="store_true",
                    help="advance one calendar entry per stop instead of one state change")
    st.add_argument("-f", "--format", choices=("ndjson", "text"), default="ndjson")
    st.set_defaults(func=step_command)


# ---------------------------------------------------------------------------

def _progress(label: str):
    if not sys.stderr.isatty():
        return None

    def cb(i: int, n: int, what: str) -> None:
        sys.stderr.write(f"\r{label} {i}/{n} {what[:40]:<40}")
        sys.stderr.flush()
        if i == n:
            sys.stderr.write("\n")
    return cb


def _metric_names(args: Any) -> list[str]:
    return args.metric or ["Availability"]


def _value(entry: dict, metric: str) -> float | None:
    s = entry.get("summary", {}).get(metric)
    return None if s is None else s.get("mean")


def _table_text(header: list[str], rows: list[list[str]]) -> str:
    w = [max(len(header[i]), *(len(r[i]) for r in rows)) if rows
         else len(header[i]) for i in range(len(header))]
    line = lambda r: "  ".join(c.ljust(w[i]) for i, c in enumerate(r))  # noqa: E731
    return "\n".join([line(header), *(line(r) for r in rows)])


def _fmt(v: float | None) -> str:
    return "N/A" if v is None else f"{v:.6g}"


def sweep_command(args: Any) -> int:
    from raptor.api import batch, paths
    try:
        varies = [batch.expand_vary(v) for v in args.vary]
    except paths.PathError as exc:
        raise CliError(str(exc), X.USAGE) from exc
    combos = batch.grid(varies)
    metrics = _metric_names(args)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        applied = apply_run_options(ws, mid, args)
        require_runnable(ws, mid)
        variants = [{"label": " ".join(c), "assignments": c} for c in combos]
        res = batch.run_variants(
            ws, mid, variants, num_runs=None, workers=args.jobs,
            timeout_s=parse_duration(args.timeout), seed=args.seed,
            progress=_progress("sweep"))
    names = [p for p, _ in varies]
    rows: list[dict[str, Any]] = []
    for combo, r in zip(combos, res):
        vals = [c.split("=", 1)[1] for c in combo]
        rows.append({"values": dict(zip(names, vals)),
                     "metrics": {m: _value(r, m) for m in metrics},
                     "run_id": r.get("run_id"), "error": r.get("error"),
                     "num_runs": r.get("num_runs")})
    if args.format == "json":
        emit_json({"schema": SWEEP_SCHEMA, "model": args.model,
                   "vary": names, "metrics": metrics, "rows": rows,
                   "options": applied}, None)
    else:
        header = names + metrics
        body: list[list[str]] = [[str(r["values"][n]) for n in names] +
                [(_fmt(r["metrics"][m]) if not r["error"] else "ERROR")
                 for m in metrics] for r in rows]
        if args.format == "csv":
            print(",".join(header))
            for b in body:
                print(",".join(b))
        else:
            print(_table_text(header, body))
        for r in rows:
            if r["error"]:
                eprint(f"error in {r['values']}: {r['error']}")
    return X.INVALID if any(r["error"] for r in rows) else X.OK


def compare_command(args: Any) -> int:
    if len(args.models) < 2:
        raise CliError("compare needs at least two models", X.USAGE)
    from raptor.api import batch
    metrics = _metric_names(args)
    entries = []
    with workspace_for() as ws:
        for path in args.models:
            mid = open_model(ws, path)
            apply_run_options(ws, mid, args)
            require_runnable(ws, mid)
            r = batch.run_variants(
                ws, mid, [{"label": path, "assignments": []}],
                workers=args.jobs, timeout_s=parse_duration(args.timeout),
                seed=args.seed)[0]
            entries.append(r)
    base = entries[0]
    rows: list[dict[str, Any]] = []
    for m in metrics:
        b = _value(base, m)
        rows.append({"metric": m, "values": [_value(e, m) for e in entries],
                     "delta": [None if (v := _value(e, m)) is None or b is None
                               else v - b for e in entries]})
    if args.format == "json":
        emit_json({"schema": COMPARE_SCHEMA, "models": args.models,
                   "rows": rows,
                   "errors": [e.get("error") for e in entries]}, None)
    else:
        header = ["metric"] + list(args.models)
        body: list[list[str]] = [[r["metric"]] + [_fmt(v) for v in r["values"]] for r in rows]
        if args.format == "csv":
            print(",".join(header))
            for b_ in body:
                print(",".join(b_))
        else:
            print(_table_text(header, body))
    return X.INVALID if any(e.get("error") for e in entries) else X.OK


def _diff_text(result: dict[str, Any]) -> str:
    """The plain-text rendering of a model diff: a header, then one aligned
    table per non-empty group."""
    from raptor.api.markdown import _DIFF_TITLES
    from raptor.api.model_diff import GROUPS, flatten
    rows = flatten(result)
    a, b = result["a"], result["b"]
    lines = [f"A: {a['path'] or a['name']}", f"B: {b['path'] or b['name']}",
             f"ignoring: {', '.join(result['ignored']) or 'nothing'}"]
    if result["identical"]:
        lines.append("no differences")
    else:
        lines.append(f"connectivity: {result['summary']['connectivity']}")
    for group in GROUPS:
        mine = [r for r in rows if r["group"] == group]
        if mine:
            body = [[f"{r['kind']} {r['path']}".strip(), r["field"], r["a"],
                     r["b"]] for r in mine]
            lines += ["", f"{_DIFF_TITLES[group]}:",
                      _table_text(["element", "field", "A", "B"], body)]
    if result["warnings"]:
        lines += ["", *(f"warning: {w}" for w in result["warnings"])]
    return "\n".join(lines)


def _diff_csv(result: dict[str, Any]) -> str:
    import csv
    import io
    from raptor.api.model_diff import flatten
    cols = ("group", "kind", "path", "field", "category", "a", "b")
    buffer = io.StringIO()
    w = csv.DictWriter(buffer, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    w.writerows(flatten(result))
    return buffer.getvalue()


def _diff_options(args: Any) -> tuple[list[str], list[str] | None]:
    ignore = [] if args.show_all else [
        c.strip() for c in args.ignore.split(",") if c.strip()]
    only = ([g.strip() for g in args.only.split(",") if g.strip()]
            if args.only else None)
    return ignore, only


def diff_command(args: Any) -> int:
    from raptor.api import model_diff
    from raptor.api.markdown import diff_markdown
    ignore, only = _diff_options(args)
    with workspace_for() as ws:
        ida, idb = open_model(ws, args.a), open_model(ws, args.b)
        try:
            result = model_diff.diff(ws, ida, idb, match=args.match,
                                     ignore=ignore)
            if only is not None:
                result = model_diff.restrict(result, only)
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
    result["a"]["path"], result["b"]["path"] = args.a, args.b
    if args.format == "json":
        emit_json(result, None)
    elif args.format == "md":
        print(diff_markdown(result))
    elif args.format == "csv":
        sys.stdout.write(_diff_csv(result))
    else:
        print(_diff_text(result))
    return 1 if args.exit_code and not result["identical"] else X.OK


def estimate_command(args: Any) -> int:
    from raptor.api.estimate import estimate
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        require_runnable(ws, mid)
        est = estimate(ws, mid, num_runs=args.runs, run_length=args.length)
    data = est.to_dict() if hasattr(est, "to_dict") else dict(vars(est))
    if args.format == "json":
        emit_json({"schema": ESTIMATE_SCHEMA, **data}, None)
    else:
        for k, v in data.items():
            print(f"{k}: {v}")
    return X.OK


def report_command(args: Any) -> int:
    from raptor.api.run import section_path
    from raptor.cli.cmd_run import REPORTS
    key = args.report.strip().lower()
    if key not in REPORTS:
        raise CliError(f"unknown report {args.report!r}; use one of "
                       f"{', '.join(sorted(set(REPORTS)))}", X.USAGE)
    runs_dir = Path(args.workspace).expanduser() / "runs" / args.run_id
    if not runs_dir.is_dir():
        raise CliError(f"no run {args.run_id!r} in {args.workspace}", X.USAGE)
    src = section_path(runs_dir, REPORTS[key][0])
    if src is None or not src.is_file() or not src.read_text().strip():
        raise CliError(f"run {args.run_id} has no {key} report", X.USAGE)
    text = src.read_text()
    if args.format == "json":
        emit_json({"schema": "raptor-report/1", "run_id": args.run_id,
                   "report": key, "lines": text.splitlines()}, None)
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return X.OK


def tables_command(args: Any) -> int:
    from raptor.api import run_tables, table_export, tables
    from raptor.api.run import run as api_run
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        rbd = ws.get(mid).rbd
        bundle = None
        if args.run:
            apply_run_options(ws, mid, args)
            require_runnable(ws, mid)
            res = api_run(ws, mid, workers=args.jobs,
                          timeout_s=parse_duration(args.timeout),
                          confirm_long_run=True, seed=args.seed)
            bundle = run_tables.bundle_from_run(ws, res.run_id)
        if args.list_tables:
            for info in tables.catalogue(rbd, bundle):
                print(f"{info.key}\t{info.title}\t{info.rows} row(s)"
                      + ("" if info.available else f"\t({info.reason})"))
            return X.OK
        try:
            chosen = tables.collect(rbd, bundle, args.table)
        except tables.UnknownTable as exc:
            raise CliError(str(exc), X.USAGE) from exc
        fmts = set(args.fmts or ["csv"])
        opts = table_export.ExportOptions(
            csv="csv" in fmts, txt_files="txt" in fmts,
            txt_report="report" in fmts, xlsx="xlsx" in fmts)
        folder = Path(args.output) if args.output else (
            Path(args.model).resolve().parent / table_export.TABLES_DIRNAME)
        try:
            from raptor.api.presentation import run_info
            run = (run_info(rbd, model=ws.get(mid).name, file=args.model,
                            runs=bundle.num_runs, units=bundle.units)
                   if bundle is not None else None)
            result = table_export.export_tables(
                chosen, folder, opts, model_name=ws.get(mid).name,
                info=run)
        except (ValueError, OSError) as exc:
            raise CliError(str(exc), X.OUTPUT) from exc
    for f in result.files:
        print(f)
    return X.OK


def render_command(args: Any) -> int:
    from raptor.api import render as api_render
    try:
        png, _meta = api_render.render_diagram(
            args.model, args.output, level=args.level,
            highlight=args.highlight or None, zoom_to=args.zoom_to or None,
            theme=args.theme, max_px=args.max_px,
            include_annotations=not args.no_annotations)
    except api_render.RenderUnavailable as exc:
        raise CliError(f"rendering needs Qt: {exc}", X.RUNTIME) from exc
    except api_render.RenderError as exc:
        raise CliError(str(exc), X.RUNTIME) from exc
    eprint(f"wrote {png}")
    return X.OK


def spf_command(args: Any) -> int:
    from raptor.api import analyse
    text = None
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        require_runnable(ws, mid)
        rows = analyse.single_point_failures(ws, mid)
        if args.output:
            text = analyse.spf_listing(ws, mid, Path(args.output).name,
                                       criticality=args.criticality)
    if args.format == "json":
        emit_json({"schema": SPF_SCHEMA, "model": args.model, "rows": rows},
                  None)
    else:
        if not rows:
            print("No single point failures")
        for r in rows:
            print((f"{r['phase']}\t" if r.get("phase") else "")
                  + f"{r['kind']}\t{r['name']}")
    if text is not None:
        write_text(Path(args.output), text)
        eprint(f"wrote {args.output}")
    return X.OK


def fev_command(args: Any) -> int:
    from raptor.api import analyse
    from raptor.api.analyse import AnalysisError
    actions: list[tuple[str, str]] = []
    if args.phase is not None:
        actions.append(("phase", str(args.phase)))
    actions += [("fail", s) for s in args.fail]
    actions += [("repair", s) for s in args.repair]
    actions += [("click", s) for s in args.cycle]
    try:
        if args.script:
            text = (sys.stdin.read() if args.script == "-"
                    else Path(args.script).read_text(encoding="utf-8"))
            actions += analyse.parse_fev_script(text)
        with workspace_for() as ws:
            mid = open_model(ws, args.model)
            writes = analyse.fev_script(ws, mid, actions,
                                        criticality=args.criticality)
    except (AnalysisError, OSError) as exc:
        raise CliError(str(exc), X.INVALID) from exc
    if args.format == "json":
        emit_json({"schema": FEV_SCHEMA, "model": args.model,
                   "writes": writes}, None)
    else:
        for i, w in enumerate(writes):
            if i:
                print("*" * 65)
            print("\n".join(w["lines"]))
    if args.log:
        block = "".join("\r\n" + "".join(f"{ln}\r\n" for ln in w["lines"])
                        + "\r\n" + "*" * 65 + "\r\n\r\n" for w in writes)
        try:
            Path(args.log).write_bytes(block.encode("utf-8"))
        except OSError as exc:
            raise CliError(f"cannot write {args.log}: {exc}", X.OUTPUT) from exc
        eprint(f"wrote {args.log}")
    return X.OK


def extract_command(args: Any) -> int:
    from raptor.api import isolate
    from raptor.cli.cmd_run import isolation_treatments
    if not args.report_hazards and not args.output:
        raise CliError("give -o FILE to save the extract, or --report-hazards", X.USAGE)
    treat = isolation_treatments(args)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        rbd = ws.get(mid).rbd
        try:
            if args.report_hazards:
                hazards = isolate.scan(rbd, args.hierarchy, treat)
                if args.format == "json":
                    emit_json({"schema": "raptor-hazards/1", "model": args.model,
                               "hierarchy": args.hierarchy,
                               "hazards": [h.to_dict() for h in hazards]}, None)
                else:
                    if not hazards:
                        print("No hazards: nothing crosses the hierarchy's boundary")
                    for h in hazards:
                        line = f"[{h.kind}] {', '.join(h.elements)}: {h.detail}"
                        if h.treatment:
                            line += f" -> {h.treatment} ({h.effect})"
                        print(line)
                return X.OK
            extracted, record = isolate.extract(rbd, args.hierarchy, treat)
        except isolate.IsolationRefused as exc:
            raise CliError(str(exc), X.REFUSED) from exc
        except isolate.IsolationError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        info = ws.register_model(extracted, name=f"{record.hierarchy}_isolated")
        dest = Path(args.output)
        fmt = "rbd" if dest.suffix.lower() == ".rbd" else "rbds"
        try:
            ws.save_model_as(info["model_id"], str(dest), fmt=fmt)
        except Exception as exc:
            raise CliError(f"cannot write {dest}: {exc}", X.OUTPUT) from exc
    if args.format == "json":
        emit_json({"schema": "raptor-extract/1", "output": str(dest),
                   "isolation": record.to_dict()}, None)
    else:
        sys.stdout.write(record.text())
    eprint(f"wrote {dest}")
    return X.OK


def step_command(args: Any) -> int:
    import json
    from raptor.api import stepping
    if args.events is not None and args.events < 1:
        raise CliError("--events must be at least 1", X.USAGE)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        require_runnable(ws, mid)
        rbd = ws.get(mid).rbd
        try:
            for record in stepping.trace(rbd, until=args.until, events=args.events, break_on=args.break_on,
                                      granularity="entry" if args.fine else "change", run=args.run):
                if args.format == "ndjson":
                    print(json.dumps(record, separators=(",", ":")), flush=True)
                    continue
                print(f"[{record['step']}] "
                      + (f"run {record['run']} " if args.run > 1 else "") + f"t={record['time']:.6g} system {record['system']} ({record['color']}), "
                      f"{record['failed']} failed" + ("  -- run complete" if record.get("done") else ""))
                for c in record["changes"]:
                    print(f"    {c['name']} (#{c['id']}): {c['from']} -> {c['to']}")
                for ln in record["log"]:
                    print("    | " + " ".join(ln.split()))
        except stepping.StepError as exc:
            raise CliError(str(exc), X.USAGE) from exc
    return X.OK
