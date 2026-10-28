# IsolateShare: an extract with `--deps system --pools share --streams separate`

`input.rbd` is `raptor extract tests/golden/LadL8BEvents/input.rbd --hierarchy H10 -o input.rbd --deps system --pools share
--streams separate`: two blocks that followed things outside now follow the extract's End node (`system`), the shared spare pool
Seals and the resource Crane are cut to 3/8 of their stock (`share`, rounded down, at least 1), and the six shared-stream draws
have streams 1..6 of their own (`separate`). Real Raptor 7 opened and simulated it (30 runs, 2000 h, every box but interim cost
ticked); the port reproduces all eight files byte for byte (dates aside).

Limit of this capture: the system never failed in any run (Ao = 1, empty failure and down-time files), so it proves that real
opens and runs a file with these rewrites and that the port's engine agrees on it, but it says little about how the scaled pool
behaves under demand. `IsolateCold` is the capture with failures.

Captured 2026-10-10 via kwin-mcp.
