#!/usr/bin/env python3
"""Point a rendered AsciiDoc page at the MathJax copy in docs/js instead of a CDN.

Asciidoctor, for `:stem: asciimath`, ends the page with a MathJax 2 configuration and a script tag that loads
MathJax from a content-delivery network. That fails offline (and the help viewer is offline). This replaces both with
`js/mathjax-config.js` and `js/mathjax/startup.js`, which ship with the docs (see docs/js/mathjax/LICENSE).

    localise_mathjax.py docs/formulas.html
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BLOCK = re.compile(r'<script type="text/x-mathjax-config">.*?</script>\s*<script src="https://[^"]*MathJax[^"]*"></script>',
                   re.S)
LOCAL = ('<script src="js/mathjax-config.js"></script>\n'
         '<script src="js/mathjax/startup.js" async></script>')


def localise(path: Path) -> bool:
    html = path.read_text(encoding="utf-8")
    new, n = BLOCK.subn(LOCAL, html)
    if n:
        path.write_text(new, encoding="utf-8")
    return bool(n)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(f"{arg}: {'localised' if localise(Path(arg)) else 'no MathJax block'}")
