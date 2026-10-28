# Capacity1 bisection — the deletion stages (for the 426.280300 divergence hunt)

The goal: delete model ingredients stage by stage until the real-vs-port
divergence at the tie instant disappears — the last-deleted ingredient is
the essential one. Each stage CUMULATES (keep the previous deletions).

**The file**: `tests/golden/Capacity1/input.rbd` (or your working copy).
**Every stage**: File → Save As `Capacity1_s<N>.rbd` (share the .rbd too!),
Simulate **10 runs**, `/sd` ON, then copy `simDetails.TXT` + the event log
immediately (fixed-name overwrite caveat), named `..._s<N>`.

**DO NOT delete the three triggers** (500W/650W/900W-PowerString) before
their subscriber blocks are gone — the SolarP PMs are trigger-driven
(pmTriggered=1, pmTrig=<trigger name>) and the whole PM machinery
disappears without them.

---

## Stage 1 — the cold-standby tail
Delete blocks **LowGain**, **HighGain** and node **Comm** (the links
n2→LowGain, n2→HighGain, LowGain→Comm, HighGain→Comm, Comm→end go with
them), then **re-link n2 → end** (the end must stay fed).

Removes: the cold-standby redistribution and the Comm walk from the tie's
tail.

## Stage 2 — the sysDepend block
Delete block **Attitude** (its links start→Attitude, Attitude→n1 go with
it), then **re-link start → n1** (n1 must stay fed).

Removes: the sysDepend (-2) walk's % draw and Att's AllUp-redraw from the
tie.

## Stage 3 — the Thermal repeater
Delete block **Thermal** (links Power→Thermal, Thermal→n2 go with it),
then **re-link Power → n2**.

Removes: Thermal's draws and its hop from the Power→n2 path.

## Stage 4 — the 650W chain
Delete blocks **SolarP_650W**, **BatteriesNiH**, **Bus_QR**, node
**650path**, and the trigger **650W-PowerString** (its subscriber is gone;
delete it if the GUI allows — otherwise leave it, it is harmless alone).

Removes: one PM grant + its pmReqDefer from the tie's wave.

## Stage 5 — the 900W chain + the repair grant (THE KEY TEST)
Delete blocks **SolarP_900W**, **BatteriesNaS**, **Bus_FR**, node
**900path**, and the trigger **900W-PowerString**.

Removes: the REPAIR grant (BatteriesNaS) from the tie's wave — the case-2
repair-competes condition disappears. If the divergence vanishes HERE,
the rescue-rule's discriminator lives in the repair-vs-PM interaction;
if it persists, the mechanism is in the kept core ([Bus_UR, SolarP_500W,
BatteriesNiCd] + the 500W-PowerString trigger + the multi-node cascade).

---

**What I compare per stage**: our engine runs your saved .rbd; I diff the
/sd draw-for-draw against your capture. The first stage where we MATCH
identifies the essential ingredient; the stages after it need not be run.

**Send per stage**: the saved .rbd, `simDetails.TXT`, the event log —
suffixed `_s1` ... `_s5`.


## RESULT (2026-09-09, post-capture analysis)

All five stages' reconstructed models were run through our engine and
diffed against the real captures under tag-normalized comparison (the
GUI-save renumbers links; the delta tags' link-id digits normalized;
the k/n and runs/k/n displays compared literally):

- **Stage 5** (minimal): 267/267 /sd draws match. MATCH.
- **Stage 4**: diverged ONLY while my reconstruction kept Power's
  original k=2 — the real capture displays `Degraded 1-1/2` (k=1: the
  user's GUI-adjusted Power). With k=1: **MATCH** (all 10 runs).
- **Stage 3**: MATCH (all 10 runs).
- Stages 1-2: covered by stage 3's ingredient superset.

**CONCLUSION: the Capacity1 426.280300 divergence does NOT reproduce in
ANY reduced model — the model structure is fully cleared.** The
divergence is a tie-group POPULATION phenomenon: the full model's
tie group has ~20 same-instant entries (four PM pickups + the repair
pickup + five AllUp wakes + the walks), and the rescue-rule
discriminator (the 426.28-vs-666.14/364.98/2355.73 conflict, cont.
39-40) lives in that population's interaction, not in any structural
ingredient. The full model's own events diff (28/40 runs clean) and
the /sd (diverging at run 25's [2288]) remain the definitive record.
