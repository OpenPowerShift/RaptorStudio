# Fixture: NormalProbe.rbd — Normal-distribution RNG probe (SOLVED 2026-08-15)

## Update 2026-08-15: SOLVED via SIMSCRIPT II.5's Appendix C

CACI (Raptor/Modsim II's runtime vendor) made another major simulation
language, SIMSCRIPT II.5, and its manual ("Building Simulation Models with
SIMSCRIPT II.5") prints the actual source of every random-deviate
generator in Appendix C — including `normal.f`, confirmed to share the
SAME runtime as Modsim II by its `SEED.V(1..10)` table matching
`FETCH_RAPTOR_SEED` exactly and using the identical base-generator
multiplier (630360016) and modulus (2^31-1). `normal.f` is NOT the
textbook Marsaglia polar method it superficially resembles: `X` is drawn
on `(0,1)` (not `(-1,1)`) paired with `Y` on `(-1,1)`, and the final scale
factor uses a FRESH uniform draw's log (`sqrt(-2*ln(fresh_u))/S`), not
`ln(S)` itself — three draws per accepted iteration, not two. See
`rng.py`'s `_normal` docstring for the exact algorithm and module
docstring for the full shared-CACI-runtime evidence.

**Verified bit-exact**: `tests/unit/test_normal_probe.py` replays block-
lifecycle simulation runs of this exact fixture and diffs every line
against the real captured log — all 500 real runs (13,590 lines) match
with zero structural mismatches. The apparent "no match" result documented
below was a real algorithm miss (every hypothesis tried WAS wrong) — this
fixture's own real captured data is what confirmed the correct one, once
found via the SIMSCRIPT manual rather than more guess-and-check.

This ALSO fixed `_gamma`/`_erlang`/`_beta`/`_binomial` (same Appendix C
listing, not independently fixture-verified each on its own, but high
confidence given `_normal`'s exact match) and confirmed `_exponential`/
`_weibull`/`_uniform_int` were already correct by coincidence.

---

## Original investigation (below, kept for the historical record)

Captured 2026-08-15. Same structure as `UniformProbe.rbd` (one block, start/end
nodes, independent streams 1 and 2) but with **Failure Distribution = Normal
(Mean=10, StDev=1)** on stream 1 and **Repair Distribution = Normal (Mean=5,
StDev=1)** on stream 2. Means chosen large relative to StDev specifically so
draws are ~10 sigma from zero and never hit the engine's negative-draw clamp
(`IF drawn<0.0 ... drawn:=0.000001`, see  `DrawNumber`), which
would otherwise silently corrupt the data.

## Result: NOT YET SOLVED

Unlike `UniformProbe.rbd` (base LCG solved cleanly, see that fixture's
README), **no candidate Normal-generation algorithm tested so far matches**.
Tested against `stream1_fail_draws_mean10.txt` (Z = draw − 10) and
`stream2_repair_draws_mean5.txt` (Z = draw − 5), using the **confirmed base
LCG** (`x_{n+1} = 630360016 * x_n mod 2147483647`, seeds `FetchRaptorSeed(1)
= 2116429302` / `FetchRaptorSeed(2) = 683743814`):

- **Box-Muller**, fresh 2 uniforms per Normal call (both cos-first/sin-first,
  both u1/u2 orderings) — no match.
- **Box-Muller with caching** (2 uniforms produce a pair of Gaussians, second
  one reused on the next call, standard optimization) — no match.
- **Marsaglia polar method** (rejection sampling, v1²+v2²<1) with caching —
  no match.
- **Inverse-CDF** (`statistics.NormalDist().inv_cdf(u)`, one uniform per
  Normal call) — no match.
- **Sum-of-12-uniforms minus 6** (classic CLT approximation, common in older
  simulation libraries) — no match.
- Ruled out **cross-run contamination** as the cause: re-tested against
  Run 1's sequence in isolation (6 fail events, 6 repair events, both
  entirely within run 1) — still no match. So this isn't the same kind of
  "hidden extra draw between runs" issue documented in `UniformProbe`.

None of these came close (differences are O(1), not O(1e-6) — this is a
clean miss, not a rounding-precision near-match on any of them).

## Hypotheses not yet tested (next steps)

1. **Normal() doesn't consume from the same LCG sequence as UniformReal()
   at all** — e.g. a separate internal state per distribution type within
   `RandomObj`, or a re-seeded/hashed substream. This is the leading
   suspect given how cleanly every "shares the stream" hypothesis failed.
2. **Static analysis of the actual runtime DLL** — we have the real
   `caci32-7.dll` (and `caci32-6.dll`) from the installed app. Disassembling/decompiling the Normal-
   distribution routine directly would settle this far more reliably than
   further black-box guessing. Worth doing before more guess-and-check.
3. Double check the `Mean`/`StDev` UI fields really map 1:1 to
   `dataArray[1]`/`dataArray[2]` as passed to `Normal(dataArray[1],
   dataArray[2])` — re-derive from Raptor 7's `BlockPropObj` param
   ordering rather than assuming.
4. Try other rejection-based or ratio-of-uniforms Gaussian methods from the
   same 1990s simulation-textbook lineage as the confirmed Lehmer generator
   (Law & Kelton catalogs several) — lower priority than (1)/(2).

## Static-analysis lead (2026-08-15)

Searched all the runtime DLLs for the raw byte pattern of the confirmed
multiplier (`630360016`) and modulus (`2147483647`) constants:
- `630360016` doesn't appear literally as a 4-byte immediate anywhere —
  expected if the multiply-mod is implemented via Schrage's method (avoids
  32-bit overflow) rather than a single `imul`+`mod`, which decomposes the
  constant into `q = m div a` / `r = m mod a` rather than storing `a` whole.
- `2147483647` appears 38 times in **`caciuser.dll`** — far more than any
  other DLL (`caci32-6/7.dll` and `cacilm32-4.dll` have 5 each, `Raptor7Student.exe`
  has 8, `cacia-*.dll`/`caciu-*.dll` have 0). Strong signal that
  `caciuser.dll` is where the RNG/statistics runtime (`RandMod`/`StatMod`
  equivalents) actually lives — worth targeting specifically.
- `objdump -d` on `caciuser.dll` only linearly disassembles ~140 lines
  before stalling (looks like it only gets through the CRT/DllMain
  preamble) — this machine doesn't have a real decompiler (no Ghidra/
  radare2/IDA installed). Getting further requires proper tooling, not
  more `objdump` guessing.

