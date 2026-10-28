# IsolateDeps: a phased hierarchy with pools, a resource and outside dependencies

`input.rbd` is `raptor extract tests/golden/LadL8BEvents/input.rbd --hierarchy H10 -o input.rbd`
(defaults: dependencies up, pools full). The source has weak-link and cost analysis on, 5 phases, a
spare pool and a resource shared with other hierarchies, and three blocks that follow things outside
H10. Real Raptor opened and simulated it (30 runs, 2000 h, every box ticked); the port reproduces all
eight files byte for byte (dates aside). The source model is GraphicsOff, so the canvas is blank when the
extract is opened in real Raptor: that is the model's setting, not a broken file.

Captured 2026-10-10 via kwin-mcp.

Finding: a `proxy` treatment (an extra block with the master's distributions, not on any Start-to-End path)
hung real Raptor's path analysis at "Analyzing System... 4%", so it was removed; there is no neutral way to
place such a block (see `raptor/api/isolate.py`).
