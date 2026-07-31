import base64
import json
import os
from pathlib import Path

import pytest

from omada_release_watch import bundle, refresh
from omada_release_watch.bundle import Outcome
from omada_release_watch.refresh import RefreshError

FIXTURE = Path(__file__).parent / "fixtures" / "signed-catalog.sigstore.json"


def _bundle_bytes(version="6.3.0.1", updated="2026-07-26T00:00:00Z"):
    catalog = json.dumps({
        "entries": {"fp1": {"version": version, "kind": "stable"}},
        "schema": 1,
        "updated": updated,
    })
    statement = {
        "_type": bundle.STATEMENT_TYPE,
        "predicateType": bundle.PREDICATE_TYPE,
        "subject": [{"name": "catalog.json", "digest": {"sha256": bundle.sha256_hex(catalog)}}],
        "predicate": {"catalog": catalog},
    }
    payload = base64.b64encode(json.dumps(statement).encode()).decode()
    return json.dumps({"dsseEnvelope": {"payload": payload}}).encode()


def _downloader(payload=None, error=None):
    def download(url, timeout=30):
        if error is not None:
            raise error
        return payload
    return download


# --- the happy path -----------------------------------------------------------

def test_a_good_download_is_written_to_the_catalog_path(tmp_path):
    target = tmp_path / "catalog.sigstore.json"

    refresh.refresh(
        "https://example.invalid/catalog.sigstore.json",
        target,
        verify=False,
        download=_downloader(_bundle_bytes()),
    )

    assert target.exists()
    assert bundle.load(target, verify=False).data["entries"]["fp1"]["version"] == "6.3.0.1"


def test_the_default_url_reads_the_bundle_committed_to_main():
    """The crawler commits the bundle to the public repository's main branch.
    A release asset needs a token even on a public repository, so the default
    source is raw content on the branch the crawler pushes to."""
    assert refresh.DEFAULT_CATALOG_URL == (
        "https://raw.githubusercontent.com/Jaturu/omada-release-watch"
        "/main/catalog.sigstore.json"
    )


def test_the_url_is_requested_as_given(tmp_path):
    seen = []

    def download(url, timeout=30):
        seen.append(url)
        return _bundle_bytes()

    refresh.refresh("https://example.invalid/c.json", tmp_path / "c.json", verify=False, download=download)
    assert seen == ["https://example.invalid/c.json"]


# --- a bad download must never replace a good catalog --------------------------

def test_a_network_failure_leaves_the_existing_catalog_alone(tmp_path):
    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(_bundle_bytes("6.2.14.10"))

    with pytest.raises(RefreshError):
        refresh.refresh(
            "https://example.invalid/c.json",
            target,
            verify=False,
            download=_downloader(error=OSError("connection refused")),
        )

    assert bundle.load(target, verify=False).data["entries"]["fp1"]["version"] == "6.2.14.10"


def test_a_malformed_download_leaves_the_existing_catalog_alone(tmp_path):
    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(_bundle_bytes("6.2.14.10"))

    with pytest.raises(RefreshError):
        refresh.refresh(
            "https://example.invalid/c.json",
            target,
            verify=False,
            download=_downloader(b"not a bundle"),
        )

    assert bundle.load(target, verify=False).data["entries"]["fp1"]["version"] == "6.2.14.10"


def test_a_download_that_fails_verification_is_refused(tmp_path):
    """Refusing new data is not the same as refusing to run. A catalog already
    on disk keeps working, but a tampered download never lands, because its
    download_url would drive what the tool installs next."""
    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(_bundle_bytes("6.2.14.10"))

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            "https://example.invalid/c.json",
            target,
            verify=True,
            download=_downloader(_bundle_bytes()),
            verifier_factory=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        )

    assert "not replaced" in str(excinfo.value).lower()
    assert bundle.load(target, verify=False).data["entries"]["fp1"]["version"] == "6.2.14.10"


def test_a_failed_refresh_leaves_no_partial_file_behind(tmp_path):
    target = tmp_path / "catalog.sigstore.json"

    with pytest.raises(RefreshError):
        refresh.refresh(
            "https://example.invalid/c.json",
            target,
            verify=False,
            download=_downloader(b"not a bundle"),
        )

    assert list(tmp_path.iterdir()) == []


