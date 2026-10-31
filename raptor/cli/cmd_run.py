"""`raptor run`: simulate a model and write/print the results."""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import (
    FORMATS, add_run_options, eprint, emit_json, open_model,
    parse_duration, require_runnable, workspace_for, write_text,
)
from raptor.cli.exitcodes import CliError
from raptor.cli.runopts import apply_run_options

#: `--report NAME` -> (artefact section in the run directory, legacy file stem)
REPORTS = {
    "final": ("final_results", "rpt_final_results"),
    "final-results": ("final_results", "rpt_final_results"),
    "end-of-run": ("end_of_run", "rpt_end_of_run"),
    "events": ("events", "rpt_events"),
    "ao-plot": ("availability_plot", "rpt_availability_plot"),
    "capacity-plot": ("capacity", "rpt_capacity_plot"),
    "interim-cost": ("cost", "rpt_interim_cost"),
    "key-params": ("key_parameters", "rpt_key_parameters"),
    "failure-times": ("failure_times", "rpt_failure_times"),
    "down-times": ("down_times", "rpt_down_times"),
    "end-times": ("end_times", "rpt_end_times"),
    # Needs the `sim_details` developer option (`--dev-features sim_details`).
    "sim-details": ("sim_details", "rpt_sim_details"),
    # Need the `domain_tree` / `core_file` developer options.
    "mission-file": ("mission_file", "rpt_mission"),
    "cumulative-by-trial": ("cumulative_by_trial", "rpt_cumulative_by_trial"),
    "cumulative-by-time": ("cumulative_by_time", "rpt_cumulative_by_time"),
    "diagnostics": ("diagnostics_log", "rpt_diagnostics"),
    "domain-tree": ("domain_tree", "rpt_domain_tree"),
    "core-file": ("core_file", "rpt_core_file"),
}
#: Reports Raptor 7 writes with CRLF line ends (the data files).
_CRLF_SECTIONS = frozenset({"key_parameters", "failure_times", "down_times",
                            "end_times", "domain_tree", "core_file",
                            "mission_file", "sim_details", "cumulative_by_trial", "cumulative_by_time"})
#: What `--report all` writes.
ALL_REPORTS = ("final", "end-of-run", "events", "ao-plot", "capacity-plot",
               "interim-cost", "key-params", "failure-times", "down-times",
               "end-times")

SCHEMA = "raptor-results/1"


def add_parser(sub: Any) -> None:
    p = sub.add_parser(
        "run", aliases=["simulate"], help="simulate a model",
        description="Simulate MODEL and print or write the results. The "
                    "model file is never modified.")
    p.add_argument("model", metavar="MODEL", help=".rbd or .rbds file")
    add_run_options(p)
    o = p.add_argument_group("output")
    o.add_argument("-o", "--output", metavar="DIR",
                   help="write the summary (in the -f format; none with "
                        "-f none) and any -r reports to DIR (default with "
                        "-r: ./<model>-results)")
    o.add_argument("-f", "--format", choices=FORMATS, default="text",
                   help="summary on stdout (default text)")
    o.add_argument("--json", metavar="PATH", dest="json_path",
                   help="write the full structured results (schema "
                        f"{SCHEMA}) to PATH ('-' = stdout)")
    o.add_argument("-r", "--report", action="append", default=[],
                   metavar="NAME",
                   help="also write a Raptor 7-format text file, "
                        "repeatable: " + ", ".join(sorted(set(REPORTS))) +
                        ", all")
    o.add_argument("--legacy-names", action="store_true",
                   help="name files as Raptor 7 does "
                        "(rpt_events_<Model>.txt ...); the default")
    o.add_argument("--metrics", metavar="LIST",
                   help="limit the summary, e.g. Availability,MTBDE,MDT")
    o.add_argument("--progress", choices=("auto", "bar", "json", "none"),
                   default="auto", help="progress on stderr: replications, and the model set-up that precedes "
                        "them on a large model (json prints one object per line)")
    o.add_argument("--strict", action="store_true",
                   help="treat model warnings as errors (exit 3)")
    o.add_argument("--workspace", metavar="DIR",
                   help="keep run artefacts in DIR (default: a temporary "
                        "directory, deleted afterwards)")
    add_isolation_options(p)
    p.set_defaults(func=run_command)


