import base64
import json
import os
from pathlib import Path

import pytest
from sigstore.errors import VerificationError
from sigstore.verify import policy

from omada_release_watch import bundle
from omada_release_watch.bundle import Outcome

FIXTURE = Path(__file__).parent / "fixtures" / "signed-catalog.sigstore.json"


# --- helpers ----------------------------------------------------------------

def _fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _with_statement(statement: dict) -> bytes:
    """The real fixture, with its signed payload swapped for `statement`.

    The signature no longer matches, which is irrelevant for the parsing
    paths and is the point for the tampering paths.
    """
    raw = json.loads(_fixture_bytes())
    raw["dsseEnvelope"]["payload"] = base64.b64encode(
        json.dumps(statement).encode()
    ).decode()
    return json.dumps(raw).encode()


def _statement(**overrides) -> dict:
    catalog = overrides.pop("catalog", '{"entries": {}, "schema": 1}')
    statement = {
        "_type": bundle.STATEMENT_TYPE,
        "predicateType": bundle.PREDICATE_TYPE,
        "subject": [{"name": "catalog.json", "digest": {"sha256": "00" * 32}}],
        "predicate": {"catalog": catalog},
    }
    statement.update(overrides)
    return statement


def _write(tmp_path: Path, raw: bytes) -> Path:
    path = tmp_path / "catalog.sigstore.json"
    path.write_bytes(raw)
    return path


class FakeVerifier:
    """Stands in for sigstore's Verifier.

    `load` verifies twice: once against the pinned identities, and if that
    fails, once against the issuer alone to decide whether the certificate
    is genuine. The two are told apart by the policy they are handed.
    """

    def __init__(self, payload: bytes | None = None, issuer_ok: bool = False):
        self.payload = payload
        self.issuer_ok = issuer_ok
        self.policies: list[object] = []

    def verify_dsse(self, parsed, pol):
        self.policies.append(pol)

        if isinstance(pol, policy.OIDCIssuer):
            if self.issuer_ok:
                return ("application/vnd.in-toto+json", b"{}")
            raise ValueError("issuer does not match")

        if self.payload is None:
            raise ValueError("pinned identity does not match")

        return ("application/vnd.in-toto+json", self.payload)


# --- unreadable and malformed input ------------------------------------------

def test_plain_catalog_json_is_not_a_bundle(tmp_path):
    path = _write(tmp_path, b'{"entries": {}, "schema": 1}')
    assert bundle.load(path, verify=False).outcome is Outcome.MALFORMED


def test_garbage_is_malformed(tmp_path):
    assert bundle.load(_write(tmp_path, b"nonsense"), verify=False).outcome is Outcome.MALFORMED


# --- statement shape ----------------------------------------------------------

@pytest.mark.parametrize(
    "statement",
    [
        _statement(_type="https://in-toto.io/Statement/v0.1"),
        _statement(predicateType="https://example.invalid/other"),
        _statement(predicate={}),
        _statement(catalog="not json at all"),
        _statement(catalog="[1, 2, 3]"),
    ],
    ids=["old-statement-version", "wrong-predicate", "no-catalog", "catalog-not-json", "catalog-not-object"],
)
def test_unusable_statements_are_malformed(tmp_path, statement):
    path = _write(tmp_path, _with_statement(statement))
    assert bundle.load(path, verify=False).outcome is Outcome.MALFORMED


def test_catalog_is_extracted_from_the_statement(tmp_path):
    statement = _statement(catalog='{"entries": {"fp1": {"version": "6.3.0.1"}}, "schema": 1}')
    result = bundle.load(_write(tmp_path, _with_statement(statement)), verify=False)

    assert result.outcome is Outcome.DISABLED
    assert result.data["entries"]["fp1"]["version"] == "6.3.0.1"


# --- verification disabled ----------------------------------------------------

def test_disabled_never_reports_a_signer(tmp_path):
    result = bundle.load(_write(tmp_path, _fixture_bytes()), verify=False)
    assert result.outcome is Outcome.DISABLED
    assert result.signer is None


def test_disabled_tolerates_a_catalog_edited_by_hand(tmp_path):
    """The subject digest is a claim of the signed statement. With verification
    off there is nothing to enforce it against, and enforcing it anyway would
    stop someone running a catalog they knowingly changed."""
    statement = _statement(catalog='{"entries": {}, "schema": 1}')
    statement["subject"][0]["digest"]["sha256"] = "ff" * 32

    result = bundle.load(_write(tmp_path, _with_statement(statement)), verify=False)
    assert result.outcome is Outcome.DISABLED
    assert result.data == {"entries": {}, "schema": 1}


# --- verification outcomes ----------------------------------------------------

def test_verified_returns_the_catalog_and_the_signer(tmp_path):
    catalog = '{"entries": {"fp1": {"version": "6.3.0.1"}}, "schema": 1}'
    statement = _statement(catalog=catalog)
    statement["subject"][0]["digest"]["sha256"] = bundle.sha256_hex(catalog)
    verifier = FakeVerifier(payload=json.dumps(statement).encode())

    result = bundle.load(
        _write(tmp_path, _fixture_bytes()),
        verifier_factory=lambda: verifier,
    )

    assert result.outcome is Outcome.VERIFIED
    assert result.data["entries"]["fp1"]["version"] == "6.3.0.1"
    assert result.signer is not None


