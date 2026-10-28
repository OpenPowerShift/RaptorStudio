"""`raptor help | config | completion | doctor`."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json
from raptor.cli.exitcodes import CliError

TOPICS = ("set-paths", "formats", "exit-codes")


def add_parser(sub: Any) -> None:
    h = sub.add_parser("help", help="help for a command or a topic",
                       description="Show help for a COMMAND, or a topic: "
                                   + ", ".join(TOPICS) + ".")
    h.add_argument("topic", nargs="?", metavar="TOPIC_OR_COMMAND")
    h.set_defaults(func=help_command)

    c = sub.add_parser("config", help="show or change application settings",
                       description="List, read or change the settings the "
                                   "desktop application keeps (QSettings) "
                                   "and the CLI defaults.")
    c.add_argument("action", choices=("list", "get", "set", "unset", "path"))
    c.add_argument("key", nargs="?")
    c.add_argument("value", nargs="?")
    c.set_defaults(func=config_command)

    z = sub.add_parser("completion", help="print a shell completion script",
                       description="Print a completion script for SHELL; "
                                   "e.g. eval \"$(raptor completion bash)\".")
    z.add_argument("shell", choices=("bash", "zsh", "fish"))
    z.set_defaults(func=completion_command)

    d = sub.add_parser("doctor", help="check the installation",
                       description="Check Python, Qt, fonts, write access "
                                   "and the MCP SDK.")
    d.add_argument("-f", "--format", choices=("text", "json"), default="text")
    d.set_defaults(func=doctor_command)


def help_command(args: Any) -> int:
    from raptor.cli.main import build_parser
    t = args.topic
    if t is None:
        build_parser().print_help()
        return X.OK
    if t == "set-paths":
        from raptor.api.paths import help_text
        print(help_text())
        return X.OK
    if t == "exit-codes":
        for code, text in sorted(X.DESCRIPTIONS.items()):
            print(f"{code:>4}  {text}")
        return X.OK
    if t == "formats":
        print("summary formats (-f/--format): text, json, ndjson, csv, "
              "markdown, none\nmodel files: .rbd (Raptor 7), "
              ".rbds (JSON; see docs/RBDS_FORMAT.md)\n"
              "report files (-r): Raptor 7 text, CRLF line ends")
        return X.OK
    parser = build_parser()
    for action in parser._actions:                  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if choices and t in choices:
            choices[t].print_help()
            return X.OK
    raise CliError(f"no help topic or command {t!r}", X.USAGE)


# ---- config ---------------------------------------------------------------

def config_path() -> Path:
    env = os.environ.get("RAPTOR_CONFIG")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "raptor-studio" / "config.toml"


def config_command(args: Any) -> int:
    if args.action == "path":
        print(config_path())
        return X.OK
    from raptor.gui.ui import settings
    if args.action == "list":
        from PySide6.QtCore import QSettings
        s = QSettings()
        for k in sorted(s.allKeys()):
            print(f"{k}={s.value(k)}")
        return X.OK
    if not args.key:
        raise CliError(f"config {args.action} needs a KEY", X.USAGE)
    if args.action == "get":
        if not settings.contains(args.key):
            raise CliError(f"no setting {args.key!r}", X.USAGE)
        print(settings.get(args.key))
    elif args.action == "set":
        if args.value is None:
            raise CliError("config set needs KEY VALUE", X.USAGE)
        v: Any = args.value
        low = v.lower()
        v = True if low == "true" else False if low == "false" else v
        settings.set_value(args.key, v)
    elif args.action == "unset":
        settings.remove(args.key)
    return X.OK


# ---- completion -----------------------------------------------------------

def _subcommands() -> dict[str, list[str]]:
    """{command: [its long options]} read from the live parser, so the
    completion scripts cannot drift from the commands."""
    from raptor.cli.main import build_parser
    parser = build_parser()
    out: dict[str, list[str]] = {}
    for action in parser._actions:                  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for name, sp in choices.items():
                opts = sorted({o for a in sp._actions   # noqa: SLF001
                               for o in a.option_strings
                               if o.startswith("--")})
                out[name] = opts
    return out


def completion_command(args: Any) -> int:
    from raptor.cli.main import build_parser
    cmds = _subcommands()
    glob = sorted({o for a in build_parser()._actions      # noqa: SLF001
                   for o in a.option_strings if o.startswith("--")})
    names = " ".join(sorted(cmds))
    if args.shell == "bash":
        cases = "\n".join(
            f'    {n}) opts="{" ".join(o)}" ;;' for n, o in sorted(cmds.items()))
        print(f'''_raptor() {{
  local cur="${{COMP_WORDS[COMP_CWORD]}}" cmd="" opts="" i
  for ((i=1;i<COMP_CWORD;i++)); do
    case "${{COMP_WORDS[i]}}" in -*) ;; *) cmd="${{COMP_WORDS[i]}}"; break;; esac
  done
  if [[ -z "$cmd" ]]; then
    COMPREPLY=( $(compgen -W "{names} {' '.join(glob)}" -- "$cur") ); return
  fi
  case "$cmd" in
{cases}
  esac
  COMPREPLY=( $(compgen -f -W "$opts" -- "$cur") )
}}
complete -o filenames -F _raptor raptor''')
    elif args.shell == "zsh":
        print(f'''#compdef raptor
_raptor() {{
  local -a cmds
  cmds=({names})
  if (( CURRENT == 2 )); then
    _describe 'command' cmds
  else
    _files
  fi
}}
_raptor "$@"''')
    else:
        lines = ["complete -c raptor -f"]
        for n in sorted(cmds):
            lines.append(f"complete -c raptor -n '__fish_use_subcommand' "
                         f"-a {n}")
            for o in cmds[n]:
                lines.append(f"complete -c raptor -n '__fish_seen_subcommand_"
                             f"from {n}' -l {o[2:]}")
        print("\n".join(lines))
    return X.OK


# ---- doctor ---------------------------------------------------------------

def doctor_command(args: Any) -> int:
    import platform
    checks: list[dict[str, Any]] = []

    def check(name: str, fn: Any) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:                    # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        checks.append({"check": name, "ok": ok, "detail": detail})

    import raptor
    check("raptor", lambda: (True, raptor.__version__))
    check("python", lambda: (sys.version_info >= (3, 12),
                             platform.python_version()))

    def qt():
        import PySide6
        return True, PySide6.__version__
    check("PySide6 (GUI)", qt)

    def mcp_sdk():
        import mcp                                  # noqa: F401
        from importlib.metadata import version
        return True, version("mcp")
    check("mcp SDK", mcp_sdk)

    def engine():
        from raptor.api.workspace import Workspace
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Workspace(d)
        return True, "workspace writable"
    check("workspace", engine)
    check("display", lambda: (bool(os.environ.get("DISPLAY")
                                   or os.environ.get("WAYLAND_DISPLAY")),
                              os.environ.get("WAYLAND_DISPLAY")
                              or os.environ.get("DISPLAY") or "none"))
    if args.format == "json":
        emit_json({"schema": "raptor-doctor/1", "checks": checks}, None)
    else:
        for c in checks:
            print(f"{'ok ' if c['ok'] else 'BAD'}  {c['check']}: {c['detail']}")
    return X.OK if all(c["ok"] for c in checks if c["check"] != "display") \
        else X.RUNTIME
