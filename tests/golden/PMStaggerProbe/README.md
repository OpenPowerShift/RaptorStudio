# PMStaggerProbe — PM initial stagger (`PMStaggerTime`)

Built 2026-09-18 via an internal build script (clones
`TwoBlockRedundancy`, two all-`Fixed` PM blocks).

## What it pins

Source `Initialize` sets the frequency-mode PM clock to
`PMTimeLeft := pmFreq - pmStagger`, so a block's
first `PM_is_due` lands `pmStagger` EARLIER than the full `PMFreq`. The
port previously started the clock at `0.0`, which `_pm_race` refilled
straight to `pmFreq` — dropping the stagger entirely (ER3-01).

Two sibling blocks differ ONLY in `PMStaggerTime`:

| block | id | `PMFreq` | `PMStaggerTime` | first `PM_is_due` |
|---|---|---|---|---|
| `Stagger4` | 3 | 10 | 4 | **t=6** |
| `Stagger0` | 4 | 10 | 0 | **t=10** |

All distributions are `Fixed`, fail time is `Fixed(100)` (PM always wins
the race), so NO RNG is drawn and the timeline is identical in this port
and real RAPTOR.

## Expected real event log (1 run, `TermCondition=100`)

```
 6.000000  Block  Stagger4  PM_is_due    Scheduled_PM
 8.000000  Block  Stagger4  PM_complete  MaintTime=2.000000
10.000000  Block  Stagger0  PM_is_due    Scheduled_PM
12.000000  Block  Stagger0  PM_complete  MaintTime=2.000000
18.000000  Block  Stagger4  PM_is_due    Scheduled_PM    (8 + PMFreq 10)
22.000000  Block  Stagger0  PM_is_due    Scheduled_PM    (12 + PMFreq 10)
```

If real's first `Stagger4` `PM_is_due` is at **t=10** (not t=6), the
stagger is NOT applied; if it is at **t=6**, the port's fix is confirmed.

## Port status: CONFIRMED 2026-09-19

Real capture in `legacy/` confirms the stagger exactly: `Stagger4` first
`PM_is_due` at **t=6**, `Stagger0` at **t=10**. The port matches real
**byte-for-byte** (134 lines, 1 run); registered in `test_orchestrator.py`.
Covered also by
`test_block_lifecycle.py::test_pm_initial_stagger_shifts_first_pm`.

## To capture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**, set
**Number of runs = 1**, tick **Detailed event log** (plus **End of run**
/ **Final results** if available), OK. Save into this directory's
`legacy/` as:

- `rpt_events_PMStaggerProbe.txt`
- `rpt_end_of_run_PMStaggerProbe.txt`
- `rpt_final_results_PMStaggerProbe.txt`

Note: the Student Edition greys out PM activities — use the Demo/other
edition that permits PM if the Student build cannot run it.

## Port-side comparison

```
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "tools")
from compare_resource_repair_order import normalise
from raptor.core.fileio.rbd import load_rbd
from raptor.core.engine.orchestrator import run_simulation
import pathlib
name = "PMStaggerProbe"
ours = [normalise(l) for l in run_simulation(load_rbd(f"tests/golden/{name}/input.rbd"), 1)[0] if l.strip()]
leg = pathlib.Path(f"tests/golden/{name}/legacy/rpt_events_{name}.txt")
real = [normalise(l) for l in leg.read_text(encoding="latin-1").splitlines()
        if l.strip() and not l.startswith("Event_Log") and "Begin_Run" not in l]
for i, (r, o) in enumerate(zip(real, ours)):
    if r != o:
        print("first divergence", i, "\n real:", r, "\n ours:", o); break
else:
    print("MATCH" if real == ours else "length differs")
PY
```
