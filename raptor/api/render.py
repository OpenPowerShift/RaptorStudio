"""Diagram rendering — Qt-free wrapper over the render worker.

Counterpart of :mod:`raptor.render.worker`.  This module **never imports
PySide6**: it spawns the worker as a separate process with
``subprocess.run(..., timeout=60)`` (an argument list, never
``shell=True``), hands it a JSON job on stdin, and returns the PNG path
plus the worker's metadata.

A missing/broken Qt degrades to a typed :class:`RenderUnavailable`
instead of taking the caller down — results are Markdown and remain
available regardless.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "RenderError",
    "RenderFailed",
    "RenderOutputMissing",
    "RenderTimeout",
    "RenderUnavailable",
    "qt_available",
    "render_diagram",
    "render_svg_file",
    "worker_path",
]

#: Wall-clock budget for a single render.  Qt import alone is ~1 s; a
#: real diagram render is a few seconds at most.
DEFAULT_TIMEOUT_S = 60


class RenderError(Exception):
    """Base class for every render failure."""


class RenderUnavailable(RenderError):
    """Qt is not importable, so no diagram can be rendered."""


class RenderTimeout(RenderError):
    """The worker exceeded its timeout and was killed."""


class RenderFailed(RenderError):
    """The worker exited non-zero (bad model, crash, bad job)."""


class RenderOutputMissing(RenderError):
    """The worker reported success but produced no PNG."""


def worker_path() -> Path:
    """Absolute path to the standalone worker script."""
    return Path(__file__).resolve().parent.parent / "render" / "worker.py"


def qt_available() -> bool:
    """True when PySide6 can be imported.

    Uses ``find_spec`` so this module stays Qt-free (an actual import
    would load Qt into an API caller or the MCP server). The MCP server
    asks the same question before registering the render tool, so the
    answer lives here rather than being re-derived there.
    """
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        return False


def render_diagram(
    model_path: str | Path,
    out_path: str | Path,
    *,
    level: int = 0,
    highlight: list[str] | None = None,
    zoom_to: list[str] | None = None,
    margin: float = 12.0,
    theme: str = "light",
    max_px: int = 1600,
    include_annotations: bool = True,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[str, dict[str, Any]]:
    """Render ``model_path`` (``.rbd``/``.rbds``) to ``out_path`` (PNG).

    The worker renders the model's stored coordinates verbatim -- layout
    is owned by the core ``auto_layout`` tool / ``build_model``, never by
    rendering -- so authored ``x``/``y`` are preserved.

    ``zoom_to`` frames a close-up on the named/id'd element(s) *within the
    currently rendered level* (it composes with ``level``; it does not
    change which level is shown), padded by ``margin`` scene units.  This
    is how a large model is rendered legibly in sections.  A selector may
    be a name, a bare id, or ``"Kind:id"``; one that matches nothing (or a
    bare id matching two element kinds) raises :class:`RenderFailed`.
    ``meta`` reports ``elements_in_view``, ``px_per_unit``,
    ``effective_label_px`` and a ``legible`` flag so a caller knows when to
    zoom in further.

    Returns ``(png_path, meta)`` where ``meta`` carries ``width``,
    ``height``, ``elements_drawn``, ``png_path``, ``model_hash`` (SHA-256
    of the model file content, added by this wrapper, not the worker) plus
    annotation / highlight counts.  Raises a :class:`RenderError` subclass
    on any failure.  The worker is a separate process, launched with an
    argument list -- never a shell.
    """
    if not qt_available():
        raise RenderUnavailable(
            "PySide6 is not installed, so diagrams cannot be rendered; "
            "results are still available as Markdown and JSON")

    script = worker_path()
    if not script.is_file():
        raise RenderUnavailable(f"render worker not found: {script}")

    if theme not in ("light", "dark"):
        raise RenderError(f"theme must be 'light' or 'dark', got {theme!r}")
    from raptor.api.limits import MAX_RENDER_PX
    if int(max_px) < 16:
        raise RenderError(f"max_px too small: {max_px}")
    if int(max_px) > MAX_RENDER_PX:
        raise RenderError(
            f"max_px too large: {max_px}; the limit is {MAX_RENDER_PX}")
    if int(level) < 0:
        raise RenderError(f"level must be >= 0, got {level}")
    if not math.isfinite(float(margin)) or float(margin) < 0:
        raise RenderError(f"margin must be a finite value >= 0, got {margin}")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    job = {
        "model": str(model_path),
        "out": str(out),
        "level": int(level),
        "highlight": list(highlight or []),
        "zoom_to": list(zoom_to or []),
        "margin": float(margin),
        "theme": theme,
        "max_px": int(max_px),
        "include_annotations": bool(include_annotations),
    }

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(job),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderTimeout(
            f"render timed out after {timeout_s}s for {model_path}") from exc
    except OSError as exc:
        raise RenderUnavailable(f"could not start render worker: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        message = detail[-1] if detail else f"exit code {proc.returncode}"
        raise RenderFailed(
            f"render worker failed for {model_path}: {message}")

    try:
        meta = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RenderFailed(
            f"render worker returned invalid metadata: "
            f"{(proc.stdout or '')[:200]!r}") from exc
    if not isinstance(meta, dict):
        raise RenderFailed("render worker metadata was not a JSON object")

    if not out.is_file():
        raise RenderOutputMissing(
            f"render worker reported success but {out} does not exist")

    meta.setdefault("png_path", str(out))
    # Content hash of the model file: cheap (a few KB) and lets a caller
    # key a cache on the rendered content, not on the path/options.
    try:
        meta.setdefault(
            "model_hash",
            hashlib.sha256(Path(model_path).read_bytes()).hexdigest())
    except OSError:
        pass
    return str(out), meta


def render_svg_file(
    model_path: str | Path,
    out_path: str | Path,
    *,
    level: int = 0,
    highlight: list[str] | None = None,
    zoom_to: list[str] | None = None,
    margin: float = 12.0,
    theme: str = "light",
    include_annotations: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Draw ``model_path`` (``.rbd``/``.rbds``) as SVG to ``out_path``, in
    process and without Qt (see `raptor.render.svg`). Returns
    ``(svg_path, meta)``; a bad level or selector raises ``ValueError``."""
    from raptor.render.svg import render_svg
    path = Path(model_path)
    if path.suffix.lower() == ".rbds":
        from raptor.core.fileio.rbds import load_rbds
        rbd = load_rbds(str(path))
    else:
        from raptor.core.fileio.rbd import load_rbd
        rbd = load_rbd(str(path))
    svg, meta = render_svg(rbd, level=level, highlight=highlight or [],
                           zoom_to=zoom_to or [], margin=margin, theme=theme,
                           include_annotations=include_annotations)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    meta["svg_path"] = str(out)
    return str(out), meta
