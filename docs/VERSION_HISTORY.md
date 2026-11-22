# Version history and restore

Every successful save appends an immutable snapshot of the model file. The
history lives in the user's private application folder, not beside the
model, so a project folder stays clean and a read-only network share still
gets history.

Open **File ▸ Version History…**:

- **Open as copy** opens the chosen version in a new window as an unsaved
  model; save it where you want.
- **Restore to file** writes the chosen version over the model file, after
  confirmation. The version being replaced is recorded first, and the
  window reloads the restored bytes immediately (so the next save cannot
  write the old in-memory model back). Restore is refused for a protected
  file or while a run/step session is active. If the file on disk no
  longer matches the newest recorded version (another program changed it),
  a separate warning says so before anything is overwritten.
- **Save version now…** records the file immediately instead of waiting
  for the next save. The optional label is one line; it is metadata, so it
  does not change the snapshot's content or checksum.
- **Label…** names the selected version, or clears its label. Labelling
  the newest version when the file is unchanged updates that version
  rather than adding a duplicate.
- **Delete version** removes one snapshot; the model file is untouched.

The list shows time, reason (`save`, `opened`, `manual`, `restored`), size
and an optional label. The version that matches the file on disk right now
is marked **(current)** and shown in bold. With no versions yet, the dialog
says so and points at **Save version now…**; if the model file itself has
been moved or deleted, it says that instead. A preview shows the first
200 KB of the version.

The command line has the same store:

```sh
raptor history plant.rbds                       # list versions
raptor history plant.rbds --snapshot            # record the file as it is now
raptor history plant.rbds --snapshot --label "before upgrade"
raptor history plant.rbds --restore ID -o plant-old.rbds
raptor history plant.rbds --restore ID --force  # overwrite plant.rbds
```

The listed versions are newest first; the current one carries a leading
`*`. `-f json` adds `current` and `current_id` alongside each snapshot's
id, time, reason, label, size and checksum.

The CLI refuses to restore over an existing output without `--force`, and
never writes a missing model's history. Exit codes: `0` success, `2` a bad
command line or unknown snapshot (including a missing model for `--snapshot`
or the plain listing), `1` a damaged snapshot (checksum mismatch) or an
unreadable store, `6` a destination that could not be written.

Snapshots are content-deduplicated: saving an unchanged file adds nothing.
The default retention is 30 versions or 50 MB per file, whichever is reached
first; the oldest versions beyond that are deleted. The size cap is checked
between versions, so one model larger than the cap is still kept. The index
is updated before pruned files are removed: a crash in between leaves an
unreferenced snapshot (recoverable) rather than an index entry whose file is
gone. A damaged snapshot is refused on restore (checksum mismatch) and
leaves the target untouched; a damaged index is quarantined and rebuilt from
the snapshot files, including each file's checksum and size.

History is not a replacement for the crash-recovery copy (unsaved work) or
for a project backup on a different disk. It records versions saved by the
**desktop application**; edits made through the CLI/MCP workspace have their
own undo backups. History is local to this account and machine; renaming or
moving a model starts a new history. The index and snapshots live under the
user's private state folder and are not shared with colleagues.
