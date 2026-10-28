"""Raptor 7's input-validation rules, checked against a model's rows.

Raptor 7 refuses bad input where it is typed: each properties dialog checks
its fields when OK is pressed and lists what is wrong (`BlockPropObj`,
`NodeBoxObj`, `EventsBoxObj`, `SystemBoxObj`, `PhaseBoxObj`, `TrigBoxObj`,
`SparePoolsBoxObj`, `ResPoolsBoxObj`), a link is refused
when it is drawn (`LinkObj.CheckValidLink`), and a
run is refused when it starts (`ValidateRBD` and `SendToEngine`). This module holds those rules once, for
every front end:

* the GUI and the API refuse an EDIT that introduces a violation
  (`new_violations`), as Raptor 7's dialog does;
* `validate()` reports every violation in a model, so a file written by
  another tool is still opened (Raptor 7 opens it too) but the problem is
  shown. A `run` violation is an error: Raptor 7 will not start the run. A
  `link` or `dialog` violation is a warning: Raptor 7 opens and runs such a
  file (the corpus has probes built that way, run by Raptor 7 and matched by
  the port) and only refuses the shape or value when it is drawn or typed.

A distribution a picker does not list is not a typed value: Raptor 7 only
leaves it out of the list, and runs such a file (Fixed failure laws are common
in scripted models). The GUI's pickers enforce the lists; the rules here only
refuse Empirical where the file cannot hold its points (a delay, a PM
duration, a phase or a trigger).

Messages are Raptor 7's own text, with its spelling and wording mistakes
corrected and trailing padding removed. Where Raptor 7 has no message because it
simply does not offer a choice (a distribution its list omits, Empirical with
no points), the text is the port's and the rule's comment says so.

Deliberately not checked: the Preferences dialog's save increment and global
units (the port's preferences are its own), and the
capacity table's "FullFlow/n" even share (not a number the user types).
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from raptor.core.distro_rules import RAPTOR7_LISTS

#: `run`: refused when the run starts. `link`: refused when drawn.
#: `dialog`: refused when typed into its dialog. `value`: a stored value no
#: front end can hold or the engine can use (an unknown distribution code, a
#: non-numeric parameter, a control character in text): refused when typed AND
#: an error at run time. Raptor 7 cannot produce these, so the text is the port's.
SEVERITY = {"run": "error", "link": "warning", "dialog": "warning", "value": "error"}

_BIG = 999999999.999999


@dataclass(frozen=True)
class Violation:
    rule: str        # stable id, e.g. "block.pm_stagger_vs_freq"
    kind: str        # run | link | dialog
    element: str     # e.g. "Block 3 'Pump'"
    message: str     # Raptor 7's alert text

    @property
    def key(self) -> tuple[str, str]:
        """Rule and element, without the element's name: renaming a block
        that already had a problem must not make the problem look new."""
        ident, sep, _name = self.element.partition(" '")
        return (self.rule, ident if sep and ident[-1:].isdigit() else self.element)

    @property
    def severity(self) -> str:
        return SEVERITY[self.kind]


# ---------------------------------------------------------------- helpers

#: Characters that must never sit in stored text: C0/DEL controls (a newline or
#: tab would split a row in the legacy `.rbd` text file or a tab-separated log)
#: and invisible/bidirectional formatting that can disguise a name.
_CONTROL_RE = re.compile("[\x00-\x1f\x7f\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff]")


def has_blank(text: str) -> bool:
    """True when *text* holds a space of any kind (plain, no-break, a line or
    tab) or a control/invisible character: a name Raptor 7 would call "blank
    spaces", and one the legacy file cannot hold."""
    return any(ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf", "Zs", "Zl", "Zp")
               for ch in text)


def control_problem(text: str) -> str | None:
    """Why *text* may not be stored (a control or invisible character), else None."""
    m = _CONTROL_RE.search(text)
    if m is None:
        return None
    return f"contains a control or invisible character (U+{ord(m.group()):04X})"

def _num(row: dict, col: str) -> float | None:
    v = row.get(col)
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _int(row: dict, col: str, default: int = 0) -> int:
    x = _num(row, col)
    return default if x is None else int(x)


def _flag(row: dict, col: str) -> bool:
    return str(row.get(col, "")).strip().lower() in ("1", "true", "yes")


def _out_of(x: float | None, lo: float, hi: float) -> bool:
    return x is not None and (x < lo or x > hi)


def _rows(rbd, section: str) -> list[dict]:
    sec = rbd.sections.get(section)
    return [r for r in (sec.rows if sec else []) if str(r.get("ID", "1")) != "0"]


def _all_rows(rbd, section: str) -> list[dict]:
    sec = rbd.sections.get(section)
    return list(sec.rows) if sec else []


def _system(rbd) -> dict:
    return (_all_rows(rbd, "SYSTEM_DATA") or [{}])[0]


def _kind(ref: object) -> str:
    return str(ref or "").replace("RBD", "", 1).lower()


def _link_counts(rbd) -> tuple[dict, dict]:
    """Real link counts: (kind, id) -> number of links in / out."""
    into: dict[tuple[str, str], int] = {}
    out: dict[tuple[str, str], int] = {}
    for link in _rows(rbd, "LINK_DATA"):
        f = (_kind(link.get("FromType")), str(link.get("FromID", "")))
        t = (_kind(link.get("ToType")), str(link.get("ToID", "")))
        out[f] = out.get(f, 0) + 1
        into[t] = into.get(t, 0) + 1
    return into, out


class _Scope:
    """Collects violations for one element of one dialog."""

    def __init__(self, out: list[Violation], prefix: str, kind: str, element: str) -> None:
        self.out, self.prefix, self.kind, self.element = out, prefix, kind, element

    def add(self, rule: str, message: str) -> None:
        self.out.append(Violation(f"{self.prefix}.{rule}", self.kind, self.element, message))

    def outside(self, rule: str, x: float | None, lo: float, hi: float, message: str) -> None:
        if _out_of(x, lo, hi):
            self.add(rule, message)

    def name(self, name: str, blank: str, spaces: str, length: str, rule: str = "name") -> None:
        """The name check every dialog repeats: not blank, no spaces, <= 20."""
        if name == "":
            self.add(rule, blank)
        elif has_blank(name):
            self.add(rule, spaces)
        elif len(name) > 20:
            self.add(rule, length)


