"""Render the File view's example-model thumbnails (light and dark).

    python tools/make_example_thumbnails.py
    python tools/make_example_thumbnails.py --check   # render to temp, compare

Run after changing a model in `new_docs/models/` or the list in
`raptor/gui/examples.py`; `tests/unit/test_backstage.py` fails if one is
missing, and `--check` (wired into `tests/unit/test_example_artifacts.py`)
fails if a committed thumbnail is stale.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
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


def _render(ex, dark: bool, out: Path) -> None:
    model = ROOT / "new_docs" / "models" / ex.file
    render_diagram(model, out, theme="dark" if dark else "light",
                   max_px=2 * FRAME_W, include_annotations=False)
    _frame(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="render each card to a temporary file and compare with the "
             "committed PNG instead of overwriting it")
    args = parser.parse_args(argv)

    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    tmp = tempfile.TemporaryDirectory(prefix="raptor-thumbnails-")
    try:
        out_dir = Path(tmp.name)
        for ex in (*EXAMPLES, *TUTORIALS):
            for dark in (False, True):
                if args.check:
                    out = out_dir / thumbnail(ex, dark).name
                else:
                    out = thumbnail(ex, dark)
                _render(ex, dark, out)
                if args.check:
                    committed = thumbnail(ex, dark)
                    if (not committed.is_file()
                            or committed.read_bytes() != out.read_bytes()):
                        stale.append(committed.name)
                else:
                    print("wrote", out.relative_to(ROOT))
    finally:
        tmp.cleanup()

    if args.check:
        if stale:
            print(f"make_example_thumbnails.py: {len(stale)} stale thumbnail(s): "
                  + ", ".join(stale), file=sys.stderr)
            return 1
        print("make_example_thumbnails.py: OK -- every committed thumbnail "
              "matches a fresh render.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