def add_isolation_options(p: Any, *, hierarchy: bool = True) -> None:
    """`--hierarchy` and the treatment options (a port extension; see
    `raptor help isolation`)."""
    g = p.add_argument_group(
        "isolated hierarchy (a port extension, not a Raptor feature)")
    if hierarchy:
        g.add_argument("--hierarchy", metavar="NAME|ID",
                       help="run only this hierarchy, its input always "
                            "available; the results are labelled as isolated")
    g.add_argument("--deps", choices=("up", "system", "refuse"),
                   default="up",
                   help="elements following something outside: assume it is "
                        "always up (default), follow the extract's own end, "
                        "or stop")
    g.add_argument("--pools", choices=("full", "share", "refuse"),
                   default="full",
                   help="spare and resource pools shared with outside blocks: "
                        "the whole pool (default), scaled by the share of "
                        "users inside, or stop")
    g.add_argument("--streams", choices=("keep", "separate"), default="keep",
                   help="draws on a shared random stream: keep (default) or "
                        "give each its own unused stream")
    g.add_argument("--treat", action="append", default=[], metavar="KEY=VALUE",
                   dest="treats",
                   help="one item: block:ID.dep=up|system|refuse, "
                        "pool:NAME.stock=N, resource:NAME.units=N; repeatable")


def isolation_treatments(args: Any) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for t in getattr(args, "treats", []) or []:
        if "=" not in t:
            raise CliError(f"--treat {t!r} is not KEY=VALUE", X.USAGE)
        k, v = t.split("=", 1)
        items[k.strip()] = int(v) if v.strip().lstrip("-").isdigit() else v.strip()
    return {"deps": getattr(args, "deps", "up"), "pools": getattr(args, "pools", "full"),
            "streams": getattr(args, "streams", "keep"), "items": items}


def _wanted_reports(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        key = n.strip().lower()
        if key == "all":
            out.extend(ALL_REPORTS)
        elif key in REPORTS:
            out.append(key)
        else:
            raise CliError(f"--report {n!r}: use one of "
                           f"{', '.join(sorted(set(REPORTS)))} or all",
                           X.USAGE)
    seen: set[str] = set()
    unique = []
    for r in out:
        if REPORTS[r][0] not in seen:
            seen.add(REPORTS[r][0])
            unique.append(r)
    return unique


def run_model(ws: Any, model_id: str, args: Any, *,
              wanted: list[str] | None = None) -> tuple[Any, dict, list[str]]:
    """Validate and run. Returns (RunResult, applied-options, warnings)."""
    from raptor.api import run as api_run
    from raptor.api.run import RunCancelledError, RunTimeoutError

    applied = apply_run_options(ws, model_id, args)
    warns = require_runnable(ws, model_id, strict=getattr(args, "strict", False))
    timeout = parse_duration(args.timeout)
    progress = _progress_callback(getattr(args, "progress", "auto"))
    kwargs = dict(
        num_runs=None, run_length=None,
        workers=max(1, getattr(args, "jobs", 1) or 1), timeout_s=timeout,
        collect_events="events" in (wanted or ()),
        confirm_long_run=True, seed=getattr(args, "seed", None),
        progress=progress,
        setup_progress=_setup_callback(getattr(args, "progress", "auto")))
    try:
        if getattr(args, "hierarchy", None):
            from raptor.api import isolate
            try:
                result = api_run.run_isolated(
                    ws, model_id, args.hierarchy,
                    treatments=isolation_treatments(args), **kwargs)
            except isolate.IsolationRefused as exc:
                raise CliError(str(exc), X.REFUSED) from exc
            except isolate.IsolationError as exc:
                raise CliError(str(exc), X.USAGE) from exc
        else:
            result = api_run.run(ws, model_id, **kwargs)
    except (RunTimeoutError, RunCancelledError) as exc:
        raise CliError(str(exc), X.TIMEOUT) from exc
    except NotImplementedError as exc:
        raise CliError(f"the model cannot be run: {exc}", X.REFUSED) from exc
    return result, applied, warns


def _progress_callback(mode: str):
    if mode == "none" or (mode == "auto" and not sys.stderr.isatty()):
        return None
    import json as _json

    def cb(done: int, total: int | None) -> None:
        if mode == "json":
            eprint(_json.dumps({"event": "progress", "done": done,
                                "total": total}))
        else:
            tot = f"/{total}" if total else ""
            sys.stderr.write(f"\rrun {done}{tot}")
            sys.stderr.flush()
    return cb


def _setup_callback(mode: str):
    """Set-up progress on stderr: a large model can spend minutes here before
    the first replication, with no other sign of life."""
    if mode == "none" or (mode == "auto" and not sys.stderr.isatty()):
        return None
    import json as _json

    def cb(stage: str, done: int, total: int) -> None:
        if mode == "json":
            eprint(_json.dumps({"event": "setup", "stage": stage,
                                "done": done, "total": total}))
        else:
            sys.stderr.write(f"\r{stage} {done}/{total}"
                             + ("\n" if done >= total else ""))
            sys.stderr.flush()
    return cb


def results_document(result: Any, ws: Any, model_id: str, model_path: str,
                     applied: dict, warns: list[str], files: list[str],
                     args: Any) -> dict:
    """The structured results (schema raptor-results/1)."""
    import raptor
    handle = ws.get(model_id)
    sec = handle.rbd.sections.get("SYSTEM_DATA")
    row = sec.rows[0] if sec and sec.rows else {}
    return {
        "schema": SCHEMA,
        "model": {"name": handle.name, "path": str(Path(model_path).resolve()),
                  "hash": result.model_hash,
                  "units": str(row.get("units", "")).strip()},
        "options": {
            "runs": result.num_runs,
            "termination": row.get("TerminationType"),
            "term_condition": _num(row.get("TermCondition")),
            "stats_start": _num(row.get("StartStats")),
            "seed": getattr(args, "seed", None),
            "jobs": getattr(args, "jobs", 1),
            "applied": applied,
        },
        "summary": {m.name: _null_nan(m.to_dict())
                    for m in result.metrics},
        "runs": result.per_run,
        "warnings": list(warns) + list(result.warnings),
        "files": files,
        "run_id": result.run_id if getattr(args, "workspace", None) else None,
        "workspace": (str(Path(args.workspace).resolve())
                      if getattr(args, "workspace", None) else None),
        "wall_seconds": round(result.wall_seconds, 3),
        "versions": {"raptor": raptor.__version__,
                     "python": platform.python_version()},
        **({"isolation": result.isolation} if getattr(result, "isolation", None) else {}),
        # --importance: the ranking `summary_text` prints and JSON carries.
        **({"importance": result.importance} if getattr(result, "importance", None) else {}),
    }


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _null_nan(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and v != v else v)
            for k, v in d.items()}