def test_a_first_refresh_creates_the_catalog(tmp_path):
    target = tmp_path / "nested" / "catalog.sigstore.json"

    refresh.refresh(
        "https://example.invalid/c.json",
        target,
        verify=False,
        download=_downloader(_bundle_bytes()),
    )

    assert target.exists()


# --- what the caller is told ----------------------------------------------------

def test_the_outcome_of_the_accepted_catalog_is_returned(tmp_path):
    result = refresh.refresh(
        "https://example.invalid/c.json",
        tmp_path / "c.json",
        verify=False,
        download=_downloader(_bundle_bytes()),
    )

    assert result.outcome is Outcome.DISABLED


def test_the_default_downloader_is_resolved_at_call_time(tmp_path, monkeypatch):
    """Binding it as a default argument would freeze it at import, which makes
    the seam look injectable while not being substitutable."""
    monkeypatch.setattr(
        "omada_release_watch.refresh.fetch_catalog_bytes",
        lambda url, timeout=30: _bundle_bytes("9.9.9.9"),
    )

    refresh.refresh("https://example.invalid/c.json", tmp_path / "c.json", verify=False)

    data = bundle.load(tmp_path / "c.json", verify=False).data
    assert data["entries"]["fp1"]["version"] == "9.9.9.9"


# --- rollback and staging safety ------------------------------------------------

URL = "https://example.invalid/catalog.sigstore.json"


def test_an_older_catalog_is_refused(tmp_path):
    """A genuinely signed but older catalog must not replace a newer one.
    The signature is authentic, so only the date can catch this."""
    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(_bundle_bytes(updated="2026-07-20T00:00:00Z"))

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=False,
            download=_downloader(_bundle_bytes(updated="2026-07-01T00:00:00Z")),
        )

    assert "older" in str(excinfo.value).lower()
    assert bundle.load(target, verify=False).data["updated"] == "2026-07-20T00:00:00Z"


def test_a_newer_catalog_replaces_an_older_one(tmp_path):
    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(_bundle_bytes(updated="2026-07-01T00:00:00Z"))

    refresh.refresh(
        URL, target, verify=False,
        download=_downloader(_bundle_bytes(updated="2026-07-20T00:00:00Z")),
    )

    assert bundle.load(target, verify=False).data["updated"] == "2026-07-20T00:00:00Z"


def test_an_identical_date_is_still_accepted(tmp_path):
    """Re-running --refresh against an unchanged catalog is routine and must
    not be treated as a rollback."""
    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(_bundle_bytes(version="6.2.0.1"))

    refresh.refresh(
        URL, target, verify=False, download=_downloader(_bundle_bytes(version="6.3.0.1")),
    )

    assert bundle.load(target, verify=False).data["entries"]["fp1"]["version"] == "6.3.0.1"


def test_an_undated_catalog_on_disk_does_not_block_a_refresh(tmp_path):
    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(_bundle_bytes(updated=None))

    refresh.refresh(URL, target, verify=False, download=_downloader(_bundle_bytes()))

    assert bundle.load(target, verify=False).data["updated"] == "2026-07-26T00:00:00Z"


def test_refresh_does_not_write_through_a_pre_placed_symlink(tmp_path):
    """A predictable staging name lets anyone who can write the directory
    redirect the write into a file of their choosing."""
    victim = tmp_path / "victim"
    victim.write_text("important")
    target = tmp_path / "catalog.sigstore.json"
    (tmp_path / "catalog.sigstore.json.part").symlink_to(victim)

    refresh.refresh(URL, target, verify=False, download=_downloader(_bundle_bytes()))

    assert victim.read_text() == "important"


# --- verification with signature checking on --------------------------------
#
# Everything above runs with verify=False. These drive the path that decides
# what may replace a trusted catalog, through the injected verifier.

def _signed(updated="2026-07-26T00:00:00Z", version="6.3.0.1"):
    """The real fixture carrying a chosen statement, plus that statement's
    bytes, which is what a verifier hands back after checking a signature."""
    catalog = json.dumps({
        "entries": {"fp1": {"version": version, "kind": "stable"}},
        "schema": 1,
        "updated": updated,
    })
    statement = {
        "_type": bundle.STATEMENT_TYPE,
        "predicateType": bundle.PREDICATE_TYPE,
        "subject": [{"name": "catalog.json", "digest": {"sha256": bundle.sha256_hex(catalog)}}],
        "predicate": {"catalog": catalog},
    }
    payload = json.dumps(statement).encode()

    raw = json.loads(FIXTURE.read_bytes())
    raw["dsseEnvelope"]["payload"] = base64.b64encode(payload).decode()

    return json.dumps(raw).encode(), payload