# The Block dialog's failure mean (`tempMean`): its
# own CASE, not `diststats`. Point Estimate and the undefined-mean shapes give
# -1, a distribution the CASE does not name (Fixed, None) leaves it at 0.
def _weibull_mean(f1: float, f2: float, f3: float) -> float:
    if f1 < 0.05:
        return -1.0
    if f1 == 1.0 or f1 > 10000000.0:
        return f2 + f3
    x = 1.0 + 1.0 / f1
    y = (1.000000000190015 + 76.18009172947146 / (x + 1.0) - 86.50532032941677 / (x + 2.0)
         + 24.01409824083091 / (x + 3.0) - 1.231739572450155 / (x + 4.0)
         + 0.001208650973866179 / (x + 5.0) - 0.000005395239384953 / (x + 6.0))
    z = (x + 5.5) ** (x + 0.5) * math.exp(-(x + 5.5)) * math.sqrt(2.0 * math.pi) * y / x
    return f2 * z + f3


_DIALOG_MEAN: dict[int, Callable[[float, float, float], float]] = {
    1: lambda a, b, c: a / (a + b) if (a + b) else 0.0,   # Beta
    2: lambda a, b, c: a,                                 # Chi-square
    3: lambda a, b, c: a * b,                             # Binomial
    4: lambda a, b, c: a + b,                             # Exponential (mean + location)
    5: lambda a, b, c: a * b,                             # Erlang
    6: lambda a, b, c: a * b + c,                         # Gamma
    7: lambda a, b, c: a,                                 # Lognormal
    8: lambda a, b, c: a,                                 # Normal
    9: lambda a, b, c: (a + b) / 2.0,                     # Uniform Integer
    10: lambda a, b, c: (a + b) / 2.0,                    # Uniform Real
    11: lambda a, b, c: -1.0 if a <= 1.0 else b / (a - 1.0) + c,   # Pearson 5
    12: lambda a, b, c: -1.0 if b <= 1.0 else c * a / (b - 1.0),   # Pearson 6
    13: lambda a, b, c: a,                                # Poisson
    14: lambda a, b, c: (a + b + c) / 3.0,                # Triangular
    15: _weibull_mean,                                    # Weibull (Lanczos, as the dialog)
    17: lambda a, b, c: -1.0,                             # Point Estimate
    20: lambda a, b, c: c,                                # Exponential Power
    21: lambda a, b, c: b + 0.57721 * a,                  # Extreme Value
    22: lambda a, b, c: b,                                # Laplace
}


def dialog_fail_mean(dist: int, vals: list[float]) -> float:
    """The failure mean the growth/decay limits are checked against. For
    Empirical (16) it is the mean of the points (`fEmpMean`)."""
    if dist == 16:
        return sum(vals) / len(vals) if vals else 0.0
    a, b, c = (list(vals) + [0.0, 0.0, 0.0])[:3]
    f = _DIALOG_MEAN.get(dist)
    return f(a, b, c) if f else 0.0


def _fail_values(row: dict) -> list[float]:
    if _int(row, "FDist") == 16 and row.get("FEmp"):
        return [float(p) for p in str(row["FEmp"]).split()]
    return [_num(row, f"FParam{i}") or 0.0 for i in (1, 2, 3)]


# ---------------------------------------------------------------- run gate

def _check_markers_and_elements(rbd, out: list[Violation]) -> None:
    """`ValidateRBD`'s connectivity checks."""
    if not _rows(rbd, "BLOCK_DATA") and not _rows(rbd, "EVENT_DATA"):
        out.append(Violation("run.no_blocks", "run", "model",
                             "There must be at least 1 block or event to simulate!"))
    into, links_out = _link_counts(rbd)
    for node in _rows(rbd, "NODE_DATA"):
        t, nid, name = _int(node, "Type"), str(node.get("ID")), node.get("Name", "")
        s = _Scope(out, "run", "run", f"Node {nid} '{name}'")
        if into.get(("node", nid), 0) < 1 and t not in (1, 4):
            s.add("node_from", f"To simulate, node '{name}' must connect from something!")
        if links_out.get(("node", nid), 0) < 1 and t not in (3, 5):
            s.add("node_to", f"To simulate, node '{name}' must connect to something!")
    for section, kind, col in (("BLOCK_DATA", "block", "blockName"),
                                     ("EVENT_DATA", "event", "eventName"),
                                     ("HIERARCHY_DATA", "hier", "hierarchyName")):
        for row in _rows(rbd, section):
            oid, name = str(row.get("ID")), row.get(col, "")
            s = _Scope(out, "run", "run", f"{kind.capitalize()} {oid} '{name}'")
            if into.get((kind, oid), 0) < 1:
                s.add(f"{kind}_from", f"To simulate, {kind} '{name}' must connect from something!")
            if links_out.get((kind, oid), 0) < 1:
                s.add(f"{kind}_to", f"To simulate, {kind} '{name}' must connect to something!")


def _term_type(make_system_row: dict) -> int:
    """Termination type as 1/2/3.

    Legacy `.rbd` rows hold the number; the port's `.rbds` SYSTEM_DATA
    stores the name (`"TimeSim"`, `"FailSim"`, `"CycleSim"`). Reading the
    numeric column with `_int` silently turned every named value into
    "time terminated", which hid the cycle-without-phases refusal on any
    `.rbds` file.
    """
    raw = str(make_system_row.get("TerminationType", "")).strip().lower()
    named = {"timesim": 1, "failsim": 2, "cyclesim": 3,
             "time": 1, "failure": 2, "cycle": 3}
    if raw in named:
        return named[raw]
    return _int(make_system_row, "TerminationType", 1)


def _check_termination(make_system_row: dict, s: _Scope) -> None:
    """The run-length fields the System dialog and the Simulation dialog both
    check."""
    s.outside("runs", _num(make_system_row, "NumRuns"), 1, 999999999,
              "Number of runs must be between 1 and 999,999,999!")
    term = _term_type(make_system_row)
    length, start = _num(make_system_row, "TermCondition"), _num(make_system_row, "StartStats")
    ordered = length is None or start is None or length > start
    if term == 1:
        s.outside("time_len", length, 0.000001, _BIG,
                  "Stop simulation time must be between 0.000001 and 999,999,999.999999!")
        if _out_of(start, 0.0, 999999999.999998):
            s.add("time_start",
                  "Start statistics time must be between 0 and 999,999,999.999998!")
        elif not ordered:
            s.add("time_order",
                  "Start statistics time must be less than stop simulation time!")
    elif term in (2, 3):
        word, key = ("failure", "fail") if term == 2 else ("cycle", "cycle")
        s.outside(f"{key}_len", length, 1, 999999999,
                  f"Stop simulation {word} must be between 1 and 999,999,999!")
        if _out_of(start, 0, 999999998):
            s.add(f"{key}_start",
                  f"Start statistics {word} must be between 0 and 999,999,998!")
        elif not ordered:
            s.add(f"{key}_order",
                  f"Start statistics {word} must be less than stop simulation {word}!")


