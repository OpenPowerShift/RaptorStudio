"""Rate definitions and expression resolution.

Provides list_rates, add_rate, update_rate, delete_rate, and
resolve_value, which wraps ``raptor.gui.ui.expr_eval.resolve`` (which
has no Qt imports).

``RbdFile.rates`` entries use the documented schema (see
``raptor/core/fileio/rbd.py`` and ``docs/RBDS_FORMAT.md``)::

    {"name": str, "expr": str, "value": float, "description": str}

``expr_eval._rate_value`` reads ``r["value"]`` as a float, so ``value``
must always be the resolved number — the expression text lives in
``expr``.  A rate's own expression may reference another rate
(``"@Base * 1.4"``), resolved against the other rates' already-computed
values, so chains work.

Every numeric field accepted by build_from_spec, add_block, and
update_element may be a number OR an expression string; when it is a
string, resolve_value is called to get the float, which is stored in the
model, and a formula entry is recorded in ``RbdFile.formulas``.
"""
from __future__ import annotations

from typing import Any

from raptor.api.workspace import Workspace
# build.py imports rates lazily (inside functions), so this top-level
# import is safe and keeps one shared BuildError class.
from raptor.api.build import BuildError


# ---------------------------------------------------------------------------
# Rate operations
# ---------------------------------------------------------------------------

def find_rate(rates: list[dict], name: str) -> int | None:
    """Index of the rate called *name*, matched case-INSENSITIVELY.

    One owner for rate identity. The GUI used to do its own
    duplicate-name check with a case-SENSITIVE set membership test, so
    the dialog would happily create `Highway` beside `highway`; this
    function then refused `highway` ("already exists") and
    `delete_rate("highway")` removed whichever of the two came first in
    the list -- possibly the other one. ``rate_name_conflict`` is the
    same rule shaped for the dialog's needs.
    """
    target = name.strip().lower()
    for i, rate in enumerate(rates):
        if rate.get("name", "").strip().lower() == target:
            return i
    return None


def rate_name_conflict(rates: list[dict], name: str, *,
                       exclude: dict | None = None) -> str | None:
    """The existing rate whose name collides with *name*, else ``None``.

    *exclude* skips a single entry -- the row being renamed, which of
    course collides with itself. Returns the existing rate's name as
    stored (not lower-cased), so a caller can show the user what it
    actually collided with.
    """
    target = name.strip().lower()
    for rate in rates:
        if exclude is not None and rate is exclude:
            continue
        if rate.get("name", "").strip().lower() == target:
            return str(rate.get("name", ""))
    return None


def prune_formulas(rates: list[dict], formulas: dict | None) -> dict:
    """*formulas* without entries pointing at a rate that no longer exists.

    A formula entry is the PROVENANCE of a stored float --
    ``{"rate": name, "value": float}`` -- so removing a rate must take
    its provenance with it, or a stale ``@Name`` reference resurfaces the
    next time that parameter is edited. That is exactly why
    ``delete_rate`` has always purged.

    Expressed as a function of the SURVIVING rates rather than of one
    removed name, so it is idempotent and a new delete site cannot forget
    it. Matching is case-insensitive, like :func:`find_rate`, because an
    entry records the name as the user typed it: ``@highway`` and
    ``@Highway`` are the same rate, and the old purge -- an exact
    ``==`` against the removed name -- missed the mismatched-case half of
    those.

    Entries of the ``{"expr": text}`` kind are untouched: they carry no
    rate reference to dangle.
    """
    if not formulas:
        return {}
    live = {str(r.get("name", "")).strip().lower() for r in (rates or [])}
    return {
        key: val for key, val in formulas.items()
        if not (isinstance(val, dict)
                and val.get("rate") is not None
                and str(val["rate"]).strip().lower() not in live)
    }


