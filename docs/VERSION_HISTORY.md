# Version history and restore

Every successful save appends an immutable snapshot of the model file. The
history lives in the user's private application folder, not beside the
model, so a project folder stays clean and a read-only network share still
gets history.

Open **File ▸ Version History…**:

- **Open as copy** opens the chosen version in a new window as an unsaved
  model; save it where you want.
- **Restore to file** writes the chosen version over the model file, after
  confirmation. The older versions remain in history, so this is reversible
  from the same dialog.
- **Delete version** removes one snapshot; the model file is untouched.

The list shows time, reason (`save`, `opened`, `manual`), size and an
optional label. A preview shows the first 200 KB of the version.

The command line has the same store:

```sh
raptor history plant.rbds                 # list versions
raptor history plant.rbds --snapshot      # record the file as it is now
raptor history plant.rbds --restore ID -o plant-old.rbds
raptor history plant.rbds --restore ID --force   # overwrite plant.rbds
```

Snapshots are content-deduplicated: saving an unchanged file adds nothing.
The default retention is 30 versions or 50 MB per file, whichever is reached
first; the oldest versions beyond that are deleted.

History is not a replacement for the crash-recovery copy (unsaved work) or
for a project backup on a different disk. It records saved versions.