class _Verifier:
    """Hands back each queued payload in call order: the download first, then
    the catalog already on disk. An empty queue means verification fails."""

    def __init__(self, *payloads, watch=None):
        self.payloads = list(payloads)
        self.watch = watch
        self.staged: list[list[str]] = []

    def verify_dsse(self, parsed, pol):
        if self.watch is not None:
            self.staged.append(sorted(p.name for p in self.watch.iterdir()))

        if not self.payloads:
            raise ValueError("signature does not verify")

        return ("application/vnd.in-toto+json", self.payloads.pop(0))


def test_a_verified_download_is_accepted(tmp_path):
    raw, payload = _signed()
    target = tmp_path / "catalog.sigstore.json"

    result = refresh.refresh(
        URL, target, verify=True,
        download=_downloader(raw),
        verifier_factory=lambda: _Verifier(payload),
    )

    assert result.outcome is Outcome.VERIFIED
    assert target.read_bytes() == raw


def test_a_download_whose_signature_does_not_verify_is_refused(tmp_path):
    raw, _ = _signed()
    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(_bundle_bytes("6.2.14.10"))

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=True,
            download=_downloader(raw),
            verifier_factory=lambda: _Verifier(),
        )

    assert "altered" in str(excinfo.value)
    assert bundle.load(target, verify=False).data["entries"]["fp1"]["version"] == "6.2.14.10"


def test_an_older_catalog_is_refused_with_verification_on(tmp_path):
    """The rollback check has to verify the catalog already on disk, which it
    can only do through the same injected verifier."""
    incoming, incoming_payload = _signed(updated="2026-07-01T00:00:00Z")
    local, local_payload = _signed(updated="2026-07-20T00:00:00Z")

    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(local)

    # One verifier across both checks, so the queue spans the download and the
    # catalog on disk in that order.
    verifier = _Verifier(incoming_payload, local_payload)

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=True,
            download=_downloader(incoming),
            verifier_factory=lambda: verifier,
        )

    assert "older" in str(excinfo.value).lower()
    assert target.read_bytes() == local


def test_a_newer_catalog_replaces_an_older_one_with_verification_on(tmp_path):
    incoming, incoming_payload = _signed(updated="2026-07-20T00:00:00Z")
    local, local_payload = _signed(updated="2026-07-01T00:00:00Z")

    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(local)

    verifier = _Verifier(incoming_payload, local_payload)

    refresh.refresh(
        URL, target, verify=True,
        download=_downloader(incoming),
        verifier_factory=lambda: verifier,
    )

    assert target.read_bytes() == incoming


def test_a_catalog_that_does_not_verify_cannot_block_its_own_replacement(tmp_path):
    """A local copy that fails verification has no say in what replaces it,
    even when the replacement is older. Otherwise a tampered catalog carrying
    a future date would pin the tool to itself forever."""
    incoming, incoming_payload = _signed(updated="2026-07-01T00:00:00Z")
    local, _ = _signed(updated="2099-01-01T00:00:00Z")

    target = tmp_path / "catalog.sigstore.json"
    target.write_bytes(local)

    # The download verifies. The catalog on disk does not.
    verifier = _Verifier(incoming_payload)

    refresh.refresh(
        URL, target, verify=True,
        download=_downloader(incoming),
        verifier_factory=lambda: verifier,
    )

    assert target.read_bytes() == incoming


# --- the verified bytes are the installed bytes ------------------------------

def test_the_download_is_verified_before_anything_is_staged(tmp_path):
    """Verifying a file by path and then renaming that path installs bytes
    that were never the bytes verified. Nothing may be on disk yet."""
    raw, payload = _signed()
    verifier = _Verifier(payload, watch=tmp_path)

    refresh.refresh(
        URL, tmp_path / "catalog.sigstore.json", verify=True,
        download=_downloader(raw),
        verifier_factory=lambda: verifier,
    )

    assert verifier.staged == [[]]


def _swap_at(monkeypatch, hook, action):
    """Run `action` on the staging path at a chosen point in the write, the
    way another process with write access to the directory would."""
    real = getattr(os, hook)
    seen = {"done": False}

    def wrapper(*args, **kwargs):
        result = real(*args, **kwargs)
        if not seen["done"]:
            for candidate in Path(args[0]).parent.glob("*.part") if isinstance(args[0], (str, Path)) else []:
                seen["done"] = True
                action(candidate)
        return result

    monkeypatch.setattr(os, hook, wrapper)


