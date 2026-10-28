"""Rule B0: nothing under ``tests/`` may execute the legacy Raptor.

The promise this file makes executable is that ``pytest tests`` passes on a
machine that has never had Raptor installed: no Wine prefix, no
``Raptor7Student.exe``, no Windows licence, no `the internal Raptor launcher`.
That is a portability requirement before it is a purity one -- CI runners and
contributors' laptops have none of those things -- and today nothing *prevents*
a violation, so the rule would be a convention nobody checks. ``test_tables.py``
shelling out to ``soffice`` shows the shape is easy to reach by accident.

**This guard is about execution only.** Reading a *captured* Raptor artifact is
inert text: opening ``rpt_events*.txt`` runs nothing. That is the distinction
the plan draws between B0 (never execute the binary, binds all of ``tests/``)
and B1 (never read a recorded oracle, binds ``tests/behaviour/``). A fidelity
test that parses a capture honours B0 completely, and banning captures here
would be over-banning rather than caution.

Two layers, because a token scan alone is not enough:

* a **token scan** for the project's own launcher vocabulary -- ``wine``,
  ``wine64``, ``Raptor7Student``, ``RAPTOR3.EXE``, ``launch_raptor``,
  ``find_raptor``, ``close_raptor``, ``launch_traced_raptor``,
  ``setup_wine32_debug`` -- which is the realistic way a legacy invocation
  creeps back in;
* an **``ast`` walk** for every call that can start a process
  (``subprocess.run``/``call``/``check_call``/``check_output``/``Popen``,
  ``os.system``, ``os.popen``, ``os.exec*``, ``os.spawn*``, ``shutil.which``),
  asserting the executable it names is ``sys.executable``, ``"python"`` or
  ``"python3"``, or is on a named allowlist. This layer generalises: it catches
  a Wine invocation by *any* spelling, which is why B0 does not depend on
  someone remembering to grep for the right word. It is the same class of
  mistake as token-scanning for capture artifacts -- a word list cannot
  generalise -- and it is also the layer that lets a docstring say "Wine" in
  prose without tripping.

Audit (2026-10-03, 188 modules under ``tests/``, 17 process launches found):
15 are ``sys.executable`` -- ``python -c`` snippets, ``-m raptor.mcp``,
``tools/rbd_builder.py``, the render worker -- and the remaining two are the
``soffice`` lines in ``tests/unit/test_tables.py`` (one ``shutil.which`` skip
guard, one headless convert), which are on ``ALLOWED_EXTERNAL`` below with their
reason.

Scanning *code*, not prose: comments and docstrings are stripped before the
token layer reads a file (``tokenize`` drops ``COMMENT``; module/class/function
docstrings are dropped by AST span). A module docstring may therefore explain
that a capture came from ``Raptor7Student`` -- as
``test_event_log_fidelity.py`` and ``test_probe_fidelity_captures.py`` do --
while ``subprocess.run(["wine", ...])`` is still a violation, because that is a
string literal *in an argument position*, not prose.

Known limits, stated rather than hidden: the scan covers ``*.py`` under
``tests/`` (a shell script dropped in ``tests/`` would not be seen), and this
file excludes itself, since it necessarily spells the banned words.
"""
from __future__ import annotations

import ast
import bisect
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

import pytest

THIS_FILE = Path(__file__).resolve()

# --- Layer 1: the project's own launcher vocabulary -------------------------
#
# The plan's §7.4 list. Kept as words (not one fused regex) so the docstring
# and the scan cannot drift apart.
FORBIDDEN_TOKENS: tuple[str, ...] = (
    "wine",
    "wine64",
    "Raptor7Student",
    "RAPTOR3.EXE",
    "launch_raptor",
    "find_raptor",
    "close_raptor",
    "launch_traced_raptor",
    "setup_wine32_debug",
)

# `RAPTOR3.EXE` is three tokens when written unquoted (an attribute access),
# so tolerate whitespace around the dot in the pattern.
_DOTTED_TOKENS = frozenset({"RAPTOR3.EXE"})


def _pattern_for(token: str) -> re.Pattern[str]:
    """Word-boundary pattern for one forbidden token, case-insensitively."""
    body = re.escape(token)
    if token in _DOTTED_TOKENS:
        body = body.replace(r"\.", r"\s*\.\s*")
    return re.compile(rf"\b{body}\b", re.IGNORECASE)


FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    _pattern_for(token) for token in FORBIDDEN_TOKENS
)

