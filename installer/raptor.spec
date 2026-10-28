# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the RAPTOR Studio installers (all three OSes).
# Built from the repo root:  pyinstaller installer/raptor.spec --noconfirm
# Produces dist/raptor-studio/ (onedir bundle) plus, on macOS,
# dist/RAPTOR Studio.app.
import glob
import importlib.metadata
import os
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "new_docs" / "book.html"), "new_docs"),
    (str(ROOT / "new_docs" / "ops_asciidoctor.css"), "new_docs"),
    (str(ROOT / "new_docs" / "images"), "new_docs/images"),
    (str(ROOT / "new_docs" / "models"), "new_docs/models"),
    # The File view's example-model thumbnails.
    (str(ROOT / "raptor" / "gui" / "resources" / "examples"),
     "raptor/gui/resources/examples"),
    (str(ROOT / "docs" / "manual.html"), "docs"),
    (str(ROOT / "docs" / "formulas.html"), "docs"),
    (str(ROOT / "docs" / "KEYBOARD_SHORTCUTS.md"), "docs"),
    (str(ROOT / "docs" / "js"), "docs/js"),
    (str(ROOT / "docs" / "ops_asciidoctor.css"), "docs"),
    # Our licences and the third-party notices, at the bundle root.
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "LICENSES"), "LICENSES"),
    (str(ROOT / "NOTICE.md"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    # The GNU texts the LGPL components (Qt/PySide6 ship none in their wheels;
    # the Windows GTK runtime) are distributed under.
    (str(ROOT / "installer" / "licenses"), "licenses/GNU"),
]


def _bundled_licences(roots=("PySide6", "weasyprint")):
    """The licence files every bundled distribution ships in its metadata,
    as (source, licenses/<name>/...) datas: the dependency closure of
    `roots`, which is what the installers carry."""
    out, seen, todo = [], set(), list(roots)
    while todo:
        name = todo.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        for f in dist.files or ():
            if re.search(r"(licen[cs]e|copying|notice|authors)", f.name, re.I) \
                    and ".dist-info" in str(f):
                out.append((str(f.locate()), f"licenses/{dist.metadata['Name']}"))
        for req in dist.requires or ():
            if "extra ==" not in req:
                todo.append(re.split(r"[\s;<>=!\[~(]", req, maxsplit=1)[0])
    if not out:
        raise SystemExit("no dependency licence files found -- the installer "
                         "would ship without them")
    return out


datas += _bundled_licences()
binaries = []

# Windows only: WeasyPrint's PDF export needs the Pango/GLib stack that
# pip does not carry. The CI runner installs the GTK3 runtime via
# chocolatey; its DLLs are bundled into _gtk/ and registered by
# installer/entry.py at startup.
if sys.platform == "win32":
    gtk_dirs = [
        r"C:\Program Files\GTK3-Runtime Win64\bin",
        r"C:\Program Files (x86)\GTK3-Runtime Win64\bin",
    ]
    gtk_dlls = []
    for d in gtk_dirs:
        gtk_dlls += glob.glob(os.path.join(d, "*.dll"))
    if not gtk_dlls:
        raise SystemExit(
            "GTK3 runtime not found -- run: choco install gtk-runtime -y")
    binaries += [("_gtk/" + Path(p).name, p, "BINARY") for p in gtk_dlls]
    # The GTK runtime's own licence texts, when the installer provides them.
    for d in gtk_dirs:
        share = Path(d).parent / "share" / "licenses"
        if share.is_dir():
            datas.append((str(share), "licenses/gtk-runtime"))

a = Analysis(
    [str(ROOT / "installer" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["weasyprint"],
    # Qt modules the app does not use; keep QtWebEngine's deps
    # (QtQuick/QtQml/QtWebChannel/QtNetwork/QtPositioning/QtOpenGL).
    excludes=[
        "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
        "PySide6.QtBluetooth", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtGraphs",
        "PySide6.QtGraphsWidgets", "PySide6.QtHelp",
        "PySide6.QtHttpServer", "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets", "PySide6.QtNetworkAuth",
        # QtPdf stays: Print renders the PDF report's pages with it.
        "PySide6.QtNfc", "PySide6.QtOpcUa",
        "PySide6.QtPdfWidgets", "PySide6.QtQuick3D",
        "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
        "PySide6.QtSerialBus", "PySide6.QtSerialPort",
        "PySide6.QtSpatialAudio", "PySide6.QtSql",
        "PySide6.QtStateMachine", "PySide6.QtTest",
        "PySide6.QtTextToSpeech", "PySide6.QtUiTools",
        "PySide6.QtVirtualKeyboard", "PySide6.QtWebSockets",
        "PySide6.QtXml",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="raptor-studio",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="raptor-studio",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="RAPTOR Studio.app",
        bundle_identifier="org.openpowershift.raptor-studio",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleDisplayName": "RAPTOR Studio",
            "LSMinimumSystemVersion": "13.0",
        },
    )
