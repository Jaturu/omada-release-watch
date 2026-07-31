# Fetch Workflow

This document describes how Omada Release Watch fetches release artifacts.

---

# Purpose

Fetch downloads a selected Omada release artifact, computes its SHA256 digest, and reports it.

A fetch also refuses to write to the configuration file or either catalog copy, whatever the catalog calls the entry. Those are the files the tool reads to decide what to trust, and an artifact written over one of them lands owned by whoever ran the command, which is the shape those checks look for.

A filename from the catalog has to match an allowlist before anything is written. The vendor uses letters, digits, spaces, dots, underscores, hyphens, parentheses and brackets, and nothing else is accepted. A denylist of path separators would let through drive-relative Windows paths, control characters and terminal escape sequences, all of which reach the terminal when a record is printed.

Fetch is intentionally explicit.

A query operation never downloads artifacts.

A refresh operation never downloads artifacts.

---

# Basic Usage

Fetch requires filters that identify exactly one artifact.

Typical usage:

```bash
./omada-release-watch.py \
  --fetch \
  --latest \
  --kind stable \
  --platform linux \
  --package tgz
```

`--kind` is always required, even when the other filters already resolve to exactly one artifact. Fetch never infers stable vs. pre-release on your behalf.

---

# Workflow

```text
Catalog
    ↓
Select exactly one artifact
    ↓
Check local cache
    ↓
Download from TP-Link, if needed
    ↓
Compute SHA256
    ↓
Compare against the catalog
```

TP-Link should be treated as the last resort when a matching artifact is already available locally.

---

# Selection Rules

Fetch must resolve to exactly one artifact.

If the filters match zero artifacts, fetch fails.

If the filters match more than one artifact, fetch fails and the query should be narrowed.

`--kind` is required on every fetch, regardless of whether the other filters already narrow to one artifact. The remaining filters are optional and only needed as far as narrowing requires.

| Filter | Purpose |
|----------|---------|
| `--kind` | Select stable or pre-release artifacts. Required. |
| `--latest` | Select the newest matching release. |
| `--version-prefix` | Restrict matches to a version prefix. |
| `--platform` | Restrict by platform, such as `linux`. |
| `--package` | Restrict by package type, such as `deb` or `tgz`. |
| `--archive` | Restrict by archive wrapper, such as `zip`. |

---

# Decision Matrix

| Local | Catalog SHA256 | Action |
|-------|----------------|--------|
| exists | matches | Skip the download. |
| exists | absent | Hash the local file and skip the download. |
| exists | differs | Stop with an error and refuse to overwrite. |
| missing | any | Download the artifact. |

This behavior allows previously acquired artifacts to be reused before downloading from TP-Link again.

---

# Local Cache

Fetched artifacts are stored in the configured output directory.

The default output directory is controlled by configuration.

Example:

```yaml
fetch:
  output_dir: downloads
```

If a local file already exists, Omada Release Watch can compute its SHA256 and compare it with catalog metadata.

---

# SHA256

After an artifact is fetched, its SHA256 digest is computed and reported.

Where the catalog records a hash for that entry, the two are compared and a mismatch stops the fetch. This applies to both paths. A fresh download that does not match is discarded rather than written, and an existing local file that does not match is reported rather than overwritten.

The comparison is what makes signing the catalog worth anything for artifacts. Vendor download URLs carry no signature of their own, so the recorded hash is the only thing tying the bytes that arrive to the bytes the catalog vouched for.

Where the catalog records no hash, nothing is compared, and the digest reported afterwards is one this tool computed from whatever arrived. The result says so: `sha256_verified` is `false` under `--json`, and console output adds a note. The two claims look alike and are not, so they are labelled rather than left to the reader.

A download is also capped at 1 GiB, roughly double the largest artifact TP-Link currently publishes. Nothing about a response is known before it is read, and the size on the wire bounds nothing, so the ceiling is what stops a hostile endpoint filling the disk before the hash is ever compared. A download past the limit is discarded and leaves nothing behind.

The catalog itself is never written to, because it is inside a signed bundle. See [Catalog Format](catalog.md).

SHA256 is the authoritative identity of the downloaded artifact.

The download URL is not considered authoritative because vendor URLs can change.

---

# Examples

## Fetch the latest stable Linux tarball

```bash
./omada-release-watch.py \
  --fetch \
  --latest \
  --kind stable \
  --platform linux \
  --package tgz
```

## Fetch the latest pre-release Linux Debian package

```bash
./omada-release-watch.py \
  --fetch \
  --latest \
  --kind pre-release \
  --platform linux \
  --package deb
```

## Refresh before fetching

```bash
./omada-release-watch.py \
  --refresh \
  --fetch \
  --latest \
  --kind stable \
  --platform linux \
  --package tgz
```
