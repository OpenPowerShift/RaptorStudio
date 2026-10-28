"""RAPTOR Studio UI smoke test (offscreen): chapter sweep.

Walks every tutorial chapter using the .rbd files in legacy/rbd/ (local-only, git-ignored), loading each
through the real UI and running a small simulation. Reports per-chapter
status: PASS (UI + results correct), XFAIL (a documented engine gap with
the expected reason), or FAIL (unexpected).

Also verifies chapter-specific surfaces: Weak Link tab for Ch11 files,
Cost tab for Ch12, Capacity for Ch14, Phases grid rows for Ch15/17,
hierarchy ovals for Ch16, distribution models for Ch19.

Harness (modal stubs, Recorder, the `TOTAL:` footer) comes from
`_sweep_base.py`. Its three deviations from the other five sweeps are
`preflight_run_anyway=True` (the preflight box's Run Anyway has to be
clicked for a chapter with a known engine gap to run at all),
`capture_information=False` (this sweep classifies a chapter from the
captured message text, so an informational box must not leak in), and
`capture_limit=None` (gap patterns are matched with `in`, so the text is
kept whole).
"""
import os, sys, time, traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _sweep_base as sb

from raptor.core.fileio.rbd import load_rbd

XFAIL = sb.XFAIL

RBD = str(REPO / "legacy" / "rbd")

# Known engine scope limits -> expected failure reasons (substring).
# Both entries below are deliberate `raise NotImplementedError` engine
# gaps, not bugs.
KNOWN_GAPS = [
    ("single outgoing connection", "multi-outgoing block"),
    ("SimStart type", "startup-transient type"),
    ("simStartType", "startup-transient type"),
    ("CycleSim", "phase-cycle termination"),
    ("SLO (stock-level ordering)", "stock-level ordering"),
    ("too many on", "cold-standby switch-off source"),
    ("cold candidate's own dependents not ported",
     "cold-standby dependent propagation"),
    ("combined with PM needing a spare/resource/trigger",
     "dependent block + PM/spare combination"),
]

# chapter -> [(file_stem, extra_check_name or None)]
CHAPTERS = {
    "Ch02 Basics": [("Basic1", None), ("Basic1_Completed", None),
                    ("Ch02_Problem13", None), ("Ch02_Problem14", None),
                    ("Ch02_Problem15", None)],
    "Ch04 Logistics": [("Logisctics1", "spares"),
                       ("Logisctics1_Completed", "spares")],
    "Ch05 Standby": [("Standby1", None), ("StandbyDep1", None)],
    "Ch06 Preventive Mx / Triggers": [
        ("PreventMx1", None), ("PreventMx1_Completed", "triggers"),
        ("TriggerWakeOrder", "triggers")],
    "Ch07 Dependence": [("Dependence1", None), ("Dependence2", None),
                        ("Ch07_Problem7", None), ("Ch07_Problem8", None),
                        ("Ch07_Problem11_Independent", None),
                        ("Ch07_Problem11_Dependent", None)],
    "Ch08 Events": [("Events1", "events"),
                    ("Events1_Completed", "events"),
                    ("Ch08_Problem4A", None), ("Ch08_Problem4B", None),
                    ("Ch08_Problem5C", None), ("Ch08_Problem5D", None)],
    "Ch09 Transients": [("Transients1", None), ("Transients3", None),
                        ("Transients5", None),
                        ("Transients1_DelayedStats", None),
                        ("Transients1_8000h", None)],
    "Ch10 FEV": [("FEV1", "fev")],
    "Ch11 Weak Link": [("WeakLinkAo", "weak"), ("WeakLinkRt", "weak"),
                       ("WeakLinkDo", "weak")],
    "Ch12 Cost": [("Cost1", "cost"), ("Cost2", "cost")],
    "Ch13 Rapco": [("Rapco", None), ("Rapco_Completed", None),
                   ("Rapco_Improved", None)],
    "Ch14 Capacity": [("CapacityBasic", "capacity"),
                      ("Capacity1", "capacity"), ("Capacity2", "capacity"),
                      ("Capacity3", "capacity"),
                      ("CapacityPriority", "capacity"),
                      ("CapacitySplit", "capacity"),
                      ("Ch14_Problem4", None), ("Ch14_Problem5", None),
                      ("Ch14_Problem6", None), ("Ch14_Problem7", None),
                      ("Ch14_Problem8", None), ("Ch14_Problem9", None),
                      ("Ch14_Problem10", None)],
    "Ch15/17 Phasing": [("Phasing1", "phases"),
                        ("Phasing1ManyRuns", "phases"),
                        ("Phasing2", "phases"), ("Phasing3", None),
                        ("Ch15_Problem2_Left", "phases"),
                        ("Ch15_Problem2_Right", "phases"),
                        ("Ch15_Problem5", "phases"),
                        ("Ch15_Problem6", "phases"),
                        ("Ch15_Problem7", "phases")],
    "Ch16 Hierarchy": [("Hierarchy1", "hiers"),
                       ("Ch16_Problem1", "hiers"),
                       ("PieFacility1", "hiers"),
                       ("PieFacility2", "hiers"),
                       ("PieFacility2_Manager", "hiers"),
                       ("PieFacility2_Baker", "hiers"),
                       ("PieFacility2_Accountant", "hiers")],
    "Ch19 Distributions": [
        ("Distributions1", None), ("Distribution1_P12", None),
        ("Distribution1_Empirical12", None),
        ("TriangularProbe", None), ("AntitheticProbe", None),
        ("Ch19_Example1", None), ("Ch19_Example2", None)],
}

