#!/usr/bin/env python3
"""
Verify the recovered Raptor 7 / Modsim II base RNG algorithm against real
draws captured from the engine. See README.md in this directory for how
the fixture files were generated.

Recovered algorithm (Lehmer / multiplicative congruential generator):

    x_{n+1} = (A * x_n) mod M
    output  = x_{n+1} / M

    A = 630360016
    M = 2147483647   (2^31 - 1)

Seeding: SetSeed(s) sets x_0 = s directly (the first draw is x_1, i.e. one
step of the recurrence applied to the seed, not the seed itself).

Seeds come from Raptor 7's FetchRaptorSeed table:
    FetchRaptorSeed(1) = 2116429302   (stream1_fail_draws.txt)
    FetchRaptorSeed(2) = 683743814    (stream2_repair_draws.txt)

Each stream matches the captured output for roughly the first few hundred
draws (modulo float32-vs-float64 last-digit rounding), then desyncs by a
fixed offset: some engine code path consumes a draw the event log does not
record as "Operating_time="/"MaintTime=". The script prints where each
stream first diverges.

Run: python3 verify_rng.py
"""
import pathlib

A = 630360016
M = 2147483647

HERE = pathlib.Path(__file__).parent


def lehmer_stream(seed: int):
    x = seed
    while True:
        x = (A * x) % M
        yield x / M


def verify(seed: int, fixture_path: pathlib.Path, label: str) -> None:
    gen = lehmer_stream(seed)
    lines = fixture_path.read_text().split()
    first_desync = None
    fp32_noise = 0
    clean = 0
    for i, tok in enumerate(lines, start=1):
        want = float(tok)
        got = next(gen)
        diff = abs(got - want)
        if diff < 5e-7:
            clean += 1
        elif diff < 2e-6:
            fp32_noise += 1
        else:
            if first_desync is None:
                first_desync = i
            break
    print(f"{label}: seed={seed}")
    print(f"  draws in fixture       : {len(lines)}")
    print(f"  exact matches before desync : {clean}")
    print(f"  fp32-rounding-noise matches : {fp32_noise}")
    print(f"  first real desync at draw # : {first_desync}")
    print()


if __name__ == "__main__":
    verify(2116429302, HERE / "stream1_fail_draws.txt", "stream1 (fail, FetchRaptorSeed(1))")
    verify(683743814, HERE / "stream2_repair_draws.txt", "stream2 (repair, FetchRaptorSeed(2))")
