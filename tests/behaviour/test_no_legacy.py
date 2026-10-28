"""Rules B1 and B6: no captured oracle in this directory, and every claim cited.

Two static guards, both over `tests/behaviour/*.py`, both ~0 s, both in the
fast tier -- because the rules they enforce are otherwise conventions nobody
checks.

**B1 -- no legacy input.** The behaviour suite's entire reason for existing is
that its expectations are *derived*, from the Raptor 7 rule a test names or
from arithmetic a reviewer can redo, and never copied out of a file real Raptor
wrote in September. A capture path in a behaviour module is therefore not a
style offence; it is the failure mode the directory was built to make
impossible, because an expectation that came out of a file cannot be argued
with.

The token set: `golden_fixtures`, `SNAPSHOT.toml`,
`FIDELITY.toml`, `legacy`, `simDetails`, `draws.txt` (which covers the
`*_draws.txt` stream dumps `test_rng.py` reads -- a gap that once caused a silent false green) and `tests/golden`. The patterns
tolerate whitespace and either path separator, so `"tests/golden"`,
`r"tests\\golden"` and `ROOT / "tests" / "golden" / ...` are all caught.

**B6 -- a named claim and a cited source.** Every function named `test_*` in
this directory must have a docstring carrying a `Source:` line. The oracle for a behaviour test is the Raptor 7 procedure
it reproduces, the port code that implements it, or a
`docs/engine-design/*.adoc` section, all of which a reviewer can go and read. That is a stronger and more permanent oracle than a text file
captured once, and it is checkable only if somebody checks it.

**Prose is prose, code is code.** Both layers strip comments and docstrings
before they read a file (`tokenize` drops `COMMENT`; module/class/function
docstrings, and an explicit module-level `__doc__ = ...`, are dropped by AST
span) while every other string literal survives. So a docstring may explain
what the captured corpus is *for* -- `test_b_behaviour_coverage.py` spends its
module docstring on it -- and a comment may warn off a tempting shortcut,
without tripping either guard; while a path in an *argument* position, a
path joined at module level, or a path built by an f-string is a string
literal in live code and is caught. The asymmetry is the whole point: the word
is only dangerous where it can cause a file to be read, and only live code can
do that. Quote characters are blanked (to spaces, so offsets survive) before
the patterns run, because a quote is not part of a path's spelling and
`ROOT / "tests" / "golden"` is the same path as `ROOT / "tests/golden"`.

One documented consequence: a live module-level *constant* naming a capture path
trips the guard even if nothing opens it. That is intended -- a behaviour test
has no reason to name one, and the fix (fold the sentence into the docstring)
is what you would want anyway.

**The one permitted delegation, stated rather than hidden.**
`test_b_behaviour_coverage.py` is the retirement gate: it reads all
206 event-log captures to extract the corpus's 318 state transitions and then
asserts the behaviour suite pins them. It cannot do that without the corpus,
so it does not name a single capture path itself -- it imports
`tools/behaviour_inventory.py`, which is where the capture root
lives, and asks it for the transitions. The capture paths are in **one** place,
outside this directory, in the tool that is allowed to read them, and the
behaviour module opens nothing. That is the shape the rule wants, not a
loophole in it: the guard below scans the behaviour modules for *direct*
access, and `test_the_corpus_scan_is_delegated_to_tools` asserts the
delegation is intact rather than letting it rot.

**Two reviewed exemptions, both named in the test.** An exemption goes on a
reviewed list **in the test**, so the next one is a one-line diff a reviewer
sees. This guard needs the same for two things: a module whose
tests are about the corpus or about this guard rather than about an engine
behaviour has no Raptor 7 to cite, and this guard necessarily spells
every banned word in order to forbid them. Both are keyed by file name, and
`test_the_exemptions_are_all_used` keeps them from growing.

Known limits, stated rather than hidden: the scan reads `*.py` under this
directory (rule B7 forbids this directory having a fixture file, so there is
nothing else here to read), and it is a token scan, so a path assembled from
fragments the reader cannot see -- `Path("golden") / ".." / "fixtures"` -- is
not resolvable by it. The positive controls at the bottom check the scanner
against source built here, so a change that silently disabled it is itself a
failure.
"""
from __future__ import annotations

import ast
import bisect
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