# Cheap pre-filter: if none of the words occurs anywhere in the raw text, then
# none can occur in the code either, so the tokeniser can be skipped. Without
# this the token layer costs ~5 s over the tree instead of ~0.
_MAYBE_FORBIDDEN = re.compile(
    "|".join(re.escape(token) for token in FORBIDDEN_TOKENS), re.IGNORECASE
)

# --- The one exception, named and reviewable -------------------------------
#
# Keyed by (repo-relative file, resolved program) so a new call in an
# allowlisted *file* is still a violation: only this exact program, in this
# exact file, is excused. `test_allowlist_entries_are_all_used` keeps the
# entries from rotting.
ALLOWED_EXTERNAL: dict[tuple[str, str], str] = {
    ("tests/unit/test_tables.py", "soffice"): (
        "LibreOffice, invoked headless to convert the .xlsx the writer "
        "produces and assert a real spreadsheet application reads every tab "
        "with numbers as numbers. It is not Raptor and cannot become Raptor, "
        "and the test skips when `shutil.which(\"soffice\")` is None -- which "
        "is what keeps B0 a portability promise rather than a wish."
    ),
    ("tests/unit/test_docs_html_current.py", "asciidoctor"): (
        "The documentation toolchain, run to prove the committed HTML is what `new_docs/tools/render_docs.sh` "
        "builds. It is not Raptor, and the test skips when `shutil.which(\"asciidoctor\")` is None."
    ),
    ("tests/unit/test_cli_manpages.py", "groff"): (
        "GNU roff, run as `groff -ww -z` to lint the generated man pages. "
        "It is a text formatter, not Raptor, and the test skips when "
        "`shutil.which(\"groff\")` is None."
    ),
    ("tests/unit/test_cli_manpages.py", "bash"): (
        "bash, to source the generated completion script and ask it for "
        "completions. Skips when bash is absent."
    ),
}

# Sentinel returned when a program is (or is derived from) the running
# interpreter. Kept distinct from a bare string so the report can say so.
_SYS_EXECUTABLE = "sys.executable"
_PYTHON_PROGRAMS = frozenset({_SYS_EXECUTABLE, "python", "python3"})


def _repo_root() -> Path:
    """The checkout root: the nearest ancestor holding ``pyproject.toml``+``tests``."""
    here = THIS_FILE
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "tests").is_dir():
            return parent
    return here.parents[2]


REPO_ROOT = _repo_root()
TESTS_ROOT = REPO_ROOT / "tests"


def _iter_test_modules() -> list[Path]:
    """Every ``*.py`` under ``tests/``, this file excluded (see module docstring)."""
    return sorted(
        path
        for path in TESTS_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
        and path != THIS_FILE
        and not (path.name == THIS_FILE.name and path.resolve() == THIS_FILE)
    )


def _relpath(path: Path) -> str:
    """Repo-relative POSIX path, so a report line can be pasted into an editor."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:  # pragma: no cover - only if tests/ moves out of the repo
        return path.as_posix()


def _read(path: Path) -> tuple[str, list[str]] | str:
    """``(source, lines)``, or an error message string if the file is unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"could not be read as UTF-8 source: {exc}"
    return text, text.splitlines()


@dataclass(frozen=True)
class _Module:
    """One scanned module under ``tests/``.

    ``error`` is set when the file could not be read, tokenised or parsed; such
    a file counts as a violation rather than a silent pass, because a file this
    guard cannot read is a file this guard cannot vouch for.
    """

    path: Path
    relpath: str
    source_lines: list[str]
    error: str | None
    token_hits: list[str]
    calls: list[_Call]

    @property
    def ok(self) -> bool:
        return self.error is None


# --- Layer 1 implementation -----------------------------------------------


@dataclass(frozen=True)
class _Token:
    """One code token, with its line and its offset in the reconstructed text."""

    line: int
    offset: int
    text: str


def _docstring_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """First-line..last-line of every module/class/function docstring."""
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
    return spans


def _code_tokens(source: str, tree: ast.AST) -> list[_Token]:
    """Tokenise ``source``, dropping comments and docstrings.

    Everything else survives -- notably string literals in argument positions,
    which is how ``subprocess.run(["wine", ...])`` is still caught by layer 1
    even though the ``ast`` layer is what usually catches it.
    """
    spans = _docstring_spans(tree)
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
        out.append(_Token(line=tok.start[0], offset=offset, text=tok.string))
        offset += len(tok.string) + 1  # tokens are joined with a single space
    return out


