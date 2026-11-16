"""Theme-coloured images for stylesheet sub-controls.

Once a stylesheet styles a sub-control's box (`QSpinBox::up-button`,
`QComboBox::drop-down`, `QCheckBox::indicator`), Qt stops drawing the
glyph inside it: the spin arrows, the combo arrow, the tick and the radio
dot all vanished in the dark theme. The stylesheet has to supply each glyph
as an `image: url(...)`, so this module paints them in the theme's colours
and writes them as small PNGs the stylesheet can point at.

Files are keyed by colour, so a theme switch gets new files. They live in
one folder in the user's cache, named after a hash of this module, that every
launch reuses: a folder per process was removed only on a clean exit, so a
crash, a kill or a test run's hard exit left one behind each time (over two
thousand in one tester's /tmp). A change to the drawing code changes the hash
and so the folder.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF

#: Painted at 3x the size the stylesheet shows them, so they stay crisp on
#: a high-DPI screen (Qt scales an `image:` down to the sub-control).
_PX = 30
_dir: Path | None = None


def _folder() -> Path:
    global _dir
    if _dir is None:
        from PySide6.QtCore import QStandardPaths
        stamp = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:10]
        cache = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericCacheLocation)
        try:
            folder = Path(cache or tempfile.gettempdir()) / "raptor-studio" / f"qss-{stamp}"
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = Path(tempfile.mkdtemp(prefix="raptor-studio-qss-"))
        _dir = folder
    return _dir


def _write(name: str, colour: str, draw, w: int = _PX, h: int = _PX) -> str:
    path = _folder() / f"{name}-{colour.lstrip('#')}.png"
    if not path.exists():
        image = QImage(w, h, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw(p, QColor(colour), w, h)
        p.end()
        # Written aside and renamed, so two windows starting at once never
        # read a half-written image.
        part = path.with_name(f"{path.stem}.{os.getpid()}.part.png")
        image.save(str(part))
        os.replace(part, path)
    # Qt stylesheets want forward slashes, also on Windows.
    return path.as_posix()


def _chevron(up: bool):
    def draw(p: QPainter, c: QColor, w: int, h: int) -> None:
        pen = QPen(c, w * 0.13)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        y0, y1 = (h * 0.62, h * 0.36) if up else (h * 0.38, h * 0.64)
        p.drawPolyline(QPolygonF([QPointF(w * 0.22, y0), QPointF(w * 0.5, y1),
                                  QPointF(w * 0.78, y0)]))
    return draw


def _tick(p: QPainter, c: QColor, w: int, h: int) -> None:
    pen = QPen(c, w * 0.14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(w * 0.2, h * 0.52), QPointF(w * 0.42, h * 0.74),
                              QPointF(w * 0.8, h * 0.28)]))


def _dot(p: QPainter, c: QColor, w: int, h: int) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(w / 2, h / 2), w * 0.22, h * 0.22)


def indicator_urls(c: dict) -> dict[str, str]:
    """Paths of every glyph the stylesheet uses, painted for palette `c`.

    Returns {} if images cannot be written (no writable temp folder), in
    which case the stylesheet leaves the glyphs to the platform style."""
    try:
        return {
            "up": _write("up", c["text"], _chevron(True)),
            "down": _write("down", c["text"], _chevron(False)),
            "up_off": _write("up", c["muted"], _chevron(True)),
            "down_off": _write("down", c["muted"], _chevron(False)),
            # The tick and dot sit on the accent fill, a mid blue in both
            # themes, so they are white in both.
            "tick": _write("tick", "#FFFFFF", _tick),
            "dot": _write("dot", "#FFFFFF", _dot),
            "tick_off": _write("tick", c["muted"], _tick),
        }
    except OSError:
        return {}