import pytest

THIS_FILE = Path(__file__).resolve()
BEHAVIOUR = THIS_FILE.parent
REPO = THIS_FILE.resolve().parents[2]

#: Quotes are blanked to spaces before a pattern runs: a quote is not part of
#: a path's spelling, and `ROOT / "tests" / "golden"` is one path spelled three
#: ways. Blanking rather than deleting keeps every offset valid, so a hit can
#: still be reported at the line it was found on.
_QUOTES = str.maketrans({c: " " for c in "\"'`"})

# --- B1: the capture token set ---------------------------------------------
#
# Kept as (label, pattern) pairs rather than one fused regex so the docstring
# and the scan cannot drift apart, and so a report can name *which* rule was
# broken. Seven labels, three of them plain words.
_CAPTURE_TOKENS: tuple[tuple[str, str], ...] = (
    ("golden_fixtures", r"\bgolden_fixtures\b"),
    ("SNAPSHOT.toml", r"\bSNAPSHOT\s*\.\s*toml\b|\bSNAPSHOT\s*[\\/]\s*toml\b"),
    ("FIDELITY.toml", r"\bFIDELITY\s*\.\s*toml\b|\bFIDELITY\s*[\\/]\s*toml\b"),
    ("legacy", r"\blegacy\b"),
    ("simDetails", r"\bsim\s*details\b"),
    ("draws.txt", r"\b[A-Za-z0-9_]*draws\s*\.\s*txt\b"),
    ("tests/golden", r"\btests\s*[\\/]\s*golden\b"),
)

#: Bare fragments, one per token, each a substring of that token's pattern.
_PRE_FILTER_FRAGMENTS: tuple[str, ...] = (
    "golden_fixtures", "SNAPSHOT", "FIDELITY", "legacy", "sim", "draws",
    "tests",
)

#: `(label, compiled pattern)` -- the case-insensitive compiled layer.
CAPTURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in _CAPTURE_TOKENS
)

# Cheap pre-filter: if no fragment of any token occurs anywhere in the raw text,
# none can occur in the code, so the tokeniser is skipped for that file
# entirely. Deliberately a *weak* over-approximation, not the patterns
# themselves: a spelling like `'tests' / 'golden'` -- two literals, a
# separator between them -- does not contain `tests/golden` as a substring but
# does violate B1, so a tight pre-filter would be a silent false green. The
# cost of being loose is that a file mentioning "tests" is tokenised anyway,
# and this guard reads six files.
_MAYBE_CAPTURE = re.compile(
    "|".join(re.escape(fragment) for fragment in _PRE_FILTER_FRAGMENTS),
    re.IGNORECASE,
)

#: The tools-side module the retirement gate delegates the corpus read to.
INVENTORY = REPO / "tools" / "behaviour_inventory.py"

#: The retirement gate itself -- the one behaviour module allowed to need the
#: corpus, and allowed to get at it only through `INVENTORY`.
DELEGATING_MODULE = "test_b_behaviour_coverage.py"

#: Modules exempt from rule B6, each with the reason it needs no `Source:`
#: line. Deliberately *not* keyed on anything a module can change about
#: itself: only a reviewer editing this table can widen the exemption, and
#: `test_the_exemptions_are_all_used` fails if an entry goes stale, so an
#: exemption cannot quietly outlive the thing that needed it.
NO_SOURCE_CITATION_REQUIRED: dict[str, str] = {
    DELEGATING_MODULE: (
        "The retirement gate's subject is the captured corpus itself, not an "
        "engine mechanism: its claim is 'the corpus exhibits 318 transitions "
        "and the behaviour suite pins N of them', derived by "
        f"`{INVENTORY.stem}.py` reading the captures. There is no "
        "Raptor 7 procedure behind it, and the plan section it implements "
        "(5.9) *is* the citation -- it is named in the module docstring and in "
        "every test's failure message. Forcing a `Source:` line here would "
        "mean inventing an anchor."
    ),
    THIS_FILE.name: (
        "This guard's own subject is this guard: its tests assert on source "
        "strings built inside the test, on the token set below, and on the "
        "plan. It spells every banned word in order to forbid them, which is "
        "also why it excludes itself from the B1 token scan (see `_scan_module`)."
    ),
}


