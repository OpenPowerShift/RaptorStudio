# Version history and restore

A version is a checkpoint you save on purpose, like a commit: it is an
immutable snapshot of the model file, and an ordinary Save (or Save As, in any
format) writes the file and records **nothing**. Only the label and the
description of a version can be edited. The history lives in the user's
private application folder, not beside the model, so a project folder stays
clean and a read-only network share still gets history.

Versions are created only by **Save version now…** in the dialog,
`raptor history --snapshot`, and the MCP `snapshot_version` tool, plus one
safety copy the program takes by itself: the file as it was just before
**Restore to file** overwrites it (reason `before restore`).

Open **File ▸ Version History…**:

- **Open as copy** opens the chosen version in a new window as an unsaved
  model; save it where you want.
- **Restore to file** writes the chosen version over the model file, after
  confirmation. The version being replaced is copied to History first (a
  `before restore` entry), and the
  window reloads the restored bytes immediately (so the next save cannot
  write the old in-memory model back). Restore is refused for a protected
  file or while a run/step session is active. If the file on disk no
  longer matches the newest recorded version (another program changed it),
  a separate warning says so before anything is overwritten.
- **Save version now…** turns the **Current** row into a version: unsaved
  edits are saved first, then the file is checkpointed and the new version
  selected. The optional label is one line; it is metadata, so it
  does not change the snapshot's content or checksum.
- **Label…** names the selected version, or clears its label. **Save
  version now…** on an unchanged file names the newest version when it has
  no label; if it already has a different label (or description), a new
  entry is added and the earlier one keeps its label. Both entries name the
  same stored file, so the bytes are kept once.
- **Description** (under the model statistics) is an optional, multi-line
  Markdown note about the selected version: why it was saved, what changed,
  what to check. It is shown rendered (headings, bold, lists, links) until
  you press **Edit**, which shows the raw Markdown; **Save** (Ctrl+Enter)
  stores it and renders it again, **Cancel** (Esc) discards the edit. If
  you pick another version or close the window with unsaved changes, you
  are asked whether to save them. After **Save version now…** the editor
  opens on the new version so you can describe it straight away. Like the
  label it is metadata: it does not change the snapshot's content or
  checksum, is limited to 20,000 characters, and a described version is
  kept like a labelled one. A `¶` in the list marks a described version
  (hover for its first line). The text is only displayed: nothing in it is
  executed, remote images are never loaded, and a clicked link opens in
  your browser only if it is a web or mail address.
- **Delete version** removes one snapshot; the model file is untouched.

The list starts with **Current**, which is not a version but the working
state: the open model as Save would write it (for an `.rbdz`, with the results
on screen). It reads *Current — not checkpointed* when it differs from the
newest version, *Current — matches the version saved <time> (<label>)* when
it does not, and ends with *· unsaved changes* while the window has unsaved
edits. Selecting it shows the model statistics and what changed since the
newest version; Open as copy, Restore, Label and Delete are off for it.
Layout counts as a difference; the zoom and pan of the canvas do not; for an
archive the results count (compared by the run's identity).

Below it come the versions, newest first, each with time, reason (`manual`,
`snapshot`, `before restore`, and for older histories `save`), size and an
optional label. The dialog also shows the history's total size on disk. With
no versions yet, Current says so; if the model file itself has been moved or
deleted, it says that instead. The preview shows the model's statistics, the
description, the saved results of an archive and how the version differs from
the open model.

For an `.rbdz` archive the saved results are part of the document: a new run
(or clearing the results) marks the open archive as having unsaved changes, so
Save and **Save version now…** write the archive with the current results
first.

For an `.rbdz` archive the saved results are part of the document: Save
records a version like any other format, and a new run (or clearing the
results) marks the open archive as having unsaved changes, so Save and
**Save version now…** write the archive with the current results first.

The command line has the same store:

```sh
raptor history plant.rbds                       # list versions
raptor history plant.rbds --snapshot            # record the file as it is now
raptor history plant.rbds --snapshot --label "before upgrade"
raptor history plant.rbds --snapshot --description-file notes.md
raptor history plant.rbds --describe ID --description "Checked **spares**"
raptor history plant.rbds --describe ID --description ""   # clear it
raptor history plant.rbds --restore ID -o plant-old.rbds
raptor history plant.rbds --restore ID --force  # overwrite plant.rbds
```

The listed versions are newest first; the current one carries a leading
`*`. `-f json` adds `current` and `current_id` alongside each snapshot's
id, time, reason, label, description (Markdown), size and checksum.
`--description-file -` reads the description from standard input.

The CLI refuses to restore over an existing output without `--force`, and
never writes a missing model's history. Exit codes: `0` success, `2` a bad
command line or unknown snapshot (including a missing model for `--snapshot`
or the plain listing), `1` a damaged snapshot (checksum mismatch) or an
unreadable store, `6` a destination that could not be written.

Identical bytes are stored once (the stored file is named by its SHA-256), however many entries name them, so a second checkpoint of an unchanged
file costs no disk; a stored file is removed only when no entry names it.
Retention: versions you save (`manual`, `snapshot`, labelled or not) are
**never deleted automatically**. Only the automatic `before restore` copies,
and entries that older releases wrote on every save (reason `save`, also
`restored` and `opened`) and that carry no label or description, are pruned: the newest
30 are kept, within 50 MB of history in all (counting each stored file once);
the newest automatic copy is always kept. Giving an old entry a label or
description makes it yours and keeps it. Instead of deleting, the dialog shows
each version's size and the total, and warns when a model's history passes
50 MB so you can remove versions with **Delete version**; `raptor history`
prints the same total and warning (`-f json` adds `total_bytes` and
`soft_limit_bytes`), and the MCP `list_versions` returns `total_bytes`,
`soft_limit_bytes` and `over_soft_limit`. The index
is updated before pruned files are removed: a crash in between leaves an
unreferenced snapshot (recoverable) rather than an index entry whose file is
gone. A damaged snapshot is refused on restore (checksum mismatch) and
leaves the target untouched; a damaged index is quarantined and rebuilt from
the snapshot files, including each file's checksum and size.

History is not a replacement for the crash-recovery copy (unsaved work) or
for a project backup on a different disk. It records only the checkpoints you save (above); edits made through the
CLI/MCP workspace have their own undo backups, and saving from the CLI or MCP
records nothing either. History is local to this account and machine; renaming or
moving a model starts a new history. The index and snapshots live under the
user's private state folder and are not shared with colleagues.
