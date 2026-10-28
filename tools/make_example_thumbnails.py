"""Render the File view's example-model thumbnails (light and dark).

    python tools/make_example_thumbnails.py

Run after changing a model in `new_docs/models/` or the list in
`raptor/gui/examples.py`; `tests/unit/test_backstage.py` fails if one is
missing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from raptor.api.render import render_diagram  # noqa: E402
from raptor.gui.examples import (  # noqa: E402
    EXAMPLES, THUMB_DIR, TUTORIALS, thumbnail,
)

#: The card's picture, px: shown at about half this, so it stays sharp on
#: a high-DPI screen. Every thumbnail is padded to this one 16:9 frame (in
#: the diagram's own background colour) so the gallery cards line up.
FRAME_W, FRAME_H = 448, 252


def _frame(path: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    image = QImage(str(path))
    image = image.scaled(FRAME_W - 16, FRAME_H - 16,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    out = QImage(FRAME_W, FRAME_H, QImage.Format.Format_RGB32)
    out.fill(QColor(QImage(str(path)).pixel(1, 1)))
    p = QPainter(out)
    p.drawImage((FRAME_W - image.width()) // 2, (FRAME_H - image.height()) // 2, image)
    p.end()
    out.save(str(path))


def main() -> None:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    for ex in (*EXAMPLES, *TUTORIALS):
        model = ROOT / "new_docs" / "models" / ex.file
        for dark in (False, True):
            out = thumbnail(ex, dark)
            render_diagram(model, out, theme="dark" if dark else "light",
                           max_px=2 * FRAME_W, include_annotations=False)
            _frame(out)
            print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
