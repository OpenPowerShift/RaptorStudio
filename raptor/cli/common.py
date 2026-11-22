"""Helpers shared by the subcommands: workspaces, output, option groups."""
from __future__ import annotations

import contextlib
import json
import math
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from raptor.cli.exitcodes import INVALID, REFUSED, USAGE, CliError
from raptor.core.run_experiment import IMPORTANCE_MODES

#: Formats a command can write its summary in.
FORMATS = ("text", "json", "ndjson", "csv", "markdown", "none")



def seed_arg(text: str) -> int:
    """`--seed N` or `--seed new` (#292): a fresh seed, drawn now so the
    result can report it and be repeated with `--seed <that number>`."""
    import argparse

    from raptor.core import run_experiment
    if str(text).strip().lower() == "new":
        return run_experiment.resolve_seed({"seed_mode": "new"})
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a whole number or 'new', got {text!r}") from None
    if not 0 <= value <= run_experiment.SEED_MAX:
        raise argparse.ArgumentTypeError(
            f"the seed must be 0..{run_experiment.SEED_MAX}")
    return value

def positive_int(text: str) -> int:
    """An argparse type for a count of at least 1 (`-j`): `-j 0` and
    `-j -3` used to run with one worker and no word."""
    import argparse
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a whole number, got {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def finite_float(text: str) -> float:
    """An argparse type for a time: `-t nan` reached the model and was
    refused as "TermCondition must be a finite number"."""
    import argparse
    import math
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {text!r}") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"expected a finite number, got {text!r}")
    return value


def job_count(text: str) -> int:
    """`-j N`: at least 1, and no more than this machine's cores. More
    workers than cores only made a run slower (`-j 64` took longer than
    `-j 1`), and `-j 9999` was taken without a word."""
    value = positive_int(text)
    cores = os.cpu_count() or 1
    if value > cores:
        print(f"note: -j {value} is more than this machine's {cores} cores; "
              f"using {cores}", file=sys.stderr)
        return cores
    return value


def eprint(*parts: object) -> None:
    """Status, progress and warnings go to stderr, never stdout."""
    print(*parts, file=sys.stderr)


@contextlib.contextmanager
def workspace_for(path: str | None = None) -> Iterator[Any]:
    """A `Workspace`: the one named by `--workspace`, else a throwaway
    directory deleted on exit. The CLI never writes into the user's model
    folder unless asked to."""
    from raptor.api.workspace import Workspace
    if path:
        yield Workspace(Path(path).expanduser())
        return
    with tempfile.TemporaryDirectory(prefix="raptor-cli-") as tmp:
        yield Workspace(tmp)


def open_model(ws: Any, model_path: str) -> str:
    """Register `model_path` in `ws`; return the model id."""
    from raptor.api.workspace import WorkspaceError
    p = Path(model_path).expanduser()
    if p.is_dir():
        raise CliError(f"{model_path} is a folder, not a model file", USAGE)
    if not p.is_file():
        raise CliError(f"no such model file: {model_path}", USAGE)
    try:
        return ws.open_model(p)["model_id"]
    except WorkspaceError as exc:
        raise CliError(str(exc), USAGE) from exc
    except Exception as exc:  # unreadable / corrupt file
        raise CliError(f"cannot read {model_path}: {exc}", USAGE) from exc


def issues_for(ws: Any, model_id: str) -> list[Any]:
    from raptor.api.validate import issues
    return issues(ws.get(model_id).rbd)


def require_runnable(ws: Any, model_id: str, strict: bool = False) -> list[str]:
    """Raise CliError if the model has errors; return warning texts.

    A shape the engine refuses on purpose (`UnrunnableModel`) is exit 4;
    ordinary validation errors are exit 3."""
    # One validation, read twice: it loads the model into the engine, which
    # is seconds on a large model.
    found = issues_for(ws, model_id)
    errors = [i for i in found if i.severity == "error"]
    warns = [str(i) for i in found if i.severity == "warning"]
    if errors:
        refused = [e for e in errors if _is_refusal(e)]
        text = "\n".join(str(e) for e in errors)
        raise CliError(
            f"the model has {len(errors)} error(s):\n{text}",
            REFUSED if refused and len(refused) == len(errors) else INVALID)
    if strict and warns:
        raise CliError(
            f"the model has {len(warns)} warning(s) (--strict):\n"
            + "\n".join(warns), INVALID)
    return warns


def _is_refusal(issue: Any) -> bool:
    msg = getattr(issue, "message", "")
    return "cannot be run" in msg or "is refused" in msg


def emit_json(obj: Any, dest: str | None, *, indent: int | None = 2) -> None:
    """Write JSON to `dest` ("-" or None = stdout)."""
    text = json.dumps(obj, indent=indent, default=str, allow_nan=False
                      ) if _finite(obj) else json.dumps(
        _scrub(obj), indent=indent, default=str, allow_nan=False)
    if dest in (None, "-"):
        sys.stdout.write(text + "\n")
        return
    write_text(Path(dest), text + "\n")


def _finite(obj: Any) -> bool:
    try:
        json.dumps(obj, default=str, allow_nan=False)
        return True
    except ValueError:
        return False


def _scrub(obj: Any) -> Any:
    """NaN/Infinity are not JSON; they become null."""
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    return obj


def write_text(path: Path, text: str) -> None:
    from raptor.cli.exitcodes import OUTPUT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    except OSError as exc:
        raise CliError(f"cannot write {path}: {exc}", OUTPUT) from exc


def out(stream: TextIO | None = None) -> TextIO:
    return stream or sys.stdout


def color_enabled(when: str) -> bool:
    if when == "always":
        return True
    if when == "never" or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def add_run_options(p: Any) -> None:
    """The run-control options shared by run, sweep, compare and estimate."""
    g = p.add_argument_group("run control")
    g.add_argument("-n", "--runs", type=int, metavar="N",
                   help="number of runs (default: the model's)")
    g.add_argument("-t", "--length", type=finite_float, metavar="T",
                   help="run length for a time-terminated run")
    g.add_argument("--stop", metavar="KIND[=VALUE]",
                   help="time | failures=N | cycles=N (default: the model's)")
    g.add_argument("--stats-start", type=finite_float, metavar="T",
                   help="start statistics at T")
    g.add_argument("--seed", type=seed_arg, metavar="N|new",
                   help="salt every random stream: a number repeats a run "
                        "(a GUI run's seed with -j 1 for Cores 1, -j 2 for "
                        "more); 'new' draws one and reports it (default: "
                        "the model's saved seed, else Raptor's fixed table)")
    g.add_argument("--block-streams", choices=("model", "separate"),
                   dest="block_streams",
                   help="separate: every block that shares a random stream "
                        "gets its own for this run, as the GUI's Run panel "
                        "Streams option (the model file is not changed); "
                        "model: the blocks' own (default: the model's "
                        "saved choice)")
    g.add_argument("-j", "--jobs", type=job_count, default=1, metavar="N",
                   help="parallel workers (default 1; N>1 is reproducible "
                        "but not draw-identical to 1)")
    g.add_argument("--timeout", metavar="DURATION", default="10m",
                   help="give up after DURATION, e.g. 90s, 10m (default 10m); "
                        "partial results are kept")
    g.add_argument("--analysis", metavar="LIST",
                   help="weak,cost,capacity (replaces the model's setting); "
                        "'none' clears all")
    g.add_argument("--precision", metavar="P",
                   help="run until the watched metric's relative half-width "
                        "is P, e.g. 10%% or 0.1 (implies growing batches; "
                        "ignores --runs)")
    g.add_argument("--precision-metric", default="system.ao", metavar="NAME",
                   help="metric watched by --precision (default system.ao; a "
                        "probability is watched as 1 - value, a time as itself)")
    g.add_argument("--min-trials", type=int, default=50, metavar="N",
                   help="first --precision batch (default 50)")
    g.add_argument("--max-trials", type=int, default=20000, metavar="N",
                   help="most --precision may run (default 20000)")
    g.add_argument("--precision-batch", type=int, default=50, metavar="N",
                   help="smallest --precision step (default 50)")
    g.add_argument("--precision-confidence", type=float, default=0.95, metavar="C",
                   help="confidence of the --precision interval (default 0.95)")
    g.add_argument("--importance", metavar="MODE",
                   choices=IMPORTANCE_MODES,
                   help="IEC 61078 Annex D importance measures estimated by "
                        "re-simulation: improvement (RRW, Vesely-Fussell), "
                        "risk (RAW), both (adds Birnbaum). Costs one extra "
                        "batch per block, two for 'both'")
    g.add_argument("--weak-type", metavar="TYPE",
                   choices=("availability", "dependability", "reliability"),
                   help="weak-link analysis type")
    s = p.add_argument_group("changing parameters (a copy; the model file is "
                             "untouched unless --save-model)")
    s.add_argument("-s", "--set", action="append", default=[],
                   metavar="PATH=VALUE", dest="sets",
                   help="change one field, repeatable; see 'raptor help "
                        "set-paths'")
    s.add_argument("--set-file", metavar="FILE",
                   help="many PATH=VALUE lines (or a TOML/JSON table)")
    s.add_argument("--save-model", metavar="PATH",
                   help="also write the changed model to PATH")


def parse_duration(text: str) -> float:
    """`90`, `90s`, `10m`, `2h` -> seconds."""
    t = text.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600}
    try:
        seconds = (float(t[:-1]) * mult[t[-1]] if t and t[-1] in mult
                   else float(t))
    except ValueError:
        raise CliError(f"bad duration {text!r}; use e.g. 90s, 10m, 2h",
                       USAGE) from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise CliError(f"bad duration {text!r}: give a time above 0, "
                       "e.g. 90s, 10m, 2h", USAGE)
    return seconds


