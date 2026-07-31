import base64
import json
from pathlib import Path

import pytest

from omada_release_watch.text import is_safe_filename

# --- is_safe_filename (security: path traversal) ----------------------------

@pytest.mark.parametrize(
    "value",
    [
        "Omada_Network_Application_v6.2.14.10_linux_x64.tar.gz",
        "omada-controller_5.15.24.18_amd64.deb",
        "a",
    ],
)
def test_is_safe_filename_accepts_plain_filenames(value):
    assert is_safe_filename(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "../../../home/user/.ssh/authorized_keys",
        "..\\..\\evil.txt",
        "sub/dir/file.tar.gz",
        "a/b",
    ],
)
def test_is_safe_filename_rejects_traversal_and_separators(value):
    assert is_safe_filename(value) is False


# --- filename safety is an allowlist ------------------------------------------
#
# A denylist of "/" and "\" leaves everything else through: drive-relative
# Windows paths, control characters, terminal escapes, and dotfiles.

@pytest.mark.parametrize("name", [
    "Omada_Controller_Windows_v5.15.20.21.zip",
    "Omada_SDN Controller_v5.15.20.19_Windows.zip",
    "Omada_Network_Application_Windows_v6.2.10.17_[20260428102207].zip",
    "Omada_SDN_Controller_v5.15.24.18_linux_x64_20250630184423 (1).tar.gz",
    "Omada_Network_Application_v6.3.0.36_linux_x64_20260724012208.tar.gz.zip",
])
def test_names_the_vendor_actually_publishes_are_accepted(name):
    assert is_safe_filename(name) is True


@pytest.mark.parametrize("name", [
    "C:evil.exe",
    "C:/evil.exe",
    "\\\\server\\share\\evil.exe",
    "a\x00b.zip",
    "a\nb.zip",
    "a\rb.zip",
    "\x1b[31mred.zip",
    ".bashrc",
    ".",
    "..",
    "",
    "../x.zip",
    "/etc/passwd",
])
def test_hostile_names_are_refused(name):
    assert is_safe_filename(name) is False


def test_a_name_longer_than_the_limit_is_refused():
    assert is_safe_filename("a" * 256 + ".zip") is False


def test_every_filename_in_the_published_catalog_is_fetchable():
    """A published artifact this client refuses to name is a broken fetch for
    every user, so the allowlist is checked against the real catalog."""
    published = Path(__file__).resolve().parent.parent / "catalog.sigstore.json"
    raw = json.loads(published.read_bytes())
    statement = json.loads(base64.b64decode(raw["dsseEnvelope"]["payload"]))
    catalog = json.loads(statement["predicate"]["catalog"])

    rejected = [
        entry["filename"]
        for entry in catalog["entries"].values()
        if not is_safe_filename(entry["filename"])
    ]

    assert rejected == []