def _check_run_settings_into(rbd, out: list[Violation]) -> None:
    """The run-start rules that read only `SYSTEM_DATA` and the run
    experiment. Split out of `_check_run` so the GUI can re-check JUST
    these after a run-setting edit (trials, termination, an analysis
    tick) instead of re-walking every block and link: an edit to those
    fields cannot break a link, so the whole-model walk would be pure
    cost."""
    s = _Scope(out, "system", "run", "System")
    _check_termination(_system(rbd), s)
    if _term_type(_system(rbd)) == 3 and not _all_rows(rbd, "PHASES"):
        # Raptor 7 says so and runs it time-terminated instead
        # the port refuses rather than change the
        # model behind the user's back.
        s.add("cycle_without_phases", "You cannot use cycle terminated without phases defined.")


def check_run_settings(rbd) -> list[Violation]:
    """The run-start rules an edit to a RUN SETTING can newly break.

    Cheap on purpose: no whole-model walk.  `check()` remains the one
    place every rule runs; this is only for "the user changed the trial
    count -- is the model still runnable?", where re-validating every
    block made changing a spin box cost seconds on a large model.
    """
    out: list[Violation] = []
    _check_run_settings_into(rbd, out)
    _check_experiment(rbd, out)
    return out


def _check_run(rbd, out: list[Violation]) -> None:
    """`ValidateRBD` and the model fields
    `SendToEngine` checks."""
    _check_markers_and_elements(rbd, out)
    _check_run_settings_into(rbd, out)


# ---------------------------------------------------------------- links

_ELEMENT_LINK_RULES = {   # kind -> (out line, in line, "A ...", "to a ...")  Raptor 7
    "block": ("A block", "a block"),
    "event": ("An event", "an event"),
    "hier": ("A hierarchy item", "a hierarchy item"),
}
_MARKER_LINK_RULES = {    # node Type -> (no-in/no-out rule, ..., limit rule)
    1: ("into", "Cannot connect to a Start Marker from any object!",
        "out", "Cannot have more than one connection from a Start Node! Place an ordinary node after the marker and fan out from that node."),
    3: ("out", "Cannot connect to any object from an End Marker!",
        "in", "Cannot have more than one connection to an End Node! Place an ordinary node before the marker and merge there."),
    4: ("into", "Cannot connect to an In Marker from any object!",
        "out", "Cannot have more than one connection from an In Marker! Place an ordinary node after the marker and fan out from that node."),
    5: ("out", "Cannot connect to any object from an Out Marker!",
        "in", "Cannot have more than one connection to an Out Marker! Place an ordinary node before the marker and merge there."),
}


def _check_links(rbd, out: list[Violation]) -> None:
    """`LinkObj.CheckValidLink`: the link shapes
    Raptor 7 will not let you draw."""
    into, links_out = _link_counts(rbd)
    for section, kind, col in (("BLOCK_DATA", "block", "blockName"), ("EVENT_DATA", "event", "eventName"),
                               ("HIERARCHY_DATA", "hier", "hierarchyName")):
        a, to = _ELEMENT_LINK_RULES[kind]
        for row in _rows(rbd, section):
            oid = str(row.get("ID"))
            s = _Scope(out, "link", "link", f"{kind.capitalize()} {oid} '{row.get(col, '')}'")
            if links_out.get((kind, oid), 0) > 1:
                s.add(f"{kind}_out", f"{a} can only connect to one object! Route the branch through a node to fan out.")
            if into.get((kind, oid), 0) > 1:
                s.add(f"{kind}_in", f"Cannot connect more than one link to {to}!")
    for node in _rows(rbd, "NODE_DATA"):
        rule = _MARKER_LINK_RULES.get(_int(node, "Type"))
        if rule is None:
            continue
        nid = str(node.get("ID"))
        n_in, n_out = into.get(("node", nid), 0), links_out.get(("node", nid), 0)
        s = _Scope(out, "link", "link", f"Node {nid} '{node.get('Name', '')}'")
        forbidden, f_msg, limited, l_msg = rule
        if (n_in if forbidden == "into" else n_out):
            s.add(f"marker_{forbidden}", f_msg)
        if (n_out if limited == "out" else n_in) > 1:
            s.add(f"marker_{limited}_limit", l_msg)


# ---------------------------------------------------------------- the Block dialog

def _block_distributions(row: dict, s: _Scope) -> None:
    """Only what the dialog's lists offer. Raptor 7 has no message for an
    unlisted code (it cannot be chosen), so this text is the port's."""
    for col, picker, what in (("FDist", "failure", "failure"), ("RDist", "repair", "repair"),
                              ("Pre-LDTDist", "delay", "pre-repair delay"),
                              ("Post-LDTDist", "delay", "post-repair delay"),
                              ("Dist", "delay", "PM duration")):
        if col not in row or (col == "Dist" and not _flag(row, "usesPM")):
            continue
        code = _int(row, col)
        if picker == "delay" and code == 0:
            code = 19        # a stored 0 is Fixed
        if code == 16 and code not in RAPTOR7_LISTS[picker]:
            # Empirical where the row cannot hold its points. Any other code a
            # list leaves out is only not offered: the pickers enforce the
            # lists, and Raptor 7 runs such a file (Fixed failure laws are
            # common in scripted models), so it is not reported.
            s.add(f"dist_offered.{col}",
                  f"Raptor 7 does not offer Empirical for the {what}: only a block's failure "
                  "and repair laws can hold data points.")
    for col, key, what in (("FDist", "FEmp", "failure"), ("RDist", "REmp", "repair")):
        if _int(row, col) == 16 and not str(row.get(key, "")).split():
            # `GetEmpirical` only stores a table that has points;
            # the port's text. A run error: there is nothing to draw from.
            s.out.append(Violation(f"block.empirical_points.{col}", "run", s.element,
                                   f"The {what} distribution is Empirical but has no data points."))


