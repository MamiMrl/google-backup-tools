# Laptop Backup 2026

Evacuation of a Huawei Windows laptop (C: and D: drives) to Google Drive before wiping the machine, plus an ongoing audit tool to identify which local folders are not yet backed up. The Google Drive copy is the intermediate step; the final destination is a Mac-formatted 2 TB SSD (downloaded from Drive on a Mac later).

## Language

### Backup Pipeline (`scan_manifest.py`, `drive_sync.py`)

**Manifest**:
A CSV file (`manifest.csv`) listing every local file that is a candidate for upload. Columns: `FullPath`, `Name`, `Category`, `SizeBytes`. Produced by `scan_manifest.py`; consumed by `drive_sync.py compare`.
_Avoid_: file list, scan output, inventory

**Drive Listing**:
A CSV file (`drive_listing.csv`) snapshotting all files currently on Google Drive. Produced by `drive_sync.py list-drive`; consumed by `drive_sync.py compare`.
_Avoid_: Drive index, Drive snapshot

**Comparison**:
A CSV file (`comparison.csv`) produced by `drive_sync.py compare`, labelling each manifest entry as `already_on_drive`, `conflict`, or `new`. Only `new` files are uploaded.
_Avoid_: diff, delta

**Category**:
One of four top-level buckets used to organise files in Drive under `Laptop Backup 2026/`: `Personal Documents`, `University`, `Projects`, `Media`. Files that don't match any known path base are labelled `Other` and excluded from upload until reviewed.
_Avoid_: folder, type, tag

**Excluded Path**:
A local path prefix listed in `EXCLUDE_PREFIXES` (drive_sync.py) that is silently skipped during scanning and upload. Represents a decision that those files are not worth keeping (games, VMs, tool caches, vendor assets, files marked for deletion).
_Avoid_: ignored path, skip rule

**Path Base**:
A mapping from a local path prefix to a Drive destination subfolder, defined in `PATH_BASES` (drive_sync.py). Determines both the Category and the exact Drive folder for a file.
_Avoid_: route, mapping rule

### Folder Audit (`folder_audit.py`)

**Depth-2 Folder**:
A local directory exactly two levels below a scan root (e.g. `D:\GitHub\repo-name` is depth-2 under `D:\`). The unit of comparison in the folder audit — coarse enough for a high-level view, fine enough to act on.
_Avoid_: subfolder, nested folder, directory

**Uploadable Size**:
The total byte size of all files within a folder that match `KEEP_EXTENSIONS`. Used instead of full folder size so that local folders and Drive folders are compared on the same subset of files (Drive never received non-whitelisted files).
_Avoid_: folder size, total size, byte count

**Backup Status**:
The result assigned to each Depth-2 Folder after matching it against the Drive Hierarchy. One of three values: `backed_up` (a matching Drive folder covers the local Uploadable Size, i.e. drive >= local), `partial` (a matching Drive folder exists but is smaller — some uploadable content is not on Drive), `missing` (no matching Drive folder). A folder with no uploadable content is vacuously `backed_up`. Matching is rename-aware: see Folder Match.
_Avoid_: sync status, upload status, result

**Folder Match**:
How a local Depth-2 Folder is located on Drive. First by **translated path** — the local path run through `PATH_BASES` to mirror the renames the upload applied (e.g. `…\Masaüstü` → `University/Desktop`); failing that, by **leaf name** (the fullest same-named Drive folder). The backup is a reorganised, curated subset — not a mirror — so neither raw path nor exact size alone is reliable.
_Avoid_: folder lookup, name match

**Drive Hierarchy**:
A fetched representation of all folders and their parent relationships inside the Audit Scope on Google Drive, used to compute per-folder uploadable sizes on the Drive side. Richer than the flat Drive Listing — includes folder structure, not just files.
_Avoid_: folder tree, Drive structure, Drive index

**Audit Scope**:
The `Laptop Backup 2026/` folder on Google Drive. The only part of Drive searched during the folder audit. Folders outside this boundary are not considered when determining Backup Status.
_Avoid_: backup root, Drive root, search scope
