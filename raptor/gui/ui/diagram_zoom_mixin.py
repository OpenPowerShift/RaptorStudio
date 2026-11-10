"""Zoom, fit and level-of-detail on the canvas.

Expects on `self`: the scene and the view transform."""
from __future__ import annotations


from typing import Any

from PySide6.QtCore import (
    Qt, QRectF,
)

from contextlib import contextmanager
from raptor.gui.ui.diagram_items import (
    LOD_LABELS_HYSTERESIS,
    LOD_LABELS_MIN_SCALE,
    ZOOM_MAX,
    ZOOM_MIN,
    _GridBackground,
    _HierItem,
)


#: Everything arrow-key nudge knows how to move. Arrow annotations are
#: included even though they are not `ItemIsMovable` (their endpoints are
#: independent); the Alt+Up enablement/handler must use this same predicate
#: or an arrow-only selection silently ascends instead of nudging.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QGraphicsView as _Base
else:
    _Base = object


class ZoomMixin(_Base):
    """Zoom, fit and level-of-detail on the canvas."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _deferred_sublabels: Any
        _lod_enabled: Any
        _lod_labels_visible: Any
        _lod_mode: Any
        _user_zoomed: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def zoom_by(self, factor: float):
        """Multiplicative zoom, clamped only at the extreme ends."""
        current = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, current * factor))
        self.scale(target / current, target / current)
        self._user_zoomed = True
        self._apply_lod()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Only auto-fit while the user hasn't taken over the zoom. Fits
        # the content rect, never the world (the scene rect extends far
        # beyond the model for panning).
        if not self._user_zoomed and getattr(self, "_content_rect", None):
            self.fitInView(self._content_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_lod()

    def zoom_percent(self) -> int:
        return round(self.transform().m11() * 100)

    def set_zoom_percent(self, percent: float) -> bool:
        """Absolute zoom from the Zoom Percent dialog. Returns False
        (unchanged) when the value is outside the clamp."""
        current = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, percent / 100.0))
        if abs(target - current) < 1e-9:
            return False
        self.scale(target / current, target / current)
        self._user_zoomed = True
        self._apply_lod()
        return True

    def reset_zoom(self):
        self._user_zoomed = False
        if getattr(self, "_content_rect", None):
            self.fitInView(self._content_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_lod()

    def _apply_lod(self):
        """Semantic zoom for element text. Two modes, by scale:

        * **near** (>= `LOD_LABELS_MIN_SCALE`, 25%): everything as drawn.
        * **off**: all text hidden -- shapes, links and badges remain, so
          the model reads as a clean miniature.

        There is no middle "counter-scaled name" mode: holding a name at
        its 100%-zoom on-screen size meant ×5/×10 labels that covered the
        topology they were meant to annotate. Below a quarter scale the
        miniature is the honest view; zoom in to read names.

        View concern only: nothing is written to the model or the saved
        file. Idempotent -- only touches items when the state changes."""
        scale = self.transform().m11()
        prev = getattr(self, "_lod_mode", None)
        if getattr(self, "_lod_enabled", True):
            # Hysteresis so dragging a splitter / resizing at the
            # threshold does not flap the labels every pixel.
            edge = LOD_LABELS_MIN_SCALE + (
                LOD_LABELS_HYSTERESIS if prev == "off"
                else -LOD_LABELS_HYSTERESIS)
            mode = "near" if scale >= edge else "off"
        else:
            mode = "near"       # disabled: always show (renders/exports)
        forced = self._lod_labels_visible is None
        if not forced and mode == prev:
            return
        self._lod_mode = mode
        self._lod_labels_visible = (mode == "near")
        if mode == "near" and getattr(self, "_deferred_sublabels", False):
            self._deferred_sublabels = False
            self._refresh_block_sublabels()

        show_all = (mode == "near")
        for coll in (getattr(self, "_block_items", {}),
                     getattr(self, "_hier_items", {}),
                     getattr(self, "_node_items", {}),
                     getattr(self, "_event_items", {})):
            for item in coll.values():
                for attr in self._LOD_LABEL_ATTRS:
                    label = getattr(item, attr, None)
                    if label is not None and hasattr(label, "setVisible"):
                        label.setVisible(show_all)

    def set_lod_enabled(self, enabled: bool):
        """Turn semantic zoom on/off. Disabling forces element labels
        visible regardless of zoom, for exports and renders where a
        label-less image would be wrong."""
        self._lod_enabled = bool(enabled)
        self._lod_labels_visible = None
        self._apply_lod()

    def _refresh_lod(self):
        """Re-apply semantic zoom after items are added outside a full
        rebuild (insert/paste/undo-of-delete), where the cached state would
        otherwise leave a new item's labels visible while the rest are
        hidden (or vice versa) until the next zoom change."""
        self._lod_labels_visible = None
        self._apply_lod()

    @contextmanager
    def labels_forced(self):
        """Context manager: force every element label visible for the
        duration (then restore the previous LOD state). Use around ANY
        scene render that becomes an artefact -- bitmap, SVG, print, PDF,
        report -- so an export is never label-less just because the view
        happened to be zoomed out."""
        was = getattr(self, "_lod_enabled", True)
        if was:
            self.set_lod_enabled(False)
        try:
            yield
        finally:
            if was:
                self.set_lod_enabled(True)

    @contextmanager
    def grid_hidden(self):
        """Context manager: hide the world-spanning grid for the
        duration (then restore it). `QGraphicsScene.render` has no item
        filter, so a grid item left in the scene is painted into every
        artefact -- skipping it when *measuring* the content rect is not
        enough. Use around any export/print/report render whose output
        should carry only the model, not the editing aid."""
        grids = [it for it in self._scene.items()
                 if isinstance(it, _GridBackground)]
        was = [g.isVisible() for g in grids]
        for g in grids:
            g.setVisible(False)
        try:
            yield
        finally:
            for g, vis in zip(grids, was):
                g.setVisible(vis)

    def fit_items(self, items) -> bool:
        """Zoom/pan to fit the given scene items (plus a slim margin),
        then re-apply semantic zoom. Returns False when *items* is empty."""
        content = QRectF()
        for it in items:
            try:
                content = content.united(it.sceneBoundingRect())
            except RuntimeError:
                continue   # C++-deleted item still in a stale selection
        if content.isEmpty():
            return False
        self._user_zoomed = True
        self.fitInView(content.adjusted(-30, -30, 30, 30), Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_lod()
        return True

    def zoom_to_selection(self) -> int:
        """Zoom to fit the currently selected items. Returns how many."""
        items = list(self._scene.selectedItems())
        if not items:
            return 0
        self.fit_items(items)
        return len(items)

    def zoom_to_hierarchy(self) -> bool:
        """Descend into the selected hierarchy oval (and fit its level).
        Returns False when no hierarchy is selected."""
        for it in self._scene.selectedItems():
            if isinstance(it, _HierItem):
                self.enter_level(it._hier_id)
                # Truthful result: `enter_level` refuses at the depth cap
                # (or for the current level), so report the actual level.
                return self._current_parent == it._hier_id
        return False
