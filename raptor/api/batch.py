"""Run a model several times with different settings, on private copies.

The shared core of `raptor sweep` and `raptor compare` (and anything that
wants "the same model, different parameters"). Each variant is a deep copy
registered in the workspace, with its `PATH=VALUE` assignments applied
through :mod:`raptor.api.paths`, so the source model is never touched.
"""
from __future__ import annotations

import copy
import itertools
from collections.abc import Callable
from typing import Any

from raptor.api import paths
from raptor.api.results import metrics_to_dict
from raptor.api.run import run as api_run
from raptor.api.workspace import Workspace


def expand_vary(spec: str) -> tuple[str, list[str]]:
    """`PATH=START:STOP:STEP` or `PATH=v1,v2,...` -> (PATH, [value text]).

    A range includes STOP (when STEP lands on it); the values are written
    with repr so no digits are lost.
    """
    path, value = paths.split_assignment(spec)
    if ":" in value and "," not in value:
        parts = value.split(":")
        if len(parts) != 3:
            raise paths.PathError(
                f"{spec!r}: a range is START:STOP:STEP")
        try:
            start, stop, step = (float(p) for p in parts)
        except ValueError:
            raise paths.PathError(f"{spec!r}: range values must be numbers") \
                from None
        if step == 0 or (stop - start) * step < 0:
            raise paths.PathError(f"{spec!r}: STEP must move START toward STOP")
        n = round((stop - start) / step) + 1
        vals = [start + i * step for i in range(max(n, 0))
                if (start + i * step - stop) * step <= 1e-9 * abs(step)]
        return path, [_trim(v) for v in vals]
    if "," in value:
        return path, [v.strip() for v in value.split(",") if v.strip()]
    return path, [value]


def _trim(v: float) -> str:
    v = round(float(v), 12)
    return str(int(v)) if v.is_integer() else repr(v)


def grid(varies: list[tuple[str, list[str]]]) -> list[list[str]]:
    """The cartesian product as lists of `PATH=VALUE` assignments."""
    axes = [[f"{p}={v}" for v in vals] for p, vals in varies]
    return [list(combo) for combo in itertools.product(*axes)]


def run_variants(
    ws: Workspace, model_id: str, variants: list[dict[str, Any]], *,
    base_assignments: list[str] | None = None,
    num_runs: int | None = None, workers: int = 1, timeout_s: float = 600.0,
    seed: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    abort: Any = None,
) -> list[dict[str, Any]]:
    """Run each variant `{"label": str, "assignments": [PATH=VALUE, ...]}`.

    Returns one dict per variant: label, assignments, run_id, num_runs and
    the metric summary. A variant whose assignments or run fail is reported
    with an `error` instead of stopping the batch. `abort` (a
    `threading.Event`) stops it: the variant in flight keeps what it has
    completed, and the rest are not started.
    """
    source = ws.get(model_id).rbd
    out: list[dict[str, Any]] = []
    for i, var in enumerate(variants):
        if abort is not None and abort.is_set():
            break
        label = var.get("label", f"variant {i + 1}")
        if progress:
            progress(i, len(variants), label)
        entry: dict[str, Any] = {"label": label,
                                 "assignments": list(var["assignments"])}
        try:
            clone = copy.deepcopy(source)
            info = ws.register_model(clone, name=f"variant{i + 1}",
                                     source="batch")
            vid = info["model_id"]
            for text in (base_assignments or []) + list(var["assignments"]):
                paths.apply_assignment(ws, vid, text)
            res = api_run(ws, vid, num_runs=num_runs, workers=workers,
                          timeout_s=timeout_s, confirm_long_run=True,
                          seed=seed, abort=abort)
            entry.update(run_id=res.run_id, num_runs=res.num_runs,
                         summary=metrics_to_dict(res.metrics),
                         warnings=res.warnings)
        except Exception as exc:                          # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out.append(entry)
    if progress:
        progress(len(variants), len(variants), "")
    return out