def _guard_no_save(window):
    # The sweep walks the local-only legacy/rbd/ models and must never
    # write to them. Ticking the plot streams below dirties the model, and
    # the unsaved-changes prompt's stubbed reply picks its first button,
    # "Save" -- which used to rewrite the file it was reading. Run without
    # saving, and fail loudly if anything still tries.
    def _never_save(*_a, **_k):
        raise AssertionError("ui_sweep6 must not save the models it reads")

    window._maybe_save_before_run = lambda: True
    window._save_file = _never_save


ctx = sb.make_context(preflight_run_anyway=True,
                      capture_information=False,
                      capture_limit=None,
                      need_tmp=False,
                      setup_window=_guard_no_save)
app, w = ctx.app, ctx.window
_MSG = ctx.msgboxes


def record(chapter, stem, ok, detail=""):
    return ctx.record((chapter, stem), ok, detail)


def skip(chapter, stem, reason=""):
    ctx.skip((chapter, stem), reason)


STARTERS = {
    # tutorial files the STUDENT completes; deliberately broken/empty
    "Basic1": "starter (completed twin passes)",
    "Distribution1_P12": "starter (student picks dist)",
    "Distribution1_Empirical12": "starter (student builds empirical)",
    "Ch15_Problem2_Left": "starter (phases to be defined)",
    "Ch15_Problem2_Right": "starter (phases to be defined)",
    "Ch15_Problem5": "starter (phases to be defined)",
    "Ch15_Problem6": "starter (phases to be defined)",
    "Ch15_Problem7": "starter (phases to be defined)",
}


