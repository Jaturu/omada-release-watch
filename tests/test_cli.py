import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from omada_release_watch import bundle
from omada_release_watch.bundle import EXPECTED_IDENTITIES, LoadResult, Outcome
from omada_release_watch.catalog import Catalog
from omada_release_watch.cli import (
    PUBLISHED_CATALOG,
    REFRESHED_CATALOG,
    describe_outcome,
    fetch_selected_artifact,
    main,
    parse_bool,
    print_status,
    query_catalog,
    query_requested,
    read_version,
    resolve_catalog_dir,
    resolve_fetch_output_dir,
    resolve_query_options,
    resolve_verify,
    select_catalog,
)
from omada_release_watch.fetch import FetchResult
from omada_release_watch.refresh import DEFAULT_CATALOG_URL


def _catalog_data(version="6.3.0.1"):
    return {
        "entries": {
            "fp1": {
                "version": version,
                "filename": f"Omada_v{version}_linux_x64.deb",
                "download_url": f"https://static.tp-link.invalid/{version}.deb",
                "source_url": "https://example.com/",
                "title": "t",
                "kind": "stable",
                "platform": "linux",
                "package": "deb",
                "archive": "none",
            }
        },
        "schema": 1,
        "updated": "2026-07-26T00:00:00Z",
    }


def _write_bundle(path, data):
    """A bundle-shaped file. Enough for the paths that do not verify."""
    catalog_text = json.dumps(data)
    statement = {
        "_type": bundle.STATEMENT_TYPE,
        "predicateType": bundle.PREDICATE_TYPE,
        "subject": [{"name": "catalog.json", "digest": {"sha256": bundle.sha256_hex(catalog_text)}}],
        "predicate": {"catalog": catalog_text},
    }
    payload = base64.b64encode(json.dumps(statement).encode()).decode()
    path.write_text(json.dumps({"dsseEnvelope": {"payload": payload}}))
    return path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "omada-release-watch.py"


# --- parse_bool -----------------------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "y", "on"])
def test_parse_bool_truthy(value):
    assert parse_bool(value) is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "n", "off"])
def test_parse_bool_falsy(value):
    assert parse_bool(value) is False


def test_parse_bool_invalid_raises():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_bool("maybe")


# --- resolve_query_options / query_requested -------------------------------------

