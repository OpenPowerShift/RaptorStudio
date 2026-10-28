# Fixture: PrePostLDT — first logistics-delay-time capture

Captured 2026-08-15 from Raptor 7 Student Edition under Wine. Same
single-block model as `UniformProbe` with exactly one change: both
`Pre-LDTDist`/`Post-LDTDist` parameters set to `Fixed(0.05)` instead of
`Fixed(0.0)` (i.e. a real, non-zero logistics delay before AND after the
repair itself — `block_lifecycle.py`'s module docstring notes both are
`Fixed(0.0)` "effectively disabled" in every fixture up to now).
`NumRuns=5`.

## What it shows

A full cycle now has FIVE block-state lines instead of three (`Failed`
-> `Repairing` -> `Repaired` -> `Good` in the happy path), each with
its own log line and node/system cascade where applicable:

```
0.924530   Block   unnamed   Failed        Operating_time=0.924530
0.924530   Block   unnamed   RepHold       LDT
0.924530   Node    end       Bad           0-1/1_df2
0.924530   System  State_change  Red       _d3
0.974530   Block   unnamed   End_Pre-LDT   Time=0.050000
0.974530   Block   unnamed   Repairing     No_resources_used
0.974530   System  Red_change    Red       _r3
1.159184   Block   unnamed   Repaired      MaintTime=0.184654
1.159184   Block   unnamed   RepHold       postLDT
1.159184   System  Red_change    Red       _h3
1.209184   Block   unnamed   End_Post-LDT  Time=0.050000
1.209184   Block   unnamed   Good          Stress=1.000000
1.209184   Node    end       Good          1-1/1_ur2
1.209184   System  State_change  Green     system_change_status
```

So the real sequence is: `Failed` -> `RepHold`/`"LDT"` (waiting out the
pre-repair logistics delay) -> `"End_Pre-LDT"` (a log-only line, no
status change, just marks the wait's end) -> `Repairing` -> `Repaired`
-> `RepHold`/`"postLDT"` (post-repair delay) -> `"End_Post-LDT"`
(log-only again) -> `Good`.

Also the **first real data for the `"Red_change"` line**
(`AnalyzeSystem`'s repeated-Red comment-tracking,
explicitly deferred everywhere it was mentioned so far — see
`system_status.py`'s module docstring). It fires on *every* block-state
change that happens while the system is already Red and stays Red
(`_r3` = "now Repairing", `_h3` = "now on hold"/RepHold — matches
`AnalyzeSystem`'s `commentString` construction using `"_r"`/`"_h"`/`"_c"`/
`"_d"`/`"_p"` prefixes per block, one alert-worthy detail: the
`"State_change"` line's own comment-building logic in `change_system_
status` uses `"_d"` for Repairing/RepHold/Done, but THIS separate
`"Red_change"` line distinguishes Repairing (`_r`) from RepHold (`_h`) —
don't conflate the two comment-builders when porting).

## Files

- `input.rbd` — the model.
- `legacy/rpt_pre_post_ldt.txt` — event log, 5 runs, 6406 lines
  (small enough to keep uncompressed, unlike `UniformProbe`/
  `TwoBlockRedundancy`'s multi-MB logs).

## What this is for

The next real fixture for porting Pre/Post-LDT handling in
`RBDBlockObj.Run`/`Repair` (currently assumed away, see
`block_lifecycle.py`'s module docstring) and for `AnalyzeSystem`'s
`"Red_change"` line (currently entirely unported, see
`system_status.py`'s module docstring — this fixture is what unblocks
it).
