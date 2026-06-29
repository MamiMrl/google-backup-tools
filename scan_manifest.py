#!/usr/bin/env python3
"""
scan_manifest.py — Walk C:\\Users\\huawei and D:\\ to produce manifest.csv.

Output columns: FullPath, Name, Category, SizeBytes

Category is derived from PATH_BASES in drive_sync.py.
Files not matching any PATH_BASE land in "Other" — shown in the summary
so you can spot anything important that was missed, but NOT uploaded by default.
"""

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from drive_sync import EXCLUDE_PREFIXES, PATH_BASES, KEEP_CATEGORIES, KEEP_EXTENSIONS

SCAN_ROOTS = [
    r"C:\Users\huawei",
    r"D:\\",
]

HOME_DIR = r"c:\users\huawei"
# Hidden dotdirs directly under the home folder that are worth keeping.
# Everything else starting with '.' is an IDE config, tool cache, or app data.
ALLOWED_HOME_DOTDIRS = {".ssh"}

# System / cache directories to always skip (not worth reviewing)
SYSTEM_SKIP = [
    r"c:\users\huawei\appdata",   # all app caches and executables — nothing personal here
    r"d:\$recycle.bin",
    r"d:\system volume information",
    r"d:\windowsapps",
    r"d:\recovery",
    r"d:\pagefile.sys",
    r"d:\mongodb",
]

OUTPUT_FILE = Path(__file__).parent / "manifest.csv"


def is_excluded(path_str: str) -> bool:
    lp = path_str.lower()
    for prefix in EXCLUDE_PREFIXES:
        if lp.startswith(prefix.lower()):
            return True
    for prefix in SYSTEM_SKIP:
        if lp.startswith(prefix):
            return True
    return False


def get_category(local_path: str) -> str:
    lp = local_path.lower()
    for prefix, dest in PATH_BASES:
        if lp.startswith(prefix.lower()):
            return dest.split("/")[0]
    return "Other"


def fmt_gb(n_bytes: int) -> str:
    return f"{n_bytes / (1024 ** 3):.2f} GB"


def fmt_mb(n_bytes: int) -> str:
    return f"{n_bytes / (1024 ** 2):.1f} MB"