def test_verified_statement_contradicting_its_own_digest_is_malformed(tmp_path):
    """A signed statement whose embedded catalog does not match the subject
    digest it signed is internally inconsistent, so it is not usable."""
    statement = _statement(catalog='{"entries": {}, "schema": 1}')
    statement["subject"][0]["digest"]["sha256"] = "ff" * 32
    verifier = FakeVerifier(payload=json.dumps(statement).encode())

    result = bundle.load(_write(tmp_path, _fixture_bytes()), verifier_factory=lambda: verifier)
    assert result.outcome is Outcome.MALFORMED


def test_wrong_signer_with_a_genuine_certificate_is_unexpected_signer(tmp_path):
    verifier = FakeVerifier(payload=None, issuer_ok=True)

    result = bundle.load(_write(tmp_path, _fixture_bytes()), verifier_factory=lambda: verifier)

    assert result.outcome is Outcome.UNEXPECTED_SIGNER
    assert result.signer is not None


def test_failed_verification_without_a_genuine_certificate_is_altered(tmp_path):
    verifier = FakeVerifier(payload=None, issuer_ok=False)

    result = bundle.load(_write(tmp_path, _fixture_bytes()), verifier_factory=lambda: verifier)

    assert result.outcome is Outcome.ALTERED


def test_altered_never_reports_a_signer(tmp_path):
    """The identity comes from a certificate that failed verification, so it is
    attacker controlled and must not be shown as fact."""
    verifier = FakeVerifier(payload=None, issuer_ok=False)

    result = bundle.load(_write(tmp_path, _fixture_bytes()), verifier_factory=lambda: verifier)

    assert result.signer is None


def test_altered_still_returns_the_catalog(tmp_path):
    """A failed signature warns, it does not stop the tool."""
    verifier = FakeVerifier(payload=None, issuer_ok=False)

    result = bundle.load(_write(tmp_path, _fixture_bytes()), verifier_factory=lambda: verifier)

    assert result.data is not None


def test_verifier_that_cannot_be_built_is_unverifiable(tmp_path):
    def explode():
        raise RuntimeError("no network")

    result = bundle.load(_write(tmp_path, _fixture_bytes()), verifier_factory=explode)

    assert result.outcome is Outcome.UNVERIFIABLE
    assert result.data is not None
    assert result.signer is None


# --- the property that keeps the second check safe ----------------------------

def test_the_pinned_policy_is_never_a_no_op(tmp_path):
    """`load` must never verify against a policy that checks nothing. The
    fallback check exists to establish the certificate is genuine, so it has
    to be a real policy."""
    verifier = FakeVerifier(payload=None, issuer_ok=True)
    bundle.load(_write(tmp_path, _fixture_bytes()), verifier_factory=lambda: verifier)

    assert verifier.policies
    assert not any(isinstance(p, policy.UnsafeNoOp) for p in verifier.policies)


def test_identities_are_pinned_to_the_expected_issuer():
    assert bundle.EXPECTED_ISSUER == "https://token.actions.githubusercontent.com"
    assert isinstance(bundle.EXPECTED_IDENTITIES, tuple)
    assert all(i.startswith("https://github.com/") for i in bundle.EXPECTED_IDENTITIES)


# --- a missing catalog is not the same as a broken one ------------------------

def test_missing_file_is_reported_as_missing(tmp_path):
    result = bundle.load(tmp_path / "absent.json", verify=False)
    assert result.outcome is Outcome.MISSING


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root ignores file modes, so the file stays readable and the test would pass for the wrong reason",
)
def test_unreadable_file_is_missing_not_malformed(tmp_path):
    path = tmp_path / "unreadable.json"
    path.write_bytes(b"{}")
    path.chmod(0o000)
    try:
        assert bundle.load(path, verify=False).outcome is Outcome.MISSING
    finally:
        path.chmod(0o644)


# --- the pinned identity must match a workflow that actually exists -------------

def test_each_pinned_identity_names_a_workflow_outside_this_repository():
    """The catalog is signed by the crawler, a separate project. This repository
    never signs, so an identity pointing back at it would mean the two halves
    had been confused."""
    for identity in bundle.EXPECTED_IDENTITIES:
        assert "/.github/workflows/" in identity
        assert "/omada-release-watch/" not in identity


def test_each_pinned_identity_is_bound_to_a_ref():
    for identity in bundle.EXPECTED_IDENTITIES:
        assert "@refs/" in identity


# --- moving the signing workflow without breaking installed clients -------------

