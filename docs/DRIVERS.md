# Downtime drivers: when they are calculated

Drivers replay the event log to attribute each system outage to the element
that started it and the state that held it down. That replay is done **once,
at the end of the run**, not when the user first opens a chart:

- the background worker calculates it after the batch, while the event log
  and the run model are hot; Pause holds it between runs and Stop abandons
  it, exactly as they do for the batch itself (`Processing results —
  downtime drivers 120/1000`);
- the synchronous (Animate) path does the same before presenting results,
  on the run's own progress bar: the status line reads
  `Working out what caused the downtime: 120 of 1,000` and Stop ends the
  pass, with the simulation results still presented;
- the finished attribution travels with the result set and feeds Drivers,
  the Story, the Charts and the canvas overlays. None of them replays the
  log again.

If the event log was not recorded, no attribution is made. The Drivers tab
says what to turn on, as it did before. If the replay itself fails, the
result set is still presented (the background path adds a note to the
report saying why the drivers are missing).

The lazy path is still there as a fallback for a result set without a
precomputed attribution (an older result, a stopped pass, a step run).
For a large log it shows a cancellable progress dialog instead of silently
freezing the window. Cancelling only abandons the extra pass; the
simulation results stay on screen, the tab says the pass was cancelled,
and opening the tab again offers to work them out.

`raptor.api.insight.attribution.attribute_batch` is the one entry point for
the background worker, the synchronous run and `raptor.api.run` (so the
CLI and the MCP server). It adds the capacity split, mission phases, the
hierarchy roll-up and the time unit in one place, and it keeps the capacity
sink paired with its own run when a run has no event log, so the worker and
the synchronous run cannot drift apart.
