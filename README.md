# Google Backup Tools

CLI tools to audit, download, and clean up your Google Drive and Google Photos storage.

## Tools

| Script | What it does |
|---|---|
| `drive_backup.py` | Lists Drive files > 50 MB, downloads them to local storage, then deletes them from Drive |
| `photos_backup.py` | Lists and downloads Google Photos media before a given year to local storage |

## Prerequisites

- Python 3.11+
- A Google Cloud project with the **Google Drive API** and **Photos Library API** enabled
- OAuth 2.0 Desktop credentials (`credentials.json`) from the Google Cloud Console

## Setup

### 1. Google Cloud credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Create a project (or select an existing one)
3. Enable **Google Drive API** and **Photos Library API**
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

> On first run, a browser window will open for OAuth authorization. Tokens are saved locally as `token.json` (Drive) and `token_photos.json` (Photos) and reused on subsequent runs.

## Usage

### Drive Backup (`drive_backup.py`)

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

### Photos Backup (`photos_backup.py`)

```bash
# Dry-run: list all media before 2025
python photos_backup.py --output /Volumes/MyDisk/GooglePhotos

# Download media before 2025 to disk
python photos_backup.py --output /Volumes/MyDisk/GooglePhotos --download

# Download media before a different year
python photos_backup.py --output /Volumes/MyDisk/GooglePhotos --download --before 2023
```

Files are organized as `<output>/<Year>/<Month>/<filename>` (e.g. `2022/03/IMG_1234.jpg`).

**Note:** The Google Photos API does not support deletion. After downloading, the script prints step-by-step instructions for manually deleting the archived media from [photos.google.com](https://photos.google.com).

## Security

`credentials.json` and `token*.json` contain your OAuth secrets — they are listed in `.gitignore` and must never be committed or shared.
