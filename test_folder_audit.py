"""Unit tests for folder_audit pure logic (no Drive / disk access)."""

from folder_audit import (
    bucket_parts, classify_status, expected_drive_relpath, match_folder,
)


def test_bucket_parts_rolls_deep_files_up_to_depth2():
    assert bucket_parts(["GitHub", "repo", "src", "x"], 2) == ("GitHub", "repo")


def test_bucket_parts_depth2_folder_itself():
    assert bucket_parts(["GitHub", "repo"], 2) == ("GitHub", "repo")


def test_bucket_parts_loose_depth1_file():
    assert bucket_parts(["GitHub"], 2) == ("GitHub",)


def test_bucket_parts_loose_root_file():
    assert bucket_parts([], 2) == ()


# ── rename-aware path translation ──────────────────────────────────────────────

def test_expected_path_applies_rename():
    # PATH_BASES maps the OneDrive Masaüstü tree to University/Desktop.
    assert expected_drive_relpath(
        r"D:\OneDrive - Kadir Has University\Masaüstü") == "University/Desktop"


def test_expected_path_appends_remainder():
    assert expected_drive_relpath(r"D:\GitHub\my-repo") == "Projects/GitHub/my-repo"


def test_expected_path_none_when_unmapped():
    assert expected_drive_relpath(r"D:\Hearthstone") is None


# ── subset-aware status ────────────────────────────────────────────────────────

def test_status_missing_when_no_drive_folder():
    assert classify_status(1000, None, tolerance=0) == "missing"


def test_status_backed_up_when_drive_covers_local():
    # Drive larger than local (renamed Desktop holds more) → covered.
    assert classify_status(1000, 1500, tolerance=0) == "backed_up"


def test_status_backed_up_on_equal():
    assert classify_status(1000, 1000, tolerance=0) == "backed_up"


def test_status_partial_when_drive_smaller():
    assert classify_status(1000, 600, tolerance=0) == "partial"


def test_status_tolerance_absorbs_small_gap():
    assert classify_status(1000, 995, tolerance=10) == "backed_up"


def test_status_empty_folder_is_vacuously_backed_up():
    assert classify_status(0, None, tolerance=0) == "backed_up"


# ── matcher prefers path over name ─────────────────────────────────────────────

def test_match_prefers_path_translation():
    by_path = {"university/desktop": (3000, "Laptop Backup 2026/University/Desktop")}
    by_name = {"masaüstü": [(50, "somewhere/else")]}
    folder = {"LocalPath": r"D:\OneDrive - Kadir Has University\Masaüstü",
              "FolderName": "Masaüstü"}
    bytes_, path = match_folder(folder, by_path, by_name)
    assert bytes_ == 3000 and path.endswith("University/Desktop")


def test_match_falls_back_to_fullest_name():
    by_path = {}
    by_name = {"unity files": [(1271, "a/Unity Files"), (5871, "b/Unity Files")]}
    folder = {"LocalPath": r"D:\Some Unmapped\Unity Files", "FolderName": "Unity Files"}
    bytes_, path = match_folder(folder, by_path, by_name)
    assert bytes_ == 5871


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
