# FevPhasing3: a real Failure Effects View session on a phased model

Model: a copy of `../Phasing3/input.rbd` (phases D000-075_Orbit ... D226-300_Orbit; phase 1 has Cut
elements and a Cut event).

Captured 2026-10-10, real Raptor 7 Student 7.0.12 under Wine, via kwin-mcp. Launch > Failure Effects
View, File > Open Interactive Log, File > Write To Failure Effects Log after each action (8 writes,
CRLF). Toolbar: phase back (160,65), forward (184,65), small eraser (207,65), large eraser (230,65).

| Write | Action just before it | Result |
|---|---|---|
| W1 | none | phase 1; Cut: SolarP_500W, BatteriesNiCd, Bus_UR, Ground_Control; Yellow |
| W2 | phase forward | phase 2; the cut components become Induced Failures; Yellow |
| W3 | click Thermal | Red |
| W4 | small eraser (clears the failures, keeps the phase) | Green, phase 2 |
| W5 | forward, forward | phase 3 (two clicks sent, only one registered: the log is the truth) |
| W6 | forward | phase 4 |
| W7 | back | phase 3 |
| W8 | large eraser | back to phase 1 (the cut state returns) |

The phase at each write is the log's "Phase Number" line (1,2,2,2,3,4,3,1).
