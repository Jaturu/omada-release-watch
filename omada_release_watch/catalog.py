from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

CURRENT_SCHEMA = 1
VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
VERSION_PREFIX_RE = re.compile(r"^\D+")


class CatalogError(ValueError):
    pass


def parse_updated(value: Any) -> datetime:
    """
    The catalog's updated date, or a floor for anything unusable.

    Ordering decisions read this, so it must never raise on whatever the
    payload happened to carry. A value it cannot read sorts oldest.
    """
    floor = datetime.min.replace(tzinfo=timezone.utc)

    if not isinstance(value, str):
        return floor

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return floor

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


DEFAULT_MAX_AGE_DAYS = 90


def stale_days(
    updated: Any,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> int | None:
    """
    How many days old the catalog is, once it is older than the limit.

    An old catalog carries a real signature, so verification says nothing
    about its age. This is the only signal available on a first acquisition,
    where there is no previous catalog to compare against.

    A date that cannot be read is not reported. `parse_updated` floors those,
    which would otherwise make every one of them look thousands of years old.
    """
    floor = datetime.min.replace(tzinfo=timezone.utc)
    signed = parse_updated(updated)

    if signed == floor:
        return None

    now = now or datetime.now(timezone.utc)
    age = (now - signed).days

    return age if age > max_age_days else None


def parse_version(value: str) -> tuple[int, ...]:
    """
    Parse a dotted numeric version, ignoring any non-numeric label prefix
    (e.g. "beta-6.3.0.1", "dev_6.3.0.1"). kind already distinguishes stable
    from pre-release, so sorting/filtering only cares about the number.
    """
    numeric = VERSION_PREFIX_RE.sub("", value.strip())

    if not VERSION_RE.match(numeric):
        raise ValueError(f"Unsupported version format: {value!r}")

    return tuple(int(part) for part in numeric.split("."))


class Catalog:
    """
    A read-only view over catalog data.

    The catalog arrives inside a signed bundle, so writing to it would break
    the signature. Reading the bundle belongs to `bundle.py`, which hands the
    parsed data here.
    """

    def __init__(self, data: dict[str, Any]):
        entries = data.get("entries", {})

        if not isinstance(entries, dict):
            raise CatalogError("Catalog does not contain a valid 'entries' object")

        schema = data.get("schema", CURRENT_SCHEMA)

        # Checked before the comparison below, which a non-integer would skip.
        if not isinstance(schema, int) or isinstance(schema, bool):
            raise CatalogError(
                f"Catalog schema must be an integer, got {type(schema).__name__}"
            )

        if schema > CURRENT_SCHEMA:
            raise CatalogError(
                f"Catalog uses schema {schema}, but this version understands "
                f"up to {CURRENT_SCHEMA}. Upgrade to read it."
            )

        self.schema = schema
        self.updated: str | None = data.get("updated")
        self._entries: dict[str, dict[str, Any]] = entries

    @property
    def entries(self) -> dict[str, dict[str, Any]]:
        """A copy. Handing out the live mapping would make a read-only view
        writable by anyone holding it."""
        return dict(self._entries)

    def records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        for fingerprint, entry in self._entries.items():
            if not isinstance(entry, dict):
                continue

            record = dict(entry)
            record.setdefault("fingerprint", fingerprint)
            records.append(record)

        return records

    def query(
        self,
        version_prefix: str | None = None,
        package: str | None = None,
        archive: str | None = None,
        platform: str | None = None,
        kind: str | None = None,
        latest: bool = False,
    ) -> list[dict[str, Any]]:
        records = self.records()

        if version_prefix:
            # Comes straight from a command line argument, so an unusable one
            # is an answer to give rather than a library error to leak.
            try:
                prefix_parts = parse_version(version_prefix)
            except ValueError as exc:
                raise CatalogError(str(exc)) from exc

            records = [
                record
                for record in records
                if self._version_parts(record)[: len(prefix_parts)] == prefix_parts
            ]

        records = self._filter_records(
            records,
            package=package,
            archive=archive,
            platform=platform,
            kind=kind,
        )

        records = self._sort_records(records)

        if latest:
            records = self._latest_release(records)

        return records

    def _version_parts(self, record: dict[str, Any]) -> tuple[int, ...]:
        version = str(record.get("version", "")).strip()
        if not version:
            return ()

        try:
            return parse_version(version)
        except ValueError:
            return ()

    def _sort_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            records,
            key=lambda record: self._version_parts(record),
            reverse=True,
        )

    def _latest_release(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not records:
            return []

        latest_version_parts = self._version_parts(records[0])

        return [
            record
            for record in records
            if self._version_parts(record) == latest_version_parts
        ]

    def _filter_records(
        self,
        records: list[dict[str, Any]],
        package: str | None = None,
        archive: str | None = None,
        platform: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        result = records

        if package:
            result = [
                item for item in result
                if str(item.get("package", "")).lower() == package.lower()
            ]

        if archive:
            result = [
                item for item in result
                if str(item.get("archive", "")).lower() == archive.lower()
            ]

        if platform:
            result = [
                item for item in result
                if str(item.get("platform", "")).lower() == platform.lower()
            ]

        if kind:
            result = [
                item for item in result
                if str(item.get("kind", "")).lower() == kind.lower()
            ]

        return result
