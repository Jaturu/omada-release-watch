from datetime import datetime, timezone

import pytest

from omada_release_watch.catalog import (
    CURRENT_SCHEMA,
    Catalog,
    CatalogError,
    parse_version,
    stale_days,
)


def _entry(version, kind="stable", platform="linux", package="tgz", archive="none", **extra):
    entry = {
        "source_url": "https://example.com/",
        "title": "title",
        "version": version,
        "filename": f"omada-{version}.tar.gz",
        "download_url": f"https://example.com/{version}.tar.gz",
        "kind": kind,
        "platform": platform,
        "package": package,
        "archive": archive,
    }
    entry.update(extra)
    return entry


def _catalog(entries=None, schema=CURRENT_SCHEMA, updated="2026-07-26T00:00:00Z"):
    return Catalog({
        "entries": entries or {},
        "schema": schema,
        "updated": updated,
    })


# --- parse_version -----------------------------------------------------------

def test_parse_version_plain_numeric():
    assert parse_version("6.3.0.1") == (6, 3, 0, 1)


@pytest.mark.parametrize(
    "value,expected",
    [("beta-6.3.0.1", (6, 3, 0, 1)), ("Beta 6.3.0", (6, 3, 0)), ("v5.15.24.18", (5, 15, 24, 18))],
)
def test_parse_version_ignores_non_numeric_prefix(value, expected):
    assert parse_version(value) == expected


def test_parse_version_rejects_non_numeric():
    with pytest.raises(ValueError):
        parse_version("not-a-version")


# --- construction -------------------------------------------------------------

def test_reads_entries_schema_and_updated():
    catalog = _catalog({"fp1": _entry("6.3.0.1")}, updated="2026-07-26T12:00:00Z")

    assert len(catalog.entries) == 1
    assert catalog.schema == CURRENT_SCHEMA
    assert catalog.updated == "2026-07-26T12:00:00Z"


def test_entries_must_be_an_object():
    with pytest.raises(CatalogError):
        Catalog({"entries": ["not", "a", "mapping"], "schema": CURRENT_SCHEMA})


def test_a_newer_schema_is_refused():
    """A catalog written by a newer publisher may mean fields differently, so
    reading it on trust would silently misinterpret it."""
    with pytest.raises(CatalogError) as excinfo:
        _catalog(schema=CURRENT_SCHEMA + 1)

    assert "upgrade" in str(excinfo.value).lower()


def test_an_older_schema_is_accepted():
    catalog = _catalog({"fp1": _entry("6.3.0.1")}, schema=CURRENT_SCHEMA - 1)
    assert len(catalog.entries) == 1


def test_records_reintroduces_the_fingerprint():
    catalog = _catalog({"fp1": _entry("6.3.0.1")})
    assert catalog.records()[0]["fingerprint"] == "fp1"


def test_non_mapping_entries_are_skipped():
    catalog = Catalog({"entries": {"fp1": _entry("6.3.0.1"), "junk": "nope"}, "schema": CURRENT_SCHEMA})
    assert [r["fingerprint"] for r in catalog.records()] == ["fp1"]


# --- query ---------------------------------------------------------------------

def test_query_version_prefix_matches_numeric_portion_regardless_of_label():
    catalog = _catalog({
        "fp-stable": _entry("6.3.0.1"),
        "fp-beta": _entry("Beta-6.3.0.1", kind="pre-release"),
        "fp-other": _entry("5.0.0.0"),
    })

    assert {r["fingerprint"] for r in catalog.query(version_prefix="6")} == {"fp-stable", "fp-beta"}


def test_query_filters_by_package_archive_platform_kind():
    catalog = _catalog({
        "fp-deb": _entry("1.0.0", package="deb"),
        "fp-zip": _entry("1.0.0", package="tgz", archive="zip"),
        "fp-win": _entry("1.0.0", platform="windows", package="exe"),
        "fp-beta": _entry("1.0.0", kind="pre-release"),
    })

    assert [r["fingerprint"] for r in catalog.query(package="deb")] == ["fp-deb"]
    assert [r["fingerprint"] for r in catalog.query(archive="zip")] == ["fp-zip"]
    assert [r["fingerprint"] for r in catalog.query(platform="windows")] == ["fp-win"]
    assert [r["fingerprint"] for r in catalog.query(kind="pre-release")] == ["fp-beta"]


