# Design

This document describes the architectural goals and design decisions behind Omada Release Watch.

The project is a lightweight artifact acquisition pipeline, intentionally explicit, modular, and easy to understand.

---

# Architecture

The application is organized as a sequence of independent stages.

```text
Refresh
    ↓
Verify (signature)
    ↓
Catalog
    ↓
Query
    ↓
Fetch
    ↓
Verify (SHA256)
```

Each stage has a single responsibility.

Refreshing downloads the published catalog.

Verification establishes that the catalog came from the crawler that produces it and has not changed since.

The catalog holds release metadata and answers questions about it.

Queries operate only on the catalog.

Fetching acquires exactly one artifact.

---

# Release Discovery Is Not Part Of This Tool

Earlier versions crawled TP-Link's download pages and forum topics directly. That work now happens once, on a schedule, in a separate project, which also signs the result and publishes it. This repository is purely a consumer: it produces no catalog, crawls nothing, and never signs. The published bundle is committed here, so a clone arrives with a catalog it can verify but did not create.

The reason is load. When every user runs their own crawl, TP-Link sees one crawler per user. Moving discovery out means it sees one, regardless of how many people use this tool.

The consequence for this project is that it consumes release data rather than producing it, and the catalog becomes an input that has to be trusted deliberately rather than something the tool observed for itself. That is why the catalog is signed and why the signature is checked on every run.

---

# Module Responsibilities

| Module | Responsibility |
|----------|----------------|
| bundle.py | Read and verify the signed catalog bundle |
| refresh.py | Download a published catalog and accept it only if it checks out |
| catalog.py | Answer queries about catalog data |
| fetch.py | Download and verify exactly one artifact |
| cli.py | Command line interface and presentation |
| config.py | Configuration loading |
| text.py | Filename safety and string helpers |
| log.py | Logging |

Each module is intentionally focused on a single primary responsibility.

`catalog.py` knows nothing about files or formats. It receives parsed data and answers questions about it, which keeps querying testable without constructing a signed bundle.

---

# Explicit Workflow

The application avoids hidden side effects.

Users explicitly choose whether to:

- update the catalog
- query the catalog
- fetch an artifact

Nothing refreshes on a timer, in the background, or as a side effect of a query. A catalog is replaced when, and only when, someone asks for it.

---

# The Catalog Is Read Only

The catalog arrives inside a signed bundle, so the tool never writes to it. Recording anything back into it, such as the hash of an artifact just downloaded, would invalidate the signature and make the next run report tampering.

This is a change from earlier versions, which wrote observed hashes back into the catalog. For any entry the publisher has already hashed, nothing is lost: the recorded hash travels in the catalog and the mismatch check in `fetch.py` still refuses to overwrite a local file whose content changed.

---

# Snapshot Model

The catalog represents the current state of the release sources as of its publication.

It is intentionally not a historical archive. Anything removed upstream disappears from the catalog too.

---

# Artifact Identity

Artifacts are identified using two related concepts.

## Fingerprint

The fingerprint identifies the logical release artifact.

It intentionally excludes `download_url`.

This prevents URL changes from creating duplicate catalog entries.

## SHA256

SHA256 identifies the downloaded artifact itself.

Once available, SHA256 becomes the authoritative artifact identity.

Download URLs are considered metadata.

---

# Design Principles

## Explicit over implicit

Commands perform only the actions requested.

## Simple configuration

Configuration should remain small and easy to understand.

## Human first

Console output is designed for humans.

JSON output is available for automation, and diagnostic notices are kept off standard output so it stays parseable.

## Modular architecture

Each module should have one primary responsibility, and a seam that cannot be tested without network access or an elaborate fake is treated as a design problem rather than an inconvenience.

## Report rather than refuse

A catalog that fails verification is reported loudly and still answers queries. Someone who modifies their own catalog is entitled to, and the requirement is that they know. The exception is accepting new data: a download that fails verification is refused outright.

---

# Deferred Ideas

The following ideas have been intentionally postponed.

- Published checksum discovery
- Accepting more than one pinned signing identity, which a change to the crawler workflow's name or branch would require

Artifact mirroring was considered during development but intentionally abandoned in favor of on-demand acquisition.