def _swap_staged_file(tmp_path, monkeypatch, content=b"attacker content"):
    """Prepare content elsewhere, then rename it over the staging name.

    A rename is used rather than unlink and rewrite, because a freshly freed
    inode number is reused immediately on some filesystems, which would leave
    the swapped file indistinguishable by identity alone.
    """
    real_fsync = os.fsync

    def swap(fd):
        real_fsync(fd)
        for staged in tmp_path.glob("*.part"):
            attacker = tmp_path / "attacker-prepared"
            attacker.write_bytes(content)
            os.replace(attacker, staged)

    monkeypatch.setattr(os, "fsync", swap)


def test_a_staging_file_swapped_before_the_rename_is_refused(tmp_path, monkeypatch):
    """Whoever can write the directory can replace the staged file after it is
    written. The rename must not install a file this run never wrote."""
    raw, payload = _signed()
    target = tmp_path / "catalog.sigstore.json"

    _swap_staged_file(tmp_path, monkeypatch)

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=True,
            download=_downloader(raw),
            verifier_factory=lambda: _Verifier(payload),
        )

    assert "changed" in str(excinfo.value).lower()
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_swap_that_keeps_the_file_identity_is_still_refused(tmp_path, monkeypatch):
    """Identity alone is not enough. Inode numbers are reused as soon as they
    are freed, so the content this run wrote is what has to be confirmed."""
    raw, payload = _signed()
    target = tmp_path / "catalog.sigstore.json"

    _swap_staged_file(tmp_path, monkeypatch)
    monkeypatch.setattr(os.path, "samestat", lambda a, b: True)

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=True,
            download=_downloader(raw),
            verifier_factory=lambda: _Verifier(payload),
        )

    assert "changed" in str(excinfo.value).lower()
    assert not target.exists()


# --- a hostile catalog response may not exhaust the machine -------------------

def test_a_catalog_download_past_the_size_limit_is_refused(tmp_path):
    def download(url, timeout=30, max_bytes=None):
        raise refresh.RefreshError("boom")

    huge = b"\0" * 5000

    with pytest.raises(RefreshError, match="larger than"):
        refresh.refresh(
            URL, tmp_path / "c.json", verify=False,
            download=lambda url: huge,
            max_bytes=4096,
        )

    assert list(tmp_path.iterdir()) == []


def test_the_default_downloader_stops_reading_past_the_limit():
    """The cap has to count decoded bytes as they arrive. Reading the whole
    body first and measuring afterwards is what it exists to prevent."""
    class _Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=None):
            for _ in range(100):
                yield b"\0" * 1024

    import omada_release_watch.refresh as module

    original = module.requests.get
    module.requests.get = lambda url, **kw: _Response()
    try:
        with pytest.raises(RefreshError, match="larger than"):
            module.fetch_catalog_bytes(URL, max_bytes=4096)
    finally:
        module.requests.get = original


# --- rollback is judged against every catalog present, not just the target ----

def test_a_download_older_than_a_catalog_elsewhere_is_refused(tmp_path):
    """--refresh writes the refresh slot, but the published copy sits beside
    it. Comparing only against the target lets an older catalog land whenever
    the slot is empty, which it always is on a first refresh."""
    published = tmp_path / "catalog.sigstore.json"
    published.write_bytes(_bundle_bytes(updated="2026-07-20T00:00:00Z"))

    target = tmp_path / "catalog-refresh.sigstore.json"

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=False,
            download=_downloader(_bundle_bytes(updated="2026-07-01T00:00:00Z")),
            compare_against=[published],
        )

    assert "older" in str(excinfo.value).lower()
    assert not target.exists()


def test_a_download_newer_than_every_catalog_present_is_accepted(tmp_path):
    published = tmp_path / "catalog.sigstore.json"
    published.write_bytes(_bundle_bytes(updated="2026-07-01T00:00:00Z"))
    target = tmp_path / "catalog-refresh.sigstore.json"
    target.write_bytes(_bundle_bytes(updated="2026-07-10T00:00:00Z"))

    refresh.refresh(
        URL, target, verify=False,
        download=_downloader(_bundle_bytes(updated="2026-07-20T00:00:00Z")),
        compare_against=[published],
    )

    assert bundle.load(target, verify=False).data["updated"] == "2026-07-20T00:00:00Z"


