"""The man pages are generated from the parser; they cannot drift from it."""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess

import pytest

from raptor.cli import manpages
from raptor.cli.main import build_parser


@pytest.fixture(scope="module")
def pages():
    return manpages.generate()


def _long_options(parser):
    return {o for a in parser._actions for o in a.option_strings
            if o.startswith("--") and o != "--help"}


def _subs(parser):
    for a in parser._actions:
        if isinstance(getattr(a, "choices", None), dict):
            return a.choices
    return {}


def _page_options(text):
    # roff-escaped "\-\-name" -> "--name"
    flat = text.replace("\\-", "-").replace("\\fB", "").replace("\\fR", "")
    return set(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", flat))


def test_every_command_has_a_page(pages):
    for name in _subs(build_parser()):
        if name in ("simulate", "describe"):          # aliases
            continue
        assert f"raptor-{name}.1" in pages, name
    assert {"raptor.1", "raptor-set-paths.7", "raptor-config.5"} <= set(pages)


def test_every_parser_option_is_in_its_page_and_no_other(pages):
    root = build_parser()
    assert _long_options(root) <= _page_options(pages["raptor.1"])
    for name, sp in _subs(root).items():
        key = f"raptor-{name}.1"
        if key not in pages:
            continue
        in_parser = _long_options(sp)
        in_page = _page_options(
            pages[key].split(".SH EXIT STATUS")[0].split(".SH EXAMPLES")[0])
        assert in_parser <= in_page, (name, in_parser - in_page)
        assert in_page - in_parser <= {"--help"}, (name, in_page - in_parser)


def test_every_page_has_the_standard_sections(pages):
    for name, text in pages.items():
        for sec in (".SH NAME", ".SH SEE ALSO", ".TH "):
            assert sec in text, (name, sec)
    for name in ("raptor.1", "raptor-run.1", "raptor-mcp.1"):
        assert ".SH EXIT STATUS" in pages[name]
        assert ".SH ENVIRONMENT" in pages[name]


@pytest.mark.skipif(shutil.which("groff") is None, reason="groff not installed")
def test_pages_are_warning_free_under_groff(pages, tmp_path):
    for name, text in pages.items():
        f = tmp_path / name
        f.write_text(text)
        proc = subprocess.run(["groff", "-ww", "-z", "-man", str(f)],
                              capture_output=True, text=True)
        assert proc.stderr.strip() == "", (name, proc.stderr)


def test_every_example_command_parses_with_the_real_parser():
    parser = build_parser()
    for _name, items in manpages.EXAMPLES.items():
        for cmd, _ in items:
            argv = shlex.split(cmd.split("|")[0])
            if not argv or argv[0] != "raptor":
                continue
            args = argv[1:]
            if args and args[0] == "mcp":      # options pass through
                continue
            if args and not args[0].startswith("-") and args[0] not in {
                    a for a in _subs(parser)}:
                continue                        # 'raptor pump.rbd' (GUI)
            parser.parse_args(args)             # raises SystemExit on error


def test_committed_man_pages_and_completions_are_current(pages, capsys):
    """`docs/man` and `docs/completions` are generated and installed by the
    wheel; regenerate with `python -m raptor.cli.manpages docs/man` and
    `raptor completion bash|zsh|fish`."""
    from pathlib import Path
    from raptor.cli.main import main
    repo = Path(__file__).resolve().parents[2]
    for name, text in pages.items():
        committed = (repo / "docs" / "man" / name)
        assert committed.is_file(), f"missing docs/man/{name}"
        assert committed.read_text() == text, f"docs/man/{name} is stale"
    for shell, fname in (("bash", "raptor"), ("zsh", "_raptor"),
                         ("fish", "raptor.fish")):
        main(["completion", shell])
        out = capsys.readouterr().out
        assert (repo / "docs" / "completions" / fname).read_text() == out, \
            f"docs/completions/{fname} is stale"


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_bash_completion_loads_and_completes_commands(tmp_path):
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    script = repo / "docs" / "completions" / "raptor"
    proc = subprocess.run(
        ["bash", "-c",
         f'source {script}; COMP_WORDS=(raptor ru); COMP_CWORD=1; _raptor; '
         f'echo "${{COMPREPLY[@]}}"'],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "run" in proc.stdout.split()
