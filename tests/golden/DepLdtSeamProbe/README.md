# DepLdtSeamProbe

Built by an internal build script (clone of `Ch07Dependent`;
block and run settings only). **Awaiting capture** (no `legacy/` yet).

## The question

Does real Raptor put a dependent block (`DepType=RBDBlock`) through the same
`Repair;` TELL boundary as an ordinary block, between its `Failed` line and its
`RepHold` line?

Casing (1) and Magnets (3) are ordinary; Armature (2) and Brushes (4) follow
PowerSupply (5), which never fails. All four have life 10, pre-LDT 2, repair 3,
post-LDT 1, so all four fail at t=10 and again at t=26. Look at the lines at
`10.000000`:

| Real shows | Means | Merge does |
|---|---|---|
| all four `Failed` first, then all four `RepHold` | dependents cross the boundary too | give the dependent path the seam hop |
| `Armature Failed` then `Armature RepHold` straight away (and likewise Brushes), ordinary blocks' `RepHold` at the end | the port is right | keep "no seam hop" as a dependency-policy setting |
| anything else | a third behaviour | capture again; do not merge yet |

The port currently produces the second pattern:

```
10  Casing   Failed
10  Armature Failed
10  Armature RepHold  LDT
10  Node end Bad / System Red
10  Magnets  Failed
10  Brushes  Failed
10  Brushes  RepHold  LDT
10  Casing   RepHold  LDT
10  Magnets  RepHold  LDT
```

The lines at 15/16 (`Repaired`, `postLDT`, `End_Post-LDT`, `Good`) decide the same
question for the post-LDT and the `Run;` boundary.

## Capture

Run length 40, 1 run. Tick **Detailed event log**, **End of run** and **Final
results**. Leave Interim Costs off. Launch with `/sd`.
File as `legacy/rpt_events_DepLdtSeamProbe.txt`, `legacy/rpt_end_of_run_DepLdtSeamProbe.txt`,
`legacy/rpt_final_results_DepLdtSeamProbe.txt` and `legacy/simDetails_DepLdtSeamProbe.txt`.
