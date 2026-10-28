#!/usr/bin/env python3
"""Standalone diagram render worker.

Runs in its own process with ``QT_QPA_PLATFORM=offscreen`` -- the MCP
server never imports Qt.  One job kind only: ``diagram``.

Job input (JSON on stdin, preferred) keys:

    model               path to a .rbd or .rbds model file   (required)
    out                 path to write the PNG                (required)
    level               hierarchy level/parent id (default 0)
    highlight           list of element names or ids to outline
    zoom_to             element name(s)/id(s) to frame a close-up on
    margin              scene-unit padding around the zoom box (default 12)
    theme               "light" | "dark" (default "light")
    max_px              long-edge cap in pixels (default 1600)
    include_annotations draw .rbds annotations (default true)

It may also be driven positionally for convenience / debugging::

    python worker.py MODEL OUT --level 0 --theme light --max-px 1600

On success it writes the PNG and prints ONE JSON object to stdout::

    {"width":..., "height":..., "elements_drawn":..., "elements_in_view":...,
     "annotations_drawn":..., "highlighted":..., "level":..., "theme":...,
     "max_px":..., "zoom_to":[...], "px_per_unit":..., "effective_label_px":...,
     "legible":..., "png_path":...}

``px_per_unit`` is how many device pixels one scene unit becomes in the
final PNG; ``effective_label_px`` is ``8.0 * px_per_unit`` (the smallest
labels are ~8 scene units tall) and ``legible`` is ``effective_label_px >=
7.0`` — a heuristic floor, not a guarantee for every label.

On failure it prints a clear message to stderr and exits non-zero
(never a traceback on stdout).

Rendering reuses the GUI's exact approach (see
``MainWindow._render_scene_png`` / ``_export_bitmap`` in
``raptor/gui/ui/main_window.py``): render the content rect -- never the
infinite canvas scene rect, which OOMs -- supersampled at scale 4, then
downscaled to ``max_px`` so text stays legible.
"""
from __future__ import annotations

# ---------------------------------------------------------------------
# Qt setup must happen before any PySide6 import.
# ---------------------------------------------------------------------
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

# `_SCALE` and `_MAX_SUPERSAMPLED_EDGE` moved to `raptor.render.scene_pixmap`
# along with the render that used them, and are gone from here: the render
# was their only reader in this module.
#: Margin (scene units) added around the content bounding rect, matching
#: DiagramView._apply_scene_layout's own -30/+30 padding.
_PAD = 30.0


class WorkerError(Exception):
    """Anything that should surface as a clean non-zero exit."""


# ---------------------------------------------------------------------
# Modal stubbing: a stray QDialog/QMessageBox modal would hang the
# worker forever.  QMenu.exec cannot be monkeypatched at all, so no menu
# is ever opened by this script.
# ---------------------------------------------------------------------

def _stub_modals() -> None:
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

    QDialog.exec = lambda self: QDialog.DialogCode.Accepted  # type: ignore[method-assign]
    QFileDialog.exec = lambda self: QFileDialog.DialogCode.Accepted  # type: ignore[method-assign]
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))  # type: ignore[method-assign]
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))  # type: ignore[method-assign]
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)  # type: ignore[method-assign]
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)  # type: ignore[method-assign]
    QMessageBox.information = staticmethod(  # type: ignore[method-assign]
        lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.question = staticmethod(  # type: ignore[method-assign]
        lambda *a, **k: QMessageBox.StandardButton.Discard)
    try:
        import raptor.gui.ui.dialogs as dialogs
        dialogs.question_box = lambda *a, **kw: False
    except Exception:
        # The dialogs module is not on every install; the Qt-level stubs
        # above are the ones that actually matter for a render.
        pass


# ---------------------------------------------------------------------
# Job parsing
# ---------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="raptor-render-worker",
        description="Render one RAPTOR block diagram to a PNG (offscreen).")
    p.add_argument("model", nargs="?", help="path to .rbd/.rbds model")
    p.add_argument("out", nargs="?", help="path to write the PNG")
    p.add_argument("--level", type=int, default=0,
                   help="hierarchy level/parent id to render (default 0)")
    p.add_argument("--highlight", action="append", default=[],
                   help="element name or id to outline (repeatable, "
                        "or comma-separated)")
    p.add_argument("--zoom-to", action="append", default=[],
                   help="element name or id to frame a close-up on "
                        "(repeatable, or comma-separated)")
    p.add_argument("--margin", type=float, default=12.0,
                   help="scene-unit padding around the zoom box (default 12)")
    p.add_argument("--theme", default="light", choices=("light", "dark"))
    p.add_argument("--max-px", type=int, default=1600,
                   help="long-edge cap in pixels (default 1600)")
    p.add_argument("--no-annotations", action="store_true",
                   help="do not draw .rbds annotations")
    return p.parse_args(argv)