# --- the scanning layers ---------------------------------------------------


@dataclass(frozen=True)
class _Token:
    """One surviving code token, with its line and its offset in the join."""

    line: int
    offset: int
    text: str


def _prose_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """First-line..last-line of every docstring, and of an explicit `__doc__`.

    A module/class/function docstring is its first statement when that
    statement is a bare string constant. An explicit module-level
    `__doc__ = "..."` is prose too, and is not a docstring to the AST, so it
    is picked up separately: this guard's own template lives in a docstring,
    and a module that reassigns `__doc__` is still only explaining itself.
    """
    spans: list[tuple[int, int]] = []
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.value.lineno is not None
            and first.value.end_lineno is not None
        ):
            spans.append((first.value.lineno, first.value.end_lineno))
    for node in getattr(tree, "body", []):
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__doc__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and node.value.lineno is not None
            and node.value.end_lineno is not None
        ):
            spans.append((node.value.lineno, node.value.end_lineno))
    return spans


def _code_tokens(source: str, tree: ast.AST) -> list[_Token]:
    """Tokenise `source`, dropping comments and docstrings, quotes blanked.

    Everything else survives -- notably string literals in argument
    positions, f-strings, and raw strings, which is how a capture path
    reaches the file at all. Tokens are joined with a single space to give a
    pattern room to span a boundary (`"tests"` / `"golden"`); that also means a
    pattern can only match across two adjacent tokens, never across statements.
    """
    spans = _prose_spans(tree)
    out: list[_Token] = []
    offset = 0
    reader = tokenize.generate_tokens(iter(source.splitlines(keepends=True)).__next__)
    for tok in reader:
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and any(
            low <= tok.start[0] <= high for low, high in spans
        ):
            continue
        text = tok.string.translate(_QUOTES)
        out.append(_Token(line=tok.start[0], offset=offset, text=text))
        offset += len(text) + 1  # tokens are joined with a single space
    return out


def capture_hits(source: str, path: str) -> list[str]:
    """Every B1 token occurring in `source`'s *code*, one report line each."""
    if not _MAYBE_CAPTURE.search(source):
        return []
    tree = ast.parse(source, filename=path)
    tokens = _code_tokens(source, tree)
    joined = " ".join(tok.text for tok in tokens)
    starts = [tok.offset for tok in tokens] + [len(joined)]

    def line_of(char_index: int) -> int:
        index = bisect.bisect_right(starts, char_index) - 1
        return tokens[index].line if 0 <= index < len(tokens) else 1

    hits: list[str] = []
    for label, pattern in CAPTURE_PATTERNS:
        for match in pattern.finditer(joined):
            hits.append(
                f"    line {line_of(match.start())}: capture token {label!r} "
                f"({match.group(0)!r}) in live code"
            )
    return sorted(set(hits))


def _test_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every `test_*` function in one module, in source order.

    Nested functions are included: B6's reason (a reviewer must be able to
    check the claim against a cited line) does not care how the function is
    nested, and a generated test nobody reads is not a cited claim.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def _source_line_gaps(tree: ast.AST) -> list[str]:
    """`test_*` functions whose docstring carries no `Source:` line.

    B6's template is `claim / why / setup / expected / Source:`. The check is
    for the literal `Source:` marker rather than for an Raptor 7 or
    `.adoc` shape, because the marker is what makes the citation impossible to
    omit silently: a reviewer then has a visible empty slot rather than an
    unnumbered citation to notice or not. Leading whitespace is allowed, since
    `clean=True` dedents the continuation lines but a citation broken across two
    lines is still a citation.
    """
    gaps: list[str] = []
    for function in _test_functions(tree):
        docstring = ast.get_docstring(function)
        if not docstring:
            gaps.append(f"{function.name}() (no docstring at all)")
        elif not re.search(r"^[ \t]*Source:", docstring, re.MULTILINE):
            gaps.append(
                f"{function.name}() (docstring has no line starting 'Source:')"
            )
    return gaps


@dataclass(frozen=True)
class _Module:
    """One scanned module under `tests/behaviour/`.

    `error` is set when the file could not be read, tokenised or parsed. Such
    a module counts as a violation rather than a silent pass: a file this
    guard cannot read is a file this guard cannot vouch for.
    """

    name: str
    error: str | None = None
    hits: list[str] = field(default_factory=list)
    missing_source: list[str] = field(default_factory=list)


