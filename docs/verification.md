# Catalog Verification

This document describes how Omada Release Watch verifies the catalog, what each outcome means, and what to do about it.

---

# Overview

The catalog decides which URL the tool downloads an installer from. That makes it a trusted input, not merely data, so it is signed and the signature is checked every time the catalog is read.

Verification is meant to be invisible. When it holds, nothing is printed.

---

# What Is Published

The catalog is distributed as a single Sigstore bundle, by default named:

```text
catalog.sigstore.json
```

One file carries all of the following:

| Part | Purpose |
|------|---------|
| The catalog | The release data itself, embedded verbatim |
| DSSE signature | Signs the statement containing the catalog |
| Signing certificate | Binds the signature to an identity |
| Transparency log proof | Establishes when the signature was made |

Because the catalog is embedded rather than re-serialized, there is nothing to normalize before checking it, and no class of formatting difference that can raise a false alarm.

---

# Signing

The catalog is produced and signed by the crawler, which is a separate project. This repository never signs anything, it only verifies.

Signing is keyless. There is no long-lived private key to protect, rotate, or lose. A short-lived certificate is issued to the crawler's workflow at signing time, and the signature is recorded in a public transparency log.

The certificate binds an identity of this form:

```text
https://github.com/<owner>/<repo>/.github/workflows/<file>@<ref>
```

The tool checks that identity against a pinned value. This matters: without pinning, any certificate issued by the public infrastructure would satisfy the check, and anyone can obtain one for their own identity in seconds. Pinning is what turns "somebody signed this" into "this project signed this".

## Moving the signing workflow

The identity includes the workflow file name and the git ref, so renaming or moving that workflow changes what the certificate says. A copy of this tool that pins only the old value stops verifying, and it is already installed, so nothing can be pushed to it.

`bundle.EXPECTED_IDENTITIES` is therefore a tuple, and any entry in it is accepted. Moving the workflow takes three steps in this order:

1. Add the new identity alongside the old one and release that, so installed copies accept both.
2. Wait for clients to update. Until they do, they accept only the old one.
3. Move the workflow. Signing switches to the new identity, which updated clients already accept.

Removing the old entry is a fourth step, and it breaks any copy that never updated. The crawler refuses to publish under an identity this file does not pin, so an out-of-order move fails on its side rather than producing a catalog nobody can verify.

Rehearse it on a throwaway repository first. A signature can only be produced by actually signing, so a transition cannot be tested in advance any other way: a fabricated bundle proves nothing about verification succeeding, and signing for real is the move itself. Both halves are covered by their own tests, but the two have never met.

---

# Outcomes

| Outcome | Meaning | Tool behavior |
|---------|---------|---------------|
| verified | Signature, contents, and signer all check out | Silent |
| altered | Contents no longer match the signature over them | Reports, keeps working |
| unexpected signer | Validly signed, but not by this project | Reports, keeps working |
| malformed | The file is not a readable catalog bundle | Stops |
| unverifiable | Verification could not run at all | Reports, keeps working |
| missing | There is no catalog at the configured path | Stops |
| disabled | Verification is turned off by configuration | Reports, keeps working |

Two distinctions in that table are deliberate.

`altered` and `unexpected signer` are separate because they call for different responses. Altered usually means you or your own tooling changed the file, and refetching fixes it. Unexpected signer means a correctly signed catalog was produced by somebody else, which is what substituting a hostile catalog looks like.

`unverifiable` is separate from any failure. Not knowing whether a catalog is good is not the same as knowing it is bad, and the two should not read alike.

## Messages

Each outcome is reported with a message this project controls, stating what happened, what it means, and what to do. Any underlying error from the verification library appears beneath that as detail, so the wording you see does not change when the library does.

Reports are written to standard error, so `--json` output on standard output stays parseable.

## A Note On Clocks

Certificate validity windows and transparency log timestamps are time sensitive. A machine with a badly wrong clock will fail verification in a way that looks exactly like tampering. If a catalog you have not touched reports as altered, check the system clock before anything else.

---

# Turning Verification Off

```yaml
catalog:
  verify: false
```

Or per invocation:

```bash
./omada-release-watch.py --verify false --latest
```

This is the supported way to run without network access, since verification may contact the transparency infrastructure.

Disabling suppresses the cryptographic check, not the notice that no check is happening. That notice appears on every run, on standard error and in the `verification` field of every JSON document.

A silent off switch would let one person disable verification on another person's behalf, which is a different thing from disabling it on your own. Two things keep that from happening. `verify: false` in a configuration file is honoured only when the file is owned by whoever is running the tool, or by root, and is not writable by other users, since anyone able to write the working directory could otherwise leave one there. And `--fetch` refuses a catalog whose verification failed, unless `--allow-unverified` is passed, because fetching acts on the catalog rather than reporting it. Verification you switched off yourself is a decision, not a failure, so it does not block a fetch.

---

# Refresh

`--refresh` downloads the published catalog and verifies it before it can replace what is already on disk. It writes `catalog-refresh.sigstore.json` beside the committed copy rather than over it. A download that fails verification is refused, the existing catalog is left untouched, and the command exits non-zero.

A download is capped at 8 MiB. The published catalog is tens of kilobytes, responses are decompressed as they are read, and the size on the wire bounds nothing, so the cap counts decoded bytes as they arrive rather than measuring afterwards.

The download is checked in memory, before anything is written. Verifying a staged file and then renaming that path would install whatever occupies the path at the moment of the rename, which is not necessarily what was verified. The staged file is also checked to be the same file this run wrote before it is renamed into place.

This refuses new data rather than refusing to run. A catalog already in place keeps working, but a tampered download never lands, because an accepted catalog decides what the tool downloads next.

Every catalog present is considered, not only the one being written. The refresh slot is empty on a first refresh, so comparing against it alone would let an older download past whenever the published copy is the only one there.

A download that is authentic but older than the catalog already in place is also refused. An old bundle carries a real signature, so verification alone cannot tell it from the current one, and replaying one would quietly steer the tool back to superseded releases. The signed `updated` date is what separates them.

---

# What Verification Does Not Establish

A verified catalog proves the catalog came from the crawler that produces it and has not changed since. It says nothing about TP-Link.

The SHA256 recorded for an artifact is trust on first use. TP-Link publishes no checksums, so a recorded hash establishes that an artifact has not changed since it was first observed. It does not establish that the artifact is authentic from TP-Link. See [Supply Chain](supply-chain.md).
