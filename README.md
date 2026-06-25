# Google Drive Backup

CLI tool to audit, download, and clean up your Google Drive storage.

## What it does

`drive_backup.py` finds files larger than 50 MB in your Google Drive, downloads them to local storage organized by type and year, then deletes them from Drive once verified on disk.

## Prerequisites

- Python 3.11+
- A Google Cloud project with the **Google Drive API** enabled
- OAuth 2.0 Desktop credentials (`credentials.json`) from the Google Cloud Console

## Setup

### 1. Google Cloud credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Create a project (or select an existing one)
3. Enable **Google Drive API**
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Choose **Desktop app**, download the JSON, and save it as `credentials.json` in this directory

### 2. Install dependencies

```bash
pip install \
  google-auth \
  google-auth-oauthlib \
  google-auth-httplib2 \
  google-api-python-client \
  tqdm
```

> On first run, a browser window will open for OAuth authorization. The token is saved locally as `token.json` and reused on subsequent runs.

## Usage

```bash
# Dry-run: list all files > 50 MB and where they would be saved
python drive_backup.py --output /Volumes/MyDisk/GoogleDrive

# Download files to disk (safe — nothing is deleted from Drive)
python drive_backup.py --output /Volumes/MyDisk/GoogleDrive --download

# Delete from Drive only the files already verified on disk
python drive_backup.py --output /Volumes/MyDisk/GoogleDrive --delete

# Limit to the 10 largest files (useful for testing)
python drive_backup.py --output /Volumes/MyDisk/GoogleDrive --download --limit 10
```

Files are organized as `<output>/<Type>/<Year>/<filename>` (e.g. `Videos/2023/clip.mp4`).

## Security

`credentials.json` and `token.json` contain your OAuth secrets — they are listed in `.gitignore` and must never be committed or shared.