def run_one(chapter, stem, extra):
    print(f"   .. {chapter} / {stem}", flush=True)
    path = f"{RBD}/{stem}.rbd"
    if not os.path.exists(path):
        # legacy/rbd/ is a local, git-ignored collection and may lack a
        # model; the tracked golden twin is the same model when it exists.
        twin = str(REPO / "tests" / "golden" / stem / "input.rbd")
        if os.path.exists(twin):
            path = twin
        else:
            skip(chapter, stem, "not in this checkout's legacy/rbd/")
            return
    try:
        rbd = load_rbd(path)
    except Exception as e:
        record(chapter, stem, False, f"load: {e}")
        return
    w._load_into_app(rbd, path)
    app.processEvents()
    v = w._diagram_dock.view

    # chapter-specific structural checks BEFORE simulating
    if extra == "hiers" and not getattr(v, "_hier_items", {}):
        skip(chapter, stem,
             "exercise starter: HIERARCHY_DATA is empty "
             "(building it is the exercise)")
        return
    if extra == "phases":
        rows = w._rbd.sections.get("PHASES")
        if not rows or not rows.rows:
            skip(chapter, stem, "exercise starter: PHASES empty by design")
            return
    if extra == "triggers":
        rows = w._rbd.sections.get("TRIGGERS")
        if not rows or not rows.rows:
            record(chapter, stem, False, "empty TRIGGERS section")
            return
    if extra == "events" and not v.events:
        record(chapter, stem, False, "no events on events model")
        return
    if extra == "spares":
        sp = w._rbd.sections.get("SPARE_POOL_DATA")
        if not sp or len(sp.rows) < 2:
            record(chapter, stem, False,
                   "expected >=1 spare pool row + template")
            return

    if stem in STARTERS:
        skip(chapter, stem, f"exercise starter: {STARTERS[stem]}")
        return
    # Run a small batch through the ribbon path. Plots are opt-in
    # (Files & Reports): tick them so the capacity-line checks below still
    # see data.
    for _k in ("ao_plot", "capacity_plot", "cost_plot"):
        w._report_stream_actions[_k].setChecked(True)
    w._run_count_spin.setValue(2)
    w._run_simulation()
    app.processEvents()

    # The capture list holds (kind, text); the chapter verdict is
    # classified off the text alone.
    err = next((t for _kind, t in _MSG if t), None)
    warn = next((t for _kind, t in _MSG
                 if "support" in t or "not ported" in t), None)
    report_data = w._results._report_data
    if not report_data:
        # classify known engine gaps as XFAIL
        gap = next((why for pat, why in KNOWN_GAPS if pat in (err or "")
                    or (warn and pat in warn)), None)
        if gap and warn:
            record(chapter, stem, XFAIL, f"engine gap: {gap}")
            return
        if gap:
            record(chapter, stem, XFAIL, f"engine gap: {gap}")
        else:
            record(chapter, stem, False, f"no results; critical={err!r}")
        return

    avail = [d.availability for d in report_data]
    if any(a is None or not (0.0 <= a <= 1.0) for a in avail):
        record(chapter, stem, False,
               f"availability out of range: {avail}")
        return

    note = ""
    if extra == "weak" and not any(d.weak_stats for d in report_data):
        record(chapter, stem, False, "weak stats missing")
        return
    if extra == "cost" and not any(d.cost for d in report_data):
        record(chapter, stem, False, "cost report missing")
        return
    if extra == "capacity" and not w._results._cap_lines:
        record(chapter, stem, False, "capacity lines missing")
        return
    if extra == "fev":
        from raptor.core.engine.step_session import StepSession
        s = StepSession(w._rbd)   # kickoff happens in __init__
        s.step(); s.finish_run()
        note = "step-session ok"
    record(chapter, stem, True, note)


for chapter, files in CHAPTERS.items():
    for stem, extra in files:
        n0 = len(_MSG)
        try:
            run_one(chapter, stem, extra)
        except Exception:
            record(chapter, stem, False, traceback.format_exc(limit=4))
        del _MSG[n0:]

# ------------------------------------------------------------------ report
# Per-chapter breakdown first (the wrapper picks the `[!]` FAIL markers
# out of this when it reports), then the canonical `TOTAL:` line the
# shared Recorder prints for every sweep.
print("\n" + "=" * 72)
by_chapter: dict[str, list] = {}
for r in ctx.recorder.results:
    ch, stem = r.tid
    by_chapter.setdefault(ch, []).append((stem, r))
for ch, items in by_chapter.items():
    print(f"{ch:32s} "
          f"{sum(1 for _, r in items if r.ok is True)} pass "
          f"{sum(1 for _, r in items if r.ok is XFAIL)} xfail "
          f"{sum(1 for _, r in items if r.ok is False)} FAIL")
    for stem, r in items:
        mark = {True: "+", XFAIL: "x"}.get(r.ok, "!")
        line = f"   [{mark}] {stem}"
        if r.ok is not True:
            tail = r.detail.splitlines()[-1][:90] if r.detail else ""
            line += f" — {tail}"
        print(line)
for (ch, stem), reason in ctx.recorder.skipped:
    print(f"   [-] {stem} — SKIP: {reason}")
print("=" * 72)
sys.exit(ctx.report())