def test_a_catalog_that_is_not_there_is_simply_skipped(tmp_path):
    target = tmp_path / "catalog-refresh.sigstore.json"

    refresh.refresh(
        URL, target, verify=False,
        download=_downloader(_bundle_bytes()),
        compare_against=[tmp_path / "absent.sigstore.json"],
    )

    assert target.exists()


def test_the_newest_catalog_present_is_the_one_to_beat(tmp_path):
    """Two catalogs, and the incoming sits between them. Whichever is checked
    first must not decide it: the newest is the baseline."""
    published = tmp_path / "catalog.sigstore.json"
    published.write_bytes(_bundle_bytes(updated="2026-07-20T00:00:00Z"))

    target = tmp_path / "catalog-refresh.sigstore.json"
    target.write_bytes(_bundle_bytes(updated="2026-07-10T00:00:00Z"))

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=False,
            download=_downloader(_bundle_bytes(updated="2026-07-15T00:00:00Z")),
            compare_against=[published],
        )

    assert "2026-07-20" in str(excinfo.value)
    assert bundle.load(target, verify=False).data["updated"] == "2026-07-10T00:00:00Z"


# --- the default downloader itself -------------------------------------------

class _FakeResponse:
    def __init__(self, chunks=(b"{}",), status=200):
        self._chunks = chunks
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"HTTP {self.status}")

    def iter_content(self, chunk_size=None):
        yield from self._chunks


def test_the_downloader_refuses_an_error_response(monkeypatch):
    """Without this, a 404 body is handed on as if it were catalog bytes."""
    import requests

    monkeypatch.setattr(
        refresh.requests, "get", lambda url, **kw: _FakeResponse(status=404)
    )

    with pytest.raises(requests.exceptions.HTTPError):
        refresh.fetch_catalog_bytes(URL)


def test_the_downloader_returns_the_body_it_read(monkeypatch):
    monkeypatch.setattr(
        refresh.requests, "get", lambda url, **kw: _FakeResponse((b"ab", b"cd"))
    )

    assert refresh.fetch_catalog_bytes(URL) == b"abcd"


def test_the_downloader_asks_for_a_timeout(monkeypatch):
    """A request with no timeout hangs forever on a server that never answers."""
    seen = {}

    def capture(url, **kwargs):
        seen.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(refresh.requests, "get", capture)
    refresh.fetch_catalog_bytes(URL, timeout=17)

    assert seen["timeout"] == 17
    assert seen["stream"] is True


def test_a_staging_name_turned_into_a_symlink_is_refused(tmp_path, monkeypatch):
    """Hardlink the staged file, then put a symlink to that hardlink back
    under the staging name. Identity and content both still match, so a check
    that resolves the name is satisfied and the rename installs the link."""
    raw, payload = _signed()
    target = tmp_path / "catalog.sigstore.json"
    store = tmp_path / "attacker-owned"
    real_fsync = os.fsync

    def relink(fd):
        real_fsync(fd)
        for staged in tmp_path.glob("*.part"):
            if staged.is_symlink():
                continue
            os.link(staged, store)
            staged.unlink()
            staged.symlink_to(store)

    monkeypatch.setattr(os, "fsync", relink)

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=True,
            download=_downloader(raw),
            verifier_factory=lambda: _Verifier(payload),
        )

    assert "changed" in str(excinfo.value).lower()
    assert not target.exists()


def test_a_swap_at_the_rename_itself_is_reported_rather_than_called_success(tmp_path, monkeypatch):
    """The rename is atomic and destroys whatever was there, so a swap in that
    last instant cannot be prevented. It must not be reported as verified."""
    raw, payload = _signed()
    target = tmp_path / "catalog.sigstore.json"
    real_replace = os.replace

    def swap(src, dst, *args, **kwargs):
        if Path(src).suffix == ".part":
            attacker = tmp_path / "attacker-prepared"
            attacker.write_bytes(b"attacker content")
            real_replace(attacker, src)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", swap)

    with pytest.raises(RefreshError) as excinfo:
        refresh.refresh(
            URL, target, verify=True,
            download=_downloader(raw),
            verifier_factory=lambda: _Verifier(payload),
        )

    assert "not what was verified" in str(excinfo.value)
    assert target.read_bytes() != raw
