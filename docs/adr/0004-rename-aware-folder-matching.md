# Rename-aware folder matching via PATH_BASES

The folder audit matches a local folder to its Drive counterpart by first translating the local path through `PATH_BASES` (the same rename map the upload used), and only falling back to leaf-name matching when no Path Base applies.

## Context

The grilling session settled on name + size matching. The first run against the real Drive produced 1 `backed_up` out of 307 folders and a 2.27 GB false `missing` for `…\Masaüstü`. Cause: the upload was not a mirror — `PATH_BASES` renamed top-level folders on the way up (`Masaüstü` → `University/Desktop`, `OneDrive…` → `University/OneDrive`), and stored several same-named folders at different sizes. Pure leaf-name matching cannot see through the renames and picks the wrong same-name copy.

## Decision

Match by **translated path** (rename-aware, unambiguous — the Conflicts copy lives at a different path and no longer collides), then by **leaf name** for folders outside every Path Base. Combined with subset-coverage status (ADR-0002), this moved the audit from 1 → 261 `backed_up` and eliminated the rename false-positives, leaving only genuine gaps (mostly content deliberately excluded in the prior session).

## Consequences

The audit now depends on `PATH_BASES` staying accurate. For reuse as an open-source tool against a different Drive, the path-translation map must be supplied or disabled (falling back to pure name matching).
