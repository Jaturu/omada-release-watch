from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import requests

from .text import is_safe_filename


class FetchError(RuntimeError):
    pass


# The largest artifact in the 6.3 line is the Windows package at about 520 MB,
# so this is roughly double the real ceiling. Nothing about a response is known
# before it is read, and it is what stops a hostile endpoint filling the disk
# before the hash is ever compared.
DEFAULT_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024


@dataclass
class FetchResult:
    """
    What was fetched, and what is known about it.

    `hash_checked` is false when the catalog carried no sha256 for this entry.
    The hash is then one this tool computed from whatever arrived, which is a
    different claim from one the signed catalog vouches for.
    """

    path: Path
    sha256: str
    downloaded: bool
    hash_checked: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def fetch_artifact(
    record: dict[str, Any],
    output_dir: str | Path,
    timeout: int = 60,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    protected: Sequence[str | Path] = (),
) -> FetchResult:
    """
    Fetch one artifact.

    If the destination already exists:
      - if catalog sha256 exists and matches, skip download
      - if catalog sha256 is missing, hash local file and skip download
      - if catalog sha256 exists and does not match, raise FetchError
    """
    download_url = str(record.get("download_url", "")).strip()
    filename = str(record.get("filename", "")).strip()
    expected_sha256 = str(record.get("sha256", "")).strip().lower()

    if not download_url:
        raise FetchError("Selected artifact does not include a download_url")

    if not filename:
        raise FetchError("Selected artifact does not include a filename")

    if not is_safe_filename(filename):
        raise FetchError(f"Refusing unsafe filename: {filename!r}")

    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    destination = destination_dir / filename

    # The catalog chooses this name. Letting it choose one of the files the
    # tool's own trust decisions read turns a fetch into a way to plant them,
    # and the result lands owned by the caller at 0644.
    reserved = {Path(item).resolve() for item in protected}

    if destination.resolve() in reserved:
        raise FetchError(
            f"Refusing to write {filename!r}: it is a file this tool reads to "
            "decide what to trust. Use a different --output-dir."
        )

    if destination.exists():
        actual_sha256 = sha256_file(destination)

        if expected_sha256 and actual_sha256 != expected_sha256:
            raise FetchError(
                "Content changed under an unchanged release identity. "
                "This artifact's version/platform/package/archive/filename "
                "match a previous catalog entry, but its content does not. "
                "Investigate before proceeding. This should not happen for "
                "a versioned, immutable release.\n"
                f"File:     {destination}\n"
                f"Expected: {expected_sha256}\n"
                f"Actual:   {actual_sha256}\n"
                "Refusing to overwrite existing file."
            )

        return FetchResult(destination, actual_sha256, False, bool(expected_sha256))

    sha256 = hashlib.sha256()

    # A predictable staging name lets anyone who can write this directory
    # pre-place a symlink and redirect the write. mkstemp cannot be guessed.
    handle, partial_name = tempfile.mkstemp(
        dir=destination_dir,
        prefix=destination.name + ".",
        suffix=".part",
    )
    partial = Path(partial_name)

    try:
        with os.fdopen(handle, "wb") as fh:
            with requests.get(download_url, stream=True, timeout=timeout) as response:
                response.raise_for_status()

                written = 0

                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue

                    written += len(chunk)

                    if written > max_bytes:
                        raise FetchError(
                            f"The download is larger than the {max_bytes} byte "
                            f"limit and was discarded.\n"
                            f"URL:      {download_url}"
                        )

                    fh.write(chunk)
                    sha256.update(chunk)

        actual_sha256 = sha256.hexdigest()

        # The signed catalog exists so this hash can be trusted. Checking it
        # only on a cache hit means the bad artifact is already on disk.
        if expected_sha256 and actual_sha256 != expected_sha256:
            partial.unlink(missing_ok=True)
            raise FetchError(
                "Downloaded content does not match the SHA256 recorded in the "
                "signed catalog. The download was discarded.\n"
                f"URL:      {download_url}\n"
                f"Expected: {expected_sha256}\n"
                f"Actual:   {actual_sha256}"
            )

        # mkstemp creates at 0600, which a downloaded artifact does not need.
        os.chmod(partial, 0o644)
        partial.replace(destination)

    except FetchError:
        partial.unlink(missing_ok=True)
        raise

    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)

        raise FetchError(f"Failed to fetch artifact: {exc}") from exc

    except OSError as exc:
        partial.unlink(missing_ok=True)

        raise FetchError(f"Failed to write artifact: {exc}") from exc

    return FetchResult(destination, actual_sha256, True, bool(expected_sha256))
