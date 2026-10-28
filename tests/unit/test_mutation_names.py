"""Every edit key a DiagramView mutator pushes has an arm in the ModelEditor
-- and no arm is dead.

The keys are free-text strings pushed as `ParamsCmd(self, "what", ...)`; the
editor (raptor/gui/model_edit.py) performs them. A key with no arm used to
fall off the end of a 257-line if-chain and silently discard the user's
edit; the editor now raises `UnknownEditKey`, and this test catches the
mismatch at test time instead of at a user's keyboard.

It parses `diagram.py`'s AST for the pushed strings and checks them against
the editor's own table, so nothing here is a hand-kept copy that can rot.
"""
from __future__ import annotations

import ast
import pathlib

from raptor.gui.model_edit import PM_OPTION_ATTRS, ModelEditor

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
SRC = (REPO / "raptor" / "gui" / "ui" / "diagram.py").read_text(
    encoding="utf-8")
TREE = ast.parse(SRC)

#: Pushed through f-strings, so the AST scan cannot see them:
#:   f"event {field}"      -> event dist / param / stream
#:   f"{which} LDT params" -> pre / post LDT params (and LDT dist)
#:   f"block pm {key}"     -> the five PM option checkboxes
DYNAMIC = ({"event dist", "event param", "event stream",
            "pre LDT params", "post LDT params",
            "pre LDT dist", "post LDT dist"}
           | {f"block pm {k}" for k in PM_OPTION_ATTRS})
#: Reached by other commands than ParamsCmd (StreamCmd et al.).
OTHER_COMMANDS = {"stream edit"}


def _pushed() -> set[str]:
    out = set()
    for node in ast.walk(TREE):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ParamsCmd" and len(node.args) > 1):
            continue
        what = node.args[1]
        if isinstance(what, ast.Constant) and isinstance(what.value, str):
            out.add(what.value)
    return out


EDITOR = ModelEditor(host=None)  # type: ignore[arg-type]  # no state needed
PUSHED = _pushed()


def test_the_scan_finds_the_mutators():
    """If the AST scan silently found nothing, the other tests would pass
    vacuously."""
    assert len(PUSHED) >= 25, sorted(PUSHED)


def test_every_pushed_what_has_an_arm():
    unhandled = sorted(w for w in PUSHED | DYNAMIC | OTHER_COMMANDS
                       if not EDITOR.handles(w))
    assert not unhandled, (
        f"edit keys with no ModelEditor arm: {unhandled}. Either the edit "
        f"would raise UnknownEditKey for users or the key is misspelled.")


def test_no_arm_is_dead():
    """An arm that nothing pushes hides a missing mutator."""
    reachable = PUSHED | DYNAMIC | OTHER_COMMANDS
    dead = sorted(ModelEditor.known_keys() - reachable)
    assert not dead, f"ModelEditor arms nothing pushes: {dead}"