def _args(**overrides):
    defaults = dict(
        version_prefix=None, package=None, archive=None, platform=None, kind=None, latest=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_query_requested_false_when_nothing_set():
    options = resolve_query_options(_args(), {})
    assert query_requested(options) is False


def test_query_requested_true_when_latest_set():
    options = resolve_query_options(_args(latest=True), {})
    assert query_requested(options) is True


def test_query_requested_true_when_config_sets_a_filter():
    options = resolve_query_options(_args(), {"platform": "linux"})
    assert query_requested(options) is True


# --- resolve_catalog_dir / resolve_fetch_output_dir ------------------------------

def test_resolve_catalog_dir_cli_override():
    args = argparse.Namespace(catalog_dir="/somewhere/else")
    assert resolve_catalog_dir(args, {}) == Path("/somewhere/else")


def test_resolve_catalog_dir_config_override():
    args = argparse.Namespace(catalog_dir=None)
    assert resolve_catalog_dir(args, {"dir": "/from/config"}) == Path("/from/config")


def test_resolve_catalog_dir_default_is_the_working_directory():
    args = argparse.Namespace(catalog_dir=None)
    assert resolve_catalog_dir(args, {}) == Path(".")


# --- catalog selection ------------------------------------------------------------

def _dated_bundle(path, updated, version="6.3.0.1"):
    data = _catalog_data(version)
    data["updated"] = updated
    return _write_bundle(path, data)


def test_the_refreshed_copy_wins_when_it_is_newer(tmp_path):
    _dated_bundle(tmp_path / PUBLISHED_CATALOG, "2026-07-01T00:00:00Z")
    _dated_bundle(tmp_path / REFRESHED_CATALOG, "2026-07-20T00:00:00Z")

    _, _, path = select_catalog(tmp_path, verify=False)
    assert path.name == REFRESHED_CATALOG


def test_the_published_copy_wins_when_it_is_newer(tmp_path):
    """Pulled but not refreshed. A git pull has to actually take effect."""
    _dated_bundle(tmp_path / PUBLISHED_CATALOG, "2026-07-20T00:00:00Z")
    _dated_bundle(tmp_path / REFRESHED_CATALOG, "2026-07-01T00:00:00Z")

    _, _, path = select_catalog(tmp_path, verify=False)
    assert path.name == PUBLISHED_CATALOG


def test_a_verified_copy_beats_a_newer_one_that_fails_verification(tmp_path, monkeypatch):
    """A corrupt refreshed copy must not poison the run when a good one is present."""
    _dated_bundle(tmp_path / PUBLISHED_CATALOG, "2026-07-01T00:00:00Z")
    _dated_bundle(tmp_path / REFRESHED_CATALOG, "2026-07-20T00:00:00Z")

    real_load = bundle.load

    def fake_load(path, verify=True, **kwargs):
        loaded = real_load(path, verify=False)
        outcome = Outcome.ALTERED if Path(path).name == REFRESHED_CATALOG else Outcome.VERIFIED
        return LoadResult(outcome, data=loaded.data)

    monkeypatch.setattr("omada_release_watch.cli.bundle.load", fake_load)

    _, result, path = select_catalog(tmp_path, verify=True)
    assert path.name == PUBLISHED_CATALOG
    assert result.outcome is Outcome.VERIFIED


def test_a_single_copy_is_used_whichever_one_it_is(tmp_path):
    _dated_bundle(tmp_path / REFRESHED_CATALOG, "2026-07-01T00:00:00Z")

    catalog, _, path = select_catalog(tmp_path, verify=False)
    assert path.name == REFRESHED_CATALOG
    assert catalog is not None


def test_a_catalog_without_an_updated_date_sorts_oldest(tmp_path):
    undated = _catalog_data()
    undated.pop("updated")
    _write_bundle(tmp_path / REFRESHED_CATALOG, undated)
    _dated_bundle(tmp_path / PUBLISHED_CATALOG, "2026-07-01T00:00:00Z")

    _, _, path = select_catalog(tmp_path, verify=False)
    assert path.name == PUBLISHED_CATALOG


@pytest.mark.parametrize("updated", [20260726, {"at": "now"}, ["2026-07-26"], True])
def test_a_non_string_updated_field_does_not_crash_selection(tmp_path, updated):
    """Catalog.updated is whatever the payload carried. Ordering must treat a
    value it cannot parse as undated rather than raising."""
    hostile = _catalog_data()
    hostile["updated"] = updated
    _write_bundle(tmp_path / REFRESHED_CATALOG, hostile)
    _dated_bundle(tmp_path / PUBLISHED_CATALOG, "2026-07-01T00:00:00Z")

    _, _, path = select_catalog(tmp_path, verify=False)
    assert path.name == PUBLISHED_CATALOG


def test_two_undated_catalogs_prefer_the_published_one(tmp_path):
    for name in (PUBLISHED_CATALOG, REFRESHED_CATALOG):
        undated = _catalog_data()
        undated.pop("updated")
        _write_bundle(tmp_path / name, undated)

    _, _, path = select_catalog(tmp_path, verify=False)
    assert path.name == PUBLISHED_CATALOG


def test_an_identical_date_prefers_the_published_one(tmp_path):
    _dated_bundle(tmp_path / PUBLISHED_CATALOG, "2026-07-20T00:00:00Z")
    _dated_bundle(tmp_path / REFRESHED_CATALOG, "2026-07-20T00:00:00Z")

    _, _, path = select_catalog(tmp_path, verify=False)
    assert path.name == PUBLISHED_CATALOG


def test_an_empty_directory_reports_missing_and_names_what_it_looked_for(tmp_path):
    catalog, result, path = select_catalog(tmp_path, verify=False)

    assert catalog is None
    assert result.outcome is Outcome.MISSING

    notice = describe_outcome(result, path)
    assert PUBLISHED_CATALOG in notice
    assert REFRESHED_CATALOG in notice
    assert "--refresh" in notice
    assert "--catalog-dir" in notice


def test_resolve_fetch_output_dir_default():
    args = argparse.Namespace(output_dir=None)
    assert resolve_fetch_output_dir(args, {}) == Path("downloads")


def test_resolve_fetch_output_dir_config_override():
    args = argparse.Namespace(output_dir=None)
    assert resolve_fetch_output_dir(args, {"output_dir": "custom-dir"}) == Path("custom-dir")


# --- fetch_selected_artifact ------------------------------------------------------

def _finding_record(fingerprint="fp1", **overrides):
    record = {
        "fingerprint": fingerprint,
        "version": "6.2.14.10",
        "filename": "a.tar.gz",
        "download_url": "https://x/a.tar.gz",
        "kind": "stable",
        "platform": "linux",
        "package": "tgz",
        "archive": "none",
    }
    record.update(overrides)
    return record


def test_fetch_selected_artifact_no_matches(tmp_path, capsys):
    result = fetch_selected_artifact([], tmp_path, json_output=False, load_result=_result(Outcome.VERIFIED))
    assert result == 1
    assert "No matching Omada artifact" in capsys.readouterr().out


def test_fetch_selected_artifact_multiple_matches(tmp_path, capsys):
    records = [_finding_record("fp1"), _finding_record("fp2")]
    result = fetch_selected_artifact(records, tmp_path, json_output=False, load_result=_result(Outcome.VERIFIED))
    assert result == 1
    assert "matched multiple artifacts" in capsys.readouterr().out


def test_fetch_selected_artifact_missing_fingerprint(tmp_path, capsys):
    record = _finding_record()
    del record["fingerprint"]
    result = fetch_selected_artifact([record], tmp_path, json_output=False, load_result=_result(Outcome.VERIFIED))
    assert result == 1
    assert "does not include a fingerprint" in capsys.readouterr().out


def test_fetch_selected_artifact_reports_the_hash_it_computed(tmp_path, monkeypatch, capsys):
    def fake_fetch_artifact(record, output_dir, **kwargs):
        return FetchResult(tmp_path / "a.tar.gz", "deadbeef" * 8, True, True)

    monkeypatch.setattr("omada_release_watch.cli.fetch_artifact", fake_fetch_artifact)

    result = fetch_selected_artifact([_finding_record()], tmp_path, json_output=False, load_result=_result(Outcome.VERIFIED))

    assert result == 0
    assert "deadbeef" * 8 in capsys.readouterr().out


def test_fetch_does_not_write_to_the_catalog(tmp_path, monkeypatch):
    """The catalog is inside a signed bundle. Writing the observed hash back
    would break the signature and the next run would report tampering."""
    bundle_path = _write_bundle(tmp_path / "catalog.sigstore.json", _catalog_data())
    before = bundle_path.read_bytes()

    def fake_fetch_artifact(record, output_dir, **kwargs):
        return FetchResult(tmp_path / "a.deb", "deadbeef" * 8, True, True)

    monkeypatch.setattr("omada_release_watch.cli.fetch_artifact", fake_fetch_artifact)
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)), "--catalog-dir", str(tmp_path),
        "--verify", "false", "--fetch", "--kind", "stable", "--latest",
    ])

    assert main() == 0
    assert bundle_path.read_bytes() == before


