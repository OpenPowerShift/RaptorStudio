# EventDepProbe: Failure Effects View click on an event (real session, 2026-10-21)

Raptor 7 Student under Wine, Launch ▸ Failure Effects View, File ▸ Open Interactive Log, File ▸ Write To Failure Effects Log after each action (5 writes, CRLF). The event `GateEvt` is far from the blocks in this file's layout; Zoom to Fit then a click on its diamond.

| Write | Action just before it | Result |
|---|---|---|
| W1 | none | Green |
| W2 | click GateEvt | Red; `join` down (status bar: Failure, Active) |
| W3 | click GateEvt | Green |
| W4 | click GateEvt | Red |
| W5 | click GateEvt, then the large eraser | Green |

Events do not draw in Failure Effects View (a click only toggles Success / Failure).
