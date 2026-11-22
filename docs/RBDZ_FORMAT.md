# `.rbdz` — RAPTOR Studio model archive (v1)

`.rbds` stays the plain, diff-friendly model document. `.rbdz` is the
**archive** form: the same model plus the result files it was run with,
inside one ZIP container. Open one and the model and its saved results come
back together; send one to a reviewer and they see the numbers, not a model
that needs re-running.

```text
plant.rbdz
├── model.rbds                     the ordinary .rbds document
├── results/
│   ├── summary.json               run id, model hash, runs, units, time
│   ├── per_run.json               the per-run figures the Overview reads
│   ├── final_results.txt          Raptor 7's Final_Results text
│   ├── final_results_ids.json     element tags for those rows (optional)
│   ├── end_of_run.txt             Raptor 7's End_Of_Run text
│   ├── events.txt                 the event log, runs separated by Begin_Run_n
│   ├── events_ids.json            element tags for the log (optional)
│   ├── attribution.json           downtime drivers and outage timeline
│   ├── availability_plot.txt      recorded Ao plot rows (optional)
│   ├── capacity.txt               recorded capacity rows (optional)
│   ├── cost.txt                   recorded interim-cost rows (optional)
│   ├── key_parameters.txt         per-run data files (optional)
│   ├── failure_times.txt
│   ├── down_times.txt
│   ├── end_times.txt
│   └── importance.txt
└── manifest.json                  format, version, entries, sizes, hashes
```

`model.rbds` is required. Every archive written by RAPTOR Studio also
contains `manifest.json`, and every `results/` entry is optional, so a
model-only archive is a valid `.rbdz`. A container with a model but no
manifest still opens (it simply carries no version or result metadata), but
a manifest that is present must be a JSON object for this format. The model
is the same document described in [`RBDS_FORMAT.md`](RBDS_FORMAT.md) and is
read by the same schema-checked reader; nothing in the archive is executable.

## Manifest

```json
{
  "format": "raptor-studio-archive",
  "version": 1,
  "generator": "RAPTOR Studio 0.11.0",
  "created": "2026-11-21T14:05:00",
  "compression": "balanced",
  "model": {
    "name": "model.rbds",
    "bytes": 15294,
    "sha256": "…"
  },
  "results": {
    "run_id": "a1b2c3d4e5f6",
    "model_hash": "9f86d081884c7d65…",
    "num_runs": 400,
    "units": "hours",
    "run_at": "2026-11-01T12:51:00",
    "model_name": "ProjectX_BESS_Availability_v2.rbds",
    "files": {"final_results.txt": {"bytes": 140782}}
  },
  "files": {"model.rbds": {"bytes": 15294}, "…": {}}
}
```

`run_id` is the short digest of the run manifest and `model_hash` is the
hash of the model **as it was run**; the archive's own `model.sha256` is the
model **as it was saved**, which differs if the model was edited after the
run. `summary.json` carries the complete run manifest.

`created` is archive metadata, not model content, so it does not affect a
model diff. The model hash is over the exact `model.rbds` bytes and is
checked on open when the manifest has one, so a hand-edited model in an
existing archive is reported rather than silently shown. A reader also
refuses a missing `model.rbds`, a manifest for another product, a manifest
that is not a JSON object or is larger than 8 MiB, a format version newer
than it understands, absolute or `..` paths, drive-letter/`:` components,
duplicate entry names, more than 10,000 entries, and an archive that
declares more than 4 GiB unpacked for the entries it is about to read
(a model-only open counts only `model.rbds` and `manifest.json`).

## Compression: what was measured

The result files are Raptor 7's own text: one line per event, per run and
per plot sample. They repeat column names, timestamps and state words
constantly, so a general-purpose compressor captures almost all of the
available saving. Measured on a real 400-run batch:

| File | Raw | deflate -6 | bzip2 -9 | LZMA -6 |
|---|---:|---:|---:|---:|
| `end_of_run.txt` (634k lines) | 39.3 MB | 2.46 MB (6.3%) | 0.58 MB (1.5%) | 0.53 MB (1.4%) |
| `availability_plot.txt` | 475 MB | 95 MB (20%) | 80 MB (17%) | 45 MB (9.5%) |
| `.rbds` model (typical) | 15 KB | 2.4 KB | 2.3 KB | 2.1 KB |

Time matters as much as the ratio on save. For the 39 MB End-of-Run text,
deflate took 0.5 s, bzip2 2.6 s and LZMA 8 s; on a 20 MB plot sample,
deflate 0.5 s, bzip2 1.0 s and LZMA 11 s. A domain-specific codec could do
better on the plot by delta-encoding each run's samples, but it would
require a parser, a versioned binary schema and a new reader for every
future plot kind; the generic container already gets a 5–20x saving on the
files that dominate an archive.

**Chosen:** ZIP with bzip2 for entries of 4 KB or more and deflate for
small metadata. `compression="smallest"` selects LZMA for a deliberate
long save, `compression="fast"` selects deflate throughout. All three use
the standard library and are readable by 7-Zip and by RAPTOR Studio.
Each entry is copied through a fixed-size buffer in both directions, so
saving or reopening an archive with a 475 MB plot does not need 475 MB of
RAM. Archives use a fixed 1980-01-01 entry timestamp, so two saves of the
same content differ only in the manifest's `created` field.

## Results restore

Opening an `.rbdz` restores the model and then feeds the result entries to
the Results dock:

- `per_run.json` rebuilds the per-run figures the Overview cards and the
  status summary read, plus an `archive_detail` block (results schema
  version 2) holding the per-node capacity means, the cost rows and the
  weak-link state times, so the Capacity, Cost and Weak Link pages open as
  the run left them rather than saying the analysis is absent. An archive
  written by an older build simply has no detail and falls back to the
  flat figures;
- `final_results.txt` / `end_of_run.txt` rebuild the tables and raw views;
- `events.txt` is split at `Begin_Run_n` rows into one log per run;
- plot and cost entries are extracted to a temporary folder and read
  lazily, so a 475 MB plot is never loaded into RAM;
- `attribution.json` is applied directly, so opening an archive does not
  replay the event log to work out the Drivers again.

A saved result set is read-only in the sense that it carries the model and
run settings it was made with; editing the model does not rewrite the
archive's results. Save As a new `.rbdz` to attach a new run.

## CLI, API and MCP

`load_model_file` (the one loader under the GUI, CLI and MCP) accepts
`.rbdz` as well as `.rbds` and `.rbd`. The workspace copies an archive
byte-for-byte as `source.rbdz` for provenance and materialises its model as
the ordinary `model.rbds` used by the rest of the workspace. Archive
creation is available from the GUI's Save As; a command-line
`--save-archive` option can be added on top of
`raptor.core.fileio.rbdz.write_archive` without changing the format.

The CLI and API **read** an archive's model; they do not write one. An
in-place CLI edit of a `.rbdz` (`--in-place`) fails with the API's format
error instead of replacing the archive with a plain `.rbds` document, and
`save_model_as` only accepts `.rbds`/`.rbd` output. Results restore is a GUI
feature: a headless caller that wants the saved numbers reads the archive
with `read_archive` / `extract_results` directly.
