# Supply Chain

How the container image is built, what provenance material is published alongside it, why the pipeline is shaped the way it is, and how to verify a release either by hand or from automation.

---

# Overview

Releases are published to Docker Hub at `docker.io/jaturu/omada-release-watch`, which is the canonical record, and mirrored to `ghcr.io/jaturu/omada-release-watch`. Both are public, so every command in this document works without an account.

The image is a multi-platform manifest covering `linux/amd64` and `linux/arm64`. Each platform is built on a runner of its own architecture rather than under emulation.

Each release carries four kinds of provenance material:

| Material | Format | Where it lives |
|---|---|---|
| Signature | Sigstore bundle | OCI referrer on the manifest and on each platform image |
| SBOM attestation | in-toto, BuildKit | Attestation manifest inside the image index |
| Provenance attestation | in-toto SLSA, BuildKit | Attestation manifest inside the image index |
| SBOM and scan reports | CycloneDX and raw JSON | OCI referrer on each platform image, and as GitHub Release assets |

Every release also has a GitHub Release carrying a rendered summary of the scans and the same report files as assets. See [Reading a release without registry tooling](#reading-a-release-without-registry-tooling).

Two SBOMs exist per release and that is intentional. The BuildKit attestation is what Docker's own tooling reads. The CycloneDX file attached with ORAS is the one the vulnerability gates were evaluated against, published alongside the Grype and Trivy reports that share its format.

---

# Authentication

None is needed. Every command below works anonymously, against either registry.

One practical caveat. Docker Hub rate limits anonymous pulls by client address, and a verification pass reads several manifests. If commands start failing with `TOOMANYREQUESTS`, either log in with any free account to get the higher allowance, or use the GHCR mirror, which does not rate limit the same way.

```bash
docker login          # optional, raises the Docker Hub rate limit
```

Cosign, Crane, and ORAS all read the credential file `docker login` writes, so one login covers signature verification, attestation reading, and attachment retrieval.

---

# The pipeline

Defined in `.github/workflows/image.yml`. Two jobs: `build-platform` runs once per architecture on a matching runner, and `publish` runs once after both.

Per platform, in `build-platform`:

1. Build for that platform alone and push it by digest with no tag.
2. Resolve the image manifest digest from the pushed object.
3. Generate an SBOM with Syft against that image.
4. Scan with Grype, applying `.grype-ignore.yaml`, and fail on high severity or above.
5. Scan with Trivy, once for a full report and once as a gate on HIGH and CRITICAL.
6. Attach the SBOM and both reports to that platform's image digest.

Once, in `publish`:

7. Merge the per-platform digests into a staging tag, `sha-<commit>`.
8. Scan the source tree for secrets and misconfiguration with Trivy.
9. Sign the manifest and every platform image with keyless Cosign, then verify.
10. Claim the release number as a git tag.
11. Promote the manifest digest to `v<VERSION>-<build>` and `latest`.
12. Mirror to GHCR and sign the mirror recursively.

Step 6 and steps 9 through 12 only run on a push to `main`. A `workflow_dispatch` run builds, scans, and merges, then stops.

---

# Why the pipeline is shaped this way

## Native runners instead of emulation

Building both architectures in one job requires QEMU, and emulated arm64 builds are slow. Each platform builds on a runner of its own architecture instead. The cost is structural: one job cannot build two architectures natively, so the work splits into a matrix plus a job that assembles the result.

## Scanning per platform

The two platform images do not contain identical software. They differ at minimum in the musl loader and the set of Alpine signing keys. A single scan resolves a multi-platform manifest to the scanner's own architecture, so it covers one platform and silently ignores the other. Each platform is therefore scanned on its own runner, and a gate failure on either one stops the release.

## Digest-scoped work before any release tag

Promotion and the release tags are the irreversible parts. Signing depends on external services that can fail for reasons unrelated to this repository, so signing and verification happen while only digests are in play. A signing failure therefore leaves no release tag behind.

Two caveats, because the surrounding steps are not digest-only. Scan reports are attached in the per-platform job, before signing. A staging tag, `sha-<commit>`, is written when the platform digests are merged, which is also before signing. Neither is a release tag, and neither carries a guarantee.

The git tag is claimed first, before any registry tag. It is what the build number is derived from, so claiming it up front means a later failure leaves a tag with no image, which is inert. Claiming it last instead lets a failure strand a published image with no tag, after which every retry recomputes the same number, hits the immutability check, and is refused. That blocks all further releases until someone creates the tag by hand.

The immutability check itself only treats a genuine not-found as "this tag is free". A rate limit, a 5xx, a DNS failure or an expired credential all stop the release rather than being read as an absent tag.

## Signing recursively, and attaching per platform

Automated consumers resolve an image reference to the platform-specific digest, because that is what they actually run. Signatures and attachments addressed only to the multi-platform manifest are invisible to them. `cosign sign --recursive` covers the manifest and every child, and attachments go on each platform image, so a policy engine that resolves `linux/arm64` finds both where it expects them.

## Keeping both attestations and ORAS attachments

These serve different consumers and neither replaces the other. BuildKit attestations are what `docker buildx imagetools inspect` and Docker Scout read, and they are the standard format for SBOM policy. Vulnerability scan results have no equivalently well-consumed attestation type, so the Grype and Trivy reports are published as ORAS referrers. Dropping either mechanism loses a real consumer.

## Resolving the image manifest digest

With attestations enabled, a single-platform build no longer pushes a bare image manifest. It pushes a small index containing the image plus its attestation, and that index digest is what the build action reports. Anything addressing the image itself, including the scanners and the attachments, needs the manifest inside that index rather than the index itself. The `Resolve image manifest digest` step extracts it by selecting the entry that is not annotated as an attestation manifest, and fails if it does not find exactly one.

The digest handed to the manifest merge is deliberately the index, not the resolved manifest, because that is what carries the attestations into the published image.

## Waiting before verification

The registry does not serve a freshly written OCI referrer immediately. Verifying a signature in the same second it was created reports that no signature exists. The verify step waits, then retries until the signature resolves, bounded by a step timeout so a genuine failure still surfaces.

## Grype in one pass, Trivy in two

Grype records suppressed findings in its report under `ignoredMatches`, along with the rule that suppressed each one, so a single run with the ignore file produces both a complete report and the gate. Trivy has no equivalent. Filtering it to gated severities would truncate the published report, so it runs once for the full report and once as the gate.

---

# Verifying a release by hand

## Find the digest

Always work from digests. Tags are mutable and can be moved. A digest and its signature are bound together.

```bash
crane digest docker.io/jaturu/omada-release-watch:v0.1.0-1
```

For the platform-specific digest, which is what attachments hang off:

```bash
crane digest --platform linux/amd64 docker.io/jaturu/omada-release-watch:v0.1.0-1
```

## Verify the signature

```bash
COSIGN_EXPERIMENTAL=1 cosign verify \
  --certificate-identity-regexp 'https://github.com/Jaturu/omada-release-watch/\.github/workflows/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  docker.io/jaturu/omada-release-watch@sha256:<digest>
```

`COSIGN_EXPERIMENTAL=1` is required. Cosign 3 stores the signature as an OCI referrer, and without this variable `cosign verify` looks only for the older `sha256-<digest>.sig` tag and reports `no signatures found` on a correctly signed image.

There is no public key. Verification checks the signing certificate against the workflow identity and the GitHub OIDC issuer. Success confirms the image was signed by this repository's release workflow and that its digest is unchanged since signing. It says nothing about freshness.

The same command works against a platform digest and against the GHCR mirror.

## See what is attached

```bash
cosign tree docker.io/jaturu/omada-release-watch@sha256:<digest>
```

## Read the SBOM and scan reports

These attach to platform digests, so resolve the platform first.

```bash
oras discover docker.io/jaturu/omada-release-watch@sha256:<platform digest>
oras pull docker.io/jaturu/omada-release-watch@sha256:<platform digest> -o ./out
```

Each platform digest carries three files, named for the platform they describe:

| File | Contents |
|---|---|
| `sbom.cyclonedx.<platform>.json` | CycloneDX SBOM |
| `grype-report.<platform>.json` | Grype findings |
| `trivy-report.<platform>.json` | Trivy findings |

## Is the current release safe to run today

[`security-status.md`](security-status.md) answers that one, and it is the page to read if you are deciding whether to pull the image. A scheduled workflow rewrites it weekly, on Mondays, and it can be run on demand from the Actions tab. It opens with a single verdict line, then lists every finding against the current release per platform, followed by the suppressions and whether a fix has appeared for any of them.

It is regenerated rather than accumulated, so the file's git history is the record of when a finding appeared or went away.

The re-scan does not rebuild or pull the image. It reads the CycloneDX SBOM already attached to each published platform digest and runs Grype and Trivy against that, using the same `.grype-ignore.yaml` the release gates used. Scanning the SBOM covers the same targets as scanning the image, both the Alpine packages and the Python packages.

A High or Critical finding fails the scheduled run after the page is committed, so the alarm is visible in the Actions tab and the page is already updated when you go looking.

Everything under Releases is the opposite: a snapshot frozen at build time that never changes. Use those to answer what was known when a given version shipped, and this page to answer what is known now.

## Reading a release without registry tooling

The commands above are the authoritative path, because they read what is bound to the digest. For a quick look with nothing installed, each release also has a GitHub Release page:

```
https://github.com/Jaturu/omada-release-watch/releases/tag/v<VERSION>-<build>
```

The body renders the component counts and the scan results per platform, including anything suppressed by `.grype-ignore.yaml` and the reason recorded for it. The six report files are attached as assets, two platforms by SBOM, Grype and Trivy.

Neither this page nor the registry attachments are signed. `cosign sign --recursive` covers the manifest list and the platform images inside it. The ORAS attachments are separate manifests that name a platform image as their subject, and nothing signs them, so `oras discover` on an SBOM referrer returns no signature. The signature establishes that the image is the one this repository's workflow built. It says nothing about the SBOM or the scan reports beyond the fact that the same run produced them.

Findings are also a snapshot. They record what the scanners knew when the release was built, and the body states both the scan time and the vulnerability database date so that age is visible. An advisory published later against a package in an older release will not appear on that release's page.

## Read the BuildKit attestations

```bash
docker buildx imagetools inspect docker.io/jaturu/omada-release-watch:v0.1.0-1 \
  --format '{{ json .SBOM }}'
```

```bash
docker buildx imagetools inspect docker.io/jaturu/omada-release-watch:v0.1.0-1 \
  --format '{{ json .Provenance }}'
```

Both return an object keyed by platform. This is the same material Docker Scout reads.

## Reading the Grype reports

Grype runs with `.grype-ignore.yaml` applied, and splits its findings into two lists. `matches` holds findings that counted against the gate. `ignoredMatches` holds findings suppressed by a rule, each recording which rule suppressed it.

```bash
jq '.ignoredMatches' ./out/grype-report.linux-amd64.json
```

A CVE appearing there was reviewed and accepted, not overlooked. The rules carry no free-text justification, only matching criteria, so the reasoning lives in the comments in `.grype-ignore.yaml`.

The gate fails on any unignored finding at high severity or above. Trivy scans independently and gates on HIGH and CRITICAL without consulting the Grype ignore file, so the two reports will not always agree.

---

# Verifying from automation

Automation should resolve to the platform digest and verify that, not the tag and not the manifest. This is why signing is recursive and attachments are per platform.

## The identity to trust

| Field | Value |
|---|---|
| Certificate identity | `https://github.com/Jaturu/omada-release-watch/.github/workflows/image.yml@refs/heads/main` |
| Identity regexp | `https://github.com/Jaturu/omada-release-watch/\.github/workflows/.*` |
| OIDC issuer | `https://token.actions.githubusercontent.com` |

The regexp form tolerates the workflow file being renamed. Pin the exact identity instead if you want a rename to break verification deliberately.

## In a CI step

```bash
DIGEST="$(crane digest --platform linux/amd64 docker.io/jaturu/omada-release-watch:v0.1.0-1)"

COSIGN_EXPERIMENTAL=1 cosign verify \
  --certificate-identity-regexp 'https://github.com/Jaturu/omada-release-watch/\.github/workflows/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "docker.io/jaturu/omada-release-watch@${DIGEST}"
```

A non-zero exit means do not deploy. `COSIGN_EXPERIMENTAL=1` must be set in the environment, not only in an interactive shell.

## In an admission controller

Sigstore's policy-controller and Kyverno both verify keyless signatures using the identity and issuer above. Two requirements specific to this image:

The registry is public, so the controller needs no pull credentials.

The controller must be able to read OCI referrers, since the signature is stored that way rather than as a `.sig` tag. Older versions that only understand the legacy tag scheme will report no signature on a correctly signed image. This is the same failure the `COSIGN_EXPERIMENTAL` variable works around for the cosign CLI.

## SBOM policy

For tools that evaluate SBOM presence, such as Docker Scout, use the BuildKit attestations. They are inside the image index and require no extra fetch:

```bash
docker scout attestation list docker.io/jaturu/omada-release-watch:v0.1.0-1
```

For tools that consume CycloneDX directly, pull the attached file from the platform digest as shown above.

---

# Constraints worth knowing

Keyless signing writes to the public Rekor transparency log. Every release therefore has an independent public record of its digest, repository path and workflow identity, held somewhere this project does not control. That is worth more than it costs: a release cannot be quietly un-made, and a signature can be checked against a log nobody here can rewrite.

The GHCR mirror carries the image and its signature but not the SBOM or scan report attachments. That is a choice rather than a limitation. GHCR serves the referrers API and accepts ORAS attachments, so the mirror could carry them if it ever becomes something consumers pull from.

Release tags are immutable. The pipeline refuses to move an existing `v<VERSION>-<build>` tag rather than overwrite it, and only a genuine not-found counts as a free tag, so a rate limit or an outage stops the release instead of being mistaken for an absent tag. The build number comes from counting existing git tags, and the tag is claimed before anything is published.

Freshly written referrers are not served immediately. Anything verifying a signature or attachment moments after it was created should expect to wait and retry.