def scan() -> list[dict]:
    rows = []
    skipped_dirs = 0
    skipped_ext = 0
    error_count = 0

    for root in SCAN_ROOTS:
        root_path = Path(root)
        if not root_path.exists():
            print(f"WARNING: {root} not found, skipping.")
            continue

        print(f"Scanning {root} ...")
        file_count = 0

        for dirpath, dirnames, filenames in os.walk(root_path, topdown=True, onerror=lambda e: None):
            dir_str = str(dirpath)

            if is_excluded(dir_str):
                dirnames.clear()
                skipped_dirs += 1
                continue

            # Prune hidden dotdirs directly under the home folder —
            # these are IDE configs, tool caches, and app data, not personal files.
            if dir_str.lower() == HOME_DIR:
                before_dot = len(dirnames)
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith(".") or d.lower() in ALLOWED_HOME_DOTDIRS
                ]
                skipped_dirs += before_dot - len(dirnames)

            # Prune excluded subdirs before descending
            before = len(dirnames)
            dirnames[:] = [
                d for d in dirnames
                if not is_excluded(os.path.join(dir_str, d))
            ]
            skipped_dirs += before - len(dirnames)

            for filename in filenames:
                full_path = os.path.join(dir_str, filename)
                if is_excluded(full_path):
                    continue
                ext = Path(filename).suffix.lower()
                if ext not in KEEP_EXTENSIONS:
                    skipped_ext += 1
                    continue
                try:
                    size = os.path.getsize(full_path)
                except (OSError, PermissionError):
                    error_count += 1
                    continue

                rows.append({
                    "FullPath": full_path,
                    "Name": filename,
                    "Category": get_category(full_path),
                    "SizeBytes": size,
                })
                file_count += 1
                if file_count % 500 == 0:
                    print(f"  {file_count:,} files so far...", end="\r")

        print(f"  Done — {file_count:,} files found in {root}          ")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["FullPath", "Name", "Category", "SizeBytes"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nExtension-filtered (not in whitelist): {skipped_ext:,}")
    print(f"Permission/read errors skipped: {error_count:,}")
    print(f"Manifest saved to: {OUTPUT_FILE}  ({len(rows):,} files)\n")

    return rows


def print_summary(rows: list[dict]) -> None:
    by_cat: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0})
    for row in rows:
        cat = row["Category"]
        by_cat[cat]["count"] += 1
        by_cat[cat]["bytes"] += row["SizeBytes"]

    upload_bytes = sum(
        s["bytes"] for c, s in by_cat.items() if c in KEEP_CATEGORIES
    )
    total_bytes = sum(row["SizeBytes"] for row in rows)

    print("=" * 65)
    print(f"SCAN SUMMARY — {len(rows):,} files, {fmt_gb(total_bytes)} total on disk")
    print("=" * 65)
    print(f"{'Category':<26} {'Files':>7}  {'Size':>9}  Note")
    print(f"{'-'*26} {'-'*7}  {'-'*9}  {'-'*20}")

    for cat, stats in sorted(by_cat.items(), key=lambda x: -x[1]["bytes"]):
        note = "WILL UPLOAD" if cat in KEEP_CATEGORIES else "review / skip"
        print(f"{cat:<26} {stats['count']:>7,}  {fmt_gb(stats['bytes']):>9}  {note}")

    print(f"\n  Total to upload : {fmt_gb(upload_bytes)}  (fits within 80 GB free on Drive)")
    print(f"\nTo drill into a category, run:")
    print(f"  python scan_manifest.py --drill <Category>")
    print(f"  e.g.  python scan_manifest.py --drill University")


def print_drill(rows: list[dict], category: str) -> None:
    cat_rows = [r for r in rows if r["Category"].lower() == category.lower()]
    if not cat_rows:
        print(f"No files found for category '{category}'.")
        print(f"Available: {sorted(set(r['Category'] for r in rows))}")
        return

    total = sum(r["SizeBytes"] for r in cat_rows)
    print(f"\n{'='*70}")
    print(f"DRILL-DOWN: {category}  ({len(cat_rows):,} files, {fmt_gb(total)})")
    print(f"{'='*70}")

    # Group by top-level folder under the path base
    by_folder: dict[str, list[dict]] = defaultdict(list)
    for row in cat_rows:
        parts = Path(row["FullPath"]).parts
        # Use the 4th path component as grouping key (e.g. D:\OneDrive\Desktop → Desktop)
        key = parts[3] if len(parts) > 3 else parts[-1]
        by_folder[key].append(row)

    for folder, files in sorted(by_folder.items(), key=lambda x: -sum(f["SizeBytes"] for f in x[1])):
        folder_bytes = sum(f["SizeBytes"] for f in files)
        print(f"\n  [{folder}]  {len(files):,} files  {fmt_gb(folder_bytes)}")
        for f in sorted(files, key=lambda x: -x["SizeBytes"])[:30]:
            print(f"    {fmt_mb(f['SizeBytes']):>9}  {f['FullPath']}")
        if len(files) > 30:
            print(f"    ... and {len(files) - 30} more files")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Scan drives and produce manifest.csv")
    parser.add_argument("--drill", metavar="CATEGORY",
                        help="Show all files in a specific category (reads existing manifest.csv)")
    args = parser.parse_args()

    if args.drill:
        if not OUTPUT_FILE.exists():
            sys.exit("manifest.csv not found — run scan_manifest.py first (without --drill).")
        rows = []
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["SizeBytes"] = int(row["SizeBytes"])
                rows.append(row)
        print_drill(rows, args.drill)
    else:
        rows = scan()
        print_summary(rows)


if __name__ == "__main__":
    main()
