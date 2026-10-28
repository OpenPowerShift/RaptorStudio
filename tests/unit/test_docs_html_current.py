"""The committed HTML help is what `new_docs/tools/render_docs.sh` builds from the AsciiDoc (review G7).

Skipped where `asciidoctor` is not installed; the docs are rebuilt with `bash new_docs/tools/render_docs.sh`."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "new_docs" / "tools" / "ops_adoc_ext.rb"

pytestmark = pytest.mark.skipif(shutil.which("asciidoctor") is None, reason="asciidoctor is not installed")


@pytest.mark.parametrize("source,committed", [("new_docs/book.adoc", "new_docs/book.html"),
                                              ("docs/manual.adoc", "docs/manual.html"),
                                              ("docs/formulas.adoc", "docs/formulas.html")])
def test_committed_html_is_what_the_toolchain_builds(tmp_path, source, committed):
    out = tmp_path / "out.html"
    subprocess.run(["asciidoctor", "-r", str(EXT), "-a", "stylesheet=ops_asciidoctor.css", "-a", "linkcss",
                    "-a", "webfonts!", "-a", "font-awesome!", "-a", "reproducible", str(ROOT / source), "-o", str(out)],
                   check=True, cwd=ROOT, capture_output=True)
    if source == "docs/formulas.adoc":
        # render_docs.sh then swaps the CDN MathJax for the vendored copy so the formulas show offline.
        subprocess.run([sys.executable, str(ROOT / "new_docs" / "tools" / "localise_mathjax.py"), str(out)],
                       check=True, cwd=ROOT, capture_output=True)
    assert out.read_text() == (ROOT / committed).read_text(), (
        f"{committed} is stale: run `bash new_docs/tools/render_docs.sh` and commit the result")