def _block_sparing(row: dict, s: _Scope) -> None:
    """Custom sparing."""
    if str(row.get("SpType", "")) != "Custom":
        return
    s.outside("stock", _num(row, "Stock"), 0, 999999999,
              "Initial stock must be between 0 and 999,999,999!")
    checks = []
    if _flag(row, "RSO"):
        checks += [("new_spares", "NewSp", 99999, "New spares value must be between 1 and 99,999!"),
                   ("arrive_every", "ArriveEvery", _BIG,
                    "New spares arrival rate value must be between 0.000001 and 999,999,999.999999!")]
    if _flag(row, "SLO"):
        s.outside("stock_level", _num(row, "Level"), 0, 99999,
                  "Stock level must be between 0 and 99,999!")
        checks += [("stock_amount", "Quan", 99999, "Stock order amount must be between 1 and 99,999!"),
                   ("stock_rate", "SLOTime", _BIG,
                    "Stock arrival rate value must be between 0.000001 and 999,999,999.999999!")]
    if _flag(row, "ESO"):
        checks.append(("emer_time", "EmerTime", _BIG,
                       "Emergency spare delay value must be between 0.000001 and 999,999,999.999999!"))
    for rule, col, hi, msg in checks:      # each is `<= 0 OR > hi`
        x = _num(row, col)
        if x is not None and (x <= 0 or x > hi):
            s.add(rule, msg)


def _block_pm(row: dict, s: _Scope) -> None:
    """PM, checked whether or not PM is ticked, as the dialog does."""
    triggered = _flag(row, "pmTriggered")
    freq, stagger = _num(row, "PMFreq"), _num(row, "PMStaggerTime")
    if not triggered:
        s.outside("pm_freq", freq, 0.000001, _BIG,
                  "Frequency of PM must be between 0.000001 and 999,999,999.999999!")
    if _out_of(stagger, 0.0, _BIG):
        s.add("pm_stagger", "PM stagger value must be between 0 and 999,999,999.999999!")
    elif not triggered and freq is not None and stagger is not None and stagger >= freq:
        s.add("pm_stagger_vs_freq", "PM stagger value cannot be greater than or equal to PM frequency!")


_BLOCK_COSTS = (  # BlockPropObj.ReceiveData's costVals[1..13]
    ("Running", "Cost to operate block"),
    ("standby", "Cost of this block being on standby"),
    ("repPerTime", "Cost to repair block"),
    ("repFixed", "Fixed cost to repair block"),
    ("pmCost", "Preventive maintenance of this block"),
    ("pmFixedCost", "Fixed preventive maintenance of this block"),
    ("repHold", "Cost of this block being on hold"),
    ("pmHoldCost", "Cost of this block being on PM hold"),
    ("idle", "Cost of this block being idle"),
    ("donePerTime", "Cost of this block being done"),
    ("initial", "Cost of block"),
    ("perSpare", "Cost of providing a spare for this block"),
    ("emerShipping", "Emergency spare additional cost for this block"),
)

_DEPENDENCY_PERCENTS = (
    ("DepNothing", "Percent operating time"),
    ("DepIdle", "Percent idle time"),
    ("DepPM", "Percent PM time"),
    ("DepFail", "Percent failed time"),
)


def _block_costs_and_dependency(row: dict, s: _Scope) -> None:
    for col, what in _BLOCK_COSTS:
        s.outside(f"cost.{col}", _num(row, col), 0.0, 999999999.99,
                  f"{what} must be between 0 and 999,999,999.99!")
    s.outside("cost.doneFixed", _num(row, "doneFixed"), -999999999.99, 999999999.99,
              "Fixed cost of this block being done must be between -999,999,999.99 and 999,999,999.99!")
    pcts = [_num(row, col) for col, _what in _DEPENDENCY_PERCENTS]
    for x, (col, what) in zip(pcts, _DEPENDENCY_PERCENTS):
        s.outside(f"dep.{col}", x, 0.0, 100.0, f"{what} must be between 0 and 100!")
    s.outside("dep.DepPMThresh", _num(row, "DepPMThresh"), 0.0, 100.0,
              "Life exhausted must be between 0 and 100!")
    known = [x for x in pcts if x is not None]
    if len(known) == len(pcts) and abs(sum(known) - 100.0) > 1e-9:
        s.add("dep.total", "Total percentage time must add up to 100!")


def _block_growth_decay(row: dict, s: _Scope) -> None:
    """Growth/decay against the failure mean."""
    gd = _int(row, "GDType")
    if gd not in (1, 2, 3):
        return
    mean = dialog_fail_mean(_int(row, "FDist"), _fail_values(row))
    rate, limit = _num(row, "GDRate") or 0.0, _num(row, "GDLimit") or 0.0
    low = f"The limit must be between 0.000001 and {mean:.6f}!"
    high = f"The limit must be between {mean:.6f} and 999,999,999.999999!"
    if mean < 0.0:
        s.add("gd.mean", "Selected failure distribution/parameters cannot be used with block degrade!")
    elif gd == 3:
        if rate < 0.0 or rate >= 999999999.999:
            s.add("gd.rate", "The rate must be between 0.000001 and 999,999,999.999999!")
        if limit < 0.0 or limit >= 999999999.999:
            s.add("gd.limit", "The limit must be between 0.000001 and 999,999,999.999999!")
    elif gd == 1 and (rate < -_BIG or rate > 999999999.999):
        s.add("gd.slope", "The slope must be between +/- 999,999,999.999999!")
    elif gd == 2 and rate <= 0.000001:
        s.add("gd.base", "The base must be greater than 0.000001!")
    else:
        falling = rate < 0.0 if gd == 1 else rate < 1.0
        if falling and (limit < 0.000002 or limit > mean):
            s.add("gd.limit_low", low)
        elif not falling and (limit < mean or limit >= _BIG):
            s.add("gd.limit_high", high)


def _gd_limit_fix(falling: bool, limit: float, mean: float) -> float:
    if falling and (limit < 0.000002 or limit > mean):
        return mean / 2.0
    if not falling and (limit < mean or limit >= _BIG):
        return mean * 2.0
    return limit


def gd_corrections(gd_type: int, rate: float, limit: float, mean: float) -> tuple[float, float]:
    """The values Raptor 7's Block dialog puts back when it refuses a
    growth/decay rate or limit: slope 0, base 1,
    and a falling limit of mean/2 or a rising one of mean*2; an asymptotic
    rate and limit both mean/2. Values it would accept come back unchanged.
    The GUI applies them with a type change, since its fields are committed
    one at a time where Raptor 7's dialog is checked at OK."""
    if gd_type == 1:
        rate = 0.0 if (rate < -_BIG or rate > 999999999.999) else rate
        return rate, _gd_limit_fix(rate < 0.0, limit, mean)
    if gd_type == 2:
        rate = 1.0 if rate <= 0.000001 else rate
        return rate, _gd_limit_fix(rate < 1.0, limit, mean)
    if gd_type == 3:
        bad = lambda x: x < 0.0 or x >= 999999999.999  # noqa: E731
        return (mean / 2.0 if bad(rate) else rate), (mean / 2.0 if bad(limit) else limit)
    return rate, limit