def summary_text(doc: dict, metrics: list[str] | None) -> str:
    rows = doc["summary"]
    names = [n for n in rows if not metrics or n in metrics]
    head = (f"{doc['model']['name']}: {doc['options']['runs']} run(s), "
            f"{doc['wall_seconds']:.1f}s")
    lines = [head, ""]
    if doc.get("isolation"):
        lines.insert(0, doc["isolation"]["banner"])
    w = max((len(n) for n in names), default=8)
    lines.append(f"{'metric':<{w}}  {'mean':>14} {'min':>14} {'max':>14} "
                 f"{'stdev':>14} {'sem':>14}")
    for n in names:
        m = rows[n]
        lines.append(f"{n:<{w}}  " + " ".join(
            f"{_fmt(m.get(k)):>14}" for k in ("mean", "min", "max", "stdev",
                                              "sem")))
    for wmsg in doc["warnings"]:
        lines.append(f"warning: {wmsg}")
    imp = doc.get("importance")
    if imp:
        from raptor.api.importance import DEFAULT_METRIC, table_lines
        lines.append("")
        lines.extend(table_lines(imp, DEFAULT_METRIC, limit=15))
    return "\n".join(lines)


def _fmt(v: Any) -> str:
    return "N/A" if v is None else f"{v:.6g}"


def summary_csv(doc: dict, metrics: list[str] | None) -> str:
    rows = ["metric,mean,min,max,stdev,sem"]
    for n, m in doc["summary"].items():
        if metrics and n not in metrics:
            continue
        rows.append(",".join([n] + [
            "" if m.get(k) is None else repr(m[k])
            for k in ("mean", "min", "max", "stdev", "sem")]))
    return "\n".join(rows) + "\n"


