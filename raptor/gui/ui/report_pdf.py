"""Export PDF and Print: gather the window's content for a report.

The layout lives in `raptor.api.report_html` (Qt-free). This module does
the parts that need the window: rendering each hierarchy level's diagram
and the current graph to images, and reading the result set, the event
log the user has filtered to, and the raw report text off the Results
dock. Print renders the same PDF and sends its pages to the printer, so a
printed report and an exported one are the same document.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from raptor.api.report_html import (
    Figure, ReportContent, TextBlock, build_html, write_pdf)
from raptor.api.tables import (
    RESULTS, Table, report_input_tables, result_tables)

#: Longest side of one rendered diagram image, in pixels: about 200 dpi
#: across a landscape A4 page.
DIAGRAM_PX = 2200
#: Widest stretch of diagram (scene units) shown across one landscape page
#: with its labels still readable (about 6 pt); a longer level is cut into
#: strips that are stacked down the page.
UNITS_PER_STRIP = 1400
#: Only a level at least this many times wider than high is cut into strips;
#: a squarer one is fitted to the page whole (cutting it would break its links).
STRIP_MIN_ASPECT = 6.0
#: Most strips one level is cut into automatically.
MAX_AUTO_TILES = 12
#: Each strip repeats this much of its neighbours (scene units), so a block on
#: a cut is whole in one of them.
STRIP_OVERLAP = 90


def hierarchy_levels(win) -> list[tuple[int, str]]:
    """[(hier_id, name), ...] for every level in the model, Home first
    (id 0), from the raw HIERARCHY_DATA rows. `view.hiers` is
    level-scoped, so it cannot list every level up front."""
    rbd = getattr(win, "_rbd", None)
    levels = [(0, "Home")]
    sec = rbd.sections.get("HIERARCHY_DATA") if rbd is not None else None
    if not sec:
        return levels
    from raptor.core.fileio.typed import hier_from_row
    for row in sec.rows:
        try:
            h = hier_from_row(row)
        except (ValueError, KeyError):
            continue
        if h.id:
            levels.append((h.id, h.name or f"Hierarchy {h.id}"))
    return levels


def _render_level(v, folder: Path, hid: int, tiles: int) -> list[str]:
    """One level's diagram as image file(s): one, or *tiles* strips side
    by side when it is to be printed across that many pages."""
    src = getattr(v, "_content_rect", None) or v.sceneRect()
    w, h = max(src.width(), 1.0), max(src.height(), 1.0)
    factor = min(3.0, DIAGRAM_PX * tiles / max(w, h))
    image = QImage(max(int(w * factor), 1), max(int(h * factor), 1),
                 QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(factor, factor)
    painter.translate(-src.left(), -src.top())
    with v.labels_forced(), v.grid_hidden():
        v.scene().render(painter, QRectF(src), QRectF(src))
    painter.end()
    names = []
    step = image.width() / tiles
    ov = int(STRIP_OVERLAP * factor) if tiles > 1 else 0
    for i in range(tiles):
        left = max(int(i * step) - ov, 0)
        right = min(int((i + 1) * step) + ov + 1, image.width())
        part = image if tiles == 1 else image.copy(left, 0, right - left,
                                               image.height())
        name = f"diagram_{hid}_{i + 1}.png"
        part.save(str(folder / name))
        names.append(name)
    return names


def _tiles_for(v, asked: int) -> int:
    """Strips for one level: what was asked for ("Pages across" is the
    minimum), or more when the level is a long chain whose labels would be
    too small across one page."""
    import math
    src = getattr(v, "_content_rect", None) or v.sceneRect()
    aspect = src.width() / max(src.height(), 1.0)
    auto = 1
    if aspect >= STRIP_MIN_ASPECT and src.width() > UNITS_PER_STRIP:
        auto = math.ceil(src.width() / UNITS_PER_STRIP)
    return max(asked, min(auto, MAX_AUTO_TILES))


def render_diagrams(win, folder: Path, tiles: int = 1) -> list[Figure]:
    """Every hierarchy level's diagram, Home first. The view's own level
    is restored afterwards."""
    v = win._diagram_dock.view
    original = v._current_parent
    app = QApplication.instance()
    figs: list[Figure] = []
    tiles = max(1, int(tiles or 1))
    try:
        for hid, name in hierarchy_levels(win):
            v._set_level(hid)
            if app is not None:
                app.processEvents()
            files = _render_level(v, folder, hid, _tiles_for(v, tiles))
            for i, f in enumerate(files):
                figs.append(Figure(name, f, part=i + 1, of=len(files)))
    finally:
        v._set_level(original)
        if app is not None:
            app.processEvents()
    return figs


def _render_graph(win, folder: Path) -> list[Figure]:
    """The plot on the Graphs tab, drawn at a fixed page-friendly size
    rather than whatever size the dock happens to be."""
    plot = getattr(win._results, "_ao_plot", None)
    if plot is None:
        return []
    old = plot.size()
    try:
        plot.resize(1800, 800)
        QApplication.processEvents()
        pm = plot.grab()
    finally:
        plot.resize(old)
    if pm.isNull() or pm.width() <= 1:
        return []
    pm.save(str(folder / "graph.png"))
    mode = getattr(win._results, "_graph_mode", None)
    title = mode.currentText() if mode is not None else "Graph"
    return [Figure(title, "graph.png")]


def _event_log(win) -> Table | None:
    headers, rows = win._results.event_log_table()
    if not rows:
        return None
    return Table("results.event_log", "Event log", RESULTS, list(headers),
                 [list(r) for r in rows],
                 text_cols=set(range(1, len(headers))))


def _raw_blocks(win) -> list[TextBlock]:
    from raptor.gui.ui.printing import collect_raw_report_sections
    where = ("The whole report: Results ▸ Raw ▸ Save, or File ▸ Export "
             "▸ Export Tables.")
    return [TextBlock(title.replace("Raw Report — ", ""), lines, where)
            for title, lines in collect_raw_report_sections(win)]


def _add_results(content: ReportContent, win, opts: dict,
                 folder: Path) -> None:
    """The parts of the report that need a run."""
    res = win._results
    bundle = res.results_bundle(model_name=content.title,
                                units=getattr(res, "_units", ""))
    tables = result_tables(bundle) if bundle is not None else []
    content.results = [
        t for t in tables if t.section and (
            opts.get("weak") if t.section == "Weak link"
            else opts.get("results"))]
    content.notes = list(getattr(res, "_notes", []) or [])
    if opts.get("results"):
        content.per_run = next((t for t in tables
                                if t.key == "results.per_run"), None)
    if opts.get("weak"):
        content.want_weak_link = True
        content.weak_link_note = (
            "Weak link analysis was off for these results. Turn it on in "
            "the Run panel's Analysis section and run again to see each "
            "element's own measures.")
    if opts.get("graphs"):
        content.graphs = _render_graph(win, folder)
    if opts.get("event_log"):
        content.event_log = _event_log(win)
    if opts.get("raw"):
        content.raw = _raw_blocks(win)


def gather(win, opts: dict, folder: Path) -> ReportContent:
    """The report the options ask for, with images written to *folder*."""
    name = opts.get("name") or "RAPTOR Studio report"
    content = ReportContent(
        title=name, revision=opts.get("revision", ""),
        date=opts.get("date", ""),
        orientation=opts.get("orientation", "portrait"))
    file = getattr(win, "_current_file", "") or ""
    if opts.get("diagram"):
        content.diagrams = render_diagrams(win, folder,
                                           opts.get("diagram_tiles", 1))
    if opts.get("data_tables") and win._rbd is not None:
        content.inputs = report_input_tables(win._rbd)
    if win._results.has_results():
        content.info = win._results.run_info(
            win._rbd, model=Path(file).stem or name, file=file)
        _add_results(content, win, opts, folder)
    return content


def export_pdf(win, path: str, opts: dict) -> None:
    """File ▸ Export PDF."""
    with tempfile.TemporaryDirectory(prefix="raptor_report_") as td:
        folder = Path(td)
        write_pdf(build_html(gather(win, opts, folder)), path, folder)


def print_report(win, printer, opts: dict) -> None:
    """Print: the same PDF, page by page, onto *printer*. Each sheet takes
    its page's orientation (a wide table is printed landscape)."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QPageLayout
    from PySide6.QtPdf import QPdfDocument
    with tempfile.TemporaryDirectory(prefix="raptor_print_") as td:
        path = Path(td) / "report.pdf"
        export_pdf(win, str(path), opts)
        doc = QPdfDocument()
        doc.load(str(path))

        def orient(page: int) -> None:
            points = doc.pagePointSize(page)
            printer.setPageOrientation(
                QPageLayout.Orientation.Landscape if points.width() > points.height()
                else QPageLayout.Orientation.Portrait)

        if doc.pageCount():
            orient(0)
        painter = QPainter()
        if not painter.begin(printer):
            doc.close()
            return
        try:
            dpi = printer.resolution()
            for page in range(doc.pageCount()):
                if page:
                    orient(page)
                    printer.newPage()
                points = doc.pagePointSize(page)
                image = doc.render(page, QSize(int(points.width() / 72 * dpi),
                                               int(points.height() / 72 * dpi)))
                target = printer.pageRect(printer.Unit.DevicePixel)
                fit = image.size().scaled(
                    int(target.width()), int(target.height()),
                    Qt.AspectRatioMode.KeepAspectRatio)
                painter.drawImage(QRectF(0, 0, fit.width(), fit.height()),
                                  image)
        finally:
            painter.end()
            doc.close()
