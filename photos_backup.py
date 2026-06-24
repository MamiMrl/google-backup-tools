#!/usr/bin/env python3
import argparse
import sys
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from tqdm import tqdm

SCOPES = ["https://www.googleapis.com/auth/photoslibrary.readonly"]
PHOTOS_API = "https://photoslibrary.googleapis.com/v1"
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "token_photos.json"


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


def list_media_before(session: AuthorizedSession, before_year: int) -> list[dict]:
    """Return all media items with creationTime before before_year, sorted oldest first."""
    items = []
    page_token = None
    body = {
        "pageSize": 100,
        "filters": {
            "dateFilter": {
                "ranges": [{
                    "startDate": {"year": 1, "month": 1, "day": 1},
                    "endDate": {"year": before_year - 1, "month": 12, "day": 31},
                }]
            }
        },
    }
    print("  Fetching: ", end="", flush=True)
    page = 0
    while True:
        if page_token:
            body["pageToken"] = page_token
        resp = session.post(f"{PHOTOS_API}/mediaItems:search", json=body)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("mediaItems", [])
        items.extend(batch)
        page += 1
        print(f"page {page} ({len(items)} total) ", end="", flush=True)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    print()
    items.sort(key=lambda x: x["mediaMetadata"]["creationTime"])
    return items


def dest_path(item: dict, output_root: Path) -> Path:
    creation_time = item["mediaMetadata"]["creationTime"]
    dt = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
    return output_root / str(dt.year) / f"{dt.month:02d}" / item["filename"]


def fetch_fresh_item(session: AuthorizedSession, item_id: str) -> dict:
    """Re-fetch a media item to get a fresh baseUrl (they expire after ~60 min)."""
    resp = session.get(f"{PHOTOS_API}/mediaItems/{item_id}")
    resp.raise_for_status()
    return resp.json()


def build_download_url(item: dict) -> str:
    suffix = "=dv" if item["mimeType"].startswith("video/") else "=d"
    return item["baseUrl"] + suffix


def download_item(session: AuthorizedSession, item: dict, path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = fetch_fresh_item(session, item["id"])
    url = build_download_url(fresh)
    tmp_path = path.with_suffix(path.suffix + ".part")
    try:
        with session.get(url, stream=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0)) or None
            with open(tmp_path, "wb") as fh:
                with tqdm(total=total, unit="B", unit_scale=True,
                          desc=item["filename"][:50], leave=False) as bar:
                    for chunk in r.iter_content(8 * 1024 * 1024):
                        fh.write(chunk)
                        bar.update(len(chunk))
    except KeyboardInterrupt:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        print(f"  ERROR: {exc}")
        return False
    tmp_path.rename(path)
    return True


def run_dry_run(session: AuthorizedSession, before_year: int) -> None:
    print(f"Scanning Google Photos for media before {before_year}...")
    items = list_media_before(session, before_year)
    if not items:
        print(f"No media found before {before_year}.")
        return
    print(f"\nFound {len(items)} item(s) before {before_year}\n")
    print(f"{'#':<6}  {'Date':<12}  {'Type':<12}  Filename")
    print("-" * 90)
    for i, item in enumerate(items[:100], 1):
        dt = item["mediaMetadata"]["creationTime"][:10]
        mime_short = item["mimeType"].split("/")[-1]
        print(f"{i:<6}  {dt:<12}  {mime_short:<12}  {item['filename']}")
    if len(items) > 100:
        print(f"  ... and {len(items) - 100} more")
    print(f"\nDry-run complete. Run --download to copy to your output directory.")


def run_download(session: AuthorizedSession, before_year: int, output: Path) -> None:
    print(f"Scanning Google Photos for media before {before_year}...")
    items = list_media_before(session, before_year)
    if not items:
        print(f"No media found before {before_year}.")
        return
    print(f"\nFound {len(items)} item(s). Downloading to {output} ...\n")
    downloaded, skipped, failed = 0, 0, 0
    for i, item in enumerate(items, 1):
        path = dest_path(item, output)
        print(f"[{i}/{len(items)}] {item['filename']}")
        if path.exists():
            print("  Already on disk — skipping.")
            skipped += 1
            continue
        if download_item(session, item, path):
            print(f"  -> {path}")
            downloaded += 1
        else:
            failed += 1
    total_accounted = downloaded + skipped
    print(f"\nDownload complete: {downloaded} downloaded, {skipped} already on disk, {failed} failed.")
    print(f"\n{total_accounted} item(s) from before {before_year} are on your disk.")
    print(f"\nTo free space in Google Photos:")
    print(f"  1. Go to photos.google.com")
    print(f"  2. Search or filter by date (before Jan 1, {before_year})")
    print(f"  3. Verify the count matches {total_accounted}")
    print(f"  4. Select all and delete")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Google Photos media to local storage.",
    )
    parser.add_argument(
        "--output", required=True, metavar="DIR",
        help="Destination directory (e.g. /Volumes/MyDisk/GooglePhotos)",
    )
    parser.add_argument(
        "--before", type=int, default=2025, metavar="YEAR",
        help="Download media with a creation date before this year (default: 2025)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--download", action="store_true", help="Download media to --output directory.")
    args = parser.parse_args()

    if not CREDENTIALS_FILE.exists():
        sys.exit(f"credentials.json not found at {CREDENTIALS_FILE}")

    print("Authenticating with Google Photos...")
    creds = auth()
    session = AuthorizedSession(creds)

    if args.download:
        run_download(session, args.before, Path(args.output))
    else:
        run_dry_run(session, args.before)


if __name__ == "__main__":
    main()