def _recorded_identities(monkeypatch, identities, tmp_path):
    """Run `load` with `identities` pinned, returning what reached the policy.

    Asserting on the constructed policy would mean reading sigstore's private
    attributes. What this project controls is which identities it hands over,
    so that is what is checked.
    """
    seen = []
    real_identity = policy.Identity

    def recorder(*, identity, issuer):
        seen.append((identity, issuer))
        return real_identity(identity=identity, issuer=issuer)

    monkeypatch.setattr(bundle, "EXPECTED_IDENTITIES", identities)
    monkeypatch.setattr(bundle.policy, "Identity", recorder)

    catalog = '{"entries": {"fp1": {"version": "6.3.0.1"}}, "schema": 1}'
    signed = _statement(
        catalog=catalog,
        subject=[{"name": "catalog.json", "digest": {"sha256": bundle.sha256_hex(catalog)}}],
    )
    verifier = FakeVerifier(payload=json.dumps(signed).encode())
    result = bundle.load(_write(tmp_path, _fixture_bytes()), verifier_factory=lambda: verifier)

    return seen, result


def test_every_pinned_identity_is_offered_to_the_verifier(tmp_path, monkeypatch):
    """A transition entry only works if it actually reaches verification.
    Renaming the signing workflow means pinning both the old and the new
    identity, letting clients update, then moving it."""
    old = "https://github.com/owner/crawler/.github/workflows/crawl.yml@refs/heads/main"
    new = "https://github.com/owner/crawler/.github/workflows/publish.yml@refs/heads/main"

    seen, result = _recorded_identities(monkeypatch, (old, new), tmp_path)

    assert [identity for identity, _ in seen] == [old, new]
    assert result.outcome is Outcome.VERIFIED


def test_a_transition_entry_uses_the_same_issuer(tmp_path, monkeypatch):
    """Both identities are the same workflow provider. An entry that pinned a
    different issuer would accept a certificate from somewhere else."""
    identities = (
        "https://github.com/owner/crawler/.github/workflows/crawl.yml@refs/heads/main",
        "https://github.com/owner/crawler/.github/workflows/publish.yml@refs/heads/main",
    )

    seen, _ = _recorded_identities(monkeypatch, identities, tmp_path)

    assert {issuer for _, issuer in seen} == {bundle.EXPECTED_ISSUER}


def test_pinning_one_identity_offers_exactly_that_one(tmp_path, monkeypatch):
    only = "https://github.com/owner/crawler/.github/workflows/crawl.yml@refs/heads/main"

    seen, _ = _recorded_identities(monkeypatch, (only,), tmp_path)

    assert [identity for identity, _ in seen] == [only]


# --- the transition, against the real policy ------------------------------------

def _fixture_certificate():
    from sigstore.models import Bundle

    return Bundle.from_json(_fixture_bytes()).signing_certificate


def _fixture_signer() -> str:
    from sigstore.models import Bundle

    return bundle.signer_identity(Bundle.from_json(_fixture_bytes()))


def test_a_transition_entry_is_accepted_in_either_position(monkeypatch):
    """The tests above assert which identities are handed over. This one runs
    sigstore's own policy against a real certificate, which is what actually
    decides whether a client mid-transition accepts the catalog."""
    signing = _fixture_signer()
    retired = signing.replace("signing-spike.yml", "retired.yml")
    certificate = _fixture_certificate()

    for identities in ((retired, signing), (signing, retired)):
        monkeypatch.setattr(bundle, "EXPECTED_IDENTITIES", identities)

        bundle.signer_policy().verify(certificate)  # raises if refused


def test_a_policy_missing_the_signing_identity_refuses_the_certificate(monkeypatch):
    """Without this, the test above would also pass against a policy that
    accepted every certificate."""
    monkeypatch.setattr(bundle, "EXPECTED_IDENTITIES", (_fixture_signer().replace(
        "signing-spike.yml", "retired.yml"),))

    with pytest.raises(VerificationError):
        bundle.signer_policy().verify(_fixture_certificate())


def test_signer_identity_reads_the_uri_out_of_the_certificate():
    """The unexpected-signer message prints this value, so a wrong extraction
    shows the user the wrong identity in the one place identity matters."""
    from sigstore.models import Bundle

    parsed = Bundle.from_json(_fixture_bytes())

    # The fixture predates the crawler and was signed by an earlier spike
    # workflow, which is exactly why the value has to be read, not assumed.
    assert bundle.signer_identity(parsed) == (
        "https://github.com/Jaturu/omada-release-watch"
        "/.github/workflows/signing-spike.yml@refs/heads/spike/catalog-signing"
    )


def test_an_unexpected_payload_type_is_malformed(tmp_path):
    """verify_dsse hands back the type alongside the payload and its contract
    says to check it before handling what came with it."""
    statement = _statement(catalog='{"entries": {}, "schema": 1}')
    path = _write(tmp_path, _with_statement(statement))

    class WrongType(FakeVerifier):
        def verify_dsse(self, parsed, pol):
            super().verify_dsse(parsed, pol)
            return ("application/vnd.something-else+json", b"{}")

    result = bundle.load(path, verifier_factory=lambda: WrongType(payload=b"{}"))

    assert result.outcome is Outcome.MALFORMED
    assert "payload type" in (result.detail or "").lower()
