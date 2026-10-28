# Fixture: Hierarchy1-7.rbd — large real-world file, no legacy engine run needed

Not a captured Wine run — this is `Hierarchy1-7.rbd`,
a real file that ships in the upstream Raptor 7 source repo itself (a demo/
sample model, "Version7_6.6.2 'Butters'", dated 2004 — older format version
than the `Events`/`UniformProbe` fixtures, which are `Version7_7.0.09`).
Copied in here unmodified as `input.rbd` purely for its size and diversity —
**149 blocks, 61 nodes, 309 links** — as a stress test for the `.rbd`
reader/writer/typed-mapper beyond the two small single-purpose fixtures.

Despite "Hierarchy" in the name, it has **zero** hierarchy/spare-pool/
resource/phase/trigger objects (`Hiers=0`, `SpPools=0`, `ResPools=0`,
`Phases=0`, `Triggers=0`) — so it does NOT unblock the still-missing typed
mappers for those sections. A genuinely hierarchy-bearing fixture is still needed for that.

## What this confirmed (2026-08-15)

- The `.rbd` column layout is **stable across the version gap** (6.6.2 →
  7.0.09 seen in the other fixtures) — identical column counts for every
  section (87 for BLOCK_DATA, 20 for NODE_DATA, 13 for LINK_DATA, 26 for
  SYSTEM_DATA). Don't assume this holds for *arbitrarily* old/new versions
  without checking, but it's a useful data point.
- Full structural round-trip (`render_rbd(parse_rbd(x))` reproduces
  `parse_rbd(x)` exactly) holds on this much larger file — not just the
  two small fixtures.
- All 150 `BLOCK_DATA` rows (149 real + 1 default template), 61
  `NODE_DATA` rows, and 309 `LINK_DATA` rows convert through
  `fileio.typed` without any exception.
- Distribution diversity beyond the two small fixtures: this file's blocks
  use Exponential (4), LogNormal (7), and None (18, the default/no-distro
  placeholder) — still doesn't exercise every one of Raptor's 22
  distributions, so `typed.py`'s per-distro coverage is still only
  partially real-world-tested.
- `SysStreamsArray` is the same `[7, 8, 9, 10, 70, 71, 72, 73, 74, 75, 101]`
  factory-default seen in both other fixtures — consistent with
  `InitFactorySettings` in Raptor 7 setting this
  default and it just never having been customized in any fixture we have.
