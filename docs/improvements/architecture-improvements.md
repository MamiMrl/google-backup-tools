# Architecture Improvements — To File as GitHub Issues

Three issues identified in the 2026-06-29 architecture review.
File them in order (Issue 1 before Issue 2 — Issue 2 references Issue 1's number).

To file from a terminal authenticated with GitHub:

```bash
gh issue create --title "..." --body "$(cat <<'EOF'
<body>
EOF
)"
```

---

## Issue 1 — Extract `drive_config.py`: routing rules as a standalone module

**Label:** `enhancement`

### What to build

`drive_sync.py` is a runnable CLI and the import target for four other scripts.
`PATH_BASES`, `KEEP_EXTENSIONS`, `EXCLUDE_PREFIXES`, `KEEP_CATEGORIES`,
`BACKUP_ROOT`, `drive_destination()`, and the path-exclusion logic currently live
inside a CLI script's top-level scope. Four scripts import from it directly.

Extract a `drive_config.py` containing only the routing rules and two helper
functions. Unify the two divergent exclusion implementations (`is_excluded()` in
`scan_manifest.py` and `is_system_skipped()` in `folder_audit.py`) into a single
`is_excluded(path, context)` where `context` is `"upload"` or `"audit"` — making
the intentional difference explicit. Update imports in all four callers.

### Acceptance criteria

- [ ] `drive_config.py` exists and contains `PATH_BASES`, `KEEP_EXTENSIONS`, `EXCLUDE_PREFIXES`, `KEEP_CATEGORIES`, `BACKUP_ROOT`, `drive_destination()`, `is_excluded()`
- [ ] `drive_sync.py`, `scan_manifest.py`, `folder_audit.py`, `upload_selected.py` import config symbols from `drive_config` — not from each other
- [ ] `is_excluded(path, "upload")` applies both `EXCLUDE_PREFIXES` and `SYSTEM_SKIP`; `is_excluded(path, "audit")` applies `SYSTEM_SKIP` only
- [ ] Unit tests cover `drive_destination()` and `is_excluded()` for both contexts
- [ ] All existing tests pass

### Blocked by

None — can start immediately.

---

## Issue 2 — Extract `drive_client.py`: one seam for all Drive I/O

**Label:** `enhancement`

### What to build

Drive interaction is spread across five files: auth in `drive_backup.py`, folder
creation and resumable upload in `drive_sync.py`, a second upload implementation
and dedup listing in `upload_selected.py`, a third (BytesIO) upload variant in
`retry_upload.py`, and a paginated hierarchy fetch in `folder_audit.py`.

Consolidate into `drive_client.py` exposing: `auth()`, `upload_file(local_path,
folder_id, filename)`, `list_files(query, fields)`, `get_or_create_folder(name,
parent_id, cache)`, `resolve_drive_folder(path_parts, root_id, cache)`. No caller
should contain direct `service.files()` calls after this change.

### Acceptance criteria

- [ ] `drive_client.py` exists and provides the interface described above
- [ ] No `service.files()` / `MediaFileUpload` / `nextPageToken` loop outside `drive_client.py`
- [ ] `drive_sync.py`, `upload_selected.py`, `retry_upload.py`, `folder_audit.py` delegate all Drive API calls through `drive_client`
- [ ] `drive_backup.py` auth is re-exported from or replaced by `drive_client.auth()`
- [ ] All existing tests pass; a `FakeDriveClient` stub is provided for future tests

### Blocked by

Issue 1 (extract `drive_config.py`) — clean up the config seam first so
`drive_client` can import from `drive_config` without circular dependencies.

---

## Issue 3 — Type the CSV pipeline with `TypedDict`s

**Label:** `enhancement`

### What to build

The five CSV files (Manifest, Drive Listing, Comparison, Upload Log, Drive
Hierarchy) are named concepts in `CONTEXT.md` and form the system's inter-module
interface, but their schemas are implicit — column names scattered in
`DictWriter(fieldnames=[...])` calls across multiple files.

Add `pipeline_types.py` with `TypedDict`s: `ManifestRow`, `DriveListingRow`,
`ComparisonRow`, `UploadLogRow`, `DriveHierarchyRow`. Annotate
`DictReader`/`DictWriter` call sites accordingly.

### Acceptance criteria

- [ ] `pipeline_types.py` defines a `TypedDict` for each of the five CSV schemas
- [ ] Each `TypedDict`'s fields match the actual columns written by the producing script
- [ ] At least the producing scripts (`scan_manifest`, `drive_sync`, `folder_audit`) use the types at their write sites
- [ ] A developer can find the full Comparison schema by reading `pipeline_types.py` alone

### Blocked by

None — can start immediately. (Independent of Issues 1 and 2.)
