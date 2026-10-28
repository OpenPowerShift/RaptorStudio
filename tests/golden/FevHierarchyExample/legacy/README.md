# FevHierarchyExample: a real Failure Effects View session with cold standby and dependencies

Model: a copy of `../HierarchyExample/input.rbd` (the manual's chapter 16 model): three hierarchy
ovals, a 1-of-2 cold-standby node (`Comm`: HighGain primary, LowGain standby with `sbStress` 0.05)
and `Attitude`, a series block that depends on the system (70 % no effect, 15 % idle, 5 % PM, 10 %
failure).

Captured 2026-10-09, real Raptor 7 Student 7.0.12 under Wine, via kwin-mcp. Launch ▸ Failure Effects
View, File ▸ Open Interactive Log, File ▸ Write To Failure Effects Log after each action (10
writes, CRLF). Visible without a log: on entering FEV, LowGain is already **Standby** (blue) with a
blue link; failing HighGain makes LowGain **Running** at once (no switch delay), restoring HighGain
puts it back to Standby.

| Write | Action just before it | Result |
|---|---|---|
| W1 | none | Green |
| W2 | click HighGain (fail the primary) | Yellow; Comm degraded (LowGain took over) |
| W3 | click HighGain (restore) | Green; LowGain back on standby |
| W4 | click LowGain (a Standby block with `sbStress` > 0 can be failed) | Yellow; Comm degraded |
| W5 | click HighGain (fail it too; LowGain still failed) | Red; Attitude **induced failure** (its worst-case outcome), Down Nodes n1, Comm |
| W6 | click Attitude (hatched, Repairing) | cycles to PM: PM Components: Attitude |
| W7 | click Attitude (hatched, PM) | cycles to Idle: Idle Components: Attitude |
| W8 | click Attitude (hatched, Idle) | cycles to operating (no effect): n1 comes back up |
| W9 | click Attitude (hatched, Running) | cycles round to Repairing: Induced Failures: Attitude |
| W10 | large eraser | Green, nothing failed |
