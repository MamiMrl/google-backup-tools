#!/usr/bin/env python3
import argparse
import hashlib
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from tqdm import tqdm

SCOPES = ["https://www.googleapis.com/auth/drive"]
SIZE_THRESHOLD = 50 * 1024 * 1024  # 50 MB in bytes
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "token.json"

MIME_FOLDER_MAP = {
    "application/pdf": "PDFs",
    "application/zip": "Archives",
    "application/x-zip-compressed": "Archives",
    "application/x-rar-compressed": "Archives",
    "application/x-compressed": "Archives",
    "application/x-7z-compressed": "Archives",
    "application/gzip": "Archives",
    "application/x-tar": "Archives",
    "application/x-bzip2": "Archives",
}


def get_type_folder(mime_type: str) -> str:
    if mime_type.startswith("video/"):
        return "Videos"
    if mime_type.startswith("image/"):
        return "Images"
    return MIME_FOLDER_MAP.get(mime_type, "Other")


def auth() -> Credentials:
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def list_large_files(service) -> list[dict]:
    """Return all owned files > 50 MB, sorted largest first."""
    files = []
    page_token = None
    while True:
        resp = service.files().list(
            q="trashed = false and 'me' in owners",
            fields="nextPageToken, files(id, name, size, mimeType, createdTime, md5Checksum)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            if "size" not in f:
                continue  # Google Workspace files (Docs, Sheets, etc.) have no binary size
            if int(f["size"]) > SIZE_THRESHOLD:
                files.append(f)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    files.sort(key=lambda f: int(f["size"]), reverse=True)
    return files


def candidate_path(file: dict, output_root: Path) -> Path:
    year = file["createdTime"][:4]
    folder = get_type_folder(file["mimeType"])
    return output_root / folder / year / file["name"]


def resolve_path(file: dict, output_root: Path) -> Path:
    """Return the destination path for a file.

    If the candidate path exists but belongs to a different file (size mismatch),
    append the Drive file ID to the stem to avoid overwriting it.
    """
    path = candidate_path(file, output_root)
    if path.exists() and path.stat().st_size != int(file["size"]):
        path = path.with_name(f"{path.stem}_{file['id']}{path.suffix}")
    return path


def compute_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_verified_on_ssd(file: dict, path: Path) -> bool:
    if not path.exists():
        return False
    if path.stat().st_size != int(file["size"]):
        return False
    if "md5Checksum" in file and compute_md5(path) != file["md5Checksum"]:
        return False
    return True


def download_file(service, file: dict, path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    size = int(file["size"])
    tmp_path = path.with_suffix(path.suffix + ".part")
    try:
        with open(tmp_path, "wb") as fh:
            downloader = MediaIoBaseDownload(
                fh, service.files().get_media(fileId=file["id"]), chunksize=8 * 1024 * 1024
            )
            with tqdm(total=size, unit="B", unit_scale=True, desc=file["name"][:50], leave=False) as bar:
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    bar.update(int(status.resumable_progress) - bar.n)
    except KeyboardInterrupt:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        print(f"  ERROR downloading {file['name']}: {exc}")
        return False

    if tmp_path.stat().st_size != size:
        tmp_path.unlink(missing_ok=True)
        print(f"  ERROR size mismatch for {file['name']}: expected {size}, got {tmp_path.stat().st_size}")
        return False

    if "md5Checksum" in file and compute_md5(tmp_path) != file["md5Checksum"]:
        tmp_path.unlink(missing_ok=True)
        print(f"  ERROR MD5 mismatch for {file['name']}")
        return False

    tmp_path.rename(path)
    return True


def run_dry_run(service, output_root: Path, limit: int | None = None) -> None:
    print("Scanning Drive for files > 50 MB (owned by you)...")
    files = list_large_files(service)[:limit]

    if not files:
        print("No files found above the 50 MB threshold.")
        return

    total_bytes = sum(int(f["size"]) for f in files)

    print(f"\nFound {len(files)} file(s) totalling {total_bytes / (1024**3):.2f} GB\n")
    print(f"{'#':<5}  {'Name':<52}  {'Size (bytes)':>14}  Destination")
    print("-" * 130)
    for i, f in enumerate(files, 1):
        print(f"{i:<5}  {f['name'][:52]:<52}  {int(f['size']):>14,}  {candidate_path(f, output_root)}")

    print(f"\nDry-run complete. Run --download to copy files to {output_root} (no deletion).")


def run_download(service, output_root: Path, limit: int | None = None) -> None:
    print("Scanning Drive for files > 50 MB (owned by you)...")
    files = list_large_files(service)[:limit]

    if not files:
        print("No files found above the 50 MB threshold.")
        return

    print(f"\nFound {len(files)} file(s). Downloading to {output_root} ...\n")
    downloaded, skipped = 0, 0
    failed_ids: set[str] = set()

    for i, f in enumerate(files, 1):
        path = resolve_path(f, output_root)
        print(f"[{i}/{len(files)}] {f['name']}")

        if is_verified_on_ssd(f, path):
            print(f"  Already verified on disk — skipping.")
            skipped += 1
            continue

        if download_file(service, f, path):
            print(f"  Downloaded -> {path}")
            downloaded += 1
        else:
            failed_ids.add(f["id"])

    print(f"\nDownload complete: {downloaded} downloaded, {skipped} already on disk, {len(failed_ids)} failed.")
    if failed_ids:
        print("Failed files:")
        for f in files:
            if f["id"] in failed_ids:
                print(f"  - {f['name']}")
    print("\nRun --delete when you've verified the disk contents.")


def run_delete(service, output_root: Path, limit: int | None = None) -> None:
    print("Scanning Drive for files > 50 MB (owned by you)...")
    files = list_large_files(service)[:limit]

    if not files:
        print("No files found above the 50 MB threshold.")
        return

    print(f"Verifying {len(files)} file(s) against disk (size + MD5)...\n")
    verified: list[tuple[dict, Path]] = []
    unverified: list[dict] = []

    for i, f in enumerate(files, 1):
        path = resolve_path(f, output_root)
        print(f"[{i}/{len(files)}] {f['name'][:70]}...", end=" ", flush=True)

        if not path.exists():
            print("NOT ON DISK")
            unverified.append(f)
        elif path.stat().st_size != int(f["size"]):
            print("SIZE MISMATCH")
            unverified.append(f)
        elif "md5Checksum" in f and compute_md5(path) != f["md5Checksum"]:
            print("MD5 MISMATCH")
            unverified.append(f)
        else:
            print("OK")
            verified.append((f, path))

    if not verified:
        print("\nNo files verified on disk. Nothing to delete.")
        return

    total_bytes = sum(int(f["size"]) for f, _ in verified)

    print(f"\n{'#':<5}  {'Name':<52}  {'Size (bytes)':>14}  Path")
    print("-" * 130)
    for i, (f, path) in enumerate(verified, 1):
        print(f"{i:<5}  {f['name'][:52]:<52}  {int(f['size']):>14,}  {path}")

    print(f"\n{len(verified)} file(s) ({total_bytes / (1024**3):.2f} GB) will be permanently deleted from Google Drive.")
    if unverified:
        print(f"{len(unverified)} file(s) not verified on disk — will NOT be deleted.")

    try:
        input("\nPress Enter to delete from Drive, or Ctrl+C to abort: ")
    except KeyboardInterrupt:
        print("\nAborted. Nothing deleted.")
        return

    print()
    failed_ids: set[str] = set()
    for f, _ in verified:
        print(f"  Deleting {f['name'][:70]}...", end=" ", flush=True)
        try:
            service.files().delete(fileId=f["id"]).execute()
            print("done")
        except Exception as exc:
            print(f"ERROR: {exc}")
            failed_ids.add(f["id"])

    freed_bytes = sum(int(f["size"]) for f, _ in verified if f["id"] not in failed_ids)
    deleted = len(verified) - len(failed_ids)
    print(f"\nDone. {deleted} file(s) deleted, {freed_bytes / (1024**3):.2f} GB freed from Drive.")
    if failed_ids:
        print(f"{len(failed_ids)} deletion(s) failed — those files remain in Drive.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up large Google Drive files to local storage.")
    parser.add_argument(
        "--output", required=True, metavar="DIR",
        help="Destination directory (e.g. /Volumes/MyDisk/GoogleDrive)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--download", action="store_true", help="Download files to --output directory (no deletion).")
    group.add_argument("--delete", action="store_true", help="Delete from Drive files already verified on disk.")
    parser.add_argument("--limit", type=int, metavar="N", help="Process only the N largest files.")
    args = parser.parse_args()

    if not CREDENTIALS_FILE.exists():
        sys.exit(f"credentials.json not found at {CREDENTIALS_FILE}")

    output_root = Path(args.output)

    print("Authenticating with Google Drive...")
    service = build("drive", "v3", credentials=auth())

    if args.download:
        run_download(service, output_root, limit=args.limit)
    elif args.delete:
        run_delete(service, output_root, limit=args.limit)
    else:
        run_dry_run(service, output_root, limit=args.limit)


if __name__ == "__main__":
    main()