# --- main() --json contract: stdout is ONLY JSON, no preamble ---------------------

def _json_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("query: {}\n")
    return config_path


def _seeded_catalog(tmp_path, version="6.2.14.10"):
    """Writes the published copy and returns the directory holding it."""
    _write_bundle(tmp_path / PUBLISHED_CATALOG, _catalog_data(version))
    return tmp_path


def test_main_json_query_emits_only_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "orw",
        "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(_seeded_catalog(tmp_path)), "--verify", "false",
        "--latest", "--kind", "stable", "--version-prefix", "6",
        "--platform", "linux", "--package", "deb", "--json",
    ])

    rc = main()
    out = capsys.readouterr().out

    assert rc == 0
    payload = json.loads(out)  # pure JSON or this raises
    assert payload["verification"] == "disabled"
    assert payload["records"][0]["version"] == "6.2.14.10"


def test_main_json_fetch_emits_only_json(tmp_path, monkeypatch, capsys):
    def fake_fetch_artifact(record, output_dir, **kwargs):
        return FetchResult(tmp_path / "downloaded.deb", "deadbeef" * 8, True, True)

    monkeypatch.setattr("omada_release_watch.cli.fetch_artifact", fake_fetch_artifact)
    monkeypatch.setattr(sys, "argv", [
        "orw",
        "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(_seeded_catalog(tmp_path)), "--verify", "false",
        "--fetch", "--kind", "stable", "--version-prefix", "6.2.14.10",
        "--platform", "linux", "--package", "deb", "--json",
    ])

    rc = main()
    out = capsys.readouterr().out

    assert rc == 0
    json.loads(out)  # pure JSON or this raises


# --- missing catalog is reported as such, not as an empty query result -----------

def test_query_against_missing_catalog_reports_the_catalog(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "orw",
        "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path / "absent"), "--verify", "false",
        "--latest",
    ])

    rc = main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "No catalog found" in out
    assert "No matching Omada records found." not in out


def test_missing_catalog_under_json_is_an_error_object(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "orw",
        "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path / "absent"), "--verify", "false",
        "--latest", "--json",
    ])

    rc = main()

    assert rc == 1
    assert "No catalog found" in json.loads(capsys.readouterr().out)["error"]


# --- bare invocation prints status and touches nothing ---------------------------

# --- errors under --json are JSON too: {"error": ...} on stdout, exit non-zero ----

# --- every JSON document says what the catalog verified as ------------------------
#
# stdout is the only channel automation reads. A recipe that discards stderr is
# documented, so a result that omits the outcome cannot be told apart from a
# verified one.

