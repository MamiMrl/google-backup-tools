import csv, os
from pathlib import Path

log = Path(r"D:\GitHub\google-backup-tools\upload_log.csv")
deleted = failed = 0
with open(log, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        try:
            os.remove(row["LocalPath"])
            deleted += 1
        except OSError as e:
            print(f"FAILED: {row['LocalPath']} — {e}")
            failed += 1
print(f"Deleted {deleted:,} files. Failed: {failed}.")
