"""Run manifest: what a result set says about its own origin.

The design spec requires that a run be "an experiment, not an animation"
(the internal UI strategy notes) and that evidence be sufficient to
reproduce a conclusion (`:679`). Concretely it asks for a manifest
recording "model identifier, revision and content hash" (`:434`) and the
"enabled analysis modules" (`:442`), and it is blunt that you must never
show "random" without recording the effective seed (`:480`).

Why this has to exist BEFORE run settings move
----------------------------------------------
The obvious place for trials, termination, analyses and recording is the
model file -- and putting them there would break reproducibility without
this module. The manifest hashes the model's ENGINEERING content only
(topology, distributions, dependencies, phases). Run settings sit
outside that hash, so changing the trial count does not change the
model's identity, and a parameter sweep does not orphan the provenance of
every result set already produced. The reverse design -- trials in the
model, so bumping them rewrites the model's hash -- makes the sweep
itself the thing that invalidates the evidence.

The manifest is written once, at run start, and travels with the results.
A result can therefore always answer "what was this run with?" without
trusting a model file that may since have been edited.
"""
from __future__ import annotations

import hashlib
import json

from raptor.core.fileio.rbd import RbdFile
from raptor.core.run_experiment import manifest as _experiment_manifest

#: The sections that define WHAT the system is. Deliberately excludes
#: anything a run touches (NumRuns, TermCondition, StartStats, the
#: analysis flags, graphicsUse, the view columns) -- see the module
#: docstring for why.
#:
#: Names must match `core/fileio/rbd.py`'s canonical section order
#: (`_LEGACY_SECTION_ORDER`) -- that list, not this one, is what the file
#: format uses. `test_run_manifest.py` asserts every name here is real,
#: because a typo in this list does not raise: `sections.get()` returns
#: None and the section is silently dropped from the hash.
_ENGINEERING_SECTIONS = (
    "BLOCK_DATA", "NODE_DATA", "EVENT_DATA", "LINK_DATA",
    "HIERARCHY_DATA", "SPARE_POOL_DATA", "RESOURCE_DATA",
    "TRIGGERS", "PHASES",
)

#: SYSTEM_DATA columns that are VIEW state, not engineering content. The
#: original persists all of them in the model (`graphTimeSlice`, `zoomVal`,
#: `xOrigin`, `yOrigin`, `imageString`), and they change when a user pans
#: or switches symbol sets, so including them would make the hash change
#: for a cosmetic edit.
_VIEW_ONLY_COLUMNS = frozenset({
    "imageString", "graphTimeSlice", "graphYmin", "graphYmax", "zoomVal",
    "xOrigin", "yOrigin", "graphicsUse",
})


#: Columns that say WHERE something is drawn, not what it is. Moving a block
#: cannot change a single simulated number, so "do these results still
#: describe this model?" ignores them (`model_results_hash`). The manifest's
#: own `model_hash` deliberately does NOT ignore them: it is a stored
#: identity, and changing what it covers would orphan every existing one.
LAYOUT_COLUMNS = frozenset({"xPos", "yPos", "zoom", "xCenter", "yCenter"})

#: SYSTEM_DATA columns that only change how results are COLOURED (the Weak
#: Link bands and metric), never a simulated number: changing them must
#: not offer to discard the results on screen.
RESULT_DISPLAY_COLUMNS = frozenset({"GYthreshold", "YRthreshold",
                                    "nodeAnalType"})


def model_results_hash(rbd: RbdFile) -> str:
    """Hash of everything that can change a simulated number, and nothing
    that only moves pixels. Compared before/after an edit to decide
    whether displayed results have gone stale."""
    return model_content_hash(rbd, ignore_columns=LAYOUT_COLUMNS,
                              ignore_system=RESULT_DISPLAY_COLUMNS)


def model_content_hash(rbd: RbdFile, *,
                       ignore_columns: frozenset = frozenset(),
                       ignore_system: frozenset = frozenset()) -> str:
    """A stable hash of the model's ENGINEERING content.

    Stable means: the same model saved twice hashes the same, a
    whitespace/formatting difference does not, and a change to a
    distribution or a link does. Deliberately NOT stable across changes
    to the trial count, the run length, which analyses are on, or a pan
    or zoom -- those are run settings and view state.

    Truncated to 16 hex chars: enough to identify a model in a report,
    short enough to read aloud and paste into a bug."""
    payload: dict = {}
    for name in _ENGINEERING_SECTIONS:
        sec = rbd.sections.get(name)
        if sec is None or not sec.rows:
            continue
        rows = []
        for row in sec.rows:
            rows.append({k: row[k] for k in sorted(row)
                         if k not in ignore_columns
                         and isinstance(row.get(k), (str, int, float))})
        payload[name] = rows

    sysdata = rbd.sections.get("SYSTEM_DATA")
    if sysdata is not None and sysdata.rows:
        row = sysdata.rows[0]
        payload["SYSTEM_DATA"] = {
            k: v for k, v in sorted(row.items())
            if k not in _VIEW_ONLY_COLUMNS and k not in ignore_system
            # Stream numbers ARE engineering content (they determine the
            # random draws, hence the results), so they stay in. The run
            # knobs are not.
            and k not in ("NumRuns", "TerminationType", "TermCondition",
                          "StartStats", "weakAnalysis", "costAnalysis",
                          "capacityAnalysis", "ShowNegErrMsg",
                          "IgnoreStartFail")
        }

    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_manifest(rbd: RbdFile, experiment: dict, *, profile: str = "",
                   engine_version: str = "") -> dict:
    """Freeze everything needed to describe (and reproduce) a run.

    Called ONCE at run start, before the engine is handed anything, so
    the values cannot drift during the run even if the user keeps
    editing."""
    return _experiment_manifest(
        experiment,
        model_hash=model_content_hash(rbd),
        model_name=rbd.file_name or "(unnamed)",
        engine_version=engine_version,
        profile=profile)


def manifest_digest(manifest: dict) -> str:
    """A short id for one run's manifest, for cross-referencing a saved
    result against the settings that produced it."""
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
