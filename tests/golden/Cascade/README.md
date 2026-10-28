# Fixture: Cascade — a real bundled Student Edition example (SOLVED, bit-exact)

Captured 2026-08-15 from Raptor 7 Student Edition under Wine, from the
Student Edition's own bundled `Examples/Cascade.rbd` (13 blocks, 22
nodes, 47 links) — NOT hand-built, the first real unmodified example this
project ran through `orchestrator.run_simulation`. `NumRuns=5`.

## Status: SOLVED 2026-08-15 — all 5 runs bit-exact (fp32 noise aside)

Getting here took three separate fixes, each exposed by this one fixture
because it's the first with enough scale/complexity to exercise them:

1. **RNG**: the `Normal`/`LogNormal`/`Gamma`/etc. transform — see
   `tests/golden/NormalProbe/README.md` and `rng.py`'s module docstring
   for the SIMSCRIPT II.5 Appendix C breakthrough.

2. **Node fan-out topology (`TestFail`)**: a single-input relay node
   (e.g. `"n1"`, id 16, `in_paths=[5]`, `good_paths=1`) that itself fans
   out to multiple downstream nodes must NOT be independently logged when
   its one upstream path fails — real Raptor's `RunEFPA` computes each
   node's `efpaConnectTo`/`efpaConnectPath` via `RBDNodeObj.TestFail`,
  a recursive reachability walk that PRUNES
   any node fully starved by the failure and substitutes its own
   downstream instead, so the status change surfaces on `"NB2-Pri"`/
   `"NB3=Pri"` (real K-out-of-N nodes) rather than the pass-through relay.
   `graph.py`'s original `build_efpa_graph` used the simpler NRBD walk
   (`_walk_forward`) directly for this, which is right for `nrbd_connect_
   to`/`next_node_link_ids` and every block/event's `downstream_link_id`, but wrong
   for a node's own `propagates_to_node_ids`/`propagates_to_link_ids` whenever a fan-
   out sits behind a trivial relay — invisible in every simpler fixture,
   which are all single-path chains. Fixed by porting `TestFail` as a
   second pass in `graph.py` (see that module's docstring for the full
   design) — this is what actually fixed the divergence found at
   `44.461693` in this fixture's earlier investigation.

3. **End-of-run tie-breaking for all 13 blocks**: once (1)+(2) were
   fixed, every run matched bit-exactly except the very last few lines —
   13 "Done End_sim" lines, one per block, all logged at the same
   timestamp (`run_length=1000.0`). Real Raptor's order for these is
   ALWAYS block creation-ID order (1..13), identically across all 5 runs
   regardless of each run's own random draws — proving the real engine
   finalizes still-active blocks in one fixed end-of-run pass, not via
   per-block scheduled events competing on the scheduler's tie-break.
   `orchestrator.py`'s `block_run_process` was internally self-scheduling
   its own "Done/End_sim" wake-up exactly at `run_length` (required for
   `test_block_lifecycle.py`'s single-block direct-vs-scheduler
   equivalence tests, so that mechanism itself is untouched), and with 13
   blocks sharing one scheduler those wake-ups raced on `Scheduler`'s
   insertion-order tie-break (confirmed correct for *real* simultaneous
   events via `tests/golden/SharedStreamTie`, just not for this case).
   Fixed in `run_simulation`: halt the scheduler strictly before
   `run_length` (`math.nextafter(run_length, -math.inf)`) so none of
   those N per-block wake-ups ever fire, then finalize EVERY block
   unconditionally (not just non-`Done` ones — a block already `Done`
   from an earlier real terminal event, e.g. spare-pool exhaustion's
   `"No_spares"`, still gets its own separate trailing `End_sim` line;
   confirmed against `tests/golden/FiniteSparePool`'s real capture) in
   `blocks_by_id` creation-ID order, in one deterministic closing pass.

Verification: all 5 runs, 9638 compared lines, 0 structural mismatches
(7320 exact + 2318 fp32-noise-only). Included in `test_orchestrator.py`'s
passing parametrized fixture list.

## Files

- `input.rbd` — copied verbatim from the Student Edition's own
  `Examples/Cascade.rbd`.
- `legacy/rpt_cascade_events.txt` — event log, 5 runs, 9660 lines.

## What this is for

The first real (not hand-built) bundled Student Edition example verified
bit-exact end-to-end, and the fixture that drove three separate root-
causes: the RNG transform, `TestFail`'s node-fan-out pruning, and
end-of-run tie-breaking. Now a standing regression test, same as every
other fixture.