def _block_start_standby_resources(row: dict, pools: dict, s: _Scope) -> None:
    start, amt = _int(row, "SimStart"), _num(row, "AmtExhausted")
    if start == 3 and amt is not None and (amt < 0.0 or amt >= 999999999.999):
        s.add("start_life", "The amount of life exhausted must be between 0.0 and 999,999,999.999999!")
    if start == 5:
        s.outside("start_repair", amt, 0.0, 99.999999,
                  "The percent of repair exhausted must be between 0.0 and 99.999999!")
    lo = -999.999 if str(row.get("blockName", "")).startswith(("Bin-", "Timer-")) else 0.0   # {BINS}
    s.outside("sb_stress", _num(row, "SBStress"), lo, 999.999,
              "The standby stress must be between 0 and 999.999!")
    res = str(row.get("Resource1Name", "None"))
    pool = pools.get(res)
    if pool is None or _int(row, "NumRes") < 1:
        return
    have = _int(pool, "NumIn")
    for col, use in (("#Res1Rep", "repair"), ("#Res1pmRep", "PM")):
        asked = _int(row, col)
        if have < asked:
            s.add(f"res.{col}", f"{asked} {res} were requested for {use} from a pool of "
                  f"{have}. Please select {have} or less!")


def _check_block(row: dict, pools: dict, out: list[Violation]) -> None:
    """`BlockPropObj.ReceiveData`."""
    name = str(row.get("blockName", ""))
    s = _Scope(out, "block", "dialog", f"Block {row.get('ID')} '{name}'")
    s.name(name, "'Block Name' field can't be blank or have blank spaces!",
           "'Block Name' field can't be blank or have blank spaces!",
           "'Block Name' must be no greater than 20 characters!")
    _block_distributions(row, s)
    _block_sparing(row, s)
    _block_pm(row, s)
    _block_costs_and_dependency(row, s)
    _block_growth_decay(row, s)
    _block_start_standby_resources(row, pools, s)


# ---------------------------------------------------------------- other dialogs

def _check_event(row: dict, out: list[Violation]) -> None:
    """`EventsBoxObj.ReceiveData`."""
    name = str(row.get("eventName", ""))
    s = _Scope(out, "event", "dialog", f"Event {row.get('ID')} '{name}'")
    s.name(name, "'Event Name' field can't be blank or have blank spaces!",
           "'Event Name' field can't be blank or have blank spaces!",
           "'Event Name' must be no greater than 20 characters!")
    s.outside("probability", _num(row, "failVal"), 0.0, 1.0,
              "Success probability must be between 0 and 1!")
    for col, what in (("InitCost", "Initial cost"), ("OpCost", "Cost per success"),
                            ("RepCost", "Cost per failure")):
        s.outside(f"cost.{col}", _num(row, col), 0.0, 999999999.0,
                  f"{what} must be between 0 and 999,999,999!")


def _check_node(row: dict, n_in: int, out: list[Violation]) -> None:
    """`NodeBoxObj.ReceiveData`, for a connect node."""
    if _int(row, "Type") != 2:
        return
    name = str(row.get("Name", ""))
    s = _Scope(out, "node", "dialog", f"Node {row.get('ID')} '{name}'")
    if has_blank(name):     # a node may be unnamed
        s.add("name", "'Node Name' field can't be blank or have blank spaces!")
    elif len(name) > 20:
        s.add("name", "'Node Name' must be no greater than 20 characters!")
    elif name.lower() in ("start", "end"):
        s.add("name_reserved",
              f"The name {name.lower()} is a reserved node name!")
    k, kstar = _num(row, "k"), _num(row, "k*")
    if k is not None and k > 9999:
        s.add("k_max", "Good paths must be between 1 and 9,999!")
    elif k is not None and k != -1 and n_in >= 1 and (k < 1 or k > n_in):
        # -1 is the stored "never set" (a one-path node; FactoryDemo's 60K);
        # the dialog only checks a k that is changed.
        s.add("k_range", f"Good paths must be between 1 and {n_in}!")
    if not _flag(row, "Cold") or kstar is None:
        return
    if k is not None and k > kstar:
        s.add("k_vs_kstar", "Maximum number running (k* on Standby tab) must be greater than or "
              "equal to good paths (k on General tab)!")
    elif kstar > n_in:
        s.add("kstar_vs_n", "Maximum number running (k* on Standby tab) must be less than or "
              "equal to total paths!")


def _check_standby_link(link: dict, s: _Scope) -> None:
    """The standby table, for a link into a
    cold-standby node."""
    s.outside("cold_priority", _num(link, "ColdPri"), 1, 99, "Priority must be between 1 and 99!")
    p = _num(link, "AProb")
    if p is not None and (p <= 0.0 or p > 1.0):
        s.add("auto_probability",
              "Auto switch probability must be greater than 0 and less than or equal to 1!")
    s.outside("auto_time", _num(link, "AutoTime"), 0.0, 999999999.0,
              "Auto switch time must be between 0 and 999,999,999!")
    man = _num(link, "ManTime")
    if man != -1.0:      # -1: "No Manual Switching"
        s.outside("manual_time", man, 0.0, 999999999.0,
                  "Manual switch time must be between 0 and 999,999,999!")


def _check_capacity_link(link: dict, generated: float, s: _Scope) -> None:
    """The capacity tables. -1 and -2 are
    "FullFlow" and an even share, not a typed number."""
    s.outside("capacity_priority", _num(link, "CapPri"), 1, 99, "Priority must be between 1 and 99!")
    nom, mx = _num(link, "nomFlow"), _num(link, "MaxFlow")
    typed_nom = nom is not None and nom not in (-1.0, -2.0)
    typed_max = mx is not None and mx not in (-1.0, -2.0)
    if typed_nom and nom is not None:
        if nom < 0:
            s.add("nominal_positive", "Nominal flow must be greater than 0!")
        elif (typed_max and mx is not None and mx >= 0 and nom > mx) or (mx == -1.0 and nom > generated):
            s.add("nominal_vs_max", "Nominal flow must be less than or equal to the max flow!")
    if typed_max and mx is not None:
        if mx < 0:
            s.add("max_positive", "The max flow must be greater than 0!")
        elif typed_nom and nom is not None and nom >= 0 and mx < nom:
            s.add("max_vs_nominal", "The max flow must be greater than the nominal flow!")
        elif mx > generated:
            s.add("max_vs_generated",
                  f"Maximum flow ({mx:g}) is higher than Flow Generated "
                  f"({generated:g}). Lower this link's Max Flow or raise "
                  f"Flow Generated in Project Settings.")