def test_query_json_carries_the_verification_outcome(capsys):
    catalog = Catalog(_catalog_data())

    rc = query_catalog(
        catalog,
        query_options=_query_options(latest=True),
        json_output=True,
        load_result=_result(Outcome.ALTERED),
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["verification"] == "altered"
    assert payload["records"][0]["version"] == "6.3.0.1"


def test_query_json_names_the_signer_when_there_is_one(capsys):
    query_catalog(
        Catalog(_catalog_data()),
        query_options=_query_options(latest=True),
        json_output=True,
        load_result=_result(Outcome.VERIFIED, signer=EXPECTED_IDENTITIES[0]),
    )

    assert json.loads(capsys.readouterr().out)["signer"] == EXPECTED_IDENTITIES[0]


def test_fetch_json_carries_the_verification_outcome(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omada_release_watch.cli.fetch_artifact",
        lambda record, output_dir, **kwargs: FetchResult(tmp_path / "a.tar.gz", "ab" * 32, True, True),
    )

    fetch_selected_artifact(
        [_finding_record()],
        tmp_path,
        json_output=True,
        load_result=_result(Outcome.VERIFIED, signer=EXPECTED_IDENTITIES[0]),
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["verification"] == "verified"
    assert payload["signer"] == EXPECTED_IDENTITIES[0]


# --- fetching from a catalog that did not verify ----------------------------------

def test_fetch_refuses_a_catalog_that_did_not_verify(tmp_path, capsys):
    """A query answers from data the user can judge. A fetch acts on it, by
    downloading whatever URL that data names."""
    rc = fetch_selected_artifact(
        [_finding_record()],
        tmp_path,
        json_output=False,
        load_result=_result(Outcome.ALTERED),
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert "--allow-unverified" in out
    assert not (tmp_path / "a.tar.gz").exists()


def test_fetch_proceeds_from_an_unverified_catalog_when_asked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omada_release_watch.cli.fetch_artifact",
        lambda record, output_dir, **kwargs: FetchResult(tmp_path / "a.tar.gz", "ab" * 32, True, True),
    )

    rc = fetch_selected_artifact(
        [_finding_record()],
        tmp_path,
        json_output=False,
        load_result=_result(Outcome.DISABLED),
        allow_unverified=True,
    )

    assert rc == 0
    assert "Fetched Omada artifact" in capsys.readouterr().out


def test_fetch_refusal_under_json_is_an_error_object(tmp_path, capsys):
    rc = fetch_selected_artifact(
        [_finding_record()],
        tmp_path,
        json_output=True,
        load_result=_result(Outcome.UNEXPECTED_SIGNER),
    )

    assert rc == 1
    assert "unexpected-signer" in json.loads(capsys.readouterr().out)["error"]


def test_fetch_selected_artifact_no_matches_json_is_error_object(tmp_path, capsys):
    rc = fetch_selected_artifact([], tmp_path, json_output=True, load_result=_result(Outcome.VERIFIED))
    assert rc == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "No matching Omada artifact found to fetch."
    }


def test_fetch_selected_artifact_multiple_matches_json_is_error_object(tmp_path, capsys):
    records = [{"fingerprint": "a"}, {"fingerprint": "b"}]
    rc = fetch_selected_artifact(records, tmp_path, json_output=True, load_result=_result(Outcome.VERIFIED))
    assert rc == 1
    assert "multiple artifacts" in json.loads(capsys.readouterr().out)["error"]


def test_main_json_fetch_without_kind_emits_error_object(tmp_path, monkeypatch, capsys):
    """Under --json an error is still JSON on stdout ({"error": ...}), exit 1.
    A human line here would break a json.loads consumer."""
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(_seeded_catalog(tmp_path)), "--verify", "false",
        "--fetch", "--version-prefix", "6", "--platform", "linux",
        "--package", "deb", "--json",
    ])

    rc = main()
    assert rc == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "Fetch requires --kind or query.kind in config.yaml."
    }


# --- end-to-end via the real CLI (subprocess) -------------------------------------

def _write_catalog(path, entries):
    _write_bundle(path, {"entries": entries, "schema": 1, "updated": "2026-07-04T00:00:00Z"})


def _run_cli(tmp_path, args):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("query: {}\n")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config_path), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_fetch_requires_kind_even_when_already_unique(tmp_path):
    """Intentional design, confirmed: --kind is always required for --fetch,
    even when other filters already resolve to exactly one artifact."""
    catalog_path = tmp_path / PUBLISHED_CATALOG
    _write_catalog(catalog_path, {
        "fp1": {
            "version": "6.2.14.10", "platform": "linux", "package": "tgz",
            "archive": "none", "kind": "stable", "filename": "a.tar.gz",
            "download_url": "https://static.tp-link.invalid/a.tar.gz",
            "source_url": "https://example.com/", "title": "t",
        }
    })

    result = _run_cli(tmp_path, [
        "--progress", "false", "--verify", "false",
        "--fetch", "--version-prefix", "6.2.14.10", "--platform", "linux", "--package", "tgz",
    ])
    assert result.returncode == 1
    assert "Fetch requires --kind" in result.stdout


def test_cli_fetch_refuses_path_traversal_filename_end_to_end(tmp_path):
    """SECURITY, defense in depth: even a hand-edited/poisoned catalog.json
    cannot make the real CLI write outside the output directory."""
    catalog_path = tmp_path / PUBLISHED_CATALOG
    _write_catalog(catalog_path, {
        "fp1": {
            "version": "6.2.14.10", "platform": "linux", "package": "tgz",
            "archive": "none", "kind": "stable",
            "filename": "../../evil.txt",
            "download_url": "https://attacker.invalid/payload",
            "source_url": "https://example.com/", "title": "t",
        }
    })

    result = _run_cli(tmp_path, [
        "--progress", "false", "--verify", "false",
        "--fetch", "--kind", "stable", "--latest",
    ])
    assert result.returncode == 1
    assert "unsafe filename" in result.stdout
    assert not (tmp_path.parent / "evil.txt").exists()