def scan_module_text(source: str, name: str) -> _Module:
    """Both rules over one module's text, with parse errors captured."""
    try:
        tree = ast.parse(source, filename=name)
    except SyntaxError as exc:
        return _Module(name, f"could not be read or parsed: {exc}")
    return _Module(name, None, capture_hits(source, name), _source_line_gaps(tree))


def _iter_modules() -> list[Path]:
    """Every `*.py` under `tests/behaviour/`, this guard excluded."""
    return sorted(
        path
        for path in BEHAVIOUR.rglob("*.py")
        if "__pycache__" not in path.parts and path.resolve() != THIS_FILE
    )


_SCAN_CACHE: dict[Path, _Module] = {}


def _scan_module(path: Path) -> _Module:
    """Scan one file for both rules. Cached: the tree does not move mid-run."""
    cached = _SCAN_CACHE.get(path)
    if cached is not None:
        return cached
    name = path.relative_to(BEHAVIOUR).as_posix()
    try:
        module = scan_module_text(path.read_text(encoding="utf-8"), name)
    except (OSError, UnicodeDecodeError) as exc:
        module = _Module(name, f"could not be read as UTF-8 source: {exc}")
    _SCAN_CACHE[path] = module
    return module


def _scan_suite() -> list[_Module]:
    """Every module under `tests/behaviour/`, scanned once."""
    return [_scan_module(path) for path in _iter_modules()]


# --- the guard tests -------------------------------------------------------


def test_no_behaviour_module_reads_a_capture(capsys: pytest.CaptureFixture[str]) -> None:
    """No module under `tests/behaviour/` names a captured legacy artifact.

    Setup: every `*.py` in the directory, comments and docstrings stripped.

    Expected: zero tokens from the set, and a printed count of the
    modules and tokens the scan actually covered.

    Source: tests/behaviour/README.md (The rule set,
    row B1)
    """
    modules = _scan_suite()
    offenders: list[str] = []
    for module in modules:
        if module.error is not None:
            offenders.append(f"    {module.name}: {module.error}")
        else:
            offenders.extend(f"    {module.name}:{line}" for line in module.hits)

    with capsys.disabled():
        print(
            f"\nrule B1 scan: {len(modules)} module(s) under tests/behaviour/ "
            f"(this guard excluded), {len(CAPTURE_PATTERNS)} token patterns, "
            f"{sum(len(m.hits) for m in modules)} hit(s):"
        )
        for module in modules:
            print(f"    {module.name}")

    assert not offenders, (
        "Rule B1: no module under tests/behaviour/ may read a captured legacy "
        "artifact -- the expectation must be derived from the source or from "
        "arithmetic, never copied out of a file real Raptor wrote. Comments "
        "and docstrings are excluded, so naming a capture in prose is fine; a "
        "capture path in live code is not:\n" + "\n".join(offenders)
    )


def test_every_test_function_cites_its_source() -> None:
    """Every `test_*` under `tests/behaviour/` needs a docstring `Source:` line.

    A behaviour test's oracle is the Raptor 7 procedure it reproduces, the
    port code, or a `docs/engine-design/` section a reviewer can go and read. Without the
    citation the expectation is an assertion with no argument behind it, which
    is B5's trap reached by another route.

    Source: tests/behaviour/README.md (The docstring
    template)
    """
    offenders: list[str] = []
    gaps = 0
    for module in _scan_suite():
        if module.error is not None:
            offenders.append(f"    {module.name}: {module.error}")
            continue
        if module.name in NO_SOURCE_CITATION_REQUIRED:
            continue
        gaps += len(module.missing_source)
        offenders.extend(f"    {module.name}: {gap}" for gap in module.missing_source)
    assert not offenders, (
        "Rule B6: every test function under tests/behaviour/ needs a docstring "
        "with a line starting 'Source:' -- the claim in plain English and the "
        f"anchor a reviewer can check it against ({gaps} gap(s) across the "
        f"suite; {sorted(NO_SOURCE_CITATION_REQUIRED)} are exempt by name in "
        "NO_SOURCE_CITATION_REQUIRED):\n" + "\n".join(offenders)
    )


