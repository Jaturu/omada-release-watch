# Security status

No High or Critical findings. 20 lower-severity finding(s) recorded below.

Release `v1.0.0-6`, published as `docker.io/jaturu/omada-release-watch:v1.0.0-6` and `docker.io/jaturu/omada-release-watch:latest`.
Manifest list `sha256:0d199f6e24425908bb786711596b81b2ae5e60278ad0cd815b8b740a9a5b102e`.
Re-scanned 2026-08-17 07:16 UTC against a vulnerability database built 2026-08-17 06:19 UTC.

This page is regenerated on a schedule from the CycloneDX SBOM attached to the published image, so it reflects what the scanners know now rather than what they knew at build time. It is generated output and is not signed. The per-release pages under Releases are the build-time snapshots and do not change.

## linux-amd64

Image digest: `sha256:677d1a917fc97601bcefbcc5f1ba206c98b2806735be4c59e6695e2bfa5b6364`

| CVE | Severity | Packages | Fixed in |
|---|---|---|---|
| CVE-2025-15366 | Medium | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0a6 |
| CVE-2025-15367 | Medium | python 3.13.14 | 3.15.0a6 |
| CVE-2025-60876 | Medium | busybox 1.37.0-r31, busybox-binsh 1.37.0-r31, ssl_client 1.37.0-r31 | No fix available |
| CVE-2026-0864 | Medium | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0b4 |
| CVE-2026-12003 | Medium | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0b3 |
| CVE-2026-4360 | Medium | python 3.13.14 | No fix available |
| CVE-2026-18503 | Low | python 3.13.14 | 3.10.21, 3.11.16, 3.12.14, 3.13.15, 3.14.7, 3.15.0rc1 |
| CVE-2026-6879 | Low | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0rc1 |

Suppressed by `.grype-ignore.yaml`. A fix version appearing on a line this image can actually move to retires the suppression, which is what issue #5 tracks.

| CVE | Severity | Package | Fixed in | Reason |
|---|---|---|---|---|
| CVE-2026-11940 | High | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0b4 | tarfile is never imported, so unreachable. Tracked in #5, retired by #7 |
| CVE-2026-11972 | High | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0b4 | tarfile is never imported, so unreachable. Tracked in #5, retired by #7 |
| CVE-2026-15308 | High | python 3.13.14 | 3.15.0 | no 3.13 backport available. Tracked in #5, retired by #7 |

## linux-arm64

Image digest: `sha256:c7910b5ab45c275c2507848242eb2bb85827794aa6acffbf91bb174689a5556d`

| CVE | Severity | Packages | Fixed in |
|---|---|---|---|
| CVE-2025-15366 | Medium | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0a6 |
| CVE-2025-15367 | Medium | python 3.13.14 | 3.15.0a6 |
| CVE-2025-60876 | Medium | busybox 1.37.0-r31, busybox-binsh 1.37.0-r31, ssl_client 1.37.0-r31 | No fix available |
| CVE-2026-0864 | Medium | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0b4 |
| CVE-2026-12003 | Medium | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0b3 |
| CVE-2026-4360 | Medium | python 3.13.14 | No fix available |
| CVE-2026-18503 | Low | python 3.13.14 | 3.10.21, 3.11.16, 3.12.14, 3.13.15, 3.14.7, 3.15.0rc1 |
| CVE-2026-6879 | Low | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0rc1 |

Suppressed by `.grype-ignore.yaml`. A fix version appearing on a line this image can actually move to retires the suppression, which is what issue #5 tracks.

| CVE | Severity | Package | Fixed in | Reason |
|---|---|---|---|---|
| CVE-2026-11940 | High | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0b4 | tarfile is never imported, so unreachable. Tracked in #5, retired by #7 |
| CVE-2026-11972 | High | python 3.13.14 | 3.13.15, 3.14.7, 3.15.0b4 | tarfile is never imported, so unreachable. Tracked in #5, retired by #7 |
| CVE-2026-15308 | High | python 3.13.14 | 3.15.0 | no 3.13 backport available. Tracked in #5, retired by #7 |
