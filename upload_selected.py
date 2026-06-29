#!/usr/bin/env python3
"""upload_selected.py — Upload a hand-picked set of folders to Google Drive.

Follow-up to folder_audit.py: the user reviewed the audit and chose specific
folders to back up. Two modes per folder:

  mirror : upload the folder's whitelisted media files, skipping any already on
           Drive (delta only). Lands under the same PATH_BASES structure as the
           original backup.
  zip    : zip the folder's FULL contents (source code included, minus .git /
           node_modules / build caches) and upload one .zip. For code projects
           whose files the media whitelist would otherwise skip.

Run with --dry-run first to preview. Reuses drive_sync upload primitives.
"""

import argparse
import csv
import os
import socket
import sys
import zipfile
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Resumable uploads over a slow link can stall; give sockets room before timing out.
socket.setdefaulttimeout(300)

# Files this size or smaller go up in a single simple request (no resumable session,
# no progress bar) — faster and dodges resumable-session stalls on tiny files.
SIMPLE_UPLOAD_MAX = 5 * 1024 * 1024

sys.path.insert(0, str(Path(__file__).parent))
from drive_backup import auth, CREDENTIALS_FILE
from drive_sync import (
    BACKUP_ROOT, KEEP_EXTENSIONS, drive_destination,
    resolve_drive_folder, upload_file_to_drive,
)

SCRATCH = Path(r"C:\Users\huawei\AppData\Local\Temp\claude\C--Users-huawei\a27dc3fc-ec4e-4cad-b8a9-ade7f601894e\scratchpad")
DRIVE_FILES_CACHE = Path(__file__).parent / "drive_files.csv"
LOG_FILE = Path(__file__).parent / "upload_selected_log.csv"

# ── selection (resolved + confirmed with the user) ─────────────────────────────

# (local_folder, drive_base_override)  — override is relative to BACKUP_ROOT;
# None means derive it from PATH_BASES via drive_destination().
MIRROR_FOLDERS = [
    (r"D:\Hearthstone\Hearthstone_Pics", "Media/Hearthstone Pics"),
    (r"C:\Users\huawei\Desktop\DESKTOP FILES", None),
    (r"D:\GitHub\VR-beer-pong_Anand", None),
    (r"D:\Unity Files\A4_Maral_Muhammed-Fixed", None),
    (r"D:\OneDrive - Kadir Has University\OneNote Uploads", None),
]

# Code/project folders → zip full contents, upload the zip.
ZIP_FOLDERS = [
    r"C:\Users\huawei\Desktop\Omnifood",
    r"C:\Users\huawei\eclipse-workspace\ChatAppMami",
    r"D:\DOWNLOADS\wetransfer_newproject_2024-08-06_1309",
    r"D:\WebStorm\E-Food",
    r"D:\Web Development - Jhonas\01-TEST",
    r"D:\Web Development - Jhonas\02-HTML-Fundamentals",
    r"D:\Web Development - Jhonas\03-CSS-Fundamentals",
    r"D:\HarvardX Leadership Principles\Part - 2 Lead With, Beyond, and Without Authority",
    r"D:\HarvardX Leadership Principles\Part - 3 Take Action",
]

# Never let these leak into a zip.
DENY_DIRS = {".git", "node_modules", "__pycache__", ".gradle", ".idea", ".vscode",
             "bin", "obj", "target", "build", "dist", ".next"}
DENY_FILES = {"credentials.json", "token.json", ".env", "id_rsa", "id_rsa.pub"}
DENY_SUFFIX = {".pem", ".key", ".class", ".part"}


# ── helpers ───────────────────────────────────────────────────────────────────

def fmt_mb(n: int) -> str:
    return f"{n / (1024 ** 2):.1f} MB"


def is_readable(path: str) -> bool:
    """OneDrive 'Files On-Demand' placeholders report a size but raise OSError 22
    on read because their bytes live only in the cloud. Detect that up front so
    such files are skipped with a clear note instead of crashing an upload."""
    try:
        with open(path, "rb") as fh:
            fh.read(1)
        return True
    except OSError:
        return False


def upload_one(service, local: Path, folder_id: str, fname: str, size: int) -> bool:
    """Upload a single file, choosing simple vs resumable by size."""
    if size <= SIMPLE_UPLOAD_MAX:
        media = MediaFileUpload(str(local), resumable=False)
        service.files().create(body={"name": fname, "parents": [folder_id]},
                               media_body=media, fields="id").execute()
        return True
    return upload_file_to_drive(service, local, folder_id, fname)


