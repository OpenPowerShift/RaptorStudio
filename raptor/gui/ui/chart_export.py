"""Saved charts for reports and slides: SVG, PNG and PDF from one drawing.

Every saved chart is the SVG from `raptor.render.charts_svg` at a page
size (`PAGES`): the PNG and the PDF are that SVG rendered by Qt, so all
three show the same layout, and none shortens a label.  The PDF stays
vector (zoom in as far as you like); the PNG is drawn at print resolution.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QMarginsF, QRectF, QSizeF
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtSvg import QSvgRenderer

from raptor.api.insight.charts import ChartSpec
from raptor.render.charts_svg import PAGES, render

#: The page sizes offered, in menu order: (key, label).
PAGE_CHOICES = (("screen", "Screen (760 × 400)"),
                ("slide", "Slide, 16:9"),
                ("a4", "A4 landscape"),
                ("a3", "A3 landscape"))

#: PNG resolution: dots per inch for the pages with a physical size.
PNG_DPI = 300
_PX_PER_MM = 96 / 25.4


def page_mm(page: str) -> tuple[float, float]:
    """A page's physical size in millimetres (screen: 96 px per inch)."""
    width, height, physical = PAGES[page]
    if physical is None:
        return width / _PX_PER_MM, height / _PX_PER_MM
    return float(physical[0].removesuffix("mm")), float(physical[1].removesuffix("mm"))


def svg_text(spec: ChartSpec, page: str) -> str:
    return render(spec, page=page)


def image(spec: ChartSpec, page: str, dpi: int = PNG_DPI) -> QImage:
    """The chart as an image at `dpi` on its page (white background)."""
    w_mm, h_mm = page_mm(page)
    w_px = max(1, round(w_mm / 25.4 * dpi))
    h_px = max(1, round(h_mm / 25.4 * dpi))
    picture = QImage(w_px, h_px, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#ffffff"))
    dots_per_metre = round(dpi / 0.0254)
    picture.setDotsPerMeterX(dots_per_metre)
    picture.setDotsPerMeterY(dots_per_metre)
    renderer = QSvgRenderer(QByteArray(svg_text(spec, page).encode("utf-8")))
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    renderer.render(painter, QRectF(0, 0, w_px, h_px))
    painter.end()
    return picture


def save_svg(spec: ChartSpec, path: str, page: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(svg_text(spec, page))


def save_png(spec: ChartSpec, path: str, page: str, dpi: int = PNG_DPI) -> bool:
    return image(spec, page, dpi).save(path)


def save_pdf(spec: ChartSpec, path: str, page: str) -> None:
    """A one-page vector PDF of the chart, the page the chart's size."""
    w_mm, h_mm = page_mm(page)
    writer = QPdfWriter(path)
    writer.setTitle(spec.title)
    writer.setCreator("RAPTOR Studio")
    writer.setPageLayout(QPageLayout(
        QPageSize(QSizeF(w_mm, h_mm), QPageSize.Unit.Millimeter, spec.title),
        QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
    writer.setResolution(PNG_DPI)
    renderer = QSvgRenderer(QByteArray(svg_text(spec, page).encode("utf-8")))
    painter = QPainter(writer)
    renderer.render(painter, QRectF(0, 0, writer.width(), writer.height()))
    painter.end()