def test_query_sorts_descending_by_version():
    catalog = _catalog({
        "old": _entry("5.0.0.0"),
        "new": _entry("6.3.0.1"),
        "mid": _entry("6.2.14.10"),
    })

    assert [r["fingerprint"] for r in catalog.query()] == ["new", "mid", "old"]


def test_query_latest_returns_every_record_tied_at_the_top_version():
    catalog = _catalog({
        "tgz": _entry("6.3.0.1", package="tgz"),
        "deb": _entry("6.3.0.1", package="deb"),
        "old": _entry("6.2.14.10"),
    })

    assert {r["fingerprint"] for r in catalog.query(latest=True)} == {"tgz", "deb"}


def test_query_latest_applies_filters_before_choosing_the_top_version():
    """A newer pre-release must not suppress the latest stable."""
    catalog = _catalog({
        "beta": _entry("6.4.0.0", kind="pre-release"),
        "stable": _entry("6.3.0.1", kind="stable"),
    })

    assert [r["fingerprint"] for r in catalog.query(latest=True, kind="stable")] == ["stable"]


def test_query_on_an_empty_catalog_returns_nothing():
    assert _catalog().query(latest=True) == []


def test_records_with_an_unparseable_version_do_not_break_sorting():
    catalog = _catalog({"good": _entry("6.3.0.1"), "bad": _entry("not-a-version")})
    assert [r["fingerprint"] for r in catalog.query()] == ["good", "bad"]


# --- the catalog is read only ---------------------------------------------------

@pytest.mark.parametrize("name", ["save", "replace", "update_entry", "load"])
def test_writing_methods_are_gone(name):
    """The catalog arrives inside a signed bundle. Writing to it would break
    the signature, so the consumer treats it as read only."""
    assert not hasattr(Catalog, name)


@pytest.mark.parametrize("schema", ["1", 1.0, None, [], {}])
def test_a_non_integer_schema_is_refused(schema):
    """The forward-compatibility guard compares with >, which a non-integer
    silently skips, letting an unreadable catalog load as if understood."""
    with pytest.raises(CatalogError):
        Catalog({"entries": {"fp1": _entry("6.3.0.1")}, "schema": schema})


# --- an old catalog is authentic, so only its date says anything --------------

def test_a_recent_catalog_is_not_stale():
    assert stale_days(
        "2026-07-28T00:00:00Z",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        max_age_days=90,
    ) is None


def test_a_catalog_past_the_age_limit_reports_its_age():
    assert stale_days(
        "2026-01-01T00:00:00Z",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        max_age_days=90,
    ) == 210


def test_the_boundary_is_not_stale():
    assert stale_days(
        "2026-05-01T00:00:00Z",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
        max_age_days=90,
    ) is None


def test_an_unreadable_date_is_not_reported_as_stale():
    """parse_updated floors anything it cannot read, which would otherwise
    make every unparseable date look thousands of years old."""
    assert stale_days("not a date", now=datetime(2026, 7, 30, tzinfo=timezone.utc)) is None
    assert stale_days(None, now=datetime(2026, 7, 30, tzinfo=timezone.utc)) is None


def test_a_future_date_is_not_stale():
    assert stale_days(
        "2027-01-01T00:00:00Z",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
    ) is None


def test_entries_cannot_be_mutated_through_the_property():
    """The class documents itself as a read-only view, and the catalog is
    inside a signed bundle, so a caller must not be able to edit it."""
    catalog = Catalog({"entries": {"fp1": _entry("6.3.0.1")}, "schema": 1})

    catalog.entries["fp2"] = _entry("9.9.9.9")
    catalog.entries.pop("fp1", None)

    assert set(catalog.entries) == {"fp1"}
    assert len(catalog.records()) == 1


def test_an_unusable_version_prefix_raises_the_catalog_error():
    """The CLI passes this straight from an argument. A library ValueError
    reaching the top level is a traceback, not an answer."""
    catalog = Catalog({"entries": {"fp1": _entry("6.3.0.1")}, "schema": 1})

    with pytest.raises(CatalogError, match="version"):
        catalog.query(version_prefix="abc")


def test_the_version_error_names_what_was_typed():
    """The label prefix is stripped before matching, so reporting the stripped
    value shows the user something they never entered."""
    with pytest.raises(ValueError, match="abc"):
        parse_version("abc")