def load_set_file(path: str) -> list[str]:
    """PATH=VALUE lines, or a TOML/JSON mapping of path -> value."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise CliError(f"no such file: {path}", USAGE)
    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(text)
        elif suffix == ".toml":
            import tomllib
            data = tomllib.loads(text)
        else:
            return [ln.strip() for ln in text.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]
    except Exception as exc:
        raise CliError(f"cannot parse {path}: {exc}", USAGE) from exc
    if not isinstance(data, dict):
        raise CliError(f"{path} must be a table of PATH = VALUE", USAGE)
    # Top-level keys are paths ("block:Pump A.fail_dist" = "weibull"); a
    # table such as [system] contributes "system.<key>".
    flat: list[str] = []
    for key, val in data.items():
        if isinstance(val, dict):
            flat.extend(f"{key}.{k}={_toml_value(v)}" for k, v in val.items())
        else:
            flat.append(f"{key}={_toml_value(val)}")
    return flat


def _toml_value(v: Any) -> str:
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def save_model_back(ws: Any, model_id: str, path: str, *,
                    in_place: bool) -> None:
    """Write an edited model back to the file the user named.

    Shared by `edit`, `groups` and `import-table` so the temp-then-replace
    rule (and the archive refusal) cannot drift between them. The target is
    resolved first: replacing a symlink's path would leave its real target
    stale.
    """
    if not in_place:
        return
    source = Path(path).expanduser()
    suffix = source.suffix.lower()
    if suffix == ".rbdz":
        raise CliError(
            "cannot write an .rbdz here; save the model as .rbds first",
            USAGE)
    fmt = "rbd" if suffix == ".rbd" else "rbds"
    target = source.resolve()
    temp = target.with_name(target.stem + ".raptor-tmp" + target.suffix)
    result = ws.save_model_as(model_id, str(temp), fmt=fmt)
    Path(result["path"]).replace(target)
