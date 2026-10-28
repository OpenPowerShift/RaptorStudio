# FevStandbyStressProbe: can a click fail a Standby block whose standby stress is 0?

Model: `FevHierarchyExample` with `LowGain`'s `SBStress` changed from 0.05 to 0 (one cell; an internal build script).

Captured 2026-10-21, real Raptor 7 Student under Wine, via kwin-mcp: Launch ▸ Failure Effects View, File ▸ Open Interactive Log, File ▸ Write To Failure Effects Log after each action (5 writes, CRLF).

| Write | Action just before it | Result |
|---|---|---|
| W1 | none | Green |
| W2 | click LowGain (Standby, stress 0) | **No change**: Green, nothing failed |
| W3 | click HighGain (fail the primary) | Yellow; Comm degraded (LowGain took over) |
| W4 | click LowGain (now Running) | Red; LowGain failed, Attitude induced failure, Down Nodes n1, Comm |
| W5 | eraser | Green |

With stress 0.05 the same click on a Standby LowGain fails it (`FevHierarchyExample` W4), so the boundary is a positive stress.
