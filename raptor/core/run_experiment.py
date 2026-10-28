"""Run experiment settings: the object the run panel owns.

This is the "Experiment" scope — the middle ground
between application settings and the model file:

* NOT the model. Trials, statistics start, which analyses run, and which
  plots get recorded do not change what the diagram *is*. The same model
  gets run at 10, 100, 1000 and 10000 trials as a convergence sweep, and
  two analysts studying one system should be able to choose different
  warm-ups.
* NOT the application. Plot verbosity is not a property of the machine.
* NOT a transient dialog either. The design spec is explicit: "A Run
  Profile is saved engineering configuration, not a transient dialog"
  (the internal UI blueprint).

So it is a first-class object, editable in one place (the run panel),
persisted with the model, and **frozen into the run manifest** at run
start so a result set can always answer "what was this run with?"
without trusting the model file it was produced from.

That last part is the reason this is not simply a block in `.rbds`:
the spec requires a manifest recording "model identifier, revision and
content hash" (the internal UI strategy notes). Had trials lived in the
model file, bumping the trial count would change the model's identity
and orphan the provenance of every result set already produced.

Storage: `RbdFile.experiment` (a plain dict, `.rbds`-only). The legacy
`.rbd` has nowhere to put it, so a legacy save warns and drops it, the
same precedent as annotations and `display_unit`.
"""
from __future__ import annotations

from typing import Any


#: The engine's own defaults, used when a model says nothing. Taken from
#: Raptor 7's Sim Options rather than invented:
#: Raptor 7's run length default was 1000, and ours had been 100.
#: Typed as `Any` rather than `object` at the value level: every caller
#: indexes into `["recording"]`, and `dict[str, object]` makes that an
#: error ("object is not indexable") for every one of them. The keys are
#: checked at runtime by `normalise`, which is the real guard.
RUN_DEFAULTS: dict[str, Any] = {
    "trials": 1,
    "termination_type": "TimeSim",   # TimeSim | FailSim | CycleSim
    "term_condition": 1000.0,
    "start_stats": 0.0,
    "analyses": [],                   # any of weak / cost / capacity
    # Off by default: it costs one extra batch per block (two for "both")
    # and that is not a cost to impose on someone asking a quick question.
    # The panel default is "both" for a user who switches it on -- see
    # `IMPORTANCE_DEFAULT_MODE` and `gui/ui/run_panel.py`.
    "importance": "none",
    "recording": {
        "event_log": True,
        "ao_plot": False,
        "capacity_plot": False,
        "cost_plot": False,
        "ao_interval": 10.0,          # original's "sample rate" default
        "cost_interval": 720.0,       # original's "frequency" default
    },
}

#: Order and labels for the recording checkboxes. Each entry states its
#: own cost, because the spec requires estimated storage to be shown
#: (the internal UI blueprint) and the whole reason a user
#: turns the event log off is memory/disk.
RECORDING_FIELDS = (
    ("event_log", "Detailed event log",
     "Every event of every run. Needed for the Event Log tab."),
    ("ao_plot", "Availability plot",
     "Availability samples over time, for the Graphs tab."),
    ("capacity_plot", "Capacity plot",
     "Flow vs capacity samples. Requires the Capacity analysis."),
    ("cost_plot", "Interim cost",
     "Cost totals over time. Requires the Cost analysis."),
)

#: Which analysis each dependent plot needs, so the run panel can grey
#: the row and explain why instead of relying on tooltip prose.
RECORDING_REQUIRES = {
    "capacity_plot": ("capacity", "Capacity"),
    "cost_plot": ("cost", "Cost"),
}

#: Wording for the StartStats-after-TermCondition problem. One string, so
#: the readiness line and the field-level tooltip cannot drift apart (they
#: had drifted into two different severities, not just two phrasings).
STATS_START_WARNING = ("statistics start is not before the run length")

