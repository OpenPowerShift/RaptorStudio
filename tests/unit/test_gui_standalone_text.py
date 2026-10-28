"""The GUI's own text stands on its own: no chapter or figure numbers, no pointers to the manual by page, and no
comparisons with "real" or "the original" RAPTOR. Credits and the licence notice are the exception, since they must
name the original project.

Scans every string literal under `raptor/gui` (docstrings excluded), so a tooltip, a dialog or a status message that
cites "Ch.17" or "real Raptor" fails here."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "raptor" / "gui"

PATTERN = re.compile(
    r"\bCh\.?\s?\d|\bch\d\d|\bChapter \d|real Raptor|\boriginal\b|\blegacy\b|\bAppendix [A-Z]\b|"
    r"\bFig(?:ure)?\.? ?\d|\bmanual (?:Ch|[A-Z]-?\d|\d)|\bManual Ch|\.mod\b", re.I)

#: (file, a fragment of the literal) -- the credit and licence text, which names the project it came from.
ALLOWED = (
    ("ui/main_window.py", "Credits."),
    ("ui/main_window.py", "Licence."),
)


def _literals(path: Path):
    tree = ast.parse(path.read_text())
    docs = set()
    for n in ast.walk(tree):
        if (isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)):
            docs.add(id(n.body[0].value))
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.lineno, n.value


def test_no_gui_text_points_at_chapters_or_compares_with_the_original():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for line, text in _literals(path):
            if PATTERN.search(text) and not any(rel == f and frag in text for f, frag in ALLOWED):
                offenders.append(f"{rel}:{line}: {text[:90]!r}")
    assert not offenders, "GUI text must stand on its own:\n" + "\n".join(offenders)
