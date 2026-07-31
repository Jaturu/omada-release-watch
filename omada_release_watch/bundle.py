from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from cryptography.x509 import (
    ExtensionNotFound,
    SubjectAlternativeName,
    UniformResourceIdentifier,
)
from sigstore.models import Bundle
from sigstore.verify import Verifier, policy

EXPECTED_ISSUER = "https://token.actions.githubusercontent.com"

# The catalog is produced and signed by the crawler, which is a separate
# project. Keyless certificates bind the workflow path and the git ref, so
# moving or renaming that workflow needs a transition entry here.
EXPECTED_IDENTITIES = (
    "https://github.com/Jaturu/omada-crawler"
    "/.github/workflows/crawl.yml@refs/heads/main",
)

# verify_dsse hands this back alongside the payload, and its contract is
# that a caller checks it before handling what came with it.
PAYLOAD_TYPE = "application/vnd.in-toto+json"

PREDICATE_TYPE = "https://omada-release-watch.dev/catalog/v1"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"


class Outcome(str, Enum):
    VERIFIED = "verified"
    MISSING = "missing"
    ALTERED = "altered"
    UNEXPECTED_SIGNER = "unexpected-signer"
    MALFORMED = "malformed"
    UNVERIFIABLE = "unverifiable"
    DISABLED = "disabled"


@dataclass
class LoadResult:
    outcome: Outcome
    data: dict[str, Any] | None = None
    detail: str | None = None
    signer: str | None = None


class BundleError(ValueError):
    pass


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _statement_from_payload(payload: bytes) -> dict[str, Any]:
    try:
        statement = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BundleError(f"Signed payload is not JSON: {exc}") from exc

    if statement.get("_type") != STATEMENT_TYPE:
        raise BundleError(f"Unexpected statement type: {statement.get('_type')!r}")

    if statement.get("predicateType") != PREDICATE_TYPE:
        raise BundleError(f"Unexpected predicate type: {statement.get('predicateType')!r}")

    return statement


def _catalog_from_statement(statement: dict[str, Any], strict: bool = True) -> dict[str, Any]:
    predicate = statement.get("predicate")
    if not isinstance(predicate, dict) or not isinstance(predicate.get("catalog"), str):
        raise BundleError("Statement does not carry a catalog payload")

    catalog_text = predicate["catalog"]

    # Only meaningful inside a verified statement. Unverified, it would just
    # stop someone running a catalog they knowingly edited.
    if strict:
        subjects = statement.get("subject") or []
        expected = ""
        if subjects and isinstance(subjects[0], dict):
            expected = str(subjects[0].get("digest", {}).get("sha256", ""))

        actual = sha256_hex(catalog_text)
        if expected and expected != actual:
            raise BundleError(
                f"Embedded catalog does not match the signed subject digest "
                f"(expected {expected}, got {actual})"
            )

    try:
        catalog = json.loads(catalog_text)
    except ValueError as exc:
        raise BundleError(f"Embedded catalog is not JSON: {exc}") from exc

    if not isinstance(catalog, dict):
        raise BundleError("Embedded catalog is not an object")

    return catalog


def _unverified_payload(raw: bytes) -> bytes:
    try:
        envelope = json.loads(raw)["dsseEnvelope"]
        return base64.b64decode(envelope["payload"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BundleError(f"Not a DSSE bundle: {exc}") from exc


def signer_identity(bundle: Bundle) -> str | None:
    try:
        san = bundle.signing_certificate.extensions.get_extension_for_class(
            SubjectAlternativeName
        )
    except (ExtensionNotFound, ValueError):
        return None

    uris = san.value.get_values_for_type(UniformResourceIdentifier)
    return uris[0] if uris else None


def load(
    path: str | Path,
    verify: bool = True,
    verifier_factory: Callable[[], Verifier] | None = None,
) -> LoadResult:
    """
    Read a signed catalog bundle from disk.

    Reading and checking are separate so a caller holding bytes can verify
    exactly the bytes it will use. Verifying a path and then acting on that
    path again reads the file twice, and the second read is not covered by
    the first result.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return LoadResult(Outcome.MISSING, detail=str(exc))

    return load_bytes(raw, verify=verify, verifier_factory=verifier_factory)


def load_bytes(
    raw: bytes,
    verify: bool = True,
    verifier_factory: Callable[[], Verifier] | None = None,
) -> LoadResult:
    """
    Check a signed catalog bundle held in memory.

    Returns the catalog and an outcome describing what could be established
    about it. A failed verification still returns the catalog, because the
    tool reports rather than refuses.

    `verifier_factory` builds the verifier, and is injectable so the outcome
    logic can be tested without reaching Sigstore.
    """
    if not verify:
        try:
            statement = _statement_from_payload(_unverified_payload(raw))
            return LoadResult(
                Outcome.DISABLED,
                data=_catalog_from_statement(statement, strict=False),
            )
        except BundleError as exc:
            return LoadResult(Outcome.MALFORMED, detail=str(exc))

    try:
        parsed = Bundle.from_json(raw)
    except Exception as exc:
        return LoadResult(Outcome.MALFORMED, detail=f"{type(exc).__name__}: {exc}")

    signer = signer_identity(parsed)
    accepted = policy.AnyOf(
        [policy.Identity(identity=i, issuer=EXPECTED_ISSUER) for i in EXPECTED_IDENTITIES]
    )

    try:
        verifier = (verifier_factory or Verifier.production)()
    except Exception as exc:
        return LoadResult(
            Outcome.UNVERIFIABLE,
            data=_data_or_none(raw),
            detail=f"{type(exc).__name__}: {exc}",
        )

    try:
        payload_type, payload = verifier.verify_dsse(parsed, accepted)
    except Exception as exc:
        established = _signer_established(verifier, parsed)
        return LoadResult(
            Outcome.UNEXPECTED_SIGNER if established else Outcome.ALTERED,
            data=_data_or_none(raw),
            detail=f"{type(exc).__name__}: {exc}",
            signer=signer if established else None,
        )

    if payload_type != PAYLOAD_TYPE:
        return LoadResult(
            Outcome.MALFORMED,
            detail=f"Unexpected payload type: {payload_type!r}",
        )

    try:
        statement = _statement_from_payload(payload)
        return LoadResult(
            Outcome.VERIFIED,
            data=_catalog_from_statement(statement),
            signer=signer,
        )
    except BundleError as exc:
        return LoadResult(Outcome.MALFORMED, detail=str(exc))


def _signer_established(verifier: Verifier, parsed: Bundle) -> bool:
    """
    Re-verify against the issuer alone, without pinning the workflow.

    A pass means the certificate really came from the expected issuer and the
    signature holds, so only our identity list did not match. That also makes
    the certificate's identity safe to show, which it is not when read
    straight off disk.
    """
    try:
        verifier.verify_dsse(parsed, policy.OIDCIssuer(EXPECTED_ISSUER))
    except Exception:
        return False

    return True


def _data_or_none(raw: bytes) -> dict[str, Any] | None:
    try:
        statement = _statement_from_payload(_unverified_payload(raw))
        return _catalog_from_statement(statement, strict=False)
    except BundleError:
        return None