def fetch_drive_files(service, refresh: bool) -> tuple[set, set]:
    """Return (set of (name_lower, size) for dedup, set of name_lower for zip dedup).
    Cached to drive_files.csv; pass --refresh to re-fetch."""
    if DRIVE_FILES_CACHE.exists() and not refresh:
        print(f"Using cached {DRIVE_FILES_CACHE.name} (pass --refresh to re-fetch).")
        ns, names = set(), set()
        with open(DRIVE_FILES_CACHE, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                names.add(row["Name"].lower())
                ns.add((row["Name"].lower(), row["SizeBytes"]))
        return ns, names

    print("Fetching Drive file index (name + size) for dedup...")
    rows, page_token = [], None
    while True:
        resp = service.files().list(
            q="trashed=false and 'me' in owners and mimeType!='application/vnd.google-apps.folder'",
            fields="nextPageToken, files(name, size)", pageSize=1000, pageToken=page_token,
        ).execute()
        rows.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        print(f"  {len(rows):,} files...", end="\r")
        if not page_token:
            break
    print(f"  {len(rows):,} files total.            ")
    with open(DRIVE_FILES_CACHE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Name", "SizeBytes"])
        w.writeheader()
        for r in rows:
            w.writerow({"Name": r["name"], "SizeBytes": r.get("size", "")})
    ns = {(r["name"].lower(), str(r.get("size", ""))) for r in rows}
    names = {r["name"].lower() for r in rows}
    return ns, names


def base_full(folder: str, override: str | None) -> str:
    if override:
        return f"{BACKUP_ROOT}/{override}"
    # drive_destination maps the folder itself to its Drive home.
    return drive_destination(folder, "Projects")


def plan_mirror(drive_ns: set) -> list[dict]:
    """Build the list of media files to upload (delta only)."""
    plan = []
    for folder, override in MIRROR_FOLDERS:
        if not os.path.isdir(folder):
            print(f"  WARNING missing on disk: {folder}")
            continue
        base = base_full(folder, override)
        for dp, dn, fn in os.walk(folder):
            for name in fn:
                if Path(name).suffix.lower() not in KEEP_EXTENSIONS:
                    continue
                full = os.path.join(dp, name)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if (name.lower(), str(size)) in drive_ns:
                    continue  # already on Drive (name+size)
                if not is_readable(full):
                    print(f"  SKIP cloud-only/unreadable: {full}")
                    continue
                rel = os.path.relpath(full, folder).replace("\\", "/")
                plan.append({
                    "LocalPath": full,
                    "DriveDest": f"{base}/{rel}",
                    "SizeBytes": size,
                })
    return plan


def skip_in_zip(dirpath: str, folder: str) -> bool:
    parts = {p.lower() for p in Path(os.path.relpath(dirpath, folder)).parts}
    return bool(parts & DENY_DIRS)


def make_zip(folder: str) -> Path | None:
    """Zip a folder's contents (minus deny-listed dirs/files) into the scratchpad."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    name = Path(folder).name
    out = SCRATCH / f"{name}.zip"
    parent = Path(folder).parent
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dn, fn in os.walk(folder):
            dn[:] = [d for d in dn if d.lower() not in DENY_DIRS]
            if skip_in_zip(dp, folder):
                continue
            for f in fn:
                if f.lower() in DENY_FILES or Path(f).suffix.lower() in DENY_SUFFIX:
                    continue
                full = os.path.join(dp, f)
                try:
                    z.write(full, os.path.relpath(full, parent))
                    count += 1
                except OSError:
                    pass
    if count == 0:
        out.unlink(missing_ok=True)
        return None
    return out


# ── run ───────────────────────────────────────────────────────────────────────

def run(dry_run: bool, refresh: bool) -> None:
    if not dry_run and not CREDENTIALS_FILE.exists():
        sys.exit(f"credentials.json not found at {CREDENTIALS_FILE}")

    service = None
    if not dry_run or True:  # need Drive index either way for an accurate dry-run
        print("Authenticating with Google Drive...")
        service = build("drive", "v3", credentials=auth())

    drive_ns, drive_names = fetch_drive_files(service, refresh)

    mirror_plan = plan_mirror(drive_ns)
    mirror_bytes = sum(r["SizeBytes"] for r in mirror_plan)

    zip_plan = []
    for folder in ZIP_FOLDERS:
        if not os.path.isdir(folder):
            print(f"  WARNING missing on disk: {folder}")
            continue
        zip_name = f"{Path(folder).name}.zip"
        dest = f"{base_full(folder, None)}.zip"
        already = zip_name.lower() in drive_names
        zip_plan.append({"Folder": folder, "ZipName": zip_name, "DriveDest": dest, "Already": already})

    print("\n" + "=" * 72)
    print("UPLOAD PLAN")
    print("=" * 72)
    print(f"MIRROR — {len(mirror_plan):,} new media files, {fmt_mb(mirror_bytes)} (delta only)")
    by_folder = {}
    for r in mirror_plan:
        key = r["DriveDest"].rsplit("/", 1)[0]
        by_folder.setdefault(key, [0, 0])
        by_folder[key][0] += 1
        by_folder[key][1] += r["SizeBytes"]
    for dest, (c, b) in sorted(by_folder.items(), key=lambda x: -x[1][1]):
        print(f"    {c:>5} files  {fmt_mb(b):>10}  -> {dest}")

    print(f"\nZIP — {sum(1 for z in zip_plan if not z['Already'])} folders to zip+upload")
    for z in zip_plan:
        tag = "  [zip already on Drive — SKIP]" if z["Already"] else ""
        print(f"    {z['ZipName']:<45} -> {z['DriveDest']}{tag}")

    if dry_run:
        print("\n[DRY RUN] Nothing uploaded. Re-run without --dry-run to execute.")
        return

    # ── execute ────────────────────────────────────────────────────────────────
    root_id = service.files().get(fileId="root", fields="id").execute()["id"]
    cache: dict[str, str] = {}
    up = fail = 0

    def best_effort_unlink(p: Path) -> None:
        # The upload may briefly hold the file handle on Windows; leaving a stray
        # temp in the scratchpad is harmless, so never let cleanup crash the run.
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    # Append to the log incrementally and flush after every success, so a crash
    # or interrupt never loses the record of what already reached Drive.
    new_log = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as logf:
        writer = csv.DictWriter(logf, fieldnames=["Type", "Local", "DriveDest", "SizeBytes"])
        if new_log:
            writer.writeheader(); logf.flush()

        def record(kind, local, dest, size):
            nonlocal up
            up += 1
            writer.writerow({"Type": kind, "Local": str(local), "DriveDest": dest, "SizeBytes": size})
            logf.flush()

        print("\n--- MIRROR ---")
        for i, r in enumerate(mirror_plan, 1):
            parts = [p for p in r["DriveDest"].split("/") if p]
            folder_parts, fname = parts[:-1], parts[-1]
            local = Path(r["LocalPath"])
            print(f"[{i}/{len(mirror_plan)}] {local.name} ({fmt_mb(r['SizeBytes'])})")
            try:
                fid = resolve_drive_folder(service, folder_parts, root_id, cache)
                if upload_one(service, local, fid, fname, r["SizeBytes"]):
                    record("mirror", local, r["DriveDest"], r["SizeBytes"])
                else:
                    fail += 1
            except KeyboardInterrupt:
                print("\nInterrupted."); break
            except Exception as exc:
                print(f"  ERROR: {exc}"); fail += 1

        print("\n--- ZIP ---")
        for z in zip_plan:
            if z["Already"]:
                print(f"  SKIP (already on Drive): {z['ZipName']}")
                continue
            print(f"  Zipping {z['Folder']} ...")
            zp = make_zip(z["Folder"])
            if zp is None:
                print(f"  empty after filtering — skipped.")
                continue
            size = zp.stat().st_size
            parts = [p for p in z["DriveDest"].split("/") if p]
            folder_parts, fname = parts[:-1], parts[-1]
            print(f"  Uploading {z['ZipName']} ({fmt_mb(size)})")
            try:
                fid = resolve_drive_folder(service, folder_parts, root_id, cache)
                if upload_one(service, zp, fid, fname, size):
                    record("zip", z["Folder"], z["DriveDest"], size)
                else:
                    fail += 1
            except Exception as exc:
                print(f"  ERROR: {exc}"); fail += 1
            finally:
                best_effort_unlink(zp)

    print(f"\nDone: {up} uploaded, {fail} failed. Log -> {LOG_FILE}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Preview without uploading")
    ap.add_argument("--refresh", action="store_true", help="Re-fetch the Drive file index")
    args = ap.parse_args()
    run(args.dry_run, args.refresh)


if __name__ == "__main__":
    main()
