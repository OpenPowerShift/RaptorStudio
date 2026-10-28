# IsolateCold: an extract with a cold-standby node and a shared resource

`input.rbd` is `raptor extract tests/golden/LadL8CEvents/input.rbd --hierarchy H11 -o input.rbd` (defaults: dependencies up, pools
full). The hierarchy holds a cold-standby node (`J10`) and uses the resource Techs, shared with 28 blocks outside. Real Raptor 7
opened and simulated it (20 runs, 3000 h, every box but interim cost ticked); there are system failures in the runs. The port
reproduces all eight files byte for byte (dates aside).

Captured 2026-10-10 via kwin-mcp.
