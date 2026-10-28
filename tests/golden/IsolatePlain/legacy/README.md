# IsolatePlain: a hierarchy extracted by `raptor extract`, run in real Raptor

`input.rbd` is `raptor extract tests/golden/HierarchyExample/input.rbd --hierarchy 650path -o input.rbd`
(defaults: outside dependencies up, pools full, streams kept). Real Raptor 7 Student opened it and
simulated it (10 runs, 730.5 h, every Files & Reports box ticked); the port reproduces all eight
files byte for byte (events, End of Run and Final Results apart from the date line).

Captured 2026-10-10 via kwin-mcp. This is the fidelity anchor for the "run one hierarchy" extension
(`raptor/api/isolate.py`): the extract is an ordinary model, and the port and real agree on it.

Finding (2026-10-10): the first extract crashed real Raptor on open ("MODSIM Application ... Click OK to
exit.") because `COMMENTS` still held comments about elements the extract no longer has. Real looks
each comment's element up by (kind, id). Comments are now filtered.
