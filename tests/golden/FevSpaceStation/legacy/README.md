# FevSpaceStation: a real Failure Effects View session

Model: `SpaceStation.rbd`, shipped in the Raptor 7 Student install (`Examples/`),
copied unchanged to `../input.rbd`. 13 blocks, a 2-of-3 node (`Power_Needed`), blocks that
depend on the system (go Idle when it is down).

Captured 2026-10-09 with real Raptor 7 Student 7.0.12 under Wine, driven through kwin-mcp in an
isolated session. Launch ▸ Failure Effects View (no simulation run first), File ▸ Open Interactive
Log, then after each action File ▸ Write To Failure Effects Log. `fevlog_FevSpaceStation.txt` is
that log (CRLF; 8 writes). The click script, in order:

| Write | Action just before it | Result |
|---|---|---|
| W1 | none (baseline, everything operating) | Green, nothing failed |
| W2 | click BatteriesNiCd (fail) | Yellow; Degraded Nodes: Power_Needed |
| W3 | click Bus_DET-FR (fail, second of three strings) | Red; two Failed, ten Idle (hatched), Down Nodes: Power_Needed, n2 |
| W4 | click SolarP_900W (hatched, Idle) | no change: its only dependency outcome is Idle |
| W5 | click BatteriesNiCd (red, unhatched: restore) | Yellow; Bus_DET-FR still failed; the idle blocks are back (no hatching) |
| W6 | large eraser (Refresh All) | Green, nothing failed |
| W7 | click Thermal (series block, fail), then SolarP_500W (hatched, Idle) | Red; Thermal failed, every other block Idle; SolarP_500W unchanged |
| W8 | small eraser (Refresh Phase) | no change: **disabled in a model with no phases** (Control menu greys Refresh Phase, Decrease/Increase Phase) |

Also observed on screen (not in the log): a failed block's string input and exit links draw red,
idle links brown, a hatched block is an induced effect, the system face follows the system colour,
and the status bar shows the clicked block's name, its failure and repair distribution and its status.
