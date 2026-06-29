"""Find files that were attempted but not in upload_log (i.e. the 46 failures)."""
import csv
from pathlib import Path

repo = Path(r"D:\GitHub\google-backup-tools")

# Build set of successfully uploaded local paths
uploaded = set()
with open(repo / "upload_log.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        uploaded.add(row["LocalPath"].lower())

# Collect what was attempted (new + conflict rows that pass extension whitelist)
KEEP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw", ".dng", ".webp",
    ".tga", ".psd", ".ai", ".svg",
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".3gp", ".flv",
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac",
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".odt", ".ods", ".odp", ".tex", ".bib", ".rtf",
    ".ipynb", ".csv",
    ".indd", ".fig", ".sketch", ".xd", ".fbx",
    ".zip", ".rar", ".7z", ".tar", ".gz",
}

failures = []
with open(repo / "comparison.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        status = row["DriveStatus"]
        if status not in ("new", "conflict"):
            continue
        ext = Path(row["LocalPath"]).suffix.lower()
        if ext not in KEEP_EXTENSIONS:
            continue
        if row["LocalPath"].lower() not in uploaded:
            failures.append(row)

print(f"Failed uploads: {len(failures)}\n")
for r in failures:
    size_mb = int(r["SizeBytes"]) / (1024**2) if r["SizeBytes"] else 0
    print(f"  [{r['DriveStatus']:8}] {size_mb:6.1f} MB  {r['LocalPath']}")