def _check_link_fields(rbd, out: list[Violation]) -> None:
    """The node dialog's per-link tables (`NodeBoxObj.BeSelected`)."""
    # Build each section's ID index ONCE. `endpoint_name` used to call
    # `_rows` per link endpoint, which is O(links x rows): on a 3,470-block
    # model with 5,184 links it re-scanned the whole block table ~10k times.
    # Raptor 7 resolves these with object references/direct child lookups,
    # not a fresh row scan per endpoint.
    id_index: dict[str, dict[str, dict]] = {}
    name_columns: dict[str, tuple[str, str]] = {
        "block": ("BLOCK_DATA", "blockName"),
        "event": ("EVENT_DATA", "eventName"),
        "node": ("NODE_DATA", "Name"),
        "hier": ("HIERARCHY_DATA", "hierarchyName"),
    }
    for kind, (section, _col) in name_columns.items():
        id_index[kind] = {str(r.get("ID", "")): r for r in _rows(rbd, section)}
    nodes = id_index["node"]
    make_system_row = _system(rbd)
    capacity, generated = _flag(make_system_row, "capacityAnalysis"), _num(make_system_row, "flowGenerated") or 0.0

    def endpoint_name(kind: str, oid: object) -> str:
        key = _kind(kind)
        section_col = name_columns.get(key)
        if section_col is not None:
            _section, col = section_col
            row = id_index[key].get(str(oid))
            if row is not None:
                return str(row.get(col, "") or f"{kind} {oid}")
        return f"{kind} {oid}"

    for link in _rows(rbd, "LINK_DATA"):
        from_type = str(link.get("FromType") or "")
        to_type = str(link.get("ToType") or "")
        label = (f"Link {link.get('ID')} "
                 f"({endpoint_name(from_type, link.get('FromID'))} "
                 f"\u2192 {endpoint_name(to_type, link.get('ToID'))})")
        s = _Scope(out, "link_field", "dialog", label)
        # A capacity flow is an INTEGER in the file: the dialog reads the
        # typed cell with STRTOINT and `SaveFile`/`OpenFile` use
        # WriteInt/ReadInt. Raptor 7 itself truncates what is
        # typed, but a decimal in the .rbd is not merely rounded -- ReadInt
        # stops at the point, leaving the fraction in the token stream and
        # shifting every later read, which crashes Raptor 7 with `MODSIM
        # Runtime error: Access Violation` while opening. Refuse the decimal
        # at entry so our own export can never produce that file.
        for col, col_label in (("nomFlow", "Nominal flow"),
                                     ("MaxFlow", "Max flow")):
            value = _num(link, col)
            if value is not None and value not in (-1.0, -2.0) \
                    and not value.is_integer():
                s.add("capacity_integer",
                      f"{col_label} must be a whole number! (the .rbd format "
                      f"stores capacity flow as an integer)")
        target = nodes.get(str(link.get("ToID"))) if _kind(link.get("ToType")) == "node" else None
        if target is not None and _flag(target, "Cold"):
            _check_standby_link(link, s)
        if capacity:
            _check_capacity_link(link, generated, s)


def _check_system(rbd, out: list[Violation]) -> None:
    """`SystemBoxObj.ReceiveData`, the Ao graph
    axis (`graphMenuObj`) and the time slice
    (`SendToEngine`)."""
    make_system_row = _system(rbd)
    s = _Scope(out, "system", "dialog", "System")
    units = str(make_system_row.get("units", ""))
    s.name(units, "The units field can't be blank or have blank spaces!",
           "The units field can't be blank or have blank spaces!",
           "The units name must be no greater than 20 characters!", rule="units")
    for col, what in (("flowGenerated", "Volume of capacity generated"),
                            ("sysLostCost", "Cost of a lost unit of volume"),
                            ("systemRedCost", "The cost of system red time")):
        s.outside(col, _num(make_system_row, col), 0.0, 999999999.0, f"{what} must be between 0 and 999,999,999!")
    gy, yr = _num(make_system_row, "GYthreshold"), _num(make_system_row, "YRthreshold")
    if yr is not None and gy is not None and (yr < 0.0 or (yr >= gy and gy > 0.01)):
        s.add("yr_threshold", "The YR threshold value must be between 0 and the GY threshold value!")
    s.outside("gy_threshold", gy, 0.10, 1.00,
              "The GY threshold value must be between 0.10 and 1.0!")
    ymin = _num(make_system_row, "graphYmin")
    if ymin is not None and ymin < 0.0:
        s.add("graph_ymin", "Y-axis minimum must be greater than or equal to 0.0.")
    elif ymin is not None and ymin > 0.999999:
        s.add("graph_ymin", "Y-axis minimum must be less than 1.0.")
    if _out_of(_num(make_system_row, "graphTimeSlice"), 0.000001, 10000000.0):
        s.add("time_slice", "Time slice must be between 0.000001 and 10,000,000!")
    _check_termination(make_system_row, _Scope(out, "system_dialog", "dialog", "System"))


def _phase_cells(row: dict) -> list[float]:
    """A block's or event's per-phase numbers (`"A: 1.0"` -> 1.0)."""
    vals = []
    for key in (k for k in row if k.startswith("P ") and k[2:].isdigit()):
        try:
            vals.append(float(str(row[key]).partition(":")[2].strip()))
        except ValueError:
            continue
    return vals


def _node_phase_ks(row: dict) -> list[int]:
    ks = []
    for key in (k for k in row if k.startswith("P") and k[1:].isdigit()):
        token = str(row[key]).strip()
        if token not in ("C", "L", ""):
            try:
                ks.append(int(float(token)))
            except ValueError:
                continue
    return ks


