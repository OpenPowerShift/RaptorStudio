"""The Raptor Studio clipboard payload: copy in one model, paste in another.

A copy is a `clipboard_plan.Buffer` (file rows). To cross a model boundary it
is also written to the system clipboard as JSON under `MIME`, with a plain
text summary beside it so pasting into a text editor is harmless. Pasting
reads it back: another model in the same window, another window, another
running copy of the app.

No Qt here (`diagram.py` owns the QClipboard); everything is a function of
bytes, rows and the target model, so it is tested without a display.

What a pasted block keeps when it lands in a *different* model
--------------------------------------------------------------
A block row names things that live in the model, not on the block. The
safest course is to keep what the target also has and drop what it does not,
saying so, rather than quietly adding definitions to a model the user did not
ask to change:

* spare pool (`SpType` Pooled + `SparePoolName`): kept if the target has a
  pool of that name, else the block takes the target's default sparing;
* repair resource (`Resource1Name`, `NumRes`): same, against RESOURCE_DATA;
* PM trigger (`pmTrig`, `pmTriggered`): same, against TRIGGERS;
* a dependency between elements copied together is remapped to the pasted
  copies (`clipboard_plan.remap_dependencies`, in or out of the model); one
  on an element that was not copied means nothing in another model, so the
  copy becomes independent there. Own-node (-1) and whole-system (-2) stay;
* columns the target does not have (another phase count) are dropped and
  columns the target has that the copy lacks take the target's defaults;
* annotation anchors are cleared (they name source elements).

Pasting into the same model it was copied from changes none of that.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from raptor.gui.clipboard_plan import Buffer, count_elements

MIME = "application/x-raptor-studio-clip"
FORMAT = "raptor-studio-clip"
VERSION = 1

#: Identifies this running copy of the app; a payload from the same process
#: and the same model token is a same-model paste.
PROCESS_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

_KINDS = frozenset({"block", "node", "event", "hier", "link", "annotation"})
_SECTION_OF = {"block": "BLOCK_DATA", "node": "NODE_DATA",
               "event": "EVENT_DATA", "hier": "HIERARCHY_DATA",
               "link": "LINK_DATA"}
#: Cap on what we will parse from a clipboard we do not control.
_MAX_BYTES = 64 * 1024 * 1024
_MAX_ITEMS = 500_000


@dataclass
class Payload:
    buffer: Buffer
    source_name: str = ""
    model_token: str = ""
    process: str = ""


@dataclass
class Adapted:
    buffer: Buffer
    notes: list[str] = field(default_factory=list)


def new_model_token() -> str:
    """A fresh identity for a model as loaded (a file opened twice is two)."""
    return uuid.uuid4().hex


# ------------------------------------------------------------------ encode
def encode(buffer: Buffer, *, source_name: str = "",
           model_token: str = "") -> bytes:
    doc = {"format": FORMAT, "version": VERSION, "source": source_name,
           "model": model_token, "process": PROCESS_TOKEN,
           "items": [[kind, row] for kind, row in buffer]}
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def summary_text(buffer: Buffer, source_name: str = "") -> str:
    """The plain-text flavour: what a text editor shows on paste."""
    counts: dict[str, int] = {}
    for kind, _row in buffer:
        counts[kind] = counts.get(kind, 0) + 1
    plural = {"block": "blocks", "node": "nodes", "event": "events",
              "hier": "hierarchies", "link": "links",
              "annotation": "annotations"}
    single = {"hier": "hierarchy"}
    parts = [f"{n} {plural[k] if n != 1 else single.get(k, k)}"
             for k, n in counts.items()]
    src = f" from {source_name}" if source_name else ""
    return f"Raptor Studio: {', '.join(parts) or 'nothing'}{src}"


# ------------------------------------------------------------------ decode
def decode(data: bytes | bytearray | memoryview | None) -> Payload | None:
    """The payload in `data`, or None for anything that is not a version-1
    Raptor Studio clip. Never raises: the clipboard belongs to the user and
    any program may have put anything on it."""
    if not data or len(data) > _MAX_BYTES:
        return None
    try:
        doc = json.loads(bytes(data).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if (not isinstance(doc, dict) or doc.get("format") != FORMAT
            or doc.get("version") != VERSION):
        return None
    items = doc.get("items")
    if not isinstance(items, list) or not items or len(items) > _MAX_ITEMS:
        return None
    buffer: Buffer = []
    for entry in items:
        if (not isinstance(entry, list) or len(entry) != 2
                or entry[0] not in _KINDS or not isinstance(entry[1], dict)):
            return None
        kind, row = entry
        if kind != "annotation":
            # File rows are text columns; anything else is not ours.
            if not all(isinstance(k, str) and isinstance(v, str)
                       for k, v in row.items()):
                return None
        buffer.append((kind, row))
    if count_elements(buffer) == 0 and not any(
            k == "annotation" for k, _ in buffer):
        return None
    return Payload(buffer=buffer, source_name=str(doc.get("source", "")),
                   model_token=str(doc.get("model", "")),
                   process=str(doc.get("process", "")))


# ------------------------------------------------------------------- adapt
def _rows(rbd: Any, section: str) -> list[dict]:
    sec = rbd.sections.get(section)
    return sec.rows if sec is not None else []


def _template(rbd: Any, section: str) -> dict:
    rows = _rows(rbd, section)
    for r in rows:
        if str(r.get("ID", "")).strip() in ("0", "0.0"):
            return r
    return rows[0] if rows else {}


def _names(rbd: Any, section: str, col: str) -> set[str]:
    return {str(r.get(col, "")) for r in _rows(rbd, section)}


def _num(text: Any) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


class _Target:
    """What the target model defines, and what it did not have."""

    def __init__(self, rbd: Any) -> None:
        self.rbd = rbd
        self.pools = _names(rbd, "SPARE_POOL_DATA", "PoolName")
        self.resources = _names(rbd, "RESOURCE_DATA", "PoolName")
        self.triggers = _names(rbd, "TRIGGERS", "TriggerName")
        self.dropped: dict[str, set[str]] = {}

    def drop(self, what: str, name: str) -> None:
        self.dropped.setdefault(what, set()).add(name)

    def notes(self) -> list[str]:
        out = []
        for what, names in self.dropped.items():
            shown = ", ".join(sorted(n for n in names if n)[:3])
            out.append(f"{what} {shown or '(unnamed)'} is not in this "
                       "model; the copy uses the default")
        return out


def _reset(row: dict, tpl: dict, cols: tuple[str, ...]) -> None:
    for c in cols:
        if c in tpl:
            row[c] = tpl[c]


def _fit_block(row: dict, tpl: dict, t: _Target) -> None:
    if (row.get("SpType") == "Pooled"
            and row.get("SparePoolName") not in t.pools):
        t.drop("spare pool", str(row.get("SparePoolName", "")))
        _reset(row, tpl, ("SpType", "SparePoolName"))
    if (_num(row.get("NumRes")) > 0
            and row.get("Resource1Name") not in t.resources):
        t.drop("resource", str(row.get("Resource1Name", "")))
        _reset(row, tpl, ("NumRes", "Resource1Name", "#Res1Rep",
                          "#Res1pmRep"))
    if _num(row.get("pmTriggered")) and row.get("pmTrig") not in t.triggers:
        t.drop("PM trigger", str(row.get("pmTrig", "")))
        _reset(row, tpl, ("pmTriggered", "pmTrig"))


def _fit_row(kind: str, row: dict, t: _Target) -> dict:
    row = dict(row)
    section = _SECTION_OF.get(kind)
    tpl = _template(t.rbd, section) if section else {}
    if tpl:
        # The target's columns: drop strangers, default the missing.
        row = {c: row.get(c, tpl[c]) for c in tpl}
    if kind == "block":
        _fit_block(row, tpl, t)
    return row


def adapt_for_target(rbd: Any, payload: Payload, *, same_model: bool
                     ) -> Adapted:
    """`payload.buffer` made to fit `rbd` (see the module docstring). A
    same-model paste is returned unchanged."""
    if same_model:
        return Adapted(list(payload.buffer))
    t = _Target(rbd)
    out: Buffer = []
    for kind, row in payload.buffer:
        if kind == "annotation":
            out.append((kind, {k: v for k, v in row.items()
                               if k not in ("anchor", "comment_of")}))
        else:
            out.append((kind, _fit_row(kind, row, t)))
    return Adapted(out, t.notes())


def same_model(payload: Payload, model_token: str) -> bool:
    return (payload.process == PROCESS_TOKEN and bool(model_token)
            and payload.model_token == model_token)