def _token_hits(source: str, tree: ast.AST) -> list[str]:
    """Report lines where a forbidden token appears in *code*."""
    if not _MAYBE_FORBIDDEN.search(source):
        return []
    tokens = _code_tokens(source, tree)
    joined = " ".join(tok.text for tok in tokens)
    starts = [tok.offset for tok in tokens] + [len(joined)]

    def line_of(char_index: int) -> int:
        index = bisect.bisect_right(starts, char_index) - 1
        return tokens[index].line if 0 <= index < len(tokens) else 1

    hits: list[str] = []
    for pattern in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(joined):
            line = line_of(match.start())
            hits.append(
                f"    line {line}: forbidden token {match.group(0)!r}"
                f" (pattern {pattern.pattern!r})"
            )
    return sorted(set(hits))


# --- Layer 2 implementation -----------------------------------------------

_SUBPROCESS_LAUNCHERS = ("run", "call", "check_call", "check_output", "Popen")
_OS_SHELL_CALLS = ("system", "popen")
_OS_ARGV0_CALLS = (
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
)

# Callee -> how to read its first argument. "argv0" means the program is
# args[0] (a sequence); "command" means the argument is a whole shell command
# line, so the program is its first whitespace-delimited word.
GUARDED_CALLS: dict[str, str] = {
    **{f"subprocess.{name}": "argv0" for name in _SUBPROCESS_LAUNCHERS},
    **{f"os.{name}": "command" for name in _OS_SHELL_CALLS},
    **{f"os.{name}": "argv0" for name in _OS_ARGV0_CALLS},
    "shutil.which": "argv0",
}

_ARGV0, _COMMAND = "argv0", "command"
_MODULE_SCOPE = 0
_MAX_RESOLVE_DEPTH = 12

# Last component of every guarded callee -- a cheap pre-filter so the resolver
# (the expensive part) is built only for modules that actually mention one.
_CALLEE_LEAVES = frozenset(name.rsplit(".", 1)[-1] for name in GUARDED_CALLS)

# A guarded call can only be written by naming the module it comes from
# (`subprocess.run`, `from os import system`), so a module that never mentions
# one of these words cannot contain one -- and 114 of the 188 modules under
# tests/ do not. Parsing only those takes the tree scan from ~9 s to ~1.5 s.
_GUARDED_MODULES = frozenset(name.split(".", 1)[0] for name in GUARDED_CALLS)
_MAYBE_GUARDED_CALL = re.compile(
    r"\b(?:{})\b".format("|".join(sorted(_GUARDED_MODULES)))
)


@dataclass(frozen=True)
class _Call:
    """One process-launching call found under ``tests/``."""

    path: str
    line: int
    callee: str
    argument: str
    program: str | None


