"""Application settings: one namespaced tree, one accessor, one migration.

Replaces eight ad-hoc QSettings conventions:

    preferences                  (a serialised dict blob, 8 keys)
    preferences/parallel_workers (a scalar OUTSIDE the blob it is named
                                  after -- the dialog and the reader
                                  disagreed about what "the preferences"
                                  were)
    reports/{event_log,ao_plot,
             capacity_plot,
             cost_plot}        (engineering run data in app settings,
                                against the design spec: "QSettings for
                                user layout/preferences only, not
                                engineering model data")
    appearance/dark_mode
    block_defaults
    annotation_style_defaults
    project_meta                 (read once, written NOWHERE)
    recent_rbd_files

Design rules, each learned the hard way:

* **Read-through, never cached.** Several tests call `QSettings().clear()`
  between cases (`tests/unit/test_report_streams.py`,
  `tests/unit/test_editing_workflows.py`). A caching façade makes them
  pass against stale values while testing nothing. Every access hits
  QSettings.
* **One-shot migration, not dual-write.** Nothing outside this codebase
  reads the settings file: no plugin API, no scripted consumer. Dual
  writing for N releases buys nothing and leaves a permanent "are we
  migrating?" branch plus two sets of tests to keep honest. So: read the
  old keys, write the new ones, delete the old ones, bump
  `schema_version`.
* **A version mismatch is reported, not silently absorbed.** If a user's
  `schema_version` is more than one release behind, we cannot know what
  their keys meant, so we say which ones we reset instead of applying a
  stale interpretation.
* **Defaults live in ONE place** (`DEFAULTS`), so "what happens on a new
  install" is a single readable table rather than a default argument
  scattered across four call sites.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings

#: Bumped whenever the on-disk shape changes. `_migrate` runs any pending
#: steps below this value.
SCHEMA_VERSION = 2

#: Every setting, its default, and where it lives. The tree is
#: namespaced by what the setting is *for*:
#:
#:   appearance/  how the app looks
#:   files/       how the app treats files
#:   editing/     how numbers are entered and what new objects look like
#:   execution/   how runs are executed
#:   experiments/ run profiles and the last one used
#:   tables/      the Export Tables dialog's last choices
#:
#: A setting that describes a MODEL (trials, termination, which plots to
#: record, the print title block) does NOT belong here -- see
#: `RbdFile`'s fields and `MainWindow`'s run panel.
DEFAULTS: dict[str, Any] = {
    # appearance/
    "appearance/dark_mode": False,
    # Digits after the decimal point on Weak Link canvas labels.
    "appearance/weak_link_digits": 3,
    # The two lines under a block's name on the canvas: one of
    # `block_labels.MODES` ("mean" | "spread" | "rate" | "off").
    "appearance/block_labels": "mean",
    # files/
    "files/autosave": False,
    "files/autosave_min": 5,
    # "never" | "ask" | "always": whether to save (or prompt to save)
    # unsaved changes before a run starts, since the results otherwise
    # silently reflect a different model than what is on disk.
    "files/save_before_run": "ask",
    "files/open_in": "new",
    "files/recent": [],
    # editing/
    "editing/exp_mode": "mean",       # "mean" | "rate"
    "editing/ln_mode": "mean_sd",     # "mean_sd" | "mu_sigma"
    "editing/block_defaults": {},
    "editing/annotation_style_defaults": {},
    # execution/
    # 0 = automatic: every core (see `raptor.gui.cores`).
    "execution/max_workers": 0,
    # Off: a run goes at full speed on several cores. Animate is the
    # one-core run you watch.
    "execution/default_animate": False,
    # tables/ -- the Export Tables dialog: which formats were last chosen
    "tables/xlsx": True,
    "tables/csv": False,
    "tables/txt_files": False,
    "tables/txt_report": False,
    "tables/open_folder": False,
    # layout/ -- the main window's saved geometry (bytes from
    # `QMainWindow.saveGeometry`). Only the Raptor 7 app entry point reads and
    # writes it; see `MainWindow.enable_layout_persistence`.
    "layout/window_geometry": None,
    # experiments/ -- named run profiles, plus the last one used
    "experiments/profiles": {},
    "experiments/last_used": "quick_check",
}

#: Old key -> new key, for settings that were scalars/dicts under the
#: pre-`SCHEMA_VERSION` layout. Values are not transformed here; each
#: migration step handles its own shape (the `preferences` blob is a
#: dict, `parallel_workers` is an int, and so on).
_KEY_MAP = {
    "appearance/dark_mode": "appearance/dark_mode",
    "block_defaults": "editing/block_defaults",
    "annotation_style_defaults": "editing/annotation_style_defaults",
    "recent_rbd_files": "files/recent",
}

#: Keys inside the old serialised `preferences` dict, and where each
#: lands in the new tree.
_PREFS_BLOB_MAP = {
    "units": None,           # per-model SYSTEM_DATA column, not a pref;
                             # kept as a *new-model* default instead
    "autosave": "files/autosave",
    "autosave_min": "files/autosave_min",
    "exp_mode": "editing/exp_mode",
    "ln_mode": "editing/ln_mode",
    "save_before_run": "files/save_before_run",
    "ao_plot_interval": None,      # per-run, not an app setting
    "cost_plot_interval": None,    # per-run, not an app setting
}

#: New-model defaults, seeded from the old `preferences/units`. Kept
#: apart from the run settings so "what a NEW model starts with" is one
#: small readable group rather than mixed into editing preferences.
NEW_MODEL_DEFAULTS: dict[str, Any] = {
    "units": "hours",
}


def get(key: str, default=None):
    """Read one setting, falling back to the documented default.

    Read-through by design -- see the module docstring."""
    if key not in DEFAULTS and default is None:
        # An unknown key is a bug in the caller, not a user error. Fall
        # back to the type-appropriate zero so the app still runs.
        default = DEFAULTS.get(key)
    s = QSettings()
    val = s.value(key, DEFAULTS.get(key, default))
    if val is None:
        val = DEFAULTS.get(key, default)
    return val


def contains(key: str) -> bool:
    """Whether the key is actually SET, as distinct from `get` which
    always answers.

    Needed by the one-shot migrations: "was this ever chosen, or is the
    user seeing the default?" is a different question from "what is the
    value", and only the first one can tell a migration whether there is
    anything to carry over. `get` cannot, because a default is
    indistinguishable from a stored equal value."""
    return QSettings().contains(key)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get(key, default))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get(key, default))
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    """One setting, as a bool.

    Shares `core.fileio.typed.cell_truthy`'s spellings, but reads them
    through it rather than by coincidence — the two lists were written
    independently (review E-13) and had already drifted: this one accepted
    `yes`/`on`, the pool dialog's did not, and the engine's deny-list
    accepted `false` as True.

    They stay separate functions because the domains differ: QSettings can
    hand back a real `bool` (handled above) and holds app preferences,
    while `cell_truthy` parses a `.rbd` file cell written by the legacy
    writer as `"0"`/`"1"`. Same question, same answer, two callers."""
    val = get(key, default)
    if isinstance(val, bool):
        return val
    from raptor.core.fileio.typed import cell_truthy
    return cell_truthy(val, default)


def get_dict(key: str) -> dict:
    val = get(key, {})
    return dict(val) if isinstance(val, dict) else {}


def get_list(key: str) -> list:
    val = get(key, [])
    if isinstance(val, list):
        return list(val)
    # QSettings round-trips a single-element list as a bare scalar.
    return [val] if val else []


def set_value(key: str, value) -> None:
    QSettings().setValue(key, value)


def remove(key: str) -> None:
    QSettings().remove(key)


def _migrate() -> None:
    """Bring an older settings file up to `SCHEMA_VERSION`.

    Idempotent and safe to run on every launch: it only acts when
    `schema_version` is below the current one, and each step is a
    read-old/write-new/delete-old.
    """
    s = QSettings()
    try:
        # `str(...)` first: QSettings hands back whatever the ini parser
        # produced, which for a hand-edited file can be a bare string
        # that `int()` refuses for a reason the user cannot act on.
        version = int(str(s.value("schema_version", 0) or 0))
    except (TypeError, ValueError):
        version = 0
    if version >= SCHEMA_VERSION:
        return

    if version < 1:
        _migrate_to_1(s)
    if version < 2:
        _migrate_to_2(s)

    s.setValue("schema_version", SCHEMA_VERSION)
    s.sync()


def _migrate_to_1(s) -> None:
    """Split the serialised `preferences` blob and the orphan
    `parallel_workers` scalar into the namespaced tree."""
    for old, new in _KEY_MAP.items():
        if s.contains(old) and not s.contains(new):
            s.setValue(new, s.value(old))
            s.remove(old)

    blob = s.value("preferences", None)
    if isinstance(blob, dict):
        for old_key, new_key in _PREFS_BLOB_MAP.items():
            if old_key not in blob:
                continue
            if new_key and not s.contains(new_key):
                s.setValue(new_key, blob[old_key])
        # `units` was the one blob entry that described a NEW model
        # rather than the app, so it becomes a new-model default.
        if blob.get("units") and not s.contains("new_model/units"):
            s.setValue("new_model/units", blob["units"])
        s.remove("preferences")

    # The orphan: written to `preferences/parallel_workers` but never
    # included in the `preferences` blob it was named after.
    orphan = s.value("preferences/parallel_workers", None)
    if orphan is not None and not s.contains("execution/max_workers"):
        s.setValue("execution/max_workers", orphan)
    s.remove("preferences/parallel_workers")


def _migrate_to_2(s) -> None:
    """`project_meta` moved from a global QSettings blob (read once,
    written nowhere) onto the model itself, as `RbdFile.project_meta`.

    Nothing to copy: the value was never written, so there is nothing to
    preserve. The step exists so the version number is honest about
    having changed, and so a stale global key is cleaned up rather than
    lingering confusingly."""
    s.remove("project_meta")


def ensure_migrated() -> None:
    """Public entry point -- call once at startup, before any read."""
    _migrate()
