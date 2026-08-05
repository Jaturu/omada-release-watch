# Omada Release Watch

This project started as a personal experiment. It was an academic exercise in using AI coding assistance on something real rather than a toy problem. I was tired of manually checking for new releases and I was concerned that TP-Link didn't publish hashes to verify against. I built this tool to do both things. It is complete overkill for the problems it solves, but exploring how far modern AI tooling can go was the point.

Omada Release Watch is a command line utility for querying TP-Link Omada Software Controller releases and fetching their artifacts.

Release information comes from a signed catalog built and published by a separate crawler. This tool only consumes it, and verifies the signature before trusting it.

The catalog is read locally, so queries are deterministic and repeatable and do not depend on TP-Link's pages being reachable or unchanged. Fetching an artifact does reach TP-Link, since that is where the file itself comes from.

---

# Why This Project Exists

TP-Link distributes Omada Software Controller releases across multiple sources:

- Official download pages
- TP-Link Business Community pre-release topics
- Platform-specific download links

Finding a particular release often requires manually searching multiple pages, while historical releases may disappear over time as pages are updated.

Omada Release Watch provides a repeatable workflow:

1. Obtain the published catalog.
2. Query it.
3. Fetch exactly one selected artifact, checked against the hash the catalog records.

Release discovery is deliberately not part of this tool. A single scheduled crawler builds the catalog, so TP-Link sees one consumer rather than one per user of this project.

---

# Features

- Query stable and pre-release controller versions.
- Distinguish Linux and Windows packages.
- Identify package and archive types independently.
- Query releases by:
  - latest version
  - version prefix
  - platform
  - package
  - archive
  - release kind
- Fetch exactly one selected artifact, checked against the hash the catalog records for it. When the catalog records none, the result says so rather than presenting a self-computed hash as a verified one.
- Verify the catalog's signature on every run, reporting anything that does not check out.
- Refuse to fetch when catalog verification fails, unless told to proceed.
- Machine-readable JSON output.
- Human-readable console output.

---

# Installation

## Requirements

- Python 3.11 or newer
- requests
- sigstore
- PyYAML

Clone the repository:

```bash
git clone https://github.com/Jaturu/omada-release-watch.git
cd omada-release-watch
```

Install dependencies:

```bash
python -m pip install --require-hashes -r requirements.txt
```

Every pin carries a hash, so an install that cannot be verified fails instead of proceeding.

Create your local configuration from the example:

```bash
cp config.example.yaml config.yaml
```

`config.yaml` is local runtime state, gitignored and not tracked.

A clone comes with the published catalog, so there is nothing to download before the first query. `--refresh` replaces it when you want a newer one.

---

# Quick Start

Show the version:

```bash
./omada-release-watch.py --version
```

Show where the catalog is and whether it verified:

```bash
./omada-release-watch.py
```

Download the current published catalog:

```bash
./omada-release-watch.py --refresh
```

Show the newest release in the catalog:

```bash
./omada-release-watch.py --latest
```

Show only Linux packages:

```bash
./omada-release-watch.py --latest --platform linux
```

Show every 6.2 release:

```bash
./omada-release-watch.py --version-prefix 6.2
```

Update the catalog and fetch the newest stable Linux tarball, which is the common case:

```bash
./omada-release-watch.py --refresh --fetch --latest --kind stable --platform linux --package tgz
```

Fetch from the catalog already in place, without updating it first:

```bash
./omada-release-watch.py --fetch --latest --kind stable --platform linux --package tgz
```

---

# Container

The image runs as a non-root user and works from `/data`, where it looks for the catalog and for `config.yaml`.

No catalog is baked into the image, so refresh and query in one run:

```bash
docker run --rm docker.io/jaturu/omada-release-watch --refresh --latest
```

Mount a volume at `/data` to keep the catalog between runs. A named volume inherits the ownership the image set and needs nothing further. A host directory does not: the container writes as uid 100, which is real ownership on Linux, so the directory has to be writable by that uid or the run needs `--user`.

```bash
docker run --rm -v omada-catalog:/data docker.io/jaturu/omada-release-watch --refresh
```

Configuration is optional. Every setting has a default, and the example file inside the image shows what they are:

```bash
docker run --rm --entrypoint cat docker.io/jaturu/omada-release-watch /app/config.example.yaml
```

To change any of them, put a file named `config.yaml` in the volume. It is read automatically, and anything given on the command line still wins over it.

The same image is published to `ghcr.io/jaturu/omada-release-watch`.

---

# The Catalog

The catalog is published as a single Sigstore bundle. That one file carries the catalog itself, a signature over it, the signing certificate, and a transparency log proof, so nothing needs to be placed alongside it.

The tool verifies it on every run against a pinned signing identity. When verification holds, nothing is printed. When it does not, the reason is reported and queries keep working, because a catalog you changed yourself is yours to change. Fetching is the exception: when verification fails, downloading an artifact the catalog chose needs `--allow-unverified`, since the catalog picks both the URL and the hash it is checked against. Turning verification off yourself is a decision rather than a failure, so the offline workflow is unaffected.

Verification can be turned off, which is the supported way to run without network access. Doing so is reported on every run rather than silently accepted.

For details, see:

- [Catalog Verification](docs/verification.md)
- [Catalog Format](docs/catalog.md)

---

# Configuration

Omada Release Watch is configured using a YAML configuration file.

By default, the application looks for:

```text
config.yaml
```

The configuration file controls catalog location and verification, output formatting, query defaults, and fetch behavior.

For the complete configuration reference, see:

- [Configuration](docs/configuration.md)

---

# Architecture

Omada Release Watch implements an explicit acquisition workflow.

```text
Refresh (download the published catalog)
    ↓
Verify (signature and signer identity)
    ↓
Catalog
    ↓
Query
    ↓
Fetch
    ↓
Verify (SHA256)
```

Every step is something you ask for. Nothing updates itself.

For a detailed discussion of the architecture and design philosophy, see:

- [Architecture](docs/architecture.md)

---

# Command Line

Omada Release Watch provides separate command line workflows for updating the catalog, querying releases, fetching artifacts, and producing JSON output.

For complete command line documentation, see:

- [Command Line Interface](docs/cli.md)

---

# Documentation

Detailed documentation is available in the `docs/` directory:

- [Command Line Interface](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Catalog Format](docs/catalog.md)
- [Catalog Verification](docs/verification.md)
- [Fetch Workflow](docs/fetch.md)
- [Supply Chain](docs/supply-chain.md)
- [Security Status](docs/security-status.md), findings against the current release, re-scanned weekly
- [Architecture](docs/architecture.md)

---

# Testing

```bash
python -m pip install --require-hashes -r requirements-dev.txt
pytest
```

Tests substitute the network and the signature verifier rather than reaching either one, so the suite runs offline.

Linting uses the same environment:

```bash
ruff check .
```

Continuous integration runs the suite on Python 3.11 and 3.13 with a 90% branch coverage floor, lints, and audits both lockfiles against known advisories.

---

# Contributing

Contributions, bug reports, and feature suggestions are welcome.

Please include sufficient detail to reproduce issues and describe the expected behavior.

---

# License

Omada Release Watch is licensed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for the full text.


## Trademark Notice

Omada is a trademark or registered trademark of TP-Link Systems Inc.
This project is an independent open source utility intended to interact
with publicly available Omada release information. It is not affiliated
with, endorsed by, sponsored by, or approved by TP-Link Systems Inc.
