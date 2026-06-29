# Folder-level comparison as the unit of analysis

The folder audit compares Depth-2 Folders rather than individual files. File-level comparison already exists in `drive_sync.py compare` but produces 11,000+ rows — too granular to act on. Folder-level gives a high-level view where each row maps to a project, repo, or directory the user can consciously decide to upload or ignore.

## Considered Options

- **File-level** — already implemented; rejected because the output is too dense to review manually.
- **Top-level (depth-1)** — too coarse; `D:\GitHub` being "partially backed up" tells you nothing actionable.
- **Depth-2 (chosen)** — project/repo granularity; each row is a decision unit.
- **Configurable depth** — added as `--depth N` flag; default is 2.
