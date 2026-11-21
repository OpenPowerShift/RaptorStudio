# Downtime drivers: when they are calculated

Drivers replay the event log to attribute each system outage to the element
that started it and the state that held it down. That replay is done **once,
at the end of the run**, not when the user first opens a chart:

- the background worker calculates it after the batch, while the event log
  and the run model are hot;
- the synchronous (Animate) path does the same before presenting results,
  using the run progress bar (`Drivers 120/1,000`);
- the finished attribution travels with the result set and feeds Drivers,
  the Story, the Charts and the canvas overlays. None of them replays the
  log again.

If the event log was not recorded, no attribution is made. The Drivers tab
says what to turn on, as it did before.

The lazy path is still there as a fallback for a result set without a
precomputed attribution (an older result, an interrupted pass, a step run).
For a large log it shows a cancellable progress dialog instead of silently
freezing the window. Cancelling only abandons the extra pass; the
simulation results stay on screen.

`raptor.api.insight.attribution.attribute_batch` is the one entry point for
both GUI paths. It adds the capacity split, mission phases, the hierarchy
roll-up and the time unit in one place, so the worker and the synchronous
run cannot drift apart.