def test_cli_latest_returns_both_kinds_at_same_version(tmp_path):
    """Regression: _latest_release used to compare raw version strings, so
    a stable/pre-release pair at the same numeric version wouldn't both
    surface under --latest."""
    catalog_path = tmp_path / PUBLISHED_CATALOG
    _write_catalog(catalog_path, {
        "fp-stable": {
            "version": "6.3.0.1", "platform": "linux", "package": "tgz",
            "archive": "none", "kind": "stable", "filename": "stable.tar.gz",
            "download_url": "https://x/stable.tar.gz",
            "source_url": "https://example.com/", "title": "t",
        },
        "fp-beta": {
            "version": "Beta-6.3.0.1", "platform": "linux", "package": "tgz",
            "archive": "none", "kind": "pre-release", "filename": "beta.tar.gz",
            "download_url": "https://x/beta.tar.gz",
            "source_url": "https://example.com/", "title": "t",
        },
    })

    result = _run_cli(tmp_path, ["--progress", "false", "--verify", "false", "--latest", "--json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert {r["fingerprint"] for r in payload["records"]} == {"fp-stable", "fp-beta"}


def test_cli_json_and_progress_true_rejected(tmp_path):
    """--json and an explicit --progress true are contradictory: --json
    always implies no progress output. --progress false stays allowed
    (see test_cli_latest_returns_both_kinds_at_same_version), only an
    explicit true conflicts."""
    catalog_path = tmp_path / PUBLISHED_CATALOG
    _write_catalog(catalog_path, {})

    result = _run_cli(tmp_path, [
        "--progress", "true", "--latest", "--json",
    ])
    assert result.returncode == 2
    assert "mutually exclusive" in result.stderr


# --- verification outcomes reaching the user -------------------------------------

def _result(outcome, **kw):
    from omada_release_watch.bundle import LoadResult
    return LoadResult(outcome, **kw)


def _query_options(**overrides):
    options = {
        "version_prefix": None,
        "package": None,
        "archive": None,
        "platform": None,
        "kind": None,
        "latest": False,
    }
    options.update(overrides)
    return options


def test_a_verified_catalog_says_nothing():
    """Signing and verification are meant to be invisible when they hold."""
    assert describe_outcome(_result(Outcome.VERIFIED), Path("c.json")) is None


def test_an_altered_catalog_says_what_happened_and_what_to_do():
    text = describe_outcome(_result(Outcome.ALTERED, detail="boom"), Path("c.json"))

    assert "does not match" in text
    assert "modified after it was published" in text
    assert "--refresh" in text
    assert "verify: false" in text
    assert "boom" in text


def test_an_altered_catalog_mentions_the_clock():
    """Certificate windows and log timestamps are time sensitive, so a wrong
    clock fails verification in a way that looks exactly like tampering."""
    assert "clock" in describe_outcome(_result(Outcome.ALTERED), Path("c.json")).lower()


def test_an_unexpected_signer_names_both_identities():
    text = describe_outcome(
        _result(Outcome.UNEXPECTED_SIGNER, signer="https://github.com/someone/else.yml@refs/heads/main"),
        Path("c.json"),
    )

    assert "someone/else.yml" in text
    assert EXPECTED_IDENTITIES[0] in text
    assert "do not rely on it" in text.lower()


def test_disabled_verification_is_reported_every_run():
    """A silent off switch would let one person disable verification on
    another person's behalf."""
    text = describe_outcome(_result(Outcome.DISABLED), Path("c.json"))
    assert text is not None
    assert "not verified" in text.lower()


def test_unverifiable_is_not_worded_as_a_failure():
    """Not knowing whether a catalog is good is not the same as knowing it is bad."""
    text = describe_outcome(_result(Outcome.UNVERIFIABLE, detail="no network"), Path("c.json"))
    assert "could not" in text.lower()
    assert "modified" not in text.lower()


def test_a_missing_catalog_names_the_path():
    text = describe_outcome(_result(Outcome.MISSING), Path("/somewhere/c.json"))
    assert "/somewhere/c.json" in text


def test_a_malformed_catalog_is_distinct_from_a_missing_one():
    missing = describe_outcome(_result(Outcome.MISSING), Path("c.json"))
    malformed = describe_outcome(_result(Outcome.MALFORMED, detail="not a bundle"), Path("c.json"))
    assert missing != malformed
    assert "not a bundle" in malformed


# --- warnings go to stderr so --json stdout stays parseable -----------------------

def test_a_warning_never_contaminates_json_stdout(tmp_path, monkeypatch, capsys):
    _write_bundle(tmp_path / "catalog.sigstore.json", _catalog_data())
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path),
        "--verify", "false", "--latest", "--json",
    ])

    rc = main()
    captured = capsys.readouterr()

    assert rc == 0
    json.loads(captured.out)          # stdout stays pure JSON
    assert "not verified" in captured.err.lower()


def test_an_altered_catalog_still_answers_the_query(tmp_path, monkeypatch, capsys):
    """A failed signature warns, it does not stop the tool."""
    _write_bundle(tmp_path / "catalog.sigstore.json", _catalog_data())
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path),
        "--verify", "false", "--latest",
    ])

    assert main() == 0
    assert "6.3.0.1" in capsys.readouterr().out


# --- status reports the verdict ---------------------------------------------------

def test_status_reports_the_verification_verdict(tmp_path, capsys):
    catalog = Catalog(_catalog_data())
    rc = print_status(catalog, Path("c.json"), _result(Outcome.DISABLED), json_output=False)

    assert rc == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_status_json_includes_the_outcome(tmp_path, capsys):
    catalog = Catalog(_catalog_data())
    print_status(catalog, Path("c.json"), _result(Outcome.VERIFIED), json_output=True)

    assert json.loads(capsys.readouterr().out)["verification"] == "verified"


# --- --refresh -------------------------------------------------------------------

