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

    h = sub.add_parser(
        "history", help="list, snapshot or restore saved versions of a model",
        description="Version history kept by the desktop application on save. "
                    "Snapshots live in the user's private state folder, not "
                    "beside the model.")
    h.add_argument("model", metavar="MODEL")
    h.add_argument("--snapshot", action="store_true",
                   help="record the file as it is now")
    h.add_argument("--label", metavar="TEXT",
                   help="label the snapshot (with --snapshot); an unchanged "
                        "file names its newest version if that has no "
                        "label, otherwise a new version is added")
    h.add_argument("--description", metavar="TEXT",
                   help="Markdown description for the snapshot (with "
                        "--snapshot), or with --describe for that version")
    h.add_argument("--description-file", metavar="PATH",
                   help="read the Markdown description from a file ('-' "
                        "is standard input)")
    h.add_argument("--describe", metavar="SNAPSHOT",
                   help="set (or with an empty description, clear) the "
                        "description of an existing snapshot")
    h.add_argument("--restore", metavar="SNAPSHOT",
                   help="write this snapshot to --output (or --force over MODEL)")
    h.add_argument("-o", "--output", metavar="PATH",
                   help="destination for --restore")
    h.add_argument("--force", action="store_true",
                   help="allow --restore to overwrite MODEL or an existing "
                        "--output")
    h.add_argument("-f", "--format", choices=("text", "json"), default="text")
    h.set_defaults(func=history_command)
    hh = sub.add_parser(
        "health", help="summarise a model's size, completeness and issues",
        description="Project health: element counts, validation findings, "
                    "unnamed elements, duplicate block names and unused pools.")
    hh.add_argument("model", metavar="MODEL")
    hh.add_argument("-f", "--format", choices=("text", "json"), default="text")
    hh.set_defaults(func=health_command)

    dd = sub.add_parser(
        "declassify", help="write a share-safe copy with names/notes removed",
        description="Write a declassified .rbds: element names become type "
                    "labels, and comments, chosen annotations, project "
                    "metadata, rate names/formulas and Advisor notes can be "
                    "removed. IDs, links and data are kept.")
    dd.add_argument("model", metavar="MODEL")
    dd.add_argument("output", metavar="OUTPUT")
    dd.add_argument("--annotations", choices=("keep", "text", "all"),
                    default="all",
                    help="remove no annotations, text only, or every shape")
    dd.add_argument("--keep-names", action="store_true",
                    help="keep element and hierarchy names")
    dd.add_argument("--keep-comments", action="store_true",
                    help="keep element comments")
    dd.add_argument("--keep-metadata", action="store_true",
                    help="keep the print title block and project preferences")
    dd.add_argument("--keep-advisor", action="store_true",
                    help="keep the reasons attached to dismissed Advisor findings")
    dd.add_argument("--keep-rates", action="store_true",
                    help="keep the rate library and field formulas")
    dd.set_defaults(func=declassify_command)

    r = sub.add_parser(
        "register-file-types",
        help="open .rbds and .rbd files with RAPTOR Studio (this user only)",
        description="Associate .rbds and .rbd model files with RAPTOR Studio for "
                    "the current user, so double-clicking one opens it: for a pip "
                    "or venv install, which has no installer to do it. .rbd is "
                    "Raptor 7's extension: it is left alone when another program "
                    "already opens it, unless --force.")
    r.add_argument("--unregister", action="store_true",
                   help="undo the registration")
    r.add_argument("--force", action="store_true",
                   help="take .rbd even when another program opens it")
    r.add_argument("--dry-run", action="store_true",
                   help="list the changes without making them")
    r.set_defaults(func=register_file_types_command)


def _history_check_options(args: Any) -> None:
    if args.snapshot and args.restore:
        raise CliError("--snapshot and --restore are separate actions", X.USAGE)
    if args.label and not args.snapshot:
        raise CliError("--label needs --snapshot", X.USAGE)
    if args.description is not None and args.description_file:
        raise CliError("use --description or --description-file, not both",
                       X.USAGE)
    has_text = args.description is not None or args.description_file
    if has_text and not (args.snapshot or args.describe):
        raise CliError("--description needs --snapshot or --describe",
                       X.USAGE)
    if args.describe and (args.snapshot or args.restore):
        raise CliError("--describe is a separate action", X.USAGE)
    if args.describe and not has_text:
        raise CliError("--describe needs --description or "
                       "--description-file", X.USAGE)
    if args.output and not args.restore:
        raise CliError("-o/--output needs --restore", X.USAGE)
    if args.force and not args.restore:
        raise CliError("--force needs --restore", X.USAGE)


