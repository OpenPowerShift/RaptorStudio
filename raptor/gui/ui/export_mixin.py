"""Exports from the main window: PDF, workbook, SVG and bitmap, clipboard copy.

Expects on `self`: the window's docks, `_rbd` and `_statusbar`."""
from __future__ import annotations


from raptor.gui.ui import msgbox
import os
from typing import TYPE_CHECKING, Any
from pathlib import Path
from PySide6.QtCore import (QSize, Qt)
from PySide6.QtGui import (QPainter, QPixmap)
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QApplication, QFileDialog,
)

from raptor.gui.ui import filedialogs as fdg




if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow as _Base
else:
    _Base = object


class ExportMixin(_Base):
    """Exports from the main window: PDF, workbook, SVG and bitmap, clipboard copy."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        _export_in_progress: Any
        def __getattr__(self, name: str) -> Any:
            """State owned by the view; typed loosely here."""
            ...

    def _export_pdf(self):
        if self._rbd is None:
            return
        from raptor.gui.ui.printing import _prompt_export_options
        # The report (`report_pdf`, laid out by `raptor.api.report_html`);
        # Print sends the same report to the printer. Same section picker.
        from raptor.gui.ui.report_pdf import export_pdf
        opts = _prompt_export_options(self, "Export PDF")
        if opts is None:
            return
        default = (Path(self._current_file).stem + ".pdf"
                   if self._current_file else "report.pdf")
        path, _ = fdg.getSaveFileName(
            self, "Export PDF", default, "PDF files (*.pdf)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._export_in_progress = True
        try:
            self._diagram_dock.view.write_back_to_rbd(self._rbd)
            export_pdf(self, path, opts)
        except Exception as e:
            # Export drives weasyprint + file I/O; without this
            # handler a failure produced no dialog and, crossing the Qt
            # slot boundary, could take the process (and the unsaved
            # model) down with no trace.
            msgbox.critical(
                self, "Export PDF Failed",
                f"Could not export the PDF:\n\n{e}")
            self._statusbar.showMessage("PDF export failed", 8000)
            return
        finally:
            self._export_in_progress = False
            QApplication.restoreOverrideCursor()
        self._statusbar.showMessage(f"PDF saved: {Path(path).name}",
                                    6000)

    def _export_tables(self):
        """File > Export > Export Tables...: the model's tables and a run's
        result tables as CSV, text or a spreadsheet."""
        from raptor.gui.ui.tables_dialog import open_export_tables_dialog
        open_export_tables_dialog(self)

    def _export_workbook_xlsx(self):
        """File > Export Excel Workbook...: one .xlsx of the default tables."""
        self._export_workbook(".xlsx")

    def _export_workbook_ods(self):
        """File > Export OpenDocument Spreadsheet...: the same as .ods."""
        self._export_workbook(".ods")

    def _export_workbook(self, ext: str):
        """The model's tables and the latest run's result tables (the Export
        Tables dialog's default choice) as ONE workbook, after a Save dialog.
        The file-type list offers both types, starting on `ext`."""
        if self._rbd is None:
            return
        from raptor.api.table_export import safe_stem, write_tables_workbook
        from raptor.api.tables import catalogue, collect
        from raptor.gui.ui.tables_dialog import gather
        rbd, bundle, model_path, name = gather(self)
        keys = [i.key for i in catalogue(rbd, bundle)
                if i.default and i.available]
        start = (str(Path(model_path).parent) if model_path
                 else os.getcwd())
        default = os.path.join(start, f"{safe_stem(name)}_tables{ext}")
        path = fdg.ask_spreadsheet_path(
            self, "Export Excel Workbook" if ext == ".xlsx"
            else "Export OpenDocument Spreadsheet", default)
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = (self._results.run_info(rbd, model=name,
                                           file=model_path or "")
                    if bundle is not None else None)
            written = write_tables_workbook(collect(rbd, bundle, keys), path,
                                            model_name=name, info=info)
        except Exception as e:      # file I/O: never let it cross the slot
            msgbox.critical(self, "Export Failed",
                            f"Could not write the workbook:\n\n{e}")
            self._statusbar.showMessage("Workbook export failed", 8000)
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._statusbar.showMessage(f"Saved {written}", 8000)

    def _export_diagram_svg(self):
        """Export the CURRENTLY DISPLAYED hierarchy level as a scalable
        vector file -- reuses the exact same content-rect measurement
        and QPainter render call the PDF report uses for its diagram
        pages (`report_pdf.py`), just targeting a `QSvgGenerator` paint
        device instead of an image. No
        options dialog (unlike Print/Export PDF): a diagram export has
        exactly one sensible output, the view as it stands, no
        section-picker/title-block choices to make."""
        if self._rbd is None:
            return
        from PySide6.QtSvg import QSvgGenerator
        default = (Path(self._current_file).stem + ".svg"
                  if self._current_file else "diagram.svg")
        path, _ = fdg.getSaveFileName(
            self, "Export Diagram SVG", default, "SVG files (*.svg)")
        if not path:
            return
        v = self._diagram_dock.view
        # Measure the content rect live from the scene's own items: the
        # cached `_content_rect` is only refreshed on a full scene rebuild
        # and goes stale after ad hoc drags (its own docstring), which
        # would render exported elements off-centre inside the viewBox
        # and could crop links.
        content = QRectF()
        from raptor.gui.ui.diagram import _GridBackground
        for it in v.scene().items():
            if isinstance(it, _GridBackground):
                continue
            content = content.united(it.sceneBoundingRect())
        if content.isEmpty():
            content = getattr(v, "_content_rect", None) or v.sceneRect()
        src = content.adjusted(-30, -30, 30, 30)
        w = max(int(src.width()), 1)
        h = max(int(src.height()), 1)
        gen = QSvgGenerator()
        gen.setFileName(path)
        gen.setSize(QSize(w, h))
        gen.setViewBox(QRectF(0, 0, w, h))
        gen.setTitle(Path(self._current_file).stem
                    if self._current_file else "RAPTOR Studio diagram")
        painter = QPainter()
        if not painter.begin(gen):
            # Report the failure instead of silently producing no file.
            msgbox.critical(
                self, "Export Diagram Failed",
                f"Could not write the SVG to:\n\n{path}")
            self._statusbar.showMessage("Diagram SVG export failed", 8000)
            return
        try:
            # No grid: `QGraphicsScene.render` paints everything in the
            # scene and has no item filter, so the world-sized
            # `_GridBackground` -- an on-screen editing aid -- was
            # baked into every exported SVG.
            with v.labels_forced(), v.grid_hidden():
                v.scene().render(painter, QRectF(0, 0, w, h), QRectF(src),
                                 Qt.AspectRatioMode.IgnoreAspectRatio)
        except Exception as e:
            msgbox.critical(
                self, "Export Diagram Failed",
                f"Could not render the diagram:\n\n{e}")
            self._statusbar.showMessage("Diagram SVG export failed", 8000)
            return
        finally:
            painter.end()
        self._statusbar.showMessage(
            f"Diagram SVG saved: {Path(path).name}", 6000)

    def _render_scene_png(self, rect: QRectF, scale: int | None = None) \
            -> QPixmap:
        """Render `rect` of the diagram scene into a supersampled QPixmap.

        DELEGATED to `raptor.render.scene_pixmap`, which the headless
        render worker also calls. The two copies had diverged in three
        ways that all changed the output image, most importantly semantic
        zoom: this version forced labels, the worker's did not, so the same
        diagram could export with or without element labels depending on
        which door rendered it.

        The shared helper also applies the worker's edge cap, which this
        version lacked -- that cap is what stops a large `itemsBoundingRect()`
        (the canvas is infinite) from trying to allocate a multi-gigabyte
        pixmap.

        Background prefers the view's `backgroundBrush()`, which is how the
        grid and themed background are actually installed, falling back to
        the viewport palette."""
        from raptor.render.scene_pixmap import render_scene_to_pixmap
        return render_scene_to_pixmap(
            self._diagram_dock.view, rect,
            scale=(scale or self._BITMAP_SCALE))

    def _export_bitmap(self):
        if self._rbd is None:
            return
        dlg = QFileDialog(self, "Save Diagram as PNG")
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        fdg.theme_file_dialog(dlg)
        dlg.setNameFilter("PNG Images (*.png)")
        dlg.selectFile("diagram.png")
        if not dlg.exec() or not dlg.selectedFiles():
            return
        path = dlg.selectedFiles()[0]
        if not path:
            return
        # Full model rect (content), supersampled — never the
        # infinite-canvas world sceneRect (multi-GB pixmap -> OOM).
        view = self._diagram_dock.view
        rect = getattr(view, "_content_rect", None) or             QRectF(view._scene.itemsBoundingRect())
        pm = self._render_scene_png(QRectF(rect))
        if not pm.save(path, "PNG"):
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}")
            return
        self._statusbar.showMessage(
            f"Saved diagram bitmap ({pm.width()}×{pm.height()}) — {path}")

    def _copy_view_to_clipboard(self):
        """Copy the CURRENT VIEWPORT (what you see) to the clipboard as
        a high-resolution PNG."""
        if self._rbd is None:
            return
        view = self._diagram_dock.view
        source = view.mapToScene(view.viewport().rect()).boundingRect()
        pm = self._render_scene_png(source)
        QApplication.clipboard().setPixmap(pm)
        self._statusbar.showMessage(
            f"Copied view to clipboard ({pm.width()}×{pm.height()} px)")
