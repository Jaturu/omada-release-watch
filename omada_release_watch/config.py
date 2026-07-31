from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def _empty_sections() -> dict:
    return {"catalog": {}, "query": {}, "output": {}, "fetch": {}}


@dataclass
class LoadedConfig:
    """
    The parsed config and whether it may weaken a security setting.

    Both come from one open of one file. Deciding from a second lookup by name
    would let the file change in between, so the file vouched for would not be
    the file that was read.
    """

    data: dict = field(default_factory=_empty_sections)
    downgrade_reason: str | None = None


def security_downgrade_reason_for(info: os.stat_result | None) -> str | None:
    """
    Why the file this handle refers to may not weaken a security setting.

    A config file is easier to plant than a command line argument. Whoever can
    write the directory a run happens in can leave one there and have someone
    else's run read it, so a file this run cannot vouch for does not get to
    turn a check off.

    A missing handle means the check could not run, which is a reason to
    refuse rather than a reason to allow.
    """
    if info is None:
        return "its ownership and mode could not be read"

    if os.name != "posix":
        return None

    if info.st_uid not in (os.geteuid(), 0):
        return f"it is owned by uid {info.st_uid} rather than by you or root"

    if info.st_mode & 0o022:
        return "it is writable by other users, so run chmod 600 on it"

    return None


def load_config(path: str | None, default_config_file: str) -> LoadedConfig:
    config = _empty_sections()

    if not path:
        return LoadedConfig(config)

    config_path = Path(path)

    try:
        handle = os.open(config_path, os.O_RDONLY)
    except FileNotFoundError:
        if path == default_config_file:
            return LoadedConfig(config)
        raise SystemExit(f"Config file not found: {config_path}") from None
    except OSError as exc:
        raise SystemExit(f"Config file could not be opened: {config_path}: {exc}") from None

    if yaml is None:
        os.close(handle)
        raise SystemExit(
            "YAML config support requires PyYAML. Install it with: python -m pip install pyyaml"
        )

    # One open, one decision. The content and the verdict describe the same
    # file even if the name is replaced a moment later.
    with os.fdopen(handle, "r", encoding="utf-8") as fh:
        try:
            info = os.fstat(fh.fileno())
        except OSError:
            info = None

        loaded = yaml.safe_load(fh) or {}

    reason = security_downgrade_reason_for(info)

    if not isinstance(loaded, dict):
        raise SystemExit(
            f"Config file must contain a YAML mapping: {config_path}"
        )

    for section in config:
        value = loaded.get(section, {})

        if not isinstance(value, dict):
            raise SystemExit(
                f"Config section '{section}' must be a mapping."
            )

        config[section].update(value)

    return LoadedConfig(config, reason)


def config_value(
    args: argparse.Namespace,
    config: dict,
    arg_name: str,
    default=None,
    *,
    config_name: str | None = None,
):
    if config_name is None:
        config_name = arg_name

    value = getattr(args, arg_name)
    if value is not None:
        return value

    return config.get(config_name, default)


def config_bool(
    args: argparse.Namespace,
    config: dict,
    name: str,
    default: bool = False,
) -> bool:
    value = getattr(args, name)
    if value is not None:
        return bool(value)
    return bool(config.get(name, default))
