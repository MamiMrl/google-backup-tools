"""Move target folders to Desktop so user can manually upload via browser."""
import os
import shutil
from pathlib import Path

DESKTOP = Path(r"C:\Users\huawei\Desktop")
MASAUSTU = Path(r"D:\OneDrive - Kadir Has University")

# Find the Masaüstü folder by scanning (avoids encoding issue)
masaustu = None
with os.scandir(str(MASAUSTU)) as it:
    for entry in it:
        if entry.is_dir() and entry.name.lower().startswith("masa"):
            masaustu = Path(entry.path)
            print(f"Found OneDrive desktop: {entry.name!r}")
            break

if not masaustu:
    print("ERROR: Could not find Masaüstü folder")
    raise SystemExit(1)

TARGETS = ["DESKTOP FILES", "DS-projects", "User Experience Enhancement in VR"]

for target_name in TARGETS:
    src = None
    with os.scandir(str(masaustu)) as it:
        for entry in it:
            if entry.name == target_name:
                src = Path(entry.path)
                break

    if src is None:
        print(f"NOT FOUND: {target_name}")
        continue

    dest = DESKTOP / target_name
    if dest.exists():
        print(f"ALREADY EXISTS on Desktop, skipping: {target_name}")
        continue

    print(f"Moving: {target_name!r} -> Desktop")
    shutil.move(str(src), str(dest))
    print(f"  Done.")

print("\nAll done. The folders are now on your Desktop.")
print("Right-click each folder on the Desktop > 'Always keep on this device' to download,")
print("then upload manually to Google Drive.")