def _check_phases(rbd, n_in: dict, out: list[Violation]) -> None:
    """`PhaseBoxObj.BeSelected`."""
    for row in _all_rows(rbd, "PHASES"):
        name = str(row.get("PhaseName", ""))
        s = _Scope(out, "phase", "dialog", f"Phase '{name}'")
        s.name(name, "'Phase Name' field can't be blank or have blank spaces!",
               "'Phase Name' field can't be blank or have blank spaces!",
               "'Phase Name' must be no greater than 20 characters!")
        code = _int(row, "Dist")
        if code == 16:     # the port's text: see _block_distributions
            s.add("dist_offered", "Raptor 7 does not offer Empirical for a phase length: only a "
                  "block's failure and repair laws can hold data points.")
    for row in _rows(rbd, "BLOCK_DATA"):
        if any(x < 0.0 or x > 999.999 for x in _phase_cells(row)):
            _Scope(out, "phase", "dialog", f"Block {row.get('ID')} '{row.get('blockName', '')}'").add(
                "block_stress", "Stress value must be between 0 and 999.999!")
    for row in _rows(rbd, "EVENT_DATA"):
        if any(x < 0.0 or x > 1.0 for x in _phase_cells(row)):
            _Scope(out, "phase", "dialog", f"Event {row.get('ID')} '{row.get('eventName', '')}'").add(
                "event_p", "p value must be between 0 and 1!")
    for row in _rows(rbd, "NODE_DATA"):
        n, name, ks = n_in.get(str(row.get("ID")), 0), row.get("Name", ""), _node_phase_ks(row)
        s = _Scope(out, "phase", "dialog", f"Node {row.get('ID')} '{name}'")
        if n == 0 and any(k != 0 for k in ks):
            s.add("node_k_zero", f"The k value for node '{name}' must be 0!")
        elif any(k < 0 or k > n or k > 9999 for k in ks):
            s.add("node_k", f"The k value for node '{name}' must be between 0 and {n}!")


def _check_triggers(rbd, out: list[Violation]) -> None:
    """`TrigBoxObj.BeSelected`."""
    for row in _all_rows(rbd, "TRIGGERS"):
        name = str(row.get("TriggerName", ""))
        s = _Scope(out, "trigger", "dialog", f"Trigger '{name}'")
        s.name(name, "The trigger name cannot be blank!",
               "A trigger name can't have blank spaces!", "The trigger name must be no greater than 20 characters!")
        s.outside("stagger", _num(row, "InitUsed"), 0.0, _BIG,
                  "The stagger value must be between 0 and 999,999,999.999999!")
        code = _int(row, "Dist")
        if code == 16:     # the port's text: see _block_distributions
            s.add("dist_offered", "Raptor 7 does not offer Empirical for a trigger: only a "
                  "block's failure and repair laws can hold data points.")


def _check_spare_pool(row: dict, out: list[Violation]) -> None:
    """`SparePoolsBoxObj.BeSelected`."""
    name = str(row.get("PoolName", ""))
    s = _Scope(out, "spare_pool", "dialog", f"Spare pool '{name}'")
    s.name(name, "The spare pool name cannot be blank!",
           "A spare pool name can't have blank spaces!", "The spare pool name must be no greater than 20 characters!")
    s.outside("stock", _num(row, "Stock"), 0.0, 999999999.0,
              "The initial stock level must be between 0 and 999,999,999!")
    s.outside("create_cost", _num(row, "SpCost"), 0.0, 999999999.0,
              "The cost to create this spare pool must be between 0 and 999,999,999!")
    s.outside("ship_cost", _num(row, "emerShipCost"), 0.0, 999999999.0,
              "The cost to ship an emergency spare must be between 0 and 999,999,999!")
    if _flag(row, "RSO"):
        s.outside("new_spares", _num(row, "NewSp"), 1.0, 99999.0,
                  "The number of new spares must be between 1 and 99,999!")
        s.outside("arrive_every", _num(row, "ArriveEvery"), 0.000001, _BIG,
                  "The new spares arrival rate must be between 0.000001 and 999,999,999.999999!")
    if _flag(row, "SLO"):
        s.outside("stock_level", _num(row, "Level"), 0.0, math.inf,
                  "The stock level for ordering new spares must be greater than 0!")
        s.outside("stock_amount", _num(row, "Quan"), 1.0, 99999.0,
                  "The number of spares ordered must be between 1 and 99,999!")
        s.outside("stock_rate", _num(row, "SLOTime"), 0.000001, _BIG,
                  "The stock arrival rate must be between 0.000001 and 999,999,999.999999!")
    if _flag(row, "ESO"):
        s.outside("emer_time", _num(row, "EmerTime"), 0.000001, _BIG,
                  "The time to receive an emergency spare must be between 0.000001 and 999,999,999.999999!")


def _check_resource(row: dict, units: str, out: list[Violation]) -> None:
    """`ResPoolsBoxObj.BeSelected`."""
    name = str(row.get("PoolName", ""))
    s = _Scope(out, "resource", "dialog", f"Resource pool '{name}'")
    s.name(name, "The resource pool name cannot be blank!",
           "A resource pool name can't have blank spaces!",
           "The resource pool name must be no greater than 20 characters!")
    s.outside("count", _num(row, "NumIn"), 0.0, 9999.0,
              "The number of resources must be between 0 and 9,999!")
    for col, what in (("SpareCost", "The cost to create this pool"),
                            ("FixedPerUse", "The fixed cost of using this resource"),
                            ("PerTimeUnit", f"The cost of using this resource per {units}")):
        s.outside(f"cost.{col}", _num(row, col), 0.0, 999999999.0, f"{what} must be between 0 and 999,999,999!")


def _check_experiment(rbd, out: list[Violation]) -> None:
    """The Simulation dialog's own fields (`SendToEngine`), held in the port's run experiment."""
    experiment = getattr(rbd, "experiment", None)
    if not isinstance(experiment, dict):
        return
    record = experiment.get("recording") or {}
    try:
        ao = float(record["ao_interval"]) if record.get("ao_interval") is not None else None
        cost = float(record["cost_interval"]) if record.get("cost_interval") is not None else None
        length = float(experiment["term_condition"]) if experiment.get("term_condition") is not None else None
    except (TypeError, ValueError):
        return
    s = _Scope(out, "experiment", "run", "Run settings")
    if ao is not None and record.get("ao_plot") and length is not None \
            and experiment.get("termination_type") == "TimeSim" and ao > length:
        s.add("ao_vs_length",
              f"Availability plot sample interval ({ao:g}) is longer than "
              f"the run length ({length:g}). Lower the interval in Run "
              f"panel ▸ Recording, or increase the run length.")
    s.outside("ao_range", ao, 0.000001, 999999999.999998,
              "Availability plot sample interval must be between 0.000001 "
              "and 999,999,999.999998.")
    if cost is not None and (cost <= 0.0 or cost > 999999999.99):
        s.add("cost_interval",
              "Interim cost sampling interval must be greater than 0 and "
              "less than 999,999,999.99.")