def _stub_download(monkeypatch, payload=None, error=None):
    def download(url, timeout=30):
        if error is not None:
            raise error
        return payload
    monkeypatch.setattr("omada_release_watch.refresh.fetch_catalog_bytes", download)


def test_refresh_adds_a_newer_copy_that_then_answers_the_query(tmp_path, monkeypatch, capsys):
    """Refresh does not overwrite the published copy. It writes alongside it
    and wins the query by being newer."""
    published = tmp_path / PUBLISHED_CATALOG
    _dated_bundle(published, "2026-07-01T00:00:00Z", version="6.2.14.10")
    before = published.read_bytes()

    downloaded = _catalog_data("6.3.0.1")
    downloaded["updated"] = "2026-07-20T00:00:00Z"
    _stub_download(monkeypatch, _write_bundle(tmp_path / "new.json", downloaded).read_bytes())

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--verify", "false", "--refresh", "--latest", "--json",
    ])

    assert main() == 0
    assert json.loads(capsys.readouterr().out)["records"][0]["version"] == "6.3.0.1"
    assert published.read_bytes() == before


def test_a_failed_refresh_exits_non_zero_and_says_why(tmp_path, monkeypatch, capsys):
    target = tmp_path / "catalog.sigstore.json"
    _write_bundle(target, _catalog_data("6.2.14.10"))
    _stub_download(monkeypatch, error=OSError("connection refused"))

    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)), "--catalog-dir", str(tmp_path),
        "--verify", "false", "--refresh",
    ])

    assert main() == 1
    assert "not replaced" in capsys.readouterr().out


def test_a_failed_refresh_still_leaves_the_old_catalog_queryable(tmp_path, monkeypatch, capsys):
    target = tmp_path / "catalog.sigstore.json"
    _write_bundle(target, _catalog_data("6.2.14.10"))
    _stub_download(monkeypatch, error=OSError("connection refused"))

    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)), "--catalog-dir", str(tmp_path),
        "--verify", "false", "--latest", "--json",
    ])

    assert main() == 0
    assert json.loads(capsys.readouterr().out)["records"][0]["version"] == "6.2.14.10"


def test_refresh_always_uses_the_published_url(tmp_path, monkeypatch):
    """Where the catalog comes from is not a user setting. A config that names
    a url is ignored rather than honored."""
    seen = []

    def download(url, timeout=30):
        seen.append(url)
        return _write_bundle(tmp_path / "new.json", _catalog_data()).read_bytes()

    monkeypatch.setattr("omada_release_watch.refresh.fetch_catalog_bytes", download)
    config = tmp_path / "config.yaml"
    config.write_text("catalog:\n  url: https://example.invalid/mine.json\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(config), "--verify", "false", "--refresh",
    ])

    main()
    assert seen == [DEFAULT_CATALOG_URL]


def _stub_bundle_download(tmp_path, data=None):
    payload = _write_bundle(tmp_path / "downloaded.json", data or _catalog_data()).read_bytes()

    def download(url, timeout=30):
        return payload

    return download


def test_refresh_writes_only_the_refreshed_copy(tmp_path, monkeypatch):
    """The published copy is tracked in git. A refresh must never touch it."""
    published = _dated_bundle(tmp_path / PUBLISHED_CATALOG, "2026-07-01T00:00:00Z")
    before = published.read_bytes()

    monkeypatch.setattr(
        "omada_release_watch.refresh.fetch_catalog_bytes", _stub_bundle_download(tmp_path)
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["orw", "--verify", "false", "--refresh"])

    assert main() == 0
    assert published.read_bytes() == before
    assert (tmp_path / REFRESHED_CATALOG).exists()


def test_a_failed_refresh_leaves_the_previous_refreshed_copy_alone(tmp_path, monkeypatch):
    existing = _dated_bundle(tmp_path / REFRESHED_CATALOG, "2026-07-01T00:00:00Z")
    before = existing.read_bytes()

    def download(url, timeout=30):
        return b"not a bundle"

    monkeypatch.setattr("omada_release_watch.refresh.fetch_catalog_bytes", download)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["orw", "--verify", "false", "--refresh"])

    assert main() == 1
    assert existing.read_bytes() == before


def test_refresh_with_a_catalog_dir_elsewhere_asks_the_user_to_move_it(
    tmp_path, monkeypatch, capsys
):
    """An override is read-only, so refresh lands in the working directory and
    says so rather than writing somewhere it may not be allowed to."""
    work = tmp_path / "work"
    work.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _dated_bundle(elsewhere / PUBLISHED_CATALOG, "2026-07-01T00:00:00Z")

    monkeypatch.setattr(
        "omada_release_watch.refresh.fetch_catalog_bytes", _stub_bundle_download(tmp_path)
    )
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "argv", [
        "orw", "--catalog-dir", str(elsewhere), "--verify", "false", "--refresh",
    ])

    assert main() == 0

    err = capsys.readouterr().err
    assert "must be updated manually" in err
    assert (work / REFRESHED_CATALOG).exists()
    assert not (elsewhere / REFRESHED_CATALOG).exists()


# --- a planted config file cannot turn verification off ---------------------------

def _verify_args(verify=None, config="config.yaml"):
    return argparse.Namespace(verify=verify, config=config)


