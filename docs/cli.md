# Command Line Interface

This document describes common command line workflows and available options for Omada Release Watch.

---

# Overview

Omada Release Watch separates updating the catalog, querying, fetching, and output formatting into explicit operations.

Nothing updates itself. Queries read the catalog already on disk, and only `--refresh` and `--fetch` reach the network.

---

# Command Workflows

## Status

Running with no options reports where the catalog is, when it was published, how many entries it holds, and whether it verified. It touches nothing.

```bash
./omada-release-watch.py
```

---

## Refresh

Download the published catalog, verify it, and write it to the catalog path.

```bash
./omada-release-watch.py --refresh
```

A download that fails verification is refused and the existing catalog is left in place. See [Catalog Verification](verification.md).

---

## Query

Query the catalog without contacting TP-Link.

```bash
./omada-release-watch.py --latest
```

```bash
./omada-release-watch.py --version-prefix 6.2
```

---

## Refresh then Query

Update the catalog before querying it.

```bash
./omada-release-watch.py --refresh --latest
```

---

## Fetch

Fetch exactly one artifact.

The selected filters must resolve to a single catalog entry.

`--kind` is always required, even if the other filters already resolve to exactly one artifact. This is intentional: fetch always requires an explicit choice between `stable` and `pre-release`.

Typical usage:

```bash
./omada-release-watch.py \
  --fetch \
  --latest \
  --kind stable \
  --platform linux \
  --package tgz
```

---

## JSON Output

Emit machine-readable JSON. Under `--json`, stdout is only JSON: queries print an object carrying `verification`, `signer` and `records` (an empty array when nothing matches), status prints an object carrying the tool version, a fetch prints the record it fetched, and errors print an `{"error": ...}` object. Progress messages are always suppressed.

Every document names what the catalog verified as, so a caller reading stdout alone can tell a verified run from one that was not. `verification` holds the outcome, and `signer` the identity that signed it, or `null`. A fetch document adds `sha256_verified`, which is `false` when the catalog recorded no hash for that entry and the digest was therefore compared with nothing.

```bash
./omada-release-watch.py --latest --json
```

The human-readable notice also goes to standard error rather than standard output, so piping stdout into a JSON parser stays safe even when the catalog reports a problem.

---

# Filtering Results

Filters may be combined to narrow matching artifacts.

```bash
./omada-release-watch.py --latest --platform linux
```

```bash
./omada-release-watch.py --latest --package deb
```

```bash
./omada-release-watch.py --latest --archive zip
```

```bash
./omada-release-watch.py --latest --kind pre-release
```

---

# Command Line Reference

## Catalog Options

| Option | Description |
|----------|-------------|
| `--version` | Show the version and exit. |
| `--config` | YAML configuration file. |
| `--catalog-dir` | Directory to read the catalog from. Read-only, never written to. |
| `--refresh` | Download the published catalog before doing anything else. |
| `--verify true\|false` | Verify the catalog signature. Enabled by default. |
| `--max-age-days` | Report a catalog older than this many days. Defaults to 90. |

---

## Query Options

| Option | Description |
|----------|-------------|
| `--latest` | Show artifacts for the newest matching release. |
| `--version-prefix` | Filter by version prefix. |
| `--platform` | Filter by platform. |
| `--package` | Filter by package type. |
| `--archive` | Filter by archive wrapper. |
| `--kind` | Filter by release type. |

---

## Fetch Options

| Option | Description |
|----------|-------------|
| `--fetch` | Download the selected artifact. |
| `--output-dir` | Directory used for downloaded artifacts. |
| `--allow-unverified` | Fetch even when the catalog did not verify. |

Fetch requires enough filters to identify exactly one artifact.

A fetch is refused when verification failed, because the catalog chooses both the URL that gets downloaded and the hash it is checked against. `--allow-unverified` proceeds anyway. It has no configuration file equivalent, deliberately: a file is easier to leave in a directory than an argument is to type.

Turning verification off with `--verify false` is not a failure. It is an instruction, so fetching still works and needs no second flag.

---

## Output Options

| Option | Description |
|----------|-------------|
| `--json` | Emit JSON output. |
| `--progress true\|false` | Enable or disable progress messages. |

---

# Exit Codes

The exit code is the same in text and `--json` mode. It is a separate channel from stdout, so `--json` output stays valid JSON regardless of the code.

| Code | Meaning |
|------|---------|
| `0` | The command did what was asked. |
| `1` | A query returned no matches, the catalog is missing or unreadable, a refresh was refused, or a fetch was refused or could not complete. |
| `2` | Invalid command line usage, from the argument parser. |

A failed verification is not by itself an error code. The tool reports it and continues, so a catalog you modified yourself still answers queries. Two things are refused rather than reported. A failed `--refresh` exits `1`, because refusing to accept new data is a different matter from refusing to run. A `--fetch` exits `1` when verification failed, unless `--allow-unverified` is passed, because a fetch acts on the catalog rather than reporting it. Verification switched off is a decision rather than a failure, so it does not trigger this.

---

# Recipes

## Show the latest Linux release

```bash
./omada-release-watch.py --latest --platform linux
```

## Download the latest stable Linux tarball

```bash
./omada-release-watch.py \
  --fetch \
  --latest \
  --kind stable \
  --platform linux \
  --package tgz
```

## Produce JSON output, ignoring the human-readable notice

```bash
./omada-release-watch.py --latest --json 2>/dev/null
```

The `verification` field in the document itself still reports the outcome, so discarding standard error does not discard the answer.

## Update the catalog and download the latest pre-release Linux tarball

```bash
./omada-release-watch.py \
  --refresh \
  --fetch \
  --latest \
  --kind pre-release \
  --platform linux \
  --package tgz
```

## Run without network access

```bash
./omada-release-watch.py --verify false --latest
```
