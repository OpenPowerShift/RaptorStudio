"""Build the wheel and use it from an empty directory (review I2): every data file the program reads must be inside it.

The package reads icons, schemas, help pages, man pages and examples at run time; a file that exists in the repository
but is not packaged only shows up on somebody else's machine. This builds the wheel (a few seconds), unpacks it, and
runs a model through the CLI with the repository off the path."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def unpacked(tmp_path_factory):
    import fcntl
    import tempfile
    out = tmp_path_factory.mktemp("wheel")
    # `pip wheel` builds inside the source tree (`build/`); parallel test workers must take turns.
    with open(Path(tempfile.gettempdir()) / "raptor_wheel_build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(out)],
                       check=True, capture_output=True, timeout=120)
    wheel = next(out.glob("*.whl"))
    dest = out / "site"
    with zipfile.ZipFile(wheel) as z:
        z.extractall(dest)
        names = set(z.namelist())
    return dest, names


def test_every_non_python_package_file_is_in_the_wheel(unpacked):
    _dest, names = unpacked
    missing = []
    for path in (ROOT / "raptor").rglob("*"):
        if (path.is_file() and path.suffix not in (".py", ".pyc") and "__pycache__" not in path.parts
                and not any(part.startswith(".") for part in path.relative_to(ROOT).parts)):
            if str(path.relative_to(ROOT)).replace("\\", "/") not in names:
                missing.append(str(path.relative_to(ROOT)))
    assert not missing, f"package data missing from the wheel: {missing[:10]}"


def test_the_help_and_man_pages_ship(unpacked):
    _dest, names = unpacked
    flat = "\n".join(names)
    for needle in ("docs/manual.html", "docs/formulas.html", "new_docs/book.html", "ops_asciidoctor.css",
                   "docs/js/mathjax/startup.js", "docs/js/mathjax/output/chtml/fonts/tex.js"):
        assert needle in flat, needle


def test_a_model_runs_from_the_unpacked_wheel(unpacked, tmp_path):
    dest, _names = unpacked
    model = ROOT / "tests" / "golden" / "TwoBlockRedundancy" / "input.rbd"
    code = (f"import sys; sys.path.insert(0, {str(dest)!r}); import raptor; "
            f"assert raptor.__file__.startswith({str(dest)!r}), raptor.__file__; "
            f"from raptor.cli.main import main; sys.exit(main(['run', {str(model)!r}, '-n', '2', '-f', 'json']))")
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert "Availability" in r.stdout
