"""Rebuild a stored run's result tables (see :mod:`raptor.api.tables`).

A completed run keeps its report texts and per-run figures on disk in the
workspace; this reads them back into a :class:`ResultsBundle`, so the MCP
tools can export or read the same tables the GUI does.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from raptor.api.run import RunNotFoundError, run_dir, section_path
from raptor.api.tables import ResultsBundle
from raptor.api.workspace import Workspace

_BEGIN_RUN = re.compile(r"Begin_Run_(\d+)")


def _read_lines(path: Path | None) -> list[str]:
    """Lines of *path*, or none if it is absent or unknown.

    *path* is ``None`` for a section name the layout does not
    declare -- possible now that the names come from `api.run`
    rather than from literals in this file.
    """
    if path is None or not path.is_file():
        return []
    return path.read_text().splitlines()


def _split_events(lines: list[str]) -> list[list[str]]:
    """The stored event log is every run's lines back to back; split it at
    the run markers (one list if there are none)."""
    runs: list[list[str]] = []
    for line in lines:
        if _BEGIN_RUN.search(line) or not runs:
            runs.append([])
        runs[-1].append(line)
    return runs


def read_summary(runs_dir: Path, run_id: str, *, model_id: str | None = None,
                 required: bool = False) -> dict:
    """The run's ``summary.json``, validated, or ``{}`` when absent.

    One reader for every door (tables, metrics, report isolation): they
    each opened the file and re-applied the same identity checks, and
    their messages had drifted. A summary naming another run, or another
    model than the caller's handle, is corrupt metadata -- not an empty
    result.
    """
    from raptor.api.workspace import RunArtefactsError

    path = Path(runs_dir) / "summary.json"
    if not path.is_file():
        if required:
            raise RunNotFoundError(f"run {run_id!r} not found")
        return {}
    try:
        summary = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunArtefactsError(
            f"run {run_id!r} has a corrupt summary; re-run it") from exc
    if not isinstance(summary, dict):
        raise RunArtefactsError(
            f"run {run_id!r} has a corrupt summary; re-run it")
    if summary.get("run_id") not in (None, run_id):
        raise RunArtefactsError(
            f"run {run_id!r} has a summary for another run; re-run it")
    if model_id is not None and summary.get("model_id") != model_id:
        raise ValueError(
            f"run {run_id!r} belongs to model "
            f"{summary.get('model_id')!r}, not {model_id!r}")
    return summary


def read_per_run(runs_dir: Path, run_id: str) -> list[dict] | None:
    """The run's ``per_run.json`` rows, or None when the file is absent."""
    from raptor.api.workspace import RunArtefactsError

    path = Path(runs_dir) / "per_run.json"
    if not path.is_file():
        return None
    try:
        rows = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunArtefactsError(
            f"run {run_id!r} has corrupt per-run data; re-run it") from exc
    if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows):
        raise RunArtefactsError(
            f"run {run_id!r} has corrupt per-run data; re-run it")
    return rows


def bundle_from_run(workspace: Workspace, run_id: str,
                    model_id: str | None = None) -> ResultsBundle:
    """The stored run `run_id` as a results bundle. Raises
    :class:`~raptor.api.run.RunNotFoundError` for an unknown run.

    When `model_id` is given, a run belonging to another model is
    refused: result tables were otherwise readable through any model
    handle, and the output carried no model/run identity to reveal it.
    """
    runs_dir = run_dir(workspace, run_id)
    if not runs_dir.is_dir():
        raise RunNotFoundError(f"Run {run_id!r} not found")
    from raptor.api.workspace import RunArtefactsError
    summary = read_summary(runs_dir, run_id, model_id=model_id)
    per_run = read_per_run(runs_dir, run_id) or []
    try:
        num_runs = int(summary.get("num_runs", len(per_run)) or 0)
    except (TypeError, ValueError) as exc:
        raise RunArtefactsError(
            f"Run {run_id!r} has invalid run metadata; re-run it") from exc
    if num_runs < 0:
        raise RunArtefactsError(
            f"Run {run_id!r} has invalid run metadata; re-run it")
    # Filenames come from `api.run` (the writer), not from literals restated
    # here. The `_read_lines(None)` guard covers a run whose artefact is
    # absent -- an incomplete or older run directory.
    return ResultsBundle(
        model_name=str(summary.get("model_name", "")),
        num_runs=num_runs,
        units=str(summary.get("units", "")),
        final_lines=_read_lines(section_path(runs_dir, "final_results")),
        end_of_run_lines=_read_lines(section_path(runs_dir, "end_of_run")),
        per_run=per_run,
        capacity_lines=_read_lines(section_path(runs_dir, "capacity")),
        cost_lines=_read_lines(section_path(runs_dir, "cost")),
        event_logs=_split_events(
            _read_lines(section_path(runs_dir, "events"))),
    )
