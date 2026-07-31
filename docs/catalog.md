# Catalog

The catalog is the snapshot of Omada release artifacts that this tool queries.

It holds the data used for querying, fetching, and SHA256 tracking.

The catalog is published inside a signed bundle, so the file on disk is not bare JSON. The published copy is:

```text
catalog.sigstore.json
```

`--refresh` does not replace it. It writes a second bundle, `catalog-refresh.sigstore.json`, and whichever carries the newer signed date answers queries. See [Configuration](configuration.md) for the full rule.

For how the bundle is structured and checked, see [Catalog Verification](verification.md). This document describes the catalog data carried inside it.

---

# Snapshot Model

The catalog is not intended to be a permanent historical database.

It represents the current view of the configured release sources.

```text
Release Sources
        │
        ▼
Published Catalog
        │
        ▼
catalog.sigstore.json
```

If TP-Link removes a release from its pages, that release stops appearing in newly published catalogs.

This behavior keeps the catalog deterministic and reflects exactly what was observed at publication time.

---

# Catalog Format

The catalog is stored as JSON.

```json
{
  "entries": {
    "<fingerprint>": {
      "version": "6.2.14.10",
      "platform": "linux",
      "package": "tgz",
      "archive": "zip",
      "kind": "pre-release",
      "filename": "...",
      "download_url": "...",
      "source_url": "...",
      "title": "...",
      "sha256": "..."
    }
  },
  "schema": 1,
  "updated": "2026-07-02T20:54:09Z"
}
```

The catalog key is the release fingerprint.

The fingerprint is reintroduced automatically when query results are returned.

---

# Fingerprints

Each catalog entry is keyed by a fingerprint.

The fingerprint identifies the logical release artifact.

The fingerprint intentionally excludes `download_url`.

This prevents TP-Link URL changes from creating duplicate catalog entries.

For example, if TP-Link republishes the same artifact using a URL with corrected encoding or a different path, the catalog should still treat the entry as the same artifact.

---

# Download URLs

The original filename is preserved.

Only the stored `download_url` is URL encoded when required.

Example filename:

```text
Omada_SDN_Controller_v5.15.24.18_linux_x64_20250630184423 (1).tar.gz
```

Example stored URL:

```text
.../Omada_SDN_Controller_v5.15.24.18_linux_x64_20250630184423%20(1).tar.gz
```

This preserves the vendor filename while ensuring the URL can be requested safely.

---

# SHA256

An entry may carry the SHA256 of its artifact, recorded by the publisher when the catalog was built.

SHA256 is the authoritative identity of the artifact itself, and fetch compares against it: a local file whose content no longer matches is reported rather than overwritten.

The tool does not write hashes back. The catalog is inside a signed bundle, so modifying it would invalidate the signature. When an entry carries no recorded hash, fetch computes and reports one but has nothing to compare it against.

A recorded hash is trust on first use. It establishes that an artifact has not changed since it was first observed, not that it is authentic from TP-Link, which publishes no checksums of its own.

---

# Package Terminology

The tool separates the installable package from the outer archive.

| Filename | Package | Archive |
|-----------|---------|----------|
| `.deb` | deb | none |
| `.deb.zip` | deb | zip |
| `.tar.gz` | tgz | none |
| `.tar.gz.zip` | tgz | zip |
| Windows ZIP installer | exe | zip |
| `.bin` | bin | none |
