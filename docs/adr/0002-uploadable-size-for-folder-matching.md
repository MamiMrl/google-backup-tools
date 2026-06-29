# Uploadable size (not full size) for folder matching

When comparing a local Depth-2 Folder to its Drive counterpart, we compare Uploadable Size — the sum of sizes of files matching `KEEP_EXTENSIONS` — not the full folder size. The original upload pipeline filtered to `KEEP_EXTENSIONS`, so Drive folders never received source code, configs, or tool output. Comparing full local size to Drive size would mark every folder as `partial` even if the backup is complete.

## Consequences

The Backup Status `backed_up` means "all uploadable files are present on Drive" — not "all files are present." Folders containing only non-whitelisted files (e.g. a pure source-code repo with no PDFs or images) will show Uploadable Size of 0 and are treated as vacuously `backed_up` (nothing to upload).

## Update: subset coverage, not exact equality

Initially `backed_up` required the Drive folder's size to *equal* the local Uploadable Size. Testing against the real Drive showed this almost never holds — the upload stored a curated subset (whitelist + EXCLUDE_PREFIXES + only `new` files), so the Drive folder is normally *smaller* than local. `backed_up` was redefined as **coverage**: `drive_bytes >= local_bytes` (within an optional tolerance). A Drive folder larger than local (common after a rename merges trees, e.g. `Desktop`) still counts as covered. See ADR-0004 for the companion rename-awareness change.
