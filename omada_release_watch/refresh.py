from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, Sequence

import requests

from . import bundle
from .bundle import LoadResult, Outcome
from .catalog import parse_updated

# The crawler commits the bundle to this branch. A release asset would need
# a token even on a public repository.
DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/Jaturu/omada-release-watch"
    "/main/catalog.sigstore.json"
)


class RefreshError(RuntimeError):
    pass


# Absent on non-POSIX, where the symlink case it guards does not arise the
# same way.
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _read_by_descriptor(path: Path) -> tuple[os.stat_result, bytes]:
    """
    Read a file without letting the name be resolved twice.

    `os.stat` and `Path.read_bytes` each resolve the name again and both
    follow symlinks, so checking one and reading the other describes two
    files whenever someone can write the directory.
    """
    handle = os.open(path, os.O_RDONLY | _NO_FOLLOW)

    with os.fdopen(handle, "rb") as fh:
        return os.fstat(fh.fileno()), fh.read()


# The published catalog is tens of kilobytes. requests decodes gzip as it
# reads, so the size on the wire bounds nothing, and .content would hold the
# whole decoded body in memory before anything looked at it.
DEFAULT_MAX_CATALOG_BYTES = 8 * 1024 * 1024


def fetch_catalog_bytes(
    url: str,
    timeout: int = 30,
    max_bytes: int = DEFAULT_MAX_CATALOG_BYTES,
) -> bytes:
    body = bytearray()

    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()

        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue

            body.extend(chunk)

            if len(body) > max_bytes:
                raise RefreshError(
                    f"The catalog at {url} is larger than the {max_bytes} byte "
                    "limit. The existing catalog was not replaced."
                )

    return bytes(body)


def _refuse_rollback(
    incoming: LoadResult,
    catalog_paths: Sequence[Path],
    verify: bool,
    verifier_factory: Callable[[], object] | None = None,
) -> None:
    """
    Refuse a validly signed but older catalog.

    Signature checking cannot catch this on its own, because an old bundle is
    authentic. Only the signed date distinguishes it from the current one.

    Every catalog present is considered, not only the one being written. The
    refresh slot is empty on a first refresh, and the published copy beside it
    is what an older download would otherwise slip past.

    The verifier is forwarded, so this decision is testable with signature
    checking on rather than only with it off.
    """
    newest: str | None = None

    for catalog_path in catalog_paths:
        if not Path(catalog_path).exists():
            continue

        current = bundle.load(catalog_path, verify=verify, verifier_factory=verifier_factory)

        # A catalog that does not check out has no say in what may replace it.
        if current.data is None:
            continue

        if verify and current.outcome is not Outcome.VERIFIED:
            continue

        updated = current.data.get("updated")

        if newest is None or parse_updated(updated) > parse_updated(newest):
            newest = updated

    if newest is None:
        return

    incoming_updated = (incoming.data or {}).get("updated")

    if parse_updated(incoming_updated) < parse_updated(newest):
        raise RefreshError(
            f"The downloaded catalog is older than the one already in place "
            f"({incoming_updated} is before {newest}). "
            "The existing catalog was not replaced."
        )


def refresh(
    url: str,
    catalog_path: str | Path,
    verify: bool = True,
    download: Callable[..., bytes] | None = None,
    verifier_factory: Callable[[], object] | None = None,
    max_bytes: int = DEFAULT_MAX_CATALOG_BYTES,
    compare_against: Sequence[str | Path] = (),
) -> LoadResult:
    """
    Replace the catalog with the published one.

    A download that cannot be verified is refused and the catalog already on
    disk is left alone. That refuses new data rather than refusing to run,
    because an accepted catalog decides what the tool downloads next.
    """
    catalog_path = Path(catalog_path)

    # Resolved here rather than as a default argument, so the module attribute
    # can be substituted in a test.
    downloader = download if download is not None else fetch_catalog_bytes

    try:
        payload = downloader(url)
    except Exception as exc:
        raise RefreshError(
            f"Could not download the catalog from {url}: {exc}\n"
            "The existing catalog was not replaced."
        ) from exc

    if len(payload) > max_bytes:
        raise RefreshError(
            f"The downloaded catalog is larger than the {max_bytes} byte limit. "
            "The existing catalog was not replaced."
        )

    # Checked in memory, before anything is written. Verifying a staged file
    # and then renaming that path installs whatever occupies the path at the
    # rename, which is not necessarily what was verified.
    result = bundle.load_bytes(payload, verify=verify, verifier_factory=verifier_factory)

    acceptable = Outcome.DISABLED if not verify else Outcome.VERIFIED

    if result.outcome is not acceptable:
        raise RefreshError(
            f"The downloaded catalog was rejected ({result.outcome.value}). "
            "The existing catalog was not replaced."
            + (f"\n\n  Detail:   {result.detail}" if result.detail else "")
        )

    _refuse_rollback(
        result,
        [catalog_path, *compare_against],
        verify,
        verifier_factory,
    )

    catalog_path.parent.mkdir(parents=True, exist_ok=True)

    # A predictable staging name lets anyone who can write this directory
    # pre-place a symlink and redirect the write. mkstemp cannot be guessed.
    handle, staged_name = tempfile.mkstemp(
        dir=catalog_path.parent,
        prefix=catalog_path.name + ".",
        suffix=".part",
    )
    staged = Path(staged_name)

    try:
        with os.fdopen(handle, "wb") as staged_file:
            staged_file.write(payload)
            staged_file.flush()
            os.fsync(staged_file.fileno())

            # By descriptor, so this never resolves the name.
            # mkstemp creates at 0600, which the catalog does not need.
            os.fchmod(staged_file.fileno(), 0o644)
            written = os.fstat(staged_file.fileno())

        # Refuse a staging file that is no longer the one this run wrote.
        # Identity alone is not enough, because a freed inode number is reused
        # as soon as it is freed.
        try:
            staged_info, staged_bytes = _read_by_descriptor(staged)
        except OSError as exc:
            raise RefreshError(
                f"The staged catalog changed before it could be installed ({exc}). "
                "The existing catalog was not replaced."
            ) from exc

        if not os.path.samestat(staged_info, written) or staged_bytes != payload:
            raise RefreshError(
                "The staged catalog changed before it could be installed. "
                "The existing catalog was not replaced."
            )

        staged.replace(catalog_path)

        # The rename resolves the name one more time, so confirm what landed
        # rather than reporting success for something never checked.
        _, installed = _read_by_descriptor(catalog_path)

        if installed != payload:
            raise RefreshError(
                "The catalog changed as it was installed, so what is on disk "
                "is not what was verified."
            )
    except OSError as exc:
        raise RefreshError(f"Could not write the catalog: {exc}") from exc
    finally:
        if staged.exists():
            staged.unlink()

    return result