def _description_text(args: Any) -> str:
    if args.description_file:
        try:
            if args.description_file == "-":
                return sys.stdin.read()
            return Path(args.description_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CliError(f"could not read {args.description_file}: {exc}",
                           X.USAGE) from exc
    return args.description or ""


def _history_describe(model: Path, args: Any) -> int:
    from raptor.api import history
    if not model.is_file():
        raise CliError(f"file not found: {model}", X.USAGE)
    text = _description_text(args)
    if not history.set_description(model, args.describe, text):
        raise CliError(f"no version {args.describe!r} for {model}", X.USAGE)
    print(f"described {args.describe}" if text.strip()
          else f"cleared the description of {args.describe}")
    return X.OK


def _history_snapshot(model: Path, label: str, description: str = "") -> int:
    from raptor.api import history
    from raptor.core.history import HistoryError
    if not model.is_file():
        raise CliError(f"file not found: {model}", X.USAGE)
    try:
        shot = history.record(model, reason="manual", label=label,
                              description=description)
    except HistoryError as exc:
        raise CliError(str(exc), X.RUNTIME) from exc
    if shot is None:
        print("nothing to record: the file is identical to the latest "
              "version")
        return X.OK
    suffix = f"  {shot.label}" if shot.label else ""
    print(f"recorded {shot.id} ({shot.size_text()}){suffix}")
    return X.OK


def _history_restore(model: Path, args: Any) -> int:
    from raptor.api import history
    from raptor.core.history import HistoryError
    if not args.output and not args.force:
        raise CliError("--restore needs -o/--output, or --force to "
                       "overwrite the model", X.USAGE)
    if args.output and Path(args.output).exists() and not args.force:
        raise CliError(f"{args.output} exists; add --force to overwrite",
                       X.USAGE)
    try:
        target = history.restore(model, args.restore, args.output or model)
    except FileNotFoundError as exc:
        raise CliError(str(exc), X.USAGE) from exc
    except HistoryError as exc:
        raise CliError(str(exc), X.RUNTIME) from exc
    except OSError as exc:
        raise CliError(f"could not write {args.output or model}: {exc}",
                       X.OUTPUT) from exc
    print(f"restored {target}")
    return X.OK


def _history_list(model: Path, fmt: str) -> int:
    from raptor.api import history
    from raptor.core.history import file_digest
    if not model.is_file():
        raise CliError(f"file not found: {model}", X.USAGE)
    from raptor.core.history import format_bytes
    shots = history.snapshots(model)
    total, limit = history.usage(model), history.soft_limit()
    digest = file_digest(model)
    current = [s.id for s in shots if s.sha256 and s.sha256 == digest]
    if fmt == "json":
        emit_json({"schema": "raptor-history/1", "model": str(model),
                   "current": digest or None,
                   "current_id": current[0] if current else None,
                   "total_bytes": total, "soft_limit_bytes": limit,
                   "snapshots": [
                       {"id": s.id, "created": s.created, "reason": s.reason,
                        "label": s.label, "description": s.description,
                        "bytes": s.bytes, "sha256": s.sha256,
                        "current": s.id in current}
                       for s in shots]}, None)
        return X.OK
    if not shots:
        print(f"no saved versions of {model}")
        return X.OK
    for shot in shots:
        label = f"  {shot.label}" if shot.label else ""
        if shot.description:
            label += "  [described]"
        mark = "*" if shot.id in current else " "
        print(f"{mark} {shot.id}  {shot.time_text()}  {shot.reason:<8} "
              f"{shot.size_text():>9}{label}")
    print(f"{len(shots)} version(s), {format_bytes(total)} on disk "
          "(identical files are stored once)")
    if total > limit:
        print(f"warning: this history is over {format_bytes(limit)}. Versions "
              "you saved are never deleted automatically; remove ones you no "
              "longer need in the app's Version History (Delete version).")
    return X.OK


def history_command(args: Any) -> int:
    _history_check_options(args)
    model = Path(args.model)
    if args.snapshot:
        return _history_snapshot(model, args.label or "",
                                 _description_text(args))
    if args.describe:
        return _history_describe(model, args)
    if args.restore:
        return _history_restore(model, args)
    return _history_list(model, args.format)


def health_command(args: Any) -> int:
    from raptor.api import health
    try:
        rbd = health.load(args.model)
    except Exception as exc:  # noqa: BLE001
        raise CliError(f"could not read {args.model}: {exc}", X.USAGE) from exc
    report = health.report(rbd)
    if args.format == "json":
        emit_json({"schema": "raptor-health/1", "model": args.model,
                   "counts": report.counts,
                   "annotations": report.annotations,
                   "text_annotations": report.text_annotations,
                   "comments": report.comments,
                   "formulas": report.formulas, "rates": report.rates,
                   "issues": [{"severity": i.severity, "message": i.message,
                               "element": i.element} for i in report.issues]},
                  None)
        return X.INVALID if report.errors else X.OK
    print(report.summary())
    if not report.issues:
        print("no issues")
    for issue in report.issues:
        print(f"{issue.severity.upper():<7} {issue.text()}")
    return X.INVALID if report.errors else X.OK


def declassify_command(args: Any) -> int:
    from raptor.api import health
    from raptor.core.redact import RedactOptions, write_share_copy
    try:
        rbd = health.load(args.model)
    except Exception as exc:  # noqa: BLE001
        raise CliError(f"could not read {args.model}: {exc}", X.USAGE) from exc
    options = RedactOptions(
        names=not args.keep_names,
        comments=not args.keep_comments,
        annotations=args.annotations,
        metadata=not args.keep_metadata,
        advisor=not args.keep_advisor,
        rates=not args.keep_rates)
    try:
        dest, report = write_share_copy(
            rbd, args.output, options, source=args.model)
    except ValueError as exc:
        raise CliError(str(exc), X.USAGE) from exc
    except Exception as exc:  # noqa: BLE001
        raise CliError(f"could not write {args.output}: {exc}", X.OUTPUT) from exc
    print(f"wrote {dest}")
    print(report.summary())
    if report.risks:
        print("residual risk:")
        for risk in report.risks:
            print(f"- {risk}")
    return X.OK


def register_file_types_command(args: Any) -> int:
    from raptor.api.file_types import plan
    todo = plan(unregister=args.unregister, force=args.force)
    for note in todo.notes:
        print(note)
    if not todo.steps:
        if not todo.notes:
            print("nothing to do: RAPTOR Studio is not registered for this user")
        return X.OK
    for step in todo.steps:
        print(("would " if args.dry_run else "") + step.text)
        if not args.dry_run:
            try:
                step.run()
            except OSError as exc:
                raise CliError(f"could not {step.text}: {exc}", X.RUNTIME) from exc
    for ext in todo.skipped:
        print(f"left alone: {ext}; give --force to take it", file=sys.stderr)
    if not args.dry_run:
        print("unregistered" if args.unregister else
              "registered: double-click a .rbds file to open it in RAPTOR Studio")
    return X.OK


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
              "markdown, none\nmodel files: .rbd (legacy format), "
              ".rbds (JSON; see docs/RBDS_FORMAT.md)\n"
              "report files (-r): legacy text format, CRLF line ends")
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


