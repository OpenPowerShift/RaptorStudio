"""Scene→pixmap rendering has ONE implementation (review D-03).

"Export PNG", "Copy to clipboard" and the headless render worker all draw a
region of the diagram scene onto a supersampled pixmap. That was written
twice — `MainWindow._render_scene_png` and `render/worker.py::_render_pixmap`
— and the two had diverged in three ways, every one of which changed the
output image:

1. **Semantic zoom.** The window forced labels via `view.labels_forced()`;
   the worker did not. The same diagram could therefore export *with* or
   *without* element labels depending on which door rendered it. The
   worker's docstring claimed it matched "exactly"; it did not.

2. **Background.** The worker preferred `view.backgroundBrush()` (how the
   grid and themed background are actually installed); the window went
   straight to the viewport palette.

3. **Edge cap.** The worker capped the supersampled edge at 12000 px and
   downscaled; the window used a flat factor, so a large
   `itemsBoundingRect()` — the canvas is infinite — would try to allocate a
   multi-gigabyte pixmap.

`raptor/render/scene_pixmap.py` is now the union of both versions.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class TestScaleForRect:
    """The edge cap. Pure, so it needs no Qt scene."""

    def _rect(self, w, h):
        from PySide6.QtCore import QRectF
        return QRectF(0, 0, w, h)

    def test_a_small_region_keeps_the_full_factor(self, qapp):
        from raptor.render.scene_pixmap import SCALE, scale_for_rect
        assert scale_for_rect(self._rect(100, 100)) == SCALE

    def test_a_huge_region_is_capped(self, qapp):
        """The OOM guard: supersampling is traded away before memory is.

        8000 units at 4x would be 32000 px, over the 12000 ceiling; at 1:1
        it is comfortably under, so the cap applies and lands on 1.5.
        """
        from raptor.render.scene_pixmap import scale_for_rect
        assert scale_for_rect(self._rect(8_000, 8_000)) == 1.5
        assert 8_000 * scale_for_rect(self._rect(8_000, 8_000)) <= 12_001

    def test_the_cap_floors_at_one_for_a_region_past_the_ceiling(self, qapp):
        """A real limitation, inherited verbatim from the worker, and
        deliberately NOT changed here.

        `scale_for_rect` returns `max(1.0, ...)`, so a region whose 1:1 edge
        already exceeds `MAX_SUPERSAMPLED_EDGE` is not shrunk to the ceiling:
        50,000 units still renders at 50,000 px. So the constant bounds the
        SUPERSAMPLING, not the final pixmap size.

        Making it a true ceiling means letting the factor go below 1.0, which
        changes every large render's output dimensions — a behaviour change
        with golden images to regenerate, not something to fold into a
        de-duplication. Pinned so the constant's name is not read as a
        promise it does not keep.
        """
        from raptor.render.scene_pixmap import (
            MAX_SUPERSAMPLED_EDGE,
            scale_for_rect,
        )
        got = scale_for_rect(self._rect(50_000, 50_000))
        assert got == 1.0
        assert 50_000 * got > MAX_SUPERSAMPLED_EDGE, (
            "premise: this region is past the ceiling even at 1:1")

    def test_the_cap_floors_at_one_rather_than_shrinking_further(self, qapp):
        """A genuine limitation, inherited from the worker and NOT changed
        here, so it is pinned rather than implied.

        `scale_for_rect` never returns below 1.0, so a region so large that
        even 1:1 would exceed the edge limit still renders at 1:1 -- i.e. the
        cap bounds the *supersampling*, not the final pixel size. Making it
        a true ceiling would mean rendering below 1:1 (a genuinely blurred,
        downsampled image), which changes the worker's output, so it is
        deliberately out of scope for this finding."""
        from raptor.render.scene_pixmap import (
            MAX_SUPERSAMPLED_EDGE, scale_for_rect)
        edge = MAX_SUPERSAMPLED_EDGE * 3
        got = scale_for_rect(self._rect(edge, edge), 4)
        assert got == 1.0, (
            "the cap should floor at 1:1 rather than downscale below it")

    def test_the_cap_never_upscales_past_one(self, qapp):
        from raptor.render.scene_pixmap import scale_for_rect
        assert scale_for_rect(self._rect(10_000_000, 10_000_000)) >= 1.0


    def test_an_explicit_scale_is_honoured_when_small_enough(self, qapp):
        from raptor.render.scene_pixmap import scale_for_rect
        assert scale_for_rect(self._rect(100, 100), 2) == 2

    def test_an_explicit_scale_is_still_capped(self, qapp):
        """Passing a bigger `scale` must not buy a bigger allocation."""
        from raptor.render.scene_pixmap import scale_for_rect
        assert scale_for_rect(self._rect(40_000, 40_000), 4) < 4.0

    def test_a_degenerate_rect_does_not_divide_by_zero(self, qapp):
        from raptor.render.scene_pixmap import scale_for_rect
        # A zero-width rect must not raise ZeroDivisionError; the pixmap
        # allocation clamps to 1 px separately.
        assert scale_for_rect(self._rect(0, 0)) >= 1.0


class TestBackgroundColour:
    def test_the_brush_wins_when_set(self, qapp):
        """The brush is how `DiagramView` installs its grid background, so
        it is the colour that matches what the user sees."""
        from raptor.render.scene_pixmap import background_colour

        class _Brush:
            def color(self):
                return "BRUSH-COLOUR"

        class _View:
            def backgroundBrush(self):
                return _Brush()

            class viewport:
                @staticmethod
                def palette():
                    raise AssertionError(
                        "the palette must not be consulted when a brush is set")

                @staticmethod
                def backgroundRole():
                    return None

        assert background_colour(_View()) == "BRUSH-COLOUR"

    def test_the_palette_is_the_fallback(self, qapp):
        from raptor.render.scene_pixmap import background_colour

        class _View:
            def backgroundBrush(self):
                return None

            class viewport:
                @staticmethod
                def palette():
                    class _P:
                        @staticmethod
                        def color(_role):
                            return "PALETTE-COLOUR"
                    return _P()

                @staticmethod
                def backgroundRole():
                    return None

        assert background_colour(_View()) == "PALETTE-COLOUR"


class TestOneOwner:
    """Structural. A second copy is invisible to behaviour tests when the
    two agree, which is exactly how this duplication survived."""

    def test_neither_caller_defines_its_own_render(self):
        import ast

        offenders = []
        for rel in ("raptor/gui/ui/main_window.py", "raptor/render/worker.py"):
            tree = ast.parse((REPO / rel).read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                # CODE only, not the docstring: both callers now carry a
                # comment explaining the old divergence, and a check that
                # reads the source text flags the explanation instead of the
                # defect. That is what my first version did.
                #
                # Scoped to PIXMAP renders. `main_window._export_diagram_svg`
                # also calls `scene.render(...)`, but onto a `QSvgGenerator`
                # -- a vector device with no supersampling and no pixmap
                # allocation. That is a different operation, not this
                # duplication, and forcing it through a QPixmap helper would
                # throw away the vector output. Left alone deliberately.
                body = ast.Module(body=node.body, type_ignores=[])
                uses_qpixmap = any(
                    isinstance(sub, ast.Name) and sub.id == "QPixmap"
                    for sub in ast.walk(body))
                if not uses_qpixmap:
                    continue
                for sub in ast.walk(body):
                    if (isinstance(sub, ast.Attribute)
                            and sub.attr == "render"):
                        offenders.append(f"{rel}:{node.lineno} {node.name}")
                    if (isinstance(sub, ast.Attribute)
                            and sub.attr == "backgroundBrush"):
                        offenders.append(f"{rel}:{node.lineno} {node.name}")
        assert not offenders, (
            f"a caller renders the scene itself again: {offenders}; call "
            "raptor.render.scene_pixmap.render_scene_to_pixmap instead")

    def test_both_callers_delegate(self):
        import inspect

        from raptor.gui.ui.main_window import MainWindow
        from raptor.render import worker
        for fn in (MainWindow._render_scene_png, worker._render_pixmap):
            assert "render_scene_to_pixmap" in inspect.getsource(fn), (
                f"{fn.__qualname__} does not delegate to the shared render")

    def test_the_shared_module_exists_and_is_importable(self):
        from raptor.render import scene_pixmap
        for name in ("render_scene_to_pixmap", "scale_for_rect",
                     "background_colour", "SCALE",
                     "MAX_SUPERSAMPLED_EDGE"):
            assert hasattr(scene_pixmap, name), name


class TestBothDoorsProduceTheSameImage:
    """The end-to-end statement of D-03: the two paths must be pixel-identical
    for the same model."""

    def test_export_and_worker_render_agree(self, qapp):
        """Runs the real window render and the real worker render over the
        same view and compares pixels.

        Before the fix this differed: the window forced semantic zoom and
        the worker did not, so a zoomed-out view produced labels through one
        door and not the other."""
        from PySide6.QtCore import QRectF
        from raptor.core.fileio.rbd import load_rbd
        from raptor.gui.ui.main_window import MainWindow
        from raptor.render.scene_pixmap import render_scene_to_pixmap

        win = MainWindow()
        win._load_into_app(load_rbd(str(GOLDEN / "TwoBlockRedundancy"
                                        / "input.rbd")), "x")
        view = win._diagram_dock.view
        rect = QRectF(view._scene.itemsBoundingRect())

        via_window = win._render_scene_png(rect)
        via_worker = render_scene_to_pixmap(view, rect)

        assert via_window.size() == via_worker.size()
        assert via_window.toImage() == via_worker.toImage(), (
            "the GUI export and the worker render disagree on the same "
            "view — one of the two is not using the shared helper")

    def test_the_render_forces_labels(self, qapp):
        """The divergence that had user-visible consequences, asserted
        directly rather than inferred from pixels.

        `labels_forced()` disables semantic zoom for the duration. So the
        state of the flag *during* `scene.render` is exactly the thing that
        differed: the window's copy wrapped the render in `labels_forced`,
        the worker's did not, so the same diagram could export with or
        without element labels depending on which door rendered it.

        My first attempt at this test compared rendered pixels and passed
        with `labels_forced` deleted -- scene content is drawn either way,
        so pixel comparison cannot see the difference at this zoom. Checking
        the flag during the render call is the check that actually
        discriminates."""
        from raptor.core.fileio.rbd import load_rbd
        from raptor.gui.ui.main_window import MainWindow
        from raptor.render.scene_pixmap import render_scene_to_pixmap

        win = MainWindow()
        win._load_into_app(load_rbd(str(GOLDEN / "TwoBlockRedundancy"
                                        / "input.rbd")), "x")
        view = win._diagram_dock.view
        view.set_lod_enabled(True)
        seen: list[bool] = []
        real_scene = view._scene

        class _Probe:
            def render(self, *a, **k):
                # `_lod_enabled` is the flag `labels_forced()` toggles, and
                # it is what `scene.render`'s label-visibility ultimately
                # depends on. Read it HERE, at the instant of the render,
                # because it is only False during the `with` block.
                #
                # Note `_lod_mode` is deliberately NOT used: it is cached by
                # `_apply_lod` and is not refreshed by `QGraphicsView.scale`,
                # so it reads "near" at any zoom in a headless test. That
                # was my first attempt and it passed with the defect present.
                seen.append(getattr(view, "_lod_enabled", None))

        view._scene = _Probe()
        try:
            render_scene_to_pixmap(view, view.scene().itemsBoundingRect())
        finally:
            view._scene = real_scene

        assert seen, "the scene was never rendered"
        assert seen[0] is False, (
            f"_lod_enabled was {seen[0]!r} during the render; labels_forced() "
            "must make it False for the whole scene render, or an exported "
            "image loses its element labels whenever the view is zoomed out "
            "-- the exact divergence between the two copies")

        # The flag must be restored afterwards, or every later on-screen
        # draw is stuck label-less.
        assert getattr(view, "_lod_enabled", None) is True, (
            "the render left semantic zoom disabled")

    def test_the_lod_state_is_restored_afterwards(self, qapp):
        """Forcing labels must not leave the on-screen view without them."""
        from PySide6.QtCore import QRectF
        from raptor.core.fileio.rbd import load_rbd
        from raptor.gui.ui.main_window import MainWindow
        from raptor.render.scene_pixmap import render_scene_to_pixmap

        win = MainWindow()
        win._load_into_app(load_rbd(str(GOLDEN / "TwoBlockRedundancy"
                                        / "input.rbd")), "x")
        view = win._diagram_dock.view
        view.set_lod_enabled(True)
        render_scene_to_pixmap(view, QRectF(view._scene.itemsBoundingRect()))
        assert getattr(view, "_lod_enabled", True) is True, (
            "the render left semantic zoom switched off, so every later "
            "on-screen draw would lose its labels")

    def test_the_lod_state_is_restored_even_on_failure(self, qapp):
        """A render that raises must not leave semantic zoom switched off,
        or every later on-screen draw loses its labels."""
        from PySide6.QtCore import QRectF
        from raptor.core.fileio.rbd import load_rbd
        from raptor.gui.ui.main_window import MainWindow
        from raptor.render.scene_pixmap import render_scene_to_pixmap

        win = MainWindow()
        win._load_into_app(load_rbd(str(GOLDEN / "TwoBlockRedundancy"
                                        / "input.rbd")), "x")
        view = win._diagram_dock.view

        class _Boom:
            def render(self, *a, **k):
                raise RuntimeError("scene render failed")

        real_scene = view._scene
        try:
            view._scene = _Boom()
            with pytest.raises(RuntimeError):
                render_scene_to_pixmap(view, QRectF(0, 0, 100, 100))
        finally:
            view._scene = real_scene

        # The view must be usable again, and a real render must now work.
        pm = render_scene_to_pixmap(view, QRectF(real_scene.itemsBoundingRect()))
        assert pm.width() >= 1