def test_config_verify_false_is_honoured_from_a_trustworthy_file(tmp_path, capsys):
    assert resolve_verify(_verify_args(config="config.yaml"), {"verify": False}, None) is False
    assert capsys.readouterr().err == ""


def test_config_verify_false_is_ignored_from_a_world_writable_file(tmp_path, capsys):
    """The notice is the point. Silently ignoring it would be its own surprise."""
    assert resolve_verify(
        _verify_args(config="config.yaml"),
        {"verify": False},
        "it is writable by other users, so run chmod 600 on it",
    ) is True
    assert "writable by other users" in capsys.readouterr().err


def test_the_command_line_can_always_turn_verification_off(tmp_path, capsys):
    """An argument is not something a third party can leave lying around."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o666)

    args = _verify_args(verify=False, config="config.yaml")
    assert resolve_verify(args, {"verify": False}, "it is writable by other users") is False
    assert capsys.readouterr().err == ""


def test_an_untrustworthy_config_does_not_disturb_verification_left_on(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {}\n")
    config_path.chmod(0o666)

    assert resolve_verify(_verify_args(config="config.yaml"), {}, "it is writable") is True
    assert capsys.readouterr().err == ""


def test_a_planted_config_cannot_disable_verification_end_to_end(tmp_path):
    catalog_path = tmp_path / PUBLISHED_CATALOG
    _write_catalog(catalog_path, {
        "fp1": {
            "version": "9.9.9.9", "platform": "linux", "package": "tgz",
            "archive": "none", "kind": "stable", "filename": "planted.tar.gz",
            "download_url": "https://attacker.invalid/planted.tar.gz",
            "source_url": "https://example.com/", "title": "t",
        }
    })
    # Left in the directory the run happens in, and picked up by default.
    planted = tmp_path / "config.yaml"
    planted.write_text("catalog: {verify: false}\n")
    planted.chmod(0o666)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--progress", "false", "--latest"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )

    assert "verify: false" in result.stderr
    assert "9.9.9.9" not in result.stdout
    assert result.returncode == 1


# --- an entry the catalog cannot vouch for ----------------------------------------

def test_fetch_says_when_the_catalog_recorded_no_hash(tmp_path, monkeypatch, capsys):
    """Otherwise a hash this tool computed from what arrived reads exactly like
    one the signed catalog vouched for."""
    monkeypatch.setattr(
        "omada_release_watch.cli.fetch_artifact",
        lambda record, output_dir, **kwargs: FetchResult(tmp_path / "a.tar.gz", "ab" * 32, True, False),
    )

    rc = fetch_selected_artifact(
        [_finding_record()], tmp_path, json_output=False,
        load_result=_result(Outcome.VERIFIED),
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "compared with" in out


def test_fetch_json_reports_whether_the_hash_was_checked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omada_release_watch.cli.fetch_artifact",
        lambda record, output_dir, **kwargs: FetchResult(tmp_path / "a.tar.gz", "ab" * 32, True, False),
    )

    fetch_selected_artifact(
        [_finding_record()], tmp_path, json_output=True,
        load_result=_result(Outcome.VERIFIED),
    )

    assert json.loads(capsys.readouterr().out)["sha256_verified"] is False


def test_fetch_says_nothing_extra_when_the_hash_was_checked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "omada_release_watch.cli.fetch_artifact",
        lambda record, output_dir, **kwargs: FetchResult(tmp_path / "a.tar.gz", "ab" * 32, True, True),
    )

    fetch_selected_artifact(
        [_finding_record()], tmp_path, json_output=False,
        load_result=_result(Outcome.VERIFIED),
    )

    assert "compared with" not in capsys.readouterr().out


# --- an unusually old catalog is worth saying out loud ----------------------------

def test_a_stale_catalog_is_reported_on_stderr(tmp_path, monkeypatch, capsys):
    """An old catalog is authentically signed, so verification says nothing
    about it. On a first acquisition there is nothing else to compare against."""
    old = _catalog_data()
    old["updated"] = "2020-01-01T00:00:00Z"
    _write_bundle(tmp_path / PUBLISHED_CATALOG, old)

    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path), "--verify", "false", "--latest",
    ])

    main()

    assert "older than" in capsys.readouterr().err


def test_a_current_catalog_says_nothing_about_its_age(tmp_path, monkeypatch, capsys):
    fresh = _catalog_data()
    fresh["updated"] = datetime.now(timezone.utc).isoformat()
    _write_bundle(tmp_path / PUBLISHED_CATALOG, fresh)

    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path), "--verify", "false", "--latest",
    ])

    main()

    assert "older than" not in capsys.readouterr().err


def test_refresh_compares_against_the_published_copy_too(tmp_path, monkeypatch, capsys):
    """The refresh slot is empty on a first refresh, so comparing only against
    it would let an older published catalog land unchallenged."""
    newer = _catalog_data()
    newer["updated"] = "2026-07-20T00:00:00Z"
    _write_bundle(tmp_path / PUBLISHED_CATALOG, newer)

    older = _catalog_data()
    older["updated"] = "2026-07-01T00:00:00Z"

    seen = {}

    def fake_refresh(url, target, verify=True, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr("omada_release_watch.cli.refresh_module.refresh", fake_refresh)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path), "--verify", "false", "--refresh", "--latest",
    ])

    with pytest.raises(RuntimeError):
        main()

    assert tmp_path / PUBLISHED_CATALOG in [Path(p) for p in seen["compare_against"]]


def test_the_unexpected_signer_message_names_every_accepted_identity(monkeypatch):
    """During a transition more than one identity is pinned. Naming one of
    them tells the reader the wrong thing about what would be accepted."""
    both = (
        "https://github.com/x/.github/workflows/old.yml@refs/heads/main",
        "https://github.com/x/.github/workflows/new.yml@refs/heads/main",
    )
    monkeypatch.setattr("omada_release_watch.cli.EXPECTED_IDENTITIES", both)

    text = describe_outcome(
        _result(Outcome.UNEXPECTED_SIGNER, signer="https://github.com/someone/else.yml"),
        Path("c.json"),
    )

    assert both[0] in text
    assert both[1] in text


def test_an_unusable_version_prefix_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    _write_bundle(tmp_path / PUBLISHED_CATALOG, _catalog_data())
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path), "--verify", "false",
        "--version-prefix", "abc",
    ])

    rc = main()

    assert rc == 1
    assert "version" in capsys.readouterr().out.lower()


def test_an_unusable_version_prefix_under_json_stays_json(tmp_path, monkeypatch, capsys):
    """stdout is documented as only ever JSON under --json. A traceback
    leaves it empty, which is the one thing a parser cannot handle."""
    _write_bundle(tmp_path / PUBLISHED_CATALOG, _catalog_data())
    monkeypatch.setattr(sys, "argv", [
        "orw", "--config", str(_json_config(tmp_path)),
        "--catalog-dir", str(tmp_path), "--verify", "false",
        "--version-prefix", "abc", "--json",
    ])

    rc = main()

    assert rc == 1
    assert "version" in json.loads(capsys.readouterr().out)["error"].lower()


# --- the tool can say what it is --------------------------------------------------

def test_version_is_read_from_the_version_file():
    """The image tag is built from this file, so the flag and the tag have to
    come from the same place or they drift apart."""
    assert read_version() == (REPO_ROOT / "VERSION").read_text().strip()


def test_version_flag_prints_it_and_exits(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--version"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0
    assert (REPO_ROOT / "VERSION").read_text().strip() in result.stdout


def test_status_json_carries_the_version(tmp_path, capsys):
    catalog = Catalog(_catalog_data())
    print_status(catalog, Path("c.json"), _result(Outcome.VERIFIED), json_output=True)

    assert json.loads(capsys.readouterr().out)["version"] == read_version()


# --- the exit code contract scripts branch on -------------------------------------

def test_no_matches_exits_one_in_text_mode(capsys):
    rc = query_catalog(
        Catalog(_catalog_data()),
        query_options=_query_options(version_prefix="9.9"),
        json_output=False,
        load_result=_result(Outcome.VERIFIED),
    )

    assert rc == 1
    assert "No matching" in capsys.readouterr().out


def test_no_matches_exits_one_under_json_with_an_empty_array(capsys):
    rc = query_catalog(
        Catalog(_catalog_data()),
        query_options=_query_options(version_prefix="9.9"),
        json_output=True,
        load_result=_result(Outcome.VERIFIED),
    )

    assert rc == 1
    assert json.loads(capsys.readouterr().out)["records"] == []


def test_matches_exit_zero(capsys):
    rc = query_catalog(
        Catalog(_catalog_data()),
        query_options=_query_options(latest=True),
        json_output=False,
        load_result=_result(Outcome.VERIFIED),
    )

    assert rc == 0


# --- turning verification off is a choice, not a failure --------------------------

def test_fetch_proceeds_when_verification_was_turned_off(tmp_path, monkeypatch, capsys):
    """--verify false is the documented offline path. Refusing afterwards
    second-guesses an instruction the caller just gave."""
    monkeypatch.setattr(
        "omada_release_watch.cli.fetch_artifact",
        lambda record, output_dir, **kwargs: FetchResult(tmp_path / "a.tar.gz", "ab" * 32, True, True),
    )

    rc = fetch_selected_artifact(
        [_finding_record()], tmp_path, json_output=False,
        load_result=_result(Outcome.DISABLED),
    )

    assert rc == 0
    assert "Fetched Omada artifact" in capsys.readouterr().out


@pytest.mark.parametrize("outcome", [
    Outcome.ALTERED,
    Outcome.UNEXPECTED_SIGNER,
    Outcome.MALFORMED,
    Outcome.UNVERIFIABLE,
])
def test_fetch_still_refuses_when_verification_failed(tmp_path, capsys, outcome):
    """These mean something went wrong, which is different from being told
    not to look."""
    rc = fetch_selected_artifact(
        [_finding_record()], tmp_path, json_output=False,
        load_result=_result(outcome),
    )

    assert rc == 1
    assert "--allow-unverified" in capsys.readouterr().out


def test_the_image_ships_what_the_version_flag_reads():
    """read_version resolves VERSION next to the package, so the Dockerfile
    has to copy it or the container reports a plausible-looking "unknown"."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()

    assert "COPY VERSION" in dockerfile
