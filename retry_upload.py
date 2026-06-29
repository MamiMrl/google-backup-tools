"""Upload specific files that failed due to Turkish characters in path."""
import csv
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from drive_backup import auth
from drive_sync import resolve_drive_folder
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Files to retry — (filename_lower, non-turkish parent folder to disambiguate)
TARGETS = [
    ("haptic feedback in vr.pptx",       "all internship files"),
    ("news.csv",                          "fake-news-detector"),
    ("presentation-muhammedmaral.pptx",   "user experience enhancement in vr"),
    ("presentation-muhammedmaral.pptx",   "presentation"),
]

repo = Path(__file__).parent

# Load DriveDest for each target from comparison.csv
dest_map: dict[tuple, str] = {}
with open(repo / "comparison.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        name = row["Name"].lower()
        dest = row["DriveDest"]
        parent = Path(dest).parent.name.lower()
        key = (name, parent)
        if key in [(t[0], t[1]) for t in TARGETS]:
            dest_map[key] = dest

print("Drive destinations:")
for k, v in dest_map.items():
    print(f"  {k} -> {v}")
print()

# Find actual file Path objects by walking the filesystem
# Start from the known-good ancestor (avoids reconstructing garbled string paths)
search_roots = [
    Path(r"D:\OneDrive - Kadir Has University"),
]

target_keys = {(t[0], t[1]) for t in TARGETS}
found_files: dict[tuple, Path] = {}

for search_root in search_roots:
    for dirpath, dirnames, filenames in os.walk(str(search_root), onerror=lambda e: None):
        for fname in filenames:
            name = fname.lower()
            parent = Path(dirpath).name.lower()
            key = (name, parent)
            if key in target_keys and key not in found_files:
                fpath = Path(dirpath) / fname
                found_files[key] = fpath
                print(f"Found: {fpath}")
        if len(found_files) == len(target_keys):
            break

print()

# Upload each found file
service = build("drive", "v3", credentials=auth())
root_id = service.files().get(fileId="root", fields="id").execute()["id"]
folder_cache: dict[str, str] = {}

existing_log: list[dict] = []
log_path = repo / "upload_log.csv"
with open(log_path, encoding="utf-8") as f:
    existing_log = list(csv.DictReader(f))

new_entries = []
for key, local in found_files.items():
    dest = dest_map.get(key)
    if not dest:
        print(f"No DriveDest for {key}, skipping.")
        continue
    dest_parts = [p for p in dest.split("/") if p]
    folder_id = resolve_drive_folder(service, dest_parts[:-1], root_id, folder_cache)
    filename = dest_parts[-1]
    size_mb = local.stat().st_size / (1024 ** 2)
    print(f"Uploading {local.name} ({size_mb:.1f} MB) -> {dest}")
    try:
        data = local.read_bytes()
        media = MediaIoBaseUpload(
            io.BytesIO(data),
            mimetype="application/octet-stream",
            resumable=True,
            chunksize=8 * 1024 * 1024,
        )
        meta = {"name": filename, "parents": [folder_id]}
        req = service.files().create(body=meta, media_body=media, fields="id")
        response = None
        while response is None:
            status, response = req.next_chunk()
            if status:
                print(f"  {int(status.progress() * 100)}%", end="\r")
        print(f"  Done.")
        new_entries.append({
            "LocalPath": str(local),
            "DriveDest": dest,
            "SizeBytes": str(local.stat().st_size),
        })
    except Exception as exc:
        print(f"  FAILED: {exc}")

if new_entries:
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["LocalPath", "DriveDest", "SizeBytes"])
        writer.writeheader()
        writer.writerows(existing_log + new_entries)
    total = len(existing_log) + len(new_entries)
    print(f"\nAdded {len(new_entries)} entries to upload_log.csv (total: {total:,})")
else:
    print("Nothing new uploaded.")
