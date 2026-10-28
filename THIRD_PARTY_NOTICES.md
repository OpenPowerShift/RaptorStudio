# Third-party notices

RAPTOR Studio itself is licensed as described in `NOTICE.md` (Booz Allen Public
License v1.0 for the RAPTOR-derived portions, Apache License 2.0 for new code).

The Python packages (`pip install raptor-studio`) depend on, but do not contain,
the components below. The Windows, macOS and Linux installers **bundle** them;
each installer carries the licence texts the components ship with in the
`third_party_licenses/` folder next to this file, and the GNU LGPL 2.1, LGPL 3.0 and GPL 3.0
texts in `third_party_licenses/GNU/` (the Qt for Python wheels carry no licence file).

| Component | Licence | Used for | Source |
|---|---|---|---|
| Qt 6 and Qt for Python (PySide6, shiboken6) | LGPL-3.0-only | The application window, diagram and in-app help (Qt WebEngine) | https://download.qt.io/official_releases/ and https://code.qt.io/cgit/pyside/pyside-setup.git |
| Qt WebEngine (Chromium) | LGPL-3.0-only; Chromium's own components under their listed licences | The in-app help viewer | https://code.qt.io/cgit/qt/qtwebengine.git |
| WeasyPrint | BSD-3-Clause | PDF export and printing | https://github.com/Kozea/WeasyPrint |
| pydyf, tinycss2, cssselect2, webencodings | BSD-3-Clause | WeasyPrint dependencies | https://github.com/Kozea |
| tinyhtml5 | MIT | WeasyPrint dependency | https://github.com/CourtBouillon/tinyhtml5 |
| Pyphen | LGPL-2.1-or-later (tri-licensed GPL/LGPL/MPL; used under LGPL) | WeasyPrint hyphenation | https://github.com/Kozea/Pyphen |
| fontTools | MIT | WeasyPrint font subsetting | https://github.com/fonttools/fonttools |
| Pillow | MIT-CMU | WeasyPrint images | https://github.com/python-pillow/Pillow |
| cffi, pycparser | MIT-0, BSD-3-Clause | WeasyPrint's binding to Pango | https://github.com/python-cffi/cffi |
| MathJax | Apache-2.0 | Offline typesetting of the formulas page (`docs/js/mathjax/LICENSE`) | https://github.com/mathjax/MathJax |
| GTK 3 runtime for Windows: GLib, Pango, Cairo, HarfBuzz, FreeType, Fontconfig and their dependencies | LGPL-2.1-or-later (GLib, Pango, GTK), LGPL-2.1 or MPL-1.1 (Cairo), MIT (HarfBuzz, Fontconfig), FreeType License | WeasyPrint's text layout, **Windows installer only** | https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer |
| Python | PSF-2.0 | The runtime inside the installers | https://www.python.org |
| PyInstaller bootloader | GPL-2.0-or-later with the PyInstaller bootloader exception | Starts the installed application | https://github.com/pyinstaller/pyinstaller |

## LGPL components

Qt, PySide6, the GTK runtime libraries and Pyphen are used under the GNU Lesser
General Public License. The installers keep them as separate, unmodified shared
libraries and Python modules inside the installation folder (PyInstaller's
"onedir" layout), so you may replace them with your own builds of the same
interface version. Their complete source code is available from the links
above; on request, OpenPowerShift will also provide the exact source of any
LGPL component in a release it distributed (info@openpowershift.com).