#: Importance-measure modes (IEC 61078 Annex D, estimated by re-simulation).
#: Owned here, next to the rest of the experiment, because this module is the
#: Qt-free home both the panel and `raptor.api.importance` can import.
#:
#: ``improvement`` needs one extra batch per block (RRW, Vesely-Fussell),
#: ``risk`` one (RAW), ``both`` two (adds Birnbaum). ``none`` runs nothing.
IMPORTANCE_MODES: tuple[str, ...] = ("none", "improvement", "risk", "both")

#: What the panel selects when the user first switches the analysis on.
IMPORTANCE_DEFAULT_MODE = "both"

_STATS_START_TOOLTIP = "Must be earlier than the run length."


def stats_start_after_run_length(experiment: dict) -> bool:
    """Does this experiment start gathering statistics after it stops?

    Only meaningful for ``TimeSim``: under ``FailSim``/``CycleSim`` the run
    ends on a condition rather than at a time, so there is no length for a
    start time to be "not before".

    Owned here, in the Qt-free module both display sites already import
    (`RECORDING_REQUIRES` is the precedent), because this is a property of
    the experiment, not of any one panel. It was duplicated in
    ``gui/ui/run_experiment_mixin.py`` and ``gui/ui/run_panel.py``, and
    the two copies disagreed about how serious it was -- see
    ``stats_start_is_blocking``.
    """
    if experiment.get("termination_type") != "TimeSim":
        return False
    return float(experiment.get("start_stats", 0.0)) >= \
        float(experiment.get("term_condition", 0.0))


def stats_start_is_blocking() -> bool:
    """Should this condition stop the run being called "Ready"?

    **False**, and deliberately so: it is a WARNING. The run still
    executes and still produces numbers -- it just excludes no start-up
    transient, which is a modelling choice, not an error. ``normalise``
    clamps both fields rather than rejecting them.

    This function exists because the two copies of the predicate disagreed
    in severity rather than merely in wording: the readiness line reported
    the panel as *not ready* (a blocking red banner) while the field
    styling just drew a red border and explained itself. Same condition,
    same screen, two answers about whether the model can be run.
    """
    return False


def stats_start_warning(experiment: dict) -> str:
    """The warning suffix for this experiment, or ``""`` if there is none."""
    if not stats_start_after_run_length(experiment):
        return ""
    return STATS_START_WARNING


def stats_start_tooltip(experiment: dict) -> str:
    """Full tooltip for the StartStats field: the problem if there is one,
    otherwise what the field is for."""
    if stats_start_after_run_length(experiment):
        return _STATS_START_TOOLTIP
    return ("Statistics gathering starts here, so start-up transients are "
            "excluded from the reported numbers.")

#: Named starting points, seeded into a new model. Deliberately few and
#: deliberately about an *experiment*, not about a diagram -- the spec's
#: list (the internal UI blueprint) is bigger because it
#: assumes a job queue; these are what a synchronous engine can honour.
PROFILES: dict[str, dict] = {
    "quick_check": {
        "label": "Quick check",
        "hint": "A handful of trials — is the model running at all?",
        "overrides": {
            "trials": 10,
            "termination_type": "TimeSim",
            "term_condition": 1000.0,
            "start_stats": 0.0,
            "recording": {"event_log": True},
        },
    },
    "steady_state": {
        "label": "Steady state",
        "hint": "Past the start-up transient, with Weak Link bands",
        "overrides": {
            "trials": 100,
            "termination_type": "TimeSim",
            "term_condition": 10000.0,
            "start_stats": 1000.0,
            "analyses": ["weak"],
            "recording": {"event_log": True},
        },
    },
    "endurance": {
        "label": "Endurance / convergence",
        "hint": "Many trials, availability plot, to watch the estimate settle",
        "overrides": {
            "trials": 1000,
            "termination_type": "TimeSim",
            "term_condition": 10000.0,
            "start_stats": 0.0,
            "analyses": ["weak"],
            "recording": {"event_log": True, "ao_plot": True},
        },
    },
    "endgame": {
        "label": "Endgame",
        "hint": "Run to the first system failures",
        "overrides": {
            "trials": 100,
            "termination_type": "FailSim",
            "term_condition": 10.0,
            "start_stats": 0.0,
            "recording": {"event_log": True},
        },
    },
}

