# PmMisDeferProbe — awaiting real capture

Built 2026-09-16 via `tools/rbd_builder.py` by cloning `Phasing1`
WHOLESALE and flipping one field. **No real capture yet.**

Cloned rather than hand-built because `PMMisDefer` only has meaning when
`activePhases > 0`, and hand-building a `PHASES` section has crashed real
Raptor four times across two probes — see the project memory. The ONLY
change from `Phasing1` is:

    SolarP_500W:  PMMisDefer  0 -> 1

`SolarP_500W` was chosen because it is already phased AND already uses
trigger PM, so the deferral has something to defer.

## What it is for

`PM:pmMisDefer` is the last boolean in the internal fixture-coverage notes's
gap list with no fixture setting it anywhere in the corpus. Source:

    IF (activePhases>0)
       IF ((pmMisDefer) AND (System.missionStatus=Mission))
          deferPM:=TRUE;
          ...pmDeferGroup.Add(SELF)
       END IF;
    END IF;                                 -- Raptor 7

i.e. a PM that comes due DURING a mission is held until the mission ends,
rather than taken immediately.

All three of `Phasing1`'s phases are `mission=True`, so every PM this
block takes is a candidate for deferral — a strong exercise rather than
an incidental one.

## This port's behaviour (so the capture is a real test)

It is implemented (`block_lifecycle.py` ~:3055, gated on
`system_state.mission_status is MissionStatus.MISSION`) and it DOES fire:
over 10 runs, 91 of 156 `PM_is_due` lines are NOT followed by an
immediate `beginning_PM`, which is the deferral signature. What the
capture settles is whether the deferral happens at the SAME instants and
releases at the same ones.

(Note when reading the log: counting `PM_is_due` against `beginning_PM`
TOTALS is misleading — a 5-run sample gave 77 and 77 and looked like no
deferral at all. Pair them positionally instead.)

## To capture

Load `input.rbd`, Launch > Simulation..., keep `Phasing1`'s own run
count, Files & Reports > tick "Detailed event log" + "End of run" +
"Final results", OK. Save into `legacy/`.
