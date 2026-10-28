# CS_Probe_cold_noLDT

Built 2026-08-29 for the multi-toggle fail-wait countdown investigation
(the internal countdown notes, the internal investigation log §18dd-18hh).

Identical to `tests/golden/CS_Probe_cold/input.rbd` (`LowGain`'s
`SBStress=0.0`, the cold-standby probe) except `LowGain`'s Pre-LDT and
Post-LDT are zeroed (`Pre-LDTDist`/`Post-LDTDist` stay `19`/FIXED, but
`Param1` on both is `0.500000 -> 0.000000`). This removes the fixed
1.0s-per-cycle repair-hold delay, shifting the toggle schedule's timing
without changing which state each segment is in -- exactly the
"reshuffled toggle schedule" the internal investigation log §18hh's own
conclusion asked for: "a real, targeted probe run... with a deliberately
reshuffled toggle schedule, so the SAME draw's fail-instant shift pins
the law by direct observation."

**Why this specific change**: every formula tried so far (§18gg/18hh,
4 variants of the interrupt-rescale × per-state-stress model) was fit
against `CS_Probe_cold`/`CS_SwitchFail`'s own EXISTING toggle timing
only -- there's no way to tell, from ONE schedule, which of several
formulas that happen to agree on that schedule is actually correct.
Shifting every cycle's own Pre/Post-LDT duration by a KNOWN, controlled
amount (0.5s -> 0.0s, twice per cycle) and comparing the SAME draw's
new fail-instant against what each candidate formula predicts is a
direct discriminating test, not more curve-fitting against the same
data.

## To capture

```
WINEPREFIX=$PWD/.wine-raptor wine "C:\Program Files (x86)\Raptor7Student\Raptor7Student.exe"
```

Open `tests/fixtures/CS_Probe_cold_noLDT/input.rbd`, run **10 runs**
(the standing convention), then **Save As > Text Files** for the event
log (same as every other golden fixture). If you want maximum
precision, also launch with `/sd` appended for `simDetails.TXT`'s exact
per-draw values -- not required, but the earlier `CS_SwitchFail`/
`CS_Probe_*` captures included it and it made the segment-table
reconstruction much more precise (§18hh's own table was built from it).

Send back the exported report + the `.rbd` (or just confirm the
filenames if they land in the usual `~/Documents/Raptor7/` spot) and
it'll get dropped into `tests/golden/CS_Probe_cold_noLDT/` in the same
shape as the other golden fixtures.
