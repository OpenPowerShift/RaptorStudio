"""Generate the man pages from the command-line parser.

The parser is the single source for options and their help text, so a page
cannot list an option that does not exist or miss one that does (a test
checks both directions). Hand-written sections (examples, files, see-also)
live in the tables below.

    python -m raptor.cli.manpages OUTDIR        # writes raptor.1, raptor-run.1, ...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X

DATE = "2026-10"          # bumped by hand when pages change meaningfully

EXAMPLES: dict[str, list[tuple[str, str]]] = {
    "raptor": [
        ("raptor", "Open the desktop application."),
        ("raptor pump.rbd", "Open pump.rbd in the desktop application."),
        ("raptor run pump.rbd -n 100 -f json | jq .summary.Availability",
         "Simulate 100 runs and read one number."),
    ],
    "run": [
        ("raptor run plant.rbd --hierarchy Cooling -n 200",
         "Check one hierarchy on its own (a port extension; the results are labelled isolated)."),
        ("raptor run pump.rbd", "Simulate with the model's own settings and "
         "print a summary table."),
        ("raptor run pump.rbd -n 500 --seed 7 --json results.json",
         "500 runs, a different random seed, structured results to a file."),
        ("raptor run pump.rbd --set 'block:Pump A.fail_dist=weibull' "
         "--set 'block:Pump A.fail_params=1.5,500'",
         "Try a different failure distribution without editing the model."),
        ("raptor run pump.rbd -r all -o out/",
         "Also write every Raptor 7-format report into out/."),
        ("raptor run pump.rbd --stop failures=10 -n 50",
         "Run until the 10th system failure, 50 times."),
    ],
    "schema": [
        ("raptor schema -o rbds.schema.json",
         "Save the .rbds JSON Schema for an editor or another validator."),
    ],
    "validate": [
        ("raptor validate *.rbd", "Check several models."),
        ("raptor validate pump.rbd --strict -f json",
         "Fail (exit 3) on warnings too; machine-readable."),
    ],
    "sweep": [
        ("raptor sweep pump.rbd --vary 'block:Pump A.fail_params[1]=200:1000:200'"
         " -n 50", "Availability for five values of the Weibull scale."),
        ("raptor sweep pump.rbd --vary 'system.length=1000,2000' --vary "
         "'node:Voter.k=1,2' -f csv", "A 2 x 2 grid as CSV."),
    ],
    "diff": [
        ("raptor diff pump_v1.rbds pump_v2.rbds",
         "List what changed between two versions of a model (layout and "
         "computed columns are left out)."),
        ("raptor diff old.rbd new.rbds --all -f md",
         "Compare an .rbd with an .rbds, ignoring nothing, as Markdown."),
        ("raptor diff a.rbds b.rbds --only elements,links --exit-code -f json",
         "JSON for elements and connections only; exit status 1 when they differ."),
    ],
    "get": [("raptor get pump.rbd system.runs 'block:Pump A.fail_params'",
             "Print current values.")],
    "set": [("raptor set pump.rbd system.runs=200 -o pump200.rbds",
             "Change a setting and save under a new name."),
            ("raptor set pump.rbds system.runs=200 --in-place --backup",
             "Change in place, keeping pump.rbds.bak.")],
    "fev": [
        ("raptor fev pump.rbd --fail 'Pump A'",
         "Fail one block and read what it does to the system."),
        ("raptor fev plane.rbd --phase 3 --fail Hydraulics -f json",
         "The same in phase 3, as JSON."),
        ("raptor fev plane.rbd --script session.fev --log fevlog.txt",
         "Replay a click script; every `write` line adds one block to the "
         "log, as Write To Failure Effects Log does."),
    ],
    "step": [
        ("raptor step pump.rbd --events 10 -f text",
         "The first ten stops of run 1: time, system state, event-log lines and the blocks that changed."),
        ("raptor step pump.rbd --run 3 --events 5",
         "The same for run 3: runs 1 and 2 are jumped over first, so run 3 sees the random numbers a batch gives it."),
        ("raptor step pump.rbd --break 'Pump A' --until 5000",
         "NDJSON, one object per stop, until Pump A changes status or time 5000 is reached."),
    ],
    "extract": [
        ("raptor extract plant.rbd --hierarchy Cooling --report-hazards",
         "List what crosses the Cooling hierarchy's boundary and what would be done about it."),
        ("raptor extract plant.rbd --hierarchy Cooling -o cooling.rbd --deps system --pools share",
         "Save Cooling as a standalone model that opens in Raptor 7."),
    ],
    "mcp": [
        ("raptor mcp", "Serve MCP over stdio (what an assistant launches)."),
        ("raptor mcp --transport http --port 8765 --token-file ~/.raptor-token",
         "Serve MCP over HTTP on localhost with a bearer token."),
        ("raptor mcp --transport http --port 0 --token-file ~/.raptor-token",
         "Pick a free port; the first line of output is the URL."),
    ],
}

FILES = [
    ("$XDG_CONFIG_HOME/raptor-studio/config.toml",
     "settings file (see raptor-config(5))"),
    ("$XDG_DATA_HOME/raptor-studio/mcp", "default MCP server workspace"),
]

ENVIRONMENT = [
    ("RAPTOR_DEBUG", "same as --debug"),
    ("RAPTOR_DEV_FEATURES", "same as --dev-features"),
    ("RAPTOR_CONFIG", "path of the settings file (same as --config)"),
    ("RAPTOR_WORKSPACE", "default for --workspace"),
    ("RAPTOR_SEED", "salt for every random stream (same as --seed)"),
    ("NO_COLOR", "disable colour output"),
    ("XDG_CONFIG_HOME, XDG_DATA_HOME", "base directories"),
]


def _esc(text: str) -> str:
    """Escape for roff: backslashes, and a leading dot/quote."""
    text = text.replace("\\", "\\\\").replace("-", "\\-")
    return re.sub(r"^([.'])", r"\\&\1", text, flags=re.M)


def _bold(s: str) -> str:
    return f"\\fB{_esc(s)}\\fR"


def _ital(s: str) -> str:
    return f"\\fI{_esc(s)}\\fR"


def _option_lines(parser: argparse.ArgumentParser) -> list[str]:
    out: list[str] = []
    for group in parser._action_groups:                 # noqa: SLF001
        acts = [a for a in group._group_actions         # noqa: SLF001
                if a.option_strings and a.help != argparse.SUPPRESS
                and not isinstance(a, argparse._HelpAction)]  # noqa: SLF001
        if not acts:
            continue
        title = (group.title or "options") if group.title not in (
            "options", "optional arguments") else "options"
        out.append(f".SS {_esc(title.capitalize())}")
        for a in acts:
            names = ", ".join(_bold(o) for o in a.option_strings)
            metavar = a.metavar if a.metavar else (
                a.dest.upper() if a.nargs != 0 and a.const is None
                and not isinstance(a, (argparse._StoreTrueAction,  # noqa: SLF001
                                       argparse._StoreFalseAction,  # noqa: SLF001
                                       argparse._CountAction,       # noqa: SLF001
                                       argparse._VersionAction)) else "")  # noqa: SLF001
            if a.choices and not a.metavar:
                metavar = "{" + "|".join(str(c) for c in a.choices) + "}"
            head = f"{names} {_ital(str(metavar))}" if metavar else names
            help_ = (a.help or "").replace("%(default)s", str(a.default))
            out += [".TP", head, _esc(help_)]
    return out


def _synopsis(progress: str, parser: argparse.ArgumentParser) -> str:
    usage = parser.format_usage().replace("usage: ", "").strip()
    usage = re.sub(r"\s+", " ", usage)
    return _bold(progress) + " " + _esc(usage[len(progress):].strip())


def page(name: str, parser: argparse.ArgumentParser, *, section: int = 1,
         prog: str, description: str, see_also: list[str],
         examples: list[tuple[str, str]] | None = None) -> str:
    title = name.upper()
    out = [f'.TH "{title}" {section} "{DATE}" "RAPTOR Studio" '
           f'"RAPTOR Studio Manual"',
           ".SH NAME",
           f"{_esc(name)} \\- {_esc(description.split(chr(10))[0])}",
           ".SH SYNOPSIS", _synopsis(prog, parser),
           ".SH DESCRIPTION", _esc(description)]
    opts = _option_lines(parser)
    if opts:
        out += [".SH OPTIONS", *opts]
    if examples:
        out.append(".SH EXAMPLES")
        for cmd, why in examples:
            out += [".PP", _esc(why), ".RS", ".nf", _bold(cmd), ".fi", ".RE"]
    return "\n".join(out) + "\n" + _tail(see_also)


def _tail(see_also: list[str]) -> str:
    return "\n".join(
        [".SH SEE ALSO",
         ", ".join(f"{_bold(s.split('(')[0])}({s.split('(')[1][:-1]})"
                   for s in see_also), ""])


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, Any]:
    for a in parser._actions:                          # noqa: SLF001
        choices = getattr(a, "choices", None)
        if isinstance(choices, dict):
            return choices
    return {}


def generate() -> dict[str, str]:
    """{filename: roff text} for every page."""
    from raptor.cli.main import build_parser
    root = build_parser()
    subs = _subparsers(root)
    pages: dict[str, str] = {}
    seen: set[int] = set()
    names = []
    for n in subs:
        if id(subs[n]) not in seen:
            seen.add(id(subs[n]))
            names.append(n)
    cmd_refs = [f"raptor-{n}(1)" for n in names]

    top = page("raptor", root, prog="raptor",
               description=root.description or "", examples=EXAMPLES["raptor"],
               see_also=cmd_refs[:6] + ["raptor-config(5)"])
    top += _exit_and_env()
    pages["raptor.1"] = top
    for n in names:
        sp = subs[n]
        desc = (sp.description or sp.format_usage()).strip()
        text = page(f"raptor-{n}", sp, prog=f"raptor {n}", description=desc,
                    examples=EXAMPLES.get(n),
                    see_also=["raptor(1)", "raptor-set-paths(7)"]
                    if n in ("run", "sweep", "get", "set") else ["raptor(1)"])
        if n == "fev":
            text += _fev_script_section()
        if n == "diff":
            text += _diff_exit_section()
        if n in ("run", "sweep", "validate", "mcp"):
            text += _exit_and_env()
        pages[f"raptor-{n}.1"] = text
    pages["raptor-set-paths.7"] = _set_paths_page()
    pages["raptor-config.5"] = _config_page()
    return pages


def _fev_script_section() -> str:
    from raptor.api.analyse import FEV_SCRIPT_ACTIONS
    out = [".SH SCRIPT ACTIONS",
           "One action per line in a \\fB--script\\fR file; blank lines and text "
           "after \\fB#\\fR are ignored. The names are the elements' names or "
           "numeric ids."]
    for verb, text in FEV_SCRIPT_ACTIONS.items():
        out += [".TP", _bold(verb), _esc(text)]
    return "\n".join(out) + "\n"


def _diff_exit_section() -> str:
    out = [".SH EXIT STATUS"]
    for code, text in (
            ("0", "success (with --exit-code: the models do not differ)"),
            ("1", "with --exit-code: the models differ (as git diff "
                  "--exit-code); otherwise a runtime error"),
            (str(X.USAGE), "usage error, or a model file that cannot be read")):
        out += [".TP", _bold(code), _esc(text)]
    return "\n".join(out) + "\n"


def _exit_and_env() -> str:
    out = [".SH EXIT STATUS"]
    for code, text in sorted(X.DESCRIPTIONS.items()):
        out += [".TP", _bold(str(code)), _esc(text)]
    out.append(".SH ENVIRONMENT")
    for var, text in ENVIRONMENT:
        out += [".TP", _bold(var), _esc(text)]
    out.append(".SH FILES")
    for f, text in FILES:
        out += [".TP", _bold(f), _esc(text)]
    return "\n".join(out) + "\n"


def _set_paths_page() -> str:
    from raptor.api.paths import SYSTEM_FIELDS, help_text
    body = help_text()
    out = ['.TH "RAPTOR-SET-PATHS" 7 "' + DATE + '" "RAPTOR Studio" '
           '"RAPTOR Studio Manual"',
           ".SH NAME", "raptor\\-set\\-paths \\- how to name a model field",
           ".SH DESCRIPTION",
           _esc("The --set options of raptor run, raptor sweep and "
                "raptor compare, and the arguments of raptor get and raptor "
                "set, name a model field with a path of the form "
                "kind:selector.field and give a value with =.")]
    out += [".nf", _esc(body), ".fi",
            ".SH SYSTEM FIELDS", ", ".join(sorted(SYSTEM_FIELDS)).replace(
                "-", "\\-")]
    return "\n".join(out) + "\n" + _tail(["raptor(1)", "raptor-run(1)"])


def _config_page() -> str:
    out = ['.TH "RAPTOR-CONFIG" 5 "' + DATE + '" "RAPTOR Studio" '
           '"RAPTOR Studio Manual"',
           ".SH NAME", "raptor\\-config \\- the settings of the raptor command",
           ".SH DESCRIPTION",
           _esc("Settings are looked up in this order, highest first: the "
                "command line, the environment, the settings file, the "
                "model's own settings, the built-in default. The settings "
                "file is TOML; its tables are [run], [output], [mcp] and "
                "[dev], and a key is the long name of the option without "
                "the leading dashes."),
           ".SH EXAMPLE", ".nf",
           "[run]\nruns = 200\njobs = 4\n\n[mcp]\nport = 8765", ".fi"]
    return "\n".join(out) + "\n" + _tail(["raptor(1)", "raptor-run(1)"])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m raptor.cli.manpages OUTDIR", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    for name, text in generate().items():
        (out / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(generate())} pages to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
