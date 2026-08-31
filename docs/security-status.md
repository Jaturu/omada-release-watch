# Security status

Action needed: 24 High or Critical finding(s) against the current release.

Release `v1.0.0-8`, published as `docker.io/jaturu/omada-release-watch:v1.0.0-8` and `docker.io/jaturu/omada-release-watch:latest`.
Manifest list `sha256:61de839d9a7039b6a1efd267bfd2ae6f05eac74b2a36ad52a03ad7af38a49f2d`.
Re-scanned 2026-08-31 13:44 UTC against a vulnerability database built 2026-08-31 06:37 UTC.

This page is regenerated on a schedule from the CycloneDX SBOM attached to the published image, so it reflects what the scanners know now rather than what they knew at build time. It is generated output and is not signed. The per-release pages under Releases are the build-time snapshots and do not change.

## linux-amd64

Image digest: `sha256:5ab4e53692d4bb8d55b2b8a1e10893ebf4cb965f6b2d423201f7a249aaac21a5`

| CVE | Severity | Packages | Fixed in |
|---|---|---|---|
| CVE-2026-14457 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-18798 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-54874 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-63072 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-63075 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-63076 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2025-15367 | Medium | python 3.13.15 | 3.15.0a6 |
| CVE-2025-60876 | Medium | busybox 1.37.0-r31, busybox-binsh 1.37.0-r31, ssl_client 1.37.0-r31 | No fix available |
| CVE-2026-15806 | Medium | python 3.13.15 | No fix available |
| CVE-2026-17084 | Medium | python 3.13.15 | No fix available |
| CVE-2026-19672 | Medium | python 3.13.15 | No fix available |
| CVE-2026-4360 | Medium | python 3.13.15 | No fix available |
| CVE-2026-63074 | Medium | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-15310 | Low | python 3.13.15 | No fix available |
| CVE-2026-63073 | Unknown | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-75803 | Unknown | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |

Suppressed by `.grype-ignore.yaml`. A fix version appearing on a line this image can actually move to retires the suppression, which is what issue #5 tracks.

| CVE | Severity | Package | Fixed in | Reason |
|---|---|---|---|---|
| CVE-2026-14456 | High | libcrypto3 3.5.7-r0 | 3.5.8-r0 | no QUIC listener in this image, so unreachable. Tracked in #9 |
| CVE-2026-14456 | High | libssl3 3.5.7-r0 | 3.5.8-r0 | no QUIC listener in this image, so unreachable. Tracked in #9 |

## linux-arm64

Image digest: `sha256:773c34118d431d717cf1ece3d4eff1512bcbb119c70c8b7935c99c65770a2f26`

| CVE | Severity | Packages | Fixed in |
|---|---|---|---|
| CVE-2026-14457 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-18798 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-54874 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-63072 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-63075 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-63076 | High | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2025-15367 | Medium | python 3.13.15 | 3.15.0a6 |
| CVE-2025-60876 | Medium | busybox 1.37.0-r31, busybox-binsh 1.37.0-r31, ssl_client 1.37.0-r31 | No fix available |
| CVE-2026-15806 | Medium | python 3.13.15 | No fix available |
| CVE-2026-17084 | Medium | python 3.13.15 | No fix available |
| CVE-2026-19672 | Medium | python 3.13.15 | No fix available |
| CVE-2026-4360 | Medium | python 3.13.15 | No fix available |
| CVE-2026-63074 | Medium | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-15310 | Low | python 3.13.15 | No fix available |
| CVE-2026-63073 | Unknown | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |
| CVE-2026-75803 | Unknown | libcrypto3 3.5.7-r0, libssl3 3.5.7-r0 | 3.5.8-r0 |

Suppressed by `.grype-ignore.yaml`. A fix version appearing on a line this image can actually move to retires the suppression, which is what issue #5 tracks.

| CVE | Severity | Package | Fixed in | Reason |
|---|---|---|---|---|
| CVE-2026-14456 | High | libcrypto3 3.5.7-r0 | 3.5.8-r0 | no QUIC listener in this image, so unreachable. Tracked in #9 |
| CVE-2026-14456 | High | libssl3 3.5.7-r0 | 3.5.8-r0 | no QUIC listener in this image, so unreachable. Tracked in #9 |
