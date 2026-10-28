"""Turn the shared run options into edits of the (private) model copy."""
from __future__ import annotations

from typing import Any

from raptor.cli.common import eprint, load_set_file
from raptor.cli.exitcodes import USAGE, CliError


def parse_stop(text: str) -> tuple[str, float | None]:
    """`time` | `failures=N` | `cycles=N` -> (TerminationType, TermCondition)."""
    kind, _, value = text.partition("=")
    kind = kind.strip().lower()
    table = {"time": "TimeSim", "failures": "FailSim", "failure": "FailSim",
             "cycles": "CycleSim", "cycle": "CycleSim"}
    if kind not in table:
        raise CliError(f"--stop {text!r}: use time, failures=N or cycles=N",
                       USAGE)
    if kind == "time":
        if value:
            raise CliError("--stop time takes no value; use --length", USAGE)
        return "TimeSim", None
    if not value:
        raise CliError(f"--stop {kind} needs a count, e.g. --stop {kind}=10",
                       USAGE)
    try:
        return table[kind], float(value)
    except ValueError:
        raise CliError(f"--stop {text!r}: {value!r} is not a number",
                       USAGE) from None


def apply_run_options(ws: Any, model_id: str, args: Any) -> dict[str, Any]:
    """Apply -n/-t/--stop/--stats-start/--analysis/--weak-type/--set/--set-file
    to the model in `ws`. Returns the options as they were applied (for the
    results file). Raises CliError (exit 2) for anything unplaceable."""
    from raptor.api import build, paths

    applied: dict[str, Any] = {}
    cfg: dict[str, Any] = {}
    if getattr(args, "runs", None) is not None:
        if args.runs < 1:
            raise CliError("--runs must be at least 1", USAGE)
        cfg["num_runs"] = args.runs
    if getattr(args, "stop", None):
        term, cond = parse_stop(args.stop)
        cfg["termination"] = term
        if cond is not None:
            cfg["run_length"] = cond
    if getattr(args, "length", None) is not None:
        if getattr(args, "stop", None) and parse_stop(args.stop)[0] != "TimeSim":
            raise CliError("--length applies to a time-terminated run; "
                           "with --stop failures/cycles give the count in "
                           "--stop", USAGE)
        cfg["run_length"] = args.length
        cfg.setdefault("termination", "TimeSim")
    if getattr(args, "stats_start", None) is not None:
        cfg["start_stats"] = args.stats_start
    analysis = getattr(args, "analysis", None)
    if analysis is not None:
        names = {a.strip().lower() for a in analysis.split(",") if a.strip()}
        if names == {"none"}:
            names = set()
        unknown = names - {"weak", "cost", "capacity"}
        if unknown:
            raise CliError(f"--analysis: unknown {sorted(unknown)}; use "
                           f"weak, cost, capacity or none", USAGE)
        cfg["weak_link"] = "weak" in names
        cfg["cost"] = "cost" in names
        cfg["capacity"] = "capacity" in names
    extra: dict[str, Any] = {}
    if getattr(args, "weak_type", None):
        extra["nodeAnalType"] = args.weak_type.capitalize()
    if cfg or extra:
        build.configure_system(ws, model_id, **cfg, **extra)
        applied.update({**cfg, **extra})

    # Importance lives in the model's experiment block, not SYSTEM_DATA (it
    # is a property of the run, and `.rbd` has nowhere to store either). Set
    # on the in-memory handle only: `run()` reads it from there, so this
    # works for a legacy model too, and `--save-model` decides separately
    # whether anything is written.
    mode = getattr(args, "importance", None)
    if mode not in (None, "none") and getattr(args, "hierarchy", None):
        # The MCP door refuses this; the CLI must agree, or the same request
        # succeeds or fails depending on which door it came in. Ranking the
        # blocks of an extracted hierarchy against the WHOLE model's numbers
        # would mix two different systems.
        raise CliError(
            "--importance is not available for an isolated hierarchy; run "
            "the whole model", USAGE)
    if mode is not None:
        from raptor.core import run_experiment as _rx
        handle = ws.get(model_id)
        experiment = dict(getattr(handle.rbd, "experiment", None) or {})
        experiment["importance"] = mode
        handle.rbd.experiment = _rx.normalise(experiment)
        applied["importance"] = mode

    texts = list(getattr(args, "sets", []) or [])
    if getattr(args, "set_file", None):
        texts = load_set_file(args.set_file) + texts
    changes = []
    for t in texts:
        try:
            changes.append(paths.apply_assignment(ws, model_id, t))
        except paths.PathError as exc:
            raise CliError(f"--set {t!r}: {exc}", USAGE) from exc
        except Exception as exc:       # BuildError / ElementNotFound / ...
            raise CliError(f"--set {t!r}: {exc}", USAGE) from exc
    if changes:
        applied["set"] = texts
    if getattr(args, "save_model", None):
        dest = args.save_model
        fmt = "rbd" if dest.lower().endswith(".rbd") else "rbds"
        res = ws.save_model_as(model_id, dest, fmt=fmt)
        eprint(f"saved changed model: {res.get('path', dest)}")
    return applied
