#!/usr/bin/env python3
"""
drive_sync.py — Compare local files with Google Drive and upload new ones.

Commands:
  list-drive   Fetch all Drive files → drive_listing.csv
  compare      Compare manifest.csv vs drive_listing.csv → comparison.csv
                 status: already_on_drive | conflict | new
  upload       Upload files with status=new from comparison.csv to Drive
                 under 'Laptop Backup 2026/{category}/...'

Typical workflow:
  1. python drive_sync.py list-drive --out drive_listing.csv
  2. python drive_sync.py compare --manifest manifest.csv --listing drive_listing.csv --out comparison.csv
  3. python drive_sync.py upload --comparison comparison.csv --dry-run   # preview
  4. python drive_sync.py upload --comparison comparison.csv             # for real
"""

import argparse
import csv
import sys
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from drive_backup import auth, CREDENTIALS_FILE

# ── constants ─────────────────────────────────────────────────────────────────

BACKUP_ROOT = "Laptop Backup 2026"
FOLDER_MIME = "application/vnd.google-apps.folder"
KEEP_CATEGORIES = {"Personal Documents", "University", "Projects", "Media"}

# Only upload files with these extensions — everything else is source code,
# simulation output, or tool config that isn't worth preserving.
KEEP_EXTENSIONS = {
    # Photos & images
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw", ".dng", ".webp",
    ".tga", ".psd", ".ai", ".svg",
    # Videos
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".3gp", ".flv",
    # Audio
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac",
    # Documents
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".odt", ".ods", ".odp", ".tex", ".bib", ".rtf",
    # Notebooks & data exports
    ".ipynb", ".csv",
    # Design & 3D
    ".indd", ".fig", ".sketch", ".xd", ".fbx",
    # Archives (thesis zips, project backups)
    ".zip", ".rar", ".7z", ".tar", ".gz",
}

# Local path prefixes to exclude from backup (lowercase Windows paths).
# These are paths we decided to skip during the Phase 1 scan.
EXCLUDE_PREFIXES = [
    r"d:\onedrive - kadir has university\belgeler\virtual machines",      # CentOS VMs
    r"d:\onedrive - kadir has university\masaüstü\checkdrivedelete\github these\slenderman",
    r"d:\yedeklemeler",          # already backed up to SSD
    r"d:\silinecekler",          # marked for deletion
    r"d:\examples",              # vendor circuit simulation examples
    r"d:\unity files\a2_maral_muhammed.rar",
    r"d:\unity files\a3_maral_muhammed.rar",
    r"d:\unity files\a4_maral_muhammed.rar",  # only keep the -Fixed version
    r"d:\unity files\game_programming",       # extracted folder, RAR is kept instead
    r"d:\ui design 3-d illustrations",        # downloaded 3D asset packs
    r"d:\udemy ui design figma&webflow\3-d illustrations",
    r"d:\udemy ui design figma&webflow\unsplash images",
    r"d:\udemy ui design figma&webflow\fonts",
    r"d:\web development - jhonas\html-css-course-master",
    r"d:\oculus",
    r"d:\riot games",
    r"d:\hearthstone",
    r"d:\heroes of the storm",
    r"d:\wpSystem",
    r"d:\notenmodule lukas",
    r"d:\mongodb",
    r"d:\arduino-nightly-windows",
    r"d:\netbeans-12.2",
    r"d:\xampp",
    r"d:\dbeaver",
    r"d:\sidefx",
    r"d:\adobe reader dc",
    r"d:\live wallpapers",
    r"c:\users\huawei\.android",
    r"c:\users\huawei\.gradle",
    r"c:\users\huawei\.vscode",
    r"c:\users\huawei\.rustup",
    r"c:\users\huawei\.cursor",
    r"c:\users\huawei\.p2",
    r"c:\users\huawei\.cargo",
    r"c:\users\huawei\.wdm",
    r"c:\users\huawei\.m2",
    r"c:\users\huawei\pycharmprojects\brainlab\venv",  # no source files, only venv
]