def make_rate_entry(
    name: str,
    expr: str,
    description: str = "",
    existing: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a documented ``RbdFile.rates`` entry, resolving *expr* to a
    float against *existing* (the other rates)."""
    if name is None or not str(name).strip():
        raise BuildError("rate name must be non-empty")
    from raptor.api.limits import (
        MAX_RATE_DESCRIPTION, MAX_RATE_NAME,
    )
    if len(str(name).strip()) > MAX_RATE_NAME:
        raise BuildError(
            f"rate name is too long; the limit is {MAX_RATE_NAME} characters")
    if expr is None:
        raise BuildError(f"rate {name!r} needs an expression")
    if len(str(description or "")) > MAX_RATE_DESCRIPTION:
        raise BuildError(
            f"rate description is too long; the limit is "
            f"{MAX_RATE_DESCRIPTION} characters")
    value = resolve_value(expr, existing)
    return {
        "name": str(name).strip(),
        "expr": str(expr),
        "value": float(value),
        "description": str(description or ""),
    }


def list_rates(workspace: Workspace, model_id: str) -> str:
    """Return JSON string of all rate definitions."""
    import json
    handle = workspace.get(model_id)
    rates = handle.rbd.rates or []
    return json.dumps(rates, indent=2)


def add_rate(
    workspace: Workspace,
    model_id: str,
    name: str,
    value: str,
    *,
    comment: str = "",
    description: str | None = None,
) -> dict:
    """Add a rate definition.  *value* is an expression string like
    ``0.00015 * 20000 / 8760  # basis`` (stored as the entry's ``expr``).
    *comment*/*description* become the entry's human-readable
    ``description``."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    if rbd.rates is None:
        rbd.rates = []
    from raptor.api.limits import MAX_RATES
    if len(rbd.rates) >= MAX_RATES:
        raise BuildError(f"rate limit reached ({MAX_RATES})")

    if find_rate(rbd.rates, name) is not None:
        raise BuildError(f"Rate {name!r} already exists")

    desc = description if description is not None else comment
    entry = make_rate_entry(name, value, desc, rbd.rates)
    rbd.rates.append(entry)
    workspace.save(model_id)
    return entry


def update_rate(
    workspace: Workspace,
    model_id: str,
    name: str,
    value: str,
    *,
    comment: str | None = None,
    description: str | None = None,
) -> dict:
    """Update an existing rate definition."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    if not rbd.rates:
        raise BuildError(f"No rates defined; cannot update {name!r}")

    idx = find_rate(rbd.rates, name)
    if idx is None:
        raise BuildError(f"Rate {name!r} not found")

    others = [r for j, r in enumerate(rbd.rates) if j != idx]
    old = rbd.rates[idx]
    if description is not None:
        desc = description
    elif comment is not None:
        desc = comment
    else:
        desc = old.get("description", "")
    entry = make_rate_entry(old["name"], value, desc, others)
    rbd.rates[idx] = entry
    # The fields linked to it (and rates defined from it) take the new value.
    propagate_rates(rbd)
    workspace.save(model_id)
    return rbd.rates[idx]


def delete_rate(workspace: Workspace, model_id: str, name: str) -> dict:
    """Delete a rate definition (and any formula provenance that linked
    to it, so a stale ``@Name`` reference can't resurface)."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    if not rbd.rates:
        raise BuildError(f"No rates defined; cannot delete {name!r}")

    idx = find_rate(rbd.rates, name)
    if idx is None:
        raise BuildError(f"Rate {name!r} not found")
    rbd.rates.pop(idx)

    # DELEGATED to `prune_formulas`, which is also what the GUI's delete
    # path calls. This used to be an inline exact-match loop against the
    # one removed name; sharing the rule means the GUI cannot leave behind
    # the corruption this prevents, and the surviving-rate formulation is
    # case-insensitive where the old loop was not.
    rbd.formulas = prune_formulas(rbd.rates, rbd.formulas)

    workspace.save(model_id)
    return {"deleted": name}


# ---------------------------------------------------------------------------
# Expression resolution
# ---------------------------------------------------------------------------

def resolve_value(text: Any, rates: list[dict] | None = None) -> float:
    """Resolve a numeric expression string to a float.

    Wraps ``raptor.gui.ui.expr_eval.resolve`` (which has no Qt imports).
    *rates* is ``RbdFile.rates`` (list of dicts with documented
    ``name``/``value`` keys); ``@RateName`` references — including
    chained ones — resolve against those values.  A plain number passes
    straight through.
    """
    if isinstance(text, bool):
        raise BuildError(f"cannot resolve boolean {text!r} as a value")
    if isinstance(text, (int, float)):
        return float(text)
    from raptor.gui.ui.expr_eval import resolve
    return resolve(str(text), rates)


def is_expression(text: Any) -> bool:
    """Return True if *text* is an expression (not a plain number)."""
    if not isinstance(text, str):
        return False
    from raptor.gui.ui.expr_eval import is_plain_number
    return not is_plain_number(text)


# ---------------------------------------------------------------------------
# Propagation: a rate's new value reaches every field linked to it
# ---------------------------------------------------------------------------

#: A formula key's kind -> the section its element lives in.
_KIND_SECTION = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                 "event": "EVENT_DATA", "link": "LINK_DATA",
                 "system": "SYSTEM_DATA"}

#: The Inspector keys a block's PM / pre- / post-LDT parameters by a
#: prefix of its own ("block:3:PMParam1"); the file's columns are
#: "Param1#3" / "Param1#1" / "Param1#2".
_PREFIXED_PARAMS = {"PMParam": "#3", "PreLDTParam": "#1", "PostLDTParam": "#2"}


def _formula_column(col: str) -> str:
    for prefix, suffix in _PREFIXED_PARAMS.items():
        if col.startswith(prefix) and col[len(prefix):].isdigit():
            return f"Param{col[len(prefix):]}{suffix}"
    return col


def _recompute_rates(rates: list[dict]) -> None:
    """Each rate's value from its expression, repeated until nothing moves
    (a rate may name one defined after it). A cycle stops after
    len(rates) passes, one step stale -- the bound `expr_eval` documents."""
    for _ in range(len(rates) + 1):
        moved = False
        for r in rates:
            others = [o for o in rates if o is not r]
            try:
                value = resolve_value(r.get("expr", r.get("value")), others)
            except (BuildError, ValueError, TypeError):
                continue      # (an ExpressionError is a ValueError)
            if value != r.get("value"):
                r["value"] = value
                moved = True
        if not moved:
            return


def propagate_rates(rbd: Any) -> int:
    """Recompute every rate from its expression (rates defined from other
    rates, e.g. ``@GenMTBF * 40``, included), then rewrite every field
    linked to a rate from its formula record. Returns how many field
    values changed.

    Editing a rate used to change only the rate: the fields linked to it
    kept the old number (the stored float is what the engine reads), so
    the Inspector showed ``@GenMTBF`` beside a value computed from the
    previous one, and a run used the old figure. This is a function of the
    rates and formulas alone, so undoing a rate edit is restoring the old
    library and propagating again."""
    from raptor.api.build import store_numeric
    rates = getattr(rbd, "rates", None) or []
    _recompute_rates(rates)
    changed = 0
    for key, record in (getattr(rbd, "formulas", None) or {}).items():
        try:
            kind, elem, col = key.split(":", 2)
        except ValueError:
            continue
        section = _KIND_SECTION.get(kind)
        sec = rbd.sections.get(section) if section else None
        if sec is None:
            continue
        text = (f"@{record['rate']}" if "rate" in record
                else record.get("expr"))
        if text is None:
            continue
        try:
            value = resolve_value(text, rates)
        except (BuildError, ValueError, TypeError):
            continue
        column = _formula_column(col)
        row = next((r for r in sec.rows
                    if kind == "system" or str(r.get("ID")) == elem), None)
        if row is None or column not in row:
            continue
        before = row[column]
        store_numeric(row, column, value)
        record["value"] = value
        changed += row[column] != before
    return changed
