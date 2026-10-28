#!/usr/bin/env python3
"""Snapshot of the whole ribbon: every QAction's properties and the exact
page / group / button layout.

The docs gate (`manual-reference.adoc`) checks that tab, group, control,
tooltip and shortcut TEXT did not move. It cannot see whether a button is
large or small, checkable, initially checked, enabled before/after a model
opens, what shortcut CONTEXT it has, or which actions exist but are not on
the ribbon. This can, so a structural refactor of the ribbon (moving it to
data) is checked against the behaviour it started with.

    python tools/ribbon_snapshot.py            # diff against the stored snapshot
    python tools/ribbon_snapshot.py --write    # (re)write the snapshot

The stored file is tests/unit/data/ribbon_snapshot.json; the unit test
tests/unit/test_ribbon_snapshot.py runs the same comparison.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "tests" / "unit" / "data" / "ribbon_snapshot.json"
FIXTURE = REPO / "tests" / "golden" / "TwoBlockRedundancy" / "input.rbd"


def _action_props(a) -> dict:
    return {
        "text": a.text(),
        "tooltip": a.toolTip(),
        "shortcuts": [s.toString() for s in a.shortcuts()],
        "context": int(a.shortcutContext().value)
        if hasattr(a.shortcutContext(), "value") else int(a.shortcutContext()),
        "checkable": a.isCheckable(),
        "checked": a.isChecked(),
        "has_icon": not a.icon().isNull(),
        "has_menu": a.menu() is not None,
    }


def _all_actions(win) -> dict:
    """Every QAction reachable as an attribute of the window, or held in a
    dict / list attribute of them (pool actions, analysis toggles, ...)."""
    from PySide6.QtGui import QAction
    out: dict[str, QAction] = {}
    for name in sorted(vars(win)):
        v = getattr(win, name, None)
        if isinstance(v, QAction):
            out[name] = v
        elif isinstance(v, dict):
            for k, x in v.items():
                if isinstance(x, QAction):
                    out[f"{name}[{k}]"] = x
    return out


def _walk_ribbon(win) -> list:
    from PySide6.QtWidgets import QToolButton, QWidget
    from raptor.gui.widgets.ribbon import RibbonButton
    pages = []
    ribbon = win._ribbon
    for i, page in enumerate(ribbon._pages):
        groups = []
        for g in page._groups:
            items = []
            for widget, size in g._items:
                d = _describe_item(widget, RibbonButton, QToolButton, QWidget)
                # The DECLARED size: the shown one depends on window width.
                d["large"] = size == "large"
                items.append(d)
            groups.append({"title": g._label.text(), "items": items})
        pages.append({"title": ribbon.tab_bar.tabText(i), "groups": groups})
    return pages


def _describe_item(w, RibbonButton, QToolButton, QWidget) -> dict:
    if isinstance(w, RibbonButton):
        return {"kind": "action", "text": w.property("ribbonActionText")}
    d = {"kind": "widget", "class": type(w).__name__}
    if isinstance(w, QToolButton):
        d["text"] = w.text()
    kids = [c for c in w.findChildren(QWidget)
            if not c.objectName().startswith("qt_")]
    d["children"] = sorted(type(c).__name__ for c in kids)
    return d


#: Settings the snapshot is only valid at their **defaults**, because the
#: ribbon's structure or enabled-state depends on them.
#:
#: `execution/default_animate` -- the Run tab's live-sim action starts checked
#: or unchecked depending on a persisted preference, and several tests toggle
#: it without restoring it.
#:
#: `files/recent` -- this one is new with the POOLED Recent-menu actions. The
#: menu holds `_recent_clear_action` ("Clear list") and a separator, both
#: enabled only when the list is non-empty, and `_recent_empty_action` ("No
#: recent files") only when it is empty. **Any test that opens a model writes
#: this list** (`_open_path` -> `_remember_recent`), and `_recent_list()`
#: keeps any entry whose path still exists -- which is every
#: `tests/golden/*/input.rbd` a test just opened. So the snapshot silently
#: depended on which tests happened to share the process, and under `pytest -n`
#: it passed or failed by worker composition. The pooled actions are why this
#: is observable at all: before them the Recent menu contributed no rows to
#: the snapshot.
#:
#: Reset here rather than only in the test so `--write` is correct even when
#: run from a session that has already opened a model.
RESET_SETTINGS = ("execution/default_animate", "files/recent")


def _reset_settings() -> None:
    from raptor.gui.ui import settings
    for key in RESET_SETTINGS:
        settings.remove(key)


def take(isolate: bool = True) -> dict:
    """`isolate=False` when the caller (pytest's conftest) has already
    redirected QSettings for the session."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if isolate:
        from PySide6.QtCore import QSettings
        tmp = tempfile.mkdtemp()
        QSettings.setPath(QSettings.Format.IniFormat,
                          QSettings.Scope.UserScope, tmp)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    _reset_settings()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1600, 900)
    win.show()
    app.processEvents()
    actions = _all_actions(win)
    snap: dict = {
        "actions": {n: _action_props(a) for n, a in actions.items()},
        "enabled": {"initial": {n: a.isEnabled()
                                for n, a in actions.items()}},
        "ribbon": _walk_ribbon(win),
    }
    win._load_into_app(load_rbd(str(FIXTURE)), str(FIXTURE))
    app.processEvents()
    actions = _all_actions(win)
    snap["enabled"]["after_load"] = {n: a.isEnabled()
                                     for n, a in actions.items()}
    win._dirty = False
    win._close_current_file()
    app.processEvents()
    snap["enabled"]["after_close"] = {n: a.isEnabled()
                                      for n, a in actions.items()}
    win._dirty = False
    win.close()
    return snap


def diff(old: dict, new: dict) -> list[str]:
    out: list[str] = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a:
                    out.append(f"+ {path}/{k}: {json.dumps(b[k])[:100]}")
                elif k not in b:
                    out.append(f"- {path}/{k}: {json.dumps(a[k])[:100]}")
                else:
                    walk(a[k], b[k], f"{path}/{k}")
        elif isinstance(a, list) and isinstance(b, list) and \
                len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i}]")
        elif a != b:
            out.append(f"~ {path}: {json.dumps(a)[:80]} -> "
                       f"{json.dumps(b)[:80]}")

    walk(old, new, "")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    snap = take()
    if args.write:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n")
        print(f"wrote {SNAPSHOT.relative_to(REPO)} "
              f"({len(snap['actions'])} actions, "
              f"{len(snap['ribbon'])} pages)")
        return 0
    old = json.loads(SNAPSHOT.read_text())
    d = diff(old, snap)
    for line in d:
        print(line)
    print(f"{len(d)} difference(s)")
    return 1 if d else 0


if __name__ == "__main__":
    raise SystemExit(main())
