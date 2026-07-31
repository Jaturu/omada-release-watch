# Configuration

Omada Release Watch is configured using a YAML configuration file.

By default, the application looks for:

```text
config.yaml
```

`config.yaml` is local runtime state, not a tracked project file. Copy `config.example.yaml` to `config.yaml` to get started. If `config.yaml` doesn't exist, the application falls back to built-in defaults rather than failing.

Command line options always override values defined in the configuration file.

---

# Configuration Sections

```yaml
catalog:
query:
output:
fetch:
```

---

# Catalog

Controls where the catalog lives and whether its signature is checked.

```yaml
catalog:
  dir: .
  verify: true
```

| Option | Description |
|----------|-------------|
| `dir` | Directory to read the catalog from. Defaults to the working directory. |
| `verify` | Check the catalog signature on every run. Enabled by default. |
| `max_age_days` | Report a catalog older than this many days. Defaults to 90. |

`verify: false` is honoured only from a configuration file this run can vouch for. A file owned by another user, or one that other users can write, cannot turn verification off, and the run reports that it ignored the setting. Use `--verify false` on the command line instead. The reason is that a configuration file is just a file in a directory, so anyone able to write that directory could otherwise disable verification for someone else's run.

The file is opened once, and both the settings and that judgement come from the same open. Looking the name up a second time to judge it would let the file be replaced in between, so the file judged would not be the file read.

`max_age_days` exists because an old catalog is authentically signed, so verification establishes nothing about its age. On a first acquisition there is no previous catalog to compare against, and a replayed but genuine one would install cleanly. The age is reported rather than refused, since the catalog is only republished when it changes and a quiet period is normal.

The directory holds up to two files. `catalog.sigstore.json` is the published copy, which arrives by clone. `catalog-refresh.sigstore.json` is written only by `--refresh`, and in a container it is the only copy there is. Whichever carries the newer signed `updated` date answers queries, and a copy that passes verification is preferred over one that does not.

The directory is only ever read. `--refresh` always writes `catalog-refresh.sigstore.json` into the working directory, so pointing `dir` somewhere else means a refreshed catalog has to be moved into place by hand. The command says so when that happens.

Setting `verify: false` is the supported way to run without network access, since verification may contact the transparency infrastructure. It is reported on every run rather than silently accepted. See [Catalog Verification](verification.md).

---

# Query

Provides default query behavior.

```yaml
query:
  latest: false
  version_prefix:
  platform:
  package:
  archive:
  kind:
```

These values behave exactly like their command line counterparts.

Command line options always override configuration values.

---

# Output

Controls presentation.

```yaml
output:
  json: false
  progress: true
```

| Option | Description |
|----------|-------------|
| `json` | Emit JSON instead of formatted console output. |
| `progress` | Display progress messages. JSON output automatically suppresses them. |

---

# Fetch

The fetch section controls artifact download behavior.

```yaml
fetch:
  output_dir: downloads
```

| Option | Description |
|----------|-------------|
| `output_dir` | Directory used for downloaded artifacts. |