def summary_markdown(doc: dict, metrics: list[str] | None) -> str:
    out = ["| metric | mean | min | max | stdev | sem |",
           "|---|---:|---:|---:|---:|---:|"]
    for n, m in doc["summary"].items():
        if metrics and n not in metrics:
            continue
        out.append(f"| {n} | " + " | ".join(
            _fmt(m.get(k)) for k in ("mean", "min", "max", "stdev", "sem"))
            + " |")
    return "\n".join(out) + "\n"


def write_reports(ws: Any, result: Any, wanted: list[str], out_dir: Path,
                  model_name: str) -> list[str]:
    """Write the Raptor 7-format text files; return their paths."""
    from raptor.api.run import section_path
    runs_dir = ws.root / "runs" / result.run_id
    written: list[str] = []
    for key in wanted:
        section, stem = REPORTS[key]
        src = section_path(runs_dir, section)
        text = src.read_text() if src and src.is_file() else ""
        if not text.strip():
            eprint(f"note: no {key} data for this model "
                   f"(not collected or not enabled)")
            continue
        dest = out_dir / f"{stem}_{model_name}.txt"
        if section in _CRLF_SECTIONS:
            text = "".join(f"{ln}\r\n" for ln in text.splitlines())
        write_text(dest, text if text.endswith("\n") else text + "\n")
        written.append(str(dest))
    return written


_SUMMARY_EXT = {"text": "txt", "markdown": "md", "csv": "csv",
                "json": "json", "ndjson": "ndjson"}


def _summary(doc: dict, metrics: Any, fmt: str) -> str:
    """The results summary in output format `fmt`, exactly as printed on stdout."""
    import json
    if fmt == "json":
        from raptor.cli.common import _scrub
        return json.dumps(_scrub(doc), indent=2, default=str, allow_nan=False) + "\n"
    if fmt == "ndjson":
        return "".join(json.dumps({"run": i, **r}, default=str) + "\n"
                       for i, r in enumerate(doc["runs"], 1))
    if fmt == "csv":
        return summary_csv(doc, metrics)
    if fmt == "markdown":
        return summary_markdown(doc, metrics)
    return summary_text(doc, metrics) + "\n"


def run_command(args: Any) -> int:
    wanted = _wanted_reports(args.report)
    metrics = [m.strip() for m in args.metrics.split(",")] if args.metrics \
        else None
    with workspace_for(args.workspace) as ws:
        model_id = open_model(ws, args.model)
        result, applied, warns = run_model(ws, model_id, args, wanted=wanted)
        for w in warns:
            eprint(f"warning: {w}")
        if args.workspace:
            eprint(f"run id: {result.run_id} (raptor report "
                   f"{result.run_id} --workspace {args.workspace})")
        files: list[str] = []
        if wanted:
            out_dir = Path(args.output or f"{Path(args.model).stem}-results")
            files = write_reports(ws, result, wanted, out_dir,
                                  ws.get(model_id).name)
        if getattr(result, "isolation", None):
            out_dir = Path(args.output or f"{Path(args.model).stem}-results")
            if wanted or args.output:
                rec_text = (ws.root / "runs" / result.run_id / "isolation.txt").read_text()
                write_text(out_dir / f"isolation_{ws.get(model_id).name}.txt", rec_text)
                files.append(str(out_dir / f"isolation_{ws.get(model_id).name}.txt"))
        # `-o DIR` gets the summary in the `-f` format; it used to write
        # nothing unless `-r` named a report. `-f none` still means no
        # summary, so say when `-o` is left empty.
        summary_path = None
        if args.output and args.format != "none":
            summary_path = (Path(args.output) / f"summary_{ws.get(model_id).name}"
                            f".{_SUMMARY_EXT[args.format]}")
            files.append(str(summary_path))
        elif args.output and not files:
            eprint(f"warning: nothing written to {args.output}: -f none and no -r report")
        doc = results_document(result, ws, model_id, args.model, applied,
                               warns, files, args)
        if summary_path is not None:
            write_text(summary_path, _summary(doc, metrics, args.format))
        if args.json_path:
            emit_json(doc, args.json_path)
        if args.json_path != "-" and args.format != "none":
            sys.stdout.write(_summary(doc, metrics, args.format))
    partial = any(w.startswith("Completed") and "timeout or abort" in w
                  for w in doc["warnings"])
    return X.TIMEOUT if partial else X.OK