def test_the_exemptions_are_all_used() -> None:
    """No exemption outlives the thing that needed it.

    An allowlist nobody prunes is an allowlist that only grows, and the
    direction it grows in is "more behaviour tests without a citation". Each
    entry must name a file that is still here, still exempt-and-present, and
    still have tests to exempt.

    Source: tests/behaviour/README.md (The rule set, row B6)
    """
    on_disk = {path.name for path in BEHAVIOUR.glob("*.py")}
    missing = sorted(set(NO_SOURCE_CITATION_REQUIRED) - on_disk)
    assert not missing, (
        "NO_SOURCE_CITATION_REQUIRED names files that are gone -- a stale "
        f"exemption is a hole nobody reads: {missing}"
    )
    unused = sorted(
        name
        for name in NO_SOURCE_CITATION_REQUIRED
        if name != THIS_FILE.name
        and not _test_functions(
            ast.parse((BEHAVIOUR / name).read_text(encoding="utf-8"))
        )
    )
    assert not unused, (
        "NO_SOURCE_CITATION_REQUIRED entries with no test functions left to "
        f"exempt: {unused}"
    )
    assert set(NO_SOURCE_CITATION_REQUIRED) <= on_disk | {THIS_FILE.name}


def test_the_scan_covers_every_module_in_the_suite() -> None:
    """The guard scans the whole directory, and excludes exactly one file.

    A guard that silently skipped a module -- a glob mistake, a `__pycache__`
    path component, an exclusion that grew by accident -- would still pass on
    the files it did read. This asserts the covered set is the existing set
    less the guard itself, and that the token set has not shrunk.

    Source: tests/behaviour/README.md (Adding a rung, step 3 -- a
    new module must land inside the gate's view)
    """
    on_disk = sorted(
        path.relative_to(BEHAVIOUR).as_posix()
        for path in BEHAVIOUR.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    scanned = sorted(module.name for module in _scan_suite())
    excluded = sorted(set(on_disk) - set(scanned))
    assert excluded == [THIS_FILE.name], (
        "the only file under tests/behaviour/ the guard may skip is its own; "
        f"these were skipped: {excluded}"
    )
    assert len(CAPTURE_PATTERNS) == len(_CAPTURE_TOKENS) >= 7, (
        "the capture token set must not shrink without a decision: "
        "records a draft that missed `*_draws.txt` and produced a silent false "
        f"green (now {len(CAPTURE_PATTERNS)} patterns, was 6)"
    )


def test_the_corpus_scan_is_delegated_to_tools() -> None:
    """The retirement gate reaches the corpus through one tools-side module.

    `test_b_behaviour_coverage.py` must read the captures to derive its
    denominator, and it does that by importing
    `tools/behaviour_inventory.py` rather than by naming a capture
    path itself. The delegation is the shape rule B1 wants -- the capture root
    in exactly one place, outside this directory -- so this test asserts it
    rather than waving at it: the inventory module exists and does hold the
    corpus, the gate imports it, and no *other* behaviour module does.

    Source: tests/behaviour/README.md (The rule
    set, row B1)
    """
    assert INVENTORY.is_file(), (
        f"{INVENTORY} is the module the retirement gate delegates the corpus "
        "read to; without it the gate would have to name a capture path itself"
    )
    inventory_text = INVENTORY.read_text(encoding="utf-8")
    assert capture_hits(inventory_text, INVENTORY.name), (
        f"{INVENTORY.name} names no capture path -- either the corpus moved "
        "(update this guard and the gate together) or the inventory stopped "
        f"reading the corpus, which makes {DELEGATING_MODULE}'s denominator "
        "vacuous"
    )

    importers: dict[str, str] = {}
    for path in _iter_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == INVENTORY.stem:
                        importers[path.name] = f"import {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module == INVENTORY.stem:
                importers[path.name] = f"from {node.module} import ..."

    assert set(importers) == {DELEGATING_MODULE}, (
        "the only behaviour module allowed to reach the captured corpus is "
        f"{DELEGATING_MODULE}, and only through {INVENTORY.stem}.py. These "
        f"others import it too: {sorted(set(importers) - {DELEGATING_MODULE})}. "
        "Two modules reading captures means two places to keep in step and one "
        "more way for the suite to depend on September."
    )


# --- positive controls: the guard must be able to see a violation ----------
#
# These assert the *scanner's* behaviour on source built here, not on the
# tree. A guard that cannot fail is not a guard, and the cheapest way to lose
# that property silently is an edit to the filtering above.


@pytest.mark.parametrize(
    "shape, source",
    [
        ("a path joined out of separate literals",
         "P = REPO / 'tests' / 'golden' / 'UniformProbe'\n"),
        ("backslashes and a raw string",
         "P = r'tests\\golden\\LadL8A'\n"),
        ("a relative path in a Path() call",
         "P = Path('tests/golden/LadL8B/input.rbd')\n"),
        ("an f-string path",
         'P = f"{ROOT}/tests/golden/{name}/rpt_events.txt"\n'),
        ("a stream draw dump",
         "LINES = (GOLDEN / 'stream1_fail_draws.txt').read_text()\n"),
        ("an import of the fixtures module",
         "import golden_fixtures\n"),
        ("the snapshot toml",
         "SNAP = TOML / 'SNAPSHOT.toml'\n"),
        ("the fidelity toml",
         "FID = ROOT / 'FIDELITY.toml'\n"),
        ("an /sd draw capture",
         "SD = RUN / 'simDetails.TXT'\n"),
        ("the bare word, as a live string",
         "TOPIC = 'legacy'\n"),
    ],
)
def test_scanner_sees_a_capture_by_any_spelling(shape: str, source: str) -> None:
    """A capture path in live code is caught however it is spelled.

    The ten shapes above are the ones a behaviour test could plausibly reach
    for; each is a different string in a different position, and the token
    layer has to see all of them. `shape` is there so the failure message
    names the spelling rather than printing ten bare parameter ids.

    Source: tests/unit/test_no_legacy_binary.py
    (`test_scanner_sees_an_executable_by_any_spelling` -- the same
    self-check, for rule B0)
    """
    module = scan_module_text(source, "<scanner control>")
    assert module.hits, (
        f"the scanner missed a capture path written as: {shape}"
    )


def test_scanner_reads_prose_but_not_code() -> None:
    """Docstrings and comments are excluded; live code is not.

    The whole reason this guard can be strict about the token `legacy` is that
    prose cannot open a file. `test_b_behaviour_coverage.py` spends its module
    docstring explaining what the captured corpus is *for*, and this file's own
    docstring spells every banned word in order to forbid it; both must be
    invisible to the scan, or the guard could only be written by never
    mentioning the problem.

    Source: tests/unit/test_no_legacy_binary.py
    (`test_scanner_reads_prose_but_not_code` -- rule B0's, same shape)
    """
    prose = (
        '"""This suite never reads the legacy corpus; the ladder is built\n'
        "from Raptor 7's own rules, not from tests/golden/ or simDetails.TXT.\n"
        '"""\n'
        "# golden_fixtures / FIDELITY.toml / *_draws.txt appear only here\n"
        "TOPIC = 'legacy'\n"
    )
    module = scan_module_text(prose, "<prose control>")
    assert module.hits == [
        "    line 5: capture token 'legacy' ('legacy') in live code"
    ], (
        "the docstring (lines 1-3) and the comment (line 4) must both be "
        f"invisible; only the live constant on line 5 may be seen; got {module.hits}"
    )

    offending = (
        '"""Fine."""\n'
        "def helper(name):\n"
        '    return open(REPO / "tests" / "golden" / name / "rpt_events.txt")\n'
    )
    module = scan_module_text(offending, "<code control>")
    assert module.hits, "a capture path in an argument position must be caught"


def test_scanner_reports_a_module_it_cannot_parse() -> None:
    """A file it cannot read counts as a violation, not as a silent pass.

    The safe direction for a coverage guard is "ask for an exemption", never
    "quietly skip": a module the guard cannot read is one the guard cannot
    vouch for. Reporting it that way is what stops a broken glob or an
    encoding mistake from quietly turning B1 into a no-op.

    Source: this module's docstring (the guard's
    asymmetry: a false positive drops a test and is caught in seconds, a false
    negative leaves one in and is caught never)
    """
    module = scan_module_text("def test_x(:\n", "<unparseable control>")
    assert module.error is not None, "an unparseable module must be reported"
    assert "could not be read or parsed" in module.error