DEFAULT_PROFILE = "quick_check"


def _tolerant(cast, value):
    """Coerce a stored cell to `cast`, going via float for an int cast.

    THE BUG THIS FIXES. The legacy `.rbd` format writes its numbers with
    `%f`, so a trial count is stored as the string `"20.000000"`, not
    `"20"`. `int("20.000000")` raises `ValueError`. Both `normalise` and
    `from_system_row` caught that exception and fell back to the default
    of 1 -- so opening ANY legacy model silently ran a single trial
    instead of the configured count, with no message and no wrong-looking
    UI. A model configured for 1000 trials produced one trial, and the
    results were reported for one trial.

    Going via float is lossless for the values in range: every cell that
    reaches here is a count, a time or a percentage, not a quantity where
    the fractional part carries meaning. It is also what the rest of the
    codebase does -- `int(float(row["ID"]))` appears throughout the
    reader -- so this aligns with the established convention rather than
    inventing a second one.
    """
    if cast is int:
        if isinstance(value, bool):
            raise TypeError("a flag is not a count")
        return int(float(value))
    return cast(value)


def _copy(value):
    """Deep-enough copy: the only nested container in an experiment is
    `recording`, and a shallow `dict()` would leave every caller's
    "copy" sharing one dict with the module-level defaults. That turns a
    single `exp["recording"]["x"] = y` into a global default change."""
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return list(value)
    return value


def _deep_merge(base: dict, over: dict) -> dict:
    """Merge `over` onto a copy of `base`, recursing one level into the
    nested `recording` dict. A profile's partial `recording` therefore
    says only what it changes, and the rest keeps the model/default
    values."""
    out = _copy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _copy(out[k])
            out[k].update(_copy(v))
        else:
            out[k] = _copy(v)
    return out


def defaults() -> dict:
    """A fresh deep copy of the defaults (never a shared mutable -- the
    nested `recording` dict is mutated in place by callers)."""
    return _copy(RUN_DEFAULTS)


def normalise(raw: dict | None) -> dict:
    """Coerce arbitrary stored/hand-edited input into a valid experiment.

    Every field falls back to its default independently, so a partial or
    stale file still yields a usable object rather than raising. Types
    are coerced too: a JSON-authored `.rbds` may carry "10" for trials.
    """
    src = raw if isinstance(raw, dict) else {}
    out = defaults()

    def num(key, cast, lo=None, hi=None):
        try:
            v = _tolerant(cast, src.get(key, out[key]))
        except (TypeError, ValueError):
            return
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        out[key] = v

    num("trials", int, 1, 1_000_000)
    num("term_condition", float, 0.0, 1e15)
    num("start_stats", float, 0.0, 1e15)

    term = str(src.get("termination_type",
                       out["termination_type"])).strip()
    if term in ("TimeSim", "FailSim", "CycleSim"):
        out["termination_type"] = term

    analyses = src.get("analyses")
    if isinstance(analyses, list):
        out["analyses"] = [a for a in ("weak", "cost", "capacity")
                           if a in analyses]

    mode = str(src.get("importance", out["importance"])).strip().lower()
    if mode in IMPORTANCE_MODES:
        out["importance"] = mode

    _normalise_recording(src.get("recording"), out["recording"])
    return out


def _normalise_recording(record, out_rec: dict) -> None:
    """Overlay a stored `recording` block onto `out_rec` (in place), field by field."""
    if not isinstance(record, dict):
        return
    for key, _label, _hint in RECORDING_FIELDS:
        out_rec[key] = bool(record.get(key, out_rec[key]))
    for key, lo in (("ao_interval", 0.001), ("cost_interval", 0.001)):
        try:
            out_rec[key] = max(lo, float(record.get(key, out_rec[key])))
        except (TypeError, ValueError):
            pass


def apply_profile(name: str, base: dict | None = None) -> dict:
    """`base` (the current experiment) overlaid with a named profile."""
    prof = PROFILES.get(name)
    if not prof:
        return normalise(base)
    return _deep_merge(normalise(base), prof["overrides"])


