"""Render a diagram scene region to a QPixmap -- the one implementation.

"Export PNG", "Copy to clipboard" and the headless render worker all need
the same thing: draw `rect` of a `DiagramView`'s scene onto a supersampled
pixmap. That was written twice -- `MainWindow._render_scene_png` and
`render/worker.py::_render_pixmap` -- and the two had already diverged in
three ways, all of which changed the output image:

1. **Semantic zoom.** The window wrapped the render in
   `view.labels_forced()`; the worker did not. So the same diagram exported
   through the worker could come out with **no element labels** whenever
   the view was zoomed far enough out to hide them. The worker's own
   docstring claimed "exactly as MainWindow._render_scene_png does it",
   which was not true.

2. **Background.** The worker preferred `view.backgroundBrush()`, which is
   how the grid/themed background is actually installed; the window went
   straight to the viewport palette. Different background for a themed
   scene.

3. **Scale cap.** The worker capped the supersampled edge at
   `_MAX_SUPERSAMPLED_EDGE` and downscaled a huge region; the window used a
   flat factor and would allocate a multi-gigabyte pixmap on a large
   infinite-canvas selection.

This module takes the *union*: semantic zoom is always forced (an exported
or printed image must always carry labels), the background brush wins when
present, and the edge cap always applies. The cap is a safety limit, not an
option -- it is what stops a large `itemsBoundingRect()` from OOM-ing, and
the window has a comment saying exactly that.

Qt lives here deliberately: both callers are Qt, so there is nothing to gain
by pushing this into `core/`.
"""
from __future__ import annotations

from typing import Any

#: Supersampling factor, matching `MainWindow._BITMAP_SCALE`.
SCALE = 4

#: Largest edge, in device pixels, that a single render may allocate.
#: A model's canvas is infinite, so `itemsBoundingRect()` on a model with
#: elements far apart can be enormous; without this cap the pixmap
#: allocation is an OOM.
MAX_SUPERSAMPLED_EDGE = 12000


def scale_for_rect(rect: Any, scale: float = SCALE) -> float:
    """The supersampling factor to use for *rect*.

    Returns *scale* unchanged unless that would produce an edge longer than
    `MAX_SUPERSAMPLED_EDGE`, in which case it shrinks just enough — but
    **never below 1.0**.

    That floor is inherited verbatim from the worker's original
    implementation, and it has a consequence worth stating plainly rather
    than discovering: for a region whose longest edge already *exceeds*
    `MAX_SUPERSAMPLED_EDGE` at 1:1, the ceiling is not met. A 50,000-unit
    region renders at 50,000 px, not 12,000. So this is a
    "do-not-supersample-below-1:1" rule, not a true ceiling on pixmap size.

    Making the ceiling Raptor 7 would mean letting the factor go below 1.0,
    which changes the output image size for every large render — a
    behaviour change that belongs in its own commit with the golden
    renders regenerated, not folded into a de-duplication. Recorded here
    so the constant's name is not taken as a promise it does not keep.
    """
    longest = max(rect.width(), rect.height())
    if longest * scale <= MAX_SUPERSAMPLED_EDGE:
        return scale
    return max(1.0, MAX_SUPERSAMPLED_EDGE / longest)


def background_colour(view: Any) -> Any:
    """The colour to fill behind a rendered scene.

    `backgroundBrush()` first: that is how `DiagramView` installs its grid
    and themed background, so it is the colour that actually matches what
    the user sees. The viewport palette is the fallback for a view with no
    brush set.
    """
    brush = view.backgroundBrush()
    if brush is not None:
        return brush.color()
    return view.viewport().palette().color(
        view.viewport().backgroundRole())


def render_scene_to_pixmap(view: Any, rect: Any,
                           scale: float | None = None) -> Any:
    """Render *rect* of *view*'s scene into a supersampled QPixmap.

    Semantic zoom is suspended for the duration: an exported or printed
    image must always carry element labels, even if the on-screen view is
    zoomed far enough out that they are hidden. The view's LOD state is
    restored on the way out (including if the render raises).
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter, QPixmap

    factor = scale_for_rect(rect, SCALE if scale is None else scale)
    with view.labels_forced():
        pm = QPixmap(max(1, int(rect.width() * factor)),
                     max(1, int(rect.height() * factor)))
        pm.fill(background_colour(view))
        painter = QPainter(pm)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            view._scene.render(painter, QRectF(pm.rect()), rect)
        finally:
            painter.end()
    return pm