def collapse_boundary_problem(entering: int, leaving: int) -> str | None:
    """Why a selection cannot be collapsed into one hierarchy, or None.

    A hierarchy takes one link in and one link out (`CheckValidLink`), so a selection entered or left by more than one
    link has no hierarchy that keeps its paths. (Raptor 7's own Collapse
    deletes the boundary links and leaves the rewiring to you, the port rewires them, so it has to refuse what
    it cannot rewire.)"""
    if entering <= 1 and leaving <= 1:
        return None
    return (f"The selection is entered by {entering} link(s) and left by {leaving}; a hierarchy "
            "takes one link in and one out (Raptor 7: \"Cannot connect more than one link to a "
            "hierarchy item!\"). Include the node where the paths split and the node where they "
            "join, so one link enters the selection and one leaves it.")


# ---------------------------------------------------------------- stored values

#: Distribution codes the engine knows (`raptor.core.rng.Distribution`).
_DIST_CODES = frozenset(range(1, 23))
#: (section, columns, 0 is allowed -- a delay's stored 0 is Fixed)
_DIST_COLUMNS = (
    ("BLOCK_DATA", ("FDist", "RDist"), False),
    ("BLOCK_DATA", ("Pre-LDTDist", "Post-LDTDist"), True),
    ("PHASES", ("Dist",), False),
    ("TRIGGERS", ("Dist",), False),
)
_PARAM_COLUMNS = ("FParam1", "FParam2", "FParam3", "RParam1", "RParam2", "RParam3")
_COORD_SECTIONS = ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA", "HIERARCHY_DATA")


def _check_values(rbd, out: list[Violation]) -> None:
    """Stored values no front end can hold: an unknown distribution code, a
    parameter or coordinate that is not a finite number, a control character
    in any text. A run would fail on the first two; the legacy file would be
    corrupted by the last (a newline starts a new row)."""
    _check_dist_codes(rbd, out)
    _check_numbers(rbd, out)
    _check_control_chars(rbd, out)


def _check_dist_codes(rbd, out: list[Violation]) -> None:
    for section, cols, zero_ok in _DIST_COLUMNS:
        for row in _rows(rbd, section) if section == "BLOCK_DATA" else _all_rows(rbd, section):
            ident = (f"Block {row.get('ID')} '{row.get('blockName', '')}'" if section == "BLOCK_DATA"
                     else f"{section.title()[:-1] if section == 'PHASES' else 'Trigger'} "
                          f"'{row.get('PhaseName', row.get('TriggerName', ''))}'")
            for col in cols:
                if col not in row or (col == "Dist" and section == "BLOCK_DATA"):
                    continue
                x = _num(row, col)
                ok = x is not None and x == int(x) and (int(x) in _DIST_CODES or (zero_ok and int(x) == 0))
                if not ok:
                    out.append(Violation(f"value.dist_code.{col}", "value", ident,
                                         f"{col} {row.get(col)!r} is not a distribution the engine knows "
                                         "(a whole number from 1 to 22)."))


def _check_numbers(rbd, out: list[Violation]) -> None:
    for row in _rows(rbd, "BLOCK_DATA"):
        ident = f"Block {row.get('ID')} '{row.get('blockName', '')}'"
        for col in _PARAM_COLUMNS:
            raw = row.get(col)
            if raw in (None, "") or _num(row, col) is not None:
                continue
            out.append(Violation(f"value.param.{col}", "value", ident,
                                 f"{col} {raw!r} must be a finite number."))
    for section in _COORD_SECTIONS:
        for row in _rows(rbd, section):
            for col in ("xPos", "yPos"):
                raw = row.get(col)
                if raw in (None, "") or _num(row, col) is not None:
                    continue
                out.append(Violation(f"value.coord.{col}", "value",
                                     f"{section.split('_')[0].title()} {row.get('ID')}",
                                     f"{col} {str(raw)[:40]!r} must be a finite number."))


def _check_control_chars(rbd, out: list[Violation]) -> None:
    for section, sec in rbd.sections.items():
        for row in sec.rows:
            text = "\x01".join(map(str, row.values()))      # one scan per row, not per cell
            if _CONTROL_RE.search(text) is None:
                continue
            for col, val in row.items():
                why = control_problem(str(val))
                if why:
                    out.append(Violation(f"value.control_char.{section}.{col}", "value",
                                         f"{section} {row.get('ID', row.get('Name', ''))}",
                                         f"{col} {why}."))


# ---------------------------------------------------------------- entry points

def check(rbd) -> list[Violation]:
    """Every Raptor 7 input rule the model breaks."""
    out: list[Violation] = []
    _check_run(rbd, out)
    _check_values(rbd, out)
    _check_links(rbd, out)
    pools = {str(r.get("PoolName")): r for r in _all_rows(rbd, "RESOURCE_DATA")}
    for row in _rows(rbd, "BLOCK_DATA"):
        _check_block(row, pools, out)
    for row in _rows(rbd, "EVENT_DATA"):
        _check_event(row, out)
    into, _ = _link_counts(rbd)
    n_in = {oid: n for (kind, oid), n in into.items() if kind == "node"}
    for row in _rows(rbd, "NODE_DATA"):
        _check_node(row, n_in.get(str(row.get("ID")), 0), out)
    _check_link_fields(rbd, out)
    _check_system(rbd, out)
    _check_phases(rbd, n_in, out)
    _check_triggers(rbd, out)
    for row in _all_rows(rbd, "SPARE_POOL_DATA"):
        _check_spare_pool(row, out)
    units = str(_system(rbd).get("units", ""))
    for row in _all_rows(rbd, "RESOURCE_DATA"):
        _check_resource(row, units, out)
    _check_experiment(rbd, out)
    return out


def new_violations(before, after) -> list[Violation]:
    """The violations *after* has that *before* did not (by rule and
    element). An edit is refused when this is not empty; a problem the model
    already had does not block an unrelated edit."""
    old = {v.key for v in check(before)} if before is not None else set()
    # `run` rules are not edit refusals: Raptor 7 applies them when a run
    # starts (a new model with no blocks yet is fine to keep editing).
    return [v for v in check(after)
            if v.kind != "run" and v.key not in old]


def refusal_text(violations: list[Violation]) -> str:
    """One message for a refused edit, in Raptor 7's numbered-list style."""
    if len(violations) == 1:
        v = violations[0]
        return f"{v.element}: {v.message}"
    lines = ["The following errors must be corrected:"]
    lines += [f"   {i}. {v.element}: {v.message}" for i, v in enumerate(violations, 1)]
    return "\n".join(lines)


class InputRuleViolation(ValueError):
    """An edit refused because it breaks a Raptor 7 input rule; the model is
    left as it was. `violations` holds the rules, `str()` Raptor 7's text."""

    def __init__(self, violations: list[Violation]) -> None:
        super().__init__(refusal_text(violations))
        self.violations = violations
