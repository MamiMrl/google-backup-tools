#!/usr/bin/env python3
"""folder_audit.py — Audit which local Depth-2 Folders are backed up to Google Drive.

Read-only. Answers the question "which of my folders are NOT yet on Drive?" at a
folder-level granularity, so you can decide what else to upload.

Concepts (see CONTEXT.md):
  Depth-2 Folder   — the unit of comparison: a directory <=2 levels below a scan root.
  Uploadable Size  — total bytes of files inside a folder matching KEEP_EXTENSIONS.
  Backup Status    — backed_up | partial | missing.
  Drive Hierarchy  — folders + sizes inside the Audit Scope on Drive.
  Audit Scope      — the 'Laptop Backup 2026/' folder on Drive (only place searched).

Subcommands:
  fetch-drive   Fetch the Drive Hierarchy under 'Laptop Backup 2026/' → drive_hierarchy.csv
                  (needs auth; ~83k files, one-time, slow)
  scan-local    Scan local Depth-2 Folders and their Uploadable Size → local_folders.csv
                  (no auth, fast)
  audit         Compare local folders vs the Drive Hierarchy → folder_audit.csv + summary
                  (no auth; runs scan-local internally if needed)

Typical workflow:
  1. python folder_audit.py fetch-drive
  2. python folder_audit.py audit
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from drive_backup import auth, CREDENTIALS_FILE
from drive_sync import KEEP_EXTENSIONS, BACKUP_ROOT, FOLDER_MIME, PATH_BASES
from scan_manifest import SCAN_ROOTS, SYSTEM_SKIP, HOME_DIR, ALLOWED_HOME_DOTDIRS

# ── constants ─────────────────────────────────────────────────────────────────

DEFAULT_DEPTH = 2
DRIVE_HIERARCHY_FILE = Path(__file__).parent / "drive_hierarchy.csv"
LOCAL_FOLDERS_FILE = Path(__file__).parent / "local_folders.csv"
AUDIT_FILE = Path(__file__).parent / "folder_audit.csv"


# ── shared helpers ────────────────────────────────────────────────────────────

def is_system_skipped(path_str: str) -> bool:
    """True for hard system / computer-generated locations we never scan.

    Unlike scan_manifest.is_excluded, this deliberately does NOT honour
    EXCLUDE_PREFIXES — the audit wants to SEE games, old backups, etc. so the
    user can decide. Only AppData, recycle bin, and friends are pruned.
    """
    lp = path_str.lower()
    return any(lp.startswith(prefix) for prefix in SYSTEM_SKIP)


def is_uploadable(filename: str) -> bool:
    return Path(filename).suffix.lower() in KEEP_EXTENSIONS


def fmt_gb(n_bytes: int) -> str:
    return f"{n_bytes / (1024 ** 3):.2f} GB"


def fmt_mb(n_bytes: int) -> str:
    return f"{n_bytes / (1024 ** 2):.1f} MB"


def root_label(root: str) -> str:
    """Display name for files sitting loose at a scan root (depth 0)."""
    p = Path(root)
    return p.name or p.drive or root


def bucket_parts(rel_parts: list[str], depth: int) -> tuple[str, ...]:
    """The first `depth` path components — the Depth-N Folder a file rolls up into.

    A file deeper than `depth` is attributed to its depth-N ancestor; a file
    shallower lands in a shorter (loose) bucket.
    """
    return tuple(rel_parts[:depth])


def expected_drive_relpath(local_path: str) -> str | None:
    """Translate a local folder to the Drive path (relative to the Audit Scope)
    where the backup *would* have placed it, applying the same PATH_BASES renames
    the upload used (e.g. '…\\Masaüstü' → 'University/Desktop').

    Returns None for folders outside every Path Base — those were never part of
    the backup scheme and fall back to name matching.
    """
    local_path = os.path.normpath(local_path)  # collapse any doubled separators
    lp = local_path.lower()
    for prefix, dest in PATH_BASES:
        if lp.startswith(prefix):
            rel = local_path[len(prefix):].lstrip("\\")
            path = f"{dest}/{rel}" if rel else dest
            return path.replace("\\", "/").rstrip("/")
    return None


def classify_status(local_bytes: int, drive_bytes: int | None, tolerance: int) -> str:
    """Subset-aware Backup Status.

    The upload stored a curated subset, so Drive size is normally <= local. A
    folder is backed_up when its Drive counterpart *covers* the local Uploadable
    Size (drive >= local within tolerance); short of that it's partial; absent
    Drive folder is missing. A folder with no uploadable content is vacuously
    backed_up (nothing to upload).
    """
    if local_bytes == 0:
        return "backed_up"
    if not drive_bytes:  # None or 0 → no covering Drive folder
        return "missing"
    if drive_bytes + tolerance >= local_bytes:
        return "backed_up"
    return "partial"


# ── fetch-drive ───────────────────────────────────────────────────────────────

def _fetch_all(service, query: str, fields: str, label: str) -> list[dict]:
    items, page_token = [], None
    while True:
        resp = service.files().list(
            q=query, fields=f"nextPageToken, files({fields})",
            pageSize=1000, pageToken=page_token,
        ).execute()
        items.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        print(f"  {label}: {len(items):,} so far...", end="\r")
        if not page_token:
            break
    print(f"  {label}: {len(items):,} total            ")
    return items


def cmd_fetch_drive(service, out_path: Path) -> None:
    """Fetch every folder + file inside the Audit Scope and compute each folder's
    recursive Uploadable Size. Writes the Drive Hierarchy to CSV."""
    print(f"Fetching Drive Hierarchy under '{BACKUP_ROOT}/' ...")

    folders = _fetch_all(
        service,
        f"mimeType='{FOLDER_MIME}' and trashed=false and 'me' in owners",
        "id, name, parents", "folders",
    )
    files = _fetch_all(
        service,
        f"mimeType!='{FOLDER_MIME}' and trashed=false and 'me' in owners",
        "id, name, size, parents", "files",
    )

    # Index folders and build parent → child-folder edges.
    folder_by_id = {f["id"]: f for f in folders}
    children: dict[str, list[str]] = defaultdict(list)
    for f in folders:
        for parent in f.get("parents", []):
            children[parent].append(f["id"])

    # Locate the Audit Scope root(s): folder(s) named exactly BACKUP_ROOT.
    scope_roots = [f["id"] for f in folders if f["name"] == BACKUP_ROOT]
    if not scope_roots:
        sys.exit(f"ERROR: no folder named '{BACKUP_ROOT}' found on Drive — nothing to audit against.")

    # Collect every folder id within scope (descendants of the scope root(s)).
    in_scope: set[str] = set()
    stack = list(scope_roots)
    while stack:
        fid = stack.pop()
        if fid in in_scope:
            continue
        in_scope.add(fid)
        stack.extend(children.get(fid, []))

    # Direct uploadable bytes per folder (sum of files whose parent is that folder).
    # Drive only ever received whitelisted files, so no extension filter is needed
    # here — everything present is backed-up content.
    direct_bytes: dict[str, int] = defaultdict(int)
    for f in files:
        size = int(f["size"]) if f.get("size") else 0
        for parent in f.get("parents", []):
            if parent in in_scope:
                direct_bytes[parent] += size

    # Recursive uploadable bytes per folder via memoised DFS.
    rec_cache: dict[str, int] = {}

    def recursive_bytes(fid: str) -> int:
        if fid in rec_cache:
            return rec_cache[fid]
        total = direct_bytes.get(fid, 0)
        for child in children.get(fid, []):
            if child in in_scope:
                total += recursive_bytes(child)
        rec_cache[fid] = total
        return total

    # Build each folder's path within the scope (for the report).
    parent_of = {f["id"]: (f.get("parents", [None])[0]) for f in folders}

    def drive_path(fid: str) -> str:
        parts, cur, guard = [], fid, 0
        while cur and cur in folder_by_id and guard < 100:
            parts.append(folder_by_id[cur]["name"])
            if cur in scope_roots:
                break
            cur = parent_of.get(cur)
            guard += 1
        return "/".join(reversed(parts))

    rows = []
    for fid in in_scope:
        if fid in scope_roots:
            continue  # the scope root itself is not an auditable folder
        rows.append({
            "FolderID": fid,
            "Name": folder_by_id[fid]["name"],
            "RecursiveUploadableBytes": recursive_bytes(fid),
            "DrivePath": drive_path(fid),
        })

    rows.sort(key=lambda r: -r["RecursiveUploadableBytes"])
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["FolderID", "Name", "RecursiveUploadableBytes", "DrivePath"])
        writer.writeheader()
        writer.writerows(rows)

    total = recursive_bytes(scope_roots[0]) if len(scope_roots) == 1 else sum(direct_bytes.values())
    print(f"\nDrive Hierarchy: {len(rows):,} folders in scope, {fmt_gb(total)} of backed-up content.")
    print(f"Saved to: {out_path}")


# ── scan-local ────────────────────────────────────────────────────────────────

def scan_local(depth: int) -> list[dict]:
    """Walk the scan roots and roll every uploadable file up into its Depth-N
    Folder. Returns one row per folder with its Uploadable Size + file count."""
    buckets: dict[str, dict] = {}

    def get_bucket(root: str, parts: tuple[str, ...]) -> dict:
        full = os.path.join(root, *parts) if parts else root
        b = buckets.get(full)
        if b is None:
            d = len(parts)
            b = {
                "LocalPath": full,
                "FolderName": parts[-1] if parts else root_label(root),
                "Depth": d,
                "IsLoose": d < depth,
                "UploadableBytes": 0,
                "UploadableFiles": 0,
            }
            buckets[full] = b
        return b

    for root in SCAN_ROOTS:
        root = os.path.normpath(root)  # e.g. "D:\\" → "D:\" so joined paths stay single-sep
        root_path = Path(root)
        if not root_path.exists():
            print(f"WARNING: {root} not found, skipping.")
            continue
        print(f"Scanning {root} ...")
        seen = 0

        for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda e: None):
            if is_system_skipped(dirpath):
                dirnames.clear()
                continue

            # Prune home dotdirs (IDE/tool caches) except the allowed ones.
            if dirpath.lower() == HOME_DIR:
                dirnames[:] = [d for d in dirnames
                               if not d.startswith(".") or d.lower() in ALLOWED_HOME_DOTDIRS]
            # Prune system subdirs before descending.
            dirnames[:] = [d for d in dirnames
                           if not is_system_skipped(os.path.join(dirpath, d))]

            rel = os.path.relpath(dirpath, root)
            rel_parts = [] if rel == "." else rel.split(os.sep)

            # Register the Depth-N folder itself so empty / no-uploadable folders
            # still appear in the audit.
            if len(rel_parts) >= depth:
                get_bucket(root, bucket_parts(rel_parts, depth))

            for filename in filenames:
                if not is_uploadable(filename):
                    continue
                try:
                    size = os.path.getsize(os.path.join(dirpath, filename))
                except (OSError, PermissionError):
                    continue
                b = get_bucket(root, bucket_parts(rel_parts, depth))
                b["UploadableBytes"] += size
                b["UploadableFiles"] += 1
                seen += 1
                if seen % 2000 == 0:
                    print(f"  {seen:,} uploadable files...", end="\r")

        print(f"  Done — {seen:,} uploadable files under {root}        ")

    return sorted(buckets.values(), key=lambda b: -b["UploadableBytes"])


def write_local_folders(rows: list[dict], out_path: Path) -> None:
    fields = ["LocalPath", "FolderName", "Depth", "IsLoose", "UploadableBytes", "UploadableFiles"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Local folders saved to: {out_path}  ({len(rows):,} folders)")


# ── audit ─────────────────────────────────────────────────────────────────────

def load_drive_hierarchy(path: Path) -> tuple[dict[str, tuple[int, str]], dict[str, list[tuple[int, str]]]]:
    """Returns two indexes over the Drive Hierarchy:
      by_path: relpath(lower) → (recursive_uploadable_bytes, full_drive_path)
      by_name: name(lower)    → list of (recursive_uploadable_bytes, full_drive_path)
    relpath is the DrivePath with the leading 'Laptop Backup 2026/' stripped.
    """
    prefix = f"{BACKUP_ROOT}/"
    by_path: dict[str, tuple[int, str]] = {}
    by_name: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            size = int(row["RecursiveUploadableBytes"])
            full = row["DrivePath"]
            rel = full[len(prefix):] if full.startswith(prefix) else full
            key = rel.lower()
            # On duplicate paths keep the fullest copy.
            if key not in by_path or size > by_path[key][0]:
                by_path[key] = (size, full)
            by_name[row["Name"].lower()].append((size, full))
    return by_path, by_name


def match_folder(folder: dict, by_path: dict, by_name: dict) -> tuple[int | None, str]:
    """Find the covering Drive folder for a local folder. Prefers the rename-aware
    Path Base translation; falls back to the fullest same-name folder."""
    rel = expected_drive_relpath(folder["LocalPath"])
    if rel is not None:
        hit = by_path.get(rel.lower())
        if hit:
            return hit[0], hit[1]
    # Fallback: fullest Drive folder sharing this leaf name.
    name_hits = by_name.get(folder["FolderName"].lower(), [])
    if name_hits:
        best = max(name_hits, key=lambda m: m[0])
        return best[0], best[1]
    return None, ""


def cmd_audit(depth: int, tolerance: int) -> None:
    if not DRIVE_HIERARCHY_FILE.exists():
        sys.exit(f"{DRIVE_HIERARCHY_FILE.name} not found — run 'fetch-drive' first.")

    by_path, by_name = load_drive_hierarchy(DRIVE_HIERARCHY_FILE)
    local = scan_local(depth)
    write_local_folders(local, LOCAL_FOLDERS_FILE)

    results = []
    counts = {"backed_up": 0, "partial": 0, "missing": 0}
    for b in local:
        drive_bytes, drive_path = match_folder(b, by_path, by_name)
        status = classify_status(b["UploadableBytes"], drive_bytes, tolerance)
        counts[status] += 1
        drive_bytes = drive_bytes or 0
        results.append({
            "LocalPath": b["LocalPath"],
            "FolderName": b["FolderName"],
            "Depth": b["Depth"],
            "IsLoose": b["IsLoose"],
            "Status": status,
            "UploadableBytes": b["UploadableBytes"],
            "UploadableFiles": b["UploadableFiles"],
            "DriveBytes": drive_bytes,
            "ByteDelta": b["UploadableBytes"] - drive_bytes,
            "DriveMatchPath": drive_path,
        })

    # Order: missing first, then partial, then backed_up; biggest first within each.
    order = {"missing": 0, "partial": 1, "backed_up": 2}
    results.sort(key=lambda r: (order[r["Status"]], -r["UploadableBytes"]))

    fields = ["LocalPath", "FolderName", "Depth", "IsLoose", "Status",
              "UploadableBytes", "UploadableFiles", "DriveBytes", "ByteDelta", "DriveMatchPath"]
    with open(AUDIT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    print_audit_summary(results, counts)
    print(f"\nFull report saved to: {AUDIT_FILE}")


def print_audit_summary(results: list[dict], counts: dict[str, int]) -> None:
    missing = [r for r in results if r["Status"] == "missing" and r["UploadableBytes"] > 0]
    partial = [r for r in results if r["Status"] == "partial"]
    missing_bytes = sum(r["UploadableBytes"] for r in missing)
    partial_gap = sum(max(0, r["ByteDelta"]) for r in partial)

    print("\n" + "=" * 72)
    print("FOLDER AUDIT SUMMARY")
    print("=" * 72)
    print(f"  backed_up : {counts['backed_up']:>5,} folders")
    print(f"  partial   : {counts['partial']:>5,} folders   ({fmt_gb(partial_gap)} possibly not on Drive)")
    print(f"  missing   : {counts['missing']:>5,} folders   ({fmt_gb(missing_bytes)} with uploadable content)")

    if missing:
        print(f"\n  Top MISSING folders (not on Drive at all):")
        for r in missing[:25]:
            tag = " [loose]" if r["IsLoose"] else ""
            print(f"    {fmt_mb(r['UploadableBytes']):>10}  {r['UploadableFiles']:>5} files  {r['LocalPath']}{tag}")
        if len(missing) > 25:
            print(f"    ... and {len(missing) - 25} more (see {AUDIT_FILE.name})")

    if partial:
        print(f"\n  Top PARTIAL folders (on Drive but size differs — review):")
        for r in sorted(partial, key=lambda x: -x["ByteDelta"])[:15]:
            print(f"    local {fmt_mb(r['UploadableBytes']):>10} vs drive {fmt_mb(r['DriveBytes']):>10}  {r['LocalPath']}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch-drive", help="Fetch Drive Hierarchy → drive_hierarchy.csv")
    p_fetch.add_argument("--out", default=str(DRIVE_HIERARCHY_FILE), metavar="FILE")

    p_scan = sub.add_parser("scan-local", help="Scan local Depth-N Folders → local_folders.csv")
    p_scan.add_argument("--depth", type=int, default=DEFAULT_DEPTH, metavar="N")

    p_audit = sub.add_parser("audit", help="Compare local vs Drive → folder_audit.csv + summary")
    p_audit.add_argument("--depth", type=int, default=DEFAULT_DEPTH, metavar="N")
    p_audit.add_argument("--tolerance-bytes", type=int, default=0, metavar="N",
                         help="Treat sizes within N bytes as a match (default 0 = exact)")

    args = parser.parse_args()

    if args.command == "scan-local":
        rows = scan_local(args.depth)
        write_local_folders(rows, LOCAL_FOLDERS_FILE)
        return

    if args.command == "audit":
        cmd_audit(args.depth, args.tolerance_bytes)
        return

    # fetch-drive needs Drive auth.
    if not CREDENTIALS_FILE.exists():
        sys.exit(f"ERROR: credentials.json not found at {CREDENTIALS_FILE}")
    print("Authenticating with Google Drive...")
    from googleapiclient.discovery import build
    service = build("drive", "v3", credentials=auth())
    cmd_fetch_drive(service, Path(args.out))


if __name__ == "__main__":
    main()
