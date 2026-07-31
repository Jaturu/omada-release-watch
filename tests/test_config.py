import argparse
import os

import pytest

from omada_release_watch.config import (
    config_bool,
    config_value,
    load_config,
    security_downgrade_reason_for,
)


def test_load_config_no_path_returns_empty_defaults():
    config = load_config(None, "config.yaml").data
    assert config == {
        "catalog": {},
        "query": {},
        "output": {},
        "fetch": {},
    }


def test_load_config_missing_default_file_returns_empty_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config("config.yaml", "config.yaml").data
    assert config["fetch"] == {}


def test_load_config_missing_explicit_file_raises(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(SystemExit):
        load_config(str(missing), "config.yaml")


def test_load_config_merges_sections(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "fetch:\n"
        "  output_dir: elsewhere\n"
        "output:\n"
        "  json: true\n"
    )
    config = load_config(str(config_file), "config.yaml").data
    assert config["fetch"] == {"output_dir": "elsewhere"}
    assert config["output"] == {"json": True}
    assert config["query"] == {}


def test_load_config_non_mapping_file_raises(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("- just\n- a\n- list\n")
    with pytest.raises(SystemExit):
        load_config(str(config_file), "config.yaml")


def test_load_config_non_mapping_section_raises(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("fetch:\n  - not\n  - a\n  - mapping\n")
    with pytest.raises(SystemExit):
        load_config(str(config_file), "config.yaml")


def _args(**kwargs):
    return argparse.Namespace(**kwargs)


def test_config_value_cli_overrides_config():
    args = _args(platform="linux")
    assert config_value(args, {"platform": "windows"}, "platform") == "linux"


def test_config_value_falls_back_to_config():
    args = _args(platform=None)
    assert config_value(args, {"platform": "windows"}, "platform") == "windows"


def test_config_value_falls_back_to_default():
    args = _args(platform=None)
    assert config_value(args, {}, "platform", "fallback") == "fallback"


def test_config_value_uses_config_name_override():
    args = _args(catalog=None)
    assert config_value(args, {"file": "custom.json"}, "catalog", config_name="file") == "custom.json"


def test_config_bool_cli_true_overrides_config_false():
    args = _args(json=True)
    assert config_bool(args, {"json": False}, "json") is True


def test_config_bool_none_falls_back_to_config():
    args = _args(json=None)
    assert config_bool(args, {"json": True}, "json") is True


def test_config_bool_default_when_absent():
    args = _args(json=None)
    assert config_bool(args, {}, "json", True) is True
    assert config_bool(args, {}, "json", False) is False


# --- a config file may not quietly weaken a security setting --------------------
#
# Whoever can write the directory a run happens in can drop a config file there
# and have someone else's run read it. Turning verification off that way would
# be a silent off switch, which is the one thing verification cannot allow.

def _owned_by(monkeypatch, uid):
    """Report a chosen owner for every stat, so the result does not depend on
    who happens to be running the suite. Root owns everything in a container."""
    real_stat = os.stat

    class _Owned:
        def __init__(self, info):
            self._info = info
            self.st_uid = uid
            self.st_mode = info.st_mode

        def __getattr__(self, name):
            return getattr(self._info, name)

    monkeypatch.setattr(os, "stat", lambda p, *a, **kw: _Owned(real_stat(p, *a, **kw)))


def test_a_config_file_owned_by_someone_else_may_not_weaken_security(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o644)

    _owned_by(monkeypatch, os.geteuid() + 1)

    reason = security_downgrade_reason_for(os.stat(config_path))
    assert reason is not None
    assert "owned" in reason


def test_a_world_writable_config_file_may_not_weaken_security(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o666)

    reason = security_downgrade_reason_for(os.stat(config_path))
    assert reason is not None
    assert "writable" in reason


def test_a_group_writable_config_file_may_not_weaken_security(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o664)

    assert security_downgrade_reason_for(os.stat(config_path)) is not None


def test_an_ordinary_config_file_may_weaken_security(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o644)

    assert security_downgrade_reason_for(os.stat(config_path)) is None


def test_a_root_owned_config_file_may_weaken_security(tmp_path, monkeypatch):
    """Root placed it, and root is already more privileged than this run."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o644)

    _owned_by(monkeypatch, 0)
    monkeypatch.setattr(os, "getuid", lambda: 1000)

    assert security_downgrade_reason_for(os.stat(config_path)) is None


def test_a_check_that_could_not_run_is_a_reason_to_refuse():
    """Answering "no reason to distrust" when the check itself failed is the
    one answer that cannot be right."""
    assert security_downgrade_reason_for(None) is not None


# --- the file vouched for must be the file that was read ------------------------

def test_a_config_that_cannot_be_stat_ed_may_not_weaken_security():
    """Returning "no reason to distrust" when the check itself failed is the
    one answer that cannot be right."""
    class _Boom:
        def fileno(self):
            raise OSError("stat failed")

    assert security_downgrade_reason_for(None) is not None


def test_the_reason_is_decided_from_the_handle_that_was_read(tmp_path):
    """Resolving the name a second time lets the file change in between, so
    the decision has to come from the descriptor the content came from."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o644)

    loaded = load_config(str(config_path), "config.yaml")

    assert loaded.data["catalog"] == {"verify": False}
    assert loaded.downgrade_reason is None


def test_a_world_writable_file_is_reported_through_the_loaded_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o666)

    loaded = load_config(str(config_path), "config.yaml")

    assert loaded.data["catalog"] == {"verify": False}
    assert "writable" in loaded.downgrade_reason


def test_a_config_swapped_after_it_is_opened_cannot_change_the_verdict(tmp_path, monkeypatch):
    """The attack the single open exists to stop: parse one file, vouch for
    another. Whatever replaces the name afterwards is not what was read."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("catalog: {verify: false}\n")
    config_path.chmod(0o666)

    real_open = os.open

    def swap_after_open(path, flags, *a, **kw):
        fd = real_open(path, flags, *a, **kw)
        if str(path).endswith("config.yaml"):
            trusted = tmp_path / "trusted.yaml"
            trusted.write_text("catalog: {}\n")
            trusted.chmod(0o600)
            os.replace(trusted, config_path)
        return fd

    monkeypatch.setattr(os, "open", swap_after_open)

    loaded = load_config(str(config_path), "config.yaml")

    assert "writable" in (loaded.downgrade_reason or "")


def test_no_config_file_has_no_reason_and_no_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    loaded = load_config("config.yaml", "config.yaml")

    assert loaded.data["catalog"] == {}
    assert loaded.downgrade_reason is None
