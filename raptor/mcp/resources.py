"""MCP resources — examples, models, runs, docs, renders.

Read-only URIs the user or model can pull by name:

    raptor://examples              bundled example models
    raptor://examples/{name}       one example as .rbds text
    raptor://models/{model_id}     the live model as .rbds
    raptor://runs/{run_id}/{section}  full report text for a run
    raptor://docs/{chapter}        a chapter of the tutorial book
    raptor://renders/{name}.png    a rendered diagram (bytes)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from raptor.bundled import data_root
from raptor.mcp.server import mcp

REPO = data_root()
CHAPTERS = REPO / "new_docs" / "chapters"


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------

@mcp.resource("raptor://examples", mime_type="application/json")
def list_examples_resource() -> str:
    """List bundled example models as JSON."""
    from raptor.mcp.examples import list_examples
    # names only: a path would show where the server is installed
    return json.dumps([{"name": e["name"]} for e in list_examples()], indent=2)


@mcp.resource("raptor://examples/{name}", mime_type="text/plain")
def get_example(name: str) -> str:
    """Read a bundled example model as .rbds text."""
    from raptor.mcp.examples import find_example
    path = find_example(name)
    if path is None:
        raise FileNotFoundError(f"example model {name!r} not found")
    if path.suffix == ".rbds":
        return _cap_text(path.read_text(encoding="utf-8"), what="example")
    # Legacy .rbd -> render as .rbds so the reader gets the friendly format.
    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.fileio.rbds import render_rbds
    return _cap_text(render_rbds(load_rbd(str(path))), what="example")


# ---------------------------------------------------------------------------
# Live models
# ---------------------------------------------------------------------------

@mcp.resource("raptor://models", mime_type="application/json")
def list_models_resource() -> str:
    """Index of the open models (model_id, name, counts) as JSON."""
    from raptor.mcp import context
    rows = [{**m, "source": context.public_source(m.get("source", ""))}
            for m in context.workspace().list_models()]
    return json.dumps(rows, indent=2, default=str)


@mcp.resource("raptor://runs", mime_type="application/json")
def list_runs_resource() -> str:
    """Index of completed runs kept in the workspace (newest first)."""
    from raptor.mcp import context
    from raptor.api.limits import MAX_LIST_RUNS
    runs_dir = context.workspace().root / "runs"
    rows: list[dict] = []
    if runs_dir.is_dir():
        for d in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime,
                        reverse=True):
            if len(rows) >= MAX_LIST_RUNS:
                break
            summ = d / "summary.json"
            if not summ.is_file():
                continue
            try:
                data = json.loads(summ.read_text())
            except (OSError, ValueError):
                continue
            rows.append({k: data.get(k) for k in
                         ("run_id", "model_id", "model_name", "num_runs",
                          "wall_seconds")})
    return json.dumps(rows, indent=2)


@mcp.resource("raptor://models/{model_id}", mime_type="text/plain")
def get_model(model_id: str) -> str:
    """Read the live model as .rbds text."""
    from raptor.mcp import context
    from raptor.core.fileio.rbds import render_rbds
    handle = context.workspace().get(model_id)
    return _cap_text(
        render_rbds(handle.rbd, meta={"file_name": handle.rbd.file_name}),
        what="model")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

#: Resources are unpaginated, so cap what one read can put in context.
MAX_RESOURCE_CHARS = 200_000
_SAFE_ID = re.compile(r"[A-Za-z0-9_\-]+")


def _cap_text(text: str, *, what: str = "resource") -> str:
    if len(text) <= MAX_RESOURCE_CHARS:
        return text
    return (text[:MAX_RESOURCE_CHARS]
            + f"\n… {what} truncated at {MAX_RESOURCE_CHARS} characters; "
              "use the paged tool (get_report/read_doc/get_table) for the "
              "rest.")

#: How many lines to request for a DERIVED section, which is synthesised
#: rather than opened as a file. A resource read has no cursor, so it asks
#: once and truncates. `weak_link` is a handful of summary lines, so this
#: is a guard against a pathological block, not a real limit.
_DERIVED_LIMIT = 10_000


@mcp.resource("raptor://runs/{run_id}/{section}", mime_type="text/plain")
def get_run_section(run_id: str, section: str) -> str:
    """Read a full report section for a completed run.

    The section list and the filenames come from `api.run`, which WRITES the
    artefacts. This module used to hold a fourth copy of that map, and the
    copies had already drifted: `weak_link` is a DERIVED section, served by
    `get_report` from the run's block statistics and not backed by any file,
    so it worked through the `get_report` tool while this resource answered
    *"no section 'weak_link' for run ..."*. Asking the owner which sections
    exist -- rather than holding a list that can miss one -- is what makes
    that divergence impossible.
    """
    from raptor.api.run import (
        ALL_SECTIONS, DERIVED_SECTIONS, get_report, section_path,
    )
    from raptor.mcp import context
    if section not in ALL_SECTIONS or not _SAFE_ID.fullmatch(run_id):
        raise FileNotFoundError(
            f"no section {section!r} for run {run_id!r}; sections: "
            + ", ".join(ALL_SECTIONS)
        )
    if section in DERIVED_SECTIONS:
        # Not a file: synthesise it the same way `get_report` does, so the
        # resource and the tool return identical text. Unpaged, because a
        # resource read has no cursor -- hence `limit`.
        ws = context.workspace()
        payload = get_report(ws, run_id, section=section, limit=_DERIVED_LIMIT)
        text = "\n".join(payload.get("lines", []))
    else:
        path = section_path(context.workspace().root / "runs" / run_id, section)
        if path is None or not path.is_file():
            raise FileNotFoundError(
                f"no section {section!r} for run {run_id!r}"
            )
        text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_RESOURCE_CHARS:
        text = (text[:MAX_RESOURCE_CHARS]
                + f"\n… truncated at {MAX_RESOURCE_CHARS} characters; "
                "use the get_report tool (offset/limit) to page the rest.")
    return text


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------

@mcp.resource("raptor://docs/{chapter}", mime_type="text/plain")
def get_chapter(chapter: str) -> str:
    """Read a chapter of the tutorial book (new_docs/chapters/*.adoc).

    The special name ``mcp-guide`` returns this chapter's MCP guide instead.
    """
    if chapter.lower() in ("mcp-guide", "mcp", "guide", "ch16-mcp"):
        guide = CHAPTERS / "ch16-mcp.adoc"
        if guide.is_file():
            return _cap_text(guide.read_text(encoding="utf-8", errors="replace"),
                             what="chapter")
    if chapter.lower() in ("rbds", "rbds-format", "rbds_format", "format"):
        fmt = REPO / "docs" / "RBDS_FORMAT.md"
        if fmt.is_file():
            return _cap_text(fmt.read_text(encoding="utf-8", errors="replace"),
                             what="chapter")
    chapter = chapter.removesuffix(".adoc")
    if _SAFE_ID.fullmatch(chapter):
        candidate = CHAPTERS / f"{chapter}.adoc"
        if candidate.is_file():
            return _cap_text(
                candidate.read_text(encoding="utf-8", errors="replace"),
                what="chapter")
    available = sorted(p.stem for p in CHAPTERS.glob("*.adoc")) \
        if CHAPTERS.is_dir() else []
    raise FileNotFoundError(
        f"chapter {chapter!r} not found; available: "
        + (", ".join(available) or "(docs are not installed)")
        + ", mcp-guide, rbds-format"
    )


# ---------------------------------------------------------------------------
# Renders
# ---------------------------------------------------------------------------

@mcp.resource("raptor://renders/{name}", mime_type="image/png")
def get_render(name: str) -> bytes:
    """Read a rendered diagram PNG by file name."""
    from raptor.mcp import context
    if (Path(name).name != name
            or not _SAFE_ID.fullmatch(Path(name).stem)
            or Path(name).suffix != ".png"):
        raise FileNotFoundError(f"render {name!r} not found")
    renders = (context.workspace().root / "renders").resolve()
    path = (renders / name).resolve()
    if renders not in path.parents or not path.is_file():
        raise FileNotFoundError(f"render {name!r} not found")
    return path.read_bytes()
