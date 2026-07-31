from __future__ import annotations

import re

# An allowlist, because a denylist of separators leaves through drive-relative
# Windows paths, control characters and terminal escapes. The set is what the
# vendor actually uses: names carry spaces, parentheses and brackets.
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\[\]+-]{0,254}$")


def is_safe_filename(value: str) -> bool:
    """
    Whether a catalog-supplied name may be written to disk.

    Leading dots are refused, which covers "." and ".." and keeps a catalog
    from naming a dotfile.
    """
    return bool(value) and bool(SAFE_FILENAME_RE.match(value))