def _read_job(argv: list[str]) -> dict[str, Any]:
    """Return the single job dict, from stdin JSON when no positional
    model was given, otherwise from CLI arguments."""
    args = _parse_args(argv)

    if not args.model:
        raw = sys.stdin.read()
        if not raw.strip():
            raise WorkerError(
                "no job: expected JSON on stdin or MODEL/OUT arguments")
        try:
            job = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorkerError(f"invalid job JSON: {exc}") from exc
        if not isinstance(job, dict):
            raise WorkerError("job JSON must be an object")
        return job

    highlights: list[str] = []
    for entry in args.highlight:
        highlights.extend(s.strip() for s in entry.split(",") if s.strip())
    zoom_to: list[str] = []
    for entry in args.zoom_to:
        zoom_to.extend(s.strip() for s in entry.split(",") if s.strip())
    return {
        "model": args.model,
        "out": args.out,
        "level": args.level,
        "highlight": highlights,
        "zoom_to": zoom_to,
        "margin": args.margin,
        "theme": args.theme,
        "max_px": args.max_px,
        "include_annotations": not args.no_annotations,
    }


# ---------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------

def _resolve_highlights(view: Any, selectors: list[Any]) -> list[Any]:
    """Map element names/ids in `selectors` to (kind, id, item) triples.

    Names are matched case-insensitively across blocks, nodes, events and
    hierarchies on the level currently displayed; bare integers match an
    id of any kind.  Unmatched selectors are ignored (a highlight for a
    hidden/other-level element should not fail the render)."""
    index: list[tuple[int, str, Any]] = []
    for bid, obj in getattr(view, "blocks", {}).items():
        item = view._block_items.get(bid)
        if item is not None:
            index.append((int(bid), str(getattr(obj, "name", "")), item))
    for nid, obj in getattr(view, "nodes", {}).items():
        item = view._node_items.get(nid)
        if item is not None:
            index.append((int(nid), str(getattr(obj, "node_name", "")), item))
    for eid, obj in getattr(view, "events", {}).items():
        item = view._event_items.get(eid)
        if item is not None:
            index.append((int(eid), str(getattr(obj, "name", "")), item))
    for hid, obj in getattr(view, "hiers", {}).items():
        item = view._hier_items.get(hid)
        if item is not None:
            index.append((int(hid), str(getattr(obj, "name", "")), item))

    matched: list[Any] = []
    seen: set[int] = set()
    for sel in selectors:
        text = str(sel).strip()
        if not text:
            continue
        want_id: int | None = None
        try:
            want_id = int(text)
        except ValueError:
            want_id = None
        low = text.lower()
        for eid, name, item in index:
            if eid in seen:
                continue
            if (want_id is not None and eid == want_id) or \
                    (want_id is None and name.lower() == low):
                seen.add(eid)
                matched.append((eid, name, item))
    return matched


def _content_rect(view: Any) -> Any:
    """The content bounding rect of the currently displayed level,
    including annotations -- never the (deliberately huge) scene rect.

    Mirrors DiagramView.fit_all / MainWindow._export_bitmap: union every
    scene item except the world-sized grid backdrop, then pad by 30."""
    from PySide6.QtCore import QRectF
    from raptor.gui.ui.diagram import _GridBackground

    content = QRectF()
    for item in view._scene.items():
        if isinstance(item, _GridBackground):
            continue
        content = content.united(item.sceneBoundingRect())
    if content.isEmpty():
        raise WorkerError("model has no visible elements to render")
    return content.adjusted(-_PAD, -_PAD, _PAD, _PAD)


def _render_pixmap(view: Any, rect: Any) -> Any:
    """DELEGATED to `raptor.render.scene_pixmap`.

    This had its own copy of the supersampled render, and its docstring
    claimed it matched `MainWindow._render_scene_png` "exactly". It did
    not: the window forced semantic zoom and this did not, so a diagram
    rendered through the worker could come out with no element labels
    whenever the view was zoomed far enough out to hide them.

    The shared helper is the union of both versions, so the two doors now
    produce the same image."""
    from raptor.render.scene_pixmap import render_scene_to_pixmap
    return render_scene_to_pixmap(view, rect)


