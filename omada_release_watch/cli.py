from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from omada_release_watch import bundle
from omada_release_watch import refresh as refresh_module
from omada_release_watch.bundle import EXPECTED_IDENTITIES, LoadResult, Outcome
from omada_release_watch.catalog import (
    DEFAULT_MAX_AGE_DAYS,
    Catalog,
    CatalogError,
    parse_updated,
    stale_days,
)
from omada_release_watch.config import config_bool, config_value, load_config
from omada_release_watch.fetch import FetchError, fetch_artifact
from omada_release_watch.log import progress
from omada_release_watch.refresh import DEFAULT_CATALOG_URL, RefreshError

# The published copy arrives by clone or image, the refreshed one only ever
# comes from --refresh. Selection compares them, it never writes the first.
PUBLISHED_CATALOG = "catalog.sigstore.json"
REFRESHED_CATALOG = "catalog-refresh.sigstore.json"

# Verified is the normal case. Disabled means the caller turned checking off,
# which is a decision rather than a failure.
FETCHABLE_OUTCOMES = (Outcome.VERIFIED, Outcome.DISABLED)

DEFAULT_CATALOG_DIR = "."
DEFAULT_CONFIG_FILE = "config.yaml"

# The release workflow builds the image tag from this same file.
VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def read_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "y", "on"}:
        return True

    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise argparse.ArgumentTypeError(
        "Expected one of: true, false, yes, no, 1, 0, on, off"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omada-release-watch",
        description=(
            "Query a signed catalog of TP-Link Omada Software "
            "Controller releases and fetch artifacts from it."
        ),
        epilog="""Examples:
  Show the latest release
    omada-release-watch --latest

  Update the catalog
    omada-release-watch --refresh

  Fetch one artifact
    omada-release-watch --fetch --kind pre-release --latest --platform linux --package tgz

  Emit JSON
    omada-release-watch --latest --json

Configuration:
  config.yaml
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"omada-release-watch {read_version()}",
        help="Show the version and exit.",
    )

    catalog_options = parser.add_argument_group("Catalog Options")
    catalog_options.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILE,
        help="YAML configuration file.",
    )
    catalog_options.add_argument(
        "--catalog-dir",
        default=None,
        help="Directory to read the catalog from.",
    )
    catalog_options.add_argument(
        "--refresh",
        action="store_true",
        default=None,
        help="Download the published catalog before doing anything else.",
    )
    catalog_options.add_argument(
        "--verify",
        type=parse_bool,
        default=None,
        help="Verify the catalog signature. Enabled by default.",
    )
    catalog_options.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Report a catalog older than this many days.",
    )

    query_options = parser.add_argument_group("Query Options")
    query_options.add_argument(
        "--latest",
        action="store_true",
        default=None,
        help="Show artifacts for the newest matching release.",
    )
    query_options.add_argument(
        "--version-prefix",
        default=None,
        help="Filter by version prefix.",
    )
    query_options.add_argument(
        "--package",
        default=None,
        help="Filter by package type.",
    )
    query_options.add_argument(
        "--archive",
        default=None,
        help="Filter by archive wrapper.",
    )
    query_options.add_argument(
        "--platform",
        default=None,
        help="Filter by target platform.",
    )
    query_options.add_argument(
        "--kind",
        default=None,
        help="Filter by release kind.",
    )

    fetch_options = parser.add_argument_group("Fetch Options")
    fetch_options.add_argument(
        "--fetch",
        action="store_true",
        default=None,
        help="Download exactly one matching artifact.",
    )
    fetch_options.add_argument(
        "--output-dir",
        default=None,
        help="Directory for fetched artifacts.",
    )
    # Command line only, deliberately. A config file is easier to plant than an
    # argument, and this one decides whether an unverified catalog may pick a
    # download.
    fetch_options.add_argument(
        "--allow-unverified",
        action="store_true",
        default=False,
        help="Fetch even when the catalog did not verify.",
    )

    output_options = parser.add_argument_group("Output Options")
    output_options.add_argument(
        "--json",
        action="store_true",
        default=None,
        help="Emit JSON output.",
    )
    output_options.add_argument(
        "--progress",
        type=parse_bool,
        default=None,
        help="Enable or disable progress output.",
    )

    return parser

def resolve_catalog_dir(
    args: argparse.Namespace,
    catalog_cfg: dict[str, Any],
) -> Path:
    state_value = config_value(
        args,
        catalog_cfg,
        "catalog_dir",
        DEFAULT_CATALOG_DIR,
        config_name="dir",
    )
    return Path(state_value)


def resolve_verify(
    args: argparse.Namespace,
    catalog_cfg: dict[str, Any],
    downgrade_reason: str | None = None,
) -> bool:
    """
    Whether to check the catalog signature.

    A command line argument is something the person running the tool typed. A
    config file is a file in a directory, so one that this run cannot vouch
    for does not get to turn verification off on their behalf.

    The reason comes from the same open that read the file, so it describes
    the file the settings actually came from.
    """
    verify = config_bool(args, catalog_cfg, "verify", True)

    if verify or args.verify is not None:
        return verify

    if downgrade_reason is None:
        return False

    print(
        f"Ignoring verify: false in {args.config}, because {downgrade_reason}.\n"
        "Verification stays on. Pass --verify false to turn it off.",
        file=sys.stderr,
    )
    return True


def resolve_query_options(
    args: argparse.Namespace,
    query_cfg: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version_prefix": config_value(args, query_cfg, "version_prefix", None),
        "package": config_value(args, query_cfg, "package", None),
        "archive": config_value(args, query_cfg, "archive", None),
        "platform": config_value(args, query_cfg, "platform", None),
        "kind": config_value(args, query_cfg, "kind", None),
        "latest": config_bool(args, query_cfg, "latest", False),
    }


def query_requested(query_options: dict[str, Any]) -> bool:
    return any(
        [
            query_options["version_prefix"],
            query_options["package"],
            query_options["archive"],
            query_options["platform"],
            query_options["kind"],
            query_options["latest"],
        ]
    )


def print_records(records: list[dict[str, Any]]) -> None:
    for item in records:
        print()
        print(f"Version:  {item.get('version', 'unknown')}")
        print(f"Kind:     {item.get('kind', 'unknown')}")
        print(f"Platform: {item.get('platform', 'unknown')}")
        print(f"Package:  {item.get('package', 'unknown')}")
        print(f"Archive:  {item.get('archive', 'unknown')}")
        print(f"File:     {item.get('filename', 'unknown')}")
        print(f"Page:     {item.get('source_url', 'unknown')}")
        print(f"Download: {item.get('download_url', 'unknown')}")


CLOCK_HINT = "If the catalog is untouched, check this machine's clock."


def _expected_identities() -> str:
    """Every accepted identity, not just the first. During a transition there
    is more than one, and naming one of them tells a misleading story."""
    separator = "\n" + " " * 12
    return separator.join(EXPECTED_IDENTITIES)


def _detail(result: LoadResult) -> str:
    return f"\n\n  Detail:   {result.detail}" if result.detail else ""


def describe_outcome(result: LoadResult, catalog_path: Path) -> str | None:
    """
    Render a verification outcome for the user.

    Returns None when there is nothing to say. The library's own error text is
    never the message, only detail underneath one this project controls, so
    the wording does not change when the library does.
    """
    if result.outcome is Outcome.VERIFIED:
        return None

    if result.outcome is Outcome.MISSING:
        return (
            f"No catalog found in {catalog_path}.\n"
            f"Looked for {PUBLISHED_CATALOG} and {REFRESHED_CATALOG}.\n"
            "Run --refresh to download one, or use --catalog-dir to point at a\n"
            "directory that has one."
        )

    if result.outcome is Outcome.MALFORMED:
        return (
            f"The catalog at {catalog_path} could not be read.\n\n"
            "  Meaning:  The file is not a signed catalog bundle.\n"
            "  Fix:      Download it again, or check that --catalog-dir points\n"
            "            at the right directory."
            f"{_detail(result)}"
        )

    if result.outcome is Outcome.ALTERED:
        return (
            "Catalog signature does not match its contents.\n\n"
            f"  Catalog:  {catalog_path}\n"
            "  Meaning:  The catalog was modified after it was published.\n"
            "  Fix:      Run --refresh to download a fresh copy, or set\n"
            "            verify: false in config.yaml to run without\n"
            f"            verification. {CLOCK_HINT}"
            f"{_detail(result)}"
        )

    if result.outcome is Outcome.UNEXPECTED_SIGNER:
        return (
            "Catalog was signed by an unexpected identity.\n\n"
            f"  Expected: {_expected_identities()}\n"
            f"  Found:    {result.signer or 'unknown'}\n"
            "  Meaning:  This catalog was published by someone other than\n"
            "            this project.\n"
            "  Fix:      Do not rely on it. Run --refresh to download from\n"
            "            the official release."
            f"{_detail(result)}"
        )

    if result.outcome is Outcome.UNVERIFIABLE:
        return (
            "Catalog could not be verified.\n\n"
            f"  Catalog:  {catalog_path}\n"
            "  Meaning:  Verification could not run, so nothing is known\n"
            "            either way about this catalog.\n"
            f"  Fix:      Check network access. {CLOCK_HINT}"
            f"{_detail(result)}"
        )

    return (
        f"Catalog is not verified: signature checking is turned off.\n"
        f"  Catalog:  {catalog_path}"
    )


def load_catalog(catalog_path: Path, verify: bool) -> tuple[Catalog | None, LoadResult]:
    result = bundle.load(catalog_path, verify=verify)

    if result.data is None:
        return None, result

    try:
        return Catalog(result.data), result
    except CatalogError as exc:
        return None, LoadResult(Outcome.MALFORMED, detail=str(exc))


def select_catalog(
    catalog_dir: Path,
    verify: bool,
) -> tuple[Catalog | None, LoadResult, Path]:
    """
    Pick the catalog to answer from.

    Verified beats unverified, then newer beats older, and the published copy
    wins a tie. Ordering reads the date out of the signed payload rather than
    the filename, so an unsigned name cannot pin the tool to a stale catalog.
    """
    catalog_dir = Path(catalog_dir)
    candidates = []

    for name in (PUBLISHED_CATALOG, REFRESHED_CATALOG):
        path = catalog_dir / name

        if not path.exists():
            continue

        catalog, result = load_catalog(path, verify=verify)
        candidates.append((catalog, result, path))

    if not candidates:
        # Resolved so the message names a real place, not a bare dot.
        return None, LoadResult(Outcome.MISSING), catalog_dir.resolve()

    def rank(candidate: tuple[Catalog | None, LoadResult, Path]) -> tuple[bool, datetime]:
        catalog, result, _ = candidate
        return (
            result.outcome is Outcome.VERIFIED,
            parse_updated(catalog.updated if catalog else None),
        )

    return max(candidates, key=rank)


def print_status(
    catalog: Catalog,
    catalog_path: Path,
    result: LoadResult,
    json_output: bool,
) -> int:
    if json_output:
        print(json.dumps({
            "version": read_version(),
            "catalog": str(catalog_path),
            "verification": result.outcome.value,
            "signer": result.signer,
            "updated": catalog.updated,
            "entries": len(catalog.entries),
        }, indent=2))
        return 0

    print("Omada Release Watch")
    print()
    print(f"  Catalog:  {catalog_path}")
    print(f"  Verified: {result.outcome.value}")
    print(f"  Updated:  {catalog.updated or 'unknown'}")
    print(f"  Entries:  {len(catalog.entries)}")
    print()
    print("Common commands:")
    print("  omada-release-watch --latest")
    print("  omada-release-watch --fetch --latest --kind stable --platform linux --package tgz")
    print()
    print("Run --help for the full reference.")
    return 0


def query_catalog(
    catalog: Catalog,
    query_options: dict[str, Any],
    json_output: bool,
    load_result: LoadResult,
) -> int:
    matches = catalog.query(**query_options)

    if json_output:
        # Verification notices go to stderr, and the documented recipes discard
        # it. Carrying the outcome here is what lets a caller act on it.
        print(json.dumps({
            "verification": load_result.outcome.value,
            "signer": load_result.signer,
            "records": matches,
        }, indent=2))
        return 0 if matches else 1

    if not matches:
        print("No matching Omada records found.")
        return 1

    if query_options["latest"]:
        print("Latest matching Omada release artifacts:")
    elif query_options["version_prefix"]:
        print(
            f"Found {len(matches)} Omada record(s) "
            f"for version prefix {query_options['version_prefix']}:"
        )
    else:
        print(f"Found {len(matches)} matching Omada record(s):")

    print_records(matches)
    return 0


def resolve_fetch_output_dir(
    args: argparse.Namespace,
    fetch_cfg: dict[str, Any],
) -> Path:
    value = config_value(args, fetch_cfg, "output_dir", "downloads")
    return Path(value)


def emit_error(message: str, json_output: bool) -> None:
    # Under --json, stdout stays valid JSON: an {"error": ...} object, not a
    # human line. The non-zero exit code is separate (the process's signal).
    if json_output:
        print(json.dumps({"error": message}))
    else:
        print(message)


def fetch_selected_artifact(
    records: list[dict[str, Any]],
    output_dir: Path,
    json_output: bool,
    load_result: LoadResult,
    allow_unverified: bool = False,
    protected: Sequence[str | Path] = (),
) -> int:
    # Answering a query hands the user data to judge. Fetching acts on it, by
    # downloading the URL that data names and trusting the hash beside it.
    #
    # Verification being switched off is the caller's own instruction, not a
    # failure, and it is the documented way to run offline. Only an outcome
    # that says something went wrong is refused.
    if load_result.outcome not in FETCHABLE_OUTCOMES and not allow_unverified:
        emit_error(
            f"Refusing to fetch from a catalog whose verification failed "
            f"({load_result.outcome.value}).\n"
            "The catalog decides which URL is downloaded and which hash it is\n"
            "checked against, so a catalog that did not check out chooses both.\n"
            "Run --refresh for a good copy, or pass --allow-unverified to\n"
            "fetch anyway.",
            json_output,
        )
        return 1

    if len(records) == 0:
        emit_error("No matching Omada artifact found to fetch.", json_output)
        return 1

    if len(records) > 1:
        emit_error(
            "Fetch query matched multiple artifacts. "
            "Narrow the query until exactly one artifact matches.",
            json_output,
        )
        return 1

    record = records[0]

    if not str(record.get("fingerprint", "")).strip():
        emit_error("Selected artifact does not include a fingerprint.", json_output)
        return 1

    try:
        fetched = fetch_artifact(record, output_dir, protected=protected)
    except FetchError as exc:
        emit_error(str(exc), json_output)
        return 1

    result = {
        **record,
        "sha256": fetched.sha256,
        "sha256_verified": fetched.hash_checked,
        "path": str(fetched.path),
        "downloaded": fetched.downloaded,
        "verification": load_result.outcome.value,
        "signer": load_result.signer,
    }

    if json_output:
        print(json.dumps(result, indent=2))
        return 0

    if fetched.downloaded:
        print("Fetched Omada artifact:")
    else:
        print("Omada artifact already exists:")
    print()
    print(f"Version:  {record.get('version', 'unknown')}")
    print(f"Kind:     {record.get('kind', 'unknown')}")
    print(f"Platform: {record.get('platform', 'unknown')}")
    print(f"Package:  {record.get('package', 'unknown')}")
    print(f"Archive:  {record.get('archive', 'unknown')}")
    print(f"File:     {record.get('filename', 'unknown')}")
    print(f"Path:     {fetched.path}")
    print(f"SHA256:   {fetched.sha256}")
    print(f"Downloaded: {str(fetched.downloaded).lower()}")
    print(f"Download: {record.get('download_url', 'unknown')}")

    # Saying nothing here would present a hash this tool computed from what
    # arrived as though the signed catalog had vouched for it.
    if not fetched.hash_checked:
        print()
        print(
            "Note: the catalog records no SHA256 for this artifact, so the\n"
            "hash above is of whatever was received and was compared with\n"
            "nothing."
        )

    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.json and args.progress:
        parser.error("--json and --progress true are mutually exclusive: --json always disables progress output")

    loaded = load_config(args.config, DEFAULT_CONFIG_FILE)
    config = loaded.data

    catalog_cfg = config["catalog"]
    query_cfg = config["query"]
    output_cfg = config["output"]
    fetch_cfg = config["fetch"]

    json_output = config_bool(args, output_cfg, "json", False)
    progress_enabled = config_bool(args, output_cfg, "progress", True)
    log_progress = progress_enabled and not json_output

    progress("START", "Omada release watch started", log_progress)

    catalog_dir = resolve_catalog_dir(args, catalog_cfg)
    progress("INFO", f"Using catalog directory: {catalog_dir}", log_progress)

    verify = resolve_verify(args, catalog_cfg, loaded.downgrade_reason)

    if args.refresh:
        url = DEFAULT_CATALOG_URL
        progress("INFO", f"Refreshing catalog from {url}", log_progress)

        refresh_target = Path(REFRESHED_CATALOG)

        try:
            refresh_module.refresh(
                url,
                refresh_target,
                verify=verify,
                # The refresh slot is empty on a first refresh. The published
                # copy is what an older download would otherwise slip past.
                compare_against=[
                    catalog_dir / PUBLISHED_CATALOG,
                    catalog_dir / REFRESHED_CATALOG,
                ],
            )
        except RefreshError as exc:
            emit_error(str(exc), json_output)
            return 1

        # The catalog directory is read-only, so a refresh cannot land there.
        if catalog_dir.resolve() != Path.cwd().resolve():
            print(
                f"Fetched and verified a new catalog, but --catalog-dir is set to\n"
                f"{catalog_dir}.\n"
                f"The new catalog is at {refresh_target} and must be updated manually.",
                file=sys.stderr,
            )

    catalog, result, catalog_path = select_catalog(catalog_dir, verify=verify)

    notice = describe_outcome(result, catalog_path)

    if catalog is None:
        emit_error(notice or "Catalog could not be read.", json_output)
        return 1

    # Warnings go to stderr so --json keeps stdout parseable.
    if notice:
        print(notice, file=sys.stderr)

    max_age = int(config_value(args, catalog_cfg, "max_age_days", DEFAULT_MAX_AGE_DAYS))
    age = stale_days(catalog.updated, max_age_days=max_age)

    if age is not None:
        print(
            f"This catalog is {age} days old, older than the {max_age} day limit.\n"
            "An old catalog carries a real signature, so verification says\n"
            "nothing about its age. Run --refresh, and treat a catalog that\n"
            "stays old as a sign it is not reaching the published one.",
            file=sys.stderr,
        )

    query_options = resolve_query_options(args, query_cfg)

    if query_requested(query_options) or args.fetch:
        try:
            matches = catalog.query(**query_options)
        except CatalogError as exc:
            emit_error(str(exc), json_output)
            return 1

        if args.fetch:
            if not query_options["kind"]:
                emit_error("Fetch requires --kind or query.kind in config.yaml.", json_output)
                return 1

            output_dir = resolve_fetch_output_dir(args, fetch_cfg)
            return fetch_selected_artifact(
                matches,
                output_dir=output_dir,
                json_output=json_output,
                load_result=result,
                allow_unverified=args.allow_unverified,
                # A catalog names the file it is written to, so it must not be
                # able to name the ones this tool reads to decide what to trust.
                protected=[
                    Path(args.config),
                    catalog_dir / PUBLISHED_CATALOG,
                    catalog_dir / REFRESHED_CATALOG,
                    Path(REFRESHED_CATALOG),
                ],
            )

        return query_catalog(
            catalog,
            query_options=query_options,
            json_output=json_output,
            load_result=result,
        )

    return print_status(catalog, catalog_path, result, json_output)