**Update 2026-08-15, continued with radare2 (now installed)**: the
`caciuser.dll` lead was a red herring — `2147483647` is also the generic
32-bit `MAX(INTEGER)` sentinel, and a search of all runtime DLLs for
transcendental FPU opcodes (`fsqrt`/`fsin`/`fcos`/`fyl2x` etc.) found **none
at all in `caciuser.dll`**. The real math is in **`Raptor7Student.exe`
itself** (`FYL2X` ×2, `FSQRT` ×2, `FSIN` ×18, `FCOS` ×17) — the simulation
kernel (`RandMod`/`Analyze` equivalents) is evidently statically linked into
the main executable, not split into a DLL.

Found the exact generic math-runtime helpers at fixed addresses (file
offset = VA − `0x400000`, confirmed against a known hit):
- `0x5e4f70` — `log10` (`fldlg2; fld; fyl2x; ret`)
- `0x5e4f80` — `ln` (`fldln2; fld; fyl2x; ret`)
- `0x5e4f90` — `exp` (`fldl2e; fmul; frndint; ...; ret`)
- `0x5e4fb0` — `sqrt` (`fld; fsqrt; ret`)

These are tiny, generic, reusable helpers (not the distribution code
itself) — the next step was to find their callers. **Blocked again**: a
full-binary scan for direct `call` (`E8 rel32`) instructions targeting
these addresses found **zero** matches, even though the helpers are clearly
real code. The surrounding disassembly shows sibling helpers (e.g.
`floor`/`ceil` wrappers right before them) ending in `jmp 0x5f5576` to a
**shared address** rather than `ret` to their caller — i.e. these are
reached via some kind of **jump-table/dispatch mechanism**, not plain
`call` sites. Resolving that requires real control-flow/dataflow analysis
(finding the dispatch table and what indexes into it) — beyond what
`radare2`'s automatic analysis could complete in reasonable time on this
6MB binary (`aa` was tried twice, ~4+ minutes each, didn't finish/produced
no xrefs) and beyond what direct byte-pattern scanning can do at all.

**Conclusion for tonight**: static analysis has hit a real wall that needs
either (a) radare2's analysis actually completing (try again with more
time/patience, or a lighter analysis level, or narrowing the address range
manually first), (b) a proper decompiler (Ghidra) that resolves jump
tables automatically, or (c) dynamic analysis (breakpoint on the `ln`
helper address under `winedbg` while running a live sim, then walk the
call stack) — dynamic analysis is probably the more promising path next
time, since it sidesteps the static jump-table problem entirely by
observing real execution.

Decision: stopped here per user direction — pivoting to starting the
Python implementation using the confirmed base generator (Tier B) with
Normal/Gamma/etc. as an open TODO (Tier C fallback: use an accurate
standard implementation, e.g. Python's own `random.gauss`/`statistics`,
until/unless this is cracked) rather than blocking all further progress on
this.

## Files

- `legacy_event_log.txt.gz` — full event log from the 500-run batch.
- `stream1_fail_draws_mean10.txt` — raw `Operating_time` values (subtract 10
  for Z).
- `stream2_repair_draws_mean5.txt` — raw `MaintTime` values (subtract 5 for
  Z).