def _draw_highlights(pm: Any, rect: Any, matches: list[Any]) -> None:
    """Outline the matched elements on the (possibly downscaled) pixmap.

    The scene was rendered from `rect` onto the whole pixmap, so the
    mapping is a plain linear scale of each item's sceneBoundingRect."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen

    if not matches:
        return
    sx = pm.width() / rect.width()
    sy = pm.height() / rect.height()
    long_edge = max(pm.width(), pm.height())
    width = max(3, round(long_edge / 250))
    pen = QPen(QColor("#FF3B30"), width)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for _eid, _name, item in matches:
        sb = item.sceneBoundingRect()
        target = QRectF((sb.x() - rect.x()) * sx, (sb.y() - rect.y()) * sy,
                        sb.width() * sx, sb.height() * sy)
        target = target.adjusted(-width, -width, width, width)
        painter.drawRect(target)
    painter.end()


def _count_elements(view: Any) -> int:
    return (len(getattr(view, "_block_items", {})) +
            len(getattr(view, "_node_items", {})) +
            len(getattr(view, "_event_items", {})) +
            len(getattr(view, "_hier_items", {})))


def _element_index(view: Any) -> list[tuple[str, int, str, Any]]:
    """(kind, id, name, item) for every element drawn on the current level."""
    out: list[tuple[str, int, str, Any]] = []
    for kind, coll, itemcoll, nameattr in (
            ("Block", "blocks", "_block_items", "name"),
            ("Node", "nodes", "_node_items", "node_name"),
            ("Event", "events", "_event_items", "name"),
            ("Hierarchy", "hiers", "_hier_items", "name")):
        for oid, obj in getattr(view, coll, {}).items():
            item = getattr(view, itemcoll, {}).get(oid)
            if item is not None:
                out.append((kind, int(oid), str(getattr(obj, nameattr, "")),
                            item))
    return out


_ZOOM_KIND_ALIASES = {
    "block": "Block", "node": "Node", "event": "Event",
    "hier": "Hierarchy", "hierarchy": "Hierarchy", "oval": "Hierarchy",
}


def _resolve_zoom_items(view: Any, selectors: list[Any]) -> list[Any]:
    """Resolve `zoom_to` selectors to items, erroring on an unknown or
    ambiguous selector.

    Unlike `_resolve_highlights` (which silently ignores misses), a
    zoom_to must resolve clearly: a bare integer that matches more than
    one element kind (a Block and a Node can share an id) is an error, and
    the message names the matches.  Accepts a name, a bare id, or a
    ``"Kind:id"`` selector (Block/Node/Event/Hier).
    """
    index = _element_index(view)
    items: list[Any] = []
    for sel in selectors:
        text = str(sel).strip()
        if not text:
            continue
        kind: str | None = None
        num: int | None = None
        name: str | None = None
        head, sep, rest = text.partition(":")
        if sep and head.strip().lower() in _ZOOM_KIND_ALIASES:
            kind = _ZOOM_KIND_ALIASES[head.strip().lower()]
            try:
                num = int(rest.strip())
            except ValueError:
                raise WorkerError(
                    f"zoom_to {text!r}: expected 'Kind:id' with an integer id"
                ) from None
        else:
            try:
                num = int(text)
            except ValueError:
                name = text.lower()
        matches = [
            t for t in index
            if (kind is None or t[0] == kind)
            and ((num is not None and t[1] == num)
                 or (name is not None and t[2].lower() == name))
        ]
        if not matches:
            raise WorkerError(
                f"zoom_to matched no element on this level: {text!r} "
                f"(use describe_model for names/ids)")
        if len(matches) > 1:
            where = ", ".join(f"{k}:{i} ({n})" for k, i, n, _ in matches)
            raise WorkerError(
                f"zoom_to {text!r} is ambiguous ({where}); use a "
                f"'Kind:id' selector")
        items.append(matches[0][3])
    return items


def _zoom_rect(view: Any, selectors: list[Any], margin: float) -> Any:
    """Bounding rect (scene units) around the named/id'd elements, padded
    by `margin`.  Raises when a selector matches nothing or is ambiguous,
    so a caller is never silently handed a whole-model render instead of a
    close-up."""
    from PySide6.QtCore import QRectF

    box = QRectF()
    for item in _resolve_zoom_items(view, selectors):
        box = box.united(item.sceneBoundingRect())
    return box.adjusted(-margin, -margin, margin, margin)


def _count_elements_in_rect(view: Any, rect: Any) -> int:
    """Elements of the displayed level whose scene rect intersects *rect*,
    so a zoomed render reports what it actually shows."""
    n = 0
    for attr in ("_block_items", "_node_items", "_event_items",
                 "_hier_items"):
        for item in getattr(view, attr, {}).values():
            if rect.intersects(item.sceneBoundingRect()):
                n += 1
    return n


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    model = job.get("model")
    out = job.get("out")
    if not model or not out:
        raise WorkerError("job must name both 'model' and 'out'")
    model_path = Path(str(model))
    if not model_path.is_file():
        raise WorkerError(f"model file not found: {model_path}")
    out_path = Path(str(out))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    level = int(job.get("level", 0) or 0)
    theme = str(job.get("theme", "light") or "light").lower()
    if theme not in ("light", "dark"):
        raise WorkerError(f"unknown theme: {theme!r} (expected light/dark)")
    max_px = int(job.get("max_px", 1600) or 1600)
    from raptor.api.limits import MAX_RENDER_PX
    if max_px < 16:
        raise WorkerError(f"max_px too small: {max_px}")
    if max_px > MAX_RENDER_PX:
        raise WorkerError(
            f"max_px too large: {max_px}; the limit is {MAX_RENDER_PX}")
    include_annotations = bool(job.get("include_annotations", True))
    highlight = job.get("highlight") or []
    if isinstance(highlight, str):
        highlight = [s.strip() for s in highlight.split(",") if s.strip()]
    if not isinstance(highlight, list):
        raise WorkerError("'highlight' must be a list of names/ids")
    zoom_to = job.get("zoom_to") or []
    if isinstance(zoom_to, str):
        zoom_to = [s.strip() for s in zoom_to.split(",") if s.strip()]
    if not isinstance(zoom_to, list):
        raise WorkerError("'zoom_to' must be a list of names/ids")
    try:
        margin = float(job.get("margin", 12.0))
    except (TypeError, ValueError):
        raise WorkerError("'margin' must be a number") from None
    if not math.isfinite(margin) or margin < 0:
        raise WorkerError(f"'margin' must be a finite value >= 0, got {margin}")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    _stub_modals()

    from raptor.gui.resources.design_tokens import ThemeManager
    ThemeManager.instance().set_dark(theme == "dark")

    # Existing loaders, chosen by extension exactly like
    # MainWindow._open_model_any.
    if str(model_path).lower().endswith(".rbds"):
        from raptor.core.fileio.rbds import load_rbds
        rbd = load_rbds(str(model_path))
    else:
        from raptor.core.fileio.rbd import load_rbd
        rbd = load_rbd(str(model_path))

    from raptor.gui.ui.diagram import DiagramView

    view = DiagramView()
    # Renders always carry labels: disable semantic zoom (the on-screen
    # level-of-detail) before the scene is built, so a big model rendered
    # at a small fit scale is not exported with its text hidden.
    view.set_lod_enabled(False)
    view.load_rbd(rbd)
    app.processEvents()

    if level:
        if level not in view.hiers:
            raise WorkerError(
                f"hierarchy level {level} not found in {model_path.name} "
                f"(available: {sorted(view.hiers)})")
        view.enter_level(int(level))
        app.processEvents()

    # Render the model's stored coordinates.  Layout is owned by the core
    # `auto_layout` MCP tool and `build_model` (both go through
    # raptor.core.fileio.legacy_layout.layout_container_positions); the
    # render worker must never re-lay-out, so authored x/y survive.

    annotations_drawn = len(getattr(view, "_anno_items", {}))
    if not include_annotations:
        for item in getattr(view, "_anno_items", {}).values():
            item.setVisible(False)
        annotations_drawn = 0

    if zoom_to:
        rect = _zoom_rect(view, list(zoom_to), margin)
    else:
        rect = _content_rect(view)
    elements_in_view = _count_elements_in_rect(view, rect)
    matches = _resolve_highlights(view, list(highlight))

    pm = _render_pixmap(view, rect)
    # Downscale to the long-edge cap.  Never upscale: a small content
    # rect is already legible at the supersampled size.
    if max(pm.width(), pm.height()) > max_px:
        from PySide6.QtCore import Qt
        pm = pm.scaled(max_px, max_px, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)

    _draw_highlights(pm, rect, matches)

    # Legibility: how many device pixels one scene unit becomes in the
    # final image.  The smallest labels are ~8 scene units tall, so
    # `effective_label_px` is the height a block label ends up at, and
    # `legible` is a heuristic floor (below ~7 px text is unreadable).
    px_per_unit = pm.width() / rect.width() if rect.width() > 0 else 0.0
    effective_label_px = round(8.0 * px_per_unit, 1)
    # `legible` derives from the SAME rounded value the caller sees, so the
    # pair can never disagree (e.g. 7.0 reported with legible=False).
    legible = effective_label_px >= 7.0

    if pm.isNull() or not pm.save(str(out_path), "PNG"):
        raise WorkerError(f"could not write PNG: {out_path}")

    return {
        "png_path": str(out_path),
        "width": int(pm.width()),
        "height": int(pm.height()),
        "elements_drawn": int(_count_elements(view)),
        "elements_in_view": int(elements_in_view),
        "annotations_drawn": int(annotations_drawn),
        "highlighted": len(matches),
        "level": int(level),
        "theme": theme,
        "max_px": int(max_px),
        "zoom_to": [str(z) for z in zoom_to],
        "px_per_unit": round(px_per_unit, 4),
        "effective_label_px": effective_label_px,
        "legible": bool(legible),
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        job = _read_job(argv)
        meta = run_job(job)
    except WorkerError as exc:
        print(f"render worker error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 -- never leak a traceback
        import traceback
        print(f"render worker failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(meta))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())