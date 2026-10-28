# Fixture: ResourceRepairOrderProbe2 (control)

See `../ResourceRepairOrderProbe/README.md` for the full rationale.
This is the CONTROL variant: `ResHolder`'s repair is `Fixed(21)`, so it
releases the single `Techs` unit at t=26 while `ResourceWaiter` fails at
t=25. `ResourceWaiter` therefore genuinely waits one time unit and a
`RepHold Waiting_for_resource(s)` line is expected in real RAPTOR too.

Built by an internal build script.