def from_system_row(row: dict) -> dict:
    """Derive an experiment from a legacy `SYSTEM_DATA` row.

    This is how a `.rbd` (which has no experiment block) and an old
    `.rbds` (written before the block existed) both get one: the fields
    the legacy format DOES have -- trials, termination, statistics
    start, analysis flags -- are read from it, and the recording settings
    it has nowhere to store come from the documented defaults.

    The two directions matter and are not symmetric. Reading is
    lossless; WRITING an experiment to a legacy `.rbd` is lossy, which
    is why the save path warns.
    """
    out = defaults()
    if not row:
        return out

    def num(field, column, cast, lo=None, hi=None):
        """`field` is the experiment key, `column` the legacy SYSTEM_DATA
        column. They are not the same name (`trials` vs `NumRuns`), and
        conflating them silently produced an experiment that ignored the
        model's own row."""
        try:
            v = _tolerant(cast, row.get(column))
        except (TypeError, ValueError):
            return
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        out[field] = v

    num("trials", "NumRuns", int, 1, 1_000_000)
    num("term_condition", "TermCondition", float, 0.0, 1e15)
    num("start_stats", "StartStats", float, 0.0, 1e15)
    term = str(row.get("TerminationType", "")).strip()
    if term in ("TimeSim", "FailSim", "CycleSim"):
        out["termination_type"] = term
    analyses = []
    if str(row.get("weakAnalysis", "")).strip().upper().startswith(("1", "T", "TRUE")):
        analyses.append("weak")
    if str(row.get("costAnalysis", "")).strip().upper().startswith(("1", "T", "TRUE")):
        analyses.append("cost")
    if str(row.get("capacityAnalysis", "")).strip().upper().startswith(("1", "T", "TRUE")):
        analyses.append("capacity")
    out["analyses"] = analyses
    return out


def to_system_row(experiment: dict) -> list[tuple[str, str]]:
    """The `SYSTEM_DATA` cells an experiment writes back.

    Only the four fields the legacy format actually has. Returns
    (column, value) pairs so the caller can push them as undoable
    `RowCellCmd`s against the same row it reads them from -- the
    experiment and the legacy columns cannot drift apart.
    """
    normalised = normalise(experiment)
    analyses = normalised["analyses"]

    def flag(on: bool) -> str:
        return "1" if on else "0"

    return [
        # Spelled as Raptor 7 spells it ("10.000000"), so a `.rbds` stores
        # the trial count once (see `fileio.rbds._split_row`).
        ("NumRuns", f"{float(normalised['trials']):.6f}"),
        ("TerminationType", normalised["termination_type"]),
        ("TermCondition", f"{normalised['term_condition']:.6f}"),
        ("StartStats", f"{normalised['start_stats']:.6f}"),
        ("weakAnalysis", flag("weak" in analyses)),
        ("costAnalysis", flag("cost" in analyses)),
        ("capacityAnalysis", flag("capacity" in analyses)),
    ]


def manifest(experiment: dict, *, model_hash: str, model_name: str,
             engine_version: str = "", profile: str = "") -> dict:
    """Freeze everything a result set needs to describe its own origin.

    This is the object that lets run settings be mutable without making
    old results unfalsifiable: the manifest is written ONCE at run start
    and travels with the results, so a result can always say what it was
    run with even after the model has moved on.

    The spec's checklist is explicit that the manifest carries the model
    identity, the enabled analysis modules and the seed strategy
    (the internal UI strategy notes: "Never show
    'random' without recording the effective seed").
    """
    normalised = normalise(experiment)
    return {
        "model_name": model_name,
        "model_hash": model_hash,
        "engine_version": engine_version,
        "profile": profile,
        "trials": normalised["trials"],
        "termination_type": normalised["termination_type"],
        "term_condition": normalised["term_condition"],
        "start_stats": normalised["start_stats"],
        "analyses": list(normalised["analyses"]),
        "recording": dict(normalised["recording"]),
        "seed_strategy": "per-model streams (SysStreamsArray), fixed; "
                         "re-running the same file replays the same "
                         "draws",
    }
