# Audit scope limited to Laptop Backup 2026/

The folder audit searches for matching folders only inside `Laptop Backup 2026/` on Drive, not all of Drive. Drive contains 83,000+ files from various sources (university OneDrive sync, personal uploads, etc.). Searching all of Drive would produce false `backed_up` results — a folder named `Downloads` or `Desktop` exists on Drive for unrelated reasons and would incorrectly appear as backed up.
