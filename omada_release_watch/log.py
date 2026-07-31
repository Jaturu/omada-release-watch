from __future__ import annotations

import sys
import time


def progress(level: str, message: str, enabled: bool = True) -> None:
    if not enabled:
        return

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] [{level}] {message}", file=sys.stderr)
