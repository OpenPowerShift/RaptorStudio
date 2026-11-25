"""The `raptor` entry point: parser, dispatch, error-to-exit-code mapping."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.exitcodes import CliError

PROG = "raptor"

#: Names accepted as the first argument. Anything else that is not an option
#: and not an existing file is a usage error ("unknown command").
COMMANDS = (
    "gui", "run", "simulate", "validate", "advise", "explain", "schema", "info", "describe", "get", "set",
    "convert", "sweep", "compare", "diff", "estimate", "report", "tables", "render",
    "spf", "fev", "step", "extract", "analytic", "whatif", "sensitivity", "recommend",
    "optimise", "fit", "library", "edit", "mcp", "config",
    "completion", "doctor", "help", "register-file-types", "history",
    "health", "declassify", "tags", "import-table",
)


def _version_text() -> str:
    import platform
    import raptor
    return (f"raptor {raptor.__version__} "
            f"(python {platform.python_version()})")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description="RAPTOR Studio: reliability block diagram simulation. "
                    "With no command, or just a model file, opens the "
                    "desktop application.",
        epilog="Run 'raptor help TOPIC' for set-paths, formats, exit-codes. "
               "Run 'raptor COMMAND --help' for a command.")
    g = p.add_argument_group("global options")
    g.add_argument("-V", "--version", action="version",
                   version=_version_text())
    g.add_argument("-v", "--verbose", action="count", default=0,
                   help="more detail on stderr (repeat: -vv)")
    g.add_argument("-q", "--quiet", action="store_true",
                   help="errors only")
    g.add_argument("--log-level", choices=("debug", "info", "warning",
                                           "error"), metavar="LEVEL",
                   help="debug|info|warning|error (default warning)")
    g.add_argument("--log-file", metavar="PATH",
                   help="also write the log to PATH")
    g.add_argument("--debug", action="store_true",
                   help="debug tracing and a native-crash backtrace "
                        "(same as RAPTOR_DEBUG=1)")
    g.add_argument("--config", metavar="PATH",
                   help="settings file (default "
                        "$XDG_CONFIG_HOME/raptor-studio/config.toml)")
    g.add_argument("--no-config", action="store_true",
                   help="ignore the settings file and RAPTOR_* defaults")
    g.add_argument("--color", choices=("auto", "always", "never"),
                   default="auto", help="colour output (honours NO_COLOR)")
    g.add_argument("--dev-features", metavar="LIST",
                   help="enable developer options: comma list, 'all' or "
                        "'none' (same as RAPTOR_DEV_FEATURES)")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    _register(sub)
    _fill_common_help(p)
    return p


#: Help for the options many commands share, where a command gives none:
#: written once so `--help` and the man pages always explain them.
COMMON_HELP = {
    "format": "output format (default %(default)s)",
    "seed": ("salt every random stream (default: the fixed seed "
             "'raptor run' uses, so the numbers match a plain run)"),
    "jobs": ("cores to run on (default %(default)s). Any 2 or more give "
             "the same numbers; they differ, equally validly, from 1"),
    "trials": "runs per point (default: the model's own)",
}


def _fill_common_help(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:                      # noqa: SLF001
        if action.option_strings and not action.help and action.dest in COMMON_HELP:
            action.help = COMMON_HELP[action.dest]
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for child in dict.fromkeys(choices.values()):
                _fill_common_help(child)


def _register(sub: Any) -> None:
    from raptor.cli import (cmd_gui, cmd_info, cmd_misc, cmd_run, cmd_tools,
                            cmd_mcp, cmd_analytic, cmd_advise, cmd_scenarios,
                            cmd_optimise, cmd_library, cmd_edit, cmd_tags,
                            cmd_import_table)
    for mod in (cmd_gui, cmd_run, cmd_info, cmd_advise, cmd_scenarios,
                cmd_optimise, cmd_library, cmd_edit, cmd_tags,
                cmd_import_table, cmd_tools, cmd_mcp, cmd_misc, cmd_analytic):
        mod.add_parser(sub)


def _setup_logging(args: argparse.Namespace) -> None:
    level = args.log_level
    if level is None:
        level = "error" if args.quiet else (
            "debug" if args.verbose >= 2 else
            "info" if args.verbose == 1 else "warning")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(level=getattr(logging, level.upper()),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=handlers, force=True)


def _apply_environment(args: argparse.Namespace) -> None:
    if args.debug:
        os.environ["RAPTOR_DEBUG"] = "1"
        os.environ["RAPTOR_GUI_DEBUG"] = "1"
    if args.dev_features is not None:
        os.environ["RAPTOR_DEV_FEATURES"] = args.dev_features
    if args.no_config:
        os.environ["RAPTOR_NO_CONFIG"] = "1"
    if args.config:
        os.environ["RAPTOR_CONFIG"] = args.config


def _legacy_switches(argv: list[str]) -> list[str]:
    """Translate Raptor 7's slash switches (`/sd`, `/d`, `/dev`,
    `/C130`, `/password-*`) into this command line, only when no file of
    that name exists. `/password-*` is accepted and ignored (nothing here is
    password protected)."""
    table = {"/sd": ["--sim-details"], "/d": ["--diagnostics"],
             "/dev": ["--dev"], "/C130": ["--mission-file"]}
    out: list[str] = []
    for a in argv:
        if a.startswith("/") and not os.path.exists(a):
            if a.startswith("/password"):
                eprint_once("note: /password is not needed; ignored")
                continue
            if a in table:
                out.extend(table[a])
                continue
        out.append(a)
    return out


_noted: set[str] = set()


def eprint_once(msg: str) -> None:
    if msg not in _noted:
        _noted.add(msg)
        print(msg, file=sys.stderr)


#: global options that take a value as the next word
_GLOBAL_VALUE_OPTIONS = frozenset({"--log-level", "--log-file", "--config",
                                   "--color", "--dev-features"})


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = _legacy_switches(argv)
    parser = build_parser()

    # No command and no argument: the GUI. A first argument that is neither
    # an option nor a command is a model file (open it in the GUI) or an
    # error (a mistyped command).
    # the command word: skip a global option's value (`--dev-features
    # sim_details run M` read "sim_details" as the command)
    words = [a for i, a in enumerate(argv) if not a.startswith("-")
             and not (i and argv[i - 1] in _GLOBAL_VALUE_OPTIONS)]
    first = words[0] if words else None
    only_globals = not words
    if first is not None and first not in COMMANDS:
        if os.path.isfile(first):
            i = argv.index(first)
            argv = argv[:i] + ["gui"] + argv[i:]
        else:
            print(f"{PROG}: unknown command or missing file {first!r}\n"
                  f"try '{PROG} --help'", file=sys.stderr)
            return X.USAGE
    elif first is None and only_globals and not _is_help_or_version(argv):
        argv = argv + ["gui"]
    if first in COMMANDS and first not in ("run", "simulate") and "--json" in argv \
            and "mcp" not in argv[:argv.index("--json")]:
        # argparse refused it with the top-level usage, which says nothing
        # about where JSON output is
        hint = ("use -f json" if _has_json_format(parser, first)
                else "this command has no JSON output")
        print(f"{PROG} {first}: there is no --json option here; {hint} "
              f"(see '{PROG} {first} --help')", file=sys.stderr)
        return X.USAGE

    passthrough: list[str] | None = None
    if first == "mcp":
        # 'raptor mcp ...' hands every option after 'mcp' to the MCP server
        # unchanged (its parser is the one place those options live).
        i = argv.index("mcp")
        passthrough = argv[i + 1:]
        argv = argv[:i + 1]

    args = parser.parse_args(argv)
    if passthrough is not None:
        args.rest = passthrough
    if args.command is None:
        args = parser.parse_args(argv + ["gui"])
    _apply_environment(args)
    _setup_logging(args)
    try:
        code = int(args.func(args) or 0)
        # Flush here, where a closed pipe is caught: output still buffered
        # at exit failed in the interpreter instead ("Exception ignored
        # while flushing sys.stdout", exit 120) after `raptor info | head`
        sys.stdout.flush()
        return code
    except CliError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted", file=sys.stderr)
        return X.INTERRUPTED
    except BrokenPipeError:
        # `raptor ... | head`: not an error. Point stdout at devnull so the
        # interpreter's exit flush does not raise again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return X.OK
    except NotImplementedError as exc:
        print(f"{PROG}: the model cannot be run: {exc}", file=sys.stderr)
        return X.REFUSED
    except Exception as exc:                      # noqa: BLE001
        from raptor.api.build import AmbiguousName, ElementNotFound
        from raptor.core.input_rules import InputRuleViolation
        if isinstance(exc, (ElementNotFound, AmbiguousName, InputRuleViolation)):
            # a misspelt or ambiguous name, or a value the input rules
            # refuse (-t 0, -n 99999999999), is a bad command line, not a
            # crash: no "use -vv for the traceback"
            print(f"{PROG}: {exc}", file=sys.stderr)
            return X.USAGE
        if args.verbose >= 2 or args.debug:
            raise
        print(f"{PROG}: error: {exc} (use -vv for the traceback)",
              file=sys.stderr)
        return X.RUNTIME


def _has_json_format(parser: Any, command: str) -> bool:
    """Whether `command` takes `-f json`."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices.get(command)
            return sub is not None and any(
                "-f" in a.option_strings and "json" in (a.choices or ())
                for a in sub._actions)
    return False


def _is_help_or_version(argv: list[str]) -> bool:
    return any(a in ("-h", "--help", "-V", "--version") for a in argv)


if __name__ == "__main__":
    raise SystemExit(main())
