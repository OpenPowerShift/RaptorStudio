# Fixture: HierarchyExample — the real bundled Student Edition `Hierarchy.rbd`

`input.rbd` copied verbatim from the Student Edition's own
`Examples/Hierarchy.rbd`. `legacy/rpt_events_hierarchy.txt` — real
capture, 10 runs. NOT the same fixture as `tests/golden/SingleHierarchy`
(hand-built, Phase 3's own hierarchy verification) or `tests/golden/
Hierarchy1-7` — this is the vendor's own example, distinguished by name.

## Status: SOLVED — all 10/10 real runs byte-exact, promoted to `test_orchestrator.py`

**2026-08-20: fully resolved.** The last bug was in
`dependency.py`'s mixed-percentage `"GoToPM"` handling: it unconditionally
induced an `"OBPM"` interrupt, but source only
does that when the dependent block `usesPM` — otherwise the draw is
silently consumed with no visible effect. `SolarP_900W` has `usesPM=0`,
so its `"GoToPM"` draws (e.g. t=681.249451 in run 8) were wrongly
transitioning it to Idle. Gating `induce(dep_block, "OBPM")` on
`dep_block.uses_pm` fixed EVERY remaining divergence across runs 2, 3, 4,
8, 9 in one shot — not just the run it was found in. This fixture is now
`test_orchestrator.py`'s `HierarchyExample` regression case (10 runs,
2084 lines, `min_match=2080`). See `project_raptor_migration.md` memory
(2026-08-20 entry) for the full diagnostic trail. The history below is
kept for context on how each earlier layer (cold standby, ResendBackFlow,
GetStartCond, RapTriggerObj/Trigger events) was found and ported.

This has turned out to be the single most demanding fixture in the whole
project — every gap closed revealed another, unrelated one underneath —
but as of 2026-08-19, real progress: a real `simDetails.TXT` capture (see
`reference-simdetails-flag` in memory) resolved BOTH of the two things
that were blocking this fixture, and cold standby's full mechanism
(kickoff, mid-run switch-off/on, the async `PerformSwitch` timer) now
runs BYTE-FOR-BYTE identical to the real capture across the fixture's
entire `LowGain`/`HighGain`/`Comm` story: kickoff redistribution,
`HighGain`'s real failure time, `Comm` going fully `Bad` the instant
`HighGain` fails (since `LowGain`'s own link is mid-async-switch, not
instantly available), the recovery exactly `auto_switch_time` (`0.5`)
later, and `HighGain`'s complete LDT/repair/postLDT cycle back to Good
with `LowGain` switching back off. See `cold_standby.py`'s own module
docstring for the full technical writeup. The fixture diverges only
AFTER that point, into a genuinely separate, previously-untouched
mechanism (`RapTriggerObj`/`Trigger` events, `"...-PowerString"`, tied to
an unrelated part of the model) — not cold standby, not `ResendBackFlow`.

**Correction to an earlier note in this file**: an earlier investigation
guessed the two blocks' `SimStart=3` config field controlled their
initial op_status directly (i.e. that they're configured to literally
start `Standby`) and, on finding no such reference in the engine,
wrongly concluded `SimStart` doesn't matter at all. BOTH were wrong in
different ways: `SimStart` (the FILE's column name) maps to `sim_start_
type`/`simStartType` at the engine level, and DOES matter — it's read by
`GetStartCond` (`block_lifecycle.get_start_
cond`), called for EVERY block at kickoff. `LowGain`/`HighGain` really do
start `Running` (that part was right), but `simStartType=3` ("up, some
complete") ALSO draws a real "how much of this block's life is already
used up" value against its own `amountExhausted` field, which
(`LowGain`'s own `amountExhausted=2000.0`, the only nonzero value in
this whole fixture) explained a real, previously-unresolved RNG-draw
mystery -- see `get_start_cond`'s own docstring.

### Resolved

1. **Multi-hierarchy links.** Node `16` connects DIRECTLY into three
   separate hierarchies (`connect_t_ref="RBDHier"`, no intervening
   `RBDNode`). Fixed in THREE places, discovered one at a time by
   actually running the file and following each new crash (`graph.py`):
   - `_walk_forward`/`_node_forward_targets`: a `RBDHier` forward-walk
     target now redirects to that hierarchy's own `in_id` node
     (`type_node=4`, a real `RBDNode`).
   - `_run_test_fail`'s FINAL `propagates_to_node_ids` computation re-derives
     each target node id straight from `links_by_id[lid].connect_to_id`
     -- which is the RAW hierarchy id for a `RBDHier`-targeting link, a
     SEPARATE bug from the one above even though it looks like the same
     redirect (the walk itself used the already-fixed `next_node_ids`
     correctly; only this final re-derivation step reverted to raw link
     data). Confirmed necessary via a direct `KeyError: 23` (a hierarchy
     id where a node id was expected).
   - `in_paths_array` was never computed for `type_node=4` (In) nodes at
     all -- added, using the same Hier-redirect logic, needed once an In
     node actually became a real `reconfigure_node` target thanks to the
     first two fixes.
2. **SLO** for `SPARE_POOL_DATA` pools (`Cells` has RSO+ESO+SLO all
   three). Ported `pool.order_stock_process` (near-duplicate of the
   already-verified `order_a_spare_process`).
3. **Custom-sparing RSO/ESO replenishment** (`Thermal`, `init_stock=1`).
   Ported `RBDBlockObj.GenerateSpares`/`OrderASpare`
   (`pool.generate_custom_spares_process`/`order_a_custom_spare_process`),
   `spares_remaining` promoted to a real per-block `ResourceQueue`.
4. **Mixed-percentage dependency draws** (`DepNothing`/`DepIdle`/`DepPM`/
   `DepFail`, `dependency.draw_dependency_action`) -- a real RNG draw on
   the DEPENDENT block's own fail stream, cached once at `SimTime=0.0`
   (`TzeroAction`) and reused for every later `Down` trigger. Traced
   `CombinedStress` (phase × standby ÷ growth-decay) down to the two
   interrupt reasons that needed it (`"OBPM"`/`"Induced_Failure"`,
   `block_lifecycle.block_run_process`'s own new interrupt handling) and
   confirmed -- **WRONGLY, see below** -- that it could be treated as
   always `1.0` for this fixture.
5. **`locDepend` (`DepNum=-1`) and positive-`DepNum`+`RBDNode`
   dependencies**, generalizing what was previously `sysDepend`-only:
   `dependency.resolve_dependency_node_id` unifies all three dependency
   kinds into "which node does this block's dependency actually target,"
   and `back_flow_dependencies_from_node`
   (renamed+generalized from `back_flow_dependencies_from_end_node`) /
   `repair_completion_status` (renamed `end_node` parameter to `dep_node`)
   both now dispatch off that resolved node instead of assuming it's
   always the End node. `orchestrator._Cascade._cascade_node`'s own
   dependency-dispatch call moved OUTSIDE the `type_node==3`-only guard
   it used to live in (confirmed by reading source: `ChangeNodeState`'s
   own `BackFlowDependencies` call sits AFTER the End-node-specific
   `SystemUp` handling closes, not nested inside it -- it was always
   meant to be unconditional on node type).

Each of 1-5 above was verified not to regress any of the 127 already-
passing tests. Gaps 4/5 together (they're a hard pair -- see the
2026-08-19 investigation note further down for why) got the file running
past the dependency-mechanism error into a NEW error, then past that one
into a genuinely new discovery:

### Still blocking

6. **`ResendBackFlow`'s `specialBackFlow` dispatch (NOT ported).** 7 of
   10 real runs now hit `dependency.resend_back_flow_would_apply`'s own
   already-documented raise (see that function's docstring) -- this fixture is
   the FIRST to actually reach the case that detector exists to catch.
   Still deliberately not implemented (needs `prevDepState`/
   `returnFromMaint` tracking with no way to verify a guess).
7. **Cold standby -- kickoff + mid-run switching now ported
   (`raptor/core/engine/cold_standby.py`), verified through the kickoff
   sequence, but a downstream RNG divergence still blocks full
   verification.** `RunColdLogic`'s kickoff redistribution AND mid-run
   switch-off/switch-on (`"Switched_Off"`/`"Switched_On"` interrupts,
   `block_lifecycle.combined_stress`'s `sbStress` modeling, a scheduler
   barrier so cold-logic runs strictly between every block's Initialize
   and its first stress computation) are all ported and unit-tested
   (`tests/unit/test_cold_standby.py`) against the exact source citations
   in `cold_standby.py`'s own docstring. Verified against the real
   capture BYTE-FOR-BYTE through the full kickoff sequence: both blocks'
   Initialize lines, `LowGain` -> `Standby`/`"Too_many_on"`, the `Node
   Comm` status line, and `LowGain`'s own `CombStress=0.050000` line.
   Still explicitly NOT ported (raises): the "not enough ready paths,
   initiate a NEW switch" async branch (`PerformSwitch` timer +
   `SetSwitchTime`'s probability draw -- no fixture data confirms the
   exact auto/manual/none split or timing) and `BackFlowDependencies
   (NodeStandby/AllUp)`.

   **Right after the verified kickoff sequence, `HighGain`'s own raw
   fail-time draw diverges from the real capture** (`476.905645` here vs
   `10.702389` in the real capture) in a way NOT yet explained by reading
   source -- all 13 blocks share one RNG stream (203), drawn once each in
   a fixed order at kickoff; the real value doesn't match ANY of the 13
   values this port computes in ANY position, so it isn't a simple
   reordering-among-known-values issue. Needs a fresh `simDetails.TXT`
   capture (`/sd` flag, see the `reference-simdetails-flag` memory) to
   pin down the exact draw sequence -- attempted 2026-08-19 but blocked
   on GUI automation not working in this environment (the Wine window's
   client area renders fully black to every screenshot tool tried:
   `scrot`, `import`, `spectacle`, across a fresh process relaunch AND a
   plasmashell restart -- not yet resolved). **An earlier version of
   this note wrongly attributed the divergence to `SimStart` not being
   respected -- that's been corrected above; `SimStart` isn't read by
   the engine at all, so it isn't part of this gap.**

6\. **`ResendBackFlow`'s `specialBackFlow` dispatch -- PORTED 2026-08-19.**
   Turned out to be directly coupled to cold standby, not a separate
   subsystem: `ResendBackFlow` is called from
   `CheckColds` right after cold-logic redistribution, AND from repair-
   completion's own `down_num<=1` self-correcting branch.
   Both are now wired (`orchestrator._Cascade.resend_back_
   flow`, called from `apply_cold_logic`'s aftermath and from
   `block_lifecycle.block_run_process`'s repair-completion handling).
   Ported: `dependency.resend_back_flow` (the real dispatcher, replacing
   the old no-op-only `resend_back_flow_would_apply` detector, kept
   alongside it), the `specialBackFlow`-gated branch and the
   `returnFromMaint=True` branch inside `back_flow_dependencies_from_
   node`'s `"Down"` handling, and `RBDBlock.
   returning_from_maintenance`/`previous_dependency_state` state tracking (the latter field
   already existed; the former is new). Found and fixed a REAL latent
   bug along the way: `time_zero_action`/`previous_dependency_state`/`return_from_
   maint` were never reset between runs (`SetToInitValues`) -- invisible until now since mixed-
   percentage dependencies were only added for this fixture, which
   hadn't verified past run 1 yet. Also found `RunColdLogic`'s own
   "prep" phase (gated by `skipBackflow`) was
   completely unported -- its `BackFlowDependencies(AllUp)` call is a
   genuine no-op for `Comm` (verified precisely, not guessed: nothing
   depends on `Comm` itself), so it's skipped correctly; its Idle-
   candidate-promotion sub-case still raises (no fixture reaches it).
   14 new unit tests (`test_dependency.py`/`test_cold_standby.py`), 146
   total, zero regressions.
7\. **Cold standby -- FULLY PORTED (kickoff, mid-run switch-off/on, AND
   the async `PerformSwitch` timer branch) and VERIFIED BYTE-FOR-BYTE**
   against the real capture, 2026-08-19, across the fixture's entire
   `LowGain`/`HighGain`/`Comm` story. The `SBlink`/`SetSwitchTime` gap
   flagged earlier the same day was real and WAS the missing piece: once
   `sort_cold_links` set `link.sb_link=True` and `SetSwitchTime`
   (drawing from `SysStream[11]`) was wired into the general link-
   status-update path, the async switch mechanism (`_prep_for_switch`/
   `perform_switch_process`) could be built and verified directly
   against the real capture's own exact timing (`Comm` goes fully `Bad`
   the instant `HighGain` fails, recovers exactly `auto_switch_time=0.5`
   later). See `cold_standby.py`'s own module docstring for the full
   writeup.
8\. **`GetStartCond` (`simStartType`) -- PORTED 2026-08-19.** Unrelated
   to cold standby directly, but discovered while chasing the same real-
   capture divergence: `GetStartCond` is
   called for EVERY block at kickoff, unconditionally, and its
   `simStartType=3` ("up, some complete") case draws a block's own fail-
   time REPEATEDLY until it exceeds `amountExhausted`, then subtracts --
   `LowGain`'s own `amountExhausted=2000.0` (the only nonzero value in
   this whole fixture) is why its real kickoff draw sometimes needed a
   second draw and `HighGain`'s own real failure time (`10.702389`) only
   matched once that was accounted for. See `block_lifecycle.get_start_
   cond`'s own docstring.

**Bottom line**: gaps 6, 7, and 8 are all closed and verified. Running
the fixture now proceeds byte-for-byte identical to the real capture all
the way through `HighGain`'s full repair cycle and `LowGain` switching
back off -- diverging only after that, into `RapTriggerObj`/`Trigger`
events (`"...-PowerString"`), a genuinely separate, previously-untouched
mechanism unrelated to anything above.

## 2026-08-19 investigation notes (kept for context)

Of the 10 mixed-`DepIdlePerc` blocks, only ONE (`Attitude`,
`dependency_num=-2`) is `sysDepend` -- the other 9 are `locDepend`
(`SolarP_500W`, `BatteriesNiCd`, `Bus_UR`, `SolarP_650W`, `Bus_QR`,
`BatteriesNiH`) or positive-`DepNum`+`RBDNode` (`SolarP_900W`, `Bus_FR`,
`BatteriesNaS`, all pointed at node `22`) -- which is why gaps 4 and 5
had to be built together (confirmed correct: neither alone would have
changed this fixture's behavior at all).

`OBPM`'s threshold check needed real mean formulas for the 3
distributions that can draw `"GoToPM"` here (`Attitude`: Gamma;
`SolarP_900W`: Weibull; confirmed by reading 's `SetStats`
directly rather than guessing) -- `dependency.fail_mean` ports those
(Gamma/Weibull/Pearson5 only; raises for anything else).

## Files

- `input.rbd` — copied verbatim from the Student Edition's own
  `Examples/Hierarchy.rbd`.
- `legacy/rpt_events_hierarchy.txt` — real capture, 10 runs, kept ready
  for whenever the remaining gaps above are closed.

## What this is for

Full end-to-end regression coverage of the vendor's own bundled
`Hierarchy.rbd` example — cold standby, `ResendBackFlow`, `GetStartCond`,
`RapTriggerObj`/`Trigger` events, and mixed-percentage dependency draws
(`"GoIdle"`/`"GoToPM"`/`"InducedFailure"`/`"DoNothing"`) all exercised
together across 10 real runs, all byte-exact. Part of
`test_orchestrator.py`'s parametrized fixture list.
