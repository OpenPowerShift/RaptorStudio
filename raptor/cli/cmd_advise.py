"""`raptor advise`: what looks wrong or unwise in a model, with reasons."""
from __future__ import annotations

from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, open_model, workspace_for
from raptor.cli.exitcodes import CliError

ADVISE_SCHEMA = "raptor-advise/1"
RULES_SCHEMA = "raptor-advise-rules/1"

_MARK = {"error": "error", "warning": "warning", "advice": "advice", "info": "note"}
_ORDER = ("error", "warning", "advice", "info")


def add_parser(sub: Any) -> None:
    a = sub.add_parser(
        "advise", help="say what looks wrong or unwise in a model",
        description="Check each MODEL for things a reliability engineer would "
                    "question: redundancy that cannot work, placeholder data, "
                    "spares or crews that cannot meet demand, maintenance that "
                    "cannot pay off, too few runs. Nothing is run and nothing "
                    "is changed. Each finding has a stable rule id and says "
                    "whether it is exact or an estimate.")
    a.add_argument("models", nargs="*", metavar="MODEL")
    a.add_argument("-f", "--format", choices=("text", "json", "md"), default="text")
    a.add_argument("--min-severity", choices=_ORDER, default="info",
                   help="hide findings below this level (default: show all)")
    a.add_argument("--only", metavar="IDS",
                   help="comma list of rule ids or prefixes to run, e.g. M-SPR,M-PM-003")
    a.add_argument("--skip", metavar="IDS",
                   help="comma list of rule ids or prefixes to leave out")
    a.add_argument("--why", action="store_true",
                   help="print the reasoning under each finding (text format)")
    a.add_argument("--strict", action="store_true",
                   help="exit 3 on warnings as well as errors")
    a.add_argument("--list-rules", action="store_true",
                   help="list every rule and exit")
    a.set_defaults(func=advise_command)


def _ids(text: str | None) -> list[str] | None:
    if not text:
        return None
    return [t.strip() for t in text.split(",") if t.strip()]


def _list_rules(fmt: str) -> int:
    from raptor.api.advisor import list_rules
    rules = [r.describe() for r in list_rules()]
    if fmt == "json":
        emit_json({"schema": RULES_SCHEMA, "rules": rules}, None)
    elif fmt == "md":
        print("| Rule | Level | Method | Title |\n|---|---|---|---|")
        for r in rules:
            print(f"| {r['id']} | {r['severity']} | {r['basis']} | {r['title']} |")
    else:
        for r in rules:
            print(f"{r['id']:<10} {r['severity']:<8} {r['basis']:<12} {r['title']}")
    return X.OK


def _text(doc: dict, why: bool) -> None:
    c = doc["counts"]
    print(f"{doc['model']}: {c['error']} error(s), {c['warning']} warning(s), "
          f"{c['advice']} suggestion(s), {c['info']} note(s)")
    for f in doc["findings"]:
        tag = "" if f["basis"] == "exact" else " (estimate)" if f["basis"] == "heuristic" else " (statistical)"
        print(f"  {_MARK[f['severity']]}: {f['rule']}{tag}: {f['message']}")
        if why:
            print(f"      why: {f['why']}")
        for fix in f["fixes"]:
            how = "; ".join(fix["assignments"]) or "in the application"
            print(f"      fix: {fix['label']} [{how}]")


def _markdown(doc: dict) -> None:
    print(f"## {doc['model']}\n")
    if not doc["findings"]:
        print("No findings.\n")
        return
    print("| Level | Rule | Finding | Method |\n|---|---|---|---|")
    for f in doc["findings"]:
        message = f["message"].replace("|", "\\|")
        print(f"| {_MARK[f['severity']]} | {f['rule']} | {message} | {f['basis']} |")
    print()


def advise_command(args: Any) -> int:
    if args.list_rules:
        return _list_rules(args.format)
    if not args.models:
        raise CliError("advise needs at least one MODEL (or --list-rules)", X.USAGE)
    from raptor.api.advisor import advise_model, counts
    from raptor.api.advisor.finding import SEVERITY_RANK
    floor = SEVERITY_RANK[args.min_severity]
    worst = X.OK
    docs: list[dict] = []
    with workspace_for() as ws:
        for path in args.models:
            try:
                mid = open_model(ws, path)
            except CliError as exc:
                docs.append({"model": path, "error": str(exc)})
                worst = max(worst, exc.code)
                continue
            found = advise_model(ws.get(mid).rbd, only=_ids(args.only), skip=_ids(args.skip))
            tally = counts(found)
            shown = [f for f in found if SEVERITY_RANK[f.severity] >= floor]
            docs.append({"model": path, "counts": tally,
                         "findings": [f.to_dict() for f in shown]})
            if tally["error"] or (args.strict and tally["warning"]):
                worst = max(worst, X.INVALID)
    if args.format == "json":
        emit_json({"schema": ADVISE_SCHEMA, "results": docs}, None)
        return worst
    for d in docs:
        if "error" in d:
            print(f"{d['model']}: {d['error']}")
        elif args.format == "md":
            _markdown(d)
        else:
            _text(d, args.why)
    return worst
