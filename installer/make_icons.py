"""Regenerate installer/raptor-studio.ico (Windows exe + document icon) and
installer/raptor-studio.icns (macOS app bundle) from the app icon PNG.

    python installer/make_icons.py
"""
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "raptor" / "gui" / "resources" / "raptor_icon.png"


def _square(img: Image.Image) -> Image.Image:
    side = max(img.size)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return out


def main() -> None:
    img = _square(Image.open(SRC).convert("RGBA"))
    img.save(HERE / "raptor-studio.ico",
             sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    img.resize((1024, 1024), Image.LANCZOS).save(HERE / "raptor-studio.icns")


if __name__ == "__main__":
    main()
