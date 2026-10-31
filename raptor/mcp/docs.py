"""Tutorial-doc search and read-back for the ``search_docs``/``read_doc``
tools.

Search works on **paragraphs**, not single lines, and requires every term
of a multi-word query to appear (so ``"capacity analysis system capacity"``
matches a passage that discusses all of them).  ``read_doc`` returns the
full text of the RBDS format guide, the MCP guide chapter, or any tutorial
chapter, so a client that cannot read MCP resources can still read the
documentation as data.
"""
from __future__ import annotations

import re
from pathlib import Path

from raptor.bundled import data_root

REPO = data_root()
CHAPTERS = REPO / "new_docs" / "chapters"
RBDS_FORMAT = REPO / "docs" / "RBDS_FORMAT.md"
RBDS_SCHEMA = REPO / "docs" / "rbds.schema.json"
IEC61078_DOC = REPO / "docs" / "IEC61078.md"
MCP_GUIDE = CHAPTERS / "ch16-mcp.adoc"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_TITLE_RE = re.compile(r"^==\s+(?P<title>.+)$")          # AsciiDoc chapters
_ANCHOR_RE = re.compile(r"^\[\[(?P<anchor>[^\]]+)\]\]\s*$")
_MD_TITLE_RE = re.compile(r"^#{1,6}\s+(?P<title>.+)$")   # Markdown reference docs


def _chapter_files() -> list[Path]:
    if not CHAPTERS.is_dir():
        return []
    return sorted(CHAPTERS.glob("*.adoc"))


#: Markdown reference docs that aren't tutorial chapters but carry
#: information an agent needs (the .rbds format guide's topology rules,
#: dependency semantics, etc.) -- included in `search_docs` so this isn't
#: findable only by already knowing to `read_doc` it by name.
def _reference_docs() -> list[Path]:
    return [p for p in (RBDS_FORMAT, IEC61078_DOC) if p.is_file()]


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _paragraphs(path: Path) -> list[dict[str, str]]:
    """Split a doc into paragraphs, tracking the nearest preceding
    heading as ``chapter`` and (AsciiDoc only) the nearest preceding
    ``[[anchor]]``. AsciiDoc chapters use ``== Title``; Markdown
    reference docs (``.md``) use ``#``..``######`` and carry no anchors."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    is_md = path.suffix.lower() == ".md"
    title_re = _MD_TITLE_RE if is_md else _TITLE_RE
    chapter = path.stem
    anchor = ""
    paras: list[dict[str, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            text = " ".join(s.strip() for s in buffer if s.strip())
            if text:
                paras.append({"chapter": chapter, "anchor": anchor, "text": text})
            buffer.clear()

    for line in lines:
        title = title_re.match(line)
        if title:
            flush()
            chapter = title.group("title").strip()
            continue
        if not is_md:
            anchor_match = _ANCHOR_RE.match(line)
            if anchor_match:
                flush()
                anchor = anchor_match.group("anchor")
                continue
        if not line.strip():
            flush()
            continue
        buffer.append(line)
    flush()
    return paras


def search_chapters(query: str, *, limit: int = 5) -> list[dict[str, str]]:
    """Return up to *limit* passages containing **all** terms of *query*.

    Searches both the tutorial chapters and the Markdown reference docs
    (currently ``docs/RBDS_FORMAT.md``) -- a topology/format rule that
    only exists in the reference doc must still turn up for a plain
    query, not only for a caller who already knows to `read_doc` it by
    name.  Each result is ``{chapter, file, anchor, text, score}``.
    Paragraphs are ranked by total term frequency, then doc order
    (tutorial chapters first, in chapter order, then reference docs).
    """
    terms = _tokens(query)
    if not terms:
        return []

    scored: list[tuple[int, int, dict[str, str]]] = []
    paths = _chapter_files() + _reference_docs()
    for order, path in enumerate(paths):
        rel = str(path.relative_to(REPO))
        for para in _paragraphs(path):
            low = para["text"].lower()
            if all(t in low for t in terms):
                freq = sum(low.count(t) for t in terms)
                scored.append((freq, order, {
                    "chapter": para["chapter"],
                    "file": rel,
                    "anchor": para["anchor"],
                    "text": para["text"][:600],
                    "score": str(freq),
                }))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:limit]]


#: Friendly names for the docs ``read_doc`` can return.
_DOC_ALIASES: dict[str, Path] = {
    "rbds": RBDS_FORMAT,
    "rbds-format": RBDS_FORMAT,
    "rbds_format": RBDS_FORMAT,
    "format": RBDS_FORMAT,
    "rbds-schema": RBDS_SCHEMA,
    "schema": RBDS_SCHEMA,
    "iec61078": IEC61078_DOC,
    "iec-61078": IEC61078_DOC,
    "61078": IEC61078_DOC,
    "dependency": CHAPTERS / "ch-dependency.adoc",
    "dependencies": CHAPTERS / "ch-dependency.adoc",
    "mcp": MCP_GUIDE,
    "mcp-guide": MCP_GUIDE,
    "mcp_guide": MCP_GUIDE,
    "guide": MCP_GUIDE,
}


def read_doc(name: str) -> dict[str, str]:
    """Return ``{name, file, text}`` for a named documentation source.

    *name* may be ``rbds`` (the format guide), ``mcp-guide`` (this MCP
    chapter), a chapter stem such as ``ch11-capacity``, a chapter title
    fragment, or a repo-relative path under ``docs/``/``new_docs/``.
    """
    key = name.strip().lower()
    if key in _DOC_ALIASES:
        return _load(name, _DOC_ALIASES[key])

    # Exact chapter stem / filename. `name` is untrusted: an absolute or
    # `..` path must not escape the docs trees (read_doc could otherwise
    # read /proc/self/environ and the HTTP bearer token).
    roots = (CHAPTERS.resolve(), RBDS_FORMAT.parent.resolve(),
             (REPO / "docs").resolve(), (REPO / "new_docs").resolve())
    for candidate in (CHAPTERS / f"{name}.adoc", CHAPTERS / name,
                      RBDS_FORMAT.parent / name, REPO / name):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if not any(resolved == root or root in resolved.parents
                   for root in roots):
            continue
        return _load(name, resolved)

    # Chapter title fragment.
    for path in _chapter_files():
        for para in _paragraphs(path):
            if key and key in para["chapter"].lower():
                return _load(name, path)

    raise FileNotFoundError(
        f"no documentation named {name!r}; try 'rbds', 'mcp-guide', a "
        f"chapter like 'ch11-capacity', or a path under docs/ or new_docs/"
    )


def _load(name: str, path: Path) -> dict[str, str]:
    try:
        rel = str(path.relative_to(REPO))
    except ValueError:
        rel = str(path)
    return {
        "name": name,
        "file": rel,
        "text": path.read_text(encoding="utf-8", errors="replace"),
    }