def _dotted(node: ast.expr) -> str | None:
    """``a.b.c`` for a Name/Attribute chain, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


class _Resolver:
    """Resolves "which program does this call actually run?".

    Deliberately small: it follows imports (``import subprocess as sp``,
    ``from os import system``) and local assignments (``cmd = [sys.executable,
    ...] + extra``), which is enough for real code, and returns ``None`` when it
    cannot prove the answer. ``None`` counts as a violation, because the safe
    direction is to ask for an allowlist entry rather than wave a launch through.
    """

    def __init__(self, tree: ast.Module, wanted: frozenset[int]) -> None:
        self.aliases: dict[str, str] = {}
        self.assignments: dict[int, list[tuple[int, str, ast.expr]]] = {}
        self.parent: dict[int, int] = {0: 0}
        self.scope_of: dict[int, int] = {}
        self._bound: set[str] = set()
        self._wanted = wanted  # node ids we must remember the scope of
        self._walk(tree, _MODULE_SCOPE)

    # -- collection ---------------------------------------------------------

    def _bind(self, scope: int, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.assignments.setdefault(scope, []).append(
                (target.lineno, target.id, value)
            )

    def _walk(self, node: ast.AST, scope: int) -> None:
        if id(node) in self._wanted:
            self.scope_of[id(node)] = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                self._walk(decorator, scope)
            for default in self._defaults(node.args):
                self._walk(default, scope)
            for arg in self._arg_nodes(node.args):
                self._bound.add(arg.arg)  # a parameter shadows any import
            inner = id(node)
            self.parent[inner] = scope
            for statement in node.body:
                self._walk(statement, inner)
            return
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                self._walk(decorator, scope)
            for base in node.bases:
                self._walk(base, scope)
            inner = id(node)
            self.parent[inner] = scope
            for statement in node.body:
                self._walk(statement, inner)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                self.aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and not node.level:
                for alias in node.names:
                    self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                self._bind(scope, target, node.value)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            self._bind(scope, node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._bind(scope, node.target, node.iter)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    self._bind(scope, item.optional_vars, item.context_expr)
        elif isinstance(node, ast.ExceptHandler) and node.name is not None:
            # `except E as e:` -- bind the (unresolvable) name so it is known bound.
            self._bound.add(node.name)
        elif isinstance(node, ast.Lambda):
            for arg in self._arg_nodes(node.args):
                self._bound.add(arg.arg)
        for child in ast.iter_child_nodes(node):
            self._walk(child, scope)

    @staticmethod
    def _defaults(args: ast.arguments) -> list[ast.expr]:
        return [*(d for d in args.defaults if d is not None),
                *(d for d in args.kw_defaults if d is not None)]

    @staticmethod
    def _arg_nodes(args: ast.arguments) -> list[ast.arg]:
        return [*args.posonlyargs, *args.args, *args.kwonlyargs,
                *( [args.vararg] if args.vararg else [] ),
                *( [args.kwarg] if args.kwarg else [] )]

    # -- callee and program resolution --------------------------------------

    def callee(self, node: ast.Call) -> str | None:
        """The module-qualified callee, if it is one this guard watches."""
        dotted = _dotted(node.func)
        if dotted is None:
            return None
        head, _, attr = dotted.partition(".")
        if not attr:  # `from subprocess import run` -> dotted is just "run"
            return self.aliases.get(dotted)
        base = self.aliases.get(head)
        if base is None or "." in base:  # `os.path.join`-style, or unknown module
            return None
        if head in self._bound:
            # A parameter shadows the import (`def f(os): subprocess.run(...)`).
            # Skipping errs towards *missing* a violation, never towards a false
            # alarm, which is the right way round for a guard.
            return None
        return f"{base}.{attr}"

    def _lookup(self, name: str, scope: int, before: int) -> tuple[ast.expr, int] | None:
        """Nearest preceding binding of ``name`` visible from ``scope``."""
        current = scope
        for _ in range(_MAX_RESOLVE_DEPTH):
            found = None
            for lineno, bound_name, value in self.assignments.get(current, ()):
                if bound_name == name and lineno < before:
                    found = value
            if found is not None:
                return found, current
            if current == _MODULE_SCOPE or current not in self.parent:
                return None
            current = self.parent[current]
        return None

    def program(self, node: ast.expr, scope: int, kind: str,
                depth: int = 0) -> str | None:
        """The program ``node`` names, or ``None`` when it cannot be proven."""
        if depth > _MAX_RESOLVE_DEPTH:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _head_word(node.value) if kind == _COMMAND else node.value
        if isinstance(node, ast.JoinedStr):
            text = self._render_fstring(node, scope, depth)
            return None if text is None else _head_word(text) if kind == _COMMAND else text
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # `[sys.executable, "-m"] + extra` and "cmd" + " tail" both work.
            return self.program(node.left, scope, kind, depth + 1)
        if kind == _ARGV0:
            if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                if not node.elts:
                    return None
                return self.program(node.elts[0], scope, kind, depth + 1)
            if isinstance(node, ast.Starred):
                return self.program(node.value, scope, kind, depth + 1)
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            head, _, attr = dotted.rpartition(".") if dotted else ("", "", "")
            if attr == "executable" and self.aliases.get(head) == "sys":
                return _SYS_EXECUTABLE
            return None
        if isinstance(node, ast.Name):
            bound = self._lookup(node.id, scope, node.lineno)
            if bound is None:
                return None
            value, value_scope = bound
            return self.program(value, value_scope, kind, depth + 1)
        return None

    def _render_fstring(self, node: ast.JoinedStr, scope: int, depth: int) -> str | None:
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                rendered = self.program(value.value, scope, _ARGV0, depth + 1)
                if rendered is None:
                    return None
                parts.append(rendered)
            else:  # pragma: no cover - a format spec, e.g. f"{x!r:>3}"
                return None
        return "".join(parts)


def _head_word(command: str) -> str | None:
    words = command.split()
    return words[0] if words else None


def _first_argument(call: ast.Call) -> ast.expr | None:
    """The argument that names the program: positional 0, or ``args=``."""
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "args":
            return keyword.value
    return None


def _candidate_calls(tree: ast.Module) -> list[ast.Call]:
    """Calls whose callee *might* be guarded; ``callee()`` does the real test."""
    candidates: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if dotted and dotted.rpartition(".")[2] in _CALLEE_LEAVES:
            candidates.append(node)
    return candidates


def _call_kind(call: ast.Call, default: str) -> str:
    """``shell=True`` makes the first argument a command line, not an argv."""
    for keyword in call.keywords:
        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant):
            return _COMMAND if keyword.value.value else default
    return default


def _calls_in(tree: ast.Module, path: str) -> list[_Call]:
    """Every guarded call in one parsed module."""
    candidates = _candidate_calls(tree)
    if not candidates:
        return []
    resolver = _Resolver(tree, frozenset(id(node) for node in candidates))
    found: list[_Call] = []
    for node in candidates:
        callee = resolver.callee(node)
        if callee is None or callee not in GUARDED_CALLS:
            continue
        argument = _first_argument(node)
        if argument is None:
            continue  # e.g. `subprocess.Popen()` inherits the current process
        program = resolver.program(argument, resolver.scope_of[id(node)],
                                   _call_kind(node, GUARDED_CALLS[callee]))
        try:
            shown = ast.unparse(argument)
        except Exception:  # pragma: no cover - unparse is total for expressions
            shown = "<expression>"
        found.append(_Call(path=path, line=node.lineno, callee=callee,
                           argument=shown, program=program))
    return found


# --- one pass over the tree, shared by the tests below ---------------------

_SCAN_CACHE: dict[Path, _Module] = {}


def _scan_module(path: Path) -> _Module:
    """Scan one file for both layers. Cached: the tree does not move mid-run."""
    cached = _SCAN_CACHE.get(path)
    if cached is not None:
        return cached
    relpath = _relpath(path)
    read = _read(path)
    if isinstance(read, str):
        module = _Module(path, relpath, [], read, [], [])
    else:
        source, lines = read
        try:
            tree = (
                ast.parse(source)
                if _MAYBE_FORBIDDEN.search(source) or _MAYBE_GUARDED_CALL.search(source)
                else None
            )
        except SyntaxError as exc:
            module = _Module(path, relpath, lines, f"could not be parsed: {exc}", [], [])
        else:
            module = _Module(
                path, relpath, lines, None,
                _token_hits(source, tree) if tree is not None else [],
                _calls_in(tree, relpath) if tree is not None else [],
            )
    _SCAN_CACHE[path] = module
    return module


def _scan_tree() -> list[_Module]:
    """Every module under ``tests/``, scanned once."""
    return [_scan_module(path) for path in _iter_test_modules()]


def _describe(call: _Call, source_lines: list[str]) -> str:
    """One violation, naming the file, the line, and what was invoked."""
    try:
        excerpt = source_lines[call.line - 1].strip()
    except IndexError:  # pragma: no cover - defensive
        excerpt = ""
    if call.program is None:
        detail = (
            f"{call.callee}({call.argument}) -- the executable is not "
            f"resolvable to a constant"
        )
    else:
        detail = f"{call.callee} launches {call.program!r} via {call.argument}"
    out = [f"    {call.path}:{call.line}: {detail}"]
    if excerpt:
        out.append(f"        {excerpt}")
    return "\n".join(out)


# --- tests ----------------------------------------------------------------


def test_no_legacy_launcher_tokens_in_test_code() -> None:
    """No module under ``tests/`` names a Wine launcher outside prose."""
    offenders: list[str] = []
    for module in _scan_tree():
        if not module.ok:
            offenders.append(f"    {module.relpath}: {module.error}")
        elif module.token_hits:
            offenders.append(f"    {module.relpath}:")
            offenders.extend(module.token_hits)
    assert not offenders, (
        "Rule B0: forbidden legacy-launcher tokens in executable code under "
        "tests/ (comments and docstrings are excluded, so prose about Wine "
        "is fine):\n" + "\n".join(offenders)
    )


def test_no_foreign_executable_is_launched_from_tests() -> None:
    """Every process launched under ``tests/`` is this interpreter (or allowlisted)."""
    offenders: list[str] = []
    for module in _scan_tree():
        if not module.ok:
            offenders.append(f"    {module.relpath}: {module.error}")
            continue
        for call in module.calls:
            if call.program in _PYTHON_PROGRAMS:
                continue
            if (call.path, call.program or "") in ALLOWED_EXTERNAL:
                continue
            offenders.append(_describe(call, module.source_lines))
    assert not offenders, (
        "Rule B0: no test may execute a foreign binary -- the suite has to run "
        "on a machine that has never had Raptor installed. Only "
        "sys.executable / \"python\" / \"python3\" is allowed, plus explicit "
        "ALLOWED_EXTERNAL entries:\n" + "\n".join(offenders)
    )


def test_allowlist_entries_are_all_used() -> None:
    """An allowlist entry nothing exercises is a silently widened hole."""
    used = {
        (call.path, call.program or "")
        for module in _scan_tree()
        for call in module.calls
    }
    stale = sorted(
        f"    {key[0]}: {key[1]!r}" for key in ALLOWED_EXTERNAL if key not in used
    )
    assert not stale, (
        "ALLOWED_EXTERNAL entries that no launch under tests/ matches any "
        "more -- delete them, or the allowlist grows holes nobody reads:\n"
        + "\n".join(stale)
    )


def test_allowlist_keys_point_at_real_files() -> None:
    """An allowlist key naming a moved or renamed file is not an excuse."""
    missing = sorted(
        f"    {key[0]}" for key in ALLOWED_EXTERNAL if not (REPO_ROOT / key[0]).is_file()
    )
    assert not missing, "ALLOWED_EXTERNAL names files that do not exist:\n" + "\n".join(missing)


# Self-check: the guard must be able to see a violation, or it guards nothing.
# These assert the *scanner's* behaviour on source built here, not on the tree.
def _scan_source(source: str, path: str = "tests/unit/test_scanned.py") -> tuple[list[str], list[_Call]]:
    tree = ast.parse(source)
    return _token_hits(source, tree), _calls_in(tree, path)


@pytest.mark.parametrize(
    "source, expected_program",
    [
        ("import subprocess\nsubprocess.run(['wine', 'x.exe'])\n", "wine"),
        ("import subprocess as sp\nsp.Popen(['wine64', 'x.exe'])\n", "wine64"),
        ("from subprocess import run\nrun(['Raptor7Student.exe'])\n", "Raptor7Student.exe"),
        ("import os\nos.system('wine Raptor7Student.exe')\n", "wine"),
        ("import os as o\no.execv('/usr/bin/wine', ['wine', 'x'])\n", "/usr/bin/wine"),
        ("import shutil\nshutil.which('RAPTOR3.EXE')\n", "RAPTOR3.EXE"),
        ("import subprocess\nsubprocess.run('wine x.exe', shell=True)\n", "wine"),
        ("import subprocess\nsubprocess.run(cmd)\n", None),
        ("import subprocess\ncmd = ['wine', 'x.exe']\nsubprocess.run(cmd)\n", "wine"),
        ("import subprocess\n"
         "cmd = ['wine', 'x.exe']\n"
         "subprocess.run(cmd + ['-v'])\n", "wine"),
        ("import sys, os\nos.system(f'{sys.executable} -c pass')\n", "sys.executable"),
        ("import sys, subprocess\ncmd = [sys.executable, '-c', 'pass']\nsubprocess.Popen(cmd)\n",
         "sys.executable"),
        ("import sys, subprocess\nsubprocess.run(['python3', '-c', 'pass'])\n", "python3"),
    ],
)
def test_scanner_sees_an_executable_by_any_spelling(
    source: str, expected_program: str | None
) -> None:
    """The ``ast`` layer resolves the program, whatever the spelling.

    ``expected_program`` is ``None`` for a call whose program cannot be proved;
    unproven is reported, not waved through.
    """
    _, calls = _scan_source(source)
    programs = [call.program for call in calls]
    assert expected_program in programs, f"{programs} did not contain {expected_program!r}"


def test_scanner_reads_prose_but_not_code() -> None:
    """Docstrings and comments are excluded from the token scan; code is not."""
    prose = ('"""Do not run Wine here; see launch_raptor.sh."""\n'
             "# wine is only mentioned in this comment\n"
             "X = 1\n")
    hits, calls = _scan_source(prose)
    assert hits == []
    assert calls == []
    offending = '"""Fine."""\nimport subprocess\nsubprocess.run(["wine", "x.exe"])\n'
    hits, _ = _scan_source(offending)
    assert hits, "a wine string literal in an argument position must be caught"