# Maps local path prefixes → Drive subfolder under BACKUP_ROOT.
# Order matters: more specific prefixes must come before shorter ones.
PATH_BASES = [
    (r"d:\onedrive - kadir has university\masaüstü", "University/Desktop"),
    (r"d:\onedrive - kadir has university\belgeler\resimler", "Media/University Pictures"),
    (r"d:\onedrive - kadir has university", "University/OneDrive"),
    (r"d:\github", "Projects/GitHub"),
    (r"d:\downloads", "Personal Documents/D-Downloads"),
    (r"d:\unity files", "Projects/Unity Files"),
    (r"d:\project management city", "Projects/Project Management CITY"),
    (r"d:\harvardx leadership principles", "Personal Documents/HarvardX"),
    (r"d:\udemy ui design figma&webflow", "Projects/Udemy UI Design"),
    (r"d:\web development - jhonas", "Projects/Web Development"),
    (r"d:\gamev4", "Projects/GameV4"),
    (r"d:\models", "Projects/Models"),
    (r"d:\vhdl", "Projects/VHDL"),
    (r"c:\users\huawei\desktop", "Personal Documents/Desktop"),
    (r"c:\users\huawei\downloads", "Personal Documents/C-Downloads"),
    (r"c:\users\huawei\eclipse-workspace", "Projects/Eclipse"),
    (r"c:\users\huawei\.ssh", "Personal Documents/SSH Keys"),
    (r"c:\users\huawei\pycharmprojects", "Projects/PyCharm"),
    (r"c:\users\huawei", "Personal Documents/Home"),  # catches root-level home files
]


# ── helpers ───────────────────────────────────────────────────────────────────

def is_excluded(local_path: str) -> bool:
    lp = local_path.lower()
    return any(lp.startswith(prefix) for prefix in EXCLUDE_PREFIXES)


def drive_destination(local_path: str, category: str) -> str:
    """Map a local Windows path to its Drive destination path under BACKUP_ROOT."""
    lp = local_path.lower()
    for prefix, dest in PATH_BASES:
        if lp.startswith(prefix):
            rel = local_path[len(prefix):].lstrip("\\")
            return f"{BACKUP_ROOT}/{dest}/{rel}".replace("\\", "/")
    # Fallback: put under category root
    return f"{BACKUP_ROOT}/{category}/{Path(local_path).name}"


# ── list-drive ────────────────────────────────────────────────────────────────

def cmd_list_drive(service, out_path: Path) -> None:
    """Fetch all non-trashed files from Drive and write to CSV."""
    print("Fetching all files from Google Drive...")
    files = []
    page_token = None
    while True:
        resp = service.files().list(
            q="trashed = false and 'me' in owners and mimeType != 'application/vnd.google-apps.folder'",
            fields="nextPageToken, files(id, name, size, mimeType, md5Checksum, createdTime, modifiedTime)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        batch = resp.get("files", [])
        files.extend(batch)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        print(f"  Fetched {len(files)} files...", end="\r")

    total_gb = sum(int(f.get("size", 0)) for f in files if f.get("size")) / (1024 ** 3)
    print(f"\nFound {len(files)} files on Drive ({total_gb:.2f} GB total).")

    fieldnames = ["DriveID", "Name", "SizeBytes", "MD5", "MimeType", "CreatedTime", "ModifiedTime"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for file in files:
            writer.writerow({
                "DriveID": file["id"],
                "Name": file["name"],
                "SizeBytes": file.get("size", ""),
                "MD5": file.get("md5Checksum", ""),
                "MimeType": file.get("mimeType", ""),
                "CreatedTime": file.get("createdTime", ""),
                "ModifiedTime": file.get("modifiedTime", ""),
            })

    print(f"Drive listing saved to: {out_path}")


# ── compare ───────────────────────────────────────────────────────────────────

def cmd_compare(manifest_path: Path, listing_path: Path, out_path: Path) -> None:
    """Compare local manifest against Drive listing. No Drive connection needed."""
    # Index Drive files by lowercase name → list of rows
    drive_by_name: dict[str, list[dict]] = {}
    with open(listing_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = row["Name"].lower()
            drive_by_name.setdefault(key, []).append(row)

    results = []
    counts: dict[str, int] = {"already_on_drive": 0, "conflict": 0, "new": 0, "excluded": 0}

    with open(manifest_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["Category"] not in KEEP_CATEGORIES:
                continue
            if is_excluded(row["FullPath"]):
                counts["excluded"] += 1
                continue

            local_size = int(row["SizeBytes"]) if row["SizeBytes"] else 0
            drive_matches = drive_by_name.get(row["Name"].lower(), [])

            status = "new"
            drive_id = ""

            if drive_matches:
                size_match = [
                    m for m in drive_matches
                    if m["SizeBytes"] and int(m["SizeBytes"]) == local_size
                ]
                if size_match:
                    status = "already_on_drive"
                    drive_id = size_match[0]["DriveID"]
                else:
                    status = "conflict"
                    drive_id = drive_matches[0]["DriveID"]

            counts[status] += 1
            results.append({
                "LocalPath": row["FullPath"],
                "Name": row["Name"],
                "Category": row["Category"],
                "SizeBytes": row["SizeBytes"],
                "DriveStatus": status,
                "DriveID": drive_id,
                "DriveDest": drive_destination(row["FullPath"], row["Category"]),
            })

    fieldnames = ["LocalPath", "Name", "Category", "SizeBytes", "DriveStatus", "DriveID", "DriveDest"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    new_gb = sum(
        int(r["SizeBytes"]) for r in results
        if r["DriveStatus"] == "new" and r["SizeBytes"]
    ) / (1024 ** 3)

    print(f"\nComparison complete:")
    print(f"  already_on_drive : {counts['already_on_drive']:,}")
    print(f"  conflict         : {counts['conflict']:,}  (same name, different size - review these)")
    print(f"  new              : {counts['new']:,}  ({new_gb:.2f} GB to upload)")
    print(f"  excluded         : {counts['excluded']:,}")
    print(f"\nResults saved to: {out_path}")


# ── upload ────────────────────────────────────────────────────────────────────

def get_or_create_folder(service, name: str, parent_id: str, cache: dict) -> str:
    """Return Drive folder ID, creating it if it doesn't exist. Uses cache to avoid redundant API calls."""
    cache_key = f"{parent_id}/{name}"
    if cache_key in cache:
        return cache[cache_key]

    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    resp = service.files().list(
        q=f"name='{escaped}' and mimeType='{FOLDER_MIME}' and '{parent_id}' in parents and trashed=false",
        fields="files(id)",
        pageSize=1,
    ).execute()
    existing = resp.get("files", [])
    if existing:
        folder_id = existing[0]["id"]
    else:
        meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        folder = service.files().create(body=meta, fields="id").execute()
        folder_id = folder["id"]

    cache[cache_key] = folder_id
    return folder_id


def resolve_drive_folder(service, path_parts: list[str], root_id: str, cache: dict) -> str:
    """Walk or create the folder hierarchy, return the final folder's Drive ID."""
    current_id = root_id
    for part in path_parts:
        if part:
            current_id = get_or_create_folder(service, part, current_id, cache)
    return current_id


def upload_file_to_drive(service, local_path: Path, folder_id: str, filename: str) -> bool:
    size = local_path.stat().st_size
    media = MediaFileUpload(str(local_path), resumable=True, chunksize=8 * 1024 * 1024)
    meta = {"name": filename, "parents": [folder_id]}
    try:
        req = service.files().create(body=meta, media_body=media, fields="id")
        response = None
        with tqdm(total=size, unit="B", unit_scale=True, desc=filename[:50], leave=False) as bar:
            prev = 0
            while response is None:
                status, response = req.next_chunk()
                if status:
                    current = int(status.resumable_progress)
                    bar.update(current - prev)
                    prev = current
            bar.update(size - bar.n)
        return True
    except HttpError as exc:
        print(f"  ERROR: {exc}")
        return False
    except KeyboardInterrupt:
        print("\n  Upload interrupted.")
        raise


def cmd_upload(service, comparison_path: Path, dry_run: bool, category_filter: str | None, upload_conflicts: bool = False, log_path: Path | None = None) -> None:
    rows = []
    skipped_ext = 0
    with open(comparison_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["DriveStatus"] == "new":
                pass
            elif row["DriveStatus"] == "conflict" and upload_conflicts:
                row = dict(row)
                row["DriveDest"] = row["DriveDest"].replace(
                    f"{BACKUP_ROOT}/", f"{BACKUP_ROOT}/Conflicts/", 1
                )
            else:
                continue
            if category_filter and row["Category"] != category_filter:
                continue
            ext = Path(row["LocalPath"]).suffix.lower()
            if ext not in KEEP_EXTENSIONS:
                skipped_ext += 1
                continue
            rows.append(row)

    if skipped_ext:
        print(f"Skipped {skipped_ext:,} files with non-whitelisted extensions (source code, configs, simulation output).")

    total_gb = sum(int(r["SizeBytes"]) for r in rows if r["SizeBytes"]) / (1024 ** 3)
    print(f"Files to upload: {len(rows):,} ({total_gb:.2f} GB)")

    if dry_run:
        print("\n[DRY RUN] Would upload:")
        for r in rows[:50]:
            print(f"  {r['LocalPath']}")
            print(f"    → {r['DriveDest']}")
        if len(rows) > 50:
            print(f"  ... and {len(rows) - 50} more")
        return

    root_resp = service.files().get(fileId="root", fields="id").execute()
    root_id = root_resp["id"]
    folder_cache: dict[str, str] = {}

    uploaded = skipped = failed = 0
    log_rows = []

    for i, row in enumerate(rows, 1):
        local_path = Path(row["LocalPath"])
        if not local_path.exists():
            print(f"[{i}/{len(rows)}] MISSING (skipping): {local_path}")
            skipped += 1
            continue

        dest_parts = [p for p in row["DriveDest"].split("/") if p]
        folder_parts = dest_parts[:-1]
        filename = dest_parts[-1] if dest_parts else local_path.name
        size_mb = int(row["SizeBytes"]) / (1024 ** 2) if row["SizeBytes"] else 0

        print(f"[{i}/{len(rows)}] {local_path.name} ({size_mb:.1f} MB)")

        try:
            folder_id = resolve_drive_folder(service, folder_parts, root_id, folder_cache)
            if upload_file_to_drive(service, local_path, folder_id, filename):
                print(f"  + {row['DriveDest']}")
                uploaded += 1
                log_rows.append({"LocalPath": str(local_path), "DriveDest": row["DriveDest"], "SizeBytes": row["SizeBytes"]})
            else:
                failed += 1
        except KeyboardInterrupt:
            print(f"\nInterrupted after {uploaded} uploads.")
            break
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed += 1

    print(f"\nDone: {uploaded} uploaded, {failed} failed, {skipped} missing locally.")

    if log_rows and log_path:
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["LocalPath", "DriveDest", "SizeBytes"])
            writer.writeheader()
            writer.writerows(log_rows)
        print(f"Upload log saved to: {log_path}  ({len(log_rows):,} entries)")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync local files to Google Drive under 'Laptop Backup 2026/'."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-drive", help="Fetch all Drive files → CSV")
    p_list.add_argument("--out", default="drive_listing.csv", metavar="FILE",
                        help="Output CSV path (default: drive_listing.csv)")

    p_cmp = sub.add_parser("compare", help="Compare local manifest vs Drive listing → CSV")
    p_cmp.add_argument("--manifest", required=True, metavar="FILE",
                       help="Local manifest CSV from the drive scan")
    p_cmp.add_argument("--listing", required=True, metavar="FILE",
                       help="Drive listing CSV from list-drive")
    p_cmp.add_argument("--out", default="comparison.csv", metavar="FILE",
                       help="Output CSV path (default: comparison.csv)")

    p_up = sub.add_parser("upload", help="Upload 'new' files from comparison CSV to Drive")
    p_up.add_argument("--comparison", required=True, metavar="FILE",
                      help="Comparison CSV from compare command")
    p_up.add_argument("--dry-run", action="store_true",
                      help="Preview what would be uploaded without uploading")
    p_up.add_argument("--upload-conflicts", action="store_true",
                      help="Also upload conflict files to 'Laptop Backup 2026/Conflicts/...' to preserve local versions")
    p_up.add_argument("--log", metavar="FILE", default="upload_log.csv",
                      help="CSV file to record successfully uploaded files (default: upload_log.csv)")
    p_up.add_argument("--category", metavar="CATEGORY",
                      help="Only upload files in this category (e.g. 'University')")

    args = parser.parse_args()

    # compare is purely local — no Drive auth needed
    if args.command == "compare":
        cmd_compare(Path(args.manifest), Path(args.listing), Path(args.out))
        return

    if not CREDENTIALS_FILE.exists():
        sys.exit(f"ERROR: credentials.json not found at {CREDENTIALS_FILE}\n"
                 f"Copy your Google Drive OAuth credentials file there.")

    print("Authenticating with Google Drive...")
    service = build("drive", "v3", credentials=auth())

    if args.command == "list-drive":
        cmd_list_drive(service, Path(args.out))
    elif args.command == "upload":
        cmd_upload(service, Path(args.comparison),
                   dry_run=args.dry_run, category_filter=args.category,
                   upload_conflicts=args.upload_conflicts,
                   log_path=Path(args.log) if not args.dry_run else None)


if __name__ == "__main__":
    main()