def config_command(args: Any) -> int:  # noqa: C901
    from PySide6.QtCore import QCoreApplication, QSettings
    # The GUI writes QSettings under this identity; a bare QSettings in
    # the CLI used to land in "Unknown Organization.conf".
    QCoreApplication.setOrganizationName("OpenPowerShift")
    QCoreApplication.setApplicationName("RAPTOR Studio")
    if args.action == "path":
        print(QSettings().fileName())
        return X.OK
    from raptor.gui.ui import settings
    if args.action == "list":
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
        if args.key == "appearance/ui_scale":
            from raptor.gui.resources.scale import SCALE_MAX, SCALE_MIN
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise CliError(
                    f"appearance/ui_scale must be an integer "
                    f"{SCALE_MIN}..{SCALE_MAX}", X.USAGE) from None
            if not SCALE_MIN <= v <= SCALE_MAX:
                raise CliError(
                    f"appearance/ui_scale must be an integer "
                    f"{SCALE_MIN}..{SCALE_MAX}", X.USAGE)
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

_NATIVE_DISPLAYS = {"Windows": "Windows", "Darwin": "macOS"}


def display_status(system: str, env: Any) -> tuple[bool, str]:
    """`(ok, detail)` for the doctor's display line.

    Only Linux desktops (X11 or Wayland) advertise a display through
    `DISPLAY` / `WAYLAND_DISPLAY`. Windows and macOS always have a native
    one, so reporting "none" there told users something was wrong when the
    application displayed normally."""
    native = _NATIVE_DISPLAYS.get(system)
    if native is not None:
        return True, f"native ({native})"
    detail = env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")
    return bool(detail), detail or "none"


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
        from raptor.mcp import sdk
        return sdk.problem() is None, sdk.problem() or sdk.installed()
    check("mcp SDK", mcp_sdk)

    def engine():
        from raptor.api.workspace import Workspace
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Workspace(d)
        return True, "workspace writable"
    check("workspace", engine)
    check("display", lambda: display_status(platform.system(), os.environ))
    if args.format == "json":
        emit_json({"schema": "raptor-doctor/1", "checks": checks}, None)
    else:
        for c in checks:
            print(f"{'ok ' if c['ok'] else 'BAD'}  {c['check']}: {c['detail']}")
    return X.OK if all(c["ok"] for c in checks if c["check"] != "display") \
        else X.RUNTIME
