import hashlib

import pytest
import requests

from omada_release_watch.fetch import FetchError, fetch_artifact, sha256_file


class FakeResponse:
    """Minimal stand-in for requests.Response, used as a context manager
    the way fetch_artifact consumes it: `with requests.get(...) as response`.
    """

    def __init__(self, chunks, status_code=200):
        self._chunks = chunks
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1024 * 1024):
        for chunk in self._chunks:
            yield chunk


def _unpack(result):
    return result.path, result.sha256, result.downloaded, result.hash_checked


def test_sha256_file_matches_known_content(tmp_path):
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")
    assert sha256_file(path) == hashlib.sha256(b"hello world").hexdigest()


def test_fetch_artifact_missing_download_url_raises(tmp_path):
    with pytest.raises(FetchError):
        fetch_artifact({"filename": "a.tar.gz"}, tmp_path)


def test_fetch_artifact_missing_filename_raises(tmp_path):
    with pytest.raises(FetchError):
        fetch_artifact({"download_url": "https://x/a.tar.gz"}, tmp_path)


# --- SECURITY: path traversal ------------------------------------------------

@pytest.mark.parametrize(
    "unsafe_filename",
    [
        "../../evil.txt",
        "../../../home/user/.ssh/authorized_keys",
        "..",
        "sub/dir/file.tar.gz",
    ],
)
def test_fetch_artifact_rejects_unsafe_filename(tmp_path, unsafe_filename, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("should never attempt a network request for an unsafe filename")

    monkeypatch.setattr("omada_release_watch.fetch.requests.get", fail_if_called)

    record = {
        "download_url": "https://attacker.example/payload",
        "filename": unsafe_filename,
    }
    with pytest.raises(FetchError, match="unsafe filename"):
        fetch_artifact(record, tmp_path)

    # nothing should have been written anywhere, inside or outside tmp_path
    assert list(tmp_path.rglob("*")) == []


# --- local cache behavior -----------------------------------------------------

def test_fetch_artifact_skips_download_when_local_file_exists_no_sha256(tmp_path, monkeypatch):
    existing = tmp_path / "release.tar.gz"
    existing.write_bytes(b"already have this")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not hit the network when a local file already exists")

    monkeypatch.setattr("omada_release_watch.fetch.requests.get", fail_if_called)

    record = {"download_url": "https://x/release.tar.gz", "filename": "release.tar.gz"}
    path, sha, downloaded, _ = _unpack(fetch_artifact(record, tmp_path))

    assert path == existing
    assert sha == hashlib.sha256(b"already have this").hexdigest()
    assert downloaded is False


def test_fetch_artifact_accepts_local_file_matching_expected_sha256(tmp_path, monkeypatch):
    content = b"verified content"
    existing = tmp_path / "release.tar.gz"
    existing.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("no network expected when local sha256 already matches")

    monkeypatch.setattr("omada_release_watch.fetch.requests.get", fail_if_called)

    record = {
        "download_url": "https://x/release.tar.gz",
        "filename": "release.tar.gz",
        "sha256": expected,
    }
    path, sha, downloaded, _ = _unpack(fetch_artifact(record, tmp_path))
    assert sha == expected
    assert downloaded is False


def test_fetch_artifact_refuses_mismatched_local_content(tmp_path):
    """SECURITY/integrity: content changed under an unchanged fingerprint
    must be a loud failure, not a silent re-download or overwrite."""
    existing = tmp_path / "release.tar.gz"
    existing.write_bytes(b"original bytes")

    record = {
        "download_url": "https://x/release.tar.gz",
        "filename": "release.tar.gz",
        "sha256": "0" * 64,  # deliberately wrong
    }
    with pytest.raises(FetchError, match="Content changed under an unchanged release identity"):
        fetch_artifact(record, tmp_path)

    # must refuse to overwrite -- original bytes must remain untouched
    assert existing.read_bytes() == b"original bytes"


# --- download path (mocked network) -------------------------------------------

def test_fetch_artifact_downloads_and_hashes_new_file(tmp_path, monkeypatch):
    content = b"brand new artifact bytes"

    def fake_get(url, stream=True, timeout=60):
        assert url == "https://x/release.tar.gz"
        return FakeResponse([content])

    monkeypatch.setattr("omada_release_watch.fetch.requests.get", fake_get)

    record = {"download_url": "https://x/release.tar.gz", "filename": "release.tar.gz"}
    path, sha, downloaded, _ = _unpack(fetch_artifact(record, tmp_path))

    assert path == tmp_path / "release.tar.gz"
    assert path.read_bytes() == content
    assert sha == hashlib.sha256(content).hexdigest()
    assert downloaded is True
    assert not list(tmp_path.glob("*.part"))


def test_fetch_artifact_cleans_up_partial_file_on_mid_stream_failure(tmp_path, monkeypatch):
    def failing_iter_content(chunk_size=1024 * 1024):
        yield b"partial-bytes-only"
        raise requests.exceptions.ConnectionError("connection dropped mid-stream")

    class FailingResponse(FakeResponse):
        def iter_content(self, chunk_size=1024 * 1024):
            return failing_iter_content(chunk_size)

    def fake_get(url, stream=True, timeout=60):
        return FailingResponse([])

    monkeypatch.setattr("omada_release_watch.fetch.requests.get", fake_get)

    record = {"download_url": "https://x/release.tar.gz", "filename": "release.tar.gz"}
    with pytest.raises(FetchError, match="Failed to fetch artifact"):
        fetch_artifact(record, tmp_path)

    assert not (tmp_path / "release.tar.gz").exists()
    assert not list(tmp_path.glob("*.part"))


def test_fetch_artifact_raises_on_http_error(tmp_path, monkeypatch):
    def fake_get(url, stream=True, timeout=60):
        return FakeResponse([b""], status_code=404)

    monkeypatch.setattr("omada_release_watch.fetch.requests.get", fake_get)

    record = {"download_url": "https://x/missing.tar.gz", "filename": "missing.tar.gz"}
    with pytest.raises(FetchError, match="Failed to fetch artifact"):
        fetch_artifact(record, tmp_path)

    assert not (tmp_path / "missing.tar.gz").exists()


# --- the signed hash must gate a fresh download ----------------------------------

def _serving(content, monkeypatch):
    def fake_get(url, stream=True, timeout=60):
        return FakeResponse([content])

    monkeypatch.setattr("omada_release_watch.fetch.requests.get", fake_get)


def test_fetch_artifact_refuses_a_download_that_does_not_match_the_signed_hash(
    tmp_path, monkeypatch
):
    """The catalog is signed so its sha256 can be trusted. Enforcing it only on
    a cache hit means the bad artifact is already on disk before anyone knows."""
    _serving(b"TROJANED INSTALLER BYTES", monkeypatch)

    record = {
        "download_url": "https://x/release.tar.gz",
        "filename": "release.tar.gz",
        "sha256": hashlib.sha256(b"the real artifact").hexdigest(),
    }

    with pytest.raises(FetchError) as excinfo:
        fetch_artifact(record, tmp_path)

    assert "does not match" in str(excinfo.value).lower()
    assert not (tmp_path / "release.tar.gz").exists()
    assert list(tmp_path.iterdir()) == []


def test_fetch_artifact_accepts_a_download_matching_the_signed_hash(tmp_path, monkeypatch):
    content = b"the real artifact"
    _serving(content, monkeypatch)

    record = {
        "download_url": "https://x/release.tar.gz",
        "filename": "release.tar.gz",
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    path, sha, downloaded, _ = _unpack(fetch_artifact(record, tmp_path))

    assert path.read_bytes() == content
    assert sha == hashlib.sha256(content).hexdigest()
    assert downloaded is True


def test_fetch_artifact_still_downloads_when_the_catalog_records_no_hash(
    tmp_path, monkeypatch
):
    content = b"unhashed artifact"
    _serving(content, monkeypatch)

    record = {"download_url": "https://x/release.tar.gz", "filename": "release.tar.gz"}
    path, sha, downloaded, _ = _unpack(fetch_artifact(record, tmp_path))

    assert path.read_bytes() == content
    assert sha == hashlib.sha256(content).hexdigest()


def test_fetch_artifact_does_not_write_through_a_pre_placed_symlink(tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    victim.write_text("important")
    (tmp_path / "release.tar.gz.part").symlink_to(victim)
    _serving(b"artifact bytes", monkeypatch)

    record = {"download_url": "https://x/release.tar.gz", "filename": "release.tar.gz"}
    fetch_artifact(record, tmp_path)

    assert victim.read_text() == "important"


# --- a hostile response may not exhaust the machine ---------------------------
#
# Nothing about a response body is known before it is read. requests decodes
# Content-Encoding transparently, so the size on the wire bounds nothing.

def test_a_download_past_the_size_limit_is_refused(tmp_path, monkeypatch):
    record = {
        "download_url": "https://x/big.tar.gz",
        "filename": "big.tar.gz",
        "sha256": "ab" * 32,
    }
    chunks = [b"\0" * 1024] * 8

    monkeypatch.setattr(
        "omada_release_watch.fetch.requests.get",
        lambda url, **kw: FakeResponse(chunks),
    )

    with pytest.raises(FetchError, match="larger than"):
        fetch_artifact(record, tmp_path, max_bytes=4096)

    assert list(tmp_path.iterdir()) == []


def test_a_download_at_the_size_limit_is_kept(tmp_path, monkeypatch):
    body = b"\0" * 4096
    record = {
        "download_url": "https://x/exact.tar.gz",
        "filename": "exact.tar.gz",
        "sha256": hashlib.sha256(body).hexdigest(),
    }

    monkeypatch.setattr(
        "omada_release_watch.fetch.requests.get",
        lambda url, **kw: FakeResponse([body]),
    )

    path, digest, downloaded, _ = _unpack(fetch_artifact(record, tmp_path, max_bytes=4096))

    assert downloaded is True
    assert path.read_bytes() == body


# --- whether the hash was checked is part of the answer -----------------------
#
# sha256 is optional in the catalog. When it is absent nothing is compared, and
# the hash reported afterwards is one this tool computed from whatever arrived.

def test_a_download_reports_that_its_hash_was_checked(tmp_path, monkeypatch):
    body = b"payload"
    record = {
        "download_url": "https://x/a.tar.gz",
        "filename": "a.tar.gz",
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    monkeypatch.setattr(
        "omada_release_watch.fetch.requests.get",
        lambda url, **kw: FakeResponse([body]),
    )

    result = fetch_artifact(record, tmp_path)

    assert result.hash_checked is True
    assert result.sha256 == hashlib.sha256(body).hexdigest()


def test_a_download_with_no_catalog_hash_says_so(tmp_path, monkeypatch):
    record = {"download_url": "https://x/a.tar.gz", "filename": "a.tar.gz"}
    monkeypatch.setattr(
        "omada_release_watch.fetch.requests.get",
        lambda url, **kw: FakeResponse([b"whatever arrived"]),
    )

    result = fetch_artifact(record, tmp_path)

    assert result.hash_checked is False
    assert result.downloaded is True


def test_a_cache_hit_with_no_catalog_hash_says_so(tmp_path):
    (tmp_path / "a.tar.gz").write_bytes(b"already here")
    record = {"download_url": "https://x/a.tar.gz", "filename": "a.tar.gz"}

    result = fetch_artifact(record, tmp_path)

    assert result.hash_checked is False
    assert result.downloaded is False


def test_a_cache_hit_checked_against_the_catalog_says_so(tmp_path):
    body = b"already here"
    (tmp_path / "a.tar.gz").write_bytes(body)
    record = {
        "download_url": "https://x/a.tar.gz",
        "filename": "a.tar.gz",
        "sha256": hashlib.sha256(body).hexdigest(),
    }

    result = fetch_artifact(record, tmp_path)

    assert result.hash_checked is True
    assert result.downloaded is False


def test_fetch_asks_for_a_timeout(tmp_path, monkeypatch):
    """A download with no timeout hangs forever on a server that never answers."""
    seen = {}

    def capture(url, **kwargs):
        seen.update(kwargs)
        return FakeResponse([b"body"])

    monkeypatch.setattr("omada_release_watch.fetch.requests.get", capture)
    fetch_artifact(
        {"download_url": "https://x/a.zip", "filename": "a.zip"},
        tmp_path,
        timeout=23,
    )

    assert seen["timeout"] == 23
    assert seen["stream"] is True


# --- a catalog may not name the files the tool's own decisions read ------------

def test_fetch_refuses_to_write_over_a_protected_path(tmp_path):
    """A catalog that names config.yaml writes a file the config gate then
    vouches for, because it lands owned by the caller at 0644."""
    protected = tmp_path / "config.yaml"
    record = {
        "download_url": "https://x/a.zip",
        "filename": "config.yaml",
        "sha256": "ab" * 32,
    }

    with pytest.raises(FetchError, match="Refusing to write"):
        fetch_artifact(record, tmp_path, protected=[protected])

    assert not protected.exists()


def test_fetch_refuses_a_protected_path_reached_by_another_route(tmp_path):
    """The comparison has to be on the resolved path, not the string."""
    (tmp_path / "sub").mkdir()
    protected = tmp_path / "sub" / ".." / "catalog.sigstore.json"
    record = {
        "download_url": "https://x/a.zip",
        "filename": "catalog.sigstore.json",
        "sha256": "ab" * 32,
    }

    with pytest.raises(FetchError, match="Refusing to write"):
        fetch_artifact(record, tmp_path, protected=[protected])


def test_fetch_allows_an_ordinary_name_beside_a_protected_one(tmp_path, monkeypatch):
    body = b"artifact"
    record = {
        "download_url": "https://x/a.zip",
        "filename": "a.zip",
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    monkeypatch.setattr(
        "omada_release_watch.fetch.requests.get",
        lambda url, **kw: FakeResponse([body]),
    )

    result = fetch_artifact(record, tmp_path, protected=[tmp_path / "config.yaml"])

    assert result.downloaded is